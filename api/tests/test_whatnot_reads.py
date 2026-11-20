"""Reading Whatnot completely: results that are asked again, orders that arrive late or by
parcel, a catch-up with a limit, a read that fails half way, and the order that no one can
name. Against the in-memory fake only; nothing here reaches the live shop, and every buyer
name is made up.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import db
from app.models import (
    Job,
    ReviewItem,
    ReviewStatus,
    Sale,
    Show,
    WhatnotConnection,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShow,
    WhatnotSyncState,
)
from app.services import jobs
from app.services.whatnot import client_for, results, sync
from app.services.whatnot import orders as whatnot_orders
from app.services.whatnot.client import Forbidden, Invalid, Unavailable, WhatnotClient
from tests import test_whatnot_orders as wo
from tests.test_whatnot_orders import apply, compare, order, read, reviews, win
from tests.test_whatnot_results import GENERATED, set_results
from tests.test_whatnot_shipping import parcel, read_shipments

shop = wo.shop  # the connected shop and its show, matched to an ended show on Whatnot
run = wo.run

HOUR = timedelta(hours=1)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def ws_of(shop) -> WhatnotShow:
    return db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"]))


def ended(app, shop, ago: timedelta, **fields):
    """The Whatnot show ended `ago` ago, in Whatnot's copy and the app's."""
    end = datetime.now(UTC) - ago
    shop["fake"].shows[shop["wshow"]].update(
        status="ended", ended_at=iso(end), scheduled_start_at=iso(end - 3 * HOUR)
    )
    read(app, shop, orders=False)  # the show list: the app's copy of the show
    with app.app_context():
        ws = ws_of(shop)
        ws.status, ws.ended_at, ws.started_at = "ended", end, end - 3 * HOUR
        for k, v in fields.items():
            setattr(ws, k, v)
        db.session.commit()
    return end


def drain(app, limit: int = 40):
    """The timer's pass and everything it queued, run in order (as the worker does)."""
    with app.app_context():
        for _ in range(limit):
            job = jobs.run_once("t")
            if job is None:
                return
            assert job.status.value in ("done", "queued"), job.error


@pytest.fixture
def timer(monkeypatch):
    monkeypatch.delenv("JOBS_INLINE", raising=False)


def slot_free(app, shop, *resources):
    """Let the timer's spacing (15 minutes, an hour) pass for these kinds of reading."""
    with app.app_context():
        for r in resources:
            sync_state = db.session.scalar(
                select(WhatnotSyncState).where(WhatnotSyncState.resource == r)
            )
            if sync_state is not None:
                sync_state.last_started_at = datetime.now(UTC) - timedelta(days=1)
        db.session.commit()


# ---------- L4 / A18 / V6: results are asked again ----------


def test_results_whatnot_called_generated_are_asked_again_for_three_days(app, shop):
    """The live show's results were read 25 minutes after it ended, said GENERATED with 94
    of its orders, and were never asked again."""
    end = ended(app, shop, timedelta(hours=3))
    set_results(shop, {**GENERATED, "orders_count": 94})
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        ws.analytics_at = end + timedelta(minutes=25)
        db.session.commit()
        assert ws.analytics["orders_count"] == 94
        # Early (within six hours of the end) it is asked again after an hour.
        assert results.results_due(shop["org"]) == [shop["wshow"]]
        # The shop's spacing: hourly at most.
        state = sync.connection.sync_state(shop["org"], "analytics")
        state.last_started_at = datetime.now(UTC) - timedelta(minutes=5)
        db.session.commit()
        assert results.results_due(shop["org"]) == []
    set_results(shop, {**GENERATED, "orders_count": 217})
    slot_free(app, shop, "analytics")
    with app.app_context():
        results.show_results_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
        db.session.commit()
        assert ws_of(shop).analytics["orders_count"] == 217


def test_a_late_read_of_generated_results_is_spaced_out_and_stops_after_three_days(app, shop):
    end = ended(app, shop, timedelta(hours=20))
    set_results(shop, GENERATED)
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        assert end + 8 * HOUR < datetime.now(UTC)  # (the read below is a late one)
        ws.analytics_at = datetime.now(UTC) - 7 * HOUR
        db.session.commit()
        assert results.results_due(shop["org"]) == [shop["wshow"]]
        ws.analytics_at = datetime.now(UTC) - 2 * HOUR
        db.session.commit()
        assert results.results_due(shop["org"]) == []
        # Three days after the end Whatnot's figures stand.
        ws.ended_at = datetime.now(UTC) - timedelta(days=3, hours=1)
        ws.analytics_at = datetime.now(UTC) - 2 * 24 * HOUR
        db.session.commit()
        assert results.results_due(shop["org"]) == []


def test_results_are_never_asked_again_while_a_show_is_on_air(app, shop):
    end = ended(app, shop, timedelta(hours=2))
    set_results(shop, GENERATED)
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        ws.analytics_at = end + 30 * timedelta(minutes=1)
        db.session.add(
            WhatnotShow(
                organization_id=shop["org"],
                whatnot_id="live-one",
                status="live",
                started_at=datetime.now(UTC) - 20 * timedelta(minutes=1),
            )
        )
        db.session.commit()
        assert results.results_due(shop["org"]) == []  # the re-read waits
        shop["fake"].calls.clear()
        results.show_results_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
        assert not [c for c in shop["fake"].calls if "analytics" in c[1]]  # not even by hand
        db.session.get(WhatnotShow, ws.id).analytics_at = end + 30 * timedelta(minutes=1)
        live = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == "live-one"))
        live.status = "ended"
        db.session.commit()
        assert results.results_due(shop["org"]) == [shop["wshow"]]  # on air no more


def test_figures_whatnot_generated_are_never_replaced_by_still_working_on_it(app, shop):
    ended(app, shop, timedelta(hours=3))
    set_results(shop, GENERATED)
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        db.session.commit()
    set_results(shop, {"calculation_status": "PENDING"})
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        assert (ws.analytics_status, ws.analytics["orders_count"]) == ("GENERATED", 226)


