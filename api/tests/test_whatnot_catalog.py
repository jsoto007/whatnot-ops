"""The shop's Whatnot listings next to its own products: the mirror, the link, an order
naming its product through a linked listing, and setting Whatnot's stock from the app.

Everything runs against the in-memory fake (app/services/whatnot/fake.py), which follows
Whatnot's published API description. Nothing here has met the live catalog: whether the
shop's listings carry SKUs at all, and how "choose any" lots appear, is not known.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import (
    AuditLog,
    InventoryMovement,
    MovementKind,
    ReviewItem,
    ReviewStatus,
    Sale,
    Sku,
    WhatnotConnection,
    WhatnotShow,
    WhatnotSyncState,
    WhatnotVariant,
)
from app.services import activity
from app.services.whatnot import catalog, client_for
from tests import test_whatnot_orders as base
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import apply, compare, read, sale_rows, win

shop = base.shop  # the same fixture: the world's shop connected to Whatnot, its show matched


def sync(app, shop) -> dict:
    with app.app_context():
        counts = catalog.sync_catalog(shop["org"], client_for(shop["org"]))
        db.session.commit()
        return counts


def variants(app) -> dict[str, tuple]:
    """Whatnot's SKU (or the title) → (linked product code, how, available, gone?)."""
    with app.app_context():
        out = {}
        for v in db.session.scalars(select(WhatnotVariant).order_by(WhatnotVariant.id)):
            sku = db.session.get(Sku, v.sku_id) if v.sku_id else None
            out[v.sku_code or v.title] = (
                sku.code if sku else None,
                v.link,
                v.available,
                v.gone_at is not None,
            )
        return out


def variant_id(app, sku_code: str) -> int:
    with app.app_context():
        return db.session.scalar(
            select(WhatnotVariant.id).where(WhatnotVariant.sku_code == sku_code)
        )


def sentences(app) -> list[str]:
    with app.app_context():
        rows = db.session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        return [
            activity.describe(a.action, a.details)
            for a in rows
            if a.action == "whatnot.listing_linked"
        ]


# ---------- the mirror and the link ----------


def test_the_catalog_is_mirrored_and_a_listing_links_itself_only_on_an_exact_code(app, shop):
    fake = shop["fake"]
    fake.add_product(
        title="Red bundle",
        variants=[
            {
                "sku": " red ",
                "price_cents": 2500,
                "quantity": 7,
                "options": [{"name": "Size", "value": "Large"}],
            }
        ],
    )
    fake.add_product(title="Mystery box", variants=[{"sku": "RED-XL", "quantity": 2}])
    fake.add_product(title="CHOOSE ANY PRODUCT", variants=[{"quantity": 500}])  # no SKU at all
    before = level(app, shop["red"])
    assert sync(app, shop) == {"products": 3, "variants": 3, "linked": 1, "complete": True}
    assert variants(app) == {
        "red": ("RED", "code", 7, False),  # the same code, whatever its case or spacing
        "RED-XL": (None, None, 2, False),  # close is not equal
        "CHOOSE ANY PRODUCT": (None, None, 500, False),
    }
    assert level(app, shop["red"]) == before  # reading moves no stock
    with app.app_context():
        page = catalog.listings(shop["org"])
        assert page["counts"] == {"all": 3, "linked": 1, "undecided": 2, "differs": 1}
        red = next(r for r in page["listings"] if r["skuCode"] == "red")
        assert (red["title"], red["options"], red["priceCents"]) == ("Red bundle", "Large", 2500)
        # Whatnot offers 7; the shop can sell 3.
        assert (red["onWhatnot"], red["sku"]["canSell"], red["differs"]) == (7, 3, True)
        assert catalog.for_skus(shop["org"]) == {
            shop["red"]: {"listings": 1, "onWhatnot": 7, "differs": True}
        }
    assert sync(app, shop)["linked"] == 0  # reading again links nothing twice


