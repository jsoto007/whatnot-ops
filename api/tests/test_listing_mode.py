"""One product at a time (stage 6): each listing is one product, and a show needs no laptop.
Winners, items and payments come from Whatnot's orders; the listing names the product; a
person links a listing while naming one of its wins; a product can be changed afterwards.
Runs against the in-memory fake Whatnot. Buyer names are made up."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import (
    ChatMessage,
    MovementKind,
    Organization,
    ReviewItem,
    ReviewStatus,
    Sale,
    Show,
    WhatnotVariant,
)
from app.services import catalog, inventory, jobs
from app.services.whatnot import catalog as listings
from app.services.whatnot import client_for
from tests import test_whatnot_orders as base
from tests.test_pipeline import level
from tests.test_whatnot_orders import set_mode

shop = base.shop


def product(app, shop, name, code, on_hand):
    with app.app_context():
        sku = catalog.create_product(shop["org"], name, skus=[{"code": code}]).skus[0]
        inventory.move(sku.id, MovementKind.restock, on_hand, source_type="test", source_id=code)
        db.session.commit()
        return sku.id


def listing(shop, title, sku=None):
    """A listing in the shop's Whatnot inventory: (the product, its one variant)."""
    p = shop["fake"].add_product(title=title, variants=[{"sku": sku, "quantity": 9}])
    return p, shop["fake"].variants_of(p["id"])[0]


def bought(shop, show_id, buyer, made, price=1200, minutes=15, **kw):
    # The show gets a place of its own ten minutes after its first order (no laptop came).
    p, v = made
    return shop["fake"].add_order(
        show_id=show_id,
        buyer=buyer,
        items=[
            {
                "title": p["title"],
                "price_cents": price,
                "product_id": p["id"],
                "variant_id": v["id"],
                "sku": v["sku"],
            }
        ],
        created=datetime.now(UTC) - timedelta(minutes=minutes),
        **kw,
    )


def read_show(app, shop, whatnot_show_id):
    """What the timer and the show's webhooks do: read the show and its orders. With jobs
    inline, the orders are applied in the same breath (the shop applies as they arrive)."""
    with app.app_context():
        jobs.enqueue(
            "whatnot_sync_show",
            {"org_id": shop["org"], "whatnot_show_id": whatnot_show_id},
            org_id=shop["org"],
        )
        db.session.commit()


def sales(app):
    with app.app_context():
        return [
            (s.buyer_username, s.status.value, s.sku.code if s.sku else None)
            for s in db.session.scalars(select(Sale).order_by(Sale.id))
        ]


@pytest.fixture
def no_laptop(app, shop, inline_jobs):
    """A shop that sells one product per listing and applies Whatnot's orders as they
    arrive, with a show on air for half an hour that no laptop is recording. RED is the
    world's product (3 on hand); BLUE has 5 and GREEN 4."""
    set_mode(app, shop, "auto")
    now = datetime.now(UTC)
    with app.app_context():
        db.session.get(Organization, shop["org"]).sale_mode = "listing"
        # The shop's only recording is from two days ago: nothing records today's show.
        db.session.get(Show, shop["show"]).started_at = now - timedelta(days=2)
        db.session.query(ChatMessage).update({"captured_at": now - timedelta(days=2)})
        db.session.commit()
    blue = product(app, shop, "Blue bundle", "BLUE", 5)
    green = product(app, shop, "Green bundle", "GREEN", 4)
    live = shop["fake"].add_show(
        title="No laptop tonight", status="live", start=now - timedelta(minutes=30)
    )
    return {**shop, "blue": blue, "green": green, "live": live["id"]}


