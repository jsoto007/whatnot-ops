"""What nobody would otherwise notice: Whatnot reads that went quiet, orders that fall
behind a show, and a live show with no laptop recording its chat.

Everything here is computed from rows the app already keeps (reading bookkeeping, shows,
chat) when a page asks; nothing is stored and nothing is sent. The pages turn the codes
into words (`web/src/lib/labels.ts`): a code never reaches the screen, and neither does a
setting or Whatnot's own error text. Read-only, and for every role that may open the app:
the people packing from the slips are the ones who most need to know.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ...db import db
from ...models import ChatMessage, Organization, Show, WhatnotEvent, WhatnotShow, WhatnotSyncState
from .. import sale_mode
from . import connection, showend
from .orders import BLIND

# A reading that has failed since it last worked for this long "keeps failing". The show
# list is read every 15 minutes (two missed turns), the orders of a live show every five.
FAILING_AFTER = {"shows": timedelta(minutes=45), "orders": timedelta(minutes=30)}
# A live show's orders, read this long ago (by the timer, or a webhook handled), are behind.
# Healthy is at most about 15 minutes: ten quiet minutes, then the next timer pass.
ORDERS_STALE_AFTER = timedelta(minutes=20)
# An ended show whose last read (the one that begins after it ended) has not happened.
FINAL_READ_DUE = timedelta(minutes=15)
# Shows older than this are history: nobody is packing from or watching them any more.
RECENT = timedelta(hours=12)
# Whatnot says the show is live and nothing of a laptop shows up, or the chat stopped.
NO_LAPTOP_AFTER = timedelta(minutes=3)
LATE_LAPTOP_AFTER = timedelta(minutes=3)
CHAT_SILENT_AFTER = timedelta(minutes=5)


def _minutes(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 60))


def notice(org_id: int, now: datetime | None = None) -> dict | None:
    """The one thing about reading Whatnot a person should know, or None: `reads_failing`
    (the show list or the orders cannot be read, and have not been for a while) or
    `orders_behind` (a live show's orders stopped arriving, or a show that ended has not had
    its last read). A rejected token or a missing permission is the banner's own business
    (`counts.whatnotProblem`), not repeated here."""
    now = now or datetime.now(UTC)
    conn = connection.active(org_id)
    if conn is None or conn.problem in BLIND:
        return None
    states = {
        r.resource: r
        for r in db.session.scalars(
            select(WhatnotSyncState).where(
                WhatnotSyncState.organization_id == org_id,
                WhatnotSyncState.resource.in_(tuple(FAILING_AFTER)),
            )
        )
    }
    worst: timedelta | None = None
    for resource, after in FAILING_AFTER.items():
        row = states.get(resource)
        if row is None or not row.last_error or row.last_started_at is None:
            continue
        if now - row.last_started_at > after:
            continue  # nobody has tried lately: a failure long past, not one that "keeps" failing
        since = now - (row.last_ok_at or conn.connected_at)
        if since >= after and (worst is None or since > worst):
            worst = since
    if worst is not None:
        return {"code": "reads_failing", "minutes": _minutes(worst)}
    behind = _orders_behind(org_id, conn, now)
    if behind is not None:
        return {"code": "orders_behind", "minutes": _minutes(behind)}
    return None


def _orders_behind(org_id, conn, now) -> timedelta | None:
    shows = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status.in_(("live", "ended")),
            func.coalesce(WhatnotShow.ended_at, WhatnotShow.started_at, WhatnotShow.created_at)
            >= now - RECENT,
        )
    ).all()
    if not shows:
        return None
    # A sign that orders reached the app besides a show's own read: an ORDER delivery from
    # Whatnot that was handled. Deliveries of other kinds (products, shows) keep the timer
    # from polling a live show without bringing any order, so they prove nothing; neither
    # does a delivery stuck in the queue, nor the shop-wide read time, which a read of some
    # other (ended) show refreshes.
    seen: list[datetime | None] = []
    if conn.last_event_at is not None and now - conn.last_event_at < ORDERS_STALE_AFTER:
        seen.append(
            db.session.scalar(
                select(func.max(WhatnotEvent.processed_at)).where(
                    WhatnotEvent.organization_id == org_id,
                    WhatnotEvent.status == "done",
                    WhatnotEvent.topic.like("orders.%"),
                    WhatnotEvent.received_at >= now - ORDERS_STALE_AFTER * 2,
                )
            )
        )
    worst: timedelta | None = None
    found = showend.endings(shows, now)
    for ws in shows:
        e = found[id(ws)]
        end = showend.passed(e, now)
        if ws.status == "live" and not e.over:
            began = ws.started_at or ws.begins_at
            if began is None or now - began < ORDERS_STALE_AFTER:
                continue  # just went live: the first read is not due yet
            last = max((t for t in (*seen, ws.orders_synced_at) if t is not None), default=began)
            late = now - max(last, began)
            if late >= ORDERS_STALE_AFTER and (worst is None or late > worst):
                worst = late
        elif (
            end is not None
            and now - end >= FINAL_READ_DUE
            and (ws.orders_synced_at is None or ws.orders_synced_at < end)
        ):
            late = now - end
            if worst is None or late > worst:
                worst = late
    return worst


def show_read(ws: WhatnotShow | None) -> dict | None:
    """When a Whatnot show's orders were last read, and whether that read began after it
    ended (so no order can be missing from it). For the order slips every role may open.
    None when there is nothing to judge: no show, one that never aired (cancelled,
    scheduled) or one Whatnot calls ended with no end time whose end by showend.py's rule
    has not come yet (the timer reads it in full then)."""
    if ws is None:
        return None
    now = datetime.now(UTC)
    e = showend.ending(ws, now)
    live = ws.status in showend.ON_AIR and not e.over
    end = showend.passed(e, now)
    if not live and end is None:
        return None
    final = bool(end is not None and ws.orders_synced_at is not None and ws.orders_synced_at >= end)
    return {
        "at": ws.orders_synced_at.isoformat() if ws.orders_synced_at else None,
        "final": final,
        "live": live,
    }


def live_gaps(org_id: int, now: datetime | None = None) -> list[dict]:
    """Shows Whatnot says are live while no laptop is recording them well: none at all, one
    that started late (what was said before is lost) or one whose chat stopped coming.
    Each is {showId (the app's show to open, or None), title, code, minutes}."""
    now = now or datetime.now(UTC)
    live = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status == "live",
            func.coalesce(WhatnotShow.started_at, WhatnotShow.scheduled_start_at) >= now - RECENT,
        )
    ).all()
    out: list[dict] = []
    for ws in live:
        out += gaps_of(ws, now)
    return out


