"""The older-orders backfill (`services/whatnot/backfill.py`).

The normal catch-up reads Whatnot's orders newest first only down to the oldest unread
show, and stops at 3,000 orders of any kind; the dashboard then added up a month that was
missing about a fifth of its paid orders. The backfill reads down to the dashboard's
longest period with no cap, stores what the app does not hold, and says how far back the
figures are complete. Against the in-memory fake; every buyer and amount is made up.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db import db
from app.models import (
    AuditLog,
    Buyer,
    Job,
    Sale,
    WhatnotConnection,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShow,
    WhatnotSyncState,
)
from app.services import dashboard, jobs
from app.services.whatnot import backfill, client_for, make_client, sync
from app.services.whatnot.client import MAX_ATTEMPTS, Response, WhatnotError
from tests import test_whatnot_orders as wo

shop = wo.shop  # the connected shop, its show matched to an ended show on Whatnot
read = wo.read

KIND = "whatnot_orders_backfill"
DAY = timedelta(days=1)


@pytest.fixture(autouse=True)
def _unpaced(monkeypatch):
    """The shared 8-requests-a-second limiter sleeps for real: not in tests."""
    from app.services.whatnot import throttle

    monkeypatch.setattr(throttle, "acquire", lambda *a, **k: None)


def put(shop, n, *, newest, every=timedelta(minutes=1), who="old", price=1000, **kw):
    """`n` orders made `every` apart, the newest at `newest`, each of one lot."""
    kw.setdefault("show_id", "old-show")
    kw.setdefault("shipping_cents", 0)
    return [
        shop["fake"].add_order(
            buyer=f"{who}_{i}",
            items=[{"title": f"Lot {i}", "price_cents": price}],
            created=newest - every * i,
            **kw,
        )
        for i in range(n)
    ]


def stored(app):
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(WhatnotOrder))


def state(app, shop):
    with app.app_context():
        row = db.session.scalar(
            select(WhatnotSyncState).where(
                WhatnotSyncState.organization_id == shop["org"],
                WhatnotSyncState.resource == backfill.RESOURCE,
            )
        )
        return row and (row.cursor, row.counts, row.last_error)


def go(app, shop, **kw):
    """One slice of the backfill, as the job runs it (no throttle: tests are not paced)."""
    with app.app_context():
        counts = backfill.run(shop["org"], make_client(), **kw)
        db.session.commit()
        return counts


def money(app, shop, days=30):
    with app.app_context():
        return dashboard.whatnot_money(shop["org"], days=days)


def jobs_of(app, kind):
    with app.app_context():
        return db.session.scalars(select(Job).where(Job.kind == kind).order_by(Job.id)).all()


WAITS = ("whatnot_apply_show", "buyers_link")  # jobs that ask to wait a few seconds


def drain(app, *, wait=True):
    """The worker: every job that is due, until none is. With `wait`, the jobs in `WAITS`
    are taken as due (the others, a report being built, wait for real)."""
    ran = []
    with app.app_context():
        while True:
            if wait:
                db.session.execute(
                    update(Job)
                    .where(Job.status == "queued", Job.kind.in_(WAITS))
                    .values(run_at=datetime.now(UTC))
                )
                db.session.commit()
            if (job := jobs.run_once("t")) is None:
                return ran
            ran.append((job.kind, job.status.value))


# ---------- what it stores ----------


def test_it_stores_orders_the_normal_catch_up_never_reaches_and_the_tiles_add_them(app, shop):
    """The normal read stops at the show's start; the backfill goes on to the floor. Orders
    of 12, 45 and 75 days ago are paid sales for the 30- and 90-day tiles; one of 100 days
    ago is beyond the dashboard's longest period and is not read."""
    now = shop["now"]
    wo.order(shop, "mika_r", "Red bundle", 1500)
    shop["fake"].add_order(
        show_id="s12", buyer="jo_b", items=[{"title": "Lot", "price_cents": 2000, "quantity": 2}],
        created=now - 12 * DAY,
    )  # fmt: skip
    put(shop, 1, newest=now - 45 * DAY, who="ana", price=3000)
    put(shop, 1, newest=now - 75 * DAY, who="vee", price=4000)
    put(shop, 1, newest=now - 100 * DAY, who="cad", price=9900)
    read(app, shop)
    assert stored(app) == 1  # the show's own order: nothing older is reached
    assert money(app, shop, 30)["sales"]["paidCents"] == 1500
    assert money(app, shop, 90)["sales"]["paidCents"] == 1500

    counts = go(app, shop)

    assert counts["complete"] is True
    assert stored(app) == 4  # the 100-day-old order is below the floor
    thirty = money(app, shop, 30)["sales"]
    assert (thirty["paidCents"], thirty["orders"], thirty["items"]) == (1500 + 4000, 2, 3)
    ninety = money(app, shop, 90)["sales"]
    assert (ninety["paidCents"], ninety["orders"], ninety["items"]) == (12500, 4, 5)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 4


