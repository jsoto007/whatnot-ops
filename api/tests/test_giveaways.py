"""Giveaways to assign: a giveaway Whatnot lists waits in its own list for a person to say
which product it was. It is never a sale; its product leaves the shelf when assigned and
comes back when changed or taken off. Runs against the in-memory fake Whatnot; buyer names
are made up."""

import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db import db
from app.models import (
    AuditLog,
    GiveawayItem,
    InventoryMovement,
    MovementKind,
    Organization,
    Show,
    WhatnotConnection,
)
from app.services import activity, catalog, dashboard, giveaways, inventory
from app.services.inventory import Actor
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level
from tests.test_whatnot_orders import apply, read, sale_rows

shop = wo.shop
URL = "/api/giveaways"


def give(shop, buyer="made_up_gia", title="Mystery gift", quantity=1, minutes=50):
    return shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer=buyer,
        items=[{"title": title, "price_cents": 0, "quantity": quantity}],
        transaction_type="giveaway",
        created=shop["now"] - timedelta(minutes=minutes),
    )


def blue(app, shop, on_hand=5):
    with app.app_context():
        sku = catalog.create_product(shop["org"], "Blue bundle", skus=[{"code": "BLU-1"}]).skus[0]
        inventory.move(sku.id, MovementKind.restock, on_hand, source_type="test", source_id="b")
        db.session.commit()
        return sku.id


def listed(client):
    return client.get(URL).get_json()


def moves(app):
    with app.app_context():
        return [
            (m.kind.value, m.quantity, m.sku_id)
            for m in db.session.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.source_type == "giveaway")
                .order_by(InventoryMovement.id)
            )
        ]


def test_a_giveaway_waits_in_its_own_list_and_is_never_a_sale(app, client, shop, sign_in):
    give(shop)
    read(app, shop)
    assert apply(app, shop) == {} and sale_rows(app) == []
    sign_in(shop["org"], "manager")
    got = listed(client)
    assert got["waiting"] == 1
    (row,) = got["giveaways"]
    assert (row["buyer"], row["title"], row["quantity"]) == ("made_up_gia", "Mystery gift", 1)
    assert row["showId"] == shop["show"] and row["product"] is None and not row["cancelled"]
    assert level(app, shop["red"]) == (3, 0) and moves(app) == []


def test_naming_the_product_moves_stock_once_and_the_slip_shows_it(app, client, shop, sign_in):
    give(shop)
    read(app, shop)
    h = sign_in(shop["org"], "manager")
    item = listed(client)["giveaways"][0]["id"]
    with app.app_context():
        before = dashboard.show_tiles(db.session.get(Show, shop["show"]))
    r = client.put(f"{URL}/{item}/product", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 200 and r.get_json()["changed"] is True
    assert r.get_json()["giveaway"]["product"]["code"] == "RED"
    assert level(app, shop["red"]) == (2, 0)
    # the same answer again (a retried form) moves nothing
    r = client.put(f"{URL}/{item}/product", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 200 and r.get_json()["changed"] is False
    assert level(app, shop["red"]) == (2, 0)
    assert moves(app) == [("reserve", 1, shop["red"]), ("commit", 1, shop["red"])]
    assert listed(client)["waiting"] == 0
    # not a sale, and no revenue: the show's numbers are as before
    assert sale_rows(app) == []
    with app.app_context():
        after = dashboard.show_tiles(db.session.get(Show, shop["show"]))
        keys = ("itemsSold", "buyers", "revenuePaidCents", "revenuePendingCents")
        assert [after[k] for k in keys] == [before[k] for k in keys]
        log = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "giveaway.product_set")
        ).all()
        assert len(log) == 1
        assert activity.describe(log[0].action, log[0].details) == (
            "Named made_up_gia's giveaway: 1 × “RED”"
        )
    slips = client.get(f"/api/orders/shows/{shop['show']}/order-slips").get_json()
    (slip,) = [s for s in slips["slips"] if s["username"] == "made_up_gia"]
    assert [(r["kind"], r["auction"], r["product"], r["matched"]) for r in slip["rows"]] == [
        ("gift", "Gift", "Red bundle", True)
    ]
    assert slip["items"] == 1


