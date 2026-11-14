"""One win per auction (the owner's rule, 2026-11-13).

"Each auction should have one win. If the win is recorded and the card is declined later,
flag it. If the win for the same auction is recorded for a different user, then that user
has the win." An auction is a numbered lot of one show: "$1 BEAUTY PULLS #520", "CHOOSE
ANY PRODUCT YOU WANT #6".

- On the stream (Whatnot's own "won the auction" banner): the same buyer on the same lot
  again is the same win. A different buyer on it is put to a person: the banner alone
  cannot say which win stands (the lot run again after a declined card, or an earlier
  banner that reached the app late), so nothing is cancelled on it.
- From Whatnot's orders (auction orders only): the payment goes to the buyer's sale for
  that lot; once the show's orders are complete, any other sale standing for the lot with
  no order of its own gives way; a payment for another listing is never a declined card's
  retry; another buyer's open decline for the lot is answered.
- A title with no lot number names no auction and is left alone. So is the same lot
  number hours apart: numbers start again in the next show.

Declined cards being flagged is tests/test_whatnot_decides.py. Every buyer, lot and order
here is made up; Whatnot is the in-memory fake.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import db
from app.models import AuditLog, ChatMessage, Order, OrderStatus, ReviewItem, Sale, Show
from app.services import activity, order_slips, pipeline
from app.services import orders as orders_service
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import apply, order, read, reviews, sale_rows, set_mode, win

shop = wo.shop  # a connected shop; its show is matched to Whatnot by the first read

LOT = "Red bundle #41"  # an auction: Whatnot's lot number is in the title
OTHER = "Red bundle #42"


def declined(buyer, title, price="25.00"):
    return ("", f"{buyer} payment declined for {title} for ${price}", "player")


def states(app):
    return [(buyer, status) for buyer, _, status, _, _ in sale_rows(app)]


def reasons(app):
    return [reason for reason, _ in reviews(app)]


def earlier(app, minutes):
    """Every line said so far was captured this long ago."""
    with app.app_context():
        for m in db.session.scalars(select(ChatMessage)):
            m.captured_at -= timedelta(minutes=minutes)
        db.session.commit()


def the_flags(reason="lot_won_twice"):
    return db.session.scalars(
        select(ReviewItem).where(ReviewItem.reason == reason).order_by(ReviewItem.id)
    ).all()


def the_flag(reason="lot_won_twice"):
    (only,) = the_flags(reason)
    return only


def listed(shop, buyer, title, listing, price=2500, **kw):
    """An order whose item names its listing on Whatnot, as the live shop's orders do."""
    kw.setdefault("created", datetime.now(UTC) - timedelta(seconds=20))
    return shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer=buyer,
        items=[{"title": title, "price_cents": price, "product_id": listing}],
        **kw,
    )


# ---------- what names an auction ----------


@pytest.mark.parametrize(
    "title,is_one",
    [
        ("$1 BEAUTY PULLS #520", True),
        ("CHOOSE ANY PRODUCT YOU WANT #6", True),
        ("Quick Hit 12", True),
        ("Red bundle", False),
        ("CHOOSE ANY PRODUCT YOU WANT", False),
        ("", False),
        (None, False),
    ],
)
def test_a_numbered_lot_is_an_auction_and_a_bare_title_is_not(title, is_one):
    assert (pipeline.auction_of(title) is not None) is is_one


def test_two_lots_are_two_auctions_however_alike():
    a, b = pipeline.auction_of("$1 BEAUTY PULLS #520"), pipeline.auction_of("$1 BEAUTY PULLS #521")
    assert a != b
    assert a == pipeline.auction_of("$1  beauty pulls  #520")


# ---------- on the stream ----------


