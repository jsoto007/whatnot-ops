"""Two people settle the same open review item at the same instant with different products.
Exactly one SKU may move for the sale; the other person gets a 409 and moves nothing. Before
resolve() locked the item and its sale first, both reserves and both payments were committed and
one unit of the first SKU vanished with no sale on it (found by review on 2026-11-09)."""

import threading
import time

from sqlalchemy import select

from app.db import db
from app.models import InventoryMovement, MovementKind, ReviewItem, Sale, Sku, User
from app.services import catalog, inventory, pipeline
from tests.test_orders_actions import sign_in, win
from tests.test_pipeline import run


def test_two_people_assign_different_skus_to_one_open_item(client, app, world):
    world["say"](app, [win("moonlove3", "$1 BEAUTY PULLS #520")])
    run(app, world["show"])
    sign_in(client, app, world["org"])
    with app.app_context():
        blue = catalog.create_product(
            world["org"], "Blue bundle", skus=[{"code": "BLUE", "priceCents": 900}]
        ).skus[0]
        inventory.move(blue.id, MovementKind.restock, 3, source_type="test", source_id="seed-b")
        db.session.commit()
        blue_id = blue.id
        item_id = db.session.scalar(select(ReviewItem.id))

    a_resolved = threading.Event()
    a_may_commit = threading.Event()
    outcome: dict[str, str] = {}

    def worker(name, sku_id, before_commit=None, wait_for=None):
        try:
            if wait_for is not None:
                wait_for.wait(10)
            with app.app_context():
                item = db.session.get(ReviewItem, item_id)
                user = db.session.scalar(select(User))
                pipeline.resolve(item, "assign_sku", {"skuId": sku_id}, user=user)
                if before_commit is not None:
                    before_commit()
                db.session.commit()
                outcome[name] = "committed"
        except Exception as e:  # noqa: BLE001
            outcome[name] = repr(e)[:200]

    def a_hold():
        a_resolved.set()
        a_may_commit.wait(10)

    ta = threading.Thread(target=worker, args=("A", world["red"], a_hold))
    tb = threading.Thread(target=worker, args=("B", blue_id, None, a_resolved))
    ta.start()
    tb.start()
    a_resolved.wait(10)
    time.sleep(1.5)  # B reaches its UPDATE on the sale row and blocks behind A's lock
    a_may_commit.set()
    ta.join(20)
    tb.join(20)

    with app.app_context():
        s = db.session.scalar(select(Sale))
        red = db.session.get(Sku, world["red"])
        blue = db.session.get(Sku, blue_id)
        moves = [
            (m.sku.code, m.kind.value, m.quantity)
            for m in db.session.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.source_type == "sale")
                .order_by(InventoryMovement.id)
            )
        ]
        items = [(r.status.value, r.resolution) for r in db.session.scalars(select(ReviewItem))]
        # Exactly one SKU should have moved for this one sale; the other must be untouched.
        touched = {code for code, _, _ in moves}
        assert len(touched) == 1, f"stock moved on two SKUs for one sale: {moves}"
        assert (red.on_hand, blue.on_hand) in {(2, 3), (3, 2)}
        assert s.status.value == "paid" and s.sku.code in touched
        # one winner committed, the other was refused as already resolved
        assert sorted(v == "committed" for v in outcome.values()) == [False, True]
        assert any("already resolved" in v for v in outcome.values())
        assert [st for st, _ in items] == ["resolved"]