def test_the_timer_asks_for_generated_results_again(app, shop, timer):
    end = ended(app, shop, timedelta(hours=3))
    set_results(shop, {**GENERATED, "orders_count": 94})
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        ws.analytics_at = end + 25 * timedelta(minutes=1)
        ws.orders_synced_at = datetime.now(UTC) - HOUR  # its orders are read in full
        db.session.commit()
    set_results(shop, {**GENERATED, "orders_count": 217})
    slot_free(app, shop, "analytics")
    with app.app_context():
        sync.schedule_all()
    drain(app)
    with app.app_context():
        assert ws_of(shop).analytics["orders_count"] == 217


# ---------- V1: an order first stored by the parcel read is applied ----------


def test_an_order_first_stored_by_a_parcel_read_is_applied_in_auto_mode(app, shop, timer):
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    read(app, shop, orders=False)
    late = order(shop, "lani808", "Red bundle")  # never listed: only the parcel names it
    parcel(shop, "lani808", [late], status="label_generated", tracking="9400111899")
    read_shipments(app, shop)
    with app.app_context():
        assert db.session.scalar(select(WhatnotOrder).where(WhatnotOrder.whatnot_id == late["id"]))
        queued = db.session.scalars(
            select(Job).where(Job.kind == "whatnot_apply_show", Job.status == "queued")
        ).all()
        assert [j.payload["whatnot_show_id"] for j in queued] == [shop["wshow"]]


def test_a_parcel_read_in_compare_mode_applies_nothing(app, shop, timer):
    read(app, shop, orders=False)
    late = order(shop, "lani808", "Red bundle")
    parcel(shop, "lani808", [late], status="label_generated", tracking="9400111899")
    read_shipments(app, shop)
    with app.app_context():
        assert not db.session.scalars(select(Job).where(Job.kind == "whatnot_apply_show")).all()


# ---------- A10: a paid order placed after the final read ----------


def test_a_paid_retry_placed_after_the_final_read_is_read_and_queued_to_apply(app, shop, timer):
    end = ended(app, shop, timedelta(hours=2))
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        ws = ws_of(shop)
        ws.orders_synced_at = end + timedelta(minutes=10)  # the "final" read, soon after the end
        db.session.commit()
    retry = order(shop, "lani808", "Red bundle", created=end + timedelta(minutes=40))
    slot_free(app, shop, "shows")
    with app.app_context():
        sync.schedule_all()
    drain(app)
    with app.app_context():
        assert db.session.scalar(select(WhatnotOrder).where(WhatnotOrder.whatnot_id == retry["id"]))
        assert ws_of(shop).orders_synced_at >= end + HOUR  # the late read is the new last read
        queued = db.session.scalars(select(Job).where(Job.kind == "whatnot_apply_show")).all()
        assert [j.payload["whatnot_show_id"] for j in queued] == [shop["wshow"]]


def test_the_late_reads_come_once_each_and_not_while_a_show_is_on_air(app, shop):
    end = datetime.now(UTC) - timedelta(hours=7)
    with app.app_context():
        ws = ws_of(shop) or WhatnotShow(organization_id=shop["org"], whatnot_id=shop["wshow"])
        ws.status, ws.ended_at, ws.started_at = "ended", end, end - 3 * HOUR
        db.session.add(ws)
        # Read at the end + 7 minutes, then at +1h10 (the first late read), not yet at +6h.
        ws.orders_synced_at = end + timedelta(minutes=70)
        db.session.commit()
        assert [w.whatnot_id for w in sync._unread_shows(shop["org"], [])] == [shop["wshow"]]
        ws.orders_synced_at = end + timedelta(hours=6, minutes=1)
        db.session.commit()
        assert sync._unread_shows(shop["org"], []) == []  # +24h not yet reached
        ws.orders_synced_at = end + timedelta(minutes=70)
        db.session.commit()
        live = WhatnotShow(
            organization_id=shop["org"],
            whatnot_id="on-air",
            status="live",
            started_at=datetime.now(UTC) - 5 * timedelta(minutes=1),
        )
        assert sync._unread_shows(shop["org"], [live]) == []  # a late read waits for the show
        ws.orders_synced_at = end - timedelta(minutes=1)  # never read since it ended
        db.session.commit()
        assert [w.whatnot_id for w in sync._unread_shows(shop["org"], [live])] == [shop["wshow"]]


# ---------- A31 / V8 / A59: the catch-up limit ----------


def two_shows(app, shop):
    """An older ended show on Whatnot, besides the shop's own."""
    older = shop["fake"].add_show(
        title="Last week",
        start=shop["now"] - timedelta(days=5, hours=3),
        ended=shop["now"] - timedelta(days=5),
    )
    return older["id"]


def test_a_catch_up_that_stops_at_its_limit_does_not_mark_the_shows_it_never_reached(
    app, shop, monkeypatch
):
    monkeypatch.setattr(sync, "MAX_NEW_ORDERS", 5)
    older = two_shows(app, shop)
    for i in range(4):
        order(shop, f"new_buyer_{i}", "Red bundle")  # the newer show: 4 orders
    for i in range(6):
        shop["fake"].add_order(
            show_id=older,
            buyer=f"old_buyer_{i}",
            items=[{"title": "Blue bundle", "price_cents": 900}],
            created=shop["now"] - timedelta(days=5, hours=2, minutes=i),
        )
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        shows = db.session.scalars(select(WhatnotShow).order_by(WhatnotShow.ended_at)).all()
        assert len(shows) == 2
        out = sync.sync_orders_of(shop["org"], client, shows)
        db.session.commit()
        by_id = {w.whatnot_id: w for w in db.session.scalars(select(WhatnotShow))}
        assert out["complete"] is False
        assert by_id[shop["wshow"]].orders_synced_at is not None  # down to its own start
        assert by_id[older].orders_synced_at is None  # NOT claimed read: 3 of 6 orders held
        held = db.session.scalar(
            select(db.func.count())
            .select_from(WhatnotOrder)
            .where(WhatnotOrder.whatnot_show_id == older)
        )
        assert 0 < held < 6
        # The next pass carries on past what is held, until the older show is read in full.
        for _ in range(4):
            todo = [
                w for w in db.session.scalars(select(WhatnotShow)) if w.orders_synced_at is None
            ]
            if not todo:
                break
            sync.sync_orders_of(shop["org"], client, todo)
            db.session.commit()
        assert by_id[older].orders_synced_at is not None
        assert (
            db.session.scalar(
                select(db.func.count())
                .select_from(WhatnotOrder)
                .where(WhatnotOrder.whatnot_show_id == older)
            )
            == 6
        )


