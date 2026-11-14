"""A sale's products as rows (services/sale_items.py, stage 4).

Phase one: every sale with a product has one row, and nothing about its stock changes. The
first row keeps the sale's own keys in the stock ledger, so sales named before rows existed
and sales that never change behave exactly as they always did. Names here are made up.
"""

import threading

import pytest
from sqlalchemy import select

from app.db import db
from app.models import (
    AuditLog,
    InventoryMovement,
    MovementKind,
    OrderStatus,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleItem,
    SaleStatus,
    Sku,
)
from app.services import activity, catalog, inventory, orders, pipeline, sale_items
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


# ---------- phase two: change a sale's product ----------


@pytest.fixture
def blue(app, world):
    """A second product, three on the shelf."""
    with app.app_context():
        sku = catalog.create_product(world["org"], "Blue bundle", skus=[{"code": "BLUE"}]).skus[0]
        inventory.move(sku.id, MovementKind.restock, 3, source_type="test", source_id="seed-blue")
        db.session.commit()
        return sku.id


def change(app, sku_id):
    with app.app_context():
        sale = db.session.scalar(select(Sale).order_by(Sale.id))
        result = sale_items.change(sale, db.session.get(Sku, sku_id), actor=pipeline.RULE_ACTOR)
        db.session.commit()
        return result


def paid_sale(app, world):
    world["say"](app, [("", "ann_made_up won Red bundle")])
    run(app, world["show"])


def test_a_paid_sales_product_changes_there_and_back_and_stock_follows(app, world, blue):
    paid_sale(app, world)
    assert (level(app, world["red"]), level(app, blue)) == ((2, 0), (3, 0))
    assert change(app, blue) == {"changed": True, "from": world["red"], "to": blue, "picked": False}
    assert (level(app, world["red"]), level(app, blue)) == ((3, 0), (2, 0))
    change(app, world["red"])  # back again: the ledger would refuse this without rows
    assert (level(app, world["red"]), level(app, blue)) == ((2, 0), (3, 0))
    change(app, blue)
    assert (level(app, world["red"]), level(app, blue)) == ((3, 0), (2, 0))
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert (sale.status, sale.sku_id, sale.price_cents) == (SaleStatus.paid, blue, 2500)
        assert sale.order.status is OrderStatus.paid
    got = rows(app)
    assert [(sku, first, open_) for _, sku, _, first, open_ in got] == [
        (world["red"], True, False),
        (blue, False, False),
        (world["red"], False, False),
        (blue, False, True),
    ]
    assert change(app, blue)["changed"] is False  # the same product: nothing to do
    assert len(rows(app)) == 4


