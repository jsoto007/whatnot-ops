"""One laptop recording that runs through two Whatnot shows becomes two shows here.

The laptop keeps one key for its recording from Start until "Clear this show" (resumed for
up to 12 hours). A host who presses Stop after one show and Start on the next show's page,
or who moves from one show's seller page to the next, records both shows under that one
key. Until now the recording was tied to whichever show it overlapped longer; the other
show got a show made from Whatnot's record (`sync.ensure_recording`), with no chat, and the
second show's wins, picks and orders were in the wrong show.

What decides that a recording ran through two shows: wins in it the app is sure of, said
in each show while that show was on air (Whatnot's own start and end; the 12-hour rule of
showend.py for a show Whatnot never ended). A win is a host/platform or video-player line
read as a sale, a sale tied to one of the show's Whatnot orders (Whatnot's word, whatever
the time), or at least two of the show's own buyers chatting while it was on air (the
seller's page records no wins). A moment that falls in two shows' on-air time counts for
neither. Pre-show chat, a show that was never ended, or two laptops on one show never split
anything. Never split: a recording a person matched to a show, a show the app made, or a
recording whose own address names another show than its first one.

Where it is split: in the longest quiet stretch of the laptop's own chat between the last
win of the first show and the first win of the second (the laptop's own clock on both
sides, so a laptop clock that is out does not move the moment).

What moves to the second show, all by re-pointing (never by cancelling and making again):
the chat lines from that moment on and what the parser read from them, the sales (by their
Whatnot order's show when it is known, else by when their line was captured: same ids, so
the same stock ledger keys and not a unit moves in stock), each buyer's Order (one buyer
with wins in both shows gets an Order in each), the Review questions about those lines or
sales, what Whatnot's orders of the second show told a person with no sale or line to
follow (a declined payment with no sale here, by its order item; a payment no sale answers,
by the order it names; the show's "orders left out" notice, when the recording was the
second show's before the split), each as it was, open, dismissed or resolved, so that
show's next apply knows it, Whatnot's lots read for the second show and their parcels. The
second show takes the shelf layout and selling way that were in use, unless its Whatnot
show has its own. Each half is then tied to its Whatnot show for good (link "split": the
clock never re-decides it), and a show the app made for either Whatnot show folds into
that half (services/show_merge.py), with its own rules.

A buyer whose box is being picked, packed or shipped with a win from the second show stops
the split (a box cannot be cut in two), and so does anything that stops the made show's
fold: nothing changes, and a person is told in Review, once per recording. The next read of
Whatnot's shows tries again, and settles the question when it splits.

The laptop keeps uploading under its key: ingest stores each line in the part its capture
time falls in (`route`), and a line already stored in any part is never stored again, so a
batch sent twice lands nowhere twice, whichever side of the moment its lines were on.

Only at a read of Whatnot's shows (the timer, a show's webhook), in a savepoint, never
waiting for a parse that is running: the next read tries again. Only recordings that got
chat lately (`SPLIT_WITHIN`): an old show the shop already settled is not rewritten.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError

from ..db import db
from ..models import (
    ChatMessage,
    EventKind,
    Order,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    Show,
    ShowSaleMode,
    ShowShelf,
    WhatnotLot,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShipment,
    WhatnotShow,
)
from ..models.orders import FULFILLMENT
from . import audit, orders, pipeline
from .showdate import show_date

log = logging.getLogger(__name__)

ACTION = "show.split"
REASON = "recording_two_shows"
# Only a recording that got chat this lately is looked at.
SPLIT_WITHIN = timedelta(days=2)
# A win the app is sure of: a host/platform or video-player line (recordings.SURE_LINE).
SURE_LINE = 0.8
# A Whatnot show's own buyers chatting in the recording while it was on air: at least this
# many different ones.
MIN_BUYERS = 2
# Splits of one recording at one read (a recording through three shows needs two).
MAX_SPLITS = 5
# A show that went live is on air from a moment before the time the app knows it from.
START_GRACE = timedelta(minutes=5)
PART_KEY = "part:"


# ---------- the laptop's recording and its parts ----------


def family(org_id: int, key: str) -> list[Show]:
    """The shows the laptop's recording `key` is here: its own show, then each part split
    off it, in the order they began."""
    return list(
        db.session.scalars(
            select(Show)
            .where(
                Show.organization_id == org_id,
                or_(Show.show_key == key, Show.recording_key == key),
            )
            .order_by(Show.started_at, Show.id)
        )
    )


def parts_of(org_id: int, key: str, *, lock: bool = False) -> list[Show]:
    """The parts split off the laptop's recording `key` (not its own show), by start."""
    q = (
        select(Show)
        .where(Show.organization_id == org_id, Show.recording_key == key)
        .order_by(Show.started_at, Show.id)
    )
    if lock:
        q = q.with_for_update(key_share=True)
    return list(db.session.scalars(q))


