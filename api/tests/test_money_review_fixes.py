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
- A paid win on a Whatnot listing a person marked "Not one of ours", whose question a person
  ignored, no longer keeps the buyer's order open for good: the order is paid, goes through
  picking, packing and shipping (by hand or by the carrier), and the win itself stays as it
  was, holding no stock (S3). Only that case: a win still to name, one Whatnot has not said
  is paid, or one whose question is still open keeps the order open, and linking the listing
  to a product asks the question again.
- Such a settled win, after S3 (G1): deleting the laptop data keeps the person's "Ignore"
  of a win that stays (N1); cancelled or declined on Whatnot once its order is being picked,
  it opens the "take it out of the package" note as a named item does (N2); in an order that
  already shipped, linking its listing neither asks again nor names it (N3); its buyer's slip
  is ready to print; and its money counts in "Paid revenue" at what Whatnot charged.

Every buyer name here is made up; Whatnot is the in-memory fake.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import (
    Buyer,
    BuyerName,
    InventoryMovement,
    Job,
    Order,
    OrderStatus,
    Organization,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotOrderItem,
    WhatnotShow,
    WhatnotSyncState,
    WhatnotVariant,
)
from app.services import (
    dashboard,
    order_slips,
    pipeline,
    recordings,
    reports,
    show_merge,
    supervisor,
)
from app.services import orders as orders_svc
from app.services.inventory import Actor
from app.services.whatnot import client_for, shipping, sync
from app.services.whatnot import orders as whatnot_orders
from tests import test_whatnot_catalog as cat
from tests import test_whatnot_orders as wo
from tests.test_autopay import open_reasons, sale_states
from tests.test_autopay import run as run_world
from tests.test_paid_wins_to_pick import CHOOSE, tiles, unmatched
from tests.test_pipeline import _owner, level, run
from tests.test_recordings import quiet
from tests.test_show_merge import _laptop, _live, _made, _order, _quiet, _read, _shows_of, _ws
from tests.test_whatnot_orders import apply, order, read, set_mode, win
from tests.test_whatnot_reads import nameless_order
from tests.test_whatnot_shipping import parcel, read_shipments

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


# ---------- S3: a paid win on a listing "Not one of ours", its question ignored ----------

ZED = "made_up_zed"


def _off_listing(app, client, shop, sign_in, title="Red bundle", sku="RED"):
    """A Whatnot listing a person marked "Not one of ours": (headers, listing id, variant)."""
    product = shop["fake"].add_product(title=title, variants=[{"sku": sku} if sku else {}])
    cat.sync(app, shop)
    variant = shop["fake"].variants_of(product["id"])[0]
    with app.app_context():
        listing = db.session.scalar(
            select(WhatnotVariant.id).where(WhatnotVariant.whatnot_id == variant["id"])
        )
    h = sign_in(shop["org"])
    r = client.patch(f"/api/whatnot/catalog/{listing}/link", json={"skuId": None}, headers=h)
    assert r.status_code == 200
    return h, listing, variant


def _ignore(client, h, item_id):
    return client.post(f"/api/review/{item_id}/resolve", json={"action": "dismiss"}, headers=h)


def _order_status(app, buyer=ZED):
    with app.app_context():
        return db.session.scalar(select(Order.status).where(Order.buyer_username == buyer)).value


def _sales(app, buyer=ZED):
    with app.app_context():
        return [
            (s.item_text, s.status.value, s.sku_id)
            for s in db.session.scalars(
                select(Sale).where(Sale.buyer_username == buyer).order_by(Sale.id)
            )
        ]


def _movements(app):
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(InventoryMovement))


def _advance(app, step, buyer=ZED):
    with app.app_context():
        o = db.session.scalar(select(Order).where(Order.buyer_username == buyer))
        orders_svc.advance(o, step, actor_label="t")
        db.session.commit()


MONEY = ("revenuePaidCents", "toPick", "revenueToPickCents", "revenueSoldCents")


