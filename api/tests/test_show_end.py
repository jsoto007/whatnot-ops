"""A show that never "ends" on Whatnot (A32): no end time, or a status left live or paused.

The rule (Javier's call, 2026-11-19): such a show is over 12 hours after it began
(`showend.ON_AIR_AT_MOST`), unless a show laptop saw its page move on to another channel first.
One function decides it (services/whatnot/showend.py) and every reader asks it. Whatnot's
own end time always wins and is never written over. Against the in-memory fake only;
every buyer and channel name here is made up.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db import db
from app.models import (
    ChatMessage,
    Job,
    ReviewItem,
    ReviewStatus,
    Sale,
    Show,
    WhatnotConnection,
    WhatnotOrder,
    WhatnotShow,
)
from app.services import picks, pipeline
from app.services.whatnot import (
    catalog,
    client_for,
    connection,
    results,
    shipping,
    showend,
    sync,
    watch,
)
from app.services.whatnot import orders as whatnot_orders
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import order
from tests.test_whatnot_reads import drain

shop = wo.shop  # the connected shop and its recording, matched to a show on Whatnot

HOUR = timedelta(hours=1)
MIN = timedelta(minutes=1)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def ws_of(shop=None) -> WhatnotShow:
    return db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"]))


def whatnot_says(app, shop, status, *, began, ended=None):
    """Whatnot has the shop's show as `status`, begun `began` ago, and gives an end time only
    when `ended` (ago) is set. The app's copy is read from it, then pinned to those times."""
    now = datetime.now(UTC)
    start, end = now - began, (now - ended) if ended is not None else None
    shop["fake"].shows[shop["wshow"]].update(
        status=status, scheduled_start_at=iso(start), ended_at=iso(end) if end else None
    )
    with app.app_context():
        sync.sync_shows(shop["org"], client_for(shop["org"]))
        ws = ws_of(shop)
        ws.status, ws.started_at, ws.ended_at = status, start, end
        db.session.commit()
    return start, end


def recorded(app, shop):
    """The recording is this Whatnot show's (a person matched it)."""
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        sync.tie(show, ws_of(shop).id, "manual")
        db.session.commit()


def moved_on(app, shop, ago, *, chat_ago=None):
    """The laptop told the server, `ago` ago, that the page moved on to another channel; the
    recording's chat last reached the server `chat_ago` ago (an hour before that unless said)."""
    now = datetime.now(UTC)
    with app.app_context():
        db.session.execute(
            update(Show).where(Show.id == shop["show"]).values(moved_on_at=now - ago)
        )
        db.session.execute(
            update(ChatMessage)
            .where(ChatMessage.show_id == shop["show"])
            .values(received_at=now - (chat_ago if chat_ago is not None else ago + HOUR))
        )
        db.session.commit()
    return now - ago


def parcels_read(app, shop, at=None):
    with app.app_context():
        ws = ws_of(shop)
        state = connection.sync_state(shop["org"], f"shipments:{ws.id}")
        state.last_started_at = state.last_ok_at = at or datetime.now(UTC)
        db.session.commit()


def queued(app, kind):
    with app.app_context():
        return [
            j.payload.get("whatnot_show_id")
            for j in db.session.scalars(select(Job).where(Job.kind == kind, Job.status == "queued"))
        ]


def timer_pass(app, shop):
    with app.app_context():
        for resource in ("shows", "open_orders", "analytics"):
            connection.sync_state(shop["org"], resource).last_started_at = None
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()


# ---------- the 12-hour rule ----------


def test_a_show_left_live_is_over_twelve_hours_after_it_began(app, shop):
    start, _ = whatnot_says(app, shop, "live", began=13 * HOUR)
    with app.app_context():
        ws = ws_of(shop)
        assert sync.on_air_now(shop["org"]) is False
        assert [w.whatnot_id for w in sync._unread_shows(shop["org"], [])] == [shop["wshow"]]
        ending = showend.ending(ws)
        assert (ending.over, ending.at, ending.by) == (True, start + showend.ON_AIR_AT_MOST, "time")
        assert ws.ended_at is None  # derived, never written over Whatnot's own


def test_a_show_live_for_less_than_twelve_hours_is_on_air_and_not_over(app, shop):
    whatnot_says(app, shop, "live", began=2 * HOUR)
    with app.app_context():
        assert sync.on_air_now(shop["org"]) is True
        assert sync._unread_shows(shop["org"], []) == []
        assert whatnot_orders._complete(ws_of(shop)) is False
        assert picks.closes_at(ws_of(shop)) is None