def test_the_open_order_refresh_beyond_its_limit_carries_on_next_time(app, shop, monkeypatch):
    monkeypatch.setattr(sync, "MAX_OPEN_REFRESH", 2)
    ids = [order(shop, f"slow_buyer_{i}", "Red bundle", status="pending")["id"] for i in range(5)]
    read(app, shop)
    for oid in ids:
        shop["fake"].set_order_status(oid, "completed")
    with app.app_context():
        client = client_for(shop["org"])
        for _ in range(3):
            sync.refresh_open_orders(shop["org"], client)
            db.session.commit()
        statuses = {o.whatnot_id: o.status for o in db.session.scalars(select(WhatnotOrder))}
        assert {statuses[i] for i in ids} == {"completed"}  # oldest-synced first, so all come


# ---------- A30 / A53: a read that fails half way keeps what it read ----------


def failing_items_after(monkeypatch, good: int, error=Unavailable):
    real = WhatnotClient.order_items
    calls = {"n": 0}

    def flaky(self, order_id):
        calls["n"] += 1
        if calls["n"] > good:
            raise error("Whatnot could not be reached")
        return real(self, order_id)

    monkeypatch.setattr(WhatnotClient, "order_items", flaky)
    return calls


def test_a_read_that_fails_part_way_keeps_the_orders_already_read_and_not_the_half_one(
    app, shop, monkeypatch
):
    for i in range(6):
        order(shop, f"buyer_{i}", "Red bundle")
    calls = failing_items_after(monkeypatch, 3)
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        db.session.commit()
        with pytest.raises(Unavailable):
            sync.sync_orders_of(shop["org"], client, [ws_of(shop)])
        db.session.rollback()  # what the job's bookkeeping does next
        kept = db.session.scalars(select(WhatnotOrder)).all()
        assert len(kept) == 3  # the three whole ones; the one whose items failed is not kept
        assert all(o.items for o in kept)
        assert ws_of(shop).orders_synced_at is None  # not claimed read
    monkeypatch.undo()
    with app.app_context():
        client = client_for(shop["org"])
        out = sync.sync_orders_of(shop["org"], client, [ws_of(shop)])
        db.session.commit()
        assert out["new"] == 3 and db.session.scalar(select(db.func.count(WhatnotOrder.id))) == 6
        assert all(o.items for o in db.session.scalars(select(WhatnotOrder)))
        assert ws_of(shop).orders_synced_at is not None
    assert calls["n"] >= 4


def test_a_failed_read_through_the_timer_keeps_progress_and_is_retried(
    app, shop, timer, monkeypatch
):
    for i in range(4):
        order(shop, f"buyer_{i}", "Red bundle")
    failing_items_after(monkeypatch, 2)
    with app.app_context():
        sync.schedule_all()
        job = jobs.run_once("t")
        assert job.status.value == "queued" and "Unavailable" in job.error
        assert db.session.scalar(select(db.func.count(WhatnotOrder.id))) == 2
        assert ws_of(shop).orders_synced_at is None


# ---------- A26: a failure does not use up the timer's slot ----------


def test_a_failed_show_list_is_due_again_in_minutes_not_a_quarter_hour(app, shop, timer):
    shop["fake"].token = "regenerated"  # every call is refused
    with app.app_context():
        sync.schedule_all()
        job = jobs.run_once("t")
        assert job.status.value == "queued"  # a rejected token: nothing behind it can work
        state = sync.connection.sync_state(shop["org"], "shows")
        # Spaced as if it had been asked RETRY_AFTER_FAILURE short of a whole interval ago.
        waited = sync.SHOWS_EVERY - (datetime.now(UTC) - state.last_started_at)
        assert abs(waited - sync.RETRY_AFTER_FAILURE) < timedelta(seconds=30)
        state.last_started_at -= sync.RETRY_AFTER_FAILURE + timedelta(seconds=5)
        db.session.commit()
        assert sync._due(shop["org"], "shows", sync.SHOWS_EVERY) is True  # the next sweep asks
    from app.services.whatnot.fake import TOKEN

    shop["fake"].token = TOKEN  # Whatnot accepts the token again
    with app.app_context():
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        assert sync._due(shop["org"], "shows", sync.SHOWS_EVERY) is False  # it worked: spaced
        assert db.session.scalar(select(WhatnotShow)) is not None


def test_a_show_list_that_keeps_failing_never_stops_a_live_shows_orders_being_read(
    app, shop, timer, monkeypatch
):
    """A show list that fails is recorded and the pass goes on to the live show's orders with
    the shows it knows: a failure that is due again in minutes must not abort every pass."""
    now = datetime.now(UTC)
    live = shop["fake"].add_show(title="Live now", status="live", start=now - timedelta(minutes=30))
    shop["fake"].add_order(
        show_id=live["id"], buyer="lani808", items=[{"title": "Red bundle", "price_cents": 900}]
    )
    with app.app_context():
        db.session.add(
            WhatnotShow(
                organization_id=shop["org"],
                whatnot_id=live["id"],
                status="live",
                started_at=now - timedelta(minutes=30),
            )
        )
        db.session.commit()
    monkeypatch.setattr(
        WhatnotClient,
        "shows",
        lambda self, status, **kw: (_ for _ in ()).throw(Unavailable("down")),
    )
    with app.app_context():
        sync.sync_job({"org_id": shop["org"]})  # the list fails; the live orders are read
        db.session.commit()
        assert db.session.scalar(select(db.func.count(WhatnotOrder.id))) == 1
        assert sync.connection.sync_state(shop["org"], "shows").last_error.startswith("Unavailable")


