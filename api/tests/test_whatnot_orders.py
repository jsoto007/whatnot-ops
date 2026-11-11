"""Whatnot's shows and orders: mirrored, compared with the app's sales, and applied.

Everything here runs against the in-memory fake (app/services/whatnot/fake.py), which
follows Whatnot's published API description. No test has run against the live Seller API:
what an order's status is at the win, and whether its title equals the video player's, are
assumptions these tests pin down, not facts they prove.
"""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import (
    AuditLog,
    InventoryMovement,
    Job,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotConnection,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShow,
)
from app.services import activity, pipeline
from app.services.whatnot import client_for, sync, webhooks
from app.services.whatnot import orders as whatnot_orders
from app.services.whatnot.fake import EXAMPLE_WEBHOOK_SECRET
from tests.test_pipeline import level, run

SECRET = EXAMPLE_WEBHOOK_SECRET  # Whatnot's published example, not a real key
LOT = "CHOOSE ANY PRODUCT YOU WANT #73"


def win(buyer, title, price="25.00"):
    return ("", f"{buyer} won the auction for {title} for ${price}", "player")


@pytest.fixture
def shop(app, world, fake_whatnot):
    """The world's shop connected to Whatnot, its show recorded over the last three hours
    and matched to an ended show on Whatnot."""
    now = datetime.now(UTC)
    fake = fake_whatnot
    wshow = fake.add_show(
        title="Friday night",
        start=now - timedelta(hours=2, minutes=30),
        ended=now - timedelta(minutes=5),
    )
    with app.app_context():
        db.session.add(WhatnotConnection(organization_id=world["org"], seller_id="1234567"))
        show = db.session.get(Show, world["show"])
        show.started_at = now - timedelta(hours=3)
        db.session.commit()
    world["say"](app, [("host_shop", "welcome everyone")])  # the recording ran until now
    return {**world, "fake": fake, "wshow": wshow["id"], "now": now}


def read(app, shop, *, orders=True):
    """What the timer and the show-ended catch-up do: read the shows, then the orders."""
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        if orders:
            ws = db.session.scalar(
                select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"])
            )
            sync.sync_show_orders(shop["org"], client, ws)
        db.session.commit()


def order(shop, buyer, title, price=2500, **kw):
    # Whatnot makes the order at the win: seconds from the win line, never an hour.
    kw.setdefault("created", datetime.now(UTC) - timedelta(seconds=20))
    return shop["fake"].add_order(
        show_id=shop["wshow"], buyer=buyer, items=[{"title": title, "price_cents": price}], **kw
    )


def apply(app, shop):
    with app.app_context():
        result = whatnot_orders.apply(db.session.get(Show, shop["show"]))
        db.session.commit()
        return result["summary"]


def compare(app, shop):
    with app.app_context():
        result = whatnot_orders.compare(db.session.get(Show, shop["show"]))
        plan = result["plan"]
        rows = [(r.buyer, r.status, r.action) for r in plan.rows]
        db.session.rollback()
        return rows, plan.uncovered, result


def sale_rows(app):
    with app.app_context():
        return [
            (s.buyer_username, s.item_text, s.status.value, s.status_reason, s.external_order_id)
            for s in db.session.scalars(select(Sale).order_by(Sale.id))
        ]


def snapshot(app):
    """Everything applying could change: sales, stock movements, review items."""
    with app.app_context():
        return (
            [
                (s.id, s.status.value, s.status_reason, s.external_order_id, s.sku_id, s.event_id)
                for s in db.session.scalars(select(Sale).order_by(Sale.id))
            ],  # fmt: skip
            db.session.scalar(select(func.count()).select_from(InventoryMovement)),
            [
                (r.reason, r.status.value, r.sale_id)
                for r in db.session.scalars(select(ReviewItem).order_by(ReviewItem.id))
            ],  # fmt: skip
        )


def reviews(app):
    with app.app_context():
        return [
            (r.reason, r.summary)
            for r in db.session.scalars(
                select(ReviewItem)
                .where(ReviewItem.status == ReviewStatus.open)
                .order_by(ReviewItem.id)
            )
        ]


# ---------- reading ----------


def test_reading_mirrors_shows_and_orders_and_changes_nothing_in_the_app(app, shop):
    world_before = snapshot(app)
    o = order(shop, "lani808", LOT, 1700)
    shop["fake"].add_order(
        show_id=shop["wshow"], buyer="kat.c", items=[{"title": "Free gift"}],
        transaction_type="giveaway", created=shop["now"] - timedelta(minutes=50),
    )  # fmt: skip
    read(app, shop)
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow))
        assert (ws.title, ws.status) == ("Friday night", "ended")
        assert ws.orders_synced_at is not None and ws.url.startswith("https://www.whatnot.com/")
        row = db.session.scalar(select(WhatnotOrder).where(WhatnotOrder.whatnot_id == o["id"]))
        assert (row.status, row.transaction_type, row.buyer_username) == (
            "pending_fulfillment", "auction", "lani808",
        )  # fmt: skip
        assert (row.subtotal_cents, row.shipping_cents, row.total_cents, row.currency) == (
            1700, 525, 2225, "USD",
        )  # fmt: skip
        item = row.items[0]
        assert (item.title, item.quantity, item.unit_price_cents, item.sale_id) == (
            LOT,
            1,
            1700,
            None,
        )
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrder)) == 2
        # The recording was matched to the show that was live while it ran.
        show = db.session.get(Show, shop["show"])
        assert (show.whatnot_show_id, show.whatnot_link) == (ws.id, "auto")
    assert snapshot(app) == world_before  # no sale, no stock movement, no review item