def test_on_the_stream_a_lot_won_by_a_second_buyer_is_put_to_a_person(app, world):
    world["say"](app, [win("ann_made_up", LOT)])
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "paid")] and level(app, world["red"]) == (2, 0)
    world["say"](app, [win("bo_made_up", LOT)])
    run(app, world["show"])
    # Nothing is cancelled on the banner's word: both stand, and a person is asked.
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid")]
    assert reasons(app) == ["lot_won_twice", "lot_won_twice"]  # one on each sale in doubt
    with app.app_context():
        first, second = db.session.scalars(select(Sale).order_by(Sale.id)).all()
        flags = {f.sale_id: f for f in the_flags()}
        assert set(flags) == {first.id, second.id}
        assert "won by ann_made_up and then by bo_made_up" in flags[first.id].summary
        assert "if bo_made_up has it, cancel ann_made_up's sale" in flags[first.id].summary
        assert "if ann_made_up has it, cancel bo_made_up's sale" in flags[second.id].summary
        # Neither buyer's order goes in a box while the lot is in doubt.
        for buyers_order in db.session.scalars(select(Order)):
            with pytest.raises(orders_service.OrderError, match="two buyers"):
                orders_service.advance(buyers_order, OrderStatus.picking, actor_label="Packer")
        # One press settles it: the first buyer's sale is cancelled and its unit comes back.
        pipeline.resolve(
            flags[first.id],
            "set_status",
            {"status": "cancelled"},
            user=None,
            actor=pipeline.RULE_ACTOR,
        )
        db.session.commit()
    assert states(app) == [("ann_made_up", "cancelled"), ("bo_made_up", "paid")]
    assert level(app, world["red"]) == (2, 0)  # one unit out, for the one win
    assert reasons(app) == []  # and the question on bo's sale is answered with it
    with app.app_context():
        winners = db.session.scalar(select(Order).where(Order.buyer_username == "bo_made_up"))
        orders_service.advance(winners, OrderStatus.picking, actor_label="Packer")


def test_the_same_lot_number_hours_apart_is_another_auction(app, world):
    """Lot numbers start again in every show, and one recording can run through two."""
    world["say"](app, [win("ann_made_up", LOT)])
    earlier(app, 180)
    run(app, world["show"])
    world["say"](app, [win("bo_made_up", LOT), win("ann_made_up", LOT)])
    run(app, world["show"])
    assert states(app) == [
        ("ann_made_up", "paid"),
        ("bo_made_up", "paid"),
        ("ann_made_up", "paid"),
    ]
    assert reasons(app) == ["lot_won_twice", "lot_won_twice"]  # only tonight's two buyers
    with app.app_context():
        first = db.session.scalars(select(Sale).order_by(Sale.id)).first()
        assert first.id not in {f.sale_id for f in the_flags()}


def test_the_same_buyer_on_the_same_lot_again_is_the_same_win(app, world):
    world["say"](app, [win("ann_made_up", LOT), win("ann_made_up", LOT)])
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "paid")]
    assert level(app, world["red"]) == (2, 0)


def test_two_different_lots_are_two_wins(app, world):
    world["say"](app, [win("ann_made_up", LOT), win("bo_made_up", OTHER)])
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid")]
    assert level(app, world["red"]) == (1, 0)


def test_a_title_with_no_lot_number_can_be_won_many_times(app, world):
    world["say"](
        app,
        [win("ann_made_up", "Red bundle"), win("bo_made_up", "Red bundle"),
         win("ann_made_up", "Red bundle")],
    )  # fmt: skip
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid"), ("ann_made_up", "paid")]
    assert level(app, world["red"]) == (0, 0)


def test_a_declined_lot_won_again_by_another_buyer_needs_no_question(app, world):
    world["say"](app, [win("ann_made_up", LOT), declined("ann_made_up", LOT)])
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "payment_failed")]
    assert reasons(app) == ["payment_declined"]  # flagged for a person
    world["say"](app, [win("bo_made_up", LOT)])  # the host ran the lot again
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "payment_failed"), ("bo_made_up", "paid")]
    # One standing win: nothing to ask. The first buyer's flag is answered by Whatnot's
    # paid order (below), not by a banner.
    assert reasons(app) == ["payment_declined"]
    assert level(app, world["red"]) == (2, 0)