def route(own: Show | None, parts: list[Show], at: datetime) -> Show | None:
    """The show a line captured at `at` goes to: the last part that began at or before it,
    else the recording's own show."""
    target = own
    for part in parts:
        if part.started_at <= at:
            target = part
    return target


def current(org_id: int, key: str) -> Show | None:
    """The show the laptop is recording now under `key`: its last part, else its own."""
    parts = parts_of(org_id, key)
    if parts:
        return parts[-1]
    return db.session.scalar(
        select(Show).where(Show.organization_id == org_id, Show.show_key == key)
    )


def laptop_key(show: Show) -> str:
    """The key the laptop uploads this show's lines under."""
    return show.recording_key or show.show_key


def _part_key(org_id: int, key: str, whatnot_id: str) -> str:
    """The part's own key: unique, at most 64 characters, never a laptop's ISO time or the
    "wn:<id>" of a made show, and the same for the same recording and show (idempotent)."""
    digest = hashlib.sha256(f"{org_id}|{key}|{whatnot_id}".encode()).hexdigest()
    return PART_KEY + digest[:40]


# ---------- which shows a recording ran through ----------


@dataclass(frozen=True)
class Window:
    """When a Whatnot show was on air, as far as the app knows."""

    ws: WhatnotShow
    begin: datetime
    end: datetime

    def holds(self, at: datetime) -> bool:
        return self.begin <= at <= self.end


def _windows(org_id: int, now: datetime) -> list[Window]:
    from .whatnot import showend, sync

    since = now - sync.RECENT_SHOWS - showend.ON_AIR_AT_MOST
    shows = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status.in_(showend.AIRED),
            func.coalesce(WhatnotShow.started_at, WhatnotShow.scheduled_start_at) >= since,
        )
    ).all()
    # Whatnot's word and the 12-hour rule only: the laptops' word comes from the very
    # recordings this looks at.
    found = showend.endings(shows, now, laptops=False)
    out = []
    for ws in shows:
        # When it went live (its `shows.started`, else its first order: the first win's line
        # can be captured a moment before Whatnot stamps that order), else when it was
        # scheduled for (a guess: an early one would take the show before).
        begin = ws.started_at - START_GRACE if ws.started_at else ws.scheduled_start_at
        ending = found[id(ws)]
        end = min(ending.at, now) if ending.over and ending.at is not None else now
        if end > begin:
            out.append(Window(ws, begin, end))
    return sorted(out, key=lambda w: (w.begin, w.ws.id))


def _only(windows: list[Window], at: datetime) -> Window | None:
    """The one show on air at `at`; None when none was, or when two were (a show Whatnot
    never ended, a schedule that was off): such a moment says nothing."""
    held = [w for w in windows if w.holds(at)]
    return held[0] if len(held) == 1 else None