def test_a_failed_open_order_read_is_due_again_in_minutes_not_an_hour(
    app, shop, timer, monkeypatch
):
    order(shop, "lani808", "Red bundle", status="pending")
    read(app, shop)
    slot_free(app, shop, "shows", "open_orders")
    monkeypatch.setattr(
        WhatnotClient, "order", lambda self, oid: (_ for _ in ()).throw(Unavailable("down"))
    )
    with app.app_context():
        sync.sync_job({"org_id": shop["org"]})  # the failure is recorded; the pass finishes
        state = sync.connection.sync_state(shop["org"], "open_orders")
        assert state.last_error.startswith("Unavailable")
        assert sync._due(shop["org"], "open_orders", sync.OPEN_ORDERS_EVERY) is False  # a beat
        state.last_started_at -= sync.RETRY_AFTER_FAILURE + timedelta(seconds=5)
        db.session.commit()
        assert sync._due(shop["org"], "open_orders", sync.OPEN_ORDERS_EVERY) is True


# ---------- A58 / A33: the open-order refresh ----------


def test_one_order_whatnot_refuses_does_not_block_the_orders_behind_it(app, shop, monkeypatch):
    first = order(shop, "buyer_a", "Red bundle", status="pending")
    second = order(shop, "buyer_b", "Red bundle", status="pending")
    read(app, shop)
    shop["fake"].set_order_status(second["id"], "completed")
    real = WhatnotClient.order

    def refuse_first(self, order_id):
        if order_id == first["id"]:
            raise Invalid("Whatnot did not accept that request")
        return real(self, order_id)

    monkeypatch.setattr(WhatnotClient, "order", refuse_first)
    with app.app_context():
        # The refused order is the oldest synced, so it comes first in the line.
        db.session.execute(
            WhatnotOrder.__table__.update()
            .where(WhatnotOrder.whatnot_id == first["id"])
            .values(synced_at=datetime.now(UTC) - 2 * HOUR)
        )
        db.session.commit()
        sync.refresh_open_orders(shop["org"], client_for(shop["org"]))
        db.session.commit()
        got = {o.whatnot_id: o for o in db.session.scalars(select(WhatnotOrder))}
        assert got[second["id"]].status == "completed"
        assert got[first["id"]].synced_at > datetime.now(UTC) - timedelta(minutes=1)  # to the back


def test_refusals_in_a_row_are_not_about_one_order_and_end_the_pass(app, shop, monkeypatch):
    for i in range(7):
        order(shop, f"buyer_{i}", "Red bundle", status="pending")
    read(app, shop)
    seen = []

    def refuse(self, order_id):
        seen.append(order_id)
        raise Invalid("no")

    monkeypatch.setattr(WhatnotClient, "order", refuse)
    with app.app_context():
        with pytest.raises(Invalid):
            sync.refresh_open_orders(shop["org"], client_for(shop["org"]))
    assert len(seen) == sync.REFUSED_IN_A_ROW


def test_an_order_that_moves_between_open_states_is_applied_again(app, shop, timer):
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    o = order(shop, "lani808", "Red bundle", status="pending")
    read(app, shop)
    shop["fake"].set_order_status(o["id"], "pending_fulfillment")
    slot_free(app, shop, "shows", "open_orders")
    with app.app_context():
        # The show's orders were read after it ended, so only the open-order re-read is due.
        ws = ws_of(shop)
        ws.orders_synced_at = datetime.now(UTC) + HOUR
        db.session.query(Job).delete()
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        assert db.session.scalar(select(WhatnotOrder.status)) == "pending_fulfillment"
        queued = db.session.scalars(select(Job).where(Job.kind == "whatnot_apply_show")).all()
        assert [j.payload["whatnot_show_id"] for j in queued] == [shop["wshow"]]


# ---------- A57: an order stored with no items ----------


def test_an_order_stored_with_no_items_gets_them_on_a_later_read(app, shop):
    o = order(shop, "lani808", "Red bundle")
    items = shop["fake"].items_of(o["id"])
    for it in items:
        del shop["fake"].order_items[it["id"]]  # Whatnot answers the order before its items
    read(app, shop)
    with app.app_context():
        assert db.session.scalar(select(db.func.count(WhatnotOrderItem.id))) == 0
    for it in items:
        shop["fake"].order_items[it["id"]] = it
    read(app, shop)
    with app.app_context():
        assert db.session.scalar(select(db.func.count(WhatnotOrderItem.id))) == 1


# ---------- A9 / A25 / A29: the order nobody can name ----------


def nameless_order(shop, title="Red bundle", price=2500, **kw):
    o = order(shop, "someone", title, price, **kw)
    o["buyer"] = {"id": None, "username": None, "display_name": None}  # a closed account
    return o