def test_the_floor_is_the_dashboards_longest_period(app, shop):
    assert backfill.FLOOR_DAYS == max(dashboard.PERIODS)


def test_it_refreshes_orders_it_holds_and_never_makes_a_second_copy(app, shop):
    now = shop["now"]
    [o] = put(shop, 1, newest=now - 20 * DAY, status="pending", price=2500)
    go(app, shop)
    assert money(app, shop)["sales"]["pendingCents"] == 2500
    shop["fake"].set_order_status(o["id"], "completed")
    aged(app, shop, until_ago=100 * DAY)  # a gap too long for a top-up: a full run again
    counts = go(app, shop)
    got = money(app, shop)["sales"]
    assert (stored(app), got["paidCents"], got["pendingCents"]) == (1, 2500, 0)
    # A full run sets the older end of the claim afresh, to its own floor.
    floor = datetime.fromisoformat(counts["completeSince"])
    assert abs(datetime.now(UTC) - 90 * DAY - floor) < timedelta(minutes=5)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 1


def test_more_than_three_thousand_orders_are_all_stored(app, shop):
    """The normal catch-up gives up at 3,000 orders of any kind, failed payments and
    giveaways included; a month of this shop holds about 6,000."""
    assert sync.MAX_ORDER_PAGES * 50 == 3000
    fake = shop["fake"]
    put(shop, 2600, newest=shop["now"] - 2 * DAY, price=1000)
    put(shop, 300, newest=shop["now"] - 3 * DAY, price=0, status="failed", who="fail")
    put(shop, 200, newest=shop["now"] - 4 * DAY, price=0, transaction_type="giveaway", who="gift")
    assert len(fake.orders) == 3100

    counts = go(app, shop)

    assert counts["complete"] is True and stored(app) == 3100
    # The list ends before the floor (a young shop): a second read from the top confirms the
    # end is real (`test_a_page_that_ends_the_list_early_is_not_taken_as_the_floor`).
    assert len([c for c in fake.calls if c == ("GET", "/orders")]) == 2 * (3100 // 50)
    got = money(app, shop, 30)["sales"]
    assert (got["orders"], got["paidCents"], got["giveaways"]) == (2600, 2_600_000, 200)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 3100


# ---------- failures, slices, resuming ----------


def test_a_failure_keeps_what_was_read_and_the_next_run_completes_without_duplicates(
    app, shop, monkeypatch
):
    monkeypatch.setattr(backfill, "BATCH", 20)
    fake = shop["fake"]
    orders = put(shop, 70, newest=shop["now"] - 5 * DAY)
    inner, down = fake.transport, {"on": True}

    def flaky(method, url, *, headers, params, json):
        if down["on"] and sum(1 for _, p in fake.calls if p.endswith("/items")) >= 45:
            return Response(503, {"type": "unavailable"})  # Whatnot goes down mid-run
        return inner(method, url, headers=headers, params=params, json=json)

    app.config["WHATNOT_TRANSPORT"] = flaky
    with app.app_context(), pytest.raises(WhatnotError):
        backfill.backfill_job({"org_id": shop["org"]})
    kept = stored(app)
    assert kept == 40  # two whole batches of 20 were committed; the third was undone
    cursor, counts, error = state(app, shop)
    assert error and not (counts or {}).get("complete")
    assert cursor["reached"]
    assert money(app, shop)["ordersCompleteSince"] is None  # an unfinished run proves nothing

    down["on"] = False
    item_reads_before = {p for _, p in fake.calls if p.endswith("/items")}
    fake.calls.clear()
    with app.app_context():
        backfill.backfill_job({"org_id": shop["org"]})
    assert stored(app) == 70
    _, counts, error = state(app, shop)
    assert counts["complete"] is True and error is None
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 70
        assert db.session.scalar(select(func.count(func.distinct(WhatnotOrder.whatnot_id)))) == 70
    # What was kept is not read again, item by item.
    again = {p for _, p in fake.calls if p.endswith("/items")}
    kept_ids = {f"/orders/{o['id']}/items" for o in orders[:kept]}
    assert again.isdisjoint(kept_ids) and len(again) == 70 - kept
    assert item_reads_before  # the first run did read items


def test_a_slice_ends_at_its_budget_and_the_job_asks_for_the_next_one(app, shop, monkeypatch):
    monkeypatch.setattr(backfill, "BATCH", 10)
    monkeypatch.setattr(backfill, "RUN_SECONDS", 0)  # one batch per slice
    monkeypatch.setattr(backfill, "NEXT_SLICE_S", 0)
    put(shop, 35, newest=shop["now"] - 5 * DAY)
    with app.app_context():
        backfill.backfill_job({"org_id": shop["org"]})
    assert 10 <= stored(app) < 35
    _, counts, _ = state(app, shop)
    assert counts["complete"] is False
    [nxt] = jobs_of(app, KIND)  # the next slice, queued by the one that just ended
    assert nxt.status.value == "queued" and nxt.dedupe_key == f"{KIND}:{shop['org']}"
    ran = drain(app)  # each slice queues the next until the floor is reached
    assert ran and all(status == "done" for _, status in ran)
    assert stored(app) == 35
    assert state(app, shop)[1]["complete"] is True
    assert not [j for j in jobs_of(app, KIND) if j.status.value == "queued"]


def test_resuming_skips_what_the_last_slice_stored(app, shop, monkeypatch):
    monkeypatch.setattr(backfill, "BATCH", 10)
    fake = shop["fake"]
    orders = put(shop, 30, newest=shop["now"] - 5 * DAY)
    go(app, shop, budget_s=0)
    first = stored(app)
    assert first == 10
    fake.calls.clear()
    go(app, shop, budget_s=0)
    assert stored(app) == 20
    items = [p for _, p in fake.calls if p.endswith("/items")]
    assert items == [f"/orders/{o['id']}/items" for o in orders[10:20]]  # ten new, none again


# ---------- a show on air ----------


def test_it_does_not_start_while_a_show_is_live(app, shop):
    live = shop["fake"].add_show(title="Live now", status="live", start=shop["now"])
    put(shop, 5, newest=shop["now"] - 5 * DAY)
    with app.app_context():
        sync.upsert_show(shop["org"], live)
        db.session.commit()
        assert backfill.on_air(shop["org"]) is True
        shop["fake"].calls.clear()
        backfill.backfill_job({"org_id": shop["org"]})
        assert backfill.due(shop["org"]) is False
    assert stored(app) == 0
    assert not [c for c in shop["fake"].calls if c[1].startswith("/orders")]
    assert not jobs_of(app, KIND)  # it asks for nothing: the timer brings it back


def test_it_stops_cleanly_when_a_show_goes_live_and_resumes_after_it(app, shop, monkeypatch):
    monkeypatch.setattr(backfill, "BATCH", 10)
    monkeypatch.setattr(backfill, "LIVE_CHECK_EVERY", 0)  # look at Whatnot's live shows each batch
    fake = shop["fake"]
    fake.page_size_cap = 10  # a page a batch: the show goes live while the second is read
    put(shop, 45, newest=shop["now"] - 5 * DAY)
    inner, pages = fake.transport, {"orders": 0}
    live = {}

    def goes_live(method, url, *, headers, params, json):
        if url.endswith("/orders"):
            pages["orders"] += 1
            if pages["orders"] == 2 and not live:  # the host presses Go Live mid-read
                live.update(fake.add_show(title="Live now", status="live", start=shop["now"]))
        return inner(method, url, headers=headers, params=params, json=json)

    app.config["WHATNOT_TRANSPORT"] = goes_live
    with app.app_context():
        backfill.backfill_job({"org_id": shop["org"]})
    assert 0 < stored(app) < 45 and live
    cursor, counts, error = state(app, shop)
    assert counts["complete"] is False and error is None and cursor["reached"]
    assert not [j for j in jobs_of(app, KIND) if j.status.value == "queued"]  # waits for the timer
    with app.app_context():
        assert backfill.on_air(shop["org"]) is True
        assert backfill.due(shop["org"]) is False

    # The show ends; the timer's show list says so; the backfill picks up where it was.
    fake.shows[live["id"]]["status"] = "ended"
    fake.shows[live["id"]]["ended_at"] = fake.shows[live["id"]]["scheduled_start_at"]
    done = stored(app)
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        db.session.commit()
        assert backfill.on_air(shop["org"]) is False
        fake.calls.clear()
        backfill.backfill_job({"org_id": shop["org"]})
    assert state(app, shop)[1]["complete"] is True and stored(app) == 45
    assert len([p for _, p in fake.calls if p.endswith("/items")]) == 45 - done


# ---------- what it must not do ----------


def test_it_reads_only_it_marks_no_show_read_makes_no_sale_and_queues_no_apply(app, shop):
    """Orders mode "auto" applies a show's orders as they arrive; a backfilled show is
    history and is never applied on its own. Run as the worker runs it: the job, then
    every job it queued."""
    now = shop["now"]
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        conn.orders_mode, conn.auto_since = "auto", now - 30 * DAY
        db.session.commit()
    read(app, shop, orders=False)  # the show list, no orders
    wo.order(shop, "mika_r", "Red bundle", 1500)  # an order of the shop's own show
    put(shop, 6, newest=now - 20 * DAY, show_id="gone-show", who="old")
    before = wo.snapshot(app)
    with app.app_context():
        audits = db.session.scalar(select(func.count()).select_from(AuditLog))
        backfill.schedule(shop["org"])
        db.session.commit()

    ran = drain(app)

    assert (KIND, "done") in ran and all(status == "done" for _, status in ran)
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow))
        assert ws.orders_synced_at is None  # never read in full: the backfill does not say so
        assert db.session.scalar(select(func.count()).select_from(Sale)) == 0
        assert db.session.scalar(select(func.count()).select_from(AuditLog)) == audits
    assert wo.snapshot(app) == before  # no sale, no stock movement, no review item
    assert stored(app) == 7
    assert "whatnot_apply_show" not in {kind for kind, _ in ran}
    assert "whatnot_apply_show" not in {j.kind for j in jobs_of_all(app)}


