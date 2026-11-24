"""A card declined at the win and paid on a later retry is two Whatnot orders. When the app
recorded the payment as a second sale (a retry more than half an hour after the decline, or a
build from before 2026-11-14), the declined sale is history: `orders.paid_on_retry` says so,
the screens stop calling it a declined payment and its open "payment declined" question is
answered. Nothing about money or stock changes. Buyer names here are made up."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import tests.test_query_counts as tq
import tests.test_whatnot_orders as wo
from app.db import db
from app.models import ChatMessage, ReviewItem, Sale, Show, WhatnotOrder
from app.services import orders, pipeline
from app.services.whatnot import orders as whatnot_orders
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import LOT, apply, order, read, reviews, sale_rows, win

shop = wo.shop  # a connected shop; its show is matched to Whatnot by the first read
count_statements = tq.count_statements
SLACK = tq.SLACK

GAP = 45  # minutes from the declined attempt to the payment: past `same_win`'s half hour


def _at(minutes_ago: float) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes_ago)


def _backdate_last_line(app, at: datetime) -> None:
    """The win line was said `at` (the chat helper stamps lines with now)."""
    with app.app_context():
        line = db.session.scalar(select(ChatMessage).order_by(ChatMessage.id.desc()).limit(1))
        line.captured_at = at
        db.session.commit()


def declined_then_paid(
    app, shop, wins, *, gap=GAP, attempts=1, paid_status="pending_fulfillment", pay=None
):
    """Each (buyer, title, cents) win is said `gap + 5` minutes ago, its card is declined at
    the win (that decline applied), `attempts - 1` more attempts fail later, and Whatnot's
    paid order comes `gap` minutes after the win. `pay` maps a win to the (buyer, title) the
    payment is for, when it is not the same."""
    start = _at(gap + 5)
    for buyer, title, cents in wins:
        shop["say"](app, [win(buyer, title, f"{cents / 100:.2f}")])
        _backdate_last_line(app, start)
    run(app, shop["show"])
    for buyer, title, cents in wins:
        first = order(shop, buyer, title, cents, created=start + timedelta(seconds=10))
        shop["fake"].set_order_status(first["id"], "failed")
    read(app, shop)
    apply(app, shop)
    paid = []
    for buyer, title, cents in wins:
        for n in range(1, attempts):
            again = order(shop, buyer, title, cents, created=start + timedelta(minutes=gap - 5 + n))
            shop["fake"].set_order_status(again["id"], "failed")
        to_buyer, to_title = (pay or {}).get((buyer, title), (buyer, title))
        o = order(shop, to_buyer, to_title, cents, created=start + timedelta(minutes=gap))
        if paid_status != "pending_fulfillment":
            shop["fake"].set_order_status(o["id"], paid_status)
        paid.append(o)
    read(app, shop)
    apply(app, shop)
    return paid


def _retried(app) -> dict[int, int]:
    with app.app_context():
        return orders.paid_on_retry(db.session.scalars(select(Sale).order_by(Sale.id)).all())


def _ids(app) -> list[int]:
    with app.app_context():
        return list(db.session.scalars(select(Sale.id).order_by(Sale.id)))


def _decline_items(app) -> list[tuple[str, dict | None]]:
    with app.app_context():
        return [
            (r.status.value, r.resolution)
            for r in db.session.scalars(
                select(ReviewItem)
                .where(ReviewItem.reason == "payment_declined")
                .order_by(ReviewItem.id)
            )
        ]


# ---------- the live shape: paid more than half an hour after the decline ----------


def test_a_retry_paid_past_half_an_hour_is_not_a_declined_payment(app, shop):
    declined_then_paid(app, shop, [("kat.c", LOT, 1100)])
    # Today's pairing leaves the late payment as a sale of its own (not changed here).
    assert [r[:3] for r in sale_rows(app)] == [
        ("kat.c", LOT, "payment_failed"),
        ("kat.c", LOT, "detected"),
    ]
    first, second = _ids(app)
    assert _retried(app) == {first: second}
    # The question the decline opened is answered, and says by what.
    assert _decline_items(app) == [
        ("resolved", {"by": whatnot_orders.PAID_ON_RETRY, "sale": second})
    ]
    assert "payment_declined" not in [reason for reason, _ in reviews(app)]
    # Reading and applying again changes nothing: no third sale, the item stays answered.
    read(app, shop)
    apply(app, shop)
    assert _ids(app) == [first, second]
    assert [status for status, _ in _decline_items(app)] == ["resolved"]


def test_two_failed_attempts_the_first_past_half_an_hour(app, shop):
    """The second attempt pairs with the payment (`_skip_retried`), the first does not and
    declined the sale: still one win, paid."""
    declined_then_paid(app, shop, [("made_up_lou", LOT, 2100)], attempts=2)
    first, second = _ids(app)
    assert _retried(app) == {first: second}
    assert [status for status, _ in _decline_items(app)] == ["resolved"]


def test_the_orders_pages_say_paid_on_a_retry_not_declined(app, shop, client, sign_in):
    declined_then_paid(app, shop, [("kat.c", LOT, 1100)])
    first, second = _ids(app)
    sign_in(shop["org"])
    listed = client.get("/api/orders").get_json()["orders"]
    sales = {s["id"]: s for o in listed for s in o["sales"]}
    assert (sales[first]["status"], sales[first]["paidOnRetry"]) == ("payment_failed", second)
    assert sales[second]["paidOnRetry"] is None
    # The order's own page and the buyer's orders say the same.
    one = client.get(f"/api/orders/{sales[first]['orderId']}").get_json()["order"]
    assert {s["id"]: s["paidOnRetry"] for s in one["sales"]} == {first: second, second: None}
    theirs = client.get("/api/orders/buyers/kat.c").get_json()["orders"]
    assert {s["id"]: s["paidOnRetry"] for o in theirs for s in o["sales"]} == {
        first: second,
        second: None,
    }
    # The show's own sales list too.
    shown = client.get(f"/api/shows/{shop['show']}/sales").get_json()["sales"]
    assert {s["id"]: s["paidOnRetry"] for s in shown} == {first: second, second: None}


def test_stock_holds_one_unit_for_the_win(app, shop):
    """A product named on the win: the decline put its unit back, the payment's sale takes one.
    Saying the decline is history moves nothing."""
    title = "Red bundle #5"
    declined_then_paid(app, shop, [("kat.c", title, 2500)])
    rows = sale_rows(app)
    assert [r[2] for r in rows] == ["payment_failed", "paid"]
    assert level(app, shop["red"]) == (2, 0)
    first, second = _ids(app)
    assert _retried(app) == {first: second}
    assert level(app, shop["red"]) == (2, 0)


# ---------- the leftover shape: a build that recorded the retry as a second sale ----------


def _leftover(app, shop, *, paid_item_names="twin"):
    """Built by hand: today's code reopens the declined sale for a quick retry, but builds
    from before 2026-11-14 recorded it again as a second sale. The paid order's item names
    the twin, or (`paid_item_names="declined"`) still the declined sale. Returns the ids of
    the declined sale and of its twin; the decline's Review item is still open."""
    start = _at(20)
    shop["say"](app, [win("made_up_nia", LOT, "19.00")])
    _backdate_last_line(app, start)
    run(app, shop["show"])
    failed = order(shop, "made_up_nia", LOT, 1900, created=start + timedelta(seconds=10))
    shop["fake"].set_order_status(failed["id"], "failed")
    read(app, shop)
    apply(app, shop)
    paid = order(shop, "made_up_nia", LOT, 1900, created=start + timedelta(minutes=2))
    read(app, shop)
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        declined = db.session.scalar(select(Sale).order_by(Sale.id))
        pipeline._lock_buyer(show.organization_id, "made_up_nia")
        twin = pipeline.create_sale_from_order(
            show,
            buyer="made_up_nia",
            item_text=LOT,
            quantity=1,
            price_cents=1900,
            external_order_id=paid["id"],
            ordered_at=start + timedelta(minutes=2),
            settled=True,
        )
        db.session.flush()
        item = db.session.scalar(
            select(WhatnotOrder).where(WhatnotOrder.whatnot_id == paid["id"])
        ).items[0]
        item.sale_id = twin.id if paid_item_names == "twin" else declined.id
        db.session.commit()
        return declined.id, twin.id