def gaps_of(ws: WhatnotShow, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    if ws.status != "live" or showend.is_over(ws, now):
        return []  # (a show left live past 12 hours, or one a laptop saw end: not on air)
    began = ws.started_at or ws.begins_at
    if began is None or now - began < NO_LAPTOP_AFTER:
        return []
    recordings = list(
        db.session.scalars(
            select(Show)
            .where(Show.organization_id == ws.organization_id, Show.whatnot_show_id == ws.id)
            .order_by(Show.id)
        )
    )
    laptops = [s for s in recordings if s.whatnot_link != "made"]
    open_id = (laptops or recordings or [None])[0]
    if not _laptop_needed(ws.organization_id, open_id):
        return []

    def gap(code: str, minutes: int) -> dict:
        return {
            "showId": open_id.id if open_id is not None else None,
            "title": ws.title,
            "code": code,
            "minutes": minutes,
        }

    if not laptops:
        return [gap("no_recording", 0)]
    out = []
    # Only the server's own times: when a recording was first heard of, and when its newest
    # chat line arrived. A laptop's clock is often out (see `sale_chat.clock_problems`) and
    # must neither hide silent chat nor invent a late start.
    last = db.session.scalar(
        select(func.max(ChatMessage.received_at)).where(
            ChatMessage.show_id.in_([s.id for s in laptops])
        )
    )
    floor = max(s.created_at for s in laptops)
    last = max(last or floor, floor)
    quiet = now - last
    if quiet >= CHAT_SILENT_AFTER:
        out.append(gap("no_chat", _minutes(quiet)))
    if ws.started_at is not None:
        late = min(s.created_at for s in laptops) - ws.started_at
        if late >= LATE_LAPTOP_AFTER:
            out.append(gap("late_recording", _minutes(late)))
    return out


def _laptop_needed(org_id: int, show: Show | None) -> bool:
    """Not when the show sells one product per listing and Whatnot's orders are applied as
    they arrive: the winner, item and payment then come from Whatnot, and a missing laptop
    loses no sale. (Chat alone is not worth a warning in that mode.)"""
    mode = (
        sale_mode.of_show(show)[0]
        if show is not None
        else db.session.scalar(select(Organization.sale_mode).where(Organization.id == org_id))
    )
    return sale_mode.laptop_needed(mode, org_id)