def test_a_show_with_three_listings_and_no_laptop_from_the_first_order_to_the_slip(
    app, client, no_laptop, sign_in
):
    s = no_laptop
    # Two listings the shop already has on Whatnot: one carries the product's code, the
    # other was linked by a person.
    red, blue = listing(s, "Red special", sku="RED"), listing(s, "Blue special")
    h = sign_in(s["org"], "manager")
    assert client.post("/api/whatnot/catalog/read", headers=h).status_code in (200, 202)
    with app.app_context():
        blue_row = db.session.scalar(
            select(WhatnotVariant.id).where(WhatnotVariant.title == "Blue special")
        )
    r = client.patch(f"/api/whatnot/catalog/{blue_row}/link", json={"skuId": s["blue"]}, headers=h)
    assert r.status_code == 200
    # A third listing is made during the show: the app's copy has never seen it.
    green = listing(s, "Green drop")
    bought(s, s["live"], "made_up_lani", red, minutes=20)
    bought(s, s["live"], "made_up_kat", blue, minutes=15)
    bought(s, s["live"], "made_up_moe", green, minutes=10)
    bought(s, s["live"], "made_up_nia", green, minutes=8)
    read_show(app, s, s["live"])

    # The show got a place of its own; the linked listings named their sales and stock moved.
    with app.app_context():
        made = db.session.scalar(select(Show).where(Show.whatnot_link == "made"))
        show_id = made.id
        seen = db.session.scalar(select(WhatnotVariant).where(WhatnotVariant.title == "Green drop"))
        assert seen is not None and seen.gone_at is None and seen.sku_id is None
    assert sales(app) == [
        ("made_up_lani", "paid", "RED"),
        ("made_up_kat", "paid", "BLUE"),
        ("made_up_moe", "detected", None),
        ("made_up_nia", "detected", None),
    ]
    assert (level(app, s["red"]), level(app, s["blue"]), level(app, s["green"])) == (
        (2, 0),
        (4, 0),
        (4, 0),
    )
    got = client.get(f"/api/shows/{show_id}").get_json()
    assert got["laptopNeeded"] is False and got["noShelves"] is False
    assert client.get("/api/auth/me").get_json()["organization"]["laptopNeeded"] is False

    # The waiting wins say which listing they are for; naming one links the listing and
    # names the show's other win of it.
    orders = client.get(f"/api/orders?show={show_id}").get_json()["orders"]
    moe = next(x for o in orders for x in o["sales"] if x["buyer"] == "made_up_moe")
    assert moe["saleMode"] == "listing"
    assert moe["listing"] == {"title": "Green drop", "options": "", "linked": False, "off": False}
    r = client.post(
        f"/api/review/{moe['review']['id']}/resolve",
        json={"action": "assign_sku", "skuId": s["green"], "remember": False, "linkListing": True},
        headers=h,
    )
    assert r.status_code == 200, r.get_json()
    assert (r.get_json()["listingLinked"], r.get_json()["alsoResolved"]) == (True, 1)
    assert sales(app)[2:] == [("made_up_moe", "paid", "GREEN"), ("made_up_nia", "paid", "GREEN")]
    assert level(app, s["green"]) == (2, 0)
    with app.app_context():
        seen = db.session.scalar(select(WhatnotVariant).where(WhatnotVariant.title == "Green drop"))
        assert (seen.sku_id, seen.link) == (s["green"], "person")

    # The listing's next order names itself: no review, stock moved.
    bought(s, s["live"], "made_up_ola", green, minutes=1)
    read_show(app, s, s["live"])
    assert sales(app)[-1] == ("made_up_ola", "paid", "GREEN")
    assert level(app, s["green"]) == (1, 0)
    with app.app_context():
        assert (
            db.session.scalar(
                select(func.count())
                .select_from(ReviewItem)
                .where(ReviewItem.status == ReviewStatus.open)
            )
            == 0
        )

    # One product is changed: the old one goes back, the new one leaves.
    with app.app_context():
        kat = db.session.scalar(select(Sale.id).where(Sale.buyer_username == "made_up_kat"))
    r = client.patch(f"/api/orders/sales/{kat}/product", json={"skuId": s["red"]}, headers=h)
    assert r.status_code == 200 and r.get_json()["changed"] is True
    assert (level(app, s["red"]), level(app, s["blue"])) == ((1, 0), (5, 0))

    # The slips: one per buyer, every row with its product and the price paid.
    slips = client.get(f"/api/orders/shows/{show_id}/order-slips").get_json()
    rows = sorted(
        (b["username"], r["product"], r["priceCents"], r["matched"])
        for b in slips["slips"]
        for r in b["rows"]
    )
    assert rows == [
        ("made_up_kat", "Red bundle", 1200, True),
        ("made_up_lani", "Red bundle", 1200, True),
        ("made_up_moe", "Green bundle", 1200, True),
        ("made_up_nia", "Green bundle", 1200, True),
        ("made_up_ola", "Green bundle", 1200, True),
    ]
    assert slips["totals"] == {"buyers": 5, "items": 5, "gifts": 0}