def test_an_order_whatnot_had_not_settled_is_left_to_the_timer_so_a_cancel_is_still_applied(
    app, shop
):
    """In "auto" mode the timer finds a settled order by comparing the open orders before
    and after its re-read, then applies the show. A backfill that settled the order first
    would leave it nothing to compare: the sale would keep its stock."""
    shop["say"](app, [wo.win("lani808", "Red bundle")])
    wo.run(app, shop["show"])
    o = wo.order(shop, "lani808", "Red bundle", status="pending")
    read(app, shop)
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        conn.orders_mode, conn.auto_since = "auto", conn.connected_at
        db.session.commit()
    shop["fake"].set_order_status(o["id"], "cancelled")  # while nobody was looking

    with app.app_context():
        backfill.backfill_job({"org_id": shop["org"]})
        assert db.session.scalar(select(WhatnotOrder.status)) == "pending"  # not touched here
    timer(app, shop)
    drain(app)

    assert wo.sale_rows(app)[0][2:4] == ("cancelled", "cancelled on Whatnot")
    assert wo.level(app, shop["red"]) == (3, 0)


def jobs_of_all(app):
    with app.app_context():
        return db.session.scalars(select(Job)).all()


def test_old_orders_give_buyers_profiles_but_never_take_a_name_from_a_newer_holder(app, shop):
    """Linking buyers reads orders by date: an old order of another account that used the
    same name earlier changes nothing for the account that uses it now."""
    from app.services import buyers

    now = shop["now"]
    shop["fake"].add_order(
        show_id="s1", buyer="same_name", buyer_id="u-new", created=now - 2 * DAY,
        items=[{"title": "Lot", "price_cents": 1000}],
    )  # fmt: skip
    shop["fake"].add_order(
        show_id="s0", buyer="same_name", buyer_id="u-old", created=now - 40 * DAY,
        items=[{"title": "Lot", "price_cents": 1000}],
    )  # fmt: skip
    shop["fake"].add_order(
        show_id="s0", buyer="only_then", buyer_id="u-then", created=now - 41 * DAY,
        items=[{"title": "Lot", "price_cents": 1000}],
    )  # fmt: skip
    with app.app_context():
        client = client_for(shop["org"])
        sync.store_order(shop["org"], client, shop["fake"].orders[next(iter(shop["fake"].orders))])
        buyers.link(shop["org"])
        db.session.commit()
    with app.app_context():
        backfill.backfill_job({"org_id": shop["org"]})
    assert jobs_of(app, "buyers_link")  # queued, soon and once, for after the slice
    with app.app_context():
        buyers.link(shop["org"])
        db.session.commit()
        holders = {b.whatnot_user_id: b.username for b in db.session.scalars(select(Buyer))}
        assert holders["u-then"] == "only_then"
        held = buyers.holders(shop["org"], ["same_name"])["same_name"]
        assert held.whatnot_user_id == "u-new"