def test_reading_twice_adds_nothing_and_stops_at_orders_older_than_the_show(app, shop):
    for i in range(3):
        order(shop, f"b{i}", LOT)
    shop["fake"].add_order(
        show_id=None, buyer="old", items=[{}], created=shop["now"] - timedelta(days=3)
    )
    shop["fake"].add_order(
        show_id=None, buyer="older", items=[{}], created=shop["now"] - timedelta(days=4)
    )
    shop["fake"].page_size_cap = 2
    read(app, shop)
    shop["fake"].calls.clear()
    read(app, shop)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrder)) == 3
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 3
    order_pages = [p for _, p in shop["fake"].calls if p == "/orders"]
    assert len(order_pages) == 2  # 3 orders of the show, then the first older one: stop
    assert not [
        p for _, p in shop["fake"].calls if p.endswith("/items")
    ]  # known orders: no re-read


def test_an_older_answer_never_overwrites_a_newer_one(app, shop):
    o = order(shop, "lani808", LOT)
    read(app, shop)
    stale = json.loads(json.dumps(o))
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    with app.app_context():
        sync.upsert_order(shop["org"], stale)  # a slow read that finished last
        db.session.commit()
        assert db.session.scalar(select(WhatnotOrder.status)) == "failed"


def test_a_read_without_the_buyer_keeps_who_it_was(app, shop):
    o = order(shop, "lani808", LOT)
    read(app, shop)
    shop["fake"].scopes = {"read:orders", "read:shows"}  # the token lost read:customers
    shop["fake"].set_order_status(o["id"], "completed")
    read(app, shop)
    with app.app_context():
        row = db.session.scalar(select(WhatnotOrder))
        assert (row.status, row.buyer_username) == ("completed", "lani808")


def test_a_recording_far_from_any_show_is_not_matched_and_a_persons_choice_stands(app, shop):
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        show.started_at = shop["now"] - timedelta(days=2)
        from app.models import ChatMessage

        db.session.query(ChatMessage).update(
            {"captured_at": shop["now"] - timedelta(days=2) + timedelta(minutes=5)}
        )
        db.session.commit()
    read(app, shop, orders=False)
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        assert show.whatnot_show_id is None
        ws = db.session.scalar(select(WhatnotShow))
        show.whatnot_show_id, show.whatnot_link = ws.id, "manual"
        db.session.commit()
    read(app, shop, orders=False)
    with app.app_context():
        assert db.session.get(Show, shop["show"]).whatnot_link == "manual"


# ---------- comparing (shadow) ----------


def test_comparing_shows_what_applying_would_do_and_changes_nothing(app, shop):
    shop["say"](
        app, [win("lani808", "Red bundle"), win("kat.c", "Red bundle"), win("ghost", "Red bundle")]
    )
    run(app, shop["show"])
    order(shop, "lani808", "Red bundle")  # agrees
    shop["fake"].set_order_status(order(shop, "kat.c", "Red bundle")["id"], "failed")
    order(shop, "newbie", LOT, 700)  # Whatnot only
    order(shop, "quitter", LOT, status="cancelled")  # cancelled before the app ever saw it
    read(app, shop)
    before = snapshot(app)
    rows, uncovered, result = compare(app, shop)
    assert rows == [
        ("lani808", "paid", "unchanged"),
        ("kat.c", "failed", "decline"),
        ("newbie", "paid", "record"),
        ("quitter", "void", "skip"),
    ]
    assert len(uncovered) == 1 and result["complete"] is True  # ghost: a sale with no order
    assert result["info"] == {"orders": 4, "giveaways": 0, "noBuyer": 0}
    assert snapshot(app) == before


# ---------- applying ----------


def test_applying_confirms_declines_cancels_records_and_calls_out_the_rest(app, shop):
    shop["say"](
        app,
        [win("lani808", "Red bundle"), win("kat.c", "Red bundle"), win("ghost", "Red bundle")],
    )
    run(app, shop["show"])
    assert level(app, shop["red"]) == (0, 0)  # three paid at the win: all three units gone
    paid = order(shop, "lani808", "Red bundle")
    failed = order(shop, "kat.c", "Red bundle")
    shop["fake"].set_order_status(failed["id"], "failed")
    order(shop, "newbie", LOT, 700)
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1, "decline": 1, "recorded": 1, "uncovered": 1}
    assert sale_rows(app) == [
        (
            "lani808",
            "Red bundle",
            "paid",
            "paid at the win; confirmed by Whatnot's order",
            paid["id"],
        ),
        ("kat.c", "Red bundle", "payment_failed", "payment failed on Whatnot", failed["id"]),
        ("ghost", "Red bundle", "paid", pipeline.AUTOPAY_REASON, None),
        ("newbie", LOT, "detected", "item not recognised", sale_rows(app)[3][4]),
    ]
    assert level(app, shop["red"]) == (1, 0)  # kat.c's unit is back on the shelf
    assert reviews(app) == [
        ("unmatched_item", f'newbie bought "{LOT}" — which product is that?'),
        ("no_payment_record", "ghost's 1 × Red bundle is not among Whatnot's orders for this show"),
    ]
    with app.app_context():
        moves = db.session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.kind == "restock")
            .order_by(InventoryMovement.id)
        ).all()
        assert (moves[-1].actor_type, moves[-1].reason) == ("whatnot", "payment failed on Whatnot")
        assert activity.movement_actor("whatnot", None, "Whatnot", {}) == "Whatnot"
        # Each order item now points at its sale; each sale knows when it was ordered.
        linked = dict(
            db.session.execute(select(WhatnotOrderItem.title, WhatnotOrderItem.sale_id)).all()
        )
        assert linked[LOT] is not None
        assert all(
            s.ordered_at is not None
            for s in db.session.scalars(select(Sale))
            if s.external_order_id
        )
    # Applying again changes nothing at all.
    before = snapshot(app)
    assert apply(app, shop) == {"unchanged": 3, "uncovered": 1}
    assert snapshot(app) == before


def test_an_order_whatnot_records_for_a_known_product_is_a_paid_sale_with_stock_moved(app, shop):
    o = order(shop, "lani808", "Red bundle")
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1}
    assert sale_rows(app) == [("lani808", "Red bundle", "paid", "paid on Whatnot", o["id"])]
    assert level(app, shop["red"]) == (2, 0)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert (
            sale.event_id is None
            and sale.paid_at == sale.ordered_at
            and sale.order.status.value == "paid"
        )
        from app import serializers

        assert serializers.sale(sale)["source"] == "whatnot"
        assert serializers.sale(sale)["wonAt"] == sale.ordered_at.isoformat()
    assert apply(app, shop) == {"unchanged": 1}
    assert level(app, shop["red"]) == (2, 0)