def test_changing_the_product_back_and_forth_moves_stock_every_time(app, client, shop, sign_in):
    give(shop)
    read(app, shop)
    blu = blue(app, shop)
    h = sign_in(shop["org"], "manager")
    item = listed(client)["giveaways"][0]["id"]
    for sku in (shop["red"], blu, shop["red"]):
        r = client.put(f"{URL}/{item}/product", json={"skuId": sku}, headers=h)
        assert r.status_code == 200 and r.get_json()["changed"] is True
    assert level(app, shop["red"]) == (2, 0) and level(app, blu) == (5, 0)
    with app.app_context():
        rows = db.session.scalars(select(GiveawayItem).order_by(GiveawayItem.id)).all()
        assert [(r.sku_id, r.closed_at is None) for r in rows] == [
            (shop["red"], False),
            (blu, False),
            (shop["red"], True),
        ]
        last = db.session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
        assert activity.describe(last.action, last.details) == (
            "Named made_up_gia's giveaway: 1 × “RED” (was “BLU-1”)"
        )
    # taking it off puts it back and the giveaway waits again
    r = client.delete(f"{URL}/{item}/product", headers=h)
    assert r.status_code == 200 and r.get_json()["changed"] is True
    assert r.get_json()["giveaway"]["product"] is None
    assert level(app, shop["red"]) == (3, 0) and listed(client)["waiting"] == 1
    assert client.delete(f"{URL}/{item}/product", headers=h).get_json()["changed"] is False
    assert level(app, shop["red"]) == (3, 0)
    with app.app_context():
        last = db.session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
        assert activity.describe(last.action, last.details) == (
            "Took “RED” off made_up_gia's giveaway; it is back in stock"
        )


def test_a_giveaway_of_two_takes_two_and_prints_two_rows(app, client, shop, sign_in):
    give(shop, quantity=2)
    read(app, shop)
    h = sign_in(shop["org"], "manager")
    item = listed(client)["giveaways"][0]["id"]
    slips = client.get(f"/api/orders/shows/{shop['show']}/order-slips").get_json()
    assert [r["product"] for r in slips["slips"][0]["rows"]] == ["Gift", "Gift"]
    client.put(f"{URL}/{item}/product", json={"skuId": shop["red"]}, headers=h)
    assert level(app, shop["red"]) == (1, 0)
    slips = client.get(f"/api/orders/shows/{shop['show']}/order-slips").get_json()
    rows = slips["slips"][0]["rows"]
    assert [(r["product"], r["continued"]) for r in rows] == [
        ("Red bundle", False),
        ("Red bundle", True),
    ]


