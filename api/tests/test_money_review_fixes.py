"""What the money-safety review of 2026-11-19 found, each pinned by a test that failed first.

- A declined payment the video player showed lands only on the same win: the same lot by
  title, within the half hour of the win for a sale an order made at a discounted price, and
  never on a sale whose own Whatnot order Whatnot says is paid (M1). When it matches such a
  sale AND another that is not, a person picks (second pass, P2).
- A paid order with no buyer is the retry of one buyer's declined attempts at one win,
  placed before it (S2): every attempt of that buyer's win, so a card declined twice and then
  paid stays paid (second pass, P1). With no order time, it holds the declines only when
  they are one buyer's (P1).
- A made show folds into a laptop's recording with one Order per buyer whatever the case
  of the name, and a box being packed stops it the same way (I1).
- The apply job stops cleanly when its show was folded away, and starts again later when
  the shows kept changing (C-3 / I5).
- Review: "shipped" on a win not paid here is a 409 in words; cancelling a paid win still
  to name is refused like "Ignore" (S5), applying the AI's cancel suggestion too, and the AI
  never does it by itself (second pass, S5 door).
- The parcels of a show that ended wait while another show is on air, and are asked for
  after it (I6).

Every buyer name here is made up; Whatnot is the in-memory fake.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import db
from app.models import (
    Job,
    Order,
    OrderStatus,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotShow,
    WhatnotSyncState,
)
from app.services import orders as orders_svc
from app.services import pipeline, show_merge, supervisor
from app.services.whatnot import client_for, sync
from app.services.whatnot import orders as whatnot_orders
from tests import test_whatnot_orders as wo
from tests.test_autopay import open_reasons, sale_states
from tests.test_autopay import run as run_world
from tests.test_paid_wins_to_pick import CHOOSE, unmatched
from tests.test_pipeline import level, run
from tests.test_show_merge import _laptop, _live, _made, _order, _quiet, _read, _shows_of, _ws
from tests.test_whatnot_orders import apply, order, read, set_mode, win
from tests.test_whatnot_reads import nameless_order

shop = wo.shop

ANN, BEA, CARA = "made_up_ann", "made_up_bea", "made_up_cara"


def decline(buyer, title, price="25.00"):
    return ("", f"{buyer} payment declined for {title} for ${price}", "player")


def open_on(app, sale_id):
    with app.app_context():
        return [
            r.reason
            for r in db.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.sale_id == sale_id, ReviewItem.status == ReviewStatus.open
                )
            )
        ]


def open_unattached(app):
    with app.app_context():
        return [
            r.reason
            for r in db.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.sale_id.is_(None), ReviewItem.status == ReviewStatus.open
                )
            )
        ]


def the_sale(app, buyer=ANN):
    with app.app_context():
        s = db.session.scalar(select(Sale).where(Sale.buyer_username == buyer).order_by(Sale.id))
        doubt = orders_svc.in_doubt(s.order) if s.order is not None else None
        return s.id, s.status, s.price_cents, s.external_order_id, doubt


# ---------- M1: a decline lands only on its own win ----------


@pytest.mark.parametrize("mode", ["shadow", "auto"])
def test_a_decline_for_another_lot_leaves_a_discounted_sale_paid_on_whatnot_alone(app, shop, mode):
    """The order made the sale at what Whatnot charged ($23.70); the player then shows a
    different lot of the same buyer declined at $25.00. Not the same win: nothing closes,
    nothing is flagged, the buyer's order is not put in doubt."""
    set_mode(app, shop, mode)
    order(shop, ANN, "Red bundle", 2500, discount_cents=130)
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [decline(ANN, "Blue thing")])
    run(app, shop["show"])
    sale_id, status, price, _, doubt = the_sale(app)
    assert (status, price, doubt) == (SaleStatus.paid, 2370, None)
    assert open_on(app, sale_id) == []
    assert level(app, shop["red"]) == (2, 0)
    if mode == "shadow":
        assert "no_open_sale" in open_unattached(app)  # a person is told, as before


def test_a_decline_for_another_lot_at_the_same_price_closes_nothing(app, world):
    """No Whatnot at all: the buyer's only sale is a $25.00 Red bundle, the player shows a
    $25.00 Blue thing declined. Same price is not the same win."""
    world["say"](
        app,
        [
            ("", f"{ANN} won the auction for Red bundle for $25.00", "player"),
            decline(ANN, "Blue thing"),
        ],
    )
    run_world(app, world)
    assert sale_states(app, world["show"]) == [(ANN, "paid", 2500)]
    assert level(app, world["red"]) == (2, 0)
    assert open_reasons(app) == ["no_open_sale"]