def _evidence(show: Show, windows: list[Window]) -> dict[int, list[datetime]]:
    """Whatnot show row id → when the recording saw that show sell (see the module
    docstring), by the laptop's clock: its own lines. A show it holds only sales of with no
    line here (made from Whatnot's orders) gets Whatnot's times of those orders instead."""
    lines: dict[int, list[datetime]] = {}
    ordered: dict[int, list[datetime]] = {}
    by_whatnot_id = {w.ws.whatnot_id: w for w in windows}

    def add(window: Window | None, at: datetime | None, into=lines) -> None:
        if window is not None and at is not None:
            into.setdefault(window.ws.id, []).append(at)

    # Wins read from a host/platform or video-player line.
    for at in db.session.scalars(
        select(ChatMessage.captured_at)
        .join(ParsedEvent, ParsedEvent.message_id == ChatMessage.id)
        .where(
            ChatMessage.show_id == show.id,
            ParsedEvent.kind == EventKind.sale,
            ParsedEvent.confidence >= SURE_LINE,
        )
    ):
        add(_only(windows, at), at)
    # Sales Whatnot ties to one of its orders: the order names the show. The sale is this
    # recording's, or its line is (a line can claim a sale another show holds).
    own_lines = select(ChatMessage.id).where(ChatMessage.show_id == show.id)
    for whatnot_show_id, line_show, line_at, ordered_at in db.session.execute(
        select(
            WhatnotOrder.whatnot_show_id,
            ChatMessage.show_id,
            ChatMessage.captured_at,
            Sale.ordered_at,
        )
        .select_from(Sale)
        .join(
            WhatnotOrder,
            and_(
                WhatnotOrder.organization_id == Sale.organization_id,
                WhatnotOrder.whatnot_id == Sale.external_order_id,
            ),
        )
        .outerjoin(ParsedEvent, ParsedEvent.id == Sale.event_id)
        .outerjoin(ChatMessage, ChatMessage.id == ParsedEvent.message_id)
        .where(
            Sale.organization_id == show.organization_id,
            or_(Sale.show_id == show.id, ChatMessage.id.in_(own_lines)),
        )
    ):
        window = by_whatnot_id.get(whatnot_show_id)
        if line_show == show.id:
            add(window, line_at)
        else:
            add(window, ordered_at, ordered)
    # The show's own buyers chatting while it was on air (the seller's page records no wins).
    first, last = db.session.execute(
        select(func.min(ChatMessage.captured_at), func.max(ChatMessage.captured_at)).where(
            ChatMessage.show_id == show.id
        )
    ).one()
    if first is None:
        return {**ordered, **lines}
    for w in windows:
        if w.end < first or w.begin > last:
            continue
        buyers = select(func.lower(WhatnotOrder.buyer_username)).where(
            WhatnotOrder.organization_id == show.organization_id,
            WhatnotOrder.whatnot_show_id == w.ws.whatnot_id,
            WhatnotOrder.buyer_username.is_not(None),
        )
        seen = [
            (name, at)
            for name, at in db.session.execute(
                select(func.lower(ChatMessage.username), ChatMessage.captured_at).where(
                    ChatMessage.show_id == show.id,
                    ChatMessage.captured_at.between(w.begin, w.end),
                    ChatMessage.username != "",
                    func.lower(ChatMessage.username).in_(buyers),
                )
            )
            if _only(windows, at) is w
        ]
        if len({name for name, _ in seen}) >= MIN_BUYERS:
            for _, at in seen:
                add(w, at)
    return {**ordered, **lines}


def _quietest(show: Show, after: datetime, upto: datetime) -> datetime:
    """The moment to split at: the line that ends the longest quiet stretch of the
    recording's chat after `after`, up to `upto` (both the laptop's clock)."""
    times = list(
        db.session.scalars(
            select(ChatMessage.captured_at)
            .where(
                ChatMessage.show_id == show.id,
                ChatMessage.captured_at > after,
                ChatMessage.captured_at <= upto,
            )
            .order_by(ChatMessage.captured_at)
        )
    )
    if not times or times[-1] < upto:
        times.append(upto)
    best, prev = (timedelta(-1), upto), after
    for at in times:
        if at - prev > best[0]:
            best = (at - prev, at)
        prev = at
    return best[1]


@dataclass(frozen=True)
class Decision:
    first: Window
    second: Window
    at: datetime


def decide(show: Show, windows: list[Window]) -> Decision | None:
    """Whether this recording ran through two shows, and where it splits. Changes nothing."""
    from .whatnot import sync

    if show.whatnot_link in ("manual", "made"):
        return None
    seen = _evidence(show, windows)
    sold = [w for w in windows if seen.get(w.ws.id)]
    if len(sold) < 2:
        return None
    first, second = sold[0], sold[1]
    stream = sync._stream_of(show.source_url)
    if stream is not None and stream != first.ws.whatnot_id.lower():
        return None  # its own address names another show: a person decides
    if show.whatnot_link == sync.SPLIT and show.whatnot_show_id != first.ws.id:
        return None  # a half already split off: only a third show after its own is split
    last_first = max(seen[first.ws.id])
    first_second = min(seen[second.ws.id])
    if last_first >= first_second:
        log.info("recording %s: wins of two shows interleave; not split", show.id)
        return None
    return Decision(first, second, _quietest(show, last_first, first_second))


# ---------- the split ----------


class Blocked(Exception):
    """Why the recording cannot be split now, in the shop's words."""