def test_a_cancelled_order_cancels_the_sale_and_restocks(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    o = order(shop, "lani808", "Red bundle")
    shop["fake"].set_order_status(o["id"], "cancelled")
    read(app, shop)
    assert apply(app, shop) == {"cancel": 1}
    assert sale_rows(app)[0][2:4] == ("cancelled", "cancelled on Whatnot")
    assert level(app, shop["red"]) == (3, 0)


def test_an_unsettled_order_leaves_the_sale_alone_then_a_decline_and_a_later_payment_follow(
    app, shop
):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    o = order(shop, "lani808", "Red bundle", status="pending")
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1}
    assert sale_rows(app)[0][2:4] == ("paid", pipeline.AUTOPAY_REASON)  # not confirmed yet
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    assert level(app, shop["red"]) == (3, 0)
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")  # paid another way
    read(app, shop)
    assert apply(app, shop) == {"mark_paid": 1}
    assert sale_rows(app)[0][2:4] == ("paid", "paid on Whatnot")
    assert level(app, shop["red"]) == (2, 0)


def test_an_unsettled_order_nobody_recorded_is_a_sale_paid_at_the_win_like_any_other(app, shop):
    o = order(shop, "lani808", "Red bundle", status="pending")
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1}
    assert sale_rows(app) == [("lani808", "Red bundle", "paid", pipeline.AUTOPAY_REASON, o["id"])]
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    assert level(app, shop["red"]) == (3, 0)


def test_giveaways_are_not_sales(app, shop):
    shop["fake"].add_order(
        show_id=shop["wshow"], buyer="kat.c", items=[{"title": "Red bundle", "price_cents": 0}],
        transaction_type="giveaway", created=shop["now"] - timedelta(hours=1),
    )  # fmt: skip
    read(app, shop)
    rows, _, result = compare(app, shop)
    assert rows == [] and result["info"]["giveaways"] == 1
    assert apply(app, shop) == {} and sale_rows(app) == []


def test_a_sale_with_no_order_is_only_called_out_once_the_show_is_over_and_fully_read(app, shop):
    shop["say"](app, [win("ghost", "Red bundle")])
    run(app, shop["show"])
    shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    order(shop, "lani808", "Red bundle")
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1}  # still live: the record is not complete
    assert [r for r, _ in reviews(app)] == []
    shop["fake"].shows[shop["wshow"]].update(
        status="ended", ended_at=datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1, "uncovered": 1}
    assert [r for r, _ in reviews(app)] == ["no_payment_record"]


def test_without_buyers_nothing_is_applied_or_called_out(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    shop["fake"].scopes = {"read:orders", "read:shows"}
    order(shop, "lani808", "Red bundle")
    read(app, shop)
    before = snapshot(app)
    rows, _, result = compare(app, shop)
    assert rows == [("", "paid", "skip")] and result["info"]["noBuyer"] == 1
    with app.app_context():
        with pytest.raises(whatnot_orders.OrdersError, match="who bought each order"):
            whatnot_orders.apply(db.session.get(Show, shop["show"]))
        db.session.rollback()
    assert snapshot(app) == before


def test_orders_never_read_cannot_be_applied(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    read(app, shop, orders=False)
    with app.app_context():
        with pytest.raises(whatnot_orders.OrdersError, match="have not been read yet"):
            whatnot_orders.apply(db.session.get(Show, shop["show"]))


def test_quantities_and_orders_with_several_items(app, shop):
    shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer="lani808",
        items=[
            {"title": "Red bundle", "price_cents": 2500, "quantity": 2},
            {"title": LOT, "price_cents": 900},
        ],
        transaction_type="buy_it_now",
        created=shop["now"] - timedelta(hours=1),
    )
    read(app, shop)
    assert apply(app, shop) == {"recorded": 2}
    with app.app_context():
        got = [
            (s.item_text, s.quantity, s.status.value)
            for s in db.session.scalars(select(Sale).order_by(Sale.item_text))
        ]
        assert got == [(LOT, 1, "detected"), ("Red bundle", 2, "paid")]
        assert len({s.external_order_id for s in db.session.scalars(select(Sale))}) == 1
    assert level(app, shop["red"]) == (1, 0)
    assert apply(app, shop) == {"unchanged": 2}
    assert level(app, shop["red"]) == (1, 0)


def test_three_wins_of_one_listing_by_one_buyer_match_three_orders_once_each(app, shop):
    shop["say"](
        app,
        [win("lani808", "Red bundle"), win("lani808", "Red bundle"), win("lani808", "Red bundle")],
    )
    run(app, shop["show"])
    ids = [order(shop, "lani808", "Red bundle")["id"] for _ in range(3)]
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 3}
    assert sorted(r[4] for r in sale_rows(app)) == sorted(ids)
    assert apply(app, shop) == {"unchanged": 3} and len(sale_rows(app)) == 3


# ---------- the win line and the order, in either order ----------


def test_a_win_line_that_arrives_after_the_order_made_the_sale_is_its_evidence_not_a_second_sale(
    app, shop
):
    just_now = datetime.now(UTC) - timedelta(seconds=20)  # the order, then its win line
    order(shop, "lani808", "Red bundle", created=just_now)
    order(shop, "newbie", LOT, 700, created=just_now)
    read(app, shop)
    assert apply(app, shop) == {"recorded": 2}
    stock = level(app, shop["red"])
    shop["say"](app, [win("lani808", "Red bundle"), win("newbie", LOT, "7.00")])
    stats = run(app, shop["show"])
    assert stats.sales == 0 and len(sale_rows(app)) == 2 and level(app, shop["red"]) == stock
    with app.app_context():
        for s in db.session.scalars(select(Sale)):
            assert s.event_id is not None and s.event.rule == "player_win"
    # A fourth-wall check: a different price is a different win, so it is its own sale.
    shop["say"](app, [win("lani808", "Red bundle", "30.00")])
    assert run(app, shop["show"]).sales == 1


