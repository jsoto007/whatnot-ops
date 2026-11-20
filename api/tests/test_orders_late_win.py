"""A win that arrives after its buyer's order has shipped (made-up names)."""

from sqlalchemy import select

from app.db import db
from app.models import OrderStatus, ReviewItem, ReviewStatus, Sale
from app.services import orders
from tests.test_pipeline import run
from tests.test_sale_items import paid_sale


def _ship_the_first_order(app):
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        for step in (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped):
            orders.advance(sale.order, step, actor_label="Packer")
        db.session.commit()


def test_a_win_added_to_an_order_that_already_shipped_is_put_to_a_person(app, world):
    """The package left without it and no pick list shows a shipped order: without a note the
    buyer's second lot would sit paid and never be packed."""
    paid_sale(app, world)
    _ship_the_first_order(app)
    world["say"](app, [("", "ann_made_up won the auction for Red bundle #7 for $31.00")])
    run(app, world["show"])
    with app.app_context():
        late = db.session.scalars(select(Sale).order_by(Sale.id)).all()[1]
        assert late.order.status is OrderStatus.shipped  # the order itself is left alone
        notes = db.session.scalars(
            select(ReviewItem).where(ReviewItem.reason == "won_after_shipping")
        ).all()
        assert [(n.sale_id, n.status) for n in notes] == [(late.id, ReviewStatus.open)]
        assert "already shipped" in notes[0].summary and "ann_made_up" in notes[0].summary


def test_a_win_on_an_order_still_to_ship_makes_no_such_note(app, world):
    paid_sale(app, world)
    world["say"](app, [("", "ann_made_up won the auction for Red bundle #7 for $31.00")])
    run(app, world["show"])
    with app.app_context():
        assert not db.session.scalars(
            select(ReviewItem).where(ReviewItem.reason == "won_after_shipping")
        ).all()