@dataclass
class _Plan:
    sales: list[int]
    orders: list[tuple[Order, list[int]]]  # an Order here, and its sales that move
    clashes: list[str]


def _plan(show: Show, d: Decision) -> _Plan:
    """What moves to the second show, read under the locks."""
    sales = db.session.execute(
        select(Sale, WhatnotOrder.whatnot_show_id, ChatMessage.captured_at)
        .outerjoin(
            WhatnotOrder,
            and_(
                WhatnotOrder.organization_id == Sale.organization_id,
                WhatnotOrder.whatnot_id == Sale.external_order_id,
            ),
        )
        .outerjoin(ParsedEvent, ParsedEvent.id == Sale.event_id)
        .outerjoin(ChatMessage, ChatMessage.id == ParsedEvent.message_id)
        .where(Sale.show_id == show.id)
        .order_by(Sale.id)
        .with_for_update(of=Sale)
    ).all()
    moving = []
    for sale, whatnot_show_id, line_at in sales:
        if whatnot_show_id is not None:
            # Whatnot's order says which show the win was in: its word, whatever the time.
            goes = whatnot_show_id == d.second.ws.whatnot_id
        else:
            at = line_at or sale.ordered_at
            goes = at is not None and at >= d.at
        if goes:
            moving.append(sale.id)
    moving_set = set(moving)
    plan = _Plan(moving, [], [])
    for order in db.session.scalars(
        select(Order).where(Order.show_id == show.id).order_by(Order.id).with_for_update()
    ):
        going = [s.id for s in order.sales if s.id in moving_set]
        if not going:
            continue
        if order.status in FULFILLMENT:
            plan.clashes.append(order.buyer_username)
        plan.orders.append((order, going))
    return plan


def _blocked_words(d: Decision, buyers: list[str]) -> str:
    n = len(buyers)
    names = ", ".join(buyers[:5]) + ("…" if n > 5 else "")
    return (
        f"This laptop recording ran through two shows on Whatnot, {_title(d.first.ws)} and "
        f"{_title(d.second.ws)}, so the app would make the second its own show. It has not: "
        f"{n} {'buyer has' if n == 1 else 'buyers have'} a box being picked, packed or shipped "
        f"with a win from the second show in it ({names}). Put "
        f"{'that box' if n == 1 else 'those boxes'} back to ready to pick and it is split at "
        "the next read of Whatnot's shows (a box that has shipped cannot be split: the two "
        "shows then stay one here)."
    )


def _title(ws: WhatnotShow) -> str:
    return f'"{ws.title}"' if ws.title else "an untitled show"


def _flag(show: Show, summary: str | None) -> None:
    """One open question per recording, rewritten as it changes; settled (summary None)
    once it is split. One a person already closed is not asked again with the same words."""
    items = db.session.scalars(
        select(ReviewItem).where(
            ReviewItem.organization_id == show.organization_id,
            ReviewItem.show_id == show.id,
            ReviewItem.reason == REASON,
        )
    ).all()
    open_items = [i for i in items if i.status is ReviewStatus.open]
    if summary is None:
        for item in open_items:
            item.status = ReviewStatus.resolved
            item.resolution = {"by": "split into two shows"}
            item.resolved_at = datetime.now(UTC)
        return
    if open_items:
        open_items[0].summary = summary
        return
    if any(i.summary == summary and i.resolved_by_id is not None for i in items):
        return
    pipeline.open_review(show.organization_id, REASON, summary, show_id=show.id, dedupe=False)


def _lock(show: Show, d: Decision) -> bool:
    """The parse lock of the recording and of every show of either Whatnot show (a made show
    may fold into a half), in id order as the parser and Whatnot's apply take them, without
    waiting (a parse running: the next read tries again); then the laptop's own show row and
    this one, so no batch of the laptop's is stored meanwhile (ingest takes them KEY SHARE,
    in the same order)."""
    others = db.session.scalars(
        select(Show.id).where(
            Show.organization_id == show.organization_id,
            Show.whatnot_show_id.in_([d.first.ws.id, d.second.ws.id]),
        )
    ).all()
    for show_id in sorted({show.id, *others}):
        if not pipeline._lock_show(show_id):
            return False
    key = laptop_key(show)
    own = db.session.scalar(
        select(Show.id).where(Show.organization_id == show.organization_id, Show.show_key == key)
    )
    for show_id in [i for i in (own, show.id) if i is not None]:
        db.session.execute(select(Show.id).where(Show.id == show_id).with_for_update())
    return True