def test_a_changed_sale_cancels_declines_and_is_paid_again_on_its_new_product(app, world, blue):
    paid_sale(app, world)
    change(app, blue)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        pipeline.set_sale_status(
            sale, SaleStatus.payment_failed, reason="declined", actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
    assert (level(app, world["red"]), level(app, blue)) == ((3, 0), (3, 0))
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        pipeline.set_sale_status(sale, SaleStatus.paid, reason="paid", actor=pipeline.RULE_ACTOR)
        db.session.commit()
    assert (level(app, world["red"]), level(app, blue)) == ((3, 0), (2, 0))
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        pipeline.set_sale_status(
            sale, SaleStatus.cancelled, reason="called off", actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
    assert (level(app, world["red"]), level(app, blue)) == ((3, 0), (3, 0))


def test_a_held_sale_changes_product_any_number_of_times(app, world, blue):
    world["say"](app, [("", "ann_made_up won Red bundle")])
    run(app, world["show"])
    with app.app_context():  # as it was before Whatnot charged at the win: only held
        sale = db.session.scalar(select(Sale))
        inventory.move(
            world["red"], MovementKind.restock, 1, source_type="sale", source_id=str(sale.id)
        )
        sale.status = SaleStatus.reserved
        inventory.reserve(world["red"], 1, source_type="test", source_id="hold")
        db.session.commit()
    before = (level(app, world["red"]), level(app, blue))
    change(app, blue)
    change(app, world["red"])
    change(app, blue)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale.status is SaleStatus.reserved and sale.sku_id == blue
    assert level(app, blue) == (3, 1)  # held, not sold
    assert before[1] == (3, 0)


def test_two_people_changing_one_sale_at_once_leave_one_unit_out(app, world, blue):
    paid_sale(app, world)
    with app.app_context():
        green = catalog.create_product(world["org"], "Green bundle", skus=[{"code": "GRN"}]).skus[0]
        inventory.move(green.id, MovementKind.restock, 3, source_type="test", source_id="seed-g")
        db.session.commit()
        green_id = green.id
    errors = []

    def go(sku_id):
        try:
            change(app, sku_id)
        except Exception as e:  # noqa: BLE001 - the test reports whatever went wrong
            errors.append(e)

    threads = [threading.Thread(target=go, args=(s,)) for s in (blue, green_id) * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        out = {world["red"]: 3, blue: 3, green_id: 3}
        out[sale.sku_id] -= 1
        assert {k: level(app, k) for k in out} == {k: (v, 0) for k, v in out.items()}
        assert len([r for r in rows(app) if r[4]]) == 1  # one open row


def test_once_the_order_is_being_picked_nothing_is_put_back_by_itself(
    app, client, world, blue, sign_in
):
    paid_sale(app, world)
    h = sign_in(world["org"], "manager")
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        orders.advance(sale.order, OrderStatus.picking, actor_label="Packer")
        db.session.commit()
        sale_id = sale.id
    r = client.patch(f"/api/orders/sales/{sale_id}/product", json={"skuId": blue}, headers=h)
    assert r.status_code == 200 and r.get_json()["picked"] is True
    # The blue one leaves the shelf; the red one may already be in the box, so a person counts it.
    assert (level(app, world["red"]), level(app, blue)) == ((2, 0), (2, 0))
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale.flagged and sale.sku_id == blue
        note = db.session.scalar(
            select(ReviewItem).where(ReviewItem.reason == "swapped_after_picking")
        )
        assert note.status is ReviewStatus.open
        assert "was changed to BLUE after its order was already being picked" in note.summary
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "sale.product_changed"))
        assert activity.describe(entry.action, entry.details) == (
            "Changed ann_made_up's product from \u201cRED\u201d to \u201cBLUE\u201d "
            "after picking had begun"
        )


def test_the_route_changes_a_product_and_refuses_what_cannot_be_changed(
    app, client, world, blue, sign_in
):
    world["say"](app, [("", "ann_made_up won Red bundle"), ("", "bo_made_up won mystery box")])
    run(app, world["show"])
    h = sign_in(world["org"], "manager")
    with app.app_context():
        named, unnamed = [s.id for s in db.session.scalars(select(Sale).order_by(Sale.id))]
    url = f"/api/orders/sales/{named}/product"
    r = client.patch(url, json={"skuId": blue}, headers=h)
    assert r.status_code == 200 and r.get_json()["sale"]["sku"]["code"] == "BLUE"
    assert (level(app, world["red"]), level(app, blue)) == ((3, 0), (2, 0))
    assert client.patch(url, json={}, headers=h).status_code == 400  # no product named
    r = client.patch(f"/api/orders/sales/{unnamed}/product", json={"skuId": blue}, headers=h)
    assert r.status_code == 409 and "Name it in Review" in r.get_json()["error"]
    viewer = sign_in(world["org"], "viewer")
    assert client.patch(url, json={"skuId": world["red"]}, headers=viewer).status_code == 403
    with app.app_context():  # a shipped order's sale is final
        sale = db.session.get(Sale, named)
        for step in (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped):
            orders.advance(sale.order, step, actor_label="Packer")
        db.session.commit()
    h = sign_in(world["org"], "manager")
    r = client.patch(url, json={"skuId": world["red"]}, headers=h)
    assert r.status_code == 409
    assert (level(app, world["red"]), level(app, blue)) == ((3, 0), (2, 0))


# ---------- found by the adversarial pass ----------


def test_the_packers_note_survives_a_decline_of_the_changed_sale(app, client, world, blue, sign_in):
    """The decline used to sweep the "count the red one back" note away with every other
    open item on the sale."""
    paid_sale(app, world)
    h = sign_in(world["org"], "manager")
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        orders.advance(sale.order, OrderStatus.picking, actor_label="Packer")
        db.session.commit()
        sale_id = sale.id
    client.patch(f"/api/orders/sales/{sale_id}/product", json={"skuId": blue}, headers=h)
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        pipeline.set_sale_status(
            sale, SaleStatus.payment_failed, reason="declined", actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
        note = db.session.scalar(
            select(ReviewItem).where(ReviewItem.reason == "swapped_after_picking")
        )
        assert note.status is ReviewStatus.open


def test_a_sale_whose_order_shipped_meanwhile_is_not_changed(app, world, blue):
    """The order is locked before its state is read: a change can no longer slip in
    between "shipped" being written and the sale being marked fulfilled."""
    paid_sale(app, world)
    started, done = threading.Event(), threading.Event()
    result = {}

    def ship():
        with app.app_context():
            sale = db.session.scalar(select(Sale))
            for step in (OrderStatus.picking, OrderStatus.packed):
                orders.advance(sale.order, step, actor_label="Packer")
            db.session.commit()
            sale.order.status = OrderStatus.shipped  # as the carrier sync does, then waits
            db.session.flush()
            started.set()
            done.wait(timeout=2)
            pipeline.set_sale_status(
                sale, SaleStatus.fulfilled, reason="shipped", actor=pipeline.RULE_ACTOR
            )
            db.session.commit()

    def swap():
        started.wait(timeout=5)
        try:
            result["change"] = change(app, blue)
        except sale_items.ChangeError as e:
            result["refused"] = str(e)

    threads = [threading.Thread(target=ship), threading.Thread(target=swap)]
    for t in threads:
        t.start()
    done.set()
    for t in threads:
        t.join()
    assert "refused" in result, result
    assert (level(app, world["red"]), level(app, blue)) == ((2, 0), (3, 0))


def test_stock_history_names_the_buyer_of_a_changed_sale_and_no_internal_key(
    app, client, world, blue, sign_in
):
    paid_sale(app, world)
    change(app, blue)
    sign_in(world["org"], "manager")
    moves = client.get(f"/api/inventory/activity?skuId={blue}").get_json()
    rows_ = moves.get("movements") or moves.get("activity") or moves.get("rows") or []
    sold = [m for m in rows_ if m.get("sale")]
    assert sold and all(m["sale"]["buyer"] == "ann_made_up" for m in sold)
    assert activity.movement_source("sale", "12:34") == "sale #12"
    assert activity.movement_source("sale_repaid", "12:34") == "sale #12 (paid after a decline)"