# ---------- when it runs ----------


def timer(app, shop):
    with app.app_context():
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()


def test_the_timer_queues_it_once_and_not_again_after_it_completed(app, shop):
    put(shop, 5, newest=shop["now"] - 5 * DAY)
    timer(app, shop)
    timer(app, shop)  # the next pass, before the worker has started it
    [first] = jobs_of(app, KIND)
    assert first.status.value == "queued" and first.payload == {"org_id": shop["org"]}

    ran = drain(app)
    assert (KIND, "done") in ran and stored(app) == 5
    assert state(app, shop)[1]["complete"] is True
    with app.app_context():  # within the day, many passes
        db.session.query(WhatnotSyncState).update({"last_started_at": datetime.now(UTC) - 3 * DAY})
        db.session.commit()
    timer(app, shop)
    timer(app, shop)
    drain(app)
    assert len(jobs_of(app, KIND)) == 1


def test_the_timer_does_not_queue_it_while_a_show_is_on_air(app, shop):
    live = shop["fake"].add_show(title="Live now", status="live", start=shop["now"])
    with app.app_context():
        sync.upsert_show(shop["org"], live)
        db.session.commit()
    timer(app, shop)
    assert not jobs_of(app, KIND)


def test_after_a_failure_the_timer_waits_before_asking_again(app, shop):
    """Not every five-minute pass: a failed read is paced like the others, by when it last
    started."""
    put(shop, 5, newest=shop["now"] - 5 * DAY)
    for _ in range(MAX_ATTEMPTS):  # the first page fails every time it is tried
        shop["fake"].fail_next(500)
    with app.app_context(), pytest.raises(WhatnotError):
        backfill.backfill_job({"org_id": shop["org"]})
    with app.app_context():
        assert state(app, shop)[2]
        assert backfill.due(shop["org"]) is False
        row = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == backfill.RESOURCE)
        )
        row.last_started_at = datetime.now(UTC) - backfill.RETRY_AFTER - timedelta(minutes=1)
        db.session.commit()
        assert backfill.due(shop["org"]) is True