def _last_chat(show_id: int) -> datetime | None:
    return db.session.scalar(
        select(func.max(ChatMessage.received_at)).where(ChatMessage.show_id == show_id)
    )


def _carry_settings(show: Show, part: Show) -> None:
    """The shelf layout and selling way in use go on in the second show, unless its Whatnot
    show has its own (a shelf code must not stop naming a product mid-show)."""
    from . import sale_mode, shelves

    source = shelves._sources([show.id]).get(show.id)
    if source is not None and part.id not in shelves._sources([part.id]):
        rows = db.session.execute(
            select(ShowShelf.code, ShowShelf.sku_id).where(ShowShelf.show_id == source)
        ).all()
        if rows:
            db.session.execute(
                insert(ShowShelf),
                [{"show_id": part.id, "code": c, "sku_id": k} for c, k in rows],
            )
    mode, chosen = sale_mode.of_show(show)
    if chosen and not sale_mode.of_show(part)[1]:
        db.session.add(ShowSaleMode(show_id=part.id, mode=mode))
    db.session.flush()


def _move_whatnot_questions(show: Show, part: Show, d: Decision, was: int | None) -> None:
    """Review items Whatnot's orders wrote with no sale, line or event to follow go to the
    half whose Whatnot show they are about, as they are (open, dismissed or resolved): that
    show's next apply looks for them there, and finds them known instead of asking again.
    - a declined payment with no sale here: by its order item's show;
    - a payment no sale here answers: by the order it names (one from a file that names no
      order says nothing about its show, and stays);
    - the show's "orders left out" notice (one per show, naming no order): only when the
      recording was the later show's before the split (`was`, read under the locks), so
      its notice was that show's. Run before the made shows fold in: a made show's notice
      is its own show's and must not be taken along."""
    from .whatnot import orders as whatnot_orders

    second_orders = select(WhatnotOrder.id).where(
        WhatnotOrder.organization_id == show.organization_id,
        WhatnotOrder.whatnot_show_id == d.second.ws.whatnot_id,
    )
    declined = select(WhatnotOrderItem.id).where(WhatnotOrderItem.order_id.in_(second_orders))
    named = select(literal("order:") + WhatnotOrder.whatnot_id).where(
        WhatnotOrder.id.in_(second_orders)
    )
    whose = [
        and_(
            ReviewItem.reason == whatnot_orders.ORDER_DECLINED,
            ReviewItem.proposal["orderItem"].as_integer().in_(declined),
        ),
        and_(
            ReviewItem.reason == "unmatched_payment",  # reconcile._review_unmatched
            ReviewItem.proposal["dedupeKey"].as_string().in_(named),
        ),
    ]
    if was is not None and was == d.second.ws.id:
        whose.append(ReviewItem.reason == whatnot_orders.LEFT_OUT)
    db.session.execute(
        update(ReviewItem)
        .where(
            ReviewItem.organization_id == show.organization_id,
            ReviewItem.show_id == show.id,
            ReviewItem.sale_id.is_(None),
            ReviewItem.message_id.is_(None),
            ReviewItem.event_id.is_(None),
            or_(*whose),
        )
        .values(show_id=part.id)
        .execution_options(synchronize_session=False)
    )