@pytest.mark.parametrize("status", ["ended", "stopped", "live"])
def test_a_show_whatnot_never_ended_completes_twelve_hours_after_it_began(app, shop, status):
    """Called ended with no end time, or left paused or live: until now such a show was never
    read in full by the timer, never complete, never owed its parcels or picks, and Whatnot's
    results were never asked for: no label was ever offered. Run through to the worker's jobs
    (the parcel read, the picks, the apply as orders arrive)."""
    start, _ = whatnot_says(app, shop, status, began=13 * HOUR)
    recorded(app, shop)
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    sold = order(shop, "made_up_buyer_1", "Red bundle", created=start + HOUR)
    timer_pass(app, shop)
    end = start + showend.ON_AIR_AT_MOST
    with app.app_context():
        ws = ws_of(shop)
        assert ws.status == status and ws.ended_at is None  # Whatnot's word, untouched
        assert sync.on_air_now(shop["org"]) is False
        assert ws.orders_synced_at is not None and ws.orders_synced_at >= end  # read in full
        assert whatnot_orders._complete(ws) is True
        assert shipping._complete(shop["org"], ws) is False  # its parcels are not read yet
        assert shop["wshow"] in results.results_due(shop["org"])
        assert picks.closes_at(ws) == end + picks.CLOSE_AFTER
        assert watch.show_read(ws)["final"] is True
    assert shop["wshow"] in queued(app, "whatnot_sync_shipments")  # owed its parcels
    assert queued(app, "pick_show") == [shop["wshow"]]
    assert queued(app, "whatnot_apply_show") == [shop["wshow"]]
    with app.app_context():  # the few seconds the jobs were queued to wait pass
        db.session.execute(
            update(Job).where(Job.status == "queued").values(run_at=datetime.now(UTC))
        )
        db.session.commit()
    drain(app)  # what the worker runs: the parcel read, the picks, the apply
    with app.app_context():
        assert shipping._complete(shop["org"], ws_of(shop)) is True
        view = shipping.packages(db.session.get(Show, shop["show"]))
        assert view["show"]["ended"] is True and view["show"]["complete"] is True
        db.session.rollback()
        # Applied as it arrived: the show was on air after the shop chose "auto" (not history).
        assert db.session.scalar(select(Sale).where(Sale.external_order_id == sold["id"]))


def test_an_ended_show_with_no_end_time_waits_for_the_rule_but_reads_as_ended(app, shop):
    whatnot_says(app, shop, "ended", began=2 * HOUR)
    recorded(app, shop)
    timer_pass(app, shop)
    with app.app_context():
        ws = ws_of(shop)
        assert sync.on_air_now(shop["org"]) is False  # Whatnot says it ended: nothing on air
        assert sync._unread_shows(shop["org"], []) == []  # not over by the rule yet
        assert whatnot_orders._complete(ws) is False
        assert watch.show_read(ws) is None  # nothing to judge yet
        view = shipping.packages(db.session.get(Show, shop["show"]))
        # The Shipping page says "ended, not read in full", never "not ended".
        assert view["show"]["ended"] is True and view["show"]["complete"] is False
        db.session.rollback()
    assert shop["wshow"] not in queued(app, "whatnot_sync_shipments")


def test_whatnots_own_end_time_wins_and_a_show_it_ended_is_unchanged(app, shop):
    start, end = whatnot_says(app, shop, "ended", began=3 * HOUR, ended=30 * MIN)
    with app.app_context():
        ending = showend.ending(ws_of(shop))
        assert (ending.over, ending.at, ending.by) == (True, end, "whatnot")


def test_whatnots_end_time_arriving_after_the_rule_ended_it_reads_nothing_twice(app, shop):
    start, _ = whatnot_says(app, shop, "ended", began=13 * HOUR)
    recorded(app, shop)
    timer_pass(app, shop)
    parcels_read(app, shop)
    with app.app_context():
        db.session.execute(update(Job).values(status="done"))  # what the first pass queued ran
        db.session.commit()
    # Whatnot now gives its own end time: earlier than the rule's (the show ran three hours).
    whatnot_says(app, shop, "ended", began=13 * HOUR, ended=10 * HOUR)
    timer_pass(app, shop)
    assert shop["wshow"] not in queued(app, "whatnot_sync_shipments")  # (the shop-wide read may)
    assert queued(app, "whatnot_sync_show") == []
    with app.app_context():
        assert sync._unread_shows(shop["org"], []) == []
        assert sync._parcels_owed(shop["org"], datetime.now(UTC)) == []
        assert shipping._complete(shop["org"], ws_of(shop)) is True


def test_whatnots_end_time_later_than_the_rules_is_read_once_more(app, shop):
    began = 12 * HOUR + 30 * MIN
    whatnot_says(app, shop, "ended", began=began)  # over by the rule 30 minutes ago
    with app.app_context():
        ws_of(shop).orders_synced_at = datetime.now(UTC) - 20 * MIN  # after the rule's end
        db.session.commit()
        assert sync._unread_shows(shop["org"], []) == []
    # The show really ran 12 h 20 min: Whatnot's end is after that read.
    whatnot_says(app, shop, "ended", began=began, ended=10 * MIN)
    with app.app_context():
        assert [w.whatnot_id for w in sync._unread_shows(shop["org"], [])] == [shop["wshow"]]
        ws_of(shop).orders_synced_at = datetime.now(UTC)
        db.session.commit()
        assert sync._unread_shows(shop["org"], []) == []


def test_a_show_left_paused_for_days_holds_nothing_back(app, shop):
    whatnot_says(app, shop, "stopped", began=timedelta(days=3))
    with app.app_context():
        assert sync.on_air_now(shop["org"]) is False
        from app.services.whatnot import backfill

        assert backfill.on_air(shop["org"]) is False
        assert catalog.due(shop["org"]) is True