def test_one_order_with_no_buyer_does_not_stop_the_rest_of_the_show_being_applied(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    order(shop, "lani808", "Red bundle")
    nameless_order(shop, "Blue bundle", 900)
    for i in range(5):
        order(shop, f"buyer_{i}", f"Lot {i}")
    read(app, shop)
    with app.app_context():
        result = whatnot_orders.apply(db.session.get(Show, shop["show"]))
        db.session.commit()
        assert result["info"]["noBuyer"] == 1 and result["info"]["blind"] is False
        assert result["summary"].get("recorded") == 5  # the other buyers' orders became sales
        sales = {s.buyer_username for s in db.session.scalars(select(Sale))}
        assert {"lani808", "buyer_0"} <= sales
    # A person is told, in the shop's words, and it names no token or permission.
    texts = [summary for reason, summary in reviews(app) if reason == "orders_left_out"]
    assert len(texts) == 1
    assert "did not say who bought 1 item" in texts[0]
    assert "token" not in texts[0].lower() and "permission" not in texts[0].lower()
    # Once the order is named the item clears by itself.
    with app.app_context():
        db.session.execute(
            WhatnotOrder.__table__.update()
            .where(WhatnotOrder.buyer_username.is_(None))
            .values(buyer_username="named_late", buyer_id="u-1")
        )
        db.session.commit()
        whatnot_orders.apply(db.session.get(Show, shop["show"]))
        db.session.commit()
    assert [r for r, _ in reviews(app) if r == "orders_left_out"] == []


def test_a_sale_with_no_order_is_not_called_out_while_a_paid_order_has_no_buyer(app, shop):
    """The nameless order may be the one that pays the sale: say nothing about it."""
    shop["say"](app, [win("ghost", "Red bundle")])
    run(app, shop["show"])
    order(shop, "other_buyer", "Blue bundle")
    nameless_order(shop, "Red bundle")
    for i in range(6):
        order(shop, f"buyer_{i}", f"Lot {i}")
    read(app, shop)
    assert "uncovered" not in apply(app, shop)
    assert [r for r, _ in reviews(app) if r == "no_payment_record"] == []


def test_with_hardly_a_buyer_named_nothing_is_applied_and_the_show_is_blind(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    order(shop, "lani808", "Red bundle")
    for _ in range(2):
        nameless_order(shop)
    read(app, shop)
    with app.app_context():
        assert whatnot_orders.blind(ws_of(shop)) is True
        with pytest.raises(whatnot_orders.OrdersError, match="who bought these orders"):
            whatnot_orders.apply(db.session.get(Show, shop["show"]))
        db.session.rollback()
    _, _, result = compare(app, shop)
    assert result["info"]["blind"] is True


def test_the_apply_job_that_cannot_apply_a_blind_show_says_so_in_review(app, shop, inline_jobs):
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    order(shop, "lani808", "Red bundle")
    for _ in range(2):
        nameless_order(shop)
    read(app, shop)
    with app.app_context():
        whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
    items = [s for r, s in reviews(app) if r == "orders_left_out"]
    assert len(items) == 1 and "None of this show's orders could be applied" in items[0]
    # Asking again does not pile a second one up.
    with app.app_context():
        whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
    assert len([r for r, _ in reviews(app) if r == "orders_left_out"]) == 1


def test_a_stream_decline_still_waits_for_orders_when_only_one_order_has_no_buyer(app, shop):
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    order(shop, "lani808", "Red bundle")
    for i in range(5):
        order(shop, f"buyer_{i}", f"Lot {i}")
    nameless_order(shop, "Blue bundle")
    read(app, shop)
    with app.app_context():
        assert whatnot_orders.decides(db.session.get(Show, shop["show"])) is True


def test_an_order_naming_no_listing_is_counted_and_told_not_dropped_silently(app, shop):
    order(shop, "lani808", "Red bundle")
    order(shop, "kat.c", "")
    read(app, shop)
    with app.app_context():
        result = whatnot_orders.apply(db.session.get(Show, shop["show"]))
        db.session.commit()
        assert result["info"]["unreadable"] == 1
    assert any("named no listing" in s for r, s in reviews(app) if r == "orders_left_out")


def test_a_left_out_notice_a_person_dismissed_is_not_opened_again_for_the_same_figures(
    app, shop, sign_in
):
    sign_in(shop["org"])
    order(shop, "lani808", "Red bundle")
    for i in range(10):
        order(shop, f"buyer_{i}", f"Lot {i}")
    nameless_order(shop, "Blue bundle")
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        from app.models import User

        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "orders_left_out"))
        item.status, item.resolved_by_id = (
            ReviewStatus.dismissed,
            db.session.scalar(select(User.id)),
        )
        db.session.commit()
    apply(app, shop)  # the next apply, minutes on: the same figures
    with app.app_context():
        rows = db.session.scalars(select(ReviewItem).where(ReviewItem.reason == "orders_left_out"))
        assert [r.status for r in rows] == [ReviewStatus.dismissed]
    # Figures that changed are worth saying again.
    nameless_order(shop, "Green bundle")
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        rows = db.session.scalars(
            select(ReviewItem).where(ReviewItem.reason == "orders_left_out").order_by(ReviewItem.id)
        ).all()
        assert [r.status for r in rows] == [ReviewStatus.dismissed, ReviewStatus.open]


