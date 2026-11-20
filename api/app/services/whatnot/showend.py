"""When a Whatnot show is over. The one place that decides it: the timer's reads, "every
order read" (orders.py, shipping.py), the parcels and labels, Whatnot's results, the picks,
the alerts and "is a show on air" all ask here.

Whatnot is the first word: a show it calls "ended" with an end time ended then, and that
time is never written over. It does not always give one. An "ended" show can come with no
end time (services/whatnot/probe.py looks for exactly that), and a show can stay "live" or
"stopped" (paused, may resume) long after it ended. Such a show was never read in full,
never complete, never owed its parcels or labels. So (Javier's call, 2026-11-19):

  * A show Whatnot gives no end time for is over `ON_AIR_AT_MOST` (12 hours) after it began:
    its first known moment (`started_at`; else when the app saw it go live, never a
    scheduled time older than that; else the time it was scheduled for; else, while
    Whatnot calls it live or paused, the moment the app first saw it). Nothing is stored:
    the end is worked out each time, so a real end time arriving later simply wins.

  * A show laptop may say sooner. When Whatnot moves the laptop's tab on to another channel
    (it does that when a show ends) the extension tells the server, which keeps the time it
    heard it (`Show.moved_on_at`, the server's clock, never the laptop's). That is the show's
    end when it is before the 12-hour one and:
      - it is not before the show began;
      - no chat line of the show's recordings reached the server after it (the laptop came
        back to the show, or another laptop is still on it);
      - while Whatnot still calls the show live or paused, there is proof nothing sold after
        it: every order of the show was read (`orders_synced_at`) at least `MOVED_ON_QUIET`
        after it, and none was placed after it (`ORDER_GRACE` allowed for the last win's
        order, stamped a moment after the hammer; an order with no time counts from when the
        app first stored it). A quiet half hour nobody read is no proof: until that read the
        show is on air (the timer makes it even while webhooks arrive, `awaiting_read`). A
        show that keeps selling is on air whatever a laptop saw. For a show Whatnot calls
        ended no order check is made: a card declined at the win and retried is a new order
        placed after the end, and must not push the end back to 12 hours (that would take
        back a show already complete).
    A laptop's word can only make a show over sooner than the 12-hour rule, never later.

  * The host may only have paused. A show over by this rule that Whatnot still calls live or
    paused with no end time keeps having its orders read (`still_read`) until the 12 hours
    have passed and its orders were read after that: an order placed after the laptop's
    move puts it back on air at the next pass, and everything that waits for a show to be
    over (labels, picks, "every order read") waits again. What was done while it looked
    over is not undone (a label made, a pick read, a second win of a lot that gave way to
    another buyer's paid order, a sale's "needs a decision" flag); a win called out as not
    among Whatnot's orders is answered when its order comes (whatnot/orders.py
    `_flag_declines`).

"Over" and "when" are two answers. A show Whatnot calls ended is over even before its
12-hour end has come (the Shipping page says "ended, not read in full", never "not ended");
what needs "every order read after the end" waits for that end to pass (`end_of`).

Whatnot's real behaviour here is not verified against the live shop (docs/WHATNOT_API.md).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from ...db import db
from ...models import ChatMessage, Show, WhatnotOrder, WhatnotShow

ON_AIR = ("live", "stopped")  # Whatnot's words for a show on air (or paused)
AIRED = ON_AIR + ("ended",)  # a show that went on air; scheduled and cancelled never did
# A show Whatnot gives no end time for is over this long after it began.
ON_AIR_AT_MOST = timedelta(hours=12)
# A laptop's word that the page moved on counts for a show Whatnot still calls live or
# paused only once this long has passed with no order of the show placed after it.
MOVED_ON_QUIET = timedelta(minutes=30)
# An order placed this soon after the laptop's word does not undo it: Whatnot stamps the
# last win's order a moment after the hammer, and the server hears the laptop a moment later.
ORDER_GRACE = timedelta(minutes=2)
# A show over by this module's rule that Whatnot still calls live or paused keeps its orders
# read until its 12 hours have passed and one read after them; that read is tried this long.
READ_AFTER_LIMIT = timedelta(days=1)


@dataclass(frozen=True)
class Ending:
    """`over`: the show is over. `at`: when it ended (None while on air, and for a show
    Whatnot calls ended with no time at all); it can be still to come for a show Whatnot
    calls ended (the 12-hour end). `by`: who said so: "whatnot", "laptop" or "time"."""

    over: bool
    at: datetime | None = None
    by: str | None = None


NOT_OVER = Ending(False)
_LOOK = object()  # look the laptops' word up (the default)


def began(ws: WhatnotShow) -> datetime | None:
    """The moment the 12 hours count from: when it went live; else when the app saw it go
    live (never a scheduled time older than that: a show started a day late is not over the
    moment it goes live); else when it was scheduled for; else (only while Whatnot calls it
    on air) when the app first saw it. A show first seen already ended has no such moment:
    when it was first seen is not when it began."""
    if ws.started_at is not None:
        return ws.started_at
    if ws.live_seen_at is not None:
        return ws.live_seen_at
    if ws.scheduled_start_at is not None:
        return ws.scheduled_start_at
    return ws.created_at if ws.status in ON_AIR else None


def time_limit(ws: WhatnotShow) -> datetime | None:
    start = began(ws)
    return start + ON_AIR_AT_MOST if start is not None else None


def ending(ws: WhatnotShow, now: datetime | None = None, *, hint=_LOOK) -> Ending:
    """Whether the show is over, when, and who says so. `hint` is the laptops' word (looked
    up unless given; None to judge without it)."""
    now = now or datetime.now(UTC)
    if ws.status not in AIRED:
        return NOT_OVER
    if ws.status == "ended" and ws.ended_at is not None:
        return Ending(True, ws.ended_at, "whatnot")
    if hint is _LOOK:
        hint = laptop_word([ws], now).get(ws.id)
    limit = time_limit(ws)
    if hint is not None and (limit is None or hint < limit):
        return Ending(True, hint, "laptop")
    if ws.status == "ended":
        return Ending(True, limit, "time" if limit is not None else None)
    if limit is not None and now >= limit:
        return Ending(True, limit, "time")
    return NOT_OVER


def endings(shows, now: datetime | None = None, *, laptops: bool = True) -> dict[int, Ending]:
    """`ending` for many shows, keyed by the Python object's id() (shows not yet stored have
    no row id); the laptops' word is looked up in one go."""
    now = now or datetime.now(UTC)
    shows = list(shows)
    word = laptop_word(shows, now) if laptops else {}
    return {id(ws): ending(ws, now, hint=word.get(ws.id)) for ws in shows}