def test_a_recording_today_is_not_tied_by_the_clock_to_a_show_left_paused_for_days(app, shop):
    """The paused show's window used to run until now, so it overlapped today's recording
    more than today's own show did."""
    old = shop["fake"].add_show(
        title="Left paused", status="stopped", start=datetime.now(UTC) - timedelta(days=3)
    )
    with app.app_context():
        sync.sync_shows(shop["org"], client_for(shop["org"]))
        db.session.commit()
        show = db.session.get(Show, shop["show"])
        stuck = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == old["id"]))
        assert stuck.status == "stopped"
        assert show.whatnot_show_id == ws_of(shop).id


def test_history_judges_a_show_the_rule_ended_by_that_end():
    """A show left live from before the shop chose "Apply as they arrive" is history (a
    person applies it); one on air when the choice was made is followed. Ended shows are
    judged exactly as before (tests/test_whatnot_orders.py)."""
    chose = datetime(2026, 11, 12, 6, 0, tzinfo=UTC)
    conn = WhatnotConnection(connected_at=chose - timedelta(days=7), auto_since=chose)
    left = WhatnotShow(status="live", started_at=chose - 30 * HOUR)
    on_air_then = WhatnotShow(status="live", started_at=chose - HOUR)
    assert sync.history(conn, left) is True
    assert sync.history(conn, on_air_then) is False
    assert sync.history(conn, WhatnotShow(status="live")) is False  # no time at all: on air


# ---------- the laptop saw the page move on ----------


def test_an_ended_show_without_an_end_time_ends_when_the_laptop_saw_it_move_on(app, shop):
    whatnot_says(app, shop, "ended", began=2 * HOUR)
    recorded(app, shop)
    seen = moved_on(app, shop, 5 * MIN)
    with app.app_context():
        ending = showend.ending(ws_of(shop))
        assert (ending.over, ending.at, ending.by) == (True, seen, "laptop")
        assert [w.whatnot_id for w in sync._unread_shows(shop["org"], [])] == [shop["wshow"]]


def test_a_late_order_after_the_move_does_not_undo_a_show_whatnot_ended(app, shop):
    """A card declined at the win and retried is a new order placed after the show ended."""
    whatnot_says(app, shop, "ended", began=2 * HOUR)
    recorded(app, shop)
    seen = moved_on(app, shop, 50 * MIN)
    order(shop, "made_up_buyer_2", "Red bundle", created=seen + 20 * MIN)
    with app.app_context():
        sync.sync_show_orders(shop["org"], client_for(shop["org"]), ws_of(shop))
        db.session.commit()
        assert showend.end_of(ws_of(shop)) == seen


def test_a_live_show_the_laptop_left_is_over_once_quiet_and_no_order_came_after(app, shop):
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    seen = moved_on(app, shop, 40 * MIN)
    with app.app_context():  # quiet, but nothing read since: no proof yet
        assert showend.end_of(ws_of(shop)) is None
        assert sync.on_air_now(shop["org"]) is True
    wo.read(app, shop)  # every order read after the quiet period: none after the move
    with app.app_context():
        assert showend.end_of(ws_of(shop)) == seen
        assert sync.on_air_now(shop["org"]) is False


def test_a_live_show_the_laptop_left_lately_is_still_on_air(app, shop):
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    moved_on(app, shop, 10 * MIN)  # Whatnot has not had time to show an order after it
    with app.app_context():
        assert sync.on_air_now(shop["org"]) is True


def test_a_live_show_still_selling_after_the_laptop_left_is_not_over(app, shop):
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    seen = moved_on(app, shop, 40 * MIN)
    order(shop, "made_up_buyer_3", "Red bundle", created=seen + 15 * MIN)
    with app.app_context():
        sync.sync_show_orders(shop["org"], client_for(shop["org"]), ws_of(shop))
        db.session.commit()
        assert showend.is_over(ws_of(shop)) is False
        assert sync.on_air_now(shop["org"]) is True


def test_the_last_sales_order_stamped_just_after_the_move_does_not_undo_it(app, shop):
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    seen = moved_on(app, shop, 40 * MIN)
    order(shop, "made_up_buyer_4", "Red bundle", created=seen + 20 * timedelta(seconds=1))
    with app.app_context():
        sync.sync_show_orders(shop["org"], client_for(shop["org"]), ws_of(shop))
        db.session.commit()
        assert showend.end_of(ws_of(shop)) == seen


@pytest.mark.parametrize("status", ["ended", "live"])
def test_chat_from_the_show_after_the_move_means_the_laptop_came_back(app, shop, status):
    start, _ = whatnot_says(app, shop, status, began=2 * HOUR)
    recorded(app, shop)
    moved_on(app, shop, 40 * MIN, chat_ago=20 * MIN)
    with app.app_context():
        ending = showend.ending(ws_of(shop))
        assert ending.by != "laptop"
        assert ending.at in (None, start + showend.ON_AIR_AT_MOST)


def test_the_laptop_can_only_make_a_show_over_sooner_than_the_rule(app, shop):
    start, _ = whatnot_says(app, shop, "ended", began=13 * HOUR)
    recorded(app, shop)
    moved_on(app, shop, 10 * MIN)  # after the rule's end: the rule stands
    with app.app_context():
        ending = showend.ending(ws_of(shop))
        assert (ending.at, ending.by) == (start + showend.ON_AIR_AT_MOST, "time")