# ---------- how far back the tile can say it is complete ----------


def test_the_tile_says_how_far_back_orders_are_complete(app, shop):
    now = shop["now"]
    put(shop, 1, newest=now - 3 * DAY, who="new", price=1000)
    # A stray old order, as the shop's first connection left: it used to be "known since".
    put(shop, 1, newest=now - 120 * DAY, who="stray", price=500)
    with app.app_context():
        client = client_for(shop["org"])
        for data in shop["fake"].orders.values():
            if data["buyer"]["username"] == "stray_0":
                sync.store_order(shop["org"], client, data)
        db.session.commit()
    before = money(app, shop, 30)
    assert before["ordersCompleteSince"] is None
    # An instant, which the database writes out in its own session's zone: compare it as an
    # instant. (Its date part against a UTC date failed from 00:00 to 04:00 UTC on a machine
    # in New York; the dashboard reads it as an instant and shows it in the shop's zone.)
    known = datetime.fromisoformat(before["customers"]["knownSince"])
    assert abs(known - (now - 120 * DAY)) < timedelta(minutes=1)

    go(app, shop)

    after = money(app, shop, 30)
    floor = datetime.fromisoformat(after["ordersCompleteSince"])
    assert abs((now - 90 * DAY) - floor) < timedelta(minutes=5)
    # "Returning" can see back to where the orders are complete, not to a stray row.
    assert after["customers"]["knownSince"] == after["ordersCompleteSince"]
    assert money(app, shop, 90)["ordersCompleteSince"] == after["ordersCompleteSince"]
    with app.app_context():
        assert backfill.complete_since(shop["org"]) == floor


# ---------- review fixes: the sentence on the tile must stay true ----------


def aged(app, shop, *, until_ago=timedelta(hours=25), **extra):
    """Make the last finished run look `until_ago` old (and anything else in `extra`)."""
    with app.app_context():
        row = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == backfill.RESOURCE)
        )
        counts = dict(row.counts)
        counts["completeUntil"] = (datetime.now(UTC) - until_ago).isoformat()
        counts.update(extra)
        row.counts = counts
        row.last_started_at = datetime.now(UTC) - until_ago
        db.session.commit()


def order_between_shows(shop, who="between_shows", price=7700, minutes_ago=1):
    return shop["fake"].add_order(
        show_id=None, buyer=who, items=[{"title": "Buy now", "price_cents": price}],
        created=datetime.now(UTC) - timedelta(minutes=minutes_ago), status="completed",
    )  # fmt: skip


def held_buyers(app):
    with app.app_context():
        return set(db.session.scalars(select(WhatnotOrder.buyer_username)))


def test_the_claim_has_two_ends_and_the_newer_one_is_when_that_run_began(app, shop):
    """A run proves every order from its floor to the moment it began: orders placed while
    it ran sit above the point later slices resume from, so they are not part of the claim."""
    put(shop, 3, newest=shop["now"] - 20 * DAY)
    before = datetime.now(UTC)
    go(app, shop)
    got = money(app, shop, 30)
    until = datetime.fromisoformat(got["ordersCompleteUntil"])
    assert before - timedelta(seconds=5) <= until <= datetime.now(UTC)
    _, counts, _ = state(app, shop)
    assert counts["completeUntil"] == got["ordersCompleteUntil"]
    assert datetime.fromisoformat(counts["completedAt"]) >= until


def test_an_order_placed_while_a_run_was_reading_is_not_in_its_claim_until_the_top_up(
    app, shop, monkeypatch
):
    """Slices resume below the point the last one stopped, so an order placed after the run
    began (between shows, so no show's read catches it) is never seen by that run. The claim
    must end where the run began, not where it finished; the top-up then reads it."""
    monkeypatch.setattr(backfill, "BATCH", 10)
    put(shop, 30, newest=shop["now"] - 20 * DAY, price=1000)
    first = go(app, shop, budget_s=0)
    assert first["finished"] is False  # the run is under way
    placed = datetime.now(UTC)
    shop["fake"].add_order(
        show_id=None, buyer="mid_run", items=[{"title": "Buy now", "price_cents": 7700}],
        created=placed, status="completed",
    )  # fmt: skip
    for _ in range(10):
        counts = go(app, shop, budget_s=0)
        if counts["finished"]:
            break
    assert counts["finished"] is True and stored(app) == 30
    assert "mid_run" not in held_buyers(app)
    until = datetime.fromisoformat(money(app, shop, 30)["ordersCompleteUntil"])
    assert until < placed  # the claim stops where the run began

    aged(app, shop)
    go(app, shop)
    assert "mid_run" in held_buyers(app)
    assert datetime.fromisoformat(money(app, shop, 30)["ordersCompleteUntil"]) > placed


