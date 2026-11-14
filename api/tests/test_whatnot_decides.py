"""Whatnot's own order decides whether a win was paid, and a declined payment is always
put to a person.

Once a shop applies Whatnot's orders as they arrive:
- a decline read from the stream (the video player, the Sold tab, a host's line, the AI
  reading the chat) no longer closes the sale. Half the cards declined at the win are paid
  a minute later (10 of 20 lots on the 2026-11-13 show), and the order is what knows. The
  sale is flagged for a person instead, and the flag clears when the order says paid;
- a sale Whatnot's failed order closes gets a "payment declined" item, and a failed order
  with no sale here gets an item of its own: a declined payment is never silent;
- a win that was declined before anyone named its product, and that Whatnot then says is
  paid, waits for its product again with nobody stepping in.

Where the order cannot decide (Compare only, a recording not matched to a show on Whatnot,
a token Whatnot rejects, orders with no buyer, a show over before the shop chose to), the
stream's decline closes the sale as it always did.

Every buyer, lot and order here is made up; Whatnot is the in-memory fake.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db import db
from app.models import (
    AuditLog,
    EventKind,
    InventoryMovement,
    Order,
    OrderStatus,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotConnection,
    WhatnotOrder,
    WhatnotShow,
)
from app.services import activity, order_slips, pipeline, supervisor
from app.services import orders as orders_service
from app.services.whatnot import orders as whatnot_orders
from tests import test_whatnot_catalog as cat
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import apply, order, read, reviews, sale_rows, set_mode, win

shop = wo.shop  # a connected shop; its show is matched to Whatnot by the first read

LOT = "CHOOSE ANY PRODUCT YOU WANT #41"


def declined(buyer, title, price="25.00"):
    """What the extension sends when Whatnot's player shows a card declined."""
    return ("", f"{buyer} payment declined for {title} for ${price}", "player")


def followed(app, shop):
    """Orders applied as they arrive, and the recording matched to its show on Whatnot."""
    set_mode(app, shop, "auto")
    read(app, shop)


def states(app):
    return [(buyer, status) for buyer, _, status, _, _ in sale_rows(app)]


def reasons(app):
    return [reason for reason, _ in reviews(app)]


def item_of(reason):
    """The one review item with this reason, whatever its status (inside an app context)."""
    return db.session.scalars(select(ReviewItem).where(ReviewItem.reason == reason)).one()


def moves(app, sale_id):
    with app.app_context():
        return [
            (m.kind.value, m.source_type)
            for m in db.session.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.source_id == str(sale_id))
                .order_by(InventoryMovement.id)
            )
        ]


# ---------- which shows Whatnot decides ----------


def test_whatnot_decides_only_a_followed_show_of_a_shop_that_applies_orders(app, shop):
    def decides():
        with app.app_context():
            return whatnot_orders.decides(db.session.get(Show, shop["show"]))

    def conn(**values):
        with app.app_context():
            row = db.session.scalar(select(WhatnotConnection))
            for k, v in values.items():
                setattr(row, k, v)
            db.session.commit()

    assert not decides()  # Compare only, and not matched to a show yet
    set_mode(app, shop, "auto")
    assert not decides()  # applying, but the recording is not matched to a show on Whatnot
    read(app, shop)
    assert decides()
    conn(problem="token_rejected")
    assert not decides()  # no order can arrive until a person fixes the token
    conn(problem="unreachable")
    assert decides()  # Whatnot did not answer once: the next read catches up
    conn(problem=None, auto_since=datetime.now(UTC))
    assert not decides()  # the show was over before the shop chose "as they arrive"
    conn(auto_since=None, disconnected_at=datetime.now(UTC))
    assert not decides()
    conn(disconnected_at=None, orders_mode="shadow")
    assert not decides()
    conn(orders_mode="auto")
    assert decides()
    with app.app_context():  # an order that does not say who bought it cannot be applied
        ws = db.session.scalar(select(WhatnotShow))
        db.session.add(
            WhatnotOrder(
                organization_id=shop["org"],
                whatnot_id="made-up-1",
                whatnot_show_id=ws.whatnot_id,
                status="failed",
                buyer_username=None,
            )
        )
        db.session.commit()
    assert not decides()