def test_a_listing_deleted_on_whatnot_is_gone_only_after_a_read_that_saw_everything(
    app, shop, monkeypatch
):
    fake = shop["fake"]
    kept = fake.add_product(title="Kept", variants=[{"sku": "KEPT"}])
    dropped = fake.add_product(title="Dropped", variants=[{"sku": "DROPPED"}])
    sync(app, shop)
    del fake.products[dropped["id"]]
    for vid in [v["id"] for v in fake.variants_of(dropped["id"])]:
        del fake.variants[vid]
    monkeypatch.setattr(catalog, "MAX_PRODUCTS", 0)  # a read cut short proves nothing
    assert sync(app, shop)["complete"] is False
    assert variants(app)["DROPPED"][3] is False
    monkeypatch.undo()
    assert sync(app, shop)["complete"] is True
    assert variants(app)["DROPPED"][3] is True and variants(app)["KEPT"][3] is False
    with app.app_context():
        assert [r["title"] for r in catalog.listings(shop["org"])["listings"]] == ["Kept"]
    assert kept["id"] in fake.products


def test_a_persons_link_and_a_persons_no_both_stand(app, client, shop, sign_in):
    fake = shop["fake"]
    fake.add_product(title="Red bundle", variants=[{"sku": "RED", "quantity": 3}])
    fake.add_product(title="Odd name", variants=[{"sku": "X-1", "quantity": 1}])
    sync(app, shop)
    h = sign_in(shop["org"])
    odd, red = variant_id(app, "X-1"), variant_id(app, "RED")
    # A person says what the oddly named listing is, and that the code match is wrong.
    r = client.patch(f"/api/whatnot/catalog/{odd}/link", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 200
    r = client.patch(f"/api/whatnot/catalog/{red}/link", json={"skuId": None}, headers=h)
    assert r.status_code == 200
    assert r.get_json()["counts"] == {"all": 2, "linked": 1, "undecided": 0, "differs": 1}
    sync(app, shop)  # a later read does not undo either
    assert variants(app) == {"RED": (None, "off", 3, False), "X-1": ("RED", "person", 1, False)}
    assert sentences(app) == [
        "Linked the Whatnot listing “Odd name” to “Red bundle”",
        "Said the Whatnot listing “Red bundle” is none of the shop's products",
    ]
    assert (
        client.patch(f"/api/whatnot/catalog/{odd}/link", json={"skuId": "1"}, headers=h).status_code
        == 400
    )
    assert (
        client.patch(
            "/api/whatnot/catalog/999999/link", json={"skuId": None}, headers=h
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/whatnot/catalog/{odd}/link", json={"skuId": 999999}, headers=h
        ).status_code
        == 404
    )
    assert (
        client.patch(f"/api/whatnot/catalog/{odd}/link", json={"skuId": None}).status_code == 403
    )  # no CSRF


def test_who_may_see_and_change_the_catalog_and_another_shop_sees_nothing(
    app, client, shop, sign_in
):
    from app.models import Organization

    shop["fake"].add_product(title="Red bundle", variants=[{"sku": "RED", "quantity": 3}])
    sync(app, shop)
    vid = variant_id(app, "RED")
    h = sign_in(shop["org"], "viewer", "v@x.test")
    assert client.get("/api/whatnot/catalog").get_json()["counts"]["all"] == 1
    assert (
        client.patch(
            f"/api/whatnot/catalog/{vid}/link", json={"skuId": None}, headers=h
        ).status_code
        == 403
    )
    assert client.post("/api/whatnot/catalog/read", headers=h).status_code == 403
    client.post("/api/auth/logout", headers=h)
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    h = sign_in(other_id, "owner", "other@x.test")
    page = client.get("/api/whatnot/catalog").get_json()
    assert page["connected"] is False and page["listings"] == []
    assert (
        client.patch(
            f"/api/whatnot/catalog/{vid}/link", json={"skuId": None}, headers=h
        ).status_code
        == 404
    )
    assert client.post("/api/whatnot/catalog/read", headers=h).status_code == 409


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_read_now_the_timer_and_a_product_webhook_all_refresh_the_catalog(
    app, client, shop, sign_in
):
    from app.services.whatnot import sync as whatnot_sync

    fake = shop["fake"]
    product = fake.add_product(title="Red bundle", variants=[{"sku": "RED", "quantity": 3}])
    h = sign_in(shop["org"])
    page = client.post("/api/whatnot/catalog/read", headers=h).get_json()
    assert page["counts"]["linked"] == 1 and page["readAt"] is not None
    # The timer reads it again only once a day.
    fake.variants_of(product["id"])[0]["inventory"]["available_quantity"] = 9
    with app.app_context():
        whatnot_sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
    assert variants(app)["RED"][2] == 3
    with app.app_context():
        state = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == catalog.RESOURCE)
        )
        state.last_started_at = datetime.now(UTC) - timedelta(days=2)
        db.session.commit()
        whatnot_sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
    assert variants(app)["RED"][2] == 9
    # A token that may not read inventory: said on the page, and the connection is not flagged.
    fake.scopes = {"read:orders", "read:shows"}
    page = client.post("/api/whatnot/catalog/read", headers=h).get_json()
    assert page["notAllowed"] is True
    with app.app_context():
        assert db.session.scalar(select(WhatnotConnection.problem)) is None