def test_a_listing_whatnot_no_longer_has_is_asked_for_once_and_can_still_be_linked(
    app, client, no_laptop, sign_in
):
    s = no_laptop
    gone = listing(s, "Flash drop")
    bought(s, s["live"], "made_up_moe", gone)
    # Deleted on Whatnot before the app ever read it.
    del s["fake"].products[gone[0]["id"]]
    for v in [k for k, v in s["fake"].variants.items() if v["product_id"] == gone[0]["id"]]:
        del s["fake"].variants[v]
    read_show(app, s, s["live"])
    with app.app_context():
        row = db.session.scalar(select(WhatnotVariant).where(WhatnotVariant.title == "Flash drop"))
        assert row is not None and row.gone_at is not None  # kept as it was sold
    asked = [c for c in s["fake"].calls if c[1].endswith(f"/products/{gone[0]['id']}")]
    read_show(app, s, s["live"])
    again = [c for c in s["fake"].calls if c[1].endswith(f"/products/{gone[0]['id']}")]
    assert len(asked) == 1 and len(again) == 1  # not asked for at every pass
    # A person can still say which product it was, for this and for its later orders.
    h = sign_in(s["org"], "manager")
    with app.app_context():
        item = db.session.scalar(
            select(ReviewItem.id).where(ReviewItem.status == ReviewStatus.open)
        )
    r = client.post(
        f"/api/review/{item}/resolve",
        json={"action": "assign_sku", "skuId": s["green"], "linkListing": True},
        headers=h,
    )
    assert r.status_code == 200 and r.get_json()["listingLinked"] is True
    assert sales(app) == [("made_up_moe", "paid", "GREEN")]


def test_without_the_right_to_read_inventory_the_orders_are_still_applied(app, no_laptop):
    s = no_laptop
    s["fake"].scopes = {"read:orders", "read:customers", "read:shows", "read:livestreams"}
    green = listing(s, "Green drop")
    bought(s, s["live"], "made_up_moe", green)
    read_show(app, s, s["live"])
    assert sales(app) == [("made_up_moe", "detected", None)]
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotVariant)) == 0


def test_linking_a_listing_is_not_offered_where_winners_pick_or_for_one_marked_not_ours(
    app, client, no_laptop, sign_in
):
    s = no_laptop
    any_lot = listing(s, "CHOOSE ANY PRODUCT YOU WANT")
    bought(s, s["live"], "made_up_moe", any_lot, minutes=19)
    bought(s, s["live"], "made_up_nia", any_lot, minutes=17)
    read_show(app, s, s["live"])
    h = sign_in(s["org"], "manager")
    with app.app_context():
        made = db.session.scalar(select(Show.id).where(Show.whatnot_link == "made"))
        row = db.session.scalar(select(WhatnotVariant.id))
        moe, nia = db.session.scalars(
            select(ReviewItem.id)
            .where(ReviewItem.status == ReviewStatus.open)
            .order_by(ReviewItem.id)
        ).all()
    # This show is sold as winners pick: the listing is a "choose any" lot, never linked.
    assert (
        client.post(f"/api/shows/{made}/mode", json={"mode": "winners_pick"}, headers=h).status_code
        == 200
    )
    body = {"action": "assign_sku", "skuId": s["green"], "linkListing": True}
    r = client.post(f"/api/review/{moe}/resolve", json=body, headers=h)
    assert r.status_code == 200
    assert (r.get_json()["listingLinked"], r.get_json()["alsoResolved"]) == (False, 0)
    assert sales(app) == [("made_up_moe", "paid", "GREEN"), ("made_up_nia", "detected", None)]
    # Back to one product per listing, and the listing marked "none of ours": still not linked.
    assert (
        client.post(f"/api/shows/{made}/mode", json={"mode": "listing"}, headers=h).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/whatnot/catalog/{row}/link", json={"skuId": None}, headers=h
        ).status_code
        == 200
    )
    r = client.post(f"/api/review/{nia}/resolve", json=body, headers=h)
    assert r.status_code == 200 and r.get_json()["listingLinked"] is False
    with app.app_context():
        assert db.session.scalar(select(WhatnotVariant.link)) == "off"