def passed(e: Ending, now: datetime) -> datetime | None:
    """The end of `e` once it has passed (Whatnot's own end as it gave it); else None."""
    if not e.over or e.at is None:
        return None
    return e.at if e.by == "whatnot" or e.at <= now else None


def end_of(ws: WhatnotShow, now: datetime | None = None) -> datetime | None:
    """When the show ended, once that moment has passed; None while it is on air (or its
    end is still to come). What needs every order read after the end compares with this."""
    now = now or datetime.now(UTC)
    return passed(ending(ws, now), now)


def is_over(ws: WhatnotShow, now: datetime | None = None) -> bool:
    return ending(ws, now).over


def on_air(shows, now: datetime | None = None) -> bool:
    """Whether any of these shows is on air: Whatnot calls it live or paused and it is not
    over (the 12 hours, or a laptop's word)."""
    now = now or datetime.now(UTC)
    shows = [ws for ws in shows if ws.status in ON_AIR]
    if not shows:
        return False
    found = endings(shows, now)
    return any(not found[id(ws)].over for ws in shows)


def ended_within(org_id: int, within: timedelta, now: datetime | None = None):
    """The shop's shows whose end has passed and is no older than `within`, each with that
    end: [(show, end)]. The database narrows them by the times a show's end can be computed
    from (an end is never more than `ON_AIR_AT_MOST` after the latest of them); this module
    decides."""
    now = now or datetime.now(UTC)
    since = now - within
    reported = select(Show.whatnot_show_id).where(
        Show.organization_id == org_id,
        Show.whatnot_show_id.is_not(None),
        Show.moved_on_at >= since,
    )
    shows = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status.in_(AIRED),
            or_(
                func.greatest(
                    WhatnotShow.ended_at,
                    WhatnotShow.started_at,
                    WhatnotShow.live_seen_at,
                    WhatnotShow.scheduled_start_at,
                    WhatnotShow.created_at,
                )
                >= since - ON_AIR_AT_MOST,
                WhatnotShow.id.in_(reported),
            ),
        )
    ).all()
    found = endings(shows, now)
    out = []
    for ws in shows:
        end = passed(found[id(ws)], now)
        if end is not None and end >= since:
            out.append((ws, end))
    return out