def test_a_move_seen_before_the_show_began_says_nothing_about_it(app, shop):
    start, _ = whatnot_says(app, shop, "ended", began=2 * HOUR)
    recorded(app, shop)
    moved_on(app, shop, 3 * HOUR)
    with app.app_context():
        assert showend.ending(ws_of(shop)).by == "time"  # the 12 hours, still to come
        assert showend.end_of(ws_of(shop)) is None


def test_whatnots_own_end_time_wins_over_the_laptop(app, shop):
    start, end = whatnot_says(app, shop, "ended", began=3 * HOUR, ended=30 * MIN)
    recorded(app, shop)
    moved_on(app, shop, 2 * HOUR)
    with app.app_context():
        assert showend.end_of(ws_of(shop)) == end


# ---------- the laptop's word needs proof that nothing sold after it ----------


def last_read(app, shop, ago):
    """Every order of the show was last read in full `ago` ago."""
    with app.app_context():
        ws_of(shop).orders_synced_at = datetime.now(UTC) - ago
        db.session.commit()


def hooks_arriving(app, shop):
    """Whatnot's webhooks are arriving (the last one a minute ago): the timer does not poll a
    live show's orders for want of them."""
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).last_event_at = datetime.now(UTC) - MIN
        db.session.commit()


def repair_done(app, shop):
    """The shop's one-time repair read (`sync.begin_repair`) was made long ago."""
    with app.app_context():
        state = connection.sync_state(shop["org"], "repair:capped-reads")
        state.last_ok_at = datetime.now(UTC) - timedelta(days=2)
        db.session.commit()


def auto(app, shop):
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()


def due_now(app):
    """The few seconds the timer's jobs were queued to wait pass."""
    with app.app_context():
        db.session.execute(
            update(Job).where(Job.status == "queued").values(run_at=datetime.now(UTC))
        )
        db.session.commit()


def jobs_done(app):
    with app.app_context():
        db.session.execute(update(Job).values(status="done"))
        db.session.commit()


def open_items(app, reason):
    with app.app_context():
        return [
            (r.sale.buyer_username if r.sale else None)
            for r in db.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.reason == reason, ReviewItem.status == ReviewStatus.open
                )
            )
        ]


def test_a_quiet_half_hour_is_no_proof_until_the_orders_are_read_after_it(app, shop):
    """A live show the laptop left 31 minutes ago, its orders last read before the move: the
    app cannot know nothing sold since (a show that keeps selling is on air). The timer's
    read of every order after the quiet half hour, with none placed after the move, ends it."""
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    order(shop, "made_up_buyer_6", "Red bundle", created=datetime.now(UTC) - 60 * MIN)
    wo.read(app, shop)
    last_read(app, shop, 35 * MIN)  # before the move
    seen = moved_on(app, shop, 31 * MIN)
    with app.app_context():
        ws = ws_of(shop)
        assert showend.is_over(ws) is False
        assert showend.end_of(ws) is None
        assert sync.on_air_now(shop["org"]) is True
        assert shipping._complete(shop["org"], ws) is False
        assert picks.closes_at(ws) is None
    timer_pass(app, shop)  # quiet: it reads the live show's orders
    with app.app_context():
        ws = ws_of(shop)
        assert ws.orders_synced_at >= seen + showend.MOVED_ON_QUIET
        ending = showend.ending(ws)
        assert (ending.over, ending.at, ending.by) == (True, seen, "laptop")
        assert sync.on_air_now(shop["org"]) is False


def test_the_proof_read_is_made_even_while_webhooks_arrive(app, shop):
    """With webhooks arriving the timer does not poll a live show's orders; a show whose
    laptop's word waits only for that read is read all the same, or it would stay on air for
    the whole 12 hours."""
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    hooks_arriving(app, shop)
    seen = moved_on(app, shop, 40 * MIN)
    with app.app_context():
        assert showend.is_over(ws_of(shop)) is False
    timer_pass(app, shop)
    with app.app_context():
        ws = ws_of(shop)
        assert ws.orders_synced_at is not None
        assert ws.orders_synced_at >= seen + showend.MOVED_ON_QUIET
        assert showend.end_of(ws) == seen
    # A live show nobody left is still not polled while webhooks arrive (they bring its orders).
    last_read(app, shop, 50 * MIN)
    moved_on(app, shop, 10 * MIN)  # too lately to need a read
    timer_pass(app, shop)
    with app.app_context():
        assert ws_of(shop).orders_synced_at < datetime.now(UTC) - 49 * MIN


def test_a_show_over_by_the_laptops_word_is_still_read_and_back_on_air_at_the_next_pass(app, shop):
    """Whatnot still calls it live: the host may only have paused. Its orders are still read,
    and an order placed after the move puts it back on air at the next pass (not at the
    hour's late read): its new orders are polled, no label is offered, no pick is read."""
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    seen = moved_on(app, shop, 40 * MIN)
    timer_pass(app, shop)  # the proof read: over by the laptop's word
    with app.app_context():
        assert showend.ending(ws_of(shop)).by == "laptop"
    sold = order(shop, "made_up_buyer_7", "Red bundle", created=datetime.now(UTC))  # resumed
    timer_pass(app, shop)
    with app.app_context():
        ws = ws_of(shop)
        assert db.session.scalar(
            select(WhatnotOrder.id).where(WhatnotOrder.whatnot_id == sold["id"])
        )
        assert showend.is_over(ws) is False
        assert showend.end_of(ws) is None
        assert sync.on_air_now(shop["org"]) is True
        assert shipping._complete(shop["org"], ws) is False
        assert picks.closes_at(ws) is None
        view = shipping.packages(db.session.get(Show, shop["show"]))
        assert view["show"]["ended"] is False and view["show"]["complete"] is False
        db.session.rollback()
    assert seen < datetime.now(UTC)


