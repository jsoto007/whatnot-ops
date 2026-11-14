"""A sale's products as rows (services/sale_items.py, stage 4).

Phase one: every sale with a product has one row, and nothing about its stock changes. The
first row keeps the sale's own keys in the stock ledger, so sales named before rows existed
and sales that never change behave exactly as they always did. Names here are made up.
"""

from sqlalchemy import select

from app.db import db
from app.models import InventoryMovement, Sale, SaleItem, SaleStatus
from app.services import pipeline, sale_items
from tests.test_pipeline import level, run


def rows(app):
    with app.app_context():
        return [
            (r.sale_id, r.sku_id, r.quantity, r.first, r.closed_at is None)
            for r in db.session.scalars(select(SaleItem).order_by(SaleItem.id))
        ]


def test_a_named_sale_has_one_row_and_its_stock_is_keyed_as_before(app, world):
    world["say"](app, [("", "ann_made_up won Red bundle x2")])
    run(app, world["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert rows(app) == [(sale.id, world["red"], 2, True, True)]
        keys = db.session.execute(
            select(
                InventoryMovement.kind, InventoryMovement.source_type, InventoryMovement.source_id
            )
            .where(InventoryMovement.source_id == str(sale.id))
            .order_by(InventoryMovement.id)
        ).all()
        assert [(k.value, t, i) for k, t, i in keys] == [
            ("reserve", "sale", str(sale.id)),
            ("commit", "sale", str(sale.id)),
        ]
        assert sale_items.source_id(sale) == str(sale.id)
    assert level(app, world["red"]) == (1, 0)


def test_a_sale_with_no_product_has_no_row_until_it_is_named(app, world):
    world["say"](app, [("", "ann_made_up won mystery box")])
    run(app, world["show"])
    assert rows(app) == []
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale_items.ensure(sale) is None and sale_items.source_id(sale) == str(sale.id)
        from app.models import ReviewItem

        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "unmatched_item"))
        pipeline.resolve(
            item, "assign_sku", {"skuId": world["red"]}, user=None, actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
        assert rows(app) == [(sale.id, world["red"], 1, True, True)]


def test_a_sale_named_before_rows_existed_gets_its_row_on_first_use(app, world):
    """What the migration does for every such sale; a sale it somehow missed heals itself."""
    world["say"](app, [("", "ann_made_up won Red bundle")])
    run(app, world["show"])
    with app.app_context():
        db.session.execute(db.delete(SaleItem))
        db.session.commit()
        sale = db.session.scalar(select(Sale))
        pipeline.set_sale_status(
            sale, SaleStatus.cancelled, reason="called off", actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
        assert rows(app) == [(sale.id, world["red"], 1, True, True)]
    assert level(app, world["red"]) == (3, 0)  # the unit came back under the sale's own key


def test_decline_then_paid_again_still_uses_the_sales_own_keys(app, world):
    world["say"](app, [("", "ann_made_up won Red bundle")])
    run(app, world["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        for status in (SaleStatus.payment_failed, SaleStatus.paid):
            pipeline.set_sale_status(sale, status, reason="test", actor=pipeline.RULE_ACTOR)
        db.session.commit()
        got = db.session.execute(
            select(InventoryMovement.kind, InventoryMovement.source_type)
            .where(InventoryMovement.source_id == str(sale.id))
            .order_by(InventoryMovement.id)
        ).all()
        assert [(k.value, t) for k, t in got] == [
            ("reserve", "sale"),
            ("commit", "sale"),
            ("restock", "sale"),
            ("reserve", "sale_repaid"),
            ("commit", "sale_repaid"),
        ]
    assert level(app, world["red"]) == (2, 0)
    assert len(rows(app)) == 1