# ---------- a decline read from the stream ----------


def test_in_compare_only_a_decline_read_from_the_player_closes_the_sale_as_before(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert level(app, shop["red"]) == (3, 0)
    assert reasons(app) == ["payment_declined"]


def test_a_decline_read_from_the_player_flags_the_sale_and_is_left_to_whatnots_order(app, shop):
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "paid")]  # still sold: the order will say
    assert level(app, shop["red"]) == (2, 0)
    assert reasons(app) == ["decline_seen"]  # and a person is told
    with app.app_context():
        flag = item_of("decline_seen")
        assert flag.sale_id == db.session.scalar(select(Sale.id))
        assert "card declined for 1 × Red bundle ($25.00)" in flag.summary
        kinds = [e.kind for e in db.session.scalars(select(ParsedEvent).order_by(ParsedEvent.id))]
        assert kinds[-1] is EventKind.payment_failed  # the line is kept as what it is
    # The same decline seen again (the Sold tab after the player) flags nothing twice.
    shop["say"](app, [declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    assert reasons(app) == ["decline_seen"]
    # Whatnot agrees it failed: its order closes the sale, the unit comes back, and the
    # flag becomes the "payment declined" item a person can act on.
    o = order(shop, "ann_made_up", "Red bundle")
    shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert level(app, shop["red"]) == (3, 0)
    assert reasons(app) == ["payment_declined"]
    with app.app_context():
        assert item_of("decline_seen").resolution == {"by": "sale closed"}
        said = item_of("payment_declined").summary
        assert "The item is back in stock." in said and "comes back by itself" in said
    assert apply(app, shop) == {"unchanged": 1}  # reading again flags nothing more
    assert reasons(app) == ["payment_declined"]


def test_a_card_declined_and_paid_a_minute_later_never_leaves_the_shelf_and_comes_back(app, shop):
    """The 2026-11-13 show's shape. Before: the player's decline closed the sale and put the
    unit back, and the paid order took it again in a second round."""
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    assert reasons(app) == ["decline_seen"]
    wo.retried(shop, "ann_made_up", "Red bundle", 2500)
    read(app, shop)
    assert apply(app, shop) == {"skip": 1, "unchanged": 1}
    assert states(app) == [("ann_made_up", "paid")]
    assert level(app, shop["red"]) == (2, 0)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert moves(app, sale.id) == [("reserve", "sale"), ("commit", "sale")]  # one round
        flag = item_of("decline_seen")  # paid on Whatnot: the flag answered itself
        assert flag.status is ReviewStatus.resolved
        assert flag.resolution == {"by": "paid on Whatnot"} and flag.resolved_at is not None
    assert reasons(app) == []


def test_an_order_whatnot_is_still_charging_leaves_the_flag_open(app, shop):
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    order(shop, "ann_made_up", "Red bundle", status="pending")
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1}
    assert reasons(app) == ["decline_seen"]


def test_a_person_who_is_sure_can_close_the_flagged_sale_at_once(app, shop):
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    with app.app_context():
        pipeline.resolve(
            item_of("decline_seen"),
            "set_status",
            {"status": "payment_failed"},
            user=None,
            actor=pipeline.RULE_ACTOR,
        )
        db.session.commit()
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert level(app, shop["red"]) == (3, 0)
    # ...and if the buyer pays after all, Whatnot's order still puts the sale back.
    order(shop, "ann_made_up", "Red bundle")
    read(app, shop)
    assert apply(app, shop) == {"mark_paid": 1}
    assert states(app) == [("ann_made_up", "paid")]
    assert level(app, shop["red"]) == (2, 0)


def test_a_decline_the_extension_could_not_tie_to_a_win_still_asks_a_person(app, shop):
    untied = ("", "payment declined (not tied to a win): Payment failed $25", "player")
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), untied])
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "paid")]
    assert reasons(app) == ["decline_untied"]