def test_a_show_over_by_the_laptops_word_is_still_read_while_webhooks_arrive(app, shop):
    """Webhooks arriving do not mean THIS order's webhook arrived: a show the laptop's word
    ended, which Whatnot still calls live, is read at every pass all the same. Otherwise a
    lost webhook leaves it over, with labels on offer, while it sells again."""
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    moved_on(app, shop, 40 * MIN)
    timer_pass(app, shop)  # the proof read: over by the laptop's word
    with app.app_context():
        assert showend.ending(ws_of(shop)).by == "laptop"
    hooks_arriving(app, shop)  # other events come; this order's own never does
    sold = order(shop, "made_up_buyer_8", "Red bundle", created=datetime.now(UTC))
    timer_pass(app, shop)
    with app.app_context():
        ws = ws_of(shop)
        assert db.session.scalar(
            select(WhatnotOrder.id).where(WhatnotOrder.whatnot_id == sold["id"])
        )
        assert showend.is_over(ws) is False
        assert shipping._complete(shop["org"], ws) is False
        db.session.rollback()


def test_whatnot_still_calling_it_live_keeps_it_read_until_twelve_hours_and_a_read_after(app, shop):
    """Over by the laptop's word, Whatnot never ending it: read on every quiet pass until the
    12 hours since it began have passed and its orders were read after that; then no more."""
    start, _ = whatnot_says(app, shop, "live", began=13 * HOUR)
    recorded(app, shop)
    repair_done(app, shop)
    seen = moved_on(app, shop, 12 * HOUR)  # an hour after it began
    last_read(app, shop, 11 * HOUR)  # read after the quiet half hour: over then
    limit = start + showend.ON_AIR_AT_MOST
    with app.app_context():
        ws = ws_of(shop)
        e = showend.ending(ws)
        assert (e.at, e.by) == (seen, "laptop")
        assert showend.still_read(ws, e) is True  # 12 hours passed, not read since
    timer_pass(app, shop)
    with app.app_context():
        ws = ws_of(shop)
        read = ws.orders_synced_at
        assert read >= limit
        assert showend.still_read(ws, showend.ending(ws)) is False
    timer_pass(app, shop)
    with app.app_context():
        assert ws_of(shop).orders_synced_at == read  # not read again
        # A show left on air for days whose last read never came is not walked back to its
        # start on every pass: the ended shows' own reads see to it.
        ws = ws_of(shop)
        ws.started_at = ws.scheduled_start_at = datetime.now(UTC) - timedelta(days=3)
        ws.orders_synced_at = None
        assert showend.still_read(ws, showend.ending(ws)) is False
        db.session.rollback()


def test_a_show_still_read_after_it_looked_over_is_applied_again_only_when_its_orders_move(
    app, shop
):
    """Polled every pass for hours while Whatnot calls it live: applying it as over each time
    would call out its wins with no order again and again (a dismissed item back every five
    minutes). Only an order that moved brings an apply."""
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    auto(app, shop)
    repair_done(app, shop)
    seen = moved_on(app, shop, 40 * MIN)
    timer_pass(app, shop)  # the proof read: over, applied as over
    assert queued(app, "whatnot_apply_show") == [shop["wshow"]]
    jobs_done(app)
    last_read(app, shop, 5 * MIN)
    timer_pass(app, shop)  # read again (still live on Whatnot), nothing new
    with app.app_context():
        assert ws_of(shop).orders_synced_at > datetime.now(UTC) - MIN
        assert showend.ending(ws_of(shop)).by == "laptop"
    assert queued(app, "whatnot_apply_show") == []
    # An order of before the move, listed late: still over, applied again.
    order(shop, "made_up_buyer_11", "Red bundle", created=seen - 5 * MIN)
    timer_pass(app, shop)
    with app.app_context():
        assert showend.ending(ws_of(shop)).by == "laptop"
    assert queued(app, "whatnot_apply_show") == [shop["wshow"]]


