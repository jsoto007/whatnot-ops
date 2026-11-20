"""Older orders: read Whatnot's orders back to the dashboard's longest period, with no cap,
and keep them read.

The normal catch-up (`sync.sync_orders_of`) reads newest first down to the oldest unread
show and gives up at 3,000 orders of ANY kind (failed payments and giveaways count), and
only shows of the last three weeks are read at all. A month of this shop holds about 6,000
orders, so the dashboard's "Sales on Whatnot" added up a month missing a fifth of its paid
orders, and nothing said so. This reads the rest, then tops it up once a day, and records
what it has proved (`completeSince` .. `completeUntil`), which the dashboard then says.

What it is and is not:
  * it only STORES: orders and their items, as every other read does (`sync.store_order`),
    and leaves an order the app holds that Whatnot had not settled to the timer's re-read
    (`sync.refresh_open_orders`), whose before-and-after comparison is what queues the
    apply of a show whose order settled. A held COMPLETED order it finds cancelled is
    stored here and applied by its show's next late read (`sync.LATE_READS`, out to three
    days after the show); for an older show the new state is stored and nothing applies it;
  * it never marks a show's orders as read (`WhatnotShow.orders_synced_at`), never makes a
    sale, never applies an order, moves stock or queues `whatnot_apply_show` ITSELF. What it
    stores is then read like any other order: the timer re-reads an unsettled one for 14
    days, and when one of a show the shop follows in "auto" mode settles, that show is
    applied, as it would have been had the normal read caught the order on time. A show
    that was over before "auto" began is history (`sync.history`) and is never applied on
    its own;
  * it never runs while a show is on air (its requests are the show's request budget), and
    stops cleanly, keeping what it stored, if a show goes live in the middle;
  * it commits every `BATCH` orders, so a failure keeps what was read and the next run
    carries on from there.

What the dashboard may say. A finished run proves every order from its floor to the moment
it BEGAN (`completeUntil`): an order placed while it ran sits above the point its later
slices resume from. The orders a show's own read misses (a buy-now placed between shows, or
past the 3,000-order cap) are caught by the TOP-UP: once a day, when no show is on air, a
run begins `OVERLAP` before the last one began, reads only those days, and moves
`completeUntil` forward. It never moves `completeSince` forward (the oldest floor stays);
a failed, paused or unfinished run moves neither end. After a gap too long for a top-up to
reach, a full run starts afresh and its own floor becomes the new `completeSince`.

The end of Whatnot's list is not the floor. `client.pages` stops on any 200 page with no
next cursor, so one odd answer would look like "no older orders". The floor counts as
reached only by seeing a page's worth of orders older than it in a row (one stray old order
near the top proves nothing, and neither do a few at an end that came early), or by two
reads of the whole list that end on the same order.

A run is cut into slices of about `RUN_SECONDS`: the worker is one lane, and a read that
lasts an hour would keep the show's own jobs waiting behind it. Each slice queues the next.
The list has no date filter, so a slice pages from the newest order again, skipping (no
database work) everything newer than where the last slice stopped, until it passes that
point; only the time spent past it counts against the slice.

An order whose items Whatnot says do not exist (404) is left out, whole, and counted
(`skipped`): it is not part of what the dashboard claims. Any other failure fails the slice,
and the timer asks again after a wait that doubles with each round of failures.

Progress lives in the shop's `whatnot_sync_state` row "orders_backfill": `cursor` is the run
in progress (its floor, when it began, the oldest order stored so far); `counts` says what
the last slice did and what has been proved.
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ...db import db
from ...models import WhatnotOrder, WhatnotShow, WhatnotSyncState
from .. import dashboard, jobs
from . import connection, sync
from .client import NotFound, WhatnotClient, WhatnotError, client_for, when

log = logging.getLogger(__name__)

RESOURCE = "orders_backfill"
KIND = "whatnot_orders_backfill"
# The dashboard's longest period: nothing older is shown, so nothing older is read.
FLOOR_DAYS = max(dashboard.PERIODS)
BATCH = 50  # orders per transaction (one page of Whatnot's list)
RUN_SECONDS = 90  # one slice, measured from the first order past where the last one stopped
NEXT_SLICE_S = 10  # the next slice is queued behind whatever the worker already has waiting
SWEEP_EVERY = timedelta(days=1)  # a finished run is topped up this long after the last began
OVERLAP = timedelta(days=2)  # a top-up starts this far before the last run began
STOP_AFTER = 50  # orders older than the floor in a row before the floor counts as passed
MAX_PASSES = 4  # reads of the whole list in one slice (the end of it has to be confirmed)
RETRY_AFTER = timedelta(hours=1)  # a failed or interrupted run is asked for again after this
MAX_RETRY_AFTER = timedelta(days=1)
ATTEMPTS = 5  # a job's attempts (`Job.max_attempts`): one round of failures is this many
LIVE_CHECK_EVERY = 30.0  # seconds between asking Whatnot which shows are live


def _row(org_id: int) -> WhatnotSyncState | None:
    """The bookkeeping row, never made by looking (the timer asks every five minutes)."""
    return db.session.scalar(
        select(WhatnotSyncState).where(
            WhatnotSyncState.organization_id == org_id, WhatnotSyncState.resource == RESOURCE
        )
    )


def claim(counts: dict | None) -> tuple[str | None, str | None]:
    """What the app has proved it holds: every order from the first to the second date (ISO
    strings), or (None, None) before any run has finished."""
    counts = counts or {}
    since, until = counts.get("completeSince"), counts.get("completeUntil")
    return (since, until) if since and until else (None, None)


def complete_since(org_id: int) -> datetime | None:
    """The date from which the app holds every order Whatnot has for the shop, once a run
    has completed (None until then). It says nothing about the days after `claim`'s second
    date: the show reads and the daily top-up keep those current."""
    row = _row(org_id)
    return when(claim(row.counts)[0]) if row is not None else None


def on_air(org_id: int) -> bool:
    """A show of the shop is live (or paused) and began within `ON_AIR_AT_MOST`: the timer's
    own rule for holding back its open-order read. A show left "live" for longer was never
    closed here and holds nothing back."""
    now = datetime.now(UTC)
    shows = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id, WhatnotShow.status.in_(sync.ACTIVE)
        )
    ).all()
    return any((ws.started_at or ws.begins_at or now) >= now - sync.ON_AIR_AT_MOST for ws in shows)


def retry_wait(counts: dict | None) -> timedelta:
    """How long after a run last started the timer leaves it alone: `RETRY_AFTER`, doubled
    for every further round of `ATTEMPTS` failures in a row, up to `MAX_RETRY_AFTER`."""
    fails = int((counts or {}).get("fails") or 0)
    if fails <= 0:
        return RETRY_AFTER
    return min(RETRY_AFTER * 2 ** min((fails - 1) // ATTEMPTS, 16), MAX_RETRY_AFTER)


def due(org_id: int) -> bool:
    """The timer's question: is no show on air, and is there a run to make: the first, the
    next top-up (`SWEEP_EVERY` after the last one began), or the carrying on of one that
    failed or stopped? A run that failed or stopped waits like the other reads do, by when
    it last started, not for every five-minute pass."""
    row = _row(org_id)
    if row is not None:
        counts = row.counts or {}
        until = when(claim(counts)[1])
        interrupted = row.cursor is not None or counts.get("fails") or until is None
        tried = row.last_started_at
        if interrupted and tried is not None and datetime.now(UTC) - tried < retry_wait(counts):
            return False
        if not interrupted and datetime.now(UTC) - until < SWEEP_EVERY:
            return False
    return not on_air(org_id)


def schedule(org_id: int, *, delay_s: int = 0) -> None:
    jobs.enqueue(
        KIND,
        {"org_id": org_id},
        org_id=org_id,
        dedupe_key=f"{KIND}:{org_id}",
        delay_s=delay_s,
    )


def _refresh_live(org_id: int, client: WhatnotClient) -> bool:
    """Ask Whatnot which shows are live or paused right now, keep what it says, and return
    whether one is on air. The database learns of a show going live from a webhook or the
    timer, and neither can run while this holds the worker."""
    for status in sync.ACTIVE:
        for data in client.shows(status):
            sync.upsert_show(org_id, data)
    db.session.commit()
    return on_air(org_id)


def _new_run(counts: dict) -> dict:
    """The cursor of a run that is starting. A top-up begins `OVERLAP` before the last
    finished run began, unless that is further back than the floor can go (a long gap):
    then it is a full run, and its floor becomes the new `completeSince`."""
    began = datetime.now(UTC)
    since, until = claim(counts)
    full_floor = began - timedelta(days=FLOOR_DAYS)
    floor, mode = full_floor, "full"
    if since and until and when(until) - OVERLAP >= full_floor:
        floor, mode = when(until) - OVERLAP, "topup"
    return {
        "floor": floor.isoformat(),
        "began": began.isoformat(),
        "mode": mode,
        "reached": None,
        "end_id": None,
        "seen": 0,
        "new": 0,
        "skipped": 0,
    }


def run(org_id: int, client: WhatnotClient, *, budget_s: float | None = None) -> dict:
    """One slice. Returns (and stores in the row's `counts`):
    `finished`: this run reached its floor; `paused`: a show is on air, nothing is queued
    and the timer brings it back after the show; `complete` / `completeSince` /
    `completeUntil` / `completedAt`: what some finished run proved (see the module
    docstring); `reachedAt`: the oldest order stored by this run so far; `seen` / `new` /
    `skipped`: orders read, not held before, and left out (Whatnot says they have no items)
    by it; `fails`: failed rounds in a row."""
    budget = RUN_SECONDS if budget_s is None else budget_s
    state = connection.sync_state(org_id, RESOURCE)
    cursor = dict(state.cursor or {})
    counts = dict(state.counts or {})
    if not cursor.get("floor"):
        cursor = _new_run(counts)
    floor = when(cursor["floor"])
    seen, new = int(cursor.get("seen") or 0), int(cursor.get("new") or 0)
    skipped = int(cursor.get("skipped") or 0)

    def save(*, finished: bool = False, paused: bool = False) -> dict:
        out = {
            **counts,
            "finished": finished,
            "paused": paused,
            "seen": seen,
            "new": new,
            "skipped": skipped,
            "reachedAt": cursor["reached"],
            "fails": 0,
        }
        if finished:
            # The claim reaches to when this run began, and back to the oldest floor ever
            # proved: only a full run (the first, or after a gap) sets the older end.
            out["completeUntil"] = cursor["began"]
            if cursor.get("mode") != "topup" or not out.get("completeSince"):
                out["completeSince"] = cursor["floor"]
            out["completedAt"] = datetime.now(UTC).isoformat()
        out["complete"] = bool(out.get("completeSince"))
        st = connection.sync_state(org_id, RESOURCE)
        # A finished run leaves no cursor: the next one starts afresh and upserts everything.
        st.cursor = None if finished else dict(cursor, seen=seen, new=new, skipped=skipped)
        st.counts = out
        return out

    last_live = time.monotonic()

    def live() -> bool:
        nonlocal last_live
        last_live = time.monotonic()
        return _refresh_live(org_id, client)

    def live_due() -> bool:
        return time.monotonic() - last_live >= LIVE_CHECK_EVERY

    if live():
        return save(paused=True)
    # Orders the app holds that Whatnot had not settled are the timer's to re-read: it
    # compares the open orders before and after, and a show whose order settled (a
    # cancelled or failed payment) is then applied in "auto" mode. Settling one here first
    # would leave that comparison nothing to see, and the sale would keep its stock.
    open_held = set(
        db.session.scalars(
            select(WhatnotOrder.whatnot_id).where(
                WhatnotOrder.organization_id == org_id,
                WhatnotOrder.status.in_(sync.OPEN_ORDER_STATUSES),
                WhatnotOrder.ordered_at >= datetime.now(UTC) - sync.OPEN_ORDER_AGE,
            )
        )
    )
    t0: float | None = None  # starts at the first order past the last slice's stop
    in_batch = 0
    for _ in range(MAX_PASSES):
        reached = when(cursor.get("reached"))  # where the last slice (or pass) stopped
        resumed = reached is None  # nothing stored yet: there is nothing to skip
        older = 0
        last_id = ""
        for data in client.orders():
            last_id = str(data.get("id") or "")
            made = when(data.get("created_at"))
            if made is not None and floor is not None and made < floor:
                # Never stored. A page's worth in a row says the floor is behind us; one
                # stray old order amid newer ones says nothing.
                older += 1
                if older >= STOP_AFTER:
                    return save(finished=True)
                continue
            older = 0
            if not resumed and made is not None and reached is not None:
                if made > reached:
                    # Stored by an earlier slice: no database work, only the paging to get
                    # past it.
                    if live_due() and live():
                        return save(paused=True)
                    continue
                resumed = True  # past the stop: an order newer than it further down is new
            if t0 is None:
                t0 = time.monotonic()
            was_new = False
            if str(data.get("id")) in open_held:
                pass
            else:
                try:
                    # A SAVEPOINT: an order whose items are not there is undone whole, never
                    # kept without them.
                    with db.session.begin_nested():
                        _, was_new = sync.store_order(org_id, client, data)
                except NotFound:
                    skipped += 1
                    log.warning("whatnot order %s has no items: left out", data.get("id"))
            seen += 1
            new += was_new
            if made is not None and (cursor["reached"] is None or made < when(cursor["reached"])):
                cursor["reached"] = made.isoformat()
            # An order at the very moment the last slice stopped is read again (a tie may
            # hold one that slice never reached) and does not count toward this batch.
            if not (made is not None and made == reached):
                in_batch += 1
            if in_batch < BATCH:
                continue
            in_batch = 0
            save()
            db.session.commit()  # what was read is kept: the next slice starts after it
            if live_due() and live():
                return save(paused=True)
            if time.monotonic() - t0 >= budget:
                return save()
        # The list ended. It may have ended early (one odd answer ends `client.pages`), and
        # fewer than `STOP_AFTER` older orders before that end prove nothing (one stray old
        # order then an odd page would look like the floor): only a second read that ends
        # on the same order says it is the end. A page's worth of older orders in a row
        # returned above already.
        if cursor.get("end_id") == last_id:
            return save(finished=True)
        cursor["end_id"] = last_id
    return save()  # the end is still being confirmed: the next slice reads on


@jobs.handler(KIND)
def backfill_job(payload: dict) -> None:
    from .. import buyers

    org_id = payload["org_id"]
    if connection.active(org_id) is None:
        return
    if on_air(org_id):
        return  # the timer asks again once the show is over
    client = client_for(org_id)
    before = int(((connection.sync_state(org_id, RESOURCE).cursor) or {}).get("seen") or 0)
    try:
        counts = sync._run(org_id, RESOURCE, lambda: run(org_id, client))
    except WhatnotError:
        # `_run` has kept why. The timer waits longer after each round of failures, so a
        # run that can never work is not asked for every hour for ever.
        st = connection.sync_state(org_id, RESOURCE)
        st.counts = {**(st.counts or {}), "fails": int((st.counts or {}).get("fails") or 0) + 1}
        db.session.commit()
        raise
    db.session.commit()
    # The buyer profiles catch up with who placed what was stored (soon, once). They read
    # orders by date, and an older order never takes a name from a newer one (`buyers._seen`).
    if counts and (counts["seen"] != before or counts["finished"]):
        buyers.schedule_link(org_id)
    if counts and not counts["finished"] and not counts["paused"] and not jobs.inline():
        schedule(org_id, delay_s=NEXT_SLICE_S)  # inline (dev, tests) leaves it to the timer
    db.session.commit()