def test_a_hosts_line_about_a_declined_payment_flags_the_sale_too(app, shop):
    followed(app, shop)
    shop["say"](
        app, [win("ann_made_up", "Red bundle"), ("host_shop", "payment declined for ann_made_up")]
    )
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "paid")] and reasons(app) == ["decline_seen"]
    assert level(app, shop["red"]) == (2, 0)


def test_a_viewers_line_about_a_declined_payment_flags_nothing(app, shop):
    """Chat is typed by anyone watching: a viewer can neither close a sale nor fill the
    admin's list with flags."""
    followed(app, shop)
    shop["say"](
        app, [win("ann_made_up", "Red bundle"), ("viewer9", "payment declined for ann_made_up")]
    )
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "paid")] and reasons(app) == []


def test_a_decline_for_a_win_nobody_recorded_waits_for_whatnots_failed_order(app, shop):
    """Wins that reach the app only as orders: the stream's decline has no sale to flag, and
    says nothing. The failed order is what a person is told about (below)."""
    followed(app, shop)
    shop["say"](app, [declined("ann_made_up", LOT, "11.00")])
    run(app, shop["show"])
    assert sale_rows(app) == [] and reasons(app) == []


def test_a_hosts_cancel_still_cancels(app, shop):
    """Only payment is Whatnot's to say. A sale the host calls off is the host's."""
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), ("host_shop", "ann_made_up cancelled")])
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "cancelled")]
    assert level(app, shop["red"]) == (3, 0)


@pytest.mark.parametrize("why", ["not matched", "token rejected", "over before"])
def test_where_whatnot_cannot_decide_the_players_decline_still_closes_the_sale(app, shop, why):
    set_mode(app, shop, "auto")
    if why != "not matched":
        read(app, shop)
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        if why == "token rejected":
            conn.problem = "token_rejected"
        if why == "over before":
            conn.auto_since = datetime.now(UTC)
        db.session.commit()
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert level(app, shop["red"]) == (3, 0)
    assert reasons(app) == ["payment_declined"]