def test_a_giveaway_whatnot_cancelled_takes_no_product_and_gives_its_own_back(
    app, client, shop, sign_in
):
    kept, gone = give(shop, "made_up_gia"), give(shop, "made_up_hal", minutes=40)
    read(app, shop)
    h = sign_in(shop["org"], "manager")
    by = {g["buyer"]: g["id"] for g in listed(client)["giveaways"]}
    client.put(f"{URL}/{by['made_up_gia']}/product", json={"skuId": shop["red"]}, headers=h)
    for o in (kept, gone):
        shop["fake"].set_order_status(o["id"], "cancelled")
    read(app, shop)
    r = client.put(f"{URL}/{by['made_up_hal']}/product", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 409 and "cancelled" in r.get_json()["error"]
    got = listed(client)
    # the one with a product stays, marked, until the product is taken off; the other is gone
    assert [(g["buyer"], g["cancelled"]) for g in got["giveaways"]] == [("made_up_gia", True)]
    assert got["waiting"] == 0
    slips = client.get(f"/api/orders/shows/{shop['show']}/order-slips").get_json()
    assert slips["slips"] == []  # neither ships
    assert level(app, shop["red"]) == (2, 0)
    assert client.delete(f"{URL}/{by['made_up_gia']}/product", headers=h).status_code == 200
    assert level(app, shop["red"]) == (3, 0) and listed(client)["giveaways"] == []


def test_only_people_who_settle_review_name_a_giveaway_and_only_their_shops(
    app, client, shop, sign_in
):
    give(shop)
    read(app, shop)
    with app.app_context():
        other = Organization(name="Other", slug="other-shop")
        db.session.add(other)
        db.session.flush()
        theirs = catalog.create_product(other.id, "Their bundle", skus=[{"code": "T"}]).skus[0].id
        old = catalog.create_product(shop["org"], "Old bundle", skus=[{"code": "OLD"}]).skus[0]
        old.active = False
        db.session.commit()
        other_id, old_id = other.id, old.id
    h = sign_in(shop["org"], "fulfillment")
    item = listed(client)["giveaways"][0]["id"]  # reading is for everyone who sees orders
    url = f"{URL}/{item}/product"
    assert client.put(url, json={"skuId": shop["red"]}, headers=h).status_code == 403
    assert client.delete(url, headers=h).status_code == 403
    h = sign_in(shop["org"], "manager", email="m@x.test")
    assert client.put(url, json={}, headers=h).status_code == 400
    # only a whole number names a product: 1.9 or true would quietly name some other one
    for bad in (
        [shop["red"]],
        {"skuId": shop["red"] + 0.9},
        {"skuId": True},
        {"skuId": "1"},
        {"skuId": 2**31},
        {"skuId": 10**20},
    ):
        assert client.put(url, json=bad, headers=h).status_code == 400
    for sku in (theirs, old_id, 999999):
        r = client.put(url, json={"skuId": sku}, headers=h)
        assert r.status_code == 400 and r.get_json()["error"] == "That product is not in use."
    assert (
        client.put(f"{URL}/999999/product", json={"skuId": shop["red"]}, headers=h).status_code
        == 404
    )
    assert level(app, shop["red"]) == (3, 0) and moves(app) == []
    # another shop sees none of it and cannot touch it
    h = sign_in(other_id, "owner", email="t@x.test")
    assert listed(client) == {"giveaways": [], "waiting": 0}
    assert client.put(url, json={"skuId": theirs}, headers=h).status_code == 404
    assert client.delete(url, headers=h).status_code == 404


def test_a_viewers_giveaway_line_in_chat_is_not_a_giveaway_to_assign(app, client, shop, sign_in):
    shop["say"](
        app,
        [("made_up_vic", "made_up_vic won the giveaway!"), ("", "made_up_vic won the giveaway!")],
    )
    read(app, shop)
    sign_in(shop["org"], "manager")
    assert listed(client) == {"giveaways": [], "waiting": 0}


def test_whatnots_giveaways_replace_the_ones_read_from_chat_on_the_slips(
    app, client, shop, sign_in
):
    from tests.test_pipeline import run

    shop["say"](app, [("host_shop", "made_up_gia won the giveaway!")])
    run(app, shop["show"])
    sign_in(shop["org"], "manager")
    url = f"/api/orders/shows/{shop['show']}/order-slips"
    assert [s["username"] for s in client.get(url).get_json()["slips"]] == ["made_up_gia"]
    give(shop, "made_up_gia")
    give(shop, "made_up_hal", minutes=40)
    read(app, shop)
    slips = client.get(url).get_json()["slips"]
    assert sorted((s["username"], len(s["rows"])) for s in slips) == [
        ("made_up_gia", 1),
        ("made_up_hal", 1),
    ]


def test_a_show_over_before_the_shop_followed_whatnot_is_not_listed(app, client, shop, sign_in):
    give(shop)
    read(app, shop)
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        conn.auto_since = datetime.now(UTC)  # the show ended five minutes before this
        db.session.commit()
    sign_in(shop["org"], "manager")
    assert listed(client) == {"giveaways": [], "waiting": 0}


def test_a_show_recorded_twice_lists_and_prints_each_giveaway_once(app, client, shop, sign_in):
    give(shop)
    read(app, shop)
    with app.app_context():
        first = db.session.get(Show, shop["show"])
        twin = Show(
            organization_id=shop["org"],
            show_key="2026-10-19T20:05:00.000Z",
            started_at=first.started_at,
            show_date=first.show_date,
            whatnot_show_id=first.whatnot_show_id,
        )
        db.session.add(twin)
        db.session.commit()
        assert len(giveaways.for_show(first)) + len(giveaways.for_show(twin)) == 1
    sign_in(shop["org"], "manager")
    assert listed(client)["waiting"] == 1


def test_two_people_naming_one_giveaway_at_once_leave_one_product_on_it(app, shop):
    give(shop)
    read(app, shop)
    blu = blue(app, shop)
    with app.app_context():
        item = giveaways.listing(shop["org"])["giveaways"][0]["id"]
    errors = []
    gate = threading.Barrier(8)

    def go(sku_id):
        try:
            with app.app_context():
                gate.wait(timeout=10)
                sku = catalog.get_sku(shop["org"], sku_id)
                giveaways.assign(shop["org"], item, sku, actor=Actor())
                db.session.commit()
        except Exception as e:  # noqa: BLE001 - the test reports whatever went wrong
            errors.append(repr(e))

    threads = [threading.Thread(target=go, args=(shop["red"] if i % 2 else blu,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    with app.app_context():
        open_rows = db.session.scalars(
            select(GiveawayItem).where(GiveawayItem.closed_at.is_(None))
        ).all()
        assert len(open_rows) == 1
        # exactly one unit is off the shelf, and the counts match the ledger
        red, blu_ = level(app, shop["red"]), level(app, blu)
        assert red[1] == 0 and blu_[1] == 0
        assert (3 - red[0]) + (5 - blu_[0]) == 1
        for sku_id, (on_hand, reserved) in ((shop["red"], red), (blu, blu_)):
            assert inventory.recompute(catalog.get_sku(shop["org"], sku_id)) == (on_hand, reserved)
        assert db.session.scalar(select(func.count()).select_from(GiveawayItem)) >= 1