@pytest.mark.parametrize("mode", ["shadow", "auto"])
def test_a_decline_of_a_win_whatnot_says_is_paid_closes_and_flags_nothing(app, shop, mode):
    """The same win, the same price, and Whatnot's own order for it is paid (a card declined
    at the win and paid a moment later): the sale stands, unflagged."""
    set_mode(app, shop, mode)
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    order(shop, ANN, "Red bundle", 2500)
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [decline(ANN, "Red bundle")])
    run(app, shop["show"])
    sale_id, status, _, external, doubt = the_sale(app)
    assert external is not None
    assert (status, doubt) == (SaleStatus.paid, None)
    assert open_on(app, sale_id) == []
    assert "no_open_sale" not in open_unattached(app)  # it was found: nothing to do
    assert level(app, shop["red"]) == (2, 0)


@pytest.mark.parametrize("mode", ["shadow", "auto"])
def test_a_decline_of_a_win_whose_card_was_retried_and_paid_closes_and_flags_nothing(
    app, shop, mode
):
    """Whatnot's real shape of a retried card: a failed order at the win, then a paid one.
    Both are read and applied; the player's decline line (late, or seen again) changes
    nothing: Whatnot says the win is paid."""
    set_mode(app, shop, mode)
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, ANN, "Red bundle", 2500, status="failed", created=now - timedelta(seconds=30))
    order(shop, ANN, "Red bundle", 2500, created=now - timedelta(seconds=10))
    read(app, shop)
    apply(app, shop)
    assert the_sale(app)[1] is SaleStatus.paid
    shop["say"](app, [decline(ANN, "Red bundle")])
    run(app, shop["show"])
    sale_id, status, _, _, doubt = the_sale(app)
    assert (status, doubt) == (SaleStatus.paid, None)
    assert open_on(app, sale_id) == []
    assert level(app, shop["red"]) == (2, 0)


def test_a_full_price_decline_is_not_tied_to_a_discounted_win_from_long_before(app, shop):
    """The order was placed two hours before the decline: another win of the same lot, not
    this one (the half hour every other win look-up uses)."""
    order(
        shop,
        ANN,
        "Red bundle",
        2500,
        discount_cents=130,
        status="pending",
        created=datetime.now(UTC) - timedelta(hours=2),
    )
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [decline(ANN, "Red bundle")])
    run(app, shop["show"])
    sale_id, status, price, _, _ = the_sale(app)
    assert (status, price) == (SaleStatus.paid, 2370)
    assert open_on(app, sale_id) == []
    assert "no_open_sale" in open_unattached(app)


def test_a_full_price_decline_still_closes_its_discounted_win_while_whatnot_is_settling(app, shop):
    """The legitimate case: the order made the sale at $23.70, Whatnot has not settled it,
    and the player shows that very win declined at its full $25.00. It is closed."""
    order(shop, ANN, "Red bundle", 2500, discount_cents=130, status="pending")
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [decline(ANN, "Red bundle")])
    run(app, shop["show"])
    sale_id, status, _, _, _ = the_sale(app)
    assert status is SaleStatus.payment_failed
    assert open_on(app, sale_id) == ["payment_declined"]
    assert level(app, shop["red"]) == (3, 0)


def test_a_decline_without_the_lot_number_still_closes_its_win(app, shop):
    """The sale carries Whatnot's lot number ("Red bundle #752"), the decline line does not:
    the same lot, so the decline is not lost (a declined payment is always acted on)."""
    order(shop, ANN, "Red bundle #752", 2500, status="pending")
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [decline(ANN, "Red bundle")])
    run(app, shop["show"])
    sale_id, status, _, _, _ = the_sale(app)
    assert status is SaleStatus.payment_failed
    assert open_on(app, sale_id) == ["payment_declined"]


def test_a_decline_for_another_lot_number_closes_nothing(app, shop):
    """ "Red bundle #7" and "Red bundle #8" are two wins: a decline for one never closes the
    other."""
    order(shop, ANN, "Red bundle #7", 2500, status="pending")
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [decline(ANN, "Red bundle #8")])
    run(app, shop["show"])
    assert the_sale(app)[1] is not SaleStatus.payment_failed


