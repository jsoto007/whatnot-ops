"""The carrier has the parcel, the order is still open: it ships anyway (Javier's call,
2026-11-21). A win with no product named leaves as it is, its product question is closed,
stock is not touched, and the Shipped tab goes on saying where the parcel is.

Against the in-memory fake Whatnot; never the live API. Buyers are made up.
"""

import pytest
from sqlalchemy import select

from app.db import db
from app.models import Order, ReviewItem, Sale, Show
from app.services import dashboard, pipeline
from app.services import orders as orders_service
from app.services.whatnot import shipping
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import LOT, apply, order, read, win
from tests.test_whatnot_shipping import parcel, read_shipments, view

shop = wo.shop


def _open_order_in_a_parcel(app, shop, status="in_transit"):
    """mika.r won a named product and a "choose any" lot nobody named; both orders are paid
    on Whatnot and sit in one parcel."""
    shop["say"](app, [win("mika.r", "Red bundle"), win("mika.r", LOT, "7.00")])
    run(app, shop["show"])
    a, b = order(shop, "mika.r", "Red bundle"), order(shop, "mika.r", LOT, 700)
    parcel(shop, "mika.r", [a, b], status=status, tracking="9400CCC")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)


def _mark(app, shop) -> int:
    with app.app_context():
        n = shipping.mark_shipped(db.session.get(Show, shop["show"]))
        db.session.commit()
        return n


def _reviews(app):
    with app.app_context():
        return sorted(
            (r.reason, r.status.value, (r.resolution or {}).get("by"))
            for r in db.session.scalars(select(ReviewItem).where(ReviewItem.sale_id.is_not(None)))
        )


def test_an_open_order_ships_when_the_carrier_has_its_parcel(app, shop):
    _open_order_in_a_parcel(app, shop)
    before = level(app, shop["red"])
    with app.app_context():
        o = db.session.scalar(select(Order))
        assert o.status.value == "open"  # the unnamed win keeps it open
        assert ("unmatched_item", "open", None) in _reviews(app)
    assert view(app, shop)["toMarkShipped"] == 1
    assert _mark(app, shop) == 1
    with app.app_context():
        o = db.session.scalar(select(Order))
        assert (o.status.value, o.tracking) == ("shipped", "9400CCC") and o.shipped_at
        by_item = {s.item_text: s for s in o.sales}
        named, unnamed = by_item["Red bundle"], by_item[LOT]
        assert named.status.value == "fulfilled"
        # The unnamed win stays as it was: no product, never paid or fulfilled here.
        assert (unnamed.status.value, unnamed.sku_id) == ("detected", None)
        assert unnamed.status_reason == "shipped with no product named (the carrier has it)"
        # Never on the pick list or asked about again.
        assert orders_service.to_pick_ids([unnamed.id]) == set()
        assert pipeline.ask_for_product(unnamed) is None
        # The Shipped tab goes on saying where the parcel is.
        where = shipping.parcels_of(shop["org"], [o])[o.id]
        assert (where["state"], where["parcels"][0]["tracking"]) == ("moving", "9400CCC")
    assert ("unmatched_item", "dismissed", "shipped") in _reviews(app)
    assert not [r for r in _reviews(app) if r[1] == "open"]
    assert level(app, shop["red"]) == before  # shipping moves no stock
    assert view(app, shop)["toMarkShipped"] == 0
    assert _mark(app, shop) == 0  # a second read marks nothing twice


def test_the_parcel_is_followed_to_delivered(app, shop):
    _open_order_in_a_parcel(app, shop)
    assert _mark(app, shop) == 1
    with app.app_context():
        s = db.session.scalar(select(shipping.WhatnotShipment))
        shop["fake"].set_shipment(s.whatnot_id, status="delivered")
    read_shipments(app, shop)
    with app.app_context():
        o = db.session.scalar(select(Order))
        assert shipping.parcels_of(shop["org"], [o])[o.id]["state"] == "delivered"
        assert o.status.value == "shipped"


def test_an_order_of_only_an_unnamed_win_ships_too(app, shop):
    shop["say"](app, [win("mika.r", LOT, "7.00")])
    run(app, shop["show"])
    parcel(shop, "mika.r", [order(shop, "mika.r", LOT, 700)], status="in_transit", tracking="94D")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    assert _mark(app, shop) == 1
    with app.app_context():
        o = db.session.scalar(select(Order))
        assert o.status.value == "shipped"
        assert [s.status.value for s in o.sales] == ["detected"]