# ---------- an order names its product ----------


def linked_listing(app, shop, title="Winter special", quantity=5) -> dict:
    """A listing whose title says nothing the shop knows, linked to RED by its SKU."""
    product = shop["fake"].add_product(title=title, variants=[{"sku": "RED", "quantity": quantity}])
    sync(app, shop)
    return shop["fake"].variants_of(product["id"])[0]


def order_for(shop, buyer, variant, title="Winter special", price=2500, **kw):
    kw.setdefault("created", datetime.now(UTC) - timedelta(seconds=20))
    return shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer=buyer,
        items=[{"title": title, "price_cents": price, "variant_id": variant["id"], "sku": "RED"}],
        **kw,
    )


def test_an_order_for_a_linked_listing_is_recorded_with_its_product(app, shop):
    variant = linked_listing(app, shop)
    o = order_for(shop, "lani808", variant)
    read(app, shop)
    assert apply(app, shop)["recorded"] == 1
    # "Winter special" names no product; the link does: reserved and paid, no review.
    assert sale_rows(app) == [("lani808", "Winter special", "paid", "paid on Whatnot", o["id"])]
    assert level(app, shop["red"]) == (2, 0)  # paid on Whatnot: it has left the shelf
    with app.app_context():
        assert db.session.scalar(select(Sale.sku_id)) == shop["red"]
        assert db.session.scalar(select(func.count()).select_from(ReviewItem)) == 0
    snapshot = sale_rows(app), level(app, shop["red"])
    apply(app, shop)  # again: nothing more
    assert (sale_rows(app), level(app, shop["red"])) == snapshot