def test_a_show_whatnot_counts_more_orders_for_than_the_app_holds_is_read_again(app, shop):
    """The repair for shows an old catch-up marked read that it never reached."""
    end = ended(app, shop, timedelta(days=5))
    for i in range(3):
        order(shop, f"buyer_{i}", "Red bundle", created=end - timedelta(minutes=30 + i))
    read(app, shop)
    with app.app_context():
        ws = ws_of(shop)
        ws.orders_synced_at = end + 2 * HOUR  # read, long ago, and nothing more is owed
        ws.analytics = {"orders_count": 3}
        db.session.commit()
        assert sync._unread_shows(shop["org"], []) == []  # held 3 of 3
        ws.analytics = {"orders_count": 9}
        db.session.commit()
        assert sync._unread_shows(shop["org"], []) == [ws]  # held 3 of 9: read again
        live = WhatnotShow(
            organization_id=shop["org"],
            whatnot_id="on-air",
            status="live",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        assert sync._unread_shows(shop["org"], [live]) == []  # not while a show is on air
        ws.orders_synced_at = datetime.now(UTC) - HOUR  # just read: not asked every pass
        db.session.commit()
        assert sync._unread_shows(shop["org"], []) == []


def test_while_a_show_is_on_air_a_catch_up_stores_only_a_few_orders_a_pass(app, shop, monkeypatch):
    monkeypatch.setattr(sync, "MAX_NEW_ORDERS_ON_AIR", 2)
    for i in range(5):
        order(shop, f"buyer_{i}", "Red bundle")
    read(app, shop, orders=False)
    with app.app_context():
        db.session.add(
            WhatnotShow(
                organization_id=shop["org"],
                whatnot_id="on-air",
                status="live",
                started_at=datetime.now(UTC) - timedelta(minutes=20),
            )
        )
        db.session.commit()
        out = sync.sync_orders_of(shop["org"], client_for(shop["org"]), [ws_of(shop)])
        db.session.commit()
        assert (out["new"], out["complete"]) == (2, False)
        assert ws_of(shop).orders_synced_at is None


# ---------- review of the first change: what it must never do ----------


def apply_jobs():
    return [
        j.payload["whatnot_show_id"]
        for j in db.session.scalars(select(Job).where(Job.kind == "whatnot_apply_show"))
    ]


def set_auto(app):
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()


def test_a_declined_payment_is_not_undone_when_a_paid_order_with_no_buyer_may_be_its_retry(
    app, shop
):
    """A named failed order and a paid retry with no buyer (the name stopped arriving): the
    buyer paid, so the sale stays paid, and a person is told why nothing was undone."""
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, "lani808", "Red bundle", status="failed", created=now - timedelta(seconds=20))
    nameless_order(shop, "Red bundle", created=now - timedelta(seconds=10))
    for i in range(5):
        order(shop, f"buyer_{i}", f"Lot {i}")
    read(app, shop)
    summary = apply(app, shop)
    assert "decline" not in summary
    with app.app_context():
        sale = db.session.scalar(select(Sale).where(Sale.buyer_username == "lani808"))
        assert sale.status.value == "paid"
    texts = [s for r, s in reviews(app) if r == "orders_left_out"]
    assert len(texts) == 1 and "declined payment was not undone" in texts[0]
    assert "lani808's Red bundle" in texts[0]  # named, so a person can check it


def test_a_declined_payment_is_still_undone_when_the_order_with_no_buyer_is_another_lot(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    order(shop, "lani808", "Red bundle", status="failed", created=now - timedelta(seconds=20))
    nameless_order(shop, "Blue bundle", 900, created=now - timedelta(seconds=10))
    for i in range(5):
        order(shop, f"buyer_{i}", f"Lot {i}")
    read(app, shop)
    assert apply(app, shop).get("decline") == 1
    with app.app_context():
        sale = db.session.scalar(select(Sale).where(Sale.buyer_username == "lani808"))
        assert sale.status.value == "payment_failed"


def test_the_left_out_notice_says_when_unpaid_sales_are_not_being_called_out(app, shop):
    shop["say"](app, [win("ghost", "Red bundle")])
    run(app, shop["show"])
    nameless_order(shop, "Red bundle")
    for i in range(6):
        order(shop, f"buyer_{i}", f"Lot {i}")
    read(app, shop)
    apply(app, shop)
    texts = [s for r, s in reviews(app) if r == "orders_left_out"]
    assert len(texts) == 1 and "sales with no order are not called out" in texts[0]


# ---------- A30/A53: a pass that fails part-way still queues what it learned ----------


def test_an_open_order_that_settled_in_a_pass_that_then_failed_is_still_applied(
    app, shop, timer, monkeypatch
):
    set_auto(app)
    a = order(shop, "buyer_aa", "Red bundle", status="pending")
    b = order(shop, "buyer_bb", "Blue bundle", status="pending")
    read(app, shop)
    shop["fake"].set_order_status(a["id"], "failed")
    real = WhatnotClient.order

    def down_for_b(self, oid):
        if oid == b["id"]:
            raise Unavailable("down")
        return real(self, oid)

    monkeypatch.setattr(WhatnotClient, "order", down_for_b)
    slot_free(app, shop, "shows", "open_orders")
    with app.app_context():
        ws_of(shop).orders_synced_at = datetime.now(UTC) + HOUR  # no show read is due
        db.session.execute(
            WhatnotOrder.__table__.update()
            .where(WhatnotOrder.whatnot_id == a["id"])
            .values(synced_at=datetime.now(UTC) - 2 * HOUR)
        )
        db.session.query(Job).delete()
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})  # the pass goes on to the end
        db.session.commit()
        assert (
            db.session.scalar(select(WhatnotOrder.status).where(WhatnotOrder.whatnot_id == a["id"]))
            == "failed"
        )
        assert apply_jobs() == [shop["wshow"]]  # the order that moved is applied, now
        assert sync.connection.sync_state(shop["org"], "open_orders").last_error is not None


def test_an_open_order_read_that_keeps_failing_does_not_starve_the_rest_of_the_pass(
    app, shop, timer, monkeypatch
):
    set_auto(app)
    stuck = order(shop, "buyer_old", "Old lot", status="pending")
    read(app, shop)
    order(shop, "buyer_new", "New lot")  # placed later, unread
    real = WhatnotClient.order

    def five_hundred(self, oid):
        if oid == stuck["id"]:
            raise Unavailable("Whatnot answered 500 for this one order")
        return real(self, oid)

    monkeypatch.setattr(WhatnotClient, "order", five_hundred)
    with app.app_context():
        ws_of(shop).orders_synced_at = None  # the after-show read is owed
        db.session.query(Job).delete()
        db.session.commit()
    slot_free(app, shop, "shows", "open_orders", "analytics")
    for _ in range(3):
        with app.app_context():
            sync.sync_job({"org_id": shop["org"]})  # never raises for the open orders
            db.session.commit()
            st = sync.connection.sync_state(shop["org"], "open_orders")
            st.last_started_at -= timedelta(minutes=5)  # the next sweep finds it due again
            db.session.commit()
    with app.app_context():
        assert ws_of(shop).orders_synced_at is not None
        kinds = {j.kind for j in db.session.scalars(select(Job))}
        assert "whatnot_apply_show" in kinds  # the read show is applied
        assert "whatnot_show_results" in kinds  # and its results asked