@pytest.mark.parametrize("status", ["pending_fulfillment", "pending"])
def test_a_win_whose_order_comes_after_the_show_looked_over_is_answered(app, shop, status):
    """The laptop saw a win; its order had not come when the show looked over, so the win was
    called out as not among Whatnot's orders. The show resumes and the order comes: the
    item closes itself (as for any order that arrives late), and while the show is on air
    again no label is offered."""
    from app.services import pipeline

    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    auto(app, shop)
    order(shop, "made_up_buyer_8", "Red bundle", created=datetime.now(UTC) - 60 * MIN)
    shop["say"](app, [("", "made_up_buyer_9 won the auction for Red bundle for $25.00", "player")])
    with app.app_context():
        pipeline.process_show(shop["show"])
        db.session.commit()
    moved_on(app, shop, 40 * MIN)
    timer_pass(app, shop)  # the proof read: over
    due_now(app)
    drain(app)  # applied as a show that is over
    assert open_items(app, "no_payment_record") == ["made_up_buyer_9"]
    late = order(shop, "made_up_buyer_9", "Red bundle", created=datetime.now(UTC), status=status)
    timer_pass(app, shop)
    due_now(app)
    drain(app)
    assert open_items(app, "no_payment_record") == []
    with app.app_context():
        sale = db.session.scalar(select(Sale).where(Sale.buyer_username == "made_up_buyer_9"))
        assert sale.external_order_id == late["id"]
        ws = ws_of(shop)
        assert showend.is_over(ws) is False
        assert shipping._complete(shop["org"], ws) is False
        view = shipping.packages(db.session.get(Show, shop["show"]))
        assert view["show"]["ended"] is False
        db.session.rollback()


def test_a_show_over_a_second_time_is_applied_as_over_once_more(app, shop):
    """Over by the laptop's word (its parcels asked for), back on air, then Whatnot ends it.
    Its orders were read after that end while the app still had it on air: they are applied
    once more as those of a show that is over, once; the next pass queues nothing for it."""
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    auto(app, shop)
    repair_done(app, shop)
    moved_on(app, shop, 40 * MIN)
    timer_pass(app, shop)  # over: its parcels are asked for
    with app.app_context():
        assert connection.sync_state(shop["org"], f"shipments:{ws_of(shop).id}").last_started_at
    order(shop, "made_up_buyer_10", "Red bundle", created=datetime.now(UTC) - 3 * MIN)
    timer_pass(app, shop)  # back on air
    with app.app_context():
        assert showend.is_over(ws_of(shop)) is False
    parcels_read(app, shop, at=datetime.now(UTC) - 35 * MIN)  # asked and read at the first end
    whatnot_says(app, shop, "ended", began=2 * HOUR, ended=2 * MIN)
    last_read(app, shop, MIN)  # read after Whatnot's end (the app still had it live)
    jobs_done(app)
    timer_pass(app, shop)
    assert queued(app, "whatnot_apply_show") == [shop["wshow"]]
    assert shop["wshow"] in queued(app, "whatnot_sync_shipments")
    assert queued(app, "pick_show") == [shop["wshow"]]  # the picks, read again after this end
    parcels_read(app, shop)
    jobs_done(app)
    timer_pass(app, shop)
    assert queued(app, "whatnot_apply_show") == []
    assert shop["wshow"] not in queued(app, "whatnot_sync_shipments")
    assert queued(app, "pick_show") == []


def test_a_show_seen_going_live_a_day_late_counts_its_hours_from_then(app, shop):
    """Scheduled 20 hours ago, seen scheduled, then live with no start known (no webhook, no
    order yet): its 12 hours count from when the app saw it go live, not from the schedule
    (it would be over the moment it went live, and its orders never polled)."""
    now = datetime.now(UTC)
    fake = shop["fake"].shows[shop["wshow"]]
    fake.update(status="scheduled", scheduled_start_at=iso(now - 20 * HOUR), ended_at=None)
    with app.app_context():
        sync.sync_shows(shop["org"], client_for(shop["org"]))
        db.session.commit()
        assert ws_of(shop).live_seen_at is None
    fake.update(status="live")
    with app.app_context():
        sync.sync_shows(shop["org"], client_for(shop["org"]))
        db.session.commit()
        ws = ws_of(shop)
        assert ws.started_at is None and ws.live_seen_at is not None
        assert showend.time_limit(ws) == ws.live_seen_at + showend.ON_AIR_AT_MOST
        assert showend.is_over(ws) is False
        assert sync.on_air_now(shop["org"]) is True
        seen_live = ws.live_seen_at
    fake.update(status="stopped")  # paused, then live again: the first time it was seen stands
    with app.app_context():
        sync.sync_shows(shop["org"], client_for(shop["org"]))
        db.session.commit()
        assert ws_of(shop).live_seen_at == seen_live
    # A show first seen already on air: when it was first seen is not when it went live.
    old = shop["fake"].add_show(title="Left on", status="live", start=now - 20 * HOUR)
    with app.app_context():
        sync.sync_shows(shop["org"], client_for(shop["org"]))
        db.session.commit()
        left = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == old["id"]))
        assert left.live_seen_at is None
        assert showend.ending(left).by == "time"