def test_a_declined_lot_won_again_by_the_same_buyer_is_a_new_sale_and_the_old_stays_closed(
    app, world
):
    world["say"](app, [win("ann_made_up", LOT), declined("ann_made_up", LOT)])
    run(app, world["show"])
    world["say"](app, [win("ann_made_up", LOT)])
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "payment_failed"), ("ann_made_up", "paid")]
    assert level(app, world["red"]) == (2, 0)  # one standing win, one unit out


def test_a_line_someone_types_in_chat_never_takes_a_lot(app, world):
    """Chat is typed by anyone watching. Only Whatnot's own banner ends a sale this way."""
    world["say"](app, [win("ann_made_up", LOT)])
    run(app, world["show"])
    typed = f"bo_made_up won the auction for {LOT} for $25.00"
    world["say"](app, [("viewer9", typed), ("host_shop", f"sold to bo_made_up {LOT}")])
    run(app, world["show"])
    assert states(app) == [("ann_made_up", "paid")]
    assert level(app, world["red"]) == (2, 0)


# ---------- from Whatnot's orders ----------


def test_whatnots_paid_order_for_a_lot_ends_another_buyers_sale_for_it(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    order(shop, "bo_made_up", LOT)  # Whatnot: the lot is bo's, and paid
    read(app, shop)  # the show is over and every order of it has been read
    assert apply(app, shop) == {"recorded": 1, "gave_way": 1}
    assert states(app) == [("ann_made_up", "cancelled"), ("bo_made_up", "paid")]
    assert sale_rows(app)[0][3] == "the auction was won by bo_made_up on Whatnot"
    assert level(app, shop["red"]) == (2, 0)
    assert apply(app, shop) == {"unchanged": 1}  # reading again changes nothing more
    assert states(app) == [("ann_made_up", "cancelled"), ("bo_made_up", "paid")]


def test_while_the_show_is_live_no_sale_is_cancelled_for_want_of_an_order(app, shop):
    """A missing order may simply not have arrived yet. The sale gives way only once every
    order of the show has been read after it ended."""
    shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    order(shop, "bo_made_up", LOT)
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1}
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid")]
    shop["fake"].shows[shop["wshow"]].update(
        status="ended", ended_at=datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    read(app, shop)
    read(app, shop)
    assert apply(app, shop)["gave_way"] == 1
    assert states(app) == [("ann_made_up", "cancelled"), ("bo_made_up", "paid")]


def test_a_sale_whatnot_confirmed_is_never_the_one_in_doubt(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    order(shop, "ann_made_up", LOT)  # ann's win is paid on Whatnot
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [win("bo_made_up", LOT)])  # then a banner names bo for the same lot
    run(app, shop["show"])
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid")]
    with app.app_context():
        second = db.session.scalars(select(Sale).order_by(Sale.id)).all()[1]
        assert the_flag().sale_id == second.id  # the question is about bo's, not ann's
    assert apply(app, shop)["gave_way"] == 1  # and Whatnot's orders answer it
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "cancelled")]
    assert reasons(app) == []
    assert level(app, shop["red"]) == (2, 0)