def ambiguous(app):
    """The open "which sale was it?" items: the sales each offers, and its words."""
    with app.app_context():
        return [
            ([c["saleId"] for c in (r.proposal or {}).get("candidates", [])], r.summary)
            for r in db.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.reason == "ambiguous_sale", ReviewItem.status == ReviewStatus.open
                )
            )
        ]


@pytest.mark.parametrize("mode", ["shadow", "auto"])
def test_a_decline_matching_a_win_paid_on_whatnot_and_another_open_one_goes_to_a_person(
    app, shop, mode
):
    """Two wins of the same lot at the same price by one buyer: the first has its own Whatnot
    order, paid; the second has none yet. The player's decline could be about either (a late
    upload of the first's retried card, or the second's). Nothing is closed, nothing is
    flagged, the buyer's order is not put in doubt: a person picks."""
    set_mode(app, shop, mode)
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    order(shop, ANN, "Red bundle", 2500, created=datetime.now(UTC) - timedelta(seconds=30))
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])  # a second win of the same lot
    run(app, shop["show"])
    shop["say"](app, [decline(ANN, "Red bundle")])
    run(app, shop["show"])
    with app.app_context():
        sales = db.session.scalars(
            select(Sale).where(Sale.buyer_username == ANN).order_by(Sale.id)
        ).all()
        assert [s.status for s in sales] == [SaleStatus.paid, SaleStatus.paid]
        assert sales[0].external_order_id is not None and sales[1].external_order_id is None
        assert {orders_svc.in_doubt(s.order) for s in sales} == {None}
        paid_there, open_one = sales[0].id, sales[1].id
    assert open_on(app, paid_there) == [] and open_on(app, open_one) == []
    [(offered, words)] = ambiguous(app)
    assert offered == [open_one]  # never the win Whatnot says is paid
    assert "paid on Whatnot" in words
    for internal in ("payment_failed", "paid)", "detected"):
        assert internal not in words
    assert level(app, shop["red"]) == (1, 0)


@pytest.mark.parametrize("mode", ["shadow", "auto"])
def test_a_decline_matching_only_wins_paid_on_whatnot_changes_nothing(app, shop, mode):
    """Both wins of the lot have their own paid orders: the decline is history."""
    set_mode(app, shop, mode)
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    order(shop, ANN, "Red bundle", 2500, created=datetime.now(UTC) - timedelta(seconds=30))
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    order(shop, ANN, "Red bundle", 2500, created=datetime.now(UTC) - timedelta(seconds=5))
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [decline(ANN, "Red bundle")])
    run(app, shop["show"])
    with app.app_context():
        sales = db.session.scalars(select(Sale).where(Sale.buyer_username == ANN)).all()
        assert sorted(s.status.value for s in sales) == ["paid", "paid"]
        assert all(s.external_order_id for s in sales)
        assert {orders_svc.in_doubt(s.order) for s in sales} == {None}
    assert ambiguous(app) == []
    assert "no_open_sale" not in open_unattached(app)
    assert level(app, shop["red"]) == (1, 0)


# ---------- S2: a nameless paid order is the retry of one buyer's declines of one win ----------


def _filler(shop, n=6):
    for i in range(n):
        order(shop, f"made_up_filler_{i}", f"Lot {i}")