def test_an_undated_order_first_stored_after_the_move_is_a_sale_after_it(app, shop):
    whatnot_says(app, shop, "live", began=2 * HOUR)
    recorded(app, shop)
    seen = moved_on(app, shop, 40 * MIN)
    wo.read(app, shop)  # every order read after the quiet: none after the move
    with app.app_context():
        assert showend.ending(ws_of(shop)).by == "laptop"
    undated = {  # Whatnot gives no time for it; the app first stores it now
        "id": "1000099001",
        "status": "pending_fulfillment",
        "show_id": shop["wshow"],
        "buyer": {"id": "9001", "username": "made_up_buyer_12"},
    }
    with app.app_context():
        row_id, new = sync.upsert_order(shop["org"], undated)
        db.session.commit()
        row = db.session.get(WhatnotOrder, row_id)
        assert new and row.ordered_at is None and row.first_stored_at is not None
        first = row.first_stored_at
        assert showend.is_over(ws_of(shop)) is False  # still selling
        assert sync.on_air_now(shop["org"]) is True
        sync.upsert_order(shop["org"], undated)  # read again: when it was first stored stays
        db.session.commit()
        row = db.session.get(WhatnotOrder, row_id)
        assert row.first_stored_at == first and row.synced_at > first
        # First stored before the move (or before the column existed): not a sale after it.
        row.first_stored_at = seen - 5 * MIN
        db.session.commit()
        assert showend.ending(ws_of(shop)).by == "laptop"
        row.first_stored_at = None
        db.session.commit()
        assert showend.ending(ws_of(shop)).by == "laptop"


def test_a_parcel_is_not_held_for_a_show_left_paused_for_days(app, shop):
    """A buyer's parcel that also holds a win from another show still on air is held (a label
    would close the bundle while they are still winning); one left paused for days is not."""
    from tests.test_whatnot_shipping import parcel, read_shipments

    now = datetime.now(UTC)
    other = shop["fake"].add_show(title="Next door", status="stopped", start=now - 2 * HOUR)
    mine = order(shop, "made_up_buyer_5", "Red bundle")
    theirs = shop["fake"].add_order(
        show_id=other["id"],
        buyer="made_up_buyer_5",
        items=[{"title": "Blue bundle", "price_cents": 900}],
        created=now - HOUR,
    )
    box = parcel(shop, "made_up_buyer_5", [mine, theirs])
    wo.read(app, shop)
    with app.app_context():
        sync.sync_orders_of(
            shop["org"],
            client_for(shop["org"]),
            [db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == other["id"]))],
        )
        db.session.commit()
    read_shipments(app, shop)
    with app.app_context():
        held = shipping._in_a_show_still_live(shop["org"], ws_of(shop), {box["id"]})
        assert held == {box["id"]}  # paused two hours ago: it may resume
        stuck = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == other["id"]))
        stuck.started_at = stuck.scheduled_start_at = now - timedelta(days=3)
        db.session.commit()
        assert shipping._in_a_show_still_live(shop["org"], ws_of(shop), {box["id"]}) == set()


def test_the_payments_tab_says_when_the_app_treats_a_show_as_over(app, client, shop, sign_in):
    sign_in(shop["org"], role="viewer")
    whatnot_says(app, shop, "live", began=13 * HOUR)
    recorded(app, shop)
    got = client.get(f"/api/shows/{shop['show']}/whatnot").get_json()
    assert got["show"]["status"] == "live" and got["show"]["endedAt"] is None  # Whatnot's word
    assert got["show"]["assumedEnd"]["by"] == "time"
    whatnot_says(app, shop, "ended", began=3 * HOUR, ended=HOUR)
    got = client.get(f"/api/shows/{shop['show']}/whatnot").get_json()
    assert got["show"]["assumedEnd"] is None  # Whatnot's own end stands


def test_whatnot_is_not_asked_for_the_results_of_a_show_it_still_calls_live(app, shop):
    whatnot_says(app, shop, "live", began=13 * HOUR)
    with app.app_context():
        assert results.results_due(shop["org"]) == [shop["wshow"]]  # asked once
        ws_of(shop).analytics_status = "NOT_ENDED"  # what Whatnot answers for a live show
        db.session.commit()
        assert results.results_due(shop["org"]) == []
    whatnot_says(app, shop, "ended", began=13 * HOUR, ended=HOUR)  # Whatnot ends it at last
    with app.app_context():
        assert results.results_due(shop["org"]) == [shop["wshow"]]


# ---------- a show the 12-hour rule ends long after it ran ----------

LOT = "Red bundle #41"  # an auction: Whatnot's lot number is in the title
DAY = timedelta(days=1)


def ran_with_a_lot_paid_by_another_buyer(app, shop, *, began, ended=None):
    """A show the shop ran `began` ago, "applying orders as they arrive" (chosen before it):
    ann's win of lot #41 came from the stream (paid at the win), Whatnot's paid order for the
    lot is bo's and was applied as it arrived (bo's sale). Whatnot calls the show ended, with
    no end time unless `ended` (ago) is set. Its orders were last read an hour in, so it was
    never complete: ann's sale stands. Returns the state before the next timer pass."""
    now = datetime.now(UTC)
    start = now - began
    won = start + 30 * MIN
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        conn.orders_mode = "auto"
        conn.connected_at = start - 2 * DAY
        conn.auto_since = start - DAY  # not history: followed since before it began
        db.session.get(Show, shop["show"]).started_at = start
        db.session.commit()
    whatnot_says(app, shop, "ended", began=began, ended=ended)
    recorded(app, shop)
    shop["say"](app, [wo.win("ann_made_up", LOT)])
    with app.app_context():  # every line of the recording was said during that show
        db.session.execute(update(ChatMessage).values(captured_at=won, received_at=won))
        db.session.commit()
    run(app, shop["show"])
    order(shop, "bo_made_up", LOT, created=won + timedelta(seconds=20))
    wo.read(app, shop)
    last_read(app, shop, began - HOUR)  # the last read was during the show
    wo.apply(app, shop)  # bo's order, applied as it arrived
    assert sales_of(app) == [
        ("ann_made_up", "paid", pipeline.AUTOPAY_REASON),
        ("bo_made_up", "paid", "paid on Whatnot"),
    ]
    jobs_done(app)
    return wo.snapshot(app), level(app, shop["red"])


