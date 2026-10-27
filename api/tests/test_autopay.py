"""Whatnot charges the winner at the win: sales are paid at once, orders are ready to pick, and
a declined card (read from Whatnot's own UI, or said by the host / platform) closes the sale
and puts the stock back. A person can restore it if the buyer pays another way."""

import pytest
from sqlalchemy import select

from app.db import db
from app.models import (
    InventoryMovement,
    Order,
    OrderStatus,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Sku,
    User,
)
from app.services import orders, pipeline, reconcile, rules

WIN = "chonwhite828 won the auction for Red bundle #760 for $25.00"
DECLINE = "chonwhite828 payment declined for Red bundle #760 for $25.00"


def run(app, world):
    with app.app_context():
        stats = pipeline.process_show(world["show"])
        db.session.commit()
        return stats


def level(app, sku_id):
    with app.app_context():
        s = db.session.get(Sku, sku_id)
        return s.on_hand, s.reserved


def sale_states(app, show_id):
    with app.app_context():
        return [
            (s.buyer_username, s.status.value, s.price_cents)
            for s in db.session.scalars(
                select(Sale).where(Sale.show_id == show_id).order_by(Sale.id)
            )
        ]


def open_reasons(app):
    with app.app_context():
        return sorted(
            r.reason
            for r in db.session.scalars(
                select(ReviewItem).where(ReviewItem.status == ReviewStatus.open)
            )
        )


def owner(org_id):
    u = User(
        organization_id=org_id,
        email="o@x.test",
        name="O",
        password_hash="x",
        role="owner",
        session_token="t",
    )
    db.session.add(u)
    db.session.flush()
    return u


# ---------- classification ----------


def test_a_player_decline_row_is_a_payment_failed_event_with_the_wins_price():
    c = rules.classify("", DECLINE, DECLINE, source="player")
    assert (c.kind.value, c.rule, c.buyer, c.item, c.price_cents, c.confidence) == (
        "payment_failed",
        "player_payment_declined",
        "chonwhite828",
        "Red bundle #760",
        2500,
        1.0,
    )


def test_an_untied_decline_row_changes_nothing_and_is_kept_for_a_person():
    body = "payment declined (not tied to a win): Payment failed $25"
    c = rules.classify("", body, body, source="player")
    assert (c.kind.value, c.rule) == ("chatter", "player_decline_untied")


@pytest.mark.parametrize(
    "user,body",
    [
        ("viewer9", DECLINE),  # the extension's sentence typed in the chat
        ("viewer9", "payment declined (not tied to a win): x"),
        ("viewer9", "payment failed for chonwhite828"),
        ("chonwhite828", "card declined"),
    ],
)
def test_a_viewer_can_never_decline_someone_elses_win(user, body):
    c = rules.classify(user, body, body, host="host_shop", source="chat")
    assert c.rule != "player_payment_declined"
    assert c.kind.value != "payment_failed" or c.confidence < 0.8


# ---------- paid at the win ----------


def test_a_win_is_paid_at_once_and_its_order_is_ready_to_pick(app, world):
    world["say"](app, [("", WIN, "player")])
    run(app, world)
    assert sale_states(app, world["show"]) == [("chonwhite828", "paid", 2500)]
    assert level(app, world["red"]) == (2, 0)  # left the shelf, nothing held
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale.paid_at is not None and sale.status_reason == pipeline.AUTOPAY_REASON
        order = db.session.scalar(select(Order))
        assert order.status is OrderStatus.paid and orders.verified(order)
        orders.advance(order, OrderStatus.picking, actor_label="t")  # no payment file needed
        db.session.commit()
    assert open_reasons(app) == []


def test_an_unrecognised_item_waits_in_review_then_is_paid_when_a_product_is_chosen(app, world):
    world["say"](app, [("", "cardguy won the auction for mystery box for $10.00", "player")])
    run(app, world)
    assert sale_states(app, world["show"]) == [("cardguy", "detected", 1000)]
    with app.app_context():
        order = db.session.scalar(select(Order))
        assert order.status is OrderStatus.open
        with pytest.raises(orders.OrderError, match="needs a decision in Review"):
            orders.advance(order, OrderStatus.picking, actor_label="t")
        db.session.rollback()
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "unmatched_item"))
        pipeline.resolve(item, "assign_sku", {"skuId": world["red"]}, user=owner(world["org"]))
        db.session.commit()
        assert db.session.scalar(select(Order)).status is OrderStatus.paid
    assert sale_states(app, world["show"]) == [("cardguy", "paid", 1000)]
    assert level(app, world["red"]) == (2, 0)


# ---------- declines ----------