def test_a_win_waiting_for_its_product_is_named_by_its_linked_listing(app, shop):
    variant = linked_listing(app, shop)
    shop["say"](app, [win("lani808", "Winter special")])
    run(app, shop["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert (sale.status.value, sale.sku_id) == ("detected", None)
        assert db.session.scalar(select(ReviewItem.reason)) == "unmatched_item"
    o = order_for(shop, "lani808", variant)
    read(app, shop)
    row = compare(app, shop)[2]["plan"].rows[0]
    assert (row.action, row.note) == ("mark_paid", "its Whatnot listing names the product")
    assert level(app, shop["red"]) == (3, 0)  # comparing changes nothing
    apply(app, shop)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert (sale.status.value, sale.sku_id, sale.external_order_id) == (
            "paid",
            shop["red"],
            o["id"],
        )
        item = db.session.scalar(select(ReviewItem))
        # The decision a person would have made, taken by Whatnot's record: nobody's name on it.
        assert (item.status, item.resolved_by_id) == (ReviewStatus.resolved, None)
        assert item.resolution == {"action": "assign_sku", "skuId": shop["red"], "reserved": True}
        # Stock moved through the ledger, as for a pick made in Review.
        kinds = db.session.scalars(
            select(InventoryMovement.kind).where(InventoryMovement.source_type == "sale")
        ).all()
        assert MovementKind.reserve in kinds
    assert level(app, shop["red"]) == (2, 0)  # reserved by the naming, then paid: off the shelf
    assert len(sale_rows(app)) == 1  # the win and its order are one sale


def test_a_listing_a_person_said_is_none_of_ours_names_nothing(app, client, shop, sign_in):
    variant = linked_listing(app, shop)
    h = sign_in(shop["org"])
    vid = variant_id(app, "RED")
    client.patch(f"/api/whatnot/catalog/{vid}/link", json={"skuId": None}, headers=h)
    shop["say"](app, [win("lani808", "Winter special")])
    run(app, shop["show"])
    order_for(shop, "lani808", variant)
    read(app, shop)
    row = compare(app, shop)[2]["plan"].rows[0]
    assert (row.action, row.note) == ("unchanged", "waiting for its product to be named")
    apply(app, shop)
    with app.app_context():
        assert db.session.scalar(select(Sale.sku_id)) is None
    assert level(app, shop["red"]) == (3, 0)


def test_a_linked_product_of_another_shop_or_one_switched_off_is_never_used(app, shop):
    from app.services import pipeline

    with app.app_context():
        assert pipeline.listed_sku(shop["org"], shop["red"]).id == shop["red"]
        assert pipeline.listed_sku(shop["org"] + 1, shop["red"]) is None
        assert pipeline.listed_sku(shop["org"], None) is None
        db.session.get(Sku, shop["red"]).active = False
        db.session.commit()
        assert pipeline.listed_sku(shop["org"], shop["red"]) is None


# ---------- found by the review of this phase (each failed before its fix) ----------


def test_a_win_with_no_price_takes_what_the_order_says_was_paid_not_the_list_price(app, shop):
    """Naming used to fill a missing price from the product's list price ($25.00), and the
    order's own price ($18.00) was then ignored: revenue overstated."""
    variant = linked_listing(app, shop)
    shop["say"](app, [("", "lani808 won Winter special")])  # a win line with no price
    run(app, shop["show"])
    with app.app_context():
        assert db.session.scalar(select(Sale.price_cents)) is None
    order_for(shop, "lani808", variant, price=1800)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert (sale.status.value, sale.sku_id, sale.price_cents) == ("paid", shop["red"], 1800)


def test_none_of_ours_also_stops_the_listings_title_from_naming_a_product(
    app, client, shop, sign_in
):
    """A person said the listing is none of the shop's products. Its title ("Red bundle")
    used to name one anyway, through the text match."""
    product = shop["fake"].add_product(title="Red bundle", variants=[{"sku": "RED"}])
    sync(app, shop)
    variant = shop["fake"].variants_of(product["id"])[0]
    h = sign_in(shop["org"])
    client.patch(
        f"/api/whatnot/catalog/{variant_id(app, 'RED')}/link", json={"skuId": None}, headers=h
    )
    order_for(shop, "lani808", variant, title="Red bundle")
    read(app, shop)
    assert apply(app, shop)["recorded"] == 1
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert (sale.sku_id, sale.status.value) == (None, "detected")  # waits for a person
        assert db.session.scalar(select(ReviewItem.reason)) == "unmatched_item"
    assert level(app, shop["red"]) == (3, 0)


def test_a_person_settling_the_same_item_at_that_moment_does_not_abort_the_apply(
    app, shop, monkeypatch
):
    from app.services import pipeline

    variant = linked_listing(app, shop)
    shop["say"](app, [win("lani808", "Winter special")])
    run(app, shop["show"])
    order_for(shop, "lani808", variant)
    read(app, shop)

    def already(*a, **kw):
        raise pipeline.ReviewError("already resolved", 409)

    monkeypatch.setattr(pipeline, "resolve", already)
    apply(app, shop)  # used to raise, and nothing of the show was applied
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert (sale.status.value, sale.sku_id) == ("detected", None)


def test_a_code_match_never_overwrites_a_decision_a_person_made_a_moment_ago(
    app, shop, monkeypatch
):
    from sqlalchemy import text

    shop["fake"].add_product(title="Red bundle", variants=[{"sku": "RED", "quantity": 3}])
    with app.app_context():
        client = client_for(shop["org"])
        for product in client.products():
            catalog.store_product(
                shop["org"], product, client.variants(product["id"]), now=datetime.now(UTC)
            )
        db.session.commit()
        real, calls = catalog.select, {"n": 0}

        def select_then_someone_decides(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:  # after auto_link listed the undecided, before it links
                with db.engine.begin() as other:
                    other.execute(text("UPDATE whatnot_variants SET link = 'off'"))
            return real(*a, **kw)

        monkeypatch.setattr(catalog, "select", select_then_someone_decides)
        assert catalog.auto_link(shop["org"]) == 0
        db.session.commit()
    assert variants(app) == {"RED": (None, "off", 3, False)}


def test_a_product_deleted_while_the_catalog_is_read_does_not_fail_the_read(app, shop):
    from app.services.whatnot import NotFound

    fake = shop["fake"]
    fake.add_product(title="Kept", variants=[{"sku": "KEPT"}])
    doomed = fake.add_product(title="Doomed", variants=[{"sku": "DOOMED"}])
    sync(app, shop)
    with app.app_context():
        client = client_for(shop["org"])
        real = client.variants

        def variants_of(product_id):
            if product_id == doomed["id"]:
                raise NotFound("gone", status=404, problem="not-found")
            return real(product_id)

        client.variants = variants_of
        counts = catalog.sync_catalog(shop["org"], client)
    assert (counts["products"], counts["complete"]) == (1, True)
    assert variants(app)["DOOMED"][3] is True and variants(app)["KEPT"][3] is False


def test_the_daily_read_waits_while_a_show_is_on_air_but_not_for_a_show_never_closed(app, shop):
    read(app, shop, orders=False)  # the show on Whatnot is known here
    with app.app_context():
        assert catalog.due(shop["org"]) is True
        ws = db.session.scalar(select(WhatnotShow))
        ws.status, ws.started_at = "live", datetime.now(UTC) - timedelta(hours=1)
        db.session.commit()
        assert catalog.due(shop["org"]) is False  # the show's orders need the request budget
        ws.started_at = ws.scheduled_start_at = datetime.now(UTC) - timedelta(days=3)
        db.session.commit()
        assert catalog.due(shop["org"]) is True  # its ending was missed: it must not block


def test_two_listings_of_one_product_are_compared_together_on_both_pages(app, shop):
    """Inventory said "differs" for the pair while the listings page said nothing."""
    fake = shop["fake"]
    for i in (1, 2):
        fake.add_product(title=f"Red {i}", variants=[{"sku": "RED", "quantity": 8}])
    sync(app, shop)
    with app.app_context():
        assert catalog.for_skus(shop["org"]) == {
            shop["red"]: {"listings": 2, "onWhatnot": 16, "differs": True}
        }
        rows = catalog.listings(shop["org"])["listings"]
        assert [(r["listings"], r["offered"], r["differs"]) for r in rows] == [(2, 16, True)] * 2
        # A shop that disconnected is shown no counts: they would only be getting older.
        db.session.scalar(select(WhatnotConnection)).disconnected_at = datetime.now(UTC)
        db.session.commit()
        assert catalog.for_skus(shop["org"]) == {}
        assert catalog.listings(shop["org"])["listings"] == []