def test_a_leftover_twin_from_an_older_build_reads_as_paid_on_a_retry(app, shop):
    """Applying now adds no third sale."""
    first, second = _leftover(app, shop)
    assert _retried(app) == {first: second}
    apply(app, shop)
    assert _ids(app) == [first, second]
    assert [r[2] for r in sale_rows(app)] == ["payment_failed", "detected"]
    assert _retried(app) == {first: second}


@pytest.mark.parametrize("names", ["twin", "declined"])
def test_they_paid_after_all_is_refused_for_a_decline_paid_on_a_retry(
    app, shop, client, sign_in, names
):
    """Review says why and offers no "They paid after all"; the server refuses it anyway, even
    where Whatnot's paid order still names the declined sale (so `whatnot_failed_order` alone
    would let it through and the win would be counted twice)."""
    first, second = _leftover(app, shop, paid_item_names=names)
    headers = sign_in(shop["org"])
    items = client.get("/api/review").get_json()["items"]
    (item,) = [i for i in items if i["reason"] == "payment_declined"]
    assert (item["sale"]["id"], item["sale"]["paidOnRetry"]) == (first, second)
    answer = client.post(
        f"/api/review/{item['id']}/resolve",
        # The win had no product: the button sends the one a person picks (Review's SKU box).
        json={"action": "set_status", "status": "paid", "skuId": shop["red"]},
        headers=headers,
    )
    assert answer.status_code == 409
    assert "count the win twice" in answer.get_json()["error"]
    assert [r[2] for r in sale_rows(app)] == ["payment_failed", "detected"]


