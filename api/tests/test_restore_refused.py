"""'They paid after all' while Whatnot's order says the payment failed (Javier's call,
2026-11-20): refused. Whatnot decides whether a win was paid; its paid order restores the
sale. Before this, the person's restore was undone by the next read of the failed order
(stock back out and in again, the decline reopened). Buyer names are made up; Whatnot is the
in-memory fake."""

from sqlalchemy import func, select

from app.db import db
from app.models import InventoryMovement, ReviewItem, ReviewStatus, Sale
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