def test_wins_claim_order_sales_one_each_and_the_extra_win_is_a_new_sale(app, shop):
    just_now = datetime.now(UTC) - timedelta(seconds=20)
    order(shop, "lani808", "Red bundle", created=just_now)
    order(shop, "lani808", "Red bundle", created=just_now)
    read(app, shop)
    assert apply(app, shop) == {"recorded": 2}
    shop["say"](app, [win("lani808", "Red bundle")] * 3)
    assert run(app, shop["show"]).sales == 1
    with app.app_context():
        got = db.session.scalars(select(Sale).order_by(Sale.id)).all()
        assert len(got) == 3 and len({s.event_id for s in got}) == 3
        assert [bool(s.external_order_id) for s in got] == [True, True, False]


def test_a_sale_in_another_recording_of_the_same_show_is_matched_not_duplicated(app, shop):
    with app.app_context():
        first = db.session.get(Show, shop["show"])
        second = Show(
            organization_id=shop["org"], show_key="second-laptop",
            started_at=first.started_at + timedelta(minutes=20), show_date=first.show_date,
        )  # fmt: skip
        db.session.add(second)
        db.session.flush()
        from app.models import ChatMessage

        db.session.add(
            ChatMessage(
                show_id=second.id, client_id="a" * 16, seq=1, username="",
                body="kat.c won the auction for Red bundle for $25.00",
                raw_text="kat.c won the auction for Red bundle for $25.00",
                source="player", captured_at=shop["now"] - timedelta(hours=1),
            )
        )  # fmt: skip
        db.session.commit()
        second_id = second.id
    run(app, second_id)
    o = order(shop, "kat.c", "Red bundle", created=shop["now"] - timedelta(minutes=59))
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1}
    assert sale_rows(app) == [
        ("kat.c", "Red bundle", "paid", "paid at the win; confirmed by Whatnot's order", o["id"])
    ]


# ---------- webhooks and the two modes ----------


def deliver(client, topic, data):
    raw = json.dumps(
        {"type": topic, "timestamp": datetime.now(UTC).isoformat(), "data": data, "version": "v2",
         "seller_id": "1234567"}
    ).encode()  # fmt: skip
    wid, ts = str(uuid.uuid4()), str(int(time.time()))
    return client.post(
        "/api/webhooks/whatnot",
        data=raw,
        headers={
            "webhook-id": wid,
            "webhook-timestamp": ts,
            "webhook-signature": "v1," + webhooks.sign(SECRET, wid, ts, raw),
        },
    )


def set_mode(app, shop, mode):
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = mode
        db.session.commit()


def test_in_shadow_mode_an_order_webhook_only_updates_the_copy(app, client, shop):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    read(app, shop)
    o = order(shop, "lani808", "Red bundle")
    before = snapshot(app)
    assert (
        deliver(client, "orders.created", {"id": o["id"], "show_id": shop["wshow"]}).status_code
        == 204
    )
    with app.app_context():
        assert db.session.scalar(select(WhatnotOrder.status)) == "pending_fulfillment"
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 1
    assert snapshot(app) == before


def test_the_webhook_body_is_never_trusted_the_order_is_read_again(app, client, shop):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    read(app, shop)
    o = order(shop, "lani808", "Red bundle")
    body = {
        "id": o["id"],
        "show_id": shop["wshow"],
        "status": "cancelled",
        "buyer": {"username": "mallory"},
    }
    deliver(client, "orders.updated", body)
    with app.app_context():
        row = db.session.scalar(select(WhatnotOrder))
        assert (row.status, row.buyer_username) == ("pending_fulfillment", "lani808")


def test_in_auto_mode_orders_become_sales_and_payment_changes_as_they_arrive(app, client, shop):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    set_mode(app, shop, "auto")
    read(app, shop)
    o = order(shop, "lani808", "Red bundle")
    deliver(client, "orders.created", {"id": o["id"], "show_id": shop["wshow"]})
    assert sale_rows(app) == [("lani808", "Red bundle", "paid", "paid on Whatnot", o["id"])]
    assert level(app, shop["red"]) == (2, 0)
    shop["fake"].set_order_status(o["id"], "failed")
    deliver(client, "orders.updated", {"id": o["id"], "show_id": shop["wshow"]})
    assert sale_rows(app)[0][2:4] == ("payment_failed", "payment failed on Whatnot")
    assert level(app, shop["red"]) == (3, 0)
    with app.app_context():
        entries = db.session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "whatnot.orders_applied")
            .order_by(AuditLog.id)
        ).all()
        assert [activity.who(e.action, e.actor_type, e.actor_label) for e in entries] == [
            "Whatnot"
        ] * 2
        assert activity.describe(entries[0].action, entries[0].details) == (
            "Applied Whatnot's orders to a show: 1 sales recorded"
        )
        assert activity.describe(entries[1].action, entries[1].details) == (
            "Applied Whatnot's orders to a show: 1 payment declined"
        )


def test_a_show_ending_reads_every_order_and_in_auto_mode_calls_out_a_sale_with_none(
    app, client, shop
):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    set_mode(app, shop, "auto")
    shop["say"](app, [win("ghost", "Red bundle")])
    run(app, shop["show"])
    order(shop, "lani808", "Red bundle")  # its own webhook never arrived
    deliver(client, "shows.ended", {"show_id": shop["wshow"]})
    assert [r[0] for r in sale_rows(app)] == ["ghost", "lani808"]
    assert [r for r, _ in reviews(app)] == ["no_payment_record"]
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow))
        assert ws.orders_synced_at >= ws.ended_at