# ---------- the shows an old catch-up marked read ----------


def test_shows_an_old_catch_up_marked_read_are_read_again_once_whatever_their_age(app, shop, timer):
    """Whatnot's results are asked for only a week after a show: the repair of a show the
    old cap marked read does not wait for them (the 30-day figure on the dashboard)."""
    end = ended(app, shop, timedelta(days=10))
    for i in range(3):
        order(shop, f"buyer_{i}", f"Lot {i}", created=end - timedelta(minutes=40 + i))
    with app.app_context():
        ws = ws_of(shop)
        ws.orders_synced_at = end + timedelta(days=1)  # "read" by the capped pass: no orders
        ws.analytics = None
        db.session.commit()
        assert db.session.scalar(select(db.func.count(WhatnotOrder.id))) == 0
        assert shop["wshow"] not in results.results_due(shop["org"])  # never asked: > 7 days
    slot_free(app, shop, "shows")
    with app.app_context():
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        assert db.session.scalar(select(db.func.count(WhatnotOrder.id))) == 3
        assert ws_of(shop).orders_synced_at > end + timedelta(days=5)  # read again, now
        # Once: the next pass does not list it again.
        before = ws_of(shop).orders_synced_at
        assert sync._unread_shows(shop["org"], []) == []
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        assert ws_of(shop).orders_synced_at == before


def test_the_one_time_repair_waits_while_a_show_is_on_air_and_never_costs_it_a_call(
    app, shop, timer, monkeypatch
):
    end = ended(app, shop, timedelta(days=10))
    with app.app_context():
        ws_of(shop).orders_synced_at = end + timedelta(days=1)  # "read" by the capped pass
        live = WhatnotShow(
            organization_id=shop["org"],
            whatnot_id="on-air",
            status="live",
            started_at=datetime.now(UTC) - timedelta(minutes=20),
        )
        db.session.add(live)
        db.session.commit()
        sync.begin_repair(shop["org"])
        assert sync._unread_shows(shop["org"], [live]) == []  # waits for the show to end
        assert ws_of(shop).orders_synced_at == end + timedelta(days=1)  # nothing was reset
        assert sync._unread_shows(shop["org"], []) == [ws_of(shop)]  # the show is over: now
    # And through the timer: a live show means no list read at all for the repair.
    calls = []
    real = WhatnotClient.orders
    monkeypatch.setattr(
        WhatnotClient, "orders", lambda self, **kw: calls.append(1) or real(self, **kw)
    )
    slot_free(app, shop, "shows")
    with app.app_context():
        live = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == "on-air"))
        live.started_at = datetime.now(UTC) - timedelta(minutes=20)
        # Webhooks are arriving, so the timer does not poll the live show's own orders.
        db.session.scalar(select(WhatnotConnection)).last_event_at = datetime.now(UTC)
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
    assert calls == []


def test_shows_the_dashboard_counts_are_read_by_the_timer(app, shop):
    """The dashboard's money is the last 30 days: the timer reads ended shows that far back."""
    assert sync.RECENT_SHOWS >= timedelta(days=30)


# ---------- items of orders held with none ----------


def test_one_order_whose_items_whatnot_refuses_never_stops_the_show_being_read(
    app, shop, monkeypatch
):
    bare = order(shop, "buyer_zz", "Lot z")
    for it in shop["fake"].items_of(bare["id"]):
        del shop["fake"].order_items[it["id"]]
    order(shop, "buyer_yy", "Lot y")
    read(app, shop)
    real = WhatnotClient.order_items

    def refuse(self, oid):
        if oid == bare["id"]:
            raise Invalid("no")
        return real(self, oid)

    monkeypatch.setattr(WhatnotClient, "order_items", refuse)
    with app.app_context():
        ws_of(shop).orders_synced_at = None
        db.session.commit()
    for _ in range(2):
        read(app, shop)  # does not raise
    with app.app_context():
        assert ws_of(shop).orders_synced_at is not None


def test_orders_with_no_items_are_all_asked_in_turn_not_the_same_few_forever(
    app, shop, monkeypatch
):
    made = [order(shop, f"buyer_{i}", f"Lot {i}") for i in range(25)]
    for o in made:
        for it in shop["fake"].items_of(o["id"]):
            del shop["fake"].order_items[it["id"]]
    read(app, shop)
    asked: list[str] = []
    real = WhatnotClient.order_items

    def spy(self, oid):
        asked.append(oid)
        return real(self, oid)

    monkeypatch.setattr(WhatnotClient, "order_items", spy)
    read(app, shop)
    first = list(asked)
    read(app, shop)
    assert len(first) == sync.MAX_ITEM_REPAIRS
    assert {o["id"] for o in made} <= set(asked)  # every one asked within two passes


def test_orders_held_with_no_items_are_not_asked_while_a_show_is_on_air(app, shop, monkeypatch):
    o = order(shop, "buyer_zz", "Lot z")
    for it in shop["fake"].items_of(o["id"]):
        del shop["fake"].order_items[it["id"]]
    read(app, shop)
    with app.app_context():
        db.session.add(
            WhatnotShow(
                organization_id=shop["org"],
                whatnot_id="on-air",
                status="live",
                started_at=datetime.now(UTC) - timedelta(minutes=20),
            )
        )
        db.session.commit()
    asked: list[str] = []
    monkeypatch.setattr(WhatnotClient, "order_items", lambda self, oid: asked.append(oid) or [])
    read(app, shop)
    assert asked == []


# ---------- results held are never wiped ----------