def test_the_same_lot_number_of_an_earlier_show_never_gives_way(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    earlier(app, 180)  # one recording through two shows: this "#41" was another auction
    run(app, shop["show"])
    order(shop, "bo_made_up", LOT)
    read(app, shop)
    summary = apply(app, shop)
    assert "gave_way" not in summary
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid")]


def test_a_won_again_lot_at_another_price_keeps_each_order_on_its_own_sale(app, shop):
    """ann wins the lot for $25 and her card is declined; the lot is run again and she wins
    it for $30. The failed order is the first win's, the paid one the second's."""
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT, "25.00"), declined("ann_made_up", LOT, "25.00")])
    run(app, shop["show"])
    shop["say"](app, [win("ann_made_up", LOT, "30.00")])
    run(app, shop["show"])
    first = order(shop, "ann_made_up", LOT, 2500, created=datetime.now(UTC) - timedelta(seconds=90))
    shop["fake"].set_order_status(first["id"], "failed")
    order(shop, "ann_made_up", LOT, 3000)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        got = [
            (sale.price_cents, sale.status.value)
            for sale in db.session.scalars(select(Sale).order_by(Sale.id))
        ]
    assert got == [(2500, "payment_failed"), (3000, "paid")]
    assert "price_mismatch" not in reasons(app)
    assert level(app, shop["red"]) == (2, 0)


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_as_they_arrive_the_activity_log_says_a_sale_gave_way(app, client, shop):
    app.config["WHATNOT_WEBHOOK_SECRET"] = wo.SECRET
    set_mode(app, shop, "auto")
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    o = order(shop, "bo_made_up", LOT)
    wo.deliver(client, "orders.created", {"id": o["id"], "show_id": shop["wshow"]})
    assert states(app) == [("ann_made_up", "cancelled"), ("bo_made_up", "paid")]
    with app.app_context():
        last = db.session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "whatnot.orders_applied")
            .order_by(AuditLog.id.desc())
        ).first()
        assert activity.describe(last.action, last.details) == (
            "Applied Whatnot's orders to a show: 1 sales recorded, "
            "1 cancelled because the auction went to another win"
        )


def test_a_buy_it_now_listing_sells_to_two_buyers(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    order(shop, "bo_made_up", LOT, transaction_type="buy_it_now")
    read(app, shop)
    # ann's win has no order of its own, and the show is over: that is put to a person
    # as it always was. Nothing gives way.
    assert apply(app, shop) == {"recorded": 1, "uncovered": 1}
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid")]
    assert reasons(app) == ["no_payment_record"]


def test_two_paid_orders_for_one_numbered_title_both_stand(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    order(shop, "ann_made_up", LOT)
    order(shop, "bo_made_up", LOT)
    read(app, shop)
    apply(app, shop)
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "paid")]


def test_an_auction_order_is_its_buyers_sale_for_the_lot_even_when_the_price_was_misread(app, shop):
    """Before: the order found no sale at its price and recorded the win a second time."""
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT, "25.00")])
    run(app, shop["show"])
    order(shop, "ann_made_up", LOT, 2400)  # Whatnot charged $24.00
    read(app, shop)
    apply(app, shop)
    assert states(app) == [("ann_made_up", "paid")]  # one win, one sale
    assert reasons(app) == ["price_mismatch"]  # and a person checks the price
    assert level(app, shop["red"]) == (2, 0)