def test_s3_a_mixed_order_is_paid_once_the_off_listing_wins_question_is_ignored_and_ships(
    app, client, shop, sign_in
):
    """One named paid item and one paid win on a listing "Not one of ours". Once a person
    ignores the win's question the buyer's order is paid and goes through picking, packing
    and shipping. The win stays as it was (no product, nothing held, never shipped as one of
    the shop's), the named item ships, and no stock moves for the win."""
    h, _, variant = _off_listing(app, client, shop, sign_in)
    order(shop, ZED, "Red bundle", 2500)  # its title names RED: paid at once
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    [(item_id, off_sale, _)] = unmatched(app)
    assert _order_status(app) == "open"
    stock, moves = level(app, shop["red"]), _movements(app)
    money = {k: tiles(app, shop)[k] for k in MONEY}
    assert _ignore(client, h, item_id).status_code == 200
    assert _order_status(app) == "paid"
    apply(app, shop)  # reading Whatnot again changes nothing
    assert _order_status(app) == "paid"
    assert unmatched(app) == [(item_id, off_sale, "dismissed")]
    # The Orders page says it in words, and offers the next step.
    rows = client.get("/api/orders", headers=h).get_json()["orders"]
    [row] = [o for o in rows if o["buyer"] == ZED]
    assert (row["status"], row["verified"], row["toPick"]) == ("paid", True, 0)
    assert {s["item"]: s["notOurs"] for s in row["sales"]} == {
        "Red bundle": False,
        "Mystery thing": True,
    }
    for step in (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped):
        _advance(app, step)
    assert _order_status(app) == "shipped"
    assert _sales(app) == [
        ("Red bundle", "fulfilled", shop["red"]),
        ("Mystery thing", "detected", None),
    ]
    assert (level(app, shop["red"]), _movements(app)) == (stock, moves)
    # The owner's call (2026-11-19): once settled, its money counts as paid (at what Whatnot
    # charged), and it is still no win to pick.
    settled = {**money, "revenuePaidCents": money["revenuePaidCents"] + 1500}
    settled["revenueSoldCents"] = money["revenueSoldCents"] + 1500
    assert {k: tiles(app, shop)[k] for k in MONEY} == settled


def test_s3_an_order_of_only_the_off_listing_win_is_paid_once_its_question_is_ignored(
    app, client, shop, sign_in
):
    h, _, variant = _off_listing(app, client, shop, sign_in)
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    [(item_id, _, _)] = unmatched(app)
    moves = _movements(app)
    assert _ignore(client, h, item_id).status_code == 200
    assert _order_status(app) == "paid"  # the buyer paid and has a parcel to pack
    _advance(app, OrderStatus.picking)
    assert _order_status(app) == "picking"
    assert _sales(app) == [("Mystery thing", "detected", None)]
    assert _movements(app) == moves
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        assert orders_svc.pick_list(show) == []
        assert orders_svc.to_pick(show) == []
        # The order slips still print the buyer's lot (read-only: nothing moves).
        [slip] = order_slips.build(show)["slips"]
        assert (slip["username"], slip["orderStatus"], slip["items"]) == (ZED, "picking", 1)
    assert _movements(app) == moves


def test_s3_ignoring_a_win_with_no_paid_whatnot_order_leaves_the_order_open(
    app, client, shop, sign_in
):
    """A win read from chat alone (no Whatnot order), and one whose Whatnot order is not paid:
    "Ignore" is allowed for both, as before, and neither makes the buyer's order paid."""
    h, _, variant = _off_listing(app, client, shop, sign_in)
    shop["say"](app, [win(ZED, "Mystery thing", "15.00")])
    run(app, shop["show"])
    cat.order_for(shop, ANN, variant, title="Mystery thing", price=1500, status="pending")
    read(app, shop)
    apply(app, shop)
    items = unmatched(app)
    assert len(items) == 2
    for item_id, _, _ in items:
        assert _ignore(client, h, item_id).status_code == 200
    apply(app, shop)
    assert (_order_status(app, ZED), _order_status(app, ANN)) == ("open", "open")
    with app.app_context():
        for buyer in (ZED, ANN):
            o = db.session.scalar(select(Order).where(Order.buyer_username == buyer))
            with pytest.raises(orders_svc.OrderError):
                orders_svc.advance(o, OrderStatus.picking, actor_label="t")
        db.session.rollback()