def test_generated_results_are_not_wiped_by_a_later_answer_with_nothing_in_it(app, shop):
    ended(app, shop, timedelta(hours=3))
    set_results(shop, GENERATED)
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        db.session.commit()
        held = dict(ws.analytics)
        assert held["orders_count"] == 226
    for answer in (
        {"calculation_status": "WILL_NOT_BE_CALCULATED"},
        {"calculation_status": "GENERATED"},
    ):
        set_results(shop, answer)
        with app.app_context():
            ws = ws_of(shop)
            results.read_results(shop["org"], client_for(shop["org"]), ws)
            db.session.commit()
            assert ws.analytics == held and ws.analytics_status == "GENERATED"
    set_results(shop, {**GENERATED, "orders_count": 230})  # a newer GENERATED answer replaces
    with app.app_context():
        ws = ws_of(shop)
        results.read_results(shop["org"], client_for(shop["org"]), ws)
        assert ws.analytics["orders_count"] == 230


# ---------- the open-order refresh when every order is refused ----------


def test_a_pass_where_every_open_order_is_refused_is_a_failure_not_a_success(
    app, shop, monkeypatch
):
    for i in range(3):
        order(shop, f"buyer_{i}", "Red bundle", status="pending")
    read(app, shop)

    def forbid(self, order_id):
        raise Forbidden("no")

    monkeypatch.setattr(WhatnotClient, "order", forbid)
    with app.app_context():
        with pytest.raises(Forbidden):
            sync.refresh_open_orders(shop["org"], client_for(shop["org"]))


# ---------- a stored order is not taken out by a failing items call ----------


def test_an_order_already_held_is_never_taken_out_when_its_items_cannot_be_read(
    app, shop, monkeypatch
):
    o = order(shop, "buyer_a", "Red bundle")
    read(app, shop)
    monkeypatch.setattr(
        WhatnotClient, "order_items", lambda self, oid: (_ for _ in ()).throw(Unavailable("down"))
    )
    with app.app_context():
        client = client_for(shop["org"])
        with pytest.raises(Unavailable):
            sync.store_order(shop["org"], client, client.order(o["id"]), items=True)
        db.session.rollback()
        assert db.session.scalar(select(db.func.count(WhatnotOrder.id))) == 1


def test_a_new_order_is_new_once_whoever_stores_it_first(app, shop):
    o = order(shop, "buyer_a", "Red bundle")
    with app.app_context():
        data = client_for(shop["org"]).order(o["id"])
        row_id, new = sync.upsert_order(shop["org"], data)
        again, new_again = sync.upsert_order(shop["org"], data)
        assert (new, new_again, row_id == again) == (True, False, True)


# ---------- a catch-up that cannot get anywhere is not repeated every sweep ----------


def test_a_walk_that_hits_its_page_limit_with_nothing_new_is_spaced_out(
    app, shop, timer, monkeypatch
):
    older = two_shows(app, shop)
    for i in range(4):
        order(shop, f"new_buyer_{i}", "Red bundle")  # the newer show: 4 orders, all held
    for i in range(3):
        shop["fake"].add_order(
            show_id=older,
            buyer=f"old_buyer_{i}",
            items=[{"title": "Blue bundle", "price_cents": 900}],
            created=shop["now"] - timedelta(days=5, hours=2, minutes=i),
        )
    with app.app_context():  # both shows read in full, then the older one "unread" again
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        sync.sync_orders_of(
            shop["org"], client, list(db.session.scalars(select(WhatnotShow)).all())
        )
        old = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == older))
        old.orders_synced_at = None
        db.session.commit()
    monkeypatch.setattr(sync, "MAX_LISTED_PAGES", 0.12)  # six orders a walk of seven
    walks = []
    real = WhatnotClient.orders
    monkeypatch.setattr(
        WhatnotClient, "orders", lambda self, **kw: walks.append(1) or real(self, **kw)
    )
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        old = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == older))
        out = sync.sync_orders_of(shop["org"], client, [old])
        db.session.commit()
        assert out["stalled"] is True and out["complete"] is False
        assert old.orders_synced_at is None
    slot_free(app, shop, "shows")
    for _ in range(3):
        with app.app_context():
            sync.sync_job({"org_id": shop["org"]})
            db.session.commit()
    # The first sweep walked (and stalled); the next two left the old show alone.
    assert len(walks) == 2  # the direct call above + one sweep
    with app.app_context():
        state = sync.connection.sync_state(shop["org"], "orders:stalled")
        state.last_started_at -= sync.STALLED_RETRY + timedelta(minutes=1)
        sync.sync_job({"org_id": shop["org"]})
    assert len(walks) == 3  # due again after six hours


def test_a_failed_orders_read_does_not_starve_the_rest_of_the_pass_and_is_still_retried(
    app, shop, timer, monkeypatch
):
    set_auto(app)
    stuck = order(shop, "buyer_old", "Old lot", status="pending")
    read(app, shop)
    shop["fake"].set_order_status(stuck["id"], "failed")
    order(shop, "buyer_new", "New lot")
    failing_items_after(monkeypatch, 0)  # every new order's items call fails
    with app.app_context():
        ws_of(shop).orders_synced_at = None
        db.session.execute(
            WhatnotOrder.__table__.update().values(synced_at=datetime.now(UTC) - 2 * HOUR)
        )
        db.session.query(Job).delete()
        db.session.commit()
    slot_free(app, shop, "shows", "open_orders")
    with app.app_context():
        with pytest.raises(Unavailable):  # raised at the END: the job is retried
            sync.sync_job({"org_id": shop["org"]})
        db.session.rollback()
        # The open orders behind it were still read, and the order that moved is applied.
        assert (
            db.session.scalar(
                select(WhatnotOrder.status).where(WhatnotOrder.whatnot_id == stuck["id"])
            )
            == "failed"
        )
        assert shop["wshow"] in apply_jobs()


def test_one_order_whatnot_finds_invalid_alone_is_skipped_not_a_failed_pass(app, shop, monkeypatch):
    order(shop, "buyer_a", "Red bundle", status="pending")
    read(app, shop)

    def invalid(self, order_id):
        raise Invalid("no")

    monkeypatch.setattr(WhatnotClient, "order", invalid)
    with app.app_context():
        assert sync.refresh_open_orders(shop["org"], client_for(shop["org"])) == 1