def test_learning_listings_reads_a_few_per_pass_the_latest_orders_first(app, no_laptop):
    s = no_laptop
    made = [listing(s, f"Drop {i}") for i in range(4)]
    for i, m in enumerate(made):
        bought(s, s["live"], f"made_up_{i}", m, minutes=20 - i)  # Drop 3 is the latest
    with app.app_context():
        from app.models import WhatnotShow
        from app.services.whatnot import sync

        client = client_for(s["org"])
        sync.sync_shows(s["org"], client)
        ws = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == s["live"]))
        sync.sync_show_orders(s["org"], client, ws)
        assert listings.learn_listings(s["org"], client, [s["live"]], limit=2) == 2
        titles = set(db.session.scalars(select(WhatnotVariant.title)))
        assert titles == {"Drop 3", "Drop 2"}
        assert listings.learn_listings(s["org"], client, [s["live"]], limit=10) == 2
        assert listings.learn_listings(s["org"], client, [s["live"]]) == 0
        db.session.rollback()


# ---------- found by the stage's adversarial review ----------


def _delete_on_whatnot(s, made):
    del s["fake"].products[made[0]["id"]]
    for v in [k for k, v in s["fake"].variants.items() if v["product_id"] == made[0]["id"]]:
        del s["fake"].variants[v]


def test_a_listing_kept_from_its_order_still_names_its_product_by_its_code(app, no_laptop):
    """Whatnot's SKU on the order line is a product code here. The listing was deleted
    before the app read it: keeping it as it was sold must not hide that code."""
    s = no_laptop
    gone = listing(s, "Blue flash", sku="BLUE")
    bought(s, s["live"], "made_up_zed", gone)
    _delete_on_whatnot(s, gone)
    read_show(app, s, s["live"])
    assert sales(app) == [("made_up_zed", "paid", "BLUE")]
    assert level(app, s["blue"]) == (4, 0)
    with app.app_context():
        row = db.session.scalar(select(WhatnotVariant).where(WhatnotVariant.title == "Blue flash"))
        # kept as it was sold, with Whatnot's SKU; a listing that is gone is not linked
        assert (row.sku_code, row.sku_id, row.gone_at is not None) == ("BLUE", None, True)


def test_a_listing_learnt_under_another_product_id_does_not_break_the_catalog_read(
    app, client, no_laptop, sign_in
):
    """An order line names a real variant under a product id the catalog does not use for
    it. The row kept from the order is the same listing when the catalog is read: it moves
    to its real product, comes back to life, and a person's link on it stands."""
    s = no_laptop
    p, v = listing(s, "Green drop")
    s["fake"].add_order(
        show_id=s["live"],
        buyer="made_up_moe",
        items=[
            {
                "title": "Green drop",
                "price_cents": 900,
                "product_id": "9999999",
                "variant_id": v["id"],
            }
        ],
        created=datetime.now(UTC) - timedelta(minutes=15),
    )
    read_show(app, s, s["live"])
    h = sign_in(s["org"], "manager")
    with app.app_context():
        row = db.session.scalar(select(WhatnotVariant))
        assert (row.product_id, row.gone_at is not None) == ("9999999", True)
        row_id = row.id
    r = client.patch(f"/api/whatnot/catalog/{row_id}/link", json={"skuId": s["green"]}, headers=h)
    assert r.status_code == 200
    with app.app_context():
        counts = listings.sync_catalog(s["org"], client_for(s["org"]))  # raised before the fix
        db.session.commit()
        assert counts
        rows = db.session.scalars(select(WhatnotVariant)).all()
        assert [(r.id, r.product_id, r.gone_at, r.sku_id, r.link) for r in rows] == [
            (row_id, p["id"], None, s["green"], "person")
        ]