def test_a_paid_order_with_no_buyer_placed_before_a_decline_is_not_its_retry(app, shop):
    shop["say"](app, [win(ANN, "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    nameless_order(shop, "Red bundle", created=now - timedelta(minutes=10))
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=10))
    _filler(shop)
    read(app, shop)
    assert apply(app, shop).get("decline") == 1
    assert the_sale(app)[1] is SaleStatus.payment_failed


def test_one_paid_order_with_no_buyer_stands_for_the_closest_decline_only(app, shop):
    shop["say"](app, [win(ANN, "Red bundle"), win(BEA, "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=30))
    order(shop, BEA, "Red bundle", status="failed", created=now - timedelta(seconds=25))
    nameless_order(shop, "Red bundle", created=now - timedelta(seconds=10))
    _filler(shop)
    read(app, shop)
    assert apply(app, shop).get("decline") == 1
    assert the_sale(app, ANN)[1] is SaleStatus.payment_failed
    assert the_sale(app, BEA)[1] is SaleStatus.paid  # held: the nameless payment may be hers


def test_a_card_declined_twice_then_paid_in_an_order_with_no_buyer_stays_paid(app, shop):
    """Whatnot's shape of a card that failed twice at the win and went through on the third
    try, the paying order with no buyer: both failed orders are that one win's attempts, and
    the one payment answers both. Declining would take back a sale the buyer paid."""
    shop["say"](app, [win(ANN, "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=40))
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=30))
    nameless_order(shop, "Red bundle", created=now - timedelta(seconds=10))
    _filler(shop)
    read(app, shop)
    summary = apply(app, shop)
    assert "decline" not in summary
    assert the_sale(app)[1] is SaleStatus.paid
    assert level(app, shop["red"]) == (2, 0)


def test_one_paid_order_with_no_buyer_holds_every_attempt_of_the_closest_buyer_only(app, shop):
    """Bea's card failed twice, Ann's once in between; one payment with no buyer follows.
    Bea's attempt is the closest before it: both of hers are held, Ann's is a decline (one
    payment never pays two buyers' wins)."""
    shop["say"](app, [win(ANN, "Red bundle"), win(BEA, "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, BEA, "Red bundle", status="failed", created=now - timedelta(seconds=40))
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=35))
    order(shop, BEA, "Red bundle", status="failed", created=now - timedelta(seconds=25))
    nameless_order(shop, "Red bundle", created=now - timedelta(seconds=10))
    _filler(shop)
    read(app, shop)
    assert apply(app, shop).get("decline") == 1
    assert the_sale(app, ANN)[1] is SaleStatus.payment_failed
    assert the_sale(app, BEA)[1] is SaleStatus.paid


def test_a_decline_dated_after_the_payment_with_no_buyer_that_answers_an_earlier_one_is_held(
    app, shop
):
    """Ann's card failed, an order with no buyer paid, and Whatnot dates one more failed
    order of hers after it. The payment may be hers and cannot claim her sale, so the later
    attempt would decline a win she paid: it is held with the first."""
    shop["say"](app, [win(ANN, "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=40))
    nameless_order(shop, "Red bundle", created=now - timedelta(seconds=30))
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=20))
    _filler(shop)
    read(app, shop)
    assert "decline" not in apply(app, shop)
    assert the_sale(app)[1] is SaleStatus.paid
    assert level(app, shop["red"]) == (2, 0)


def test_a_failed_order_with_no_time_is_held_by_a_dated_payment_with_no_buyer(app, shop):
    """Whatnot gave the failed order no time, so it cannot be placed before the payment; it
    is the only buyer's, so it is held as for a payment with no time."""
    shop["say"](app, [win(ANN, "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    failed = order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=40))
    failed["created_at"] = None
    nameless_order(shop, "Red bundle", created=now - timedelta(seconds=10))
    _filler(shop)
    read(app, shop)
    assert "decline" not in apply(app, shop)
    assert the_sale(app)[1] is SaleStatus.paid


def _untimed_nameless(shop, title="Red bundle"):
    o = nameless_order(shop, title, created=datetime.now(UTC) - timedelta(seconds=10))
    o["created_at"] = None  # Whatnot gave no order time
    return o


def test_a_paid_order_with_no_buyer_and_no_time_holds_one_buyers_declines(app, shop):
    """No time to place it after the failed order, but only one buyer's win it could pay:
    held, as before the first pass. Undoing a sale the buyer paid is worse than a decline a
    person chases."""
    shop["say"](app, [win(ANN, "Red bundle")])
    run(app, shop["show"])
    order(
        shop, ANN, "Red bundle", status="failed", created=datetime.now(UTC) - timedelta(seconds=30)
    )
    _untimed_nameless(shop)
    _filler(shop)
    read(app, shop)
    assert "decline" not in apply(app, shop)
    assert the_sale(app)[1] is SaleStatus.paid


def test_a_paid_order_with_no_buyer_and_no_time_holds_nothing_for_two_buyers(app, shop):
    """Two buyers' declines it could be the retry of, and no time to tell which: both are
    declines, as with no such payment."""
    shop["say"](app, [win(ANN, "Red bundle"), win(BEA, "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, ANN, "Red bundle", status="failed", created=now - timedelta(seconds=30))
    order(shop, BEA, "Red bundle", status="failed", created=now - timedelta(seconds=25))
    _untimed_nameless(shop)
    _filler(shop)
    read(app, shop)
    assert apply(app, shop).get("decline") == 2
    assert the_sale(app, ANN)[1] is SaleStatus.payment_failed
    assert the_sale(app, BEA)[1] is SaleStatus.payment_failed


# ---------- I1: one Order per buyer after a fold, whatever the case ----------


def _orders(app):
    with app.app_context():
        return [
            (o.show_id, o.buyer_username.lower(), o.status.value)
            for o in db.session.scalars(select(Order).order_by(Order.id))
        ]


def test_a_fold_joins_a_buyers_orders_whatever_the_case_of_the_name(app, shop):
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, CARA, "Red bundle", minutes=35)
    _made(app, shop, live)
    rec = _laptop(app, shop, live, address=True, lines=[win("Made_Up_Cara", "Blue bundle", "7.00")])
    run(app, rec)
    _read(app, shop, live)
    assert _shows_of(app, live) == [rec]
    assert [(s, b) for s, b, _ in _orders(app)] == [(rec, CARA)]
    with app.app_context():
        assert len(db.session.scalar(select(Order)).sales) == 2


def test_a_box_being_packed_stops_the_fold_whatever_the_case_of_the_name(app, shop):
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, CARA, "Red bundle", minutes=35)
    made = _made(app, shop, live)
    with app.app_context():
        o = db.session.scalar(select(Order).where(Order.show_id == made))
        orders_svc.advance(o, OrderStatus.picking, actor_label="t")
        orders_svc.advance(o, OrderStatus.packed, actor_label="t")
        db.session.commit()
    rec = _laptop(app, shop, live, address=True, lines=[win("Made_Up_Cara", "Blue bundle", "7.00")])
    run(app, rec)
    _read(app, shop, live)  # links the recording; the fold on its own is refused
    assert sorted(_shows_of(app, live)) == sorted([made, rec])
    assert sorted(_orders(app)) == sorted([(made, CARA, "packed"), (rec, CARA, "open")])
    with app.app_context():
        blockers = show_merge.preview(_ws(live))["blockers"]  # the owner's merge says why
    assert len(blockers) == 1
    assert CARA in blockers[0].lower()


# ---------- C-3 / I5: the apply job and a show folded away ----------


def test_the_apply_job_stops_cleanly_when_its_made_show_was_folded_away(app, shop, monkeypatch):
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, CARA, "Red bundle", minutes=35)
    made = _made(app, shop, live)
    rec = _laptop(app, shop, live, address=True)
    set_mode(app, shop, "auto")
    real_ensure = sync.ensure_recording

    def ensure(ws):
        show = real_ensure(ws)  # the made show, and meanwhile another process folds it
        with app.app_context():
            sync.sync_shows(shop["org"], client_for(shop["org"]))
            db.session.commit()
        return show

    monkeypatch.setattr(sync, "ensure_recording", ensure)
    real_rows = whatnot_orders.rows_for

    def blind(ws):
        rows, info = real_rows(ws)
        return rows, {**info, "blind": True}  # an OrdersError after the fold

    monkeypatch.setattr(whatnot_orders, "rows_for", blind)
    with app.app_context():
        db.session.query(Job).delete()
        db.session.commit()
        whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": live})
        db.session.commit()
    assert _shows_of(app, live) == [rec]
    with app.app_context():
        assert db.session.get(Show, made) is None
        again = db.session.scalars(select(Job).where(Job.kind == "whatnot_apply_show")).all()
        assert [j.payload["whatnot_show_id"] for j in again] == [live]


def test_an_apply_whose_shows_keep_changing_is_queued_again(app, shop, monkeypatch):
    read(app, shop)  # in "Compare only": no apply queued by the read
    set_mode(app, shop, "auto")
    tries = []

    def changing(show, **kw):
        tries.append(show.id)
        raise whatnot_orders.ShowsChanged()

    monkeypatch.setattr(whatnot_orders, "apply", changing)
    with app.app_context():
        assert db.session.scalars(select(Job).where(Job.kind == "whatnot_apply_show")).all() == []
        whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
        db.session.commit()
    assert len(tries) == 3
    with app.app_context():
        again = db.session.scalars(select(Job).where(Job.kind == "whatnot_apply_show")).all()
        assert [j.payload["whatnot_show_id"] for j in again] == [shop["wshow"]]
        assert again[0].status.value == "queued" and again[0].run_at > datetime.now(UTC)


# ---------- S5: review's "set status" on a paid win still to name ----------


def _to_name(app, shop):
    order(shop, ANN, CHOOSE.format(12), 1100)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    return item_id, sale_id


def test_marking_a_win_not_paid_here_shipped_is_a_409_in_words(app, client, shop, sign_in):
    item_id, sale_id = _to_name(app, shop)
    h = sign_in(shop["org"], role="manager")
    r = client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "set_status", "status": "fulfilled"},
        headers=h,
    )
    assert r.status_code == 409
    words = r.get_json()["error"]
    assert "shipped" in words
    for internal in ("fulfilled", "detected", "status"):
        assert internal not in words
    assert the_sale(app)[1] is SaleStatus.detected


def test_cancelling_a_paid_win_still_to_name_is_refused_like_ignore(app, client, shop, sign_in):
    item_id, sale_id = _to_name(app, shop)
    h = sign_in(shop["org"], role="manager")
    r = client.post(
        f"/api/review/{item_id}/resolve",
        json={"action": "set_status", "status": "cancelled"},
        headers=h,
    )
    assert r.status_code == 409
    assert "Name the product" in r.get_json()["error"]
    assert the_sale(app)[1] is SaleStatus.detected
    assert unmatched(app) == [(item_id, sale_id, "open")]


def _ai_cancel_suggestion(app, sale_id, confidence=0.99):
    with app.app_context():
        s = db.session.get(Sale, sale_id)
        item = pipeline.open_review(
            s.organization_id,
            "cancel_sale",
            "The AI read this purchase as cancelled.",
            show_id=s.show_id,
            sale_id=s.id,
            proposal={
                "action": "cancel_sale",
                "saleId": s.id,
                "confidence": confidence,
                "summary": "cancelled",
            },
            source="ai",
        )
        db.session.commit()
        return item.id


def test_applying_the_ais_cancel_suggestion_on_a_paid_win_still_to_name_is_refused(
    app, client, shop, sign_in
):
    """The other door to the same cancel: a person presses "apply" on the AI's suggestion.
    Refused in the same words; the win still waits for its product."""
    item_id, sale_id = _to_name(app, shop)
    suggestion = _ai_cancel_suggestion(app, sale_id)
    h = sign_in(shop["org"], role="manager")
    r = client.post(
        f"/api/review/{suggestion}/resolve", json={"action": "apply_proposal"}, headers=h
    )
    assert r.status_code == 409
    assert "Name the product" in r.get_json()["error"]
    assert the_sale(app)[1] is SaleStatus.detected
    assert unmatched(app) == [(item_id, sale_id, "open")]


def test_the_ai_never_cancels_a_paid_win_still_to_name_by_itself(app, shop):
    item_id, sale_id = _to_name(app, shop)
    suggestion = _ai_cancel_suggestion(app, sale_id)
    with app.app_context():
        with pytest.raises(pipeline.ReviewError):
            supervisor.apply_proposal(
                db.session.get(ReviewItem, suggestion), actor=supervisor.AI_ACTOR
            )
        db.session.rollback()
    assert the_sale(app)[1] is SaleStatus.detected
    assert unmatched(app) == [(item_id, sale_id, "open")]


# ---------- I6: parcels wait while another show is on air ----------


def _shipments_queued(app, whatnot_show_id):
    with app.app_context():
        return [
            j
            for j in db.session.scalars(select(Job).where(Job.kind == "whatnot_sync_shipments"))
            if j.payload.get("whatnot_show_id") == whatnot_show_id
        ]


def test_an_ended_shows_parcels_wait_while_another_show_is_on_air_and_come_after(app, shop):
    read(app, shop, orders=False)  # the show ended five minutes ago; its orders are not read
    nxt = shop["fake"].add_show(
        title="Next show", status="live", start=datetime.now(UTC) - timedelta(minutes=20)
    )
    read(app, shop, orders=False)
    with app.app_context():
        assert sync.on_air_now(shop["org"]) is True
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        ws = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"]))
        assert ws.orders_synced_at is not None and ws.orders_synced_at >= ws.ended_at
        state = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == f"shipments:{ws.id}")
        )
        assert state is None or state.last_started_at is None  # not asked for, not spent
    assert _shipments_queued(app, shop["wshow"]) == []
    # The next show ends: the next pass asks for the parcels.
    shop["fake"].shows[nxt["id"]].update(
        status="ended", ended_at=datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    with app.app_context():
        sync.connection.sync_state(shop["org"], "shows").last_started_at = None
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        assert sync.on_air_now(shop["org"]) is False
    assert len(_shipments_queued(app, shop["wshow"])) == 1