def test_when_the_lot_goes_to_another_buyer_the_first_buyers_decline_is_answered(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    early = datetime.now(UTC) - timedelta(seconds=90)
    failed = order(shop, "ann_made_up", LOT, created=early)
    shop["fake"].set_order_status(failed["id"], "failed")
    lost = order(shop, "cy_made_up", OTHER, created=early)  # a decline with no sale here
    shop["fake"].set_order_status(lost["id"], "failed")
    read(app, shop)
    assert apply(app, shop) == {"decline": 1, "skip": 1}
    assert reasons(app) == ["payment_declined", "order_declined"]
    pending = order(shop, "bo_made_up", LOT, status="pending")
    read(app, shop)
    apply(app, shop)
    # Whatnot is still taking bo's payment: nothing is answered on a payment not made.
    assert reasons(app) == ["payment_declined", "order_declined"]
    shop["fake"].set_order_status(pending["id"], "pending_fulfillment")
    order(shop, "dee_made_up", OTHER)  # both lots were run again and paid
    read(app, shop)
    apply(app, shop)
    assert states(app) == [
        ("ann_made_up", "payment_failed"),
        ("bo_made_up", "paid"),
        ("dee_made_up", "paid"),
    ]
    assert reasons(app) == []
    with app.app_context():
        said = {
            i.reason: i.resolution
            for i in db.session.scalars(select(ReviewItem).order_by(ReviewItem.id))
        }
        assert said["payment_declined"] == {"by": "the auction went to another buyer"}
        assert said["order_declined"] == {"by": "the auction went to another buyer"}
        assert db.session.scalar(select(Sale).where(Sale.buyer_username == "ann_made_up"))
    assert level(app, shop["red"]) == (1, 0)


def test_a_payment_for_another_listing_is_never_a_declined_cards_retry(app, shop):
    """One buyer, two auctions whose titles and prices are alike. Whatnot's listing ids tell
    them apart: the first stays declined, the second is paid."""
    read(app, shop)
    shop["say"](app, [win("ann_made_up", "Red bundle"), win("ann_made_up", "Red bundle")])
    run(app, shop["show"])
    first = listed(shop, "ann_made_up", "Red bundle", "listing-1", created=datetime.now(UTC))
    shop["fake"].set_order_status(first["id"], "failed")
    listed(
        shop,
        "ann_made_up",
        "Red bundle",
        "listing-2",
        created=datetime.now(UTC) + timedelta(minutes=3),
    )
    read(app, shop)
    assert apply(app, shop) == {"decline": 1, "unchanged": 1}
    # Which of the two alike wins each order meets is by time; one is declined, one paid.
    assert sorted(status for _, status in states(app)) == ["paid", "payment_failed"]
    assert reasons(app) == ["payment_declined"]
    assert level(app, shop["red"]) == (2, 0)


def test_the_same_listing_failed_then_paid_is_still_one_retried_win(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    first = listed(
        shop, "ann_made_up", LOT, "listing-1", created=datetime.now(UTC) - timedelta(seconds=90)
    )
    shop["fake"].set_order_status(first["id"], "failed")
    listed(shop, "ann_made_up", LOT, "listing-1")
    read(app, shop)
    assert apply(app, shop) == {"skip": 1, "unchanged": 1}
    assert states(app) == [("ann_made_up", "paid")] and reasons(app) == []
    assert level(app, shop["red"]) == (2, 0)


# ---------- found by the second adversarial pass ----------


def test_a_buyers_own_paid_order_arriving_late_records_the_win_again(app, shop):
    """ann's sale gave way to bo's paid order once the show was over. Then Whatnot shows a
    paid order of ann's own for the lot (it arrived late). A cancelled sale cannot come
    back, so her win is recorded again; nothing is left for a person."""
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT)])
    run(app, shop["show"])
    order(shop, "bo_made_up", LOT)
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1, "gave_way": 1}
    order(shop, "ann_made_up", LOT)
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1, "recorded": 1}
    assert states(app) == [
        ("ann_made_up", "cancelled"),
        ("bo_made_up", "paid"),
        ("ann_made_up", "paid"),
    ]
    assert reasons(app) == []
    assert level(app, shop["red"]) == (1, 0)  # two paid orders, two units out
    assert apply(app, shop) == {"unchanged": 2}  # and it stays that way


def test_a_paid_order_answers_the_question_on_its_own_sale(app, shop):
    read(app, shop)
    shop["say"](app, [win("ann_made_up", LOT), win("bo_made_up", LOT)])
    run(app, shop["show"])
    assert reasons(app) == ["lot_won_twice", "lot_won_twice"]
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        assert order_slips.build(show, only="ready")["ready"] == 0  # neither slip is ready
    order(shop, "ann_made_up", LOT)  # Whatnot: ann has it, and paid
    read(app, shop)
    assert apply(app, shop) == {"unchanged": 1, "gave_way": 1}
    assert states(app) == [("ann_made_up", "paid"), ("bo_made_up", "cancelled")]
    assert reasons(app) == []  # nobody is still asked whether to cancel ann's sale
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        assert order_slips.build(show, only="ready")["ready"] == 1
    assert level(app, shop["red"]) == (2, 0)


def test_a_decline_with_no_sale_is_not_answered_by_the_same_lot_number_an_hour_on(app, shop):
    read(app, shop)
    lost = order(shop, "ann_made_up", LOT, created=datetime.now(UTC) - timedelta(minutes=90))
    shop["fake"].set_order_status(lost["id"], "failed")
    read(app, shop)
    apply(app, shop)
    assert reasons(app) == ["order_declined"]
    order(shop, "bo_made_up", LOT)  # the same title, an hour and a half later
    read(app, shop)
    apply(app, shop)
    assert "order_declined" in reasons(app)
