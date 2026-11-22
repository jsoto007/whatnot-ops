"""'They paid after all' while Whatnot's order says the payment failed (Javier's call,
2026-11-20): refused. Whatnot decides whether a win was paid; its paid order restores the
sale. Before this, the person's restore was undone by the next read of the failed order
(stock back out and in again, the decline reopened). Buyer names are made up; Whatnot is the
in-memory fake."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db import db
from app.models import ChatMessage, InventoryMovement, ReviewItem, ReviewStatus, Sale
from app.services import dashboard, pipeline
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import apply, order, read, snapshot, win

shop = wo.shop
RAE = "made_up_rae"


def _declined_by_whatnot(app, shop):
    shop["say"](app, [win(RAE, "Red bundle", "25.00")])
    run(app, shop["show"])
    o = order(shop, RAE, "Red bundle")
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    return o


def _item(app):
    with app.app_context():
        item = db.session.scalar(
            select(ReviewItem).where(
                ReviewItem.reason == "payment_declined", ReviewItem.status == ReviewStatus.open
            )
        )
        return item.id if item else None


def _sale(app):
    with app.app_context():
        s = db.session.scalar(select(Sale))
        return s.status.value, s.external_order_id


def _moves(app):
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(InventoryMovement))


def _paid_after_all(client, headers, item_id):
    return client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "set_status", "status": "paid"},
        headers=headers,
    )


def test_they_paid_after_all_is_refused_while_whatnots_order_says_failed(
    app, client, shop, sign_in
):
    o = _declined_by_whatnot(app, shop)
    h = sign_in(shop["org"], role="manager", email="made_up_mgr@x.test")
    before, moves = snapshot(app), _moves(app)
    assert level(app, shop["red"]) == (3, 0)  # back in stock
    r = _paid_after_all(client, h, _item(app))
    assert r.status_code == 409
    words = r.get_json()["error"]
    assert f"#{o['id']}" in words and "failed" in words
    assert snapshot(app) == before and _moves(app) == moves
    assert level(app, shop["red"]) == (3, 0)
    assert _sale(app)[0] == "payment_failed"
    assert _item(app) is not None  # the decision stays open: "Keep declined" still works


def test_whatnots_paid_order_restores_it_and_reading_again_changes_nothing(
    app, client, shop, sign_in
):
    """The way back is Whatnot's: once its order says paid, applying the orders restores
    the sale, and every read after that leaves it alone (the bug was a restore undone)."""
    o = _declined_by_whatnot(app, shop)
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    read(app, shop)
    apply(app, shop)
    assert _sale(app) == ("paid", o["id"])
    assert level(app, shop["red"]) == (2, 0)
    assert _item(app) is None
    before, moves = snapshot(app), _moves(app)
    for _ in range(3):
        read(app, shop)
        apply(app, shop)
    assert snapshot(app) == before and _moves(app) == moves
    assert level(app, shop["red"]) == (2, 0)


def test_an_order_still_taking_payment_refuses_nothing(app, client, shop, sign_in):
    """Whatnot no longer says failed (the buyer is paying again): the person may restore."""
    o = _declined_by_whatnot(app, shop)
    shop["fake"].set_order_status(o["id"], "pending")
    read(app, shop)  # stored, not applied
    h = sign_in(shop["org"], role="manager", email="made_up_mgr@x.test")
    r = _paid_after_all(client, h, _item(app))
    assert r.status_code == 200, r.get_json()
    assert _sale(app)[0] == "paid"
    assert level(app, shop["red"]) == (2, 0)


def _stream_declined_with_failed_order_read(app, shop):
    """A shop that compares first: the video player showed the card declined, and Whatnot's
    failed order for the win was read but nobody has applied it yet (not tied to the sale)."""
    shop["say"](app, [win(RAE, "Red bundle", "25.00")])
    run(app, shop["show"])
    o = order(shop, RAE, "Red bundle")
    shop["fake"].set_order_status(o["id"], "failed")
    shop["say"](app, [("", f"{RAE} payment declined for Red bundle for $25.00", "player")])
    run(app, shop["show"])
    read(app, shop)  # stored, not applied
    assert _sale(app) == ("payment_failed", None)
    return o


def test_a_failed_order_read_but_not_applied_refuses_the_restore_too(app, client, shop, sign_in):
    """Restored now, applying that order would decline the sale again, and Whatnot's paid
    order after that could no longer restore it (a paid win left uncounted; second
    independent check of 7278d9c, probe 2h)."""
    o = _stream_declined_with_failed_order_read(app, shop)
    h = sign_in(shop["org"], role="manager", email="made_up_mgr@x.test")
    before, moves = snapshot(app), _moves(app)
    r = _paid_after_all(client, h, _item(app))
    assert r.status_code == 409 and f"#{o['id']}" in r.get_json()["error"]
    assert snapshot(app) == before and _moves(app) == moves
    apply(app, shop)  # the person applies Whatnot's orders: nothing to undo
    assert _sale(app)[0] == "payment_failed" and _moves(app) == moves
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    read(app, shop)
    apply(app, shop)
    assert _sale(app) == ("paid", o["id"])
    assert level(app, shop["red"]) == (2, 0)


def _paid_in_a_file(client, headers, shop):
    return client.post(
        f"/api/orders/shows/{shop['show']}/reconcile",
        json={"csv": f"buyer,item,status\n{RAE},Red bundle,paid\n", "apply": True},
        headers=headers,
    )


def test_a_payment_file_paid_row_never_restores_what_whatnot_says_failed(
    app, client, shop, sign_in
):
    """The file restored it; the next read of the failed order declined it again; and
    Whatnot's paid order was then 'unmatched': the paid win stayed uncounted (probe 1c)."""
    o = _declined_by_whatnot(app, shop)
    h = sign_in(shop["org"], role="manager", email="made_up_mgr@x.test")
    before, moves = snapshot(app), _moves(app)
    r = _paid_in_a_file(client, h, shop)
    assert r.status_code == 200, r.get_json()
    assert snapshot(app) == before and _moves(app) == moves
    assert level(app, shop["red"]) == (3, 0)
    read(app, shop)
    apply(app, shop)
    assert _sale(app)[0] == "payment_failed" and _moves(app) == moves
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    read(app, shop)
    apply(app, shop)
    assert _sale(app) == ("paid", o["id"])
    assert level(app, shop["red"]) == (2, 0)