def test_a_show_nobody_recorded_gets_its_own_show_once_it_has_been_live_a_while(app, client, shop):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    set_mode(app, shop, "auto")
    fake = shop["fake"]
    now = datetime.now(UTC)
    with app.app_context():  # the shop's only recording is from two days ago
        from app.models import ChatMessage

        db.session.get(Show, shop["show"]).started_at = now - timedelta(days=2)
        db.session.query(ChatMessage).update({"captured_at": now - timedelta(days=2)})
        db.session.commit()
    live = fake.add_show(title="Unrecorded", status="live", start=now + timedelta(days=3))
    o = fake.add_order(
        show_id=live["id"], buyer="lani808", items=[{"title": "Red bundle", "price_cents": 2500}]
    )
    # It just went live: a laptop may still start recording. Nothing is made yet.
    fake.shows[live["id"]]["scheduled_start_at"] = (now - timedelta(minutes=2)).isoformat()
    fake.orders[o["id"]]["created_at"] = (now - timedelta(minutes=1)).isoformat()
    deliver(client, "orders.created", {"id": o["id"], "show_id": live["id"]})
    assert sale_rows(app) == []
    # Twenty minutes in and still no recording: the show gets its own entry and the sale.
    fake.shows[live["id"]]["scheduled_start_at"] = (now - timedelta(minutes=20)).isoformat()
    deliver(client, "shows.updated", {"id": live["id"]})
    deliver(client, "orders.updated", {"id": o["id"], "show_id": live["id"]})
    assert sale_rows(app) == [("lani808", "Red bundle", "paid", "paid on Whatnot", o["id"])]
    with app.app_context():
        made = db.session.scalar(select(Show).where(Show.whatnot_link == "made"))
        assert made.show_key == f"wn:{live['id']}" and made.id != shop["show"]
        assert db.session.scalar(select(Sale)).show_id == made.id


# ---------- the timer ----------


def test_the_timer_reads_shows_catches_up_ended_ones_and_rereads_unsettled_orders(
    app, shop, monkeypatch
):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    from app.services import jobs

    o = order(shop, "lani808", "Red bundle", status="pending")
    with app.app_context():
        assert sync.schedule_all() == 1 and sync.schedule_all() == 1  # one queued job per shop
        assert (
            db.session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "whatnot_sync")
            )
            == 1
        )
        assert jobs.run_once("t").status.value == "done"
        ws = db.session.scalar(select(WhatnotShow))
        assert ws.orders_synced_at is not None  # ended and never read in full: caught up
        assert db.session.scalar(select(WhatnotOrder.status)) == "pending"
        states = {
            s["resource"]
            for s in __import__("app").services.whatnot.connection.status(shop["org"])["sync"]
        }
        assert states == {"shows", "orders", "open_orders", "shipments", "analytics", "ledger"}
    # Days later the order settles. Nothing lists it near the top any more; it is re-read by id.
    shop["fake"].set_order_status(o["id"], "completed")
    with app.app_context():
        from app.models import WhatnotSyncState

        db.session.query(WhatnotSyncState).update(
            {"last_started_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        db.session.commit()
        shop["fake"].calls.clear()
        sync.schedule_all()
        while (job := jobs.run_once("t")) is not None:  # the timer's pass, and what it queued
            assert job.status.value == "done"
        assert db.session.scalar(select(WhatnotOrder.status)) == "completed"
        assert ("GET", f"/orders/{o['id']}") in shop["fake"].calls


def test_a_failed_read_is_recorded_and_a_rejected_token_flags_the_connection(
    app, shop, monkeypatch
):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    from app.services import jobs
    from app.services.whatnot import connection

    shop["fake"].token = "regenerated"
    with app.app_context():
        sync.schedule_all()
        job = jobs.run_once("t")
        assert job.status.value == "queued" and "Unauthorized" in job.error  # retried later
        conn = db.session.scalar(select(WhatnotConnection))
        assert conn.problem == "token_rejected"
        status = connection.status(shop["org"])
        assert status["sync"] == [{"resource": "shows", "lastOkAt": None, "failing": True}]


# ---------- the routes ----------


def test_the_show_page_compares_refreshes_links_and_applies(app, client, shop, sign_in):
    h = sign_in(shop["org"], role="manager")
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    order(shop, "lani808", "Red bundle")
    order(shop, "newbie", LOT, 700)
    url = f"/api/shows/{shop['show']}/whatnot"
    assert client.get(url).get_json()["show"] is None  # nothing read from Whatnot yet
    read(app, shop, orders=False)
    view = client.get(url).get_json()
    assert view["show"]["title"] == "Friday night" and view["link"] == "auto" and view["rows"] == []
    assert view["mode"] == "shadow" and [c["title"] for c in view["choices"]] == ["Friday night"]
    assert client.post(f"{url}/apply", headers=h).status_code == 409  # orders not read yet

    view = client.post(f"{url}/refresh", headers=h).get_json()
    assert view["queued"] is False and view["complete"] is True
    assert [(r["buyer"], r["action"]) for r in view["rows"]] == [
        ("lani808", "unchanged"),
        ("newbie", "record"),
    ]
    assert view["summary"] == {"unchanged": 1, "record": 1} and view["uncovered"] == []
    # Whatnot's own ids for shows never reach the page; the order number does (staff know it).
    assert shop["wshow"] not in json.dumps(view)

    applied = client.post(f"{url}/apply", headers=h).get_json()
    assert applied["applied"] == {"unchanged": 1, "recorded": 1}
    assert applied["summary"] == {"unchanged": 2}
    with app.app_context():
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "whatnot.orders_applied")
        )
        assert activity.describe(entry.action, entry.details) == (
            "Applied Whatnot's orders to a show: 1 sales recorded"
        )

    # A person says this recording is of no Whatnot show; the timer does not put it back.
    view = client.post(f"{url}/link", json={"whatnotShowId": None}, headers=h).get_json()
    assert view["show"] is None and view["link"] == "manual"
    read(app, shop, orders=False)
    assert client.get(url).get_json()["show"] is None
    choice = view["choices"][0]["id"]
    view = client.post(f"{url}/link", json={"whatnotShowId": choice}, headers=h).get_json()
    assert view["show"]["id"] == choice
    assert client.post(f"{url}/link", json={"whatnotShowId": 99999}, headers=h).status_code == 404