def test_a_slice_asks_whatnot_for_live_shows_before_reading_any_order(app, shop):
    """The database learns of a show going live from a webhook or the timer, neither of
    which runs while a slice holds the worker: a show Whatnot has live that the database
    does not know yet stops the slice before its first read of the orders."""
    put(shop, 5, newest=shop["now"] - 5 * DAY)
    shop["fake"].add_show(title="Live now", status="live", start=shop["now"])
    with app.app_context():
        assert backfill.on_air(shop["org"]) is False  # the database does not know
    counts = go(app, shop)
    assert counts["paused"] is True and stored(app) == 0
    assert not [c for c in shop["fake"].calls if c == ("GET", "/orders")]


def test_an_order_between_shows_after_completion_is_read_by_the_daily_top_up(app, shop):
    """The normal reads only look inside a show's window: a buy-now placed between shows
    must still reach the tile, and the sentence on it must say how far it was checked."""
    put(shop, 3, newest=shop["now"] - 20 * DAY, price=1000)
    read(app, shop)  # the shop's ended show was read in full
    go(app, shop)
    since_before = money(app, shop, 30)["ordersCompleteSince"]
    order_between_shows(shop)
    for _ in range(3):  # passes of the timer before a day has gone by: nothing is asked
        timer(app, shop)
        assert KIND not in {kind for kind, _ in drain(app)}
    assert not jobs_of(app, KIND)
    # (A re-read of the ended show may already have stored the order on its way down from
    # the newest; what matters below is that the top-up moves the claim past it.)

    aged(app, shop)  # a day later
    shop["fake"].calls.clear()
    timer(app, shop)
    drain(app)

    assert "between_shows" in held_buyers(app)
    got = money(app, shop, 30)
    assert got["sales"]["paidCents"] == 3000 + 7700
    assert got["ordersCompleteSince"] == since_before  # the older end never moves forward
    assert datetime.now(UTC) - datetime.fromisoformat(got["ordersCompleteUntil"]) < timedelta(
        minutes=5
    )  # the newer end did
    pages = [c for c in shop["fake"].calls if c == ("GET", "/orders")]
    # A top-up reads days, not 90 of them: here one page, read twice, because the list ends
    # after only three orders older than its floor, which is not a page's worth in a row, so
    # a second read must end on the same order (PR 79 review, C3).
    assert len(pages) == 2
    with app.app_context():
        assert backfill.due(shop["org"]) is False  # and not again until the next day


def test_a_top_up_that_fails_or_stops_moves_neither_end_of_the_claim(app, shop, monkeypatch):
    put(shop, 3, newest=shop["now"] - 20 * DAY)
    go(app, shop)
    aged(app, shop)
    claim = {k: v for k, v in money(app, shop, 30).items() if k.startswith("ordersComplete")}
    assert claim["ordersCompleteSince"] and claim["ordersCompleteUntil"]
    for i in range(3):
        order_between_shows(shop, who=f"between_{i}", minutes_ago=i + 1)
    for _ in range(MAX_ATTEMPTS):
        shop["fake"].fail_next(500)
    with app.app_context(), pytest.raises(WhatnotError):
        backfill.backfill_job({"org_id": shop["org"]})
    assert {
        k: v for k, v in money(app, shop, 30).items() if k.startswith("ordersComplete")
    } == claim
    # Stopped by its budget before it reached the floor: nothing moves either.
    monkeypatch.setattr(backfill, "BATCH", 1)
    counts = go(app, shop, budget_s=0)
    assert counts["finished"] is False
    assert {
        k: v for k, v in money(app, shop, 30).items() if k.startswith("ordersComplete")
    } == claim
    cursor, _, _ = state(app, shop)
    assert cursor is not None  # the unfinished top-up is kept to be carried on


def test_a_top_up_waits_for_the_show_to_end(app, shop):
    put(shop, 3, newest=shop["now"] - 20 * DAY)
    read(app, shop)
    go(app, shop)
    aged(app, shop)
    live = shop["fake"].add_show(title="Live now", status="live", start=shop["now"])
    with app.app_context():
        sync.upsert_show(shop["org"], live)
        db.session.commit()
        assert backfill.due(shop["org"]) is False
    timer(app, shop)
    assert not jobs_of(app, KIND)


def test_a_page_that_ends_the_list_early_is_not_taken_as_the_floor(app, shop):
    """`client.pages` ends the list on any 200 page with no next cursor. One odd answer in
    the middle must not become a permanent "every order back to": the list is read again
    from the top, and the end counts only when two reads agree where it is."""
    now = shop["now"]
    put(shop, 60, newest=now - 5 * DAY, price=1000)
    put(shop, 60, newest=now - 50 * DAY, who="older", price=1000)
    fake = shop["fake"]
    inner, n = fake.transport, {"orders": 0}

    def short_page(method, url, *, headers, params, json):
        if url.endswith("/orders"):
            n["orders"] += 1
            if n["orders"] == 2:  # one odd 200 answer mid-run (empty, no cursor)
                return Response(200, {"data": []})
        return inner(method, url, headers=headers, params=params, json=json)

    app.config["WHATNOT_TRANSPORT"] = short_page
    counts = go(app, shop)
    assert counts["complete"] is True and stored(app) == 120