def test_a_payment_file_still_restores_a_decline_whatnot_has_no_order_for(
    app, client, shop, sign_in
):
    shop["say"](app, [win(RAE, "Red bundle", "25.00")])
    run(app, shop["show"])
    shop["say"](app, [("", f"{RAE} payment declined for Red bundle for $25.00", "player")])
    run(app, shop["show"])
    assert _sale(app)[0] == "payment_failed"
    h = sign_in(shop["org"], role="manager", email="made_up_mgr@x.test")
    assert _paid_in_a_file(client, h, shop).status_code == 200
    assert _sale(app)[0] == "paid"
    assert level(app, shop["red"]) == (2, 0)


# ---------- a person restored before Whatnot had any order, then Whatnot declined it ----------


def _say_at(app, shop, lines, when):
    with app.app_context():
        n0 = db.session.scalar(select(func.max(ChatMessage.seq))) or 0
    shop["say"](app, lines)
    with app.app_context():
        db.session.execute(update(ChatMessage).where(ChatMessage.seq > n0).values(captured_at=when))
        db.session.commit()


def _owner_restores(app, shop):
    from tests.test_pipeline import _owner

    with app.app_context():
        item = db.session.scalar(
            select(ReviewItem).where(
                ReviewItem.reason == "payment_declined", ReviewItem.status == ReviewStatus.open
            )
        )
        pipeline.resolve(item, "set_status", {"status": "paid"}, user=_owner(shop["org"]))
        db.session.commit()


def _paid_charged(app):
    """What Whatnot charged for the sales counted as paid here, in cents."""
    with app.app_context():
        paid = db.session.scalars(select(Sale.id).where(Sale.status == "paid")).all()
        return sum(dashboard.charged_cents(paid).values())