def split(show: Show, d: Decision) -> Show | None:
    """Split the recording at `d.at` (see the module docstring). Returns the new show, or
    None when it cannot be done now (a parse running: nothing changed). Raises Blocked,
    having changed nothing, when a person must act first. The caller commits."""
    from . import show_merge
    from .whatnot import sync

    if not _lock(show, d):
        return None
    # Which Whatnot show the recording was before it splits, read under the locks.
    was = db.session.scalar(select(Show.whatnot_show_id).where(Show.id == show.id))
    org_id = show.organization_id
    key = laptop_key(show)
    plan = _plan(show, d)
    if plan.clashes:
        raise Blocked(_blocked_words(d, sorted(set(plan.clashes), key=str.lower)))
    part_key = _part_key(org_id, key, d.second.ws.whatnot_id)
    begins = d.second.ws.started_at or d.second.ws.begins_at or d.at
    with db.session.begin_nested():
        made = db.session.execute(
            insert(Show)
            .values(
                organization_id=org_id,
                show_key=part_key,
                recording_key=key,
                started_at=d.at,
                show_date=show_date(begins),
                channel=show.channel,
                whatnot_show_id=d.second.ws.id,
                whatnot_link=sync.SPLIT,
            )
            .on_conflict_do_nothing(constraint="uq_shows_org_key")
            .returning(Show.id)
        ).scalar()
        if made is None:
            log.warning("recording %s: its part for %s exists already", show.id, d.second.ws.id)
            return None
        part = db.session.get(Show, made)
        lines = db.session.execute(
            update(ChatMessage)
            .where(ChatMessage.show_id == show.id, ChatMessage.captured_at >= d.at)
            .values(show_id=part.id)
            .execution_options(synchronize_session=False)
        ).rowcount
        # What the parser read from a line goes with it (else it reads the line again).
        part_lines = select(ChatMessage.id).where(ChatMessage.show_id == part.id)
        db.session.execute(
            update(ParsedEvent)
            .where(ParsedEvent.message_id.in_(part_lines))
            .values(show_id=part.id)
            .execution_options(synchronize_session=False)
        )
        part_events = select(ParsedEvent.id).where(ParsedEvent.show_id == part.id)
        moving = plan.sales or [0]
        db.session.execute(
            update(Sale)
            .where(Sale.id.in_(moving))
            .values(show_id=part.id)
            .execution_options(synchronize_session=False)
        )
        # Each buyer's Order: all of it moves, or the second show's wins get one of their own.
        touched: list[int] = []
        new_orders = 0
        for order, going in plan.orders:
            if len(going) == len(order.sales):
                order.show_id = part.id
                touched.append(order.id)
                continue
            other = Order(
                organization_id=org_id, show_id=part.id, buyer_username=order.buyer_username
            )
            db.session.add(other)
            db.session.flush()
            db.session.execute(
                update(Sale)
                .where(Sale.id.in_(going))
                .values(order_id=other.id)
                .execution_options(synchronize_session=False)
            )
            touched += [order.id, other.id]
            new_orders += 1
        # Questions follow their sale, else their line.
        db.session.execute(
            update(ReviewItem)
            .where(
                ReviewItem.show_id == show.id,
                or_(
                    ReviewItem.sale_id.in_(moving),
                    and_(
                        ReviewItem.sale_id.is_(None),
                        or_(
                            ReviewItem.message_id.in_(part_lines),
                            ReviewItem.event_id.in_(part_events),
                        ),
                    ),
                ),
            )
            .values(show_id=part.id)
            .execution_options(synchronize_session=False)
        )
        # What Whatnot's orders told a person with nothing of the above to follow: before
        # the recording is tied to the first show and the made shows fold in.
        _move_whatnot_questions(show, part, d, was)
        # Whatnot's lots of the second show's orders, and the parcels most of them are in.
        second_orders = select(WhatnotOrder.whatnot_id).where(
            WhatnotOrder.organization_id == org_id,
            WhatnotOrder.whatnot_show_id == d.second.ws.whatnot_id,
        )
        db.session.execute(
            update(WhatnotLot)
            .where(
                WhatnotLot.show_id == show.id,
                func.split_part(WhatnotLot.whatnot_order_id, "-", 1).in_(second_orders),
            )
            .values(show_id=part.id)
            .execution_options(synchronize_session=False)
        )
        for shipment_id in db.session.scalars(
            select(WhatnotShipment.id).where(WhatnotShipment.show_id == show.id)
        ).all():
            counts = dict(
                db.session.execute(
                    select(WhatnotLot.show_id, func.count())
                    .where(WhatnotLot.shipment_id == shipment_id)
                    .group_by(WhatnotLot.show_id)
                ).all()
            )
            if counts.get(part.id, 0) > counts.get(show.id, 0):
                db.session.execute(
                    update(WhatnotShipment)
                    .where(WhatnotShipment.id == shipment_id)
                    .values(show_id=part.id)
                    .execution_options(synchronize_session=False)
                )
        # When chat last arrived (the Live pill), and the laptop's word that the page moved
        # on, each on the half it is about.
        moved_on = show.moved_on_at if show.moved_on_at and show.moved_on_at >= d.at else None
        db.session.execute(
            update(Show)
            .where(Show.id == show.id)
            .values(
                updated_at=_last_chat(show.id) or Show.updated_at,
                moved_on_at=None if moved_on else Show.moved_on_at,
            )
            .execution_options(synchronize_session=False)
        )
        db.session.execute(
            update(Show)
            .where(Show.id == part.id)
            .values(updated_at=_last_chat(part.id) or d.at, moved_on_at=moved_on)
            .execution_options(synchronize_session=False)
        )
        # Each half is its Whatnot show's for good. One whose address names its show keeps
        # that tie (the address says it already).
        if not sync.by_address(show, d.first.ws):
            sync.tie(show, d.first.ws.id, sync.SPLIT)
        db.session.flush()
        db.session.expire_all()
        part = db.session.get(Show, part.id)
        show = db.session.get(Show, show.id)
        _carry_settings(show, part)
        for order in db.session.scalars(select(Order).where(Order.id.in_(touched or [0]))):
            orders.refresh(order)
        db.session.flush()
        # A show the app made for either Whatnot show folds into its half (into the show's
        # pinned recording; one a person matched waits for the owner's Merge, as before).
        for ws in (d.first.ws, d.second.ws):
            fold = show_merge._plan(ws)
            if (
                fold.folded
                and fold.keeper is not None
                and sync.pinned(fold.keeper, ws)
                and all(s.whatnot_link == "made" for s in fold.folded)
            ):
                try:
                    show_merge.merge(ws, wait=False, made_only=True)
                except show_merge.MergeError as e:
                    raise Blocked(
                        f"This laptop recording ran through two shows on Whatnot, "
                        f"{_title(d.first.ws)} and {_title(d.second.ws)}, so the app would "
                        "make the second its own show and put the show made from Whatnot's "
                        f"orders into it. It has not: {e}"
                    ) from None
        audit.record(
            org_id,
            ACTION,
            target_type="show",
            target_id=part.id,
            details={
                "date": show.show_date.isoformat(),
                "title": d.first.ws.title or "",
                "toDate": part.show_date.isoformat(),
                "toTitle": d.second.ws.title or "",
                "lines": lines,
                "sales": len(plan.sales),
                "orders": len(plan.orders),
                "newOrders": new_orders,
                "from": show.id,
            },
        )
    _flag(show, None)
    return part