def test_a_decline_from_whatnots_ui_closes_the_sale_and_returns_the_stock(app, world):
    world["say"](app, [("", WIN, "player")])
    run(app, world)
    world["say"](app, [("", DECLINE, "player")])
    run(app, world)
    assert sale_states(app, world["show"]) == [("chonwhite828", "payment_failed", 2500)]
    assert level(app, world["red"]) == (3, 0)
    with app.app_context():
        assert db.session.scalar(select(Order)).status is OrderStatus.cancelled
        kinds = [
            (m.kind.value, m.source_type)
            for m in db.session.scalars(
                select(InventoryMovement)
                .where(
                    InventoryMovement.sku_id == world["red"],
                    InventoryMovement.source_type != "test",
                )
                .order_by(InventoryMovement.id)
            )
        ]
        assert kinds == [("reserve", "sale"), ("commit", "sale"), ("restock", "sale")]
    assert open_reasons(app) == ["payment_declined"]
    # Seen again (a second laptop, a re-render): nothing moves twice, no extra review.
    world["say"](app, [("", DECLINE, "player")])
    run(app, world)
    assert level(app, world["red"]) == (3, 0)
    assert open_reasons(app) == ["payment_declined"]


def test_the_decline_picks_the_lot_by_price_when_the_buyer_won_twice(app, world):
    world["say"](
        app,
        [
            ("", "chonwhite828 won the auction for Red bundle #760 for $25.00", "player"),
            ("", "chonwhite828 won the auction for Red bundle #761 for $30.00", "player"),
            ("", "chonwhite828 payment declined for Red bundle #761 for $30.00", "player"),
        ],
    )
    run(app, world)
    assert sale_states(app, world["show"]) == [
        ("chonwhite828", "paid", 2500),
        ("chonwhite828", "payment_failed", 3000),
    ]
    assert level(app, world["red"]) == (2, 0)


def test_a_host_decline_line_closes_a_paid_sale_but_a_viewers_does_not(app, world):
    world["say"](
        app,
        [
            ("", WIN, "player"),
            ("troll", "payment failed for chonwhite828"),
            ("chonwhite828", "card declined"),
        ],
    )
    run(app, world)
    assert sale_states(app, world["show"])[0][1] == "paid"
    world["say"](app, [("host_shop", "payment failed for chonwhite828")])
    run(app, world)
    assert sale_states(app, world["show"])[0][1] == "payment_failed"
    assert level(app, world["red"]) == (3, 0)


def test_a_decline_nobody_can_tie_to_a_win_goes_to_review_and_moves_nothing(app, world):
    world["say"](
        app,
        [
            ("", WIN, "player"),
            ("", "payment declined (not tied to a win): Payment failed", "player"),
        ],
    )
    run(app, world)
    assert sale_states(app, world["show"])[0][1] == "paid"
    assert open_reasons(app) == ["decline_untied"]


def test_a_decline_after_picking_started_is_flagged_for_a_person_not_restocked(app, world):
    world["say"](app, [("", WIN, "player")])
    run(app, world)
    with app.app_context():
        orders.advance(db.session.scalar(select(Order)), OrderStatus.picking, actor_label="t")
        db.session.commit()
    world["say"](app, [("", DECLINE, "player")])
    run(app, world)
    assert level(app, world["red"]) == (2, 0)  # the unit is in a box: a person decides
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale.status is SaleStatus.payment_failed and sale.flagged
    assert open_reasons(app) == ["closed_after_picking", "payment_declined"]