def _restored_then_declined_by_whatnot(app, shop):
    """The stream showed the card declined; Whatnot had no order yet, so a person restored
    the sale (allowed); then Whatnot's failed order was read and applied: declined again."""
    first = datetime.now(UTC) - timedelta(minutes=50)
    _say_at(app, shop, [win(RAE, "Red bundle", "25.00")], first)
    run(app, shop["show"])
    decline = ("", f"{RAE} payment declined for Red bundle for $25.00", "player")
    _say_at(app, shop, [decline], first + timedelta(seconds=30))
    run(app, shop["show"])
    read(app, shop)  # no order on Whatnot yet
    _owner_restores(app, shop)
    assert _sale(app)[0] == "paid"
    o = order(shop, RAE, "Red bundle", created=first + timedelta(seconds=10))
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    apply(app, shop)
    assert _sale(app)[0] == "payment_failed"
    assert level(app, shop["red"]) == (3, 0)
    return o, first


@pytest.mark.parametrize("way", ["same order", "new order 10 min later"])
def test_whatnots_paid_order_counts_after_a_restore_and_a_second_decline(app, shop, way):
    """Before: Whatnot's paid row was 'declined twice; paid in the file' (unmatched): the paid
    win stayed uncounted (tile 0 against 2500 charged) and one unit too many stayed on the
    shelf (second independent check of 3266531, probes P1same / P1new10)."""
    o, first = _restored_then_declined_by_whatnot(app, shop)
    if way == "same order":
        shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    else:
        order(shop, RAE, "Red bundle", created=first + timedelta(minutes=10))
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        sales = [
            (s.status.value, s.external_order_id)
            for s in db.session.scalars(select(Sale).order_by(Sale.id))
        ]
        opened = sorted(
            r.reason
            for r in db.session.scalars(
                select(ReviewItem).where(ReviewItem.status == ReviewStatus.open)
            )
        )
    paid = [s for s in sales if s[0] == "paid"]
    assert len(paid) == 1 and sales[0][0] == "payment_failed"
    assert level(app, shop["red"]) == (2, 0)  # one unit out, once
    assert _paid_charged(app) == 2500  # counted once
    assert "unmatched_payment" not in opened and "payment_declined" not in opened
    before, moves = snapshot(app), _moves(app)
    for _ in range(3):  # reading and applying again changes nothing
        read(app, shop)
        apply(app, shop)
    assert snapshot(app) == before and _moves(app) == moves


def test_the_recorded_again_sale_keeps_the_product_a_person_named(app, shop):
    """A "choose any" lot whose product a person named: recorded again, it is still that
    product (it came in unnamed, uncounted until named again; review of the fix, A10)."""
    from tests.test_pipeline import _owner

    lot = "CHOOSE ANY PRODUCT YOU WANT #41"
    first = datetime.now(UTC) - timedelta(minutes=50)
    _say_at(app, shop, [win(RAE, lot, "25.00")], first)
    run(app, shop["show"])
    with app.app_context():
        item = db.session.scalar(
            select(ReviewItem).where(
                ReviewItem.reason == "unmatched_item", ReviewItem.status == ReviewStatus.open
            )
        )
        pipeline.resolve(item, "assign_sku", {"skuId": shop["red"]}, user=_owner(shop["org"]))
        db.session.commit()
    decline = ("", f"{RAE} payment declined for {lot} for $25.00", "player")
    _say_at(app, shop, [decline], first + timedelta(seconds=30))
    run(app, shop["show"])
    read(app, shop)
    _owner_restores(app, shop)
    o = order(shop, RAE, lot, created=first + timedelta(seconds=10))
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    apply(app, shop)
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        new = db.session.scalar(select(Sale).where(Sale.status == "paid"))
        assert new is not None and new.sku_id == shop["red"]
    assert level(app, shop["red"]) == (2, 0)
    assert _paid_charged(app) == 2500