def test_who_may_look_and_who_may_apply(app, client, shop, sign_in):
    read(app, shop)
    url = f"/api/shows/{shop['show']}/whatnot"
    h = sign_in(shop["org"], role="fulfillment")
    assert client.get(url).status_code == 200  # packers see orders already
    for path in ("/apply", "/refresh", "/link"):
        assert client.post(url + path, json={}, headers=h).status_code == 403
    assert (
        client.patch("/api/whatnot/settings", json={"ordersMode": "auto"}, headers=h).status_code
        == 403
    )
    assert client.post(url + "/apply").status_code == 403  # no CSRF token


def test_another_shop_cannot_see_or_apply_this_shows_orders(app, client, shop, sign_in):
    from app.models import Organization

    read(app, shop)
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    h = sign_in(other_id)
    url = f"/api/shows/{shop['show']}/whatnot"
    assert client.get(url).status_code == 404
    assert client.post(url + "/apply", headers=h).status_code == 404
    with app.app_context():
        ws_id = db.session.scalar(select(WhatnotShow.id))
        mine = Show(
            organization_id=other_id,
            show_key="k",
            started_at=datetime.now(UTC),
            show_date=datetime.now(UTC).date(),
        )
        db.session.add(mine)
        db.session.commit()
        mine_id = mine.id
    r = client.post(f"/api/shows/{mine_id}/whatnot/link", json={"whatnotShowId": ws_id}, headers=h)
    assert r.status_code == 404  # cannot attach to another shop's Whatnot show


def test_only_the_owner_switches_the_mode_and_it_is_logged(app, client, shop, sign_in):
    h = sign_in(shop["org"])
    assert (
        client.patch(
            "/api/whatnot/settings", json={"ordersMode": "sometimes"}, headers=h
        ).status_code
        == 400
    )
    s = client.patch("/api/whatnot/settings", json={"ordersMode": "auto"}, headers=h).get_json()
    assert s["ordersMode"] == "auto"
    with app.app_context():
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "whatnot.mode"))
        assert activity.describe(entry.action, entry.details) == (
            "Whatnot's orders are now applied as they arrive"
        )


def test_live_and_upcoming_shows_are_listed(app, client, shop, sign_in):
    sign_in(shop["org"], role="viewer")
    now = datetime.now(UTC)
    shop["fake"].add_show(title="Saturday", status="scheduled", start=now + timedelta(days=1))
    shop["fake"].add_show(title="Right now", status="live", start=now - timedelta(minutes=30))
    read(app, shop, orders=False)
    got = client.get("/api/whatnot/shows").get_json()
    assert [s["title"] for s in got["live"]] == ["Right now"]
    assert [s["title"] for s in got["upcoming"]] == ["Saturday"]
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).disconnected_at = now
        db.session.commit()
    assert client.get("/api/whatnot/shows").get_json() == {
        "connected": False,
        "live": [],
        "upcoming": [],
    }


def test_a_sale_from_an_order_reads_like_a_win_for_the_person_picking_the_product(
    app, client, shop, sign_in
):
    """The winner names the product in chat: the lines around the ORDER's time are shown,
    though no win line was ever captured."""
    h = sign_in(shop["org"], role="manager")
    from app.models import ChatMessage

    with app.app_context():
        db.session.add(
            ChatMessage(
                show_id=shop["show"], client_id="b" * 16, seq=50, username="newbie",
                body="red bundle please", raw_text="newbie red bundle please",
                captured_at=shop["now"] - timedelta(minutes=59),
            )
        )  # fmt: skip
        db.session.commit()
    order(shop, "newbie", LOT, 700, created=shop["now"] - timedelta(hours=1))
    read(app, shop)
    apply(app, shop)
    sales = client.get(f"/api/shows/{shop['show']}/sales").get_json()["sales"]
    assert sales[0]["source"] == "whatnot" and sales[0]["wonAt"] is not None
    assert any(
        "red bundle please" in (c.get("body") or c.get("text") or "")
        for c in sales[0].get("chat", [])
    )
    # Named from Review like any other unknown lot: reserved, paid at the win.
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "unmatched_item"))
        item_id = item.id
    r = client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "assign_sku", "skuId": shop["red"]},
        headers=h,
    )
    assert r.status_code == 200
    assert sale_rows(app)[0][2] == "paid" and level(app, shop["red"]) == (2, 0)
    assert apply(app, shop) == {"unchanged": 1}
    assert sale_rows(app)[0][3] == "paid at the win; confirmed by Whatnot's order"


# ---------- regressions from the 2026-11-10 money-safety review ----------


def test_two_items_of_one_order_with_the_same_title_are_two_sales(app, shop):
    """They used to become one sale (the second item found the first item's sale by the
    order number): one unit short on the shelf count, and never corrected."""
    shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer="lani808",
        items=[
            {"title": "Red bundle", "price_cents": 2500},
            {"title": "Red bundle", "price_cents": 2500},
        ],
        created=shop["now"] - timedelta(hours=1),
    )
    read(app, shop)
    assert apply(app, shop) == {"recorded": 2}
    assert len(sale_rows(app)) == 2 and level(app, shop["red"]) == (1, 0)
    assert apply(app, shop) == {"unchanged": 2}
    assert len(sale_rows(app)) == 2 and level(app, shop["red"]) == (1, 0)


@pytest.mark.parametrize(
    ("order_title", "line_title", "line_price", "one_sale"),
    [
        ("Red bundle", "Red bundle #752", "25.00", True),  # the player adds the lot number
        ("Red bundle #752", "Red bundle", "25.00", True),
        ("RED  BUNDLE!", "red bundle", "25.00", True),
        ("Red bundle #7", "Red bundle #8", "25.00", False),  # two lots are two wins
        ("Red bundle", "Red bundle", "26.00", False),  # another price is another win
        ("Red bundle", "Blue bundle", "25.00", False),
    ],
)
def test_a_win_line_worded_differently_from_the_order_is_still_the_same_win(
    app, shop, order_title, line_title, line_price, one_sale
):
    order(shop, "lani808", order_title, created=datetime.now(UTC) - timedelta(seconds=20))
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [win("lani808", line_title, line_price)])
    made = run(app, shop["show"]).sales
    assert (made, len(sale_rows(app))) == ((0, 1) if one_sale else (1, 2))