def test_s3_once_whatnot_says_an_ignored_off_listing_win_is_paid_the_apply_pays_the_order(
    app, client, shop, sign_in
):
    """The question was ignored while Whatnot's order was still pending; Whatnot then takes
    the payment. Reading and applying its orders is what makes the buyer's order paid."""
    h, _, variant = _off_listing(app, client, shop, sign_in)
    o = cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500, status="pending")
    read(app, shop)
    apply(app, shop)
    [(item_id, _, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    assert _order_status(app) == "open"
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    read(app, shop)
    apply(app, shop)
    assert _order_status(app) == "paid"
    assert _sales(app) == [("Mystery thing", "detected", None)]


def test_s3_a_winners_choice_title_on_an_off_listing_must_still_be_named(
    app, client, shop, sign_in
):
    title = CHOOSE.format(61)
    h, _, variant = _off_listing(app, client, shop, sign_in, title=title, sku=None)
    cat.order_for(shop, ZED, variant, title=title, price=1100)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    r = _ignore(client, h, item_id)
    assert r.status_code == 409
    assert unmatched(app) == [(item_id, sale_id, "open")]
    assert _order_status(app) == "open"


def test_s3_linking_the_listing_to_a_product_asks_again_and_reopens_the_order(
    app, client, shop, sign_in
):
    """The listing turns out to be one of ours after all: the question is asked again at
    once and the order waits for it; the next orders read names the product by the listing,
    and the order is paid again, with the stock moved for it."""
    h, listing, variant = _off_listing(app, client, shop, sign_in)
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    assert _order_status(app) == "paid"
    r = client.patch(f"/api/whatnot/catalog/{listing}/link", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 200
    asked = unmatched(app, sale_id)
    assert [st for _, _, st in asked] == ["dismissed", "open"]
    assert _order_status(app) == "open"
    on_shelf = level(app, shop["red"])[0]
    apply(app, shop)
    assert _sales(app) == [("Mystery thing", "paid", shop["red"])]
    assert _order_status(app) == "paid"
    assert level(app, shop["red"])[0] == on_shelf - 1


def test_s3_linking_the_listing_while_the_order_is_picked_keeps_the_step_and_holds_the_next(
    app, client, shop, sign_in
):
    """Already being picked when the listing is linked: the order stays where it is (nothing
    takes an order back out of fulfilment), the question is open again, and packing waits."""
    h, listing, variant = _off_listing(app, client, shop, sign_in)
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    _advance(app, OrderStatus.picking)
    r = client.patch(f"/api/whatnot/catalog/{listing}/link", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 200
    assert [st for _, _, st in unmatched(app, sale_id)] == ["dismissed", "open"]
    assert _order_status(app) == "picking"
    with pytest.raises(orders_svc.OrderError):
        _advance(app, OrderStatus.packed)


def test_s3_a_question_still_open_keeps_the_order_open(app, client, shop, sign_in):
    _, _, variant = _off_listing(app, client, shop, sign_in)
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    [(_, _, state)] = unmatched(app)
    assert state == "open"
    assert _order_status(app) == "open"
    with pytest.raises(orders_svc.OrderError):
        _advance(app, OrderStatus.picking)


def test_s3_the_carrier_ships_an_order_whose_off_listing_win_was_ignored(
    app, client, shop, sign_in
):
    """The parcel left before anyone pressed a button: the shipments pass ships the order
    (it used to stay open for good, so the carrier rescue never touched it)."""
    set_mode(app, shop, "auto")
    h, _, variant = _off_listing(app, client, shop, sign_in)
    o = cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    shop["fake"].add_shipment(
        recipient=ZED,
        order_ids=[o["id"]],
        status="dropped_off",
        tracking="9400MADEUPS3",
        created=shop["now"] - timedelta(minutes=50),
    )
    read(app, shop)
    apply(app, shop)
    [(item_id, _, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    moves = _movements(app)
    with app.app_context():
        shipping.sync_shipments_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
        db.session.commit()
    assert _order_status(app) == "shipped"
    assert _sales(app) == [("Mystery thing", "detected", None)]
    assert _movements(app) == moves


def test_s3_an_ai_dismiss_applied_by_a_person_pays_the_order_too(app, client, shop, sign_in):
    _, _, variant = _off_listing(app, client, shop, sign_in)
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    [(item_id, _, _)] = unmatched(app)
    with app.app_context():
        item = db.session.get(ReviewItem, item_id)
        item.proposal = {"action": "dismiss", "confidence": 0.99}
        supervisor.apply_proposal(item, actor=Actor("user", None, "M"))
        db.session.commit()
    assert _order_status(app) == "paid"


def test_s3_a_chat_win_ignored_before_its_whatnot_order_arrived_is_settled_by_one_apply(
    app, client, shop, sign_in
):
    """The win was read from chat and its question ignored (allowed: no Whatnot order yet).
    Its paid order on the "Not one of ours" listing then arrives: one read and one apply make
    the buyer's order paid, not only a second one."""
    h, _, variant = _off_listing(app, client, shop, sign_in)
    shop["say"](app, [win(ZED, "Mystery thing", "15.00")])
    run(app, shop["show"])
    [(item_id, _, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    assert _order_status(app) == "open"
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    assert _order_status(app) == "paid"
    assert _sales(app) == [("Mystery thing", "detected", None)]


def test_s3_marking_the_listing_not_ours_after_its_question_was_ignored_pays_the_order(
    app, client, shop, sign_in
):
    """Ignored while Whatnot's order was still pending (allowed), then paid on Whatnot, and
    only then is the listing marked "Not one of ours": that decision itself pays the order."""
    product = shop["fake"].add_product(title="Mystery thing", variants=[{}])
    cat.sync(app, shop)
    variant = shop["fake"].variants_of(product["id"])[0]
    with app.app_context():
        listing = db.session.scalar(
            select(WhatnotVariant.id).where(WhatnotVariant.whatnot_id == variant["id"])
        )
    o = shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer=ZED,
        items=[{"title": "Mystery thing", "price_cents": 1500, "variant_id": variant["id"]}],
        created=datetime.now(UTC) - timedelta(seconds=20),
        status="pending",
    )
    read(app, shop)
    apply(app, shop)
    [(item_id, _, _)] = unmatched(app)
    h = sign_in(shop["org"])
    assert _ignore(client, h, item_id).status_code == 200
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    read(app, shop)  # Whatnot's copy says paid; not applied
    assert _order_status(app) == "open"
    r = client.patch(f"/api/whatnot/catalog/{listing}/link", json={"skuId": None}, headers=h)
    assert r.status_code == 200
    assert _order_status(app) == "paid"
    assert _sales(app) == [("Mystery thing", "detected", None)]


# ---------- G1: follow-ups to S3 (a settled win: paid on Whatnot, "Not one of ours") ----------


def _settled_win(app, client, shop, sign_in, *, named=False, price=1500, **kw):
    """ZED's paid win on a listing "Not one of ours", its question ignored, so the order is
    paid; with `named`, also a paid RED of theirs in another Whatnot order. Returns
    (headers, listing id, the off listing's Whatnot order, sale id, review item id)."""
    h, listing, variant = _off_listing(app, client, shop, sign_in)
    if named:
        order(shop, ZED, "Red bundle", 2500)
    off = cat.order_for(shop, ZED, variant, title="Mystery thing", price=price, **kw)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    assert _order_status(app) == "paid"
    return h, listing, off, sale_id, item_id


def _settled(app, sale_id) -> bool:
    with app.app_context():
        return sale_id in orders_svc.settled_unnamed_ids([db.session.get(Sale, sale_id)])


def _notes(app, reason="closed_after_picking"):
    with app.app_context():
        return [
            (n.sale_id, n.summary)
            for n in db.session.scalars(
                select(ReviewItem)
                .where(ReviewItem.reason == reason, ReviewItem.status == ReviewStatus.open)
                .order_by(ReviewItem.id)
            )
        ]


def test_g1_n1_deleting_the_laptop_data_keeps_the_ignore_of_a_win_that_stays(
    app, client, shop, sign_in
):
    """The win was read from chat, its question ignored, then its paid Whatnot order on the
    "Not one of ours" listing claimed it (S3: the order is paid). Deleting the laptop's data
    keeps that sale (Whatnot's) and must keep the person's decision with it, detached from the
    chat line that is gone: otherwise the order went back to open with nothing in Review."""
    h, _, variant = _off_listing(app, client, shop, sign_in)
    shop["say"](app, [win(ZED, "Mystery thing", "15.00")])
    run(app, shop["show"])
    [(item_id, sale_id, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    assert _order_status(app) == "paid"
    quiet(app, shop["show"])
    with app.app_context():
        before = recordings.preview(db.session.get(Show, shop["show"]))
        db.session.rollback()
    with app.app_context():
        out = recordings.delete_laptop_data(shop["org"], shop["show"], user=_owner(shop["org"]))
        db.session.commit()
    assert out["kept"] is True and out["reviews"] == before["reviews"]
    with app.app_context():
        item = db.session.get(ReviewItem, item_id)
        assert item is not None, "the person's Ignore was deleted with the chat line"
        assert (item.status, item.sale_id, item.event_id, item.message_id) == (
            ReviewStatus.dismissed,
            sale_id,
            None,
            None,
        )
    assert _settled(app, sale_id)
    apply(app, shop)  # derives the order again
    assert _order_status(app) == "paid"
    assert unmatched(app) == [(item_id, sale_id, "dismissed")]
    _advance(app, OrderStatus.picking)
    assert _order_status(app) == "picking"


@pytest.mark.parametrize(
    ("whatnot_status", "closed"), [("cancelled", "cancelled"), ("failed", "payment_failed")]
)
def test_g1_n2_a_settled_win_closed_on_whatnot_while_picked_opens_the_take_it_out_note(
    app, client, shop, sign_in, whatnot_status, closed
):
    """The order (a RED and the settled win) is being picked when Whatnot cancels, or fails,
    the settled win's order. The packer may have put it in the box: the "take it out of the
    package" note opens, as for a named item, and the reprinted slip says so. Nothing moves
    in stock (the win held none), and the note does not ask to count anything back."""
    _, _, off, sale_id, _ = _settled_win(app, client, shop, sign_in, named=True)
    _advance(app, OrderStatus.picking)
    moves = _movements(app)
    shop["fake"].set_order_status(off["id"], whatnot_status)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        assert db.session.get(Sale, sale_id).status.value == closed
    notes = _notes(app)
    assert [n for n, _ in notes] == [sale_id], "no take-it-out note for the settled win"
    words = notes[0][1]
    assert ZED in words and "Mystery thing" in words and "being picked" in words
    assert "Take it out of the package" in words and "count it back" not in words
    assert _order_status(app) == "picking"
    assert _movements(app) == moves
    with app.app_context():
        [slip] = order_slips.build(db.session.get(Show, shop["show"]))["slips"]
        closed_rows = [r["product"] for r in slip["rows"] if r.get("closed")]
        db.session.rollback()
    assert len(closed_rows) == 1 and "take it out" in closed_rows[0]
    _advance(app, OrderStatus.packed)  # the RED still goes out, as for a named item
    assert _order_status(app) == "packed"


def test_g1_n2_a_settled_win_that_joined_after_picking_began_opens_no_note(
    app, client, shop, sign_in
):
    """Only a win that was on the order when picking began may be in the box: one recorded
    after it (its order still open on the app's side, the question still open) never was."""
    h, _, variant = _off_listing(app, client, shop, sign_in)
    order(shop, ZED, "Red bundle", 2500)
    read(app, shop)
    apply(app, shop)
    _advance(app, OrderStatus.picking)
    late = cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
    read(app, shop)
    apply(app, shop)
    [(item_id, _, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    shop["fake"].set_order_status(late["id"], "cancelled")
    read(app, shop)
    apply(app, shop)
    assert _notes(app) == []


@pytest.mark.parametrize("kind", ["named", "settled"])
def test_g1_n2_an_order_whose_only_item_is_closed_while_picked_waits_like_a_named_one(
    app, client, shop, sign_in, kind
):
    """The order's only item is cancelled on Whatnot while it is being picked. For a named
    item that is (today) a "take it out" note in Review and an order that stays where it is:
    neither packing nor going back a step is allowed. A settled win now does exactly the
    same: before, the order stayed in picking with nothing at all in Review."""
    if kind == "named":
        off = order(shop, ZED, "Red bundle", 2500)
        read(app, shop)
        apply(app, shop)
    else:
        _, _, off, _, _ = _settled_win(app, client, shop, sign_in)
    _advance(app, OrderStatus.picking)
    shop["fake"].set_order_status(off["id"], "cancelled")
    read(app, shop)
    apply(app, shop)
    assert _order_status(app) == "picking"
    assert open_reasons(app) == ["closed_after_picking"]
    for step in (OrderStatus.packed, OrderStatus.paid):
        with pytest.raises(orders_svc.OrderError):
            _advance(app, step)
    assert _order_status(app) == "picking"


def test_g1_n3_linking_the_listing_after_the_order_shipped_neither_asks_nor_names(
    app, client, shop, sign_in
):
    """The parcel left with the settled win in it; the listing is linked to a product only
    then. The win is not asked about again, the orders reads never name it from the listing,
    no stock moves for a unit that already left, and it stays settled: its money stays in
    "Paid revenue" and it is no win to pick."""
    h, listing, _, sale_id, item_id = _settled_win(app, client, shop, sign_in)
    for step in (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped):
        _advance(app, step)
    moves, on_shelf = _movements(app), level(app, shop["red"])
    money = {k: tiles(app, shop)[k] for k in MONEY}
    r = client.patch(f"/api/whatnot/catalog/{listing}/link", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 200
    assert unmatched(app, sale_id) == [(item_id, sale_id, "dismissed")]
    for _ in range(2):
        read(app, shop)
        apply(app, shop)
    assert unmatched(app, sale_id) == [(item_id, sale_id, "dismissed")]
    assert _sales(app) == [("Mystery thing", "detected", None)]
    assert (_movements(app), level(app, shop["red"])) == (moves, on_shelf)
    assert _order_status(app) == "shipped"
    assert _settled(app, sale_id)
    assert {k: tiles(app, shop)[k] for k in MONEY} == money
    assert money["toPick"] == 0 and money["revenuePaidCents"] == 1500


def test_g1_n3_a_win_that_joined_an_order_after_it_shipped_is_still_asked(
    app, client, shop, sign_in
):
    """Not a settled win: one won after the parcel left (Review says so), whose question was
    ignored while only chat knew of it. Its paid Whatnot order is for no listing of ours
    (none linked, none "Not one of ours"): it must be named, so the question is asked again
    even though its order shipped. Only a win that left in the parcel is spared."""
    order(shop, ZED, "Red bundle", 2500)
    read(app, shop)
    apply(app, shop)
    for step in (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped):
        _advance(app, step)
    shop["say"](app, [win(ZED, "Mystery thing", "15.00")])
    run(app, shop["show"])
    [(item_id, sale_id, _)] = unmatched(app)
    h = sign_in(shop["org"])
    assert _ignore(client, h, item_id).status_code == 200
    order(shop, ZED, "Mystery thing", 1500)
    read(app, shop)
    for _ in range(2):  # the first apply ties the win to its order, the next one asks
        apply(app, shop)
    assert [st for _, _, st in unmatched(app, sale_id)] == ["dismissed", "open"]
    assert not _settled(app, sale_id)
    assert tiles(app, shop)["toPick"] == 1


@pytest.mark.parametrize("lots", [False, True])
def test_g1_slips_a_buyer_whose_win_is_settled_is_ready_to_print(app, client, shop, sign_in, lots):
    """The order is Ready to pick: its slip is no longer held back as "needs review" because
    of the settled win (with Whatnot's lots read or not). The win's row prints as before."""
    if lots:
        h, listing, variant = _off_listing(app, client, shop, sign_in)
        named = order(shop, ZED, "Red bundle", 2500)
        off = cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500)
        parcel(shop, ZED, [named, off])
        read(app, shop)
        apply(app, shop)
        read_shipments(app, shop)
        [(item_id, _, _)] = unmatched(app)
        assert _ignore(client, h, item_id).status_code == 200
        assert _order_status(app) == "paid"
    else:
        _settled_win(app, client, shop, sign_in, named=True)
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        every = order_slips.build(show)
        ready = order_slips.build(show, only="ready")
        db.session.rollback()
    [slip] = every["slips"]
    assert slip["needsReview"] is False
    assert slip["items"] == 2
    assert [s["username"] for s in ready["slips"]] == [ZED]
    assert ready["ready"] == 1 and ready["leftOut"] == 0
    [win_row] = [r for r in slip["rows"] if not r["matched"]]
    assert win_row["product"] in ("Mystery thing", order_slips.NO_PICK)


def test_g1_revenue_a_settled_wins_money_counts_as_paid_at_what_whatnot_charged(
    app, client, shop, sign_in
):
    """Won in chat at $15.00; Whatnot charged $14.00 (a $1 discount on its order). Once the
    win is settled its money counts in "Paid revenue" at $14.00: the tiles, the Revenue sheet,
    the Buyers page figures and the top buyers. It is still no win to pick, and holds no
    stock. A name that passed to another account later (`held_since`) keeps it out."""
    h, _, variant = _off_listing(app, client, shop, sign_in)
    shop["say"](app, [win(ZED, "Mystery thing", "15.00")])
    run(app, shop["show"])
    [(item_id, sale_id, _)] = unmatched(app)
    assert _ignore(client, h, item_id).status_code == 200
    moves = _movements(app)
    t = tiles(app, shop)
    assert (t["revenuePaidCents"], t["revenueSoldCents"]) == (0, 0)  # not paid on Whatnot yet
    cat.order_for(shop, ZED, variant, title="Mystery thing", price=1500, discount_cents=100)
    read(app, shop)
    apply(app, shop)
    assert _order_status(app) == "paid"
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        line = db.session.scalar(
            select(WhatnotOrderItem).where(WhatnotOrderItem.sale_id == sale_id)
        )
        assert (sale.price_cents, line.unit_price_cents) == (1500, 1400)  # the test's premise
    t = tiles(app, shop)
    assert (t["revenuePaidCents"], t["revenueSoldCents"]) == (1400, 1400)
    assert (t["toPick"], t["revenueToPickCents"], t["revenuePendingCents"]) == (0, 0, 0)
    with app.app_context():
        org = shop["org"]
        assert dashboard.buyer_figures(org)[ZED]["paidCents"] == 1400
        [top] = [b for b in dashboard.top_buyers(org) if b["buyer"] == ZED]
        assert top["paidCents"] == 1400
        show = db.session.get(Show, shop["show"])
        assert ZED not in dashboard.buyer_figures(org, before=show.show_date)
        book = reports.build(db.session.get(Organization, org), show=show)
        # The name came to its holder from another Whatnot account on this show's date: the
        # win is the holder's ...
        person = Buyer(organization_id=org, username=ZED)
        db.session.add(person)
        db.session.flush()
        name = BuyerName(
            organization_id=org,
            buyer_id=person.id,
            name_lower=ZED,
            username=ZED,
            since_date=show.show_date,
        )
        db.session.add(name)
        db.session.flush()
        assert dashboard.buyer_figures(org)[ZED]["paidCents"] == 1400
        # ... from the next day on: the earlier account's, out of the holder's figures.
        name.since_date = show.show_date + timedelta(days=1)
        db.session.flush()
        figures = dashboard.buyer_figures(org)
        assert ZED not in figures or figures[ZED]["paidCents"] == 0
        db.session.rollback()
    from io import BytesIO

    from openpyxl import load_workbook

    revenue = list(load_workbook(BytesIO(book))["Revenue"].iter_rows(values_only=True))[1]
    assert revenue[4] == 14.0  # "Paid revenue", in dollars
    assert _movements(app) == moves


def test_g1_n2_a_settled_win_cancelled_on_whatnot_after_its_parcel_left_tells_a_person(
    app, client, shop, sign_in
):
    """The parcel already left with the settled win in it when Whatnot cancels its order: as
    for a shipped named item, a person is told (the note), the order stays shipped, and
    nothing moves in stock."""
    _, _, off, sale_id, _ = _settled_win(app, client, shop, sign_in)
    for step in (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped):
        _advance(app, step)
    moves = _movements(app)
    shop["fake"].set_order_status(off["id"], "cancelled")
    read(app, shop)
    apply(app, shop)
    notes = _notes(app)
    assert [n for n, _ in notes] == [sale_id], "nobody told of a cancelled item that shipped"
    assert "already shipped" in notes[0][1] and "count it back" not in notes[0][1]
    assert _order_status(app) == "shipped"
    assert _movements(app) == moves


def test_g1_n2_a_settled_win_declined_while_picked_then_paid_waits_again_without_the_note(
    app, client, shop, sign_in
):
    """Declined on Whatnot while its order is being picked (the take-it-out note opens), then
    paid after all: as when a named item is paid again, the note is settled. The win waits for
    its product again (its question open), and once a person ignores it again it is settled
    again, with nothing left open to hold it."""
    h, _, off, sale_id, _ = _settled_win(app, client, shop, sign_in, named=True)
    _advance(app, OrderStatus.picking)
    shop["fake"].set_order_status(off["id"], "failed")
    read(app, shop)
    apply(app, shop)
    assert [n for n, _ in _notes(app)] == [sale_id]
    shop["fake"].set_order_status(off["id"], "completed")
    read(app, shop)
    apply(app, shop)
    assert _notes(app) == [], "the take-it-out note outlived the payment"
    asked = unmatched(app, sale_id)
    assert asked[-1][2] == "open"
    assert _ignore(client, h, asked[-1][0]).status_code == 200
    assert _settled(app, sale_id)