def sales_of(app):
    return [(buyer, status, reason) for buyer, _, status, reason, _ in wo.sale_rows(app)]


def first_pass(app, shop):
    """A timer pass and every job it leads to (picks wait a few minutes: run them too)."""
    timer_pass(app, shop)
    for _ in range(3):
        due_now(app)
        drain(app)


@pytest.mark.parametrize("began", [5 * DAY, 20 * DAY])
def test_a_show_the_rule_ends_days_after_it_ran_cancels_no_sale_by_itself(app, shop, began):
    """The first pass after the 12-hour rule came in reaches shows of up to 30 days ago that
    Whatnot never gave an end time: each is now over and read in full, and its orders are
    applied as complete. Whatnot's paid order for lot #41 is bo's; ann's win stands with no
    order. Days later nobody is watching that show: ann's sale is not cancelled and no stock
    moves on the app's own clock; a person is asked (the "no payment record" item)."""
    before, stock = ran_with_a_lot_paid_by_another_buyer(app, shop, began=began)
    first_pass(app, shop)
    with app.app_context():
        ws = ws_of(shop)
        assert showend.ending(ws).by == "time"
        assert whatnot_orders._complete(ws) is True  # read in full after its end
    assert sales_of(app) == [
        ("ann_made_up", "paid", pipeline.AUTOPAY_REASON),
        ("bo_made_up", "paid", "paid on Whatnot"),
    ]
    assert level(app, shop["red"]) == stock
    assert wo.snapshot(app)[1] == before[1]  # no stock movement
    assert open_items(app, "no_payment_record") == ["ann_made_up"]
    # The next pass reads and applies nothing again; applying by hand opens no second item.
    after = wo.snapshot(app)
    timer_pass(app, shop)
    assert queued(app, "whatnot_apply_show") == []
    due_now(app)
    drain(app)
    assert wo.snapshot(app) == after
    assert "gave_way" not in wo.apply(app, shop)
    assert wo.snapshot(app) == after
    assert open_items(app, "no_payment_record") == ["ann_made_up"]


def test_a_show_the_rule_ended_lately_still_settles_on_one_win(app, shop):
    """14 hours after it began (its 12-hour end two hours ago): the show was just run, and
    Whatnot's paid order says who has the lot, as it does for a show Whatnot ended."""
    _, stock = ran_with_a_lot_paid_by_another_buyer(app, shop, began=14 * HOUR)
    first_pass(app, shop)
    with app.app_context():
        assert showend.ending(ws_of(shop)).by == "time"
    assert sales_of(app) == [
        ("ann_made_up", "cancelled", "the auction was won by bo_made_up on Whatnot"),
        ("bo_made_up", "paid", "paid on Whatnot"),
    ]
    assert level(app, shop["red"]) != stock  # ann's unit is back
    assert open_items(app, "no_payment_record") == []


def test_a_show_whatnot_ended_days_ago_cancels_no_sale_by_itself_either(app, shop):
    """Whatnot's own end time, five days ago, read in full only now: days after a show nobody
    is watching it, so the rule is the age of the end, not who said it. A person decides."""
    before, stock = ran_with_a_lot_paid_by_another_buyer(
        app, shop, began=5 * DAY + 3 * HOUR, ended=5 * DAY
    )
    first_pass(app, shop)
    with app.app_context():
        assert showend.ending(ws_of(shop)).by == "whatnot"
    assert sales_of(app) == [
        ("ann_made_up", "paid", pipeline.AUTOPAY_REASON),
        ("bo_made_up", "paid", "paid on Whatnot"),
    ]
    assert level(app, shop["red"]) == stock
    assert wo.snapshot(app)[1] == before[1]
    assert open_items(app, "no_payment_record") == ["ann_made_up"]


def test_an_end_time_whatnot_fills_in_days_later_undoes_nothing_a_person_was_asked(app, shop):
    """The 12-hour rule ended it five days ago and a person was asked about ann's win. Whatnot
    then gives the show an end time: the next apply still cancels nothing by itself."""
    before, stock = ran_with_a_lot_paid_by_another_buyer(app, shop, began=5 * DAY)
    first_pass(app, shop)
    assert open_items(app, "no_payment_record") == ["ann_made_up"]
    with app.app_context():
        ws = ws_of(shop)
        ws.status, ws.ended_at = "ended", datetime.now(UTC) - 5 * DAY + 2 * HOUR
        db.session.commit()
        assert showend.ending(ws_of(shop)).by == "whatnot"
    first_pass(app, shop)
    assert "gave_way" not in wo.apply(app, shop)
    assert sales_of(app) == [
        ("ann_made_up", "paid", pipeline.AUTOPAY_REASON),
        ("bo_made_up", "paid", "paid on Whatnot"),
    ]
    assert level(app, shop["red"]) == stock
    assert wo.snapshot(app)[1] == before[1]
    assert open_items(app, "no_payment_record") == ["ann_made_up"]