@pytest.fixture
def fake_ai(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("JOBS_INLINE", "1")


def test_what_the_ai_reads_in_chat_about_a_payment_is_put_to_a_person_not_applied(
    app, shop, fake_ai
):
    """The control is tests/test_ai.py: the same two lines in a shop that is not connected
    close the sale and leave a payment_declined item."""
    followed(app, shop)
    shop["say"](
        app,
        [
            ("", "ann_made_up won Red bundle"),
            ("host_shop", "ann_made_up card declined, relisting"),
        ],
    )
    run(app, shop["show"])  # the parse queues the AI's look at the show; inline, it runs now
    assert states(app) == [("ann_made_up", "paid")]
    assert level(app, shop["red"]) == (2, 0)
    assert reasons(app) == ["payment_failed"]  # the AI's suggestion, for a person
    with app.app_context():
        assert supervisor.audit_show(shop["show"])["applied"] == 0
        item = item_of("payment_failed")
        assert item.source == "ai" and item.proposal["action"] == "payment_failed"
    # Whatnot says it was paid: the suggestion is answered.
    order(shop, "ann_made_up", "Red bundle")
    read(app, shop)
    apply(app, shop)
    assert states(app) == [("ann_made_up", "paid")] and reasons(app) == []
    with app.app_context():
        assert item_of("payment_failed").resolution == {"by": "paid on Whatnot"}


# ---------- a failed order with no sale here ----------


def test_a_payment_whatnot_declined_for_a_win_with_no_sale_is_flagged_once(app, shop):
    followed(app, shop)
    failed = order(shop, "ann_made_up", LOT, 1100)
    shop["fake"].set_order_status(failed["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"skip": 1}
    assert sale_rows(app) == []  # nothing to close, nothing recorded
    assert reasons(app) == ["order_declined"]
    with app.app_context():
        flag = item_of("order_declined")
        assert flag.sale_id is None and flag.proposal["orderId"] == failed["id"]
        assert f"Whatnot declined ann_made_up's payment for 1 × {LOT} ($11.00)" in flag.summary
    assert apply(app, shop) == {"skip": 1}
    assert reasons(app) == ["order_declined"]  # read again: still the one item
    with app.app_context():  # a person has seen it: it does not come back
        pipeline.resolve(
            item_of("order_declined"), "dismiss", {}, user=None, actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
    assert apply(app, shop) == {"skip": 1}
    assert reasons(app) == []


def test_the_flag_for_a_declined_order_clears_when_the_retry_is_paid(app, shop):
    followed(app, shop)
    early = datetime.now(UTC) - timedelta(seconds=90)
    failed = order(shop, "ann_made_up", LOT, 1100, created=early)
    shop["fake"].set_order_status(failed["id"], "failed")
    read(app, shop)
    apply(app, shop)
    assert reasons(app) == ["order_declined"]
    order(shop, "ann_made_up", LOT, 1100)  # the retry went through
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1, "skip": 1}
    assert states(app) == [("ann_made_up", "detected")]
    assert reasons(app) == ["unmatched_item"]  # only "which product is that?" is left
    with app.app_context():
        assert item_of("order_declined").resolution == {"by": "paid on Whatnot"}


def test_a_cancelled_order_with_no_sale_is_not_a_declined_payment(app, shop):
    followed(app, shop)
    void = order(shop, "ann_made_up", LOT, 1100)
    shop["fake"].set_order_status(void["id"], "cancelled")
    read(app, shop)
    assert apply(app, shop) == {"skip": 1}
    assert reasons(app) == []


def test_in_compare_only_a_person_applying_the_orders_gets_the_same_flags(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    for buyer, title in (("ann_made_up", "Red bundle"), ("bo_made_up", LOT)):
        o = order(shop, buyer, title, 2500)
        shop["fake"].set_order_status(o["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1, "skip": 1}
    assert reasons(app) == ["payment_declined", "order_declined"]
    with app.app_context():  # nothing comes back by itself here: a person marks it paid
        assert "mark it paid" in item_of("payment_declined").summary


# ---------- declined with no product, then paid ----------


def _declined_by_whatnot(app, shop, buyer="ann_made_up"):
    """A pick-your-own win whose card Whatnot declined: the failed order is applied before
    the retry exists, as it is when orders arrive one at a time."""
    shop["say"](app, [win(buyer, LOT, "11.00")])
    run(app, shop["show"])
    failed = order(shop, buyer, LOT, 1100, created=datetime.now(UTC) - timedelta(seconds=90))
    shop["fake"].set_order_status(failed["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    assert states(app) == [(buyer, "payment_failed")]
    assert reasons(app) == ["payment_declined"]  # a person is told
    return failed


def test_a_declined_win_with_no_product_paid_on_a_retry_waits_for_its_product_again(app, shop):
    failed = _declined_by_whatnot(app, shop)
    paid = order(shop, "ann_made_up", LOT, 1100)  # the retry went through
    read(app, shop)
    assert apply(app, shop) == {"reopen": 1, "skip": 1}
    with app.app_context():
        sale = db.session.scalar(select(Sale))  # still the one sale
        assert db.session.scalar(select(func.count()).select_from(Sale)) == 1
        assert (sale.status, sale.sku_id, sale.flagged) == (SaleStatus.detected, None, True)
        assert sale.status_reason == "item not recognised"
        assert sale.external_order_id == paid["id"] != failed["id"]  # the order that ships
        assert sale.order.status is OrderStatus.open  # the buyer has an order again
        assert moves(app, sale.id) == []  # nothing was ever held for it
    assert reasons(app) == ["unmatched_item"]  # "which product is that?" is open again
    assert apply(app, shop) == {"skip": 1, "unchanged": 1}  # reading again changes nothing
    assert reasons(app) == ["unmatched_item"]


def test_the_reopened_win_is_named_and_paid_like_any_other_one_round_of_stock(app, shop):
    _declined_by_whatnot(app, shop)
    order(shop, "ann_made_up", LOT, 1100)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.status == ReviewStatus.open))
        pipeline.resolve(
            item, "assign_sku", {"skuId": shop["red"]}, user=None, actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
        sale = db.session.scalar(select(Sale))
        assert sale.status is SaleStatus.paid and sale.order.status is OrderStatus.paid
        assert moves(app, sale.id) == [("reserve", "sale"), ("commit", "sale")]
    assert level(app, shop["red"]) == (2, 0)
    assert apply(app, shop) == {"skip": 1, "unchanged": 1}
    assert level(app, shop["red"]) == (2, 0)


def test_a_win_the_player_declined_before_it_was_matched_is_reopened_by_the_paid_order(app, shop):
    """Compare only, or the first minutes of a recording: the player's decline closed the
    sale and left a "mark it paid if they pay another way" item. Whatnot's paid order
    answers that item too."""
    read(app, shop)
    shop["say"](app, [win("bo_made_up", LOT, "11.00"), declined("bo_made_up", LOT, "11.00")])
    run(app, shop["show"])
    assert states(app) == [("bo_made_up", "payment_failed")]
    assert reasons(app) == ["payment_declined"]
    order(shop, "bo_made_up", LOT, 1100)
    read(app, shop)
    assert apply(app, shop) == {"reopen": 1}
    assert states(app) == [("bo_made_up", "detected")]
    assert reasons(app) == ["unmatched_item"]
    with app.app_context():
        was = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "payment_declined"))
        assert was.status is ReviewStatus.resolved and was.resolution == {"by": "sale paid"}


def test_a_declined_win_whose_buyers_package_already_left_goes_to_a_person(app, shop):
    _declined_by_whatnot(app, shop)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        sale.order.status = OrderStatus.shipped  # the buyer's other items went out without it
        db.session.commit()
    order(shop, "ann_made_up", LOT, 1100)
    read(app, shop)
    assert apply(app, shop) == {"skip": 1, "unmatched": 1}
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert any("marked payment declined here, but paid on Whatnot" in t for _, t in reviews(app))
    with app.app_context():
        assert not pipeline.await_product(db.session.scalar(select(Sale)))


def test_a_reopened_win_whose_listing_is_linked_to_a_product_is_named_at_once(app, shop):
    variant = cat.linked_listing(app, shop)  # "Winter special": the title names nothing
    shop["say"](app, [win("ann_made_up", "Winter special")])
    run(app, shop["show"])
    early = datetime.now(UTC) - timedelta(seconds=90)
    failed = cat.order_for(shop, "ann_made_up", variant, created=early)
    shop["fake"].set_order_status(failed["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    assert level(app, shop["red"]) == (3, 0)
    cat.order_for(shop, "ann_made_up", variant)
    read(app, shop)
    assert apply(app, shop) == {"reopen": 1, "skip": 1}
    assert states(app) == [("ann_made_up", "paid")] and reasons(app) == []
    assert level(app, shop["red"]) == (2, 0)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale.sku_id == shop["red"]
        assert moves(app, sale.id) == [("reserve", "sale"), ("commit", "sale")]


def test_a_win_a_person_cancelled_is_not_reopened_by_a_paid_order(app, shop):
    read(app, shop)
    shop["say"](app, [win("bo_made_up", LOT, "11.00")])
    run(app, shop["show"])
    with app.app_context():
        pipeline.set_sale_status(
            db.session.scalar(select(Sale)),
            SaleStatus.cancelled,
            reason="called off at the table",
            actor=pipeline.RULE_ACTOR,
        )
        db.session.commit()
    order(shop, "bo_made_up", LOT, 1100)
    read(app, shop)
    assert apply(app, shop) == {"unmatched": 1}
    assert states(app) == [("bo_made_up", "cancelled")]
    assert any("cancelled here, but paid on Whatnot" in summary for _, summary in reviews(app))


def test_await_product_refuses_every_sale_that_is_not_a_declined_win_with_no_product(app, shop):
    shop["say"](app, [win("ann_made_up", "Red bundle"), win("bo_made_up", LOT, "11.00")])
    run(app, shop["show"])
    with app.app_context():
        named, unnamed = db.session.scalars(select(Sale).order_by(Sale.id)).all()
        assert not pipeline.await_product(named)  # paid
        assert not pipeline.await_product(unnamed)  # already waiting
        pipeline.set_sale_status(
            named, SaleStatus.payment_failed, reason="test", actor=pipeline.RULE_ACTOR
        )
        assert not pipeline.await_product(named)  # declined WITH a product: `_repay`'s job
        db.session.commit()
        assert named.status is SaleStatus.payment_failed
        assert unnamed.status is SaleStatus.detected
    assert level(app, shop["red"]) == (3, 0)


def test_a_sale_named_while_the_orders_were_being_applied_is_left_as_the_person_made_it(
    app, shop, monkeypatch
):
    _declined_by_whatnot(app, shop)
    order(shop, "ann_made_up", LOT, 1100)
    read(app, shop)
    monkeypatch.setattr(pipeline, "await_product", lambda sale: False)  # lost the race
    assert apply(app, shop) == {"skip": 1, "unchanged": 1}
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert reasons(app) == ["payment_declined"]


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_as_they_arrive_a_declined_then_paid_win_is_flagged_then_settles_itself(app, client, shop):
    """The whole of it, the way a live show delivers it: the win, the failed order, the
    player's decline, then the paid order a minute later."""
    app.config["WHATNOT_WEBHOOK_SECRET"] = wo.SECRET
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", LOT, "11.00")])
    run(app, shop["show"])
    early = datetime.now(UTC) - timedelta(seconds=90)
    failed = order(shop, "ann_made_up", LOT, 1100, created=early)
    shop["fake"].set_order_status(failed["id"], "failed")
    wo.deliver(client, "orders.created", {"id": failed["id"], "show_id": shop["wshow"]})
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert reasons(app) == ["payment_declined"]
    shop["say"](app, [declined("ann_made_up", LOT, "11.00")])
    run(app, shop["show"])
    assert reasons(app) == ["payment_declined"]  # the player's decline adds nothing to it
    paid = order(shop, "ann_made_up", LOT, 1100)
    wo.deliver(client, "orders.created", {"id": paid["id"], "show_id": shop["wshow"]})
    assert states(app) == [("ann_made_up", "detected")]
    assert reasons(app) == ["unmatched_item"]
    with app.app_context():
        last = db.session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "whatnot.orders_applied")
            .order_by(AuditLog.id.desc())
        ).first()
        assert activity.who(last.action, last.actor_type, last.actor_label) == "Whatnot"
        assert activity.describe(last.action, last.details) == (
            "Applied Whatnot's orders to a show: "
            "1 waiting for a product again after a declined card was paid"
        )


# ---------- the order slips say who reads the parcels ----------


def test_the_order_slips_say_whether_the_app_reads_parcels_from_whatnot(app, shop):
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        assert order_slips.build(show)["parcelsFromWhatnot"] is True
        db.session.scalar(select(WhatnotConnection)).disconnected_at = datetime.now(UTC)
        db.session.flush()
        assert order_slips.build(show)["parcelsFromWhatnot"] is False
        db.session.rollback()


# ---------- found by the adversarial pass ----------


def test_an_order_with_a_card_in_doubt_is_not_picked_until_it_is_settled(app, shop):
    """The sale still counts as paid, so its order reads "Ready to pick". Before this
    stage the stream's decline closed the sale; now nothing of the order may go in a box
    while the card is in doubt."""
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    with app.app_context():
        buyers_order = db.session.scalar(select(Order))
        assert buyers_order.status is OrderStatus.paid
        with pytest.raises(orders_service.OrderError, match="showed a card declined"):
            orders_service.advance(buyers_order, OrderStatus.picking, actor_label="Packer")
        db.session.rollback()
    order(shop, "ann_made_up", "Red bundle")  # Whatnot says it was paid after all
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        buyers_order = db.session.scalar(select(Order))
        orders_service.advance(buyers_order, OrderStatus.picking, actor_label="Packer")
        assert buyers_order.status is OrderStatus.picking


def test_a_slip_with_a_card_in_doubt_is_not_among_the_ready_slips(app, shop):
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        doc = order_slips.build(show, only="ready")
        assert (doc["ready"], doc["leftOut"], doc["slips"]) == (0, 1, [])
    order(shop, "ann_made_up", "Red bundle")  # paid after all
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        assert order_slips.build(show, only="ready")["ready"] == 1


def test_a_flag_a_person_answered_or_the_order_cleared_does_not_come_back(app, shop):
    followed(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), declined("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    order(shop, "ann_made_up", "Red bundle")
    read(app, shop)
    apply(app, shop)
    assert reasons(app) == []  # paid on Whatnot
    shop["say"](app, [declined("ann_made_up", "Red bundle")])  # the Sold tab shows it again
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "paid")] and reasons(app) == []


def test_two_wins_of_one_listing_the_first_declined_the_second_paid_is_settled(app, shop):
    """One buyer, the same listing twice at the same price. The first win's card is declined
    and never retried; the second is paid. The orders alone read the payment as the first
    win's retry. The buyer's second win is standing with no order of its own, so the
    payment is that win's, and the first stays declined and flagged."""
    followed(app, shop)
    shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    read(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    first = order(shop, "ann_made_up", "Red bundle", created=datetime.now(UTC))
    shop["fake"].set_order_status(first["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    shop["say"](app, [win("ann_made_up", "Red bundle")])  # the second win
    run(app, shop["show"])
    order(shop, "ann_made_up", "Red bundle", created=datetime.now(UTC) + timedelta(minutes=3))
    read(app, shop)
    apply(app, shop)
    assert states(app) == [("ann_made_up", "payment_failed"), ("ann_made_up", "paid")]
    assert reasons(app) == ["payment_declined"]  # the first win's, still for a person
    assert level(app, shop["red"]) == (2, 0)  # one unit out for the one payment


def test_two_wins_of_one_listing_read_together_are_put_to_a_person_at_once(app, shop):
    """The same two wins, but the failed and the paid order arrive in one read. Nothing
    says which win the failed order was for, so the payment goes to the first and the win
    left with no order is flagged while the show is still live, not only when it is over."""
    followed(app, shop)
    shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    read(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), win("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    first = order(shop, "ann_made_up", "Red bundle", created=datetime.now(UTC))
    shop["fake"].set_order_status(first["id"], "failed")
    order(shop, "ann_made_up", "Red bundle", created=datetime.now(UTC) + timedelta(minutes=3))
    read(app, shop)
    apply(app, shop)
    assert states(app) == [("ann_made_up", "paid"), ("ann_made_up", "paid")]
    assert reasons(app) == ["no_payment_record"]
    with app.app_context():
        assert "one of them was not paid" in item_of("no_payment_record").summary
    # Had the second win really been paid too, its own order answers the item.
    order(shop, "ann_made_up", "Red bundle", created=datetime.now(UTC) + timedelta(minutes=4))
    read(app, shop)
    apply(app, shop)
    assert reasons(app) == []


def test_a_failed_order_with_no_time_does_not_stay_flagged_once_its_retry_is_paid(app, shop):
    followed(app, shop)
    failed = order(shop, "ann_made_up", LOT, 1100)
    shop["fake"].set_order_status(failed["id"], "failed")
    order(shop, "ann_made_up", LOT, 1100)  # the retry, paid
    read(app, shop)
    with app.app_context():  # Whatnot gave the failed order no time: nothing pairs the two
        db.session.execute(
            update(WhatnotOrder)
            .where(WhatnotOrder.whatnot_id == failed["id"])
            .values(ordered_at=None)
        )
        db.session.commit()
    apply(app, shop)
    assert states(app) == [("ann_made_up", "detected")]
    assert reasons(app) == ["unmatched_item"]  # no "payment declined: nothing recorded"
    apply(app, shop)
    assert reasons(app) == ["unmatched_item"]