def test_a_short_list_that_really_ends_before_the_floor_still_completes(app, shop):
    put(shop, 5, newest=shop["now"] - 5 * DAY)
    counts = go(app, shop)
    assert counts["complete"] is True and stored(app) == 5


def test_an_empty_shop_completes_after_two_agreeing_reads(app, shop):
    counts = go(app, shop)
    assert counts["complete"] is True and stored(app) == 0


def test_one_order_out_of_order_near_the_top_does_not_end_the_run(app, shop):
    """The floor is passed only after a page's worth of older orders in a row."""
    now = shop["now"]
    put(shop, 30, newest=now - 5 * DAY, price=1000)
    put(shop, 30, newest=now - 40 * DAY, who="older", price=1000)
    stray = put(shop, 1, newest=now - 100 * DAY, who="stray", price=1)[0]
    fake = shop["fake"]
    inner = fake.transport

    def stray_on_top(method, url, *, headers, params, json):
        r = inner(method, url, headers=headers, params=params, json=json)
        if url.endswith("/orders") and not params.get("cursor") and r.status == 200:
            data = [d for d in r.body["data"] if d["id"] != stray["id"]]
            data.insert(1, fake._order_view(fake.orders[stray["id"]]))
            return Response(200, {**r.body, "data": data})
        return r

    app.config["WHATNOT_TRANSPORT"] = stray_on_top
    counts = go(app, shop)
    assert counts["complete"] is True and stored(app) == 60
    assert "stray_0" not in held_buyers(app)  # below the floor: never stored


def test_a_list_that_ends_early_right_after_a_stray_old_order_is_not_taken_as_the_floor(app, shop):
    """PR 79 review (C3): one stray order older than the floor ends the first page, and the
    next page is one odd 200 answer (empty, no cursor). A few old orders at the end of the
    list prove nothing (only a page's worth in a row does): the end counts only when a
    second read ends on the same order."""
    now = shop["now"]
    put(shop, 60, newest=now - 5 * DAY, price=1000)
    put(shop, 60, newest=now - 50 * DAY, who="older", price=1000)
    stray = put(shop, 1, newest=now - 100 * DAY, who="stray", price=1)[0]
    fake = shop["fake"]
    inner, n = fake.transport, {"orders": 0}

    def stray_then_short_page(method, url, *, headers, params, json):
        if url.endswith("/orders"):
            n["orders"] += 1
            if n["orders"] == 2:  # one odd 200 answer right after the stray (empty, no cursor)
                return Response(200, {"data": []})
        r = inner(method, url, headers=headers, params=params, json=json)
        if url.endswith("/orders") and r.status == 200:
            data = [d for d in r.body["data"] if d["id"] != stray["id"]]
            if not params.get("cursor"):  # Whatnot lists the stray at the end of page one
                data.append(fake._order_view(fake.orders[stray["id"]]))
            return Response(200, {**r.body, "data": data})
        return r

    app.config["WHATNOT_TRANSPORT"] = stray_then_short_page
    counts = go(app, shop)
    assert counts["complete"] is True and stored(app) == 120
    assert "stray_0" not in held_buyers(app)  # below the floor: never stored


def test_an_out_of_order_order_below_the_resume_point_is_still_stored(app, shop, monkeypatch):
    """A resumed slice skips what is newer than where the last one stopped, but only until
    it has passed that point: a newer order listed further down was never stored."""
    monkeypatch.setattr(backfill, "BATCH", 10)
    now = shop["now"]
    orders = put(shop, 30, newest=now - 5 * DAY)
    late = shop["fake"].add_order(
        show_id="old-show", buyer="listed_late", created=now - 5 * DAY + timedelta(seconds=30),
        items=[{"title": "Lot", "price_cents": 1000}],
    )  # fmt: skip
    fake, inner = shop["fake"], shop["fake"].transport

    # Whatnot lists it deep in the list (index 25), not where its date puts it.
    def deep(method, url, *, headers, params, json):
        r = inner(method, url, headers=headers, params=params, json=json)
        if url.endswith("/orders") and r.status == 200:
            data = [d for d in r.body["data"] if d["id"] != late["id"]]
            if len(data) and data[-1]["id"] == orders[29]["id"]:
                data.append(fake._order_view(fake.orders[late["id"]]))
            return Response(200, {**r.body, "data": data})
        return r

    app.config["WHATNOT_TRANSPORT"] = deep
    go(app, shop, budget_s=0)
    assert "listed_late" not in held_buyers(app)
    for _ in range(5):
        go(app, shop, budget_s=0)
    assert "listed_late" in held_buyers(app)


