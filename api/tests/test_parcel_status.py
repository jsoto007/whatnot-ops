"""Where a shipped order's parcel is, as Whatnot last said: the Orders page's Shipped tab
(services/whatnot/shipping.parcels_of). Buyers are made up."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select

from app.db import db
from app.models import Order, WhatnotConnection
from app.services.whatnot import shipping
from tests import test_whatnot_orders as wo
from tests.test_pipeline import run
from tests.test_whatnot_orders import apply, order, read, win
from tests.test_whatnot_shipping import parcel, read_shipments

shop = wo.shop  # the same connected shop and matched show as the orders tests

BUYERS = ("buyer_a", "buyer_b", "buyer_c", "buyer_d", "buyer_e")


def shipped(app, client, shop, sign_in):
    """Five buyers whose parcels the carrier took, and whose orders were marked shipped.
    Returns the sign-in header and each buyer's parcel on Whatnot."""
    shop["say"](app, [win(b, "Red bundle") for b in BUYERS])
    run(app, shop["show"])
    made = {
        b: parcel(
            shop, b, [order(shop, b, "Red bundle")], status="dropped_off", tracking=f"9400{b[-1]}"
        )
        for b in BUYERS
    }
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    h = sign_in(shop["org"], role="fulfillment")
    r = client.post(f"/api/shipping/shows/{shop['show']}/mark-shipped", headers=h)
    assert r.get_json()["marked"] == 5
    return h, made


def listed(client, query="?status=shipped"):
    return {o["buyer"]: o for o in client.get(f"/api/orders{query}").get_json()["orders"]}


def test_the_shipped_tab_says_where_each_parcel_is(app, client, shop, sign_in):
    _, made = shipped(app, client, shop, sign_in)
    # Just marked shipped: the carrier has every parcel.
    assert {b: o["parcel"]["state"] for b, o in listed(client).items()} == dict.fromkeys(
        BUYERS, "moving"
    )

    fake = shop["fake"]
    fake.set_shipment(made["buyer_a"]["id"], status="in_transit")
    fake.set_shipment(made["buyer_b"]["id"], status="delivered")
    fake.set_shipment(made["buyer_c"]["id"], status="failed")
    fake.set_shipment(made["buyer_d"]["id"], status="label_generated")  # never handed over
    fake.set_shipment(made["buyer_e"]["id"], status="cancelled")
    read_shipments(app, shop)

    orders = listed(client)
    assert {b: (o["parcel"] or {}).get("state") for b, o in orders.items()} == {
        "buyer_a": "moving",
        "buyer_b": "delivered",
        "buyer_c": "failed",
        "buyer_d": "waiting",
        "buyer_e": None,  # its only parcel was cancelled: Whatnot lists nothing to follow
    }
    a = orders["buyer_a"]["parcel"]
    assert a["changedAt"] and len(a["parcels"]) == 1
    assert (a["parcels"][0]["status"], a["parcels"][0]["tracking"]) == ("in_transit", "9400a")
    assert a["parcels"][0]["changedAt"] and "address" not in str(a).lower()

    # The order's own page says the same.
    one = client.get(f"/api/orders/{orders['buyer_c']['id']}").get_json()["order"]
    assert one["parcel"]["state"] == "failed"


def test_a_parcel_the_carrier_still_has_not_got_after_three_days_is_late(
    app, client, shop, sign_in
):
    _, made = shipped(app, client, shop, sign_in)
    shop["fake"].set_shipment(made["buyer_a"]["id"], status="label_generated")
    read_shipments(app, shop)
    assert listed(client)["buyer_a"]["parcel"]["state"] == "waiting"
    with app.app_context():
        o = db.session.scalar(select(Order).where(Order.buyer_username == "buyer_a"))
        o.shipped_at = datetime.now(UTC) - shipping.LATE_AFTER - timedelta(hours=1)
        db.session.commit()
    assert listed(client)["buyer_a"]["parcel"]["state"] == "late"
    # Once the carrier has it, it is not late any more, however long it took.
    shop["fake"].set_shipment(made["buyer_a"]["id"], status="in_transit")
    read_shipments(app, shop)
    assert listed(client)["buyer_a"]["parcel"]["state"] == "moving"


def test_a_buyer_with_two_parcels_is_delivered_only_when_both_are(app, client, shop, sign_in):
    shop["say"](app, [win("buyer_a", "Red bundle"), win("buyer_a", "Red bundle")])
    run(app, shop["show"])
    first = parcel(shop, "buyer_a", [order(shop, "buyer_a", "Red bundle")], status="dropped_off")
    second = parcel(shop, "buyer_a", [order(shop, "buyer_a", "Red bundle")], status="dropped_off")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    h = sign_in(shop["org"], role="fulfillment")
    client.post(f"/api/shipping/shows/{shop['show']}/mark-shipped", headers=h)
    shop["fake"].set_shipment(first["id"], status="delivered")
    read_shipments(app, shop)
    p = listed(client)["buyer_a"]["parcel"]
    assert p["state"] == "moving" and len(p["parcels"]) == 2
    shop["fake"].set_shipment(second["id"], status="delivered")
    read_shipments(app, shop)
    assert listed(client)["buyer_a"]["parcel"]["state"] == "delivered"
    shop["fake"].set_shipment(second["id"], status="failed")
    read_shipments(app, shop)
    assert listed(client)["buyer_a"]["parcel"]["state"] == "failed"


def test_only_shipped_orders_of_a_connected_shop_carry_a_parcel(app, client, shop, sign_in):
    shop["say"](app, [win("buyer_a", "Red bundle")])
    run(app, shop["show"])
    parcel(shop, "buyer_a", [order(shop, "buyer_a", "Red bundle")], status="label_generated")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    sign_in(shop["org"], role="fulfillment")
    # Not shipped yet: the Shipping page is where its label and parcel are.
    assert listed(client, "")["buyer_a"]["parcel"] is None

    with app.app_context():
        o = db.session.scalar(select(Order).where(Order.buyer_username == "buyer_a"))
        o.status = o.status.__class__("shipped")
        o.shipped_at = datetime.now(UTC)
        db.session.commit()
    assert listed(client)["buyer_a"]["parcel"]["state"] == "waiting"

    # Disconnected: what was read earlier is not shown as if it were current.
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).disconnected_at = datetime.now(UTC)
        db.session.commit()
    assert listed(client)["buyer_a"]["parcel"] is None


def test_many_orders_are_looked_up_together(app, client, shop, sign_in):
    shipped(app, client, shop, sign_in)
    with app.app_context():
        orders = db.session.scalars(select(Order)).all()
        asked = []

        def note(conn, cursor, statement, *rest):
            asked.append(statement)

        event.listen(db.engine, "before_cursor_execute", note)
        try:
            out = shipping.parcels_of(shop["org"], list(orders))
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        assert len(out) == 5
        # The connection, the shows, the orders' parcels, the parcels: not one round per order.
        assert len(asked) <= 4, asked