def test_a_win_line_long_after_the_order_is_another_win(app, shop):
    order(shop, "lani808", "Red bundle", created=shop["now"] - timedelta(hours=1))
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [win("lani808", "Red bundle")])  # now
    assert run(app, shop["show"]).sales == 1 and len(sale_rows(app)) == 2


def test_the_orders_quantity_stands_when_the_win_line_counts_differently(app, shop):
    shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer="lani808",
        items=[{"title": "Red bundle", "price_cents": 2500, "quantity": 2}],
        created=datetime.now(UTC) - timedelta(seconds=20),
    )
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [win("lani808", "Red bundle")])  # the player never says "x2"
    assert run(app, shop["show"]).sales == 0
    assert len(sale_rows(app)) == 1 and level(app, shop["red"]) == (1, 0)  # two units, once


def test_one_recording_through_two_shows_does_not_sell_a_win_twice(app, shop):
    """The laptop recorded straight through two shows. The recording is matched to the
    second; the first show gets its own entry. A win in the first show is one sale either
    way round: the order after the win line, or the win line after the order."""
    now, fake = shop["now"], shop["fake"]
    fake.shows[shop["wshow"]]["ended_at"] = (now - timedelta(minutes=95)).isoformat()
    fake.add_show(
        title="Second", start=now - timedelta(minutes=90), ended=now - timedelta(minutes=5)
    )
    with app.app_context():
        from app.models import ChatMessage

        db.session.query(ChatMessage).update({"captured_at": now - timedelta(minutes=120)})
        db.session.commit()
    # In show 1's window: a win the laptop saw, and one it missed.
    order(shop, "lani808", "Red bundle", created=now - timedelta(minutes=120))
    order(shop, "kat.c", "Red bundle", created=now - timedelta(minutes=110))
    with app.app_context():
        from app.models import ChatMessage

        line = "lani808 won the auction for Red bundle for $25.00"
        db.session.add(
            ChatMessage(
                show_id=shop["show"], client_id="c" * 16, seq=90, username="",
                body=line, raw_text=line,
                source="player", captured_at=now - timedelta(minutes=120) + timedelta(seconds=5),
            )
        )  # fmt: skip
        db.session.commit()
    run(app, shop["show"])
    read(app, shop)
    with app.app_context():
        first = db.session.scalar(
            select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"])
        )
        assert sync.recordings_of(first) == []  # the recording went to the second show
        made = sync.ensure_recording(first)
        db.session.commit()
        result = whatnot_orders.apply(made)
        db.session.commit()
        assert result["summary"] == {"unchanged": 1, "recorded": 1}
        made_id = made.id
    assert [(r[0], r[2]) for r in sale_rows(app)] == [("lani808", "paid"), ("kat.c", "paid")]
    assert level(app, shop["red"]) == (1, 0)
    # kat.c's win line turns up late in the same recording: evidence, not a third sale.
    with app.app_context():
        from app.models import ChatMessage

        line = "kat.c won the auction for Red bundle for $25.00"
        db.session.add(
            ChatMessage(
                show_id=shop["show"], client_id="d" * 16, seq=91, username="",
                body=line, raw_text=line,
                source="player", captured_at=now - timedelta(minutes=110) + timedelta(seconds=5),
            )
        )  # fmt: skip
        db.session.commit()
    assert run(app, shop["show"]).sales == 0 and len(sale_rows(app)) == 2
    # And Whatnot's word on the payment still reaches the sale that lives in the other show.
    lani = next(o for o in fake.orders.values() if o["buyer"]["username"] == "lani808")
    fake.set_order_status(lani["id"], "failed")
    read(app, shop)
    with app.app_context():
        assert whatnot_orders.apply(db.session.get(Show, made_id))["summary"]["decline"] == 1
        db.session.commit()
    assert sale_rows(app)[0][2] == "payment_failed" and level(app, shop["red"]) == (2, 0)
    # None of it is called out as "no order" on the second show's own page.
    assert reviews(app) == []


def test_apply_and_the_parser_never_work_on_one_show_at_once(app, shop):
    """Both make sales from the same wins. While Apply holds the show, the parser is told
    it is busy (and comes back later, as it does when two parses meet); afterwards its win
    line is the order's evidence. No second sale, no deadlock."""
    import threading

    order(shop, "lani808", "Red bundle", created=datetime.now(UTC) - timedelta(seconds=20))
    read(app, shop)
    shop["say"](app, [win("lani808", "Red bundle")])
    seen = {}
    real = whatnot_orders._plan

    def plan_then_parse(show, ws):
        def parse():
            try:
                run(app, shop["show"])
                seen["parser"] = "ran"
            except pipeline.ParseBusy:
                seen["parser"] = "busy"

        t = threading.Thread(target=parse)
        t.start()
        t.join(20)
        return real(show, ws)

    whatnot_orders._plan = plan_then_parse
    try:
        assert apply(app, shop) == {"recorded": 1}
    finally:
        whatnot_orders._plan = real
    assert seen == {"parser": "busy"}
    assert run(app, shop["show"]).sales == 0 and len(sale_rows(app)) == 1
    assert level(app, shop["red"]) == (2, 0)


def test_two_applies_at_once_record_each_sale_once(app, shop):
    import threading

    for buyer in ("lani808", "kat.c", "newbie"):
        order(shop, buyer, "Red bundle")
    read(app, shop)
    gate = threading.Barrier(2, timeout=20)
    out = []

    def go():
        with app.app_context():
            gate.wait()
            result = whatnot_orders.apply(db.session.get(Show, shop["show"]))
            db.session.commit()
            out.append(result["summary"])

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(out, key=str) == [{"recorded": 3}, {"unchanged": 3}]
    assert len(sale_rows(app)) == 3 and level(app, shop["red"]) == (0, 0)