# ---------- what is never a retry ----------


def test_a_payment_for_another_lot_is_not_a_retry(app, shop):
    other = LOT.replace("#73", "#74")
    declined_then_paid(app, shop, [("kat.c", LOT, 1100)], pay={("kat.c", LOT): ("kat.c", other)})
    assert [r[1:3] for r in sale_rows(app)] == [(LOT, "payment_failed"), (other, "detected")]
    assert _retried(app) == {}
    assert [status for status, _ in _decline_items(app)] == ["open"]


def test_another_buyers_payment_is_not_a_retry(app, shop):
    declined_then_paid(app, shop, [("kat.c", LOT, 1100)], pay={("kat.c", LOT): ("lani808", LOT)})
    assert _retried(app) == {}
    assert [status for status, _ in _decline_items(app)] == ["open"]


def test_a_payment_still_settling_is_not_yet_a_retry(app, shop):
    declined_then_paid(app, shop, [("kat.c", LOT, 1100)], paid_status="pending")
    assert _retried(app) == {}
    assert [status for status, _ in _decline_items(app)] == ["open"]


def test_once_the_retry_is_cancelled_the_decline_reads_as_declined_again(app, shop):
    (paid,) = declined_then_paid(app, shop, [("kat.c", LOT, 1100)])
    first, second = _ids(app)
    assert _retried(app) == {first: second}
    shop["fake"].set_order_status(paid["id"], "cancelled")
    read(app, shop)
    apply(app, shop)
    assert [r[2] for r in sale_rows(app)] == ["payment_failed", "cancelled"]
    assert _retried(app) == {}
    # A declined payment is always put to a person: the question this answered is asked again.
    assert [status for status, _ in _decline_items(app)] == ["open"]
    read(app, shop)
    apply(app, shop)
    assert [status for status, _ in _decline_items(app)] == ["open"]  # once, and it stays