def still_read(ws: WhatnotShow, e: Ending, now: datetime | None = None) -> bool:
    """A show over by the app's rule (a laptop's word, or the 12 hours) that Whatnot still
    calls live or paused with no end time: the host may only have paused. Its orders are
    still read on the timer's passes, so an order placed after the laptop's move puts it back
    on air at once, until the 12 hours since it began have passed and every order was read
    after that (then nothing it sells can be of that show any more). That last read is
    tried for `READ_AFTER_LIMIT` at most: a show left on air for days is not walked back to
    its start on every pass (the ended shows' own reads, sync._unread_shows, see to it)."""
    if not e.over or ws.status not in ON_AIR or ws.ended_at is not None:
        return False
    now = now or datetime.now(UTC)
    limit = time_limit(ws)
    if limit is None or now < limit:
        return True
    read = ws.orders_synced_at
    return (read is None or read < limit) and now < limit + READ_AFTER_LIMIT


def awaiting_read(shows, now: datetime | None = None) -> set[int]:
    """Row ids of the shows Whatnot calls live or paused whose laptop's word would end them
    but for a read of every order after the quiet period: the timer reads those even while
    webhooks arrive (it does not poll a live show then), or the word would never count."""
    return _laptops(shows, now)[1]


def laptop_word(shows, now: datetime | None = None) -> dict[int, datetime]:
    """Show row id → the moment a laptop recording it saw its page move on to another
    channel, for the shows where that word counts (see the module's notes). Only for shows
    whose end Whatnot did not give; shows not stored yet have none."""
    return _laptops(shows, now)[0]


def _laptops(shows, now: datetime | None = None) -> tuple[dict[int, datetime], set[int]]:
    """(`laptop_word`, `awaiting_read`)."""
    now = now or datetime.now(UTC)
    asked = {
        ws.id: ws
        for ws in shows
        if ws.id is not None
        and ws.status in AIRED
        and not (ws.status == "ended" and ws.ended_at is not None)
    }
    if not asked:
        return {}, set()
    seen = dict(
        db.session.execute(
            select(Show.whatnot_show_id, func.max(Show.moved_on_at))
            .where(Show.whatnot_show_id.in_(asked), Show.moved_on_at.is_not(None))
            .group_by(Show.whatnot_show_id)
        ).all()
    )
    if not seen:
        return {}, set()
    # The server's own times on both sides: when it heard the laptop, when chat reached it.
    last_chat = dict(
        db.session.execute(
            select(Show.whatnot_show_id, func.max(ChatMessage.received_at))
            .join(Show, Show.id == ChatMessage.show_id)
            .where(Show.whatnot_show_id.in_(seen))
            .group_by(Show.whatnot_show_id)
        ).all()
    )
    on_air = [asked[i] for i in seen if asked[i].status in ON_AIR]
    last_order: dict[tuple[int, str], datetime] = {}
    # When each order was placed; one Whatnot gives no time for counts from when the app
    # first stored it (an undated order first stored after the move is a sale after it).
    placed = func.coalesce(WhatnotOrder.ordered_at, WhatnotOrder.first_stored_at)
    for org_id in {ws.organization_id for ws in on_air}:
        for show_id, at in db.session.execute(
            select(WhatnotOrder.whatnot_show_id, func.max(placed))
            .where(
                WhatnotOrder.organization_id == org_id,
                WhatnotOrder.whatnot_show_id.in_(
                    [ws.whatnot_id for ws in on_air if ws.organization_id == org_id]
                ),
            )
            .group_by(WhatnotOrder.whatnot_show_id)
        ):
            last_order[(org_id, show_id)] = at
    out: dict[int, datetime] = {}
    waiting: set[int] = set()
    for show_id, at in seen.items():
        ws = asked[show_id]
        start = ws.begins_at
        if start is not None and at < start:
            continue  # before the show began: about something else
        chat = last_chat.get(show_id)
        if chat is not None and chat > at:
            continue  # chat of the show came after it: the show was still going
        if ws.status in ON_AIR:
            if now < at + MOVED_ON_QUIET:
                continue
            order = last_order.get((ws.organization_id, ws.whatnot_id))
            if order is not None and order > at + ORDER_GRACE:
                continue  # still selling: on air whatever a laptop saw
            # The proof nothing sold after the move: every order of the show read after the
            # quiet period. Until then a quiet half hour may only be one nobody looked at.
            read = ws.orders_synced_at
            if read is None or read < at + MOVED_ON_QUIET:
                waiting.add(show_id)
                continue
        out[show_id] = at
    return out, waiting