def test_an_order_whose_sale_was_closed_here_goes_to_a_person_not_to_a_second_sale(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    o = order(shop, "lani808", "Red bundle")
    read(app, shop)
    apply(app, shop)
    with app.app_context():  # someone cancelled it here; Whatnot still says paid
        sale = db.session.scalar(select(Sale))
        pipeline.set_sale_status(
            sale, SaleStatus.cancelled, reason="by hand", actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
    rows, _, result = compare(app, shop)
    assert rows == [("lani808", "paid", "unmatched")]
    note = result["plan"].rows[0].note
    assert note == "cancelled here, but paid on Whatnot"
    assert apply(app, shop) == {"unmatched": 1}
    assert len(sale_rows(app)) == 1 and level(app, shop["red"]) == (3, 0)
    summary = reviews(app)[-1][1]
    assert "cancelled here, but paid on Whatnot" in summary and "payment_failed" not in summary
    assert o["id"]


def test_an_amount_no_column_can_hold_does_not_stop_the_shows_orders_from_being_read(app, shop):
    huge = order(shop, "whale", "Red bundle")
    shop["fake"].orders[huge["id"]]["amounts"]["total"] = {
        "amount": "99999999.99",
        "currency": "USD",
    }
    shop["fake"].items_of(huge["id"])[0]["unit_price"] = {"amount": "1e30", "currency": "USD"}
    order(shop, "lani808", "Red bundle")
    read(app, shop)
    with app.app_context():
        got = {o.buyer_username: o.total_cents for o in db.session.scalars(select(WhatnotOrder))}
        assert got == {"whale": None, "lani808": 3025}
        assert sorted(
            str(i.unit_price_cents) for i in db.session.scalars(select(WhatnotOrderItem))
        ) == ["2500", "None"]


def test_one_delivery_to_four_threads_is_stored_and_queued_once(app, shop, monkeypatch):
    import threading

    from app.models import WhatnotEvent

    monkeypatch.delenv("JOBS_INLINE", raising=False)
    body = {"type": "orders.updated", "seller_id": "1234567", "data": {"id": "999"}}
    gate = threading.Barrier(4, timeout=20)
    out = []

    def go():
        with app.app_context():
            gate.wait()
            out.append(webhooks.accept(body, "one-delivery"))

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(out) == ["duplicate", "duplicate", "duplicate", "queued"]
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotEvent)) == 1
        assert (
            db.session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "whatnot_event")
            )
            == 1
        )


def test_an_order_for_a_win_the_laptop_missed_never_takes_the_buyers_other_sale(app, shop):
    """The buyer's only sale here is "Red bundle". Whatnot also has their "Mystery box",
    which the laptop missed. Matching it to the Red sale (it was "the buyer's only sale")
    left the Red order to make a second Red sale: two units out for one win."""
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, "lani808", "Mystery box", 500, created=now - timedelta(minutes=10))
    red = order(shop, "lani808", "Red bundle", created=now - timedelta(seconds=30))
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1, "unchanged": 1}
    assert [(r[1], r[2], r[4] == red["id"]) for r in sale_rows(app)] == [
        ("Red bundle", "paid", True),
        ("Mystery box", "detected", False),
    ]
    assert level(app, shop["red"]) == (2, 0)


def test_a_failed_order_for_another_win_never_declines_the_buyers_paid_sale(app, shop):
    """The same, with the missed order failed: it used to decline the captured, paid Red
    sale and put its unit back on the shelf."""
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    shop["fake"].set_order_status(
        order(shop, "lani808", "Mystery box", 500, created=now - timedelta(minutes=10))["id"],
        "failed",
    )
    order(shop, "lani808", "Red bundle", created=now - timedelta(seconds=30))
    read(app, shop)
    assert apply(app, shop) == {"skip": 1, "unchanged": 1}
    assert [(r[1], r[2]) for r in sale_rows(app)] == [("Red bundle", "paid")]
    assert level(app, shop["red"]) == (2, 0)


def test_a_chat_sale_at_list_price_still_meets_its_order_and_takes_its_price(app, shop):
    """A sale read from chat with no price in the line carries the product's list price,
    which says nothing about what was paid: it must not keep the order from matching."""
    shop["say"](app, [("", "lani808 won Red bundle")])  # the platform's line, no price
    run(app, shop["show"])
    order(shop, "lani808", "Red bundle", 1900)  # won at $19, list price $25
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1}
    assert len(sale_rows(app)) == 1 and level(app, shop["red"]) == (2, 0)


def test_an_order_worded_unlike_the_sale_meets_it_through_the_product_both_name(app, shop):
    """Chat called it by a name the shop taught the app; Whatnot's order has the listing
    title. Both name the same product, at the same moment: one sale."""
    with app.app_context():
        from app.models import Sku
        from app.services import catalog

        catalog.add_alias(shop["org"], db.session.get(Sku, shop["red"]), "crimson set")
        db.session.commit()
    shop["say"](app, [("", "lani808 won crimson set")])
    run(app, shop["show"])
    assert [(r[1], r[2]) for r in sale_rows(app)] == [("crimson set", "paid")]
    order(shop, "lani808", "Red bundle")
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1}
    assert len(sale_rows(app)) == 1 and level(app, shop["red"]) == (2, 0)


def test_a_line_total_no_column_can_hold_is_left_unread_not_applied(app, shop):
    """500 × $100,000 is inside each limit but not their product: the dashboard's sums
    would have failed for the whole show."""
    shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer="whale",
        items=[{"title": "Red bundle", "price_cents": 10_000_000, "quantity": 500}],
        created=datetime.now(UTC) - timedelta(seconds=20),
    )
    read(app, shop)
    with app.app_context():
        assert db.session.scalar(select(WhatnotOrderItem.unit_price_cents)) is None
        from app.services import dashboard

        apply(app, shop)
        dashboard.show_tiles(db.session.get(Show, shop["show"]))  # no "integer out of range"