@pytest.mark.parametrize("title", ["Red bundle #5", LOT])
def test_the_lot_won_again_later_with_its_own_banner_is_not_a_retry(app, shop, title):
    """The lot auctioned again 110 minutes later and won by the same buyer, read from its own
    win banner: that payment is the second auction's, and the first card's decline stands
    (found by the adversarial pass of 2026-11-24)."""
    first = _at(130)
    shop["say"](app, [win("zed.q", title, "25.00")])
    _backdate_last_line(app, first)
    run(app, shop["show"])
    failed = order(shop, "zed.q", title, 2500, created=first + timedelta(seconds=10))
    shop["fake"].set_order_status(failed["id"], "failed")
    read(app, shop)
    apply(app, shop)
    second = _at(20)
    shop["say"](app, [win("zed.q", title, "25.00")])
    _backdate_last_line(app, second)
    run(app, shop["show"])
    order(shop, "zed.q", title, 2500, created=second + timedelta(seconds=10))
    read(app, shop)
    apply(app, shop)
    assert _retried(app) == {}
    assert [status for status, _ in _decline_items(app)] == ["open"]


def test_a_list_with_many_retried_declines_costs_no_more_statements(
    app, shop, client, sign_in, count_statements
):
    declined_then_paid(app, shop, [("buyer0", LOT, 1100)])
    sign_in(shop["org"])
    with count_statements() as few:
        assert client.get("/api/orders").status_code == 200
    more = [(f"buyer{i}", LOT.replace("#73", f"#{80 + i}"), 1100) for i in range(1, 5)]
    declined_then_paid(app, shop, more)
    assert len(_retried(app)) == 5
    with count_statements() as many:
        assert client.get("/api/orders").status_code == 200
    assert many.count <= few.count + SLACK


# ---------- the rule itself ----------

AT = datetime(2026, 11, 22, 7, 56, tzinfo=UTC)


def _pair(
    *,
    title=LOT,
    paid_title=LOT,
    show="S1",
    paid_show="S1",
    failed_at=AT,
    paid_at=AT + timedelta(minutes=35),
    kind="auction",
    listings=None,
    declined_won=AT,
    retry_won=None,
):
    declined = Sale(id=1, buyer_username="kat.c", item_text=title)
    retry = Sale(id=2, buyer_username="kat.c", item_text=paid_title)
    failed = WhatnotOrder(
        id=10, whatnot_show_id=show, ordered_at=failed_at, transaction_type=kind, status="failed"
    )
    paid = WhatnotOrder(
        id=11,
        whatnot_show_id=paid_show,
        ordered_at=paid_at,
        transaction_type=kind,
        status="completed",
    )
    return orders._is_retry(
        declined,
        failed,
        retry,
        paid,
        listings or {},
        declined_won=declined_won,
        retry_won=retry_won,
    )


def test_the_rule_pairs_one_lot_of_one_show_paid_after_the_decline():
    assert _pair() is True
    assert _pair(paid_at=AT + timedelta(hours=20)) is True  # however late, in the same show
    assert _pair(listings={10: {"L1"}, 11: {"L1"}}) is True
    assert _pair(listings={10: {"L1"}}) is True  # one side's listing unknown
    # The retry's sale read from its own win line: another laptop's copy of this very win.
    assert _pair(retry_won=AT + timedelta(seconds=40)) is True
    assert _pair(declined_won=None, retry_won=AT + timedelta(minutes=29)) is True  # order time


@pytest.mark.parametrize(
    "case",
    [
        {"paid_title": LOT.replace("#73", "#74")},  # another lot
        {"title": "Red bundle", "paid_title": "Red bundle"},  # no lot number: many wins
        {"paid_show": "S2"},  # lot numbers start again in every show
        {"show": None, "paid_show": None},  # a show it cannot be pinned to
        {"paid_at": AT - timedelta(minutes=1)},  # paid before the decline
        {"paid_at": AT},  # not after it
        {"paid_at": None},
        {"failed_at": None},
        {"kind": "buy_it_now"},  # "Buy it now" orders are left alone
        {"listings": {10: {"L1"}, 11: {"L2"}}},  # two listings are two auctions
        {"retry_won": AT + timedelta(minutes=110)},  # the lot won again: its own banner
        {"retry_won": AT - timedelta(minutes=31)},  # a banner before the declined win
        {"declined_won": None, "failed_at": None, "retry_won": AT},  # nothing to compare
    ],
)
def test_the_rule_refuses(case):
    assert _pair(**case) is False