def test_a_person_restores_a_declined_sale_once_when_the_buyer_pays_another_way(app, world):
    world["say"](app, [("", WIN, "player"), ("", DECLINE, "player")])
    run(app, world)
    assert level(app, world["red"]) == (3, 0)
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "payment_declined"))
        pipeline.resolve(item, "set_status", {"status": "paid"}, user=owner(world["org"]))
        db.session.commit()
        assert db.session.scalar(select(Order)).status is OrderStatus.paid
    assert sale_states(app, world["show"]) == [("chonwhite828", "paid", 2500)]
    assert level(app, world["red"]) == (2, 0)
    # Declined again: the second round restocks under its own source...
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        pipeline.set_sale_status(
            sale, SaleStatus.payment_failed, reason="t", actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
    assert level(app, world["red"]) == (3, 0)
    # ...and a third payment is refused rather than silently moving nothing.
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        with pytest.raises(pipeline.ReviewError, match="already paid again"):
            pipeline.set_sale_status(sale, SaleStatus.paid, reason="t", actor=pipeline.RULE_ACTOR)
        db.session.rollback()
    assert level(app, world["red"]) == (3, 0)


# ---------- the payment file ----------


def _csv(app, world, text, *, apply=True):
    with app.app_context():
        from app.models import Show

        show = db.session.get(Show, world["show"])
        p = reconcile.plan(show, reconcile.parse_csv(text))
        if apply:
            reconcile.apply(show, p, actor=pipeline.RULE_ACTOR, import_id="t1")
        db.session.commit()
        return [(r.buyer, r.status, r.action) for r in p.rows], list(p.uncovered)


def test_the_payment_file_confirms_wins_and_records_its_declines(app, world):
    world["say"](
        app,
        [
            ("", WIN, "player"),
            ("", "kat.c won the auction for Red bundle #761 for $30.00", "player"),
        ],
    )
    run(app, world)
    rows, uncovered = _csv(
        app,
        world,
        "Buyer,Product,Sold Price,Status\n"
        "chonwhite828,Red bundle #760,25.00,Completed\n"
        "kat.c,Red bundle #761,30.00,Payment failed\n",
    )
    assert rows == [
        ("chonwhite828", "paid", "unchanged"),
        ("kat.c", "failed", "decline"),
    ]
    assert uncovered == []
    assert [s[1] for s in sale_states(app, world["show"])] == ["paid", "payment_failed"]
    assert level(app, world["red"]) == (2, 0)


def test_a_paid_row_restores_a_sale_declined_at_the_win(app, world):
    world["say"](app, [("", WIN, "player"), ("", DECLINE, "player")])
    run(app, world)
    rows, _ = _csv(
        app, world, "Buyer,Product,Sold Price,Status\nchonwhite828,Red bundle #760,25.00,Paid\n"
    )
    assert rows == [("chonwhite828", "paid", "mark_paid")]
    assert sale_states(app, world["show"])[0][1] == "paid"
    assert level(app, world["red"]) == (2, 0)
    assert open_reasons(app) == []  # the decline's review closed with it


def test_a_win_missing_from_the_payment_file_is_flagged(app, world):
    world["say"](
        app,
        [
            ("", WIN, "player"),
            ("", "kat.c won the auction for Red bundle #761 for $30.00", "player"),
        ],
    )
    run(app, world)
    _, uncovered = _csv(app, world, "Buyer,Product,Status\nchonwhite828,Red bundle #760,Paid\n")
    assert len(uncovered) == 1
    assert open_reasons(app) == ["no_payment_record"]
    # The same file again: the confirmed sale is not asked about twice.
    _, uncovered = _csv(app, world, "Buyer,Product,Status\nchonwhite828,Red bundle #760,Paid\n")
    assert len(uncovered) == 1


def test_the_backfill_command_lists_first_and_marks_older_reserved_sales_paid(app, world):
    with app.app_context():
        from app.models import Show

        show = db.session.get(Show, world["show"])
        sale = Sale(
            organization_id=world["org"],
            show_id=show.id,
            buyer_username="old.buyer",
            sku_id=world["red"],
            item_text="Red bundle",
            quantity=1,
            price_cents=2500,
        )
        db.session.add(sale)
        db.session.flush()
        pipeline._reserve(sale)  # how a sale stood before automatic payment
        db.session.commit()
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["autopay-reserved"])
    assert "would be marked paid" in dry.output and "old.buyer" in dry.output
    assert level(app, world["red"]) == (3, 1)
    done = runner.invoke(args=["autopay-reserved", "--apply"])
    assert "1 reserved sale(s) marked paid" in done.output
    assert level(app, world["red"]) == (2, 0)
    assert sale_states(app, world["show"]) == [("old.buyer", "paid", 2500)]


# ---------- peer review regressions (2026-10-27) ----------

WIN2 = "chonwhite828 won the auction for Red bundle #761 for $30.00"


def _resolve_decline(app, world, body):
    with app.app_context():
        item = db.session.scalar(
            select(ReviewItem).where(
                ReviewItem.reason == "payment_declined", ReviewItem.status == ReviewStatus.open
            )
        )
        pipeline.resolve(item, "set_status", body, user=owner(world["org"]))
        db.session.commit()


def test_a_repeated_decline_never_closes_the_buyers_other_sale(app, world):
    world["say"](app, [("", WIN, "player"), ("", WIN2, "player"), ("", DECLINE, "player")])
    run(app, world)
    world["say"](app, [("", DECLINE, "player")])  # re-rendered, or a second laptop
    run(app, world)
    assert [s[1] for s in sale_states(app, world["show"])] == ["payment_failed", "paid"]
    assert level(app, world["red"]) == (2, 0)


def test_a_decline_at_another_price_closes_nothing(app, world):
    world["say"](app, [("", WIN2, "player"), ("", DECLINE, "player")])  # $30 win, $25 decline
    run(app, world)
    assert [s[1] for s in sale_states(app, world["show"])] == ["paid"]
    assert open_reasons(app) == ["no_open_sale"]