def test_an_order_whose_items_do_not_exist_is_skipped_and_the_run_completes(app, shop):
    """Whatnot says one order has no items (404). It is left out, whole (no row without its
    items), and counted; it never stops every later run at the same place."""
    now = shop["now"]
    put(shop, 60, newest=now - 5 * DAY, price=1000)
    bad = put(shop, 1, newest=now - 40 * DAY, who="bad", price=1000)[0]
    put(shop, 60, newest=now - 41 * DAY, who="older", price=1000)
    fake = shop["fake"]
    inner = fake.transport

    def items_404(method, url, *, headers, params, json):
        if url.endswith(f"/orders/{bad['id']}/items"):
            return Response(404, {"type": "not-found"})
        return inner(method, url, headers=headers, params=params, json=json)

    app.config["WHATNOT_TRANSPORT"] = items_404
    counts = go(app, shop)
    assert counts["complete"] is True and counts["skipped"] == 1
    assert stored(app) == 120 and "bad_0" not in held_buyers(app)
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 120


def test_a_failing_run_is_asked_for_less_and_less_often(app, shop):
    """Not every hour for ever: each round of failed attempts doubles the wait (up to a day),
    and a slice that works forgets the failures."""
    put(shop, 5, newest=shop["now"] - 5 * DAY)
    r = backfill.retry_wait
    assert [r({"fails": n}) for n in (0, 1, 5, 6, 10, 11)] == [
        backfill.RETRY_AFTER * k for k in (1, 1, 1, 2, 2, 4)
    ]
    assert r({"fails": 10_000}) == backfill.MAX_RETRY_AFTER
    for _ in range(backfill.ATTEMPTS + 1):  # one round of the job's attempts, and one more
        for _ in range(MAX_ATTEMPTS):
            shop["fake"].fail_next(500)
        with app.app_context(), pytest.raises(WhatnotError):
            backfill.backfill_job({"org_id": shop["org"]})
    _, counts, _ = state(app, shop)
    assert counts["fails"] == backfill.ATTEMPTS + 1
    with app.app_context():
        row = db.session.scalar(
            select(WhatnotSyncState).where(WhatnotSyncState.resource == backfill.RESOURCE)
        )
        row.last_started_at = datetime.now(UTC) - backfill.RETRY_AFTER - timedelta(minutes=1)
        db.session.commit()
        assert backfill.due(shop["org"]) is False  # one hour is no longer enough
        row.last_started_at = datetime.now(UTC) - 2 * backfill.RETRY_AFTER - timedelta(minutes=1)
        db.session.commit()
        assert backfill.due(shop["org"]) is True
        backfill.backfill_job({"org_id": shop["org"]})  # Whatnot is back
    assert state(app, shop)[1]["fails"] == 0


@pytest.mark.parametrize("followed", [True, False])
def test_a_stored_open_order_is_applied_later_only_for_a_show_being_followed(app, shop, followed):
    """The backfill itself applies nothing. An unsettled order it stores for a show the shop
    follows in "auto" mode is then the timer's, like any order the normal read had caught:
    when it settles the show is applied. A show that was over before "auto" began is
    history and is never applied on its own."""
    read(app, shop)  # the show read in full: no order yet
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        conn.orders_mode = "auto"
        conn.auto_since = conn.connected_at if followed else datetime.now(UTC) + DAY
        db.session.commit()
    missed = wo.order(shop, "late_x", "Red bundle", status="pending_fulfillment")
    before = wo.snapshot(app)
    go(app, shop)
    assert wo.snapshot(app) == before  # the backfill itself changed nothing
    shop["fake"].set_order_status(missed["id"], "completed")
    timer(app, shop)
    drain(app)
    with app.app_context():
        made = [s.external_order_id for s in db.session.scalars(select(Sale))]
    assert (made == [missed["id"]]) if followed else (made == [])


def test_returning_counts_only_orders_from_where_they_are_complete(app, shop):
    """The sentence says the app sees back to the floor, so a stray order older than it must
    not make a buyer "returning" either."""
    now = shop["now"]
    stray = shop["fake"].add_order(
        show_id="s0", buyer="back_again", buyer_id="u-back", created=now - 120 * DAY,
        items=[{"title": "Lot", "price_cents": 1000}],
    )  # fmt: skip
    shop["fake"].add_order(
        show_id="s1", buyer="back_again", buyer_id="u-back", created=now - 10 * DAY,
        items=[{"title": "Lot", "price_cents": 2000}],
    )  # fmt: skip
    with app.app_context():  # the stray row the shop's first connection left behind
        sync.store_order(shop["org"], client_for(shop["org"]), stray)
        db.session.commit()
    before = money(app, shop, 90)
    assert before["ordersCompleteSince"] is None
    go(app, shop)
    ninety = money(app, shop, 90)
    assert ninety["ordersCompleteSince"] is not None
    assert (ninety["customers"]["returning"]["buyers"], ninety["customers"]["new"]["buyers"]) == (
        0,
        1,
    )