def test_a_label_alone_ships_nothing(app, shop):
    """Only the carrier's scan counts: a label made is not a parcel gone."""
    _open_order_in_a_parcel(app, shop, status="label_generated")
    assert view(app, shop)["toMarkShipped"] == 0
    assert _mark(app, shop) == 0
    with app.app_context():
        assert db.session.scalar(select(Order)).status.value == "open"
    assert ("unmatched_item", "open", None) in _reviews(app)


def test_a_win_whatnot_has_no_order_for_still_holds_the_order(app, shop):
    """Only chat saw this second win: Whatnot has no paid order for it and it is in no
    parcel, so the carrier's news says nothing about it."""
    shop["say"](app, [win("mika.r", "Red bundle"), win("mika.r", LOT, "7.00")])
    run(app, shop["show"])
    parcel(shop, "mika.r", [order(shop, "mika.r", "Red bundle")], status="in_transit")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    assert view(app, shop)["toMarkShipped"] == 0
    assert _mark(app, shop) == 0
    with app.app_context():
        assert db.session.scalar(select(Order)).status.value == "open"
        assert db.session.scalar(select(Sale).where(Sale.item_text == LOT)).status.value == (
            "detected"
        )


def test_money_flags_stay_open_when_the_order_ships(app, shop):
    _open_order_in_a_parcel(app, shop)
    with app.app_context():
        named = db.session.scalar(select(Sale).where(Sale.item_text == "Red bundle"))
        pipeline.open_review(
            shop["org"],
            "won_after_shipping",
            "a money question the carrier does not answer",
            show_id=shop["show"],
            sale_id=named.id,
        )
        db.session.commit()
    assert _mark(app, shop) == 1
    assert ("won_after_shipping", "open", None) in _reviews(app)
    assert ("unmatched_item", "dismissed", "shipped") in _reviews(app)


# ---------- what an independent review broke, and now holds (2026-11-22) ----------


def _unnamed(title=LOT):
    return db.session.scalar(select(Sale).where(Sale.item_text == title))


def _sold(app, shop):
    with app.app_context():
        t = dashboard.show_tiles(db.session.get(Show, shop["show"]))
        return t["revenueSoldCents"], t["toPick"]


def test_a_winner_chooses_lot_that_left_keeps_its_money_and_needs_nobody(
    app, client, shop, sign_in
):
    """A "CHOOSE ANY…" title is never "not one of ours", so once it left the pick list it was
    in no list at all: 700 cents fell out of the tiles and the shipped order read as
    needing a decision."""
    _open_order_in_a_parcel(app, shop)
    assert _sold(app, shop) == (3200, 1)  # 2500 named + 700 to pick
    assert _mark(app, shop) == 1
    assert _sold(app, shop) == (3200, 0)  # the same money, nothing to pick
    sign_in(shop["org"], role="fulfillment")
    got = client.get("/api/orders?status=shipped").get_json()["orders"][0]
    assert got["verified"] is True
    left = next(s for s in got["sales"] if s["item"] == LOT)
    assert (left["toPick"], left["notOurs"], left["leftUnnamed"]) == (False, True, True)
    named = next(s for s in got["sales"] if s["item"] != LOT)
    assert named["leftUnnamed"] is False


def test_another_open_question_never_brings_the_product_question_back(app, shop):
    """The win's price differed from Whatnot's order, so a second item was open on it. That
    kept it "to pick" on a shipped order, and naming it took a second unit out of stock."""
    _open_order_in_a_parcel(app, shop)
    with app.app_context():
        flag = pipeline.open_review(
            shop["org"],
            "price_mismatch",
            "the stream said 7.00, Whatnot's order says 8.00",
            show_id=shop["show"],
            sale_id=_unnamed().id,
        )
        db.session.commit()
        flag_id = flag.id
    before = level(app, shop["red"])
    assert _mark(app, shop) == 1
    read(app, shop)
    apply(app, shop)  # the next orders pass asks nothing again
    with app.app_context():
        s = _unnamed()
        assert orders_service.to_pick_ids([s.id]) == set()
        assert not pipeline.must_name(s) and pipeline.ask_for_product(s) is None
        assert orders_service.settled_unnamed_ids([s]) == {s.id}
        # The money question is still there for a person, but it cannot name the product.
        flag = db.session.get(ReviewItem, flag_id)
        assert flag.status.value == "open"
        with pytest.raises(pipeline.ReviewError, match="already shipped with no product"):
            pipeline.resolve(
                flag, "assign_sku", {"skuId": shop["red"]}, user=None, actor=pipeline.WHATNOT_ACTOR
            )
        db.session.rollback()
        assert _unnamed().status.value == "detected"
    assert [r for r in _reviews(app) if r[0] == "unmatched_item" and r[1] == "open"] == []
    assert level(app, shop["red"]) == before
    assert _sold(app, shop) == (3200, 0)