def test_restoring_a_decline_that_never_came_back_takes_nothing_off_the_shelf_again(app, world):
    world["say"](app, [("", WIN, "player")])
    run(app, world)
    with app.app_context():
        orders.advance(db.session.scalar(select(Order)), OrderStatus.picking, actor_label="t")
        db.session.commit()
    world["say"](app, [("", DECLINE, "player")])
    run(app, world)
    assert level(app, world["red"]) == (2, 0)  # in the box: not restocked
    _resolve_decline(app, world, {"status": "paid"})
    assert sale_states(app, world["show"])[0][1] == "paid"
    assert level(app, world["red"]) == (2, 0)  # still one unit sold, not two


def test_a_decline_cannot_be_restored_onto_an_order_that_shipped_without_it(app, world):
    world["say"](app, [("", WIN, "player"), ("", WIN2, "player"), ("", DECLINE, "player")])
    run(app, world)
    with app.app_context():
        o = db.session.scalar(select(Order))
        for st in (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped):
            orders.advance(o, st, actor_label="t")
        db.session.commit()
    with pytest.raises(pipeline.ReviewError, match="already shipped"):
        _resolve_decline(app, world, {"status": "paid"})
    assert level(app, world["red"]) == (2, 0)


def test_a_declined_win_with_no_product_is_restored_once_its_product_is_named(app, world):
    world["say"](
        app,
        [
            ("", "cardguy won the auction for mystery box for $10.00", "player"),
            ("", "cardguy payment declined for mystery box for $10.00", "player"),
        ],
    )
    run(app, world)
    assert sale_states(app, world["show"]) == [("cardguy", "payment_failed", 1000)]
    with pytest.raises(pipeline.ReviewError, match="no product yet"):
        _resolve_decline(app, world, {"status": "paid"})
    _resolve_decline(app, world, {"status": "paid", "skuId": world["red"]})
    assert sale_states(app, world["show"]) == [("cardguy", "paid", 1000)]
    assert level(app, world["red"]) == (2, 0)


def test_a_win_added_after_picking_started_is_restocked_when_declined(app, world):
    world["say"](app, [("", WIN2, "player")])
    run(app, world)
    with app.app_context():
        orders.advance(db.session.scalar(select(Order)), OrderStatus.picking, actor_label="t")
        db.session.commit()
    world["say"](app, [("", WIN, "player"), ("", DECLINE, "player")])  # won after picking began
    run(app, world)
    assert [s[1] for s in sale_states(app, world["show"])] == ["paid", "payment_failed"]
    assert level(app, world["red"]) == (2, 0)  # #760 was never pulled: back on the shelf


def test_the_backfill_skips_sales_a_payment_file_left_out(app, world):
    with app.app_context():
        from app.models import Show

        show = db.session.get(Show, world["show"])
        for buyer in ("paid.one", "walker"):
            sale = Sale(
                organization_id=world["org"],
                show_id=show.id,
                buyer_username=buyer,
                sku_id=world["red"],
                item_text="Red bundle",
                quantity=1,
                price_cents=2500,
            )
            db.session.add(sale)
            db.session.flush()
            pipeline._reserve(sale)
        db.session.commit()
    _csv(app, world, "Buyer,Product,Status\npaid.one,Red bundle,Paid\n")  # walker uncovered
    out = app.test_cli_runner().invoke(args=["autopay-reserved", "--apply"]).output
    assert "skip sale" in out and "walker" in out
    assert "0 reserved sale(s) marked paid" in out
    assert sale_states(app, world["show"]) == [
        ("paid.one", "paid", 2500),
        ("walker", "reserved", 2500),
    ]


def test_an_untied_sold_tab_decline_offers_the_named_buyers_sale_first(app, world):
    world["say"](
        app,
        [
            ("", "kat.c won the auction for Red bundle #761 for $30.00", "player"),
            ("", WIN, "player"),
            (
                "",
                "payment declined (not tied to a win): "
                "@chonwhite828 Red bundle #760 $25 Payment failed",
                "player",
            ),
        ],
    )
    run(app, world)
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "decline_untied"))
        first = item.proposal["candidates"][0]
        assert "chonwhite828" in first["label"]
        pipeline.resolve(item, "pick_sale", {"saleId": first["saleId"]}, user=owner(world["org"]))
        db.session.commit()
    assert [s[1] for s in sale_states(app, world["show"])] == ["paid", "payment_failed"]
    assert level(app, world["red"]) == (2, 0)
    assert open_reasons(app) == ["payment_declined"]