def test_one_listing_that_cannot_be_stored_stops_neither_the_others_nor_the_orders(
    app, no_laptop, monkeypatch
):
    s = no_laptop
    good, bad = listing(s, "Good drop", sku="GREEN"), listing(s, "Bad drop")
    bought(s, s["live"], "made_up_moe", good, minutes=16)
    bought(s, s["live"], "made_up_nia", bad, minutes=15)  # the latest: asked for first
    real = listings.store_product

    def store(org_id, product, variants, *, now):
        if product.get("title") == "Bad drop":
            raise ValueError("a listing the app cannot store")
        return real(org_id, product, variants, now=now)

    monkeypatch.setattr(listings, "store_product", store)
    read_show(app, s, s["live"])
    with app.app_context():
        assert set(db.session.scalars(select(WhatnotVariant.title))) == {"Good drop"}
    # the good listing named its sale by its code; the other win waits for a person
    assert sales(app) == [("made_up_moe", "paid", "GREEN"), ("made_up_nia", "detected", None)]


def test_a_listing_linked_to_another_product_is_not_relinked_in_passing(
    app, client, no_laptop, sign_in
):
    s = no_laptop
    made = listing(s, "Mystery drop")
    bought(s, s["live"], "made_up_moe", made)
    read_show(app, s, s["live"])
    h = sign_in(s["org"], "manager")
    with app.app_context():
        row = db.session.scalar(select(WhatnotVariant.id))
        item = db.session.scalar(
            select(ReviewItem.id).where(ReviewItem.status == ReviewStatus.open)
        )
    # Someone links the listing to Blue on the listings page while the win still waits.
    assert (
        client.patch(
            f"/api/whatnot/catalog/{row}/link", json={"skuId": s["blue"]}, headers=h
        ).status_code
        == 200
    )
    got = client.get("/api/review").get_json()["items"][0]["sale"]["listing"]
    assert got["linked"] is True  # the picker then does not offer the box
    r = client.post(
        f"/api/review/{item}/resolve",
        json={"action": "assign_sku", "skuId": s["green"], "linkListing": True},
        headers=h,
    )
    assert r.status_code == 200 and r.get_json()["listingLinked"] is False
    with app.app_context():
        assert db.session.scalar(select(WhatnotVariant.sku_id)) == s["blue"]
    # Changing it on the listings page says what it was.
    assert (
        client.patch(
            f"/api/whatnot/catalog/{row}/link", json={"skuId": s["green"]}, headers=h
        ).status_code
        == 200
    )
    with app.app_context():
        from app.models import AuditLog
        from app.services import activity

        last = db.session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "whatnot.listing_linked")
            .order_by(AuditLog.id.desc())
        ).first()
        assert activity.describe(last.action, last.details) == (
            "Linked the Whatnot listing “Mystery drop” to “Green bundle” (was “Blue bundle”)"
        )


def test_a_page_of_waiting_wins_asks_for_their_listings_once_not_once_each(
    app, client, no_laptop, sign_in
):
    from sqlalchemy import event

    s = no_laptop
    made = listing(s, "Mystery drop")
    sign_in(s["org"], "manager")
    counts = []
    for n, buyers in ((2, ["made_up_a", "made_up_b"]), (6, [f"made_up_{c}" for c in "cdef"])):
        for i, buyer in enumerate(buyers):
            bought(s, s["live"], buyer, made, minutes=20 - i)
        read_show(app, s, s["live"])
        with app.app_context():
            show_id = db.session.scalar(select(Show.id).where(Show.whatnot_link == "made"))
        seen = []
        with app.app_context():
            engine = db.engine

        def count(conn, cursor, statement, *a, seen=seen):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", count)
        try:
            got = client.get(f"/api/orders?show={show_id}").get_json()["orders"]
        finally:
            event.remove(engine, "before_cursor_execute", count)
        waiting = [x for o in got for x in o["sales"] if x["listing"]]
        assert len(waiting) == n
        counts.append(len(seen))
    assert counts[0] == counts[1]  # six waiting wins cost what two do