def test_a_question_raised_after_it_left_does_not_reopen_it_either(app, shop):
    _open_order_in_a_parcel(app, shop)
    assert _mark(app, shop) == 1
    with app.app_context():
        pipeline.open_review(
            shop["org"],
            pipeline.DECLINE_SEEN,
            "a decline read late from the Sold tab",
            show_id=shop["show"],
            sale_id=_unnamed().id,
        )
        db.session.commit()
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        s = _unnamed()
        assert orders_service.to_pick_ids([s.id]) == set() and not pipeline.must_name(s)
    assert [r for r in _reviews(app) if r[0] == "unmatched_item" and r[1] == "open"] == []


def test_an_order_that_is_busy_does_not_lose_the_rest_of_the_pass(app, shop, monkeypatch):
    """A person deciding a Review item of one order at that very moment made Postgres stop
    one of the two (they lock in opposite order); the error ended the whole pass."""
    from sqlalchemy.exc import OperationalError

    shop["say"](app, [win("aaa", "Red bundle"), win("bbb", "Red bundle")])
    run(app, shop["show"])
    parcel(shop, "aaa", [order(shop, "aaa", "Red bundle")], status="in_transit", tracking="94A")
    parcel(shop, "bbb", [order(shop, "bbb", "Red bundle")], status="in_transit", tracking="94B")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    real = orders_service.ship_by_carrier

    def busy_once(o, **kw):
        if o.buyer_username == "aaa":
            raise OperationalError("UPDATE review_items", {}, Exception("deadlock detected"))
        return real(o, **kw)

    monkeypatch.setattr(orders_service, "ship_by_carrier", busy_once)
    assert _mark(app, shop) == 1
    with app.app_context():
        got = {o.buyer_username: o.status.value for o in db.session.scalars(select(Order))}
    assert got == {"aaa": "paid", "bbb": "shipped"}
    monkeypatch.setattr(orders_service, "ship_by_carrier", real)
    assert _mark(app, shop) == 1  # the next pass ships it


def test_a_win_that_left_is_not_named_after_whatnot_cancels_its_order_either(app, shop):
    """Whatnot cancelled the order after the parcel left, and nobody has applied that yet:
    the sale still stands here. The guard looked at the payment, so it let the product be
    named and a unit reserved for an item already gone."""
    from app.models import WhatnotOrder

    _open_order_in_a_parcel(app, shop)
    with app.app_context():
        s = _unnamed()
        flag = pipeline.open_review(
            shop["org"],
            "price_mismatch",
            "the stream said 7.00, Whatnot's order says 8.00",
            show_id=shop["show"],
            sale_id=s.id,
        )
        db.session.commit()
        flag_id, order_id = flag.id, s.external_order_id
    before = level(app, shop["red"])
    assert _mark(app, shop) == 1
    with app.app_context():
        stored = db.session.scalar(select(WhatnotOrder).where(WhatnotOrder.whatnot_id == order_id))
        stored.status = "cancelled"  # read from Whatnot, not applied to the sale yet
        db.session.commit()
        s = _unnamed()
        assert s.status.value == "detected"
        assert orders_service.left_unnamed_ids([s]) == {s.id}
        with pytest.raises(pipeline.ReviewError, match="already shipped with no product"):
            pipeline.resolve(
                db.session.get(ReviewItem, flag_id),
                "assign_sku",
                {"skuId": shop["red"]},
                user=None,
                actor=pipeline.WHATNOT_ACTOR,
            )
        db.session.rollback()
    assert level(app, shop["red"]) == before