def _recent(org_id: int, now: datetime) -> list[Show]:
    from .whatnot import sync

    return list(
        db.session.scalars(
            select(Show)
            .where(
                Show.organization_id == org_id,
                Show.started_at >= now - sync.RECENT_SHOWS,
                Show.updated_at >= now - SPLIT_WITHIN,
                Show.whatnot_link.is_distinct_from("manual"),
                Show.whatnot_link.is_distinct_from("made"),
            )
            .order_by(Show.started_at, Show.id)
        )
    )


def split_recordings(org_id: int) -> int:
    """At a read of Whatnot's shows: split every recent recording that ran through two of
    the shop's shows. Never raises: the reading of Whatnot's shows must go on. Returns how
    many splits were made."""
    now = datetime.now(UTC)
    recordings = _recent(org_id, now)
    if not recordings:
        return 0
    windows = _windows(org_id, now)
    if len(windows) < 2:
        return 0
    made: list[int] = []
    for show_id in [s.id for s in recordings]:
        for _ in range(MAX_SPLITS):
            try:
                with db.session.begin_nested():
                    show = db.session.get(Show, show_id)
                    d = decide(show, windows) if show is not None else None
                    part = split(show, d) if d is not None else None
            except Blocked as e:
                _flag(db.session.get(Show, show_id), str(e))
                log.info("recording %s not split: a person must act first", show_id)
                break
            except OperationalError:
                # A deadlock with the parser or an apply: only this savepoint is undone.
                log.warning("recording %s not split just now", show_id, exc_info=True)
                break
            except Exception:
                # Never at the cost of the read of Whatnot's shows: undone, and logged.
                log.exception("recording %s not split", show_id)
                break
            if part is None:
                break
            made.append(part.id)
            show_id = part.id  # a recording through three shows: its new half splits again
    for part_id in made:
        _read_later(part_id)
    return len(made)


def _read_later(show_id: int) -> None:
    """A part holding lines the parser has not read yet is read (queued once per show)."""
    unread = db.session.scalar(
        select(ChatMessage.id)
        .where(
            ChatMessage.show_id == show_id,
            ~select(ParsedEvent.id).where(ParsedEvent.message_id == ChatMessage.id).exists(),
        )
        .limit(1)
    )
    show = db.session.get(Show, show_id)
    if unread is not None and show is not None:
        pipeline.schedule_parse(show)