def test_a_declined_sale_whose_order_shipped_never_blocks_whatnots_orders(app, shop):
    """Declined after picking, its buyer's order then shipped with their other win: when
    Whatnot says that declined win was paid, a person decides (nothing to pay it again
    with). It used to be marked paid, refused inside, and abort every apply of the show:
    another buyer's paid order was never recorded (review of the fix, A4d/A4e)."""
    from app.models import Order
    from app.services import orders as orders_svc
    from tests.test_pipeline import _owner

    first = datetime.now(UTC) - timedelta(minutes=50)
    _say_at(app, shop, [win(RAE, "Red bundle", "25.00")], first)
    _say_at(app, shop, [win(RAE, "Red bundle", "25.00")], first + timedelta(minutes=35))
    run(app, shop["show"])
    o = order(shop, RAE, "Red bundle", created=first + timedelta(seconds=10))
    order(shop, RAE, "Red bundle", created=first + timedelta(minutes=35, seconds=5))
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        orders_svc.advance(
            db.session.scalar(select(Order)), orders_svc.OrderStatus.picking, actor_label="t"
        )
        db.session.commit()
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    apply(app, shop)  # declined while picking: its unit stays out, a person is told
    with app.app_context():
        for it in db.session.scalars(
            select(ReviewItem).where(
                ReviewItem.status == ReviewStatus.open,
                ReviewItem.reason == "closed_after_picking",
            )
        ):
            pipeline.resolve(it, "dismiss", {}, user=_owner(shop["org"]))
        od = db.session.scalar(select(Order))
        orders_svc.advance(od, orders_svc.OrderStatus.packed, actor_label="t")
        orders_svc.advance(od, orders_svc.OrderStatus.shipped, actor_label="t")
        db.session.commit()
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    zed = order(shop, "made_up_zed", "Red bundle", created=first + timedelta(minutes=5))
    read(app, shop)
    apply(app, shop)  # does not raise
    with app.app_context():
        zeds = db.session.scalars(select(Sale).where(Sale.buyer_username == "made_up_zed")).all()
        assert [(s.status.value, s.external_order_id) for s in zeds] == [("paid", zed["id"])]
        declined = db.session.scalar(select(Sale).where(Sale.external_order_id == o["id"]))
        assert declined.status.value == "payment_failed"  # a person decides


def test_a_win_with_two_products_is_never_recorded_again_with_one(app, shop):
    """Recorded again, only the first product followed: the second stayed on the shelf
    though Whatnot charged for the win (review of the fix, Q2). A person decides instead,
    as before, and nothing is half-moved."""
    from app.models import MovementKind, Sku
    from app.services import catalog, inventory, sale_items
    from app.services.inventory import Actor
    from tests.test_pipeline import _owner

    with app.app_context():
        b = catalog.create_product(
            shop["org"], "Blue widget", skus=[{"code": "BLU", "priceCents": 1000}]
        ).skus[0]
        inventory.move(b.id, MovementKind.restock, 3, source_type="test", source_id="seedb")
        db.session.commit()
        blue = b.id
    first = datetime.now(UTC) - timedelta(minutes=50)
    _say_at(app, shop, [win(RAE, "Red bundle", "25.00")], first)
    run(app, shop["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        sale_items.add(sale, db.session.get(Sku, blue), actor=Actor.user(_owner(shop["org"])))
        db.session.commit()
    decline = ("", f"{RAE} payment declined for Red bundle for $25.00", "player")
    _say_at(app, shop, [decline], first + timedelta(seconds=30))
    run(app, shop["show"])
    read(app, shop)
    _owner_restores(app, shop)
    o = order(shop, RAE, "Red bundle", created=first + timedelta(seconds=10))
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    apply(app, shop)
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    for _ in range(2):
        read(app, shop)
        apply(app, shop)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Sale)) == 1
        opened = {
            r.reason
            for r in db.session.scalars(
                select(ReviewItem).where(ReviewItem.status == ReviewStatus.open)
            )
        }
    assert "unmatched_payment" in opened  # a person decides
    assert level(app, shop["red"]) == (3, 0) and level(app, blue) == (3, 0)  # both back
