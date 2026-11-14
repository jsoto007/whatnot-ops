"""What a winner said in chat around their win, so a person can hold it against the sale.

A winner names the product in chat after the win (a "CHOOSE ANY PRODUCT YOU WANT #73" lot
names none), may take a while to do it, and may change their mind ("actually the blue
one"). The rules leave all of that as chatter; this only collects those lines next to the
sale and decides nothing. A sale's win time is the earliest evidence of the win: its win
line's capture time (for a player win, when "won!" appeared) or the time Whatnot first
recorded an order for it, whichever is earlier (`won_at`).

One show on Whatnot can be several recordings here (two laptops, or recording started
again): the chat of all of them is read as one, a line both laptops saw counts once, and a
buyer's wins in every recording decide together which win a line goes to.

Clocks. A chat line carries the time on the laptop that recorded it; an order carries
Whatnot's. The times are used as the laptop gave them: a "correction" worked out from upload
delays goes wrong by minutes the first time the Wi-Fi drops. Instead the laptop's clock is
judged (`clocks`): in step with the server, out (or jumping about), or impossible to tell.
A show with a laptop whose clock is out gets no suggested picks (`unsafe`), and the show
page says which laptop and by how much, so a person sets its clock right. A win line's own
time is only ever held against an order's on a laptop known to be in step.
"""

import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import ChatMessage, Device, ParsedEvent, Sale, SaleStatus, Show, WhatnotLot
from . import rules

BEFORE = timedelta(minutes=2)
TOGETHER = timedelta(minutes=15)
MAX_LINES = 20  # per sale on the show page and in the workbook: the ones nearest the win
# Whatnot records the order a moment after the hammer. With no win line to go by, a line
# typed this shortly before the order time still counts as after the win. Kept short on
# purpose: a wider margin hands the buyer's PREVIOUS win's pick to this one when they win
# back to back, which ships a wrong product; a line lost to a narrow margin only waits for a
# person. A guess: the real gap has not been measured on the live shop.
ORDER_LAG = timedelta(seconds=3)
# The same line as two laptops recorded it: the same words from the same person this close.
SAME_LINE = timedelta(seconds=30)
_STANDING = (SaleStatus.detected, SaleStatus.reserved, SaleStatus.paid, SaleStatus.fulfilled)


def _line_time(sale: Sale) -> datetime | None:
    return sale.event.message.captured_at if sale.event and sale.event.message else None


def won_at(sale: Sale) -> datetime | None:
    """When the lot was won, as shown and sorted: the win line's own time, else (a sale the
    app learnt of from Whatnot's order) when Whatnot first recorded an order for it."""
    return _line_time(sale) or sale.ordered_at


# ---------- is a laptop's clock in step with the server's? ----------

# Chat is looked at a minute at a time. A minute counts when it has enough lines, spread
# over enough of it, that were uploaded as they were typed (not in one go afterwards).
_MINUTE = 60
MIN_MINUTE_LINES = 4
MIN_MINUTE_SPAN = timedelta(seconds=20)
MIN_MINUTES = 5  # fewer such minutes than this, and nothing is said about the clock
STEADY_WITHIN = timedelta(seconds=5)
STEADY_SHARE = 0.8
# (typed − received) is the clock's lead less the upload's delay: on a clock that is right
# it is a second or two below zero, more on a slow connection, and never above zero.
CLOCK_AHEAD_MAX = timedelta(seconds=5)
CLOCK_BEHIND_MAX = timedelta(seconds=15)


@dataclass(frozen=True)
class Clock:
    """What can be told about the clock of a laptop that recorded chat.

    `ok` True: in step with the server (and so with Whatnot). False: out, or not steady (it
    was set during the show, or its uploads came in fits and starts). None: cannot be told
    (too little chat, or uploaded in one go afterwards). `ahead`: how far it runs ahead
    (negative: behind), when that is one steady figure."""

    ok: bool | None
    ahead: timedelta | None = None


UNKNOWN = Clock(None)


def _source(device_id: int | None, show_id: int) -> tuple[str, int]:
    """Whose clock stamped a line: the laptop's (several recordings of one laptop share
    it), or for a line with no laptop on record, its recording's."""
    return ("laptop", device_id) if device_id is not None else ("recording", show_id)


def clocks(show_ids) -> dict[tuple[str, int], Clock]:
    """What can be told about each clock that stamped chat in these recordings.

    A line is received after it was typed, by the upload's delay, so (typed − received) can
    never be more than the clock's lead, and for the quickest line of a minute it is within
    a second or two of it. A clock is judged only from minutes uploaded live; it is in step
    when those minutes agree with each other and with the server."""
    ids = list(show_ids)
    if not ids:
        return {}
    minute = func.floor(func.extract("epoch", ChatMessage.captured_at) / _MINUTE)
    lead = func.extract("epoch", ChatMessage.captured_at - ChatMessage.received_at)
    rows = db.session.execute(
        select(
            ChatMessage.device_id,
            ChatMessage.show_id,
            func.count(),
            func.max(lead),
            func.min(ChatMessage.captured_at),
            func.max(ChatMessage.captured_at),
            func.min(ChatMessage.received_at),
            func.max(ChatMessage.received_at),
        )
        .where(ChatMessage.show_id.in_(ids))
        .group_by(ChatMessage.device_id, ChatMessage.show_id, minute)
    ).all()
    leads: dict[tuple[str, int], list[float]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for device_id, show_id, lines, ahead, typed0, typed1, got0, got1 in rows:
        source = _source(device_id, show_id)
        seen.add(source)
        span = typed1 - typed0
        if lines >= MIN_MINUTE_LINES and span >= MIN_MINUTE_SPAN and got1 - got0 >= span / 2:
            leads[source].append(float(ahead))
    out = {}
    for source in seen:
        live = sorted(leads[source])
        if len(live) < MIN_MINUTES:
            out[source] = UNKNOWN
            continue
        middle = live[len(live) // 2]
        near = sum(1 for x in live if abs(x - middle) <= STEADY_WITHIN.total_seconds())
        if near < STEADY_SHARE * len(live):
            out[source] = Clock(False)  # not one steady figure: nothing more can be said
            continue
        ahead = timedelta(seconds=middle)
        out[source] = Clock(-CLOCK_BEHIND_MAX <= ahead <= CLOCK_AHEAD_MAX, ahead)
    return out


def _sources_of(show_ids) -> dict[int, set[tuple[str, int]]]:
    """recording id → the clocks that stamped its chat."""
    out: dict[int, set[tuple[str, int]]] = defaultdict(set)
    for show_id, device_id in db.session.execute(
        select(ChatMessage.show_id, ChatMessage.device_id)
        .where(ChatMessage.show_id.in_(list(show_ids)))
        .distinct()
    ):
        out[show_id].add(_source(device_id, show_id))
    return out


def unsafe(shows: list[Show]) -> set[int]:
    """The recordings (by id) for which a pick must not be suggested, because a chat line may
    be stamped far from when it was typed and would be handed to the wrong win:

    - a show with a laptop whose clock is known to be out; any recording of the same Whatnot
      show counts, because their chat is read as one;
    - a show whose chat was stamped by more than one clock, unless every one of them is
      known to be in step: the lines of two laptops are only one chat when their clocks
      agree, and "cannot be told" is not agreement.

    One laptop whose clock cannot be told (too little chat, or uploaded afterwards) is read
    as it always was: its lines and its wins are on the same clock."""
    groups = {show.id: recordings_of(show) for show in shows}
    every = {i for ids in groups.values() for i in ids}
    if not every:
        return set()
    state = clocks(every)
    stamped = _sources_of(every)
    out = set()
    for show_id, ids in groups.items():
        sources = {src for i in ids for src in stamped[i]}
        known = [state.get(src, UNKNOWN).ok for src in sources]
        if any(ok is False for ok in known) or (len(sources) > 1 and not all(known)):
            out.add(show_id)
    return out


def clock_problems(show: Show) -> list[dict]:
    """For the show page: each laptop whose clock is out for this show, with how many
    seconds ahead it runs (negative: behind; None when it is not one steady figure)."""
    ids = recordings_of(show)
    state = clocks(ids)
    out = []
    for (kind, key), clock in sorted(state.items()):
        if clock.ok is not False:
            continue
        name = None
        if kind == "laptop":
            device = db.session.get(Device, key)
            name = device.name if device is not None else None
        seconds = round(clock.ahead.total_seconds()) if clock.ahead is not None else None
        out.append({"laptop": name, "seconds": seconds})
    return out


def picks_from(sale: Sale, in_step: frozenset = frozenset()) -> datetime | None:
    """From when a chat line can be about this win.

    - No win line (a sale made from Whatnot's order): the order's time less ORDER_LAG.
    - A win read from the video player: that line's time. It is stamped at the hammer by the
      same laptop clock as the chat it is compared with, so no other clock comes into it.
    - A win line someone typed ("sold to…", which can come well after the hammer): its time,
      or the order's less ORDER_LAG when that is earlier, but only on a laptop whose clock
      is known to be in step (`in_step`: see `clocks`). Otherwise the two are on different
      clocks and the line's time stands."""
    order = sale.ordered_at - ORDER_LAG if sale.ordered_at is not None else None
    line = sale.event.message if sale.event and sale.event.message else None
    if line is None:
        return order
    typed = line.source != "player"
    if typed and order is not None and _source(line.device_id, line.show_id) in in_step:
        return min(line.captured_at, order)
    return line.captured_at


def recordings_of(show: Show) -> list[int]:
    """This recording and every other recording of the same Whatnot show, oldest first."""
    if show.whatnot_show_id is None:
        return [show.id]
    ids = db.session.scalars(
        select(Show.id)
        .where(
            Show.organization_id == show.organization_id,
            Show.whatnot_show_id == show.whatnot_show_id,
        )
        .order_by(Show.id)
    ).all()
    return list(ids) or [show.id]


def lot_of(sale: Sale) -> int | None:
    """Whatnot's lot number of the sale: the win's own, else the one in its title."""
    lot = (sale.event.details or {}).get("lot") if sale.event else None
    if isinstance(lot, int) and not isinstance(lot, bool):
        return lot
    if isinstance(lot, str) and lot.isdigit():
        return int(lot)
    return rules.lot_number(sale.item_text)


def _handle(name: str) -> str:
    return name.strip().lstrip("@").lower()


def _pattern(buyer: str, *, bare: bool) -> str:
    at = "@" if not bare else "@?"
    # Handles may hold "." and "-", so "@bob." ends at "bob" but "@bob.x" is someone else.
    end = r"(?!\w|[.-][\w.-])"
    return rf"(?<![\w.@-]){at}{re.escape(buyer)}{end}"


def _names(text: str, buyer: str, *, bare: bool) -> bool:
    """`text` names `buyer` as a whole handle: "@buyer" always, the bare handle when `bare`.
    The reference definition; `_Named` answers the same question for many buyers at once."""
    return re.search(_pattern(buyer, bare=bare), text, re.I) is not None


# A handle as `_pattern` sees it: a run of handle characters that starts the text or follows
# a character that cannot be in a handle, optionally right after such an "@".
_RUN = re.compile(r"(?<![\w.@-])(@?)([\w.-]+)")
# Buyers checked through `_RUN` (lower-case ASCII, as Whatnot handles are); anything else
# keeps its own regex. Same answer as `_names`, except that look-alike letters `re.I` folds
# into ASCII ("ſ", "ı", "İ") no longer name a buyer: stricter, the safe way for chat text.
_PLAIN = re.compile(r"[a-z0-9_.-]+")


class _Named:
    """Which of a show's buyers a line names, in one pass over the line. Asking `_names`
    per buyer cost (lines x buyers) regex searches, and past 256 buyers Python's regex cache
    (512 patterns, two per buyer) recompiled every one: ~30 s for a 300-buyer show."""

    def __init__(self, buyers):
        self.plain = {b for b in buyers if _PLAIN.fullmatch(b)}
        self.other = {
            b: (re.compile(_pattern(b, bare=True), re.I), re.compile(_pattern(b, bare=False), re.I))
            for b in buyers
            if b not in self.plain
        }

    def __call__(self, text: str, *, bare: bool) -> set[str]:
        found: set[str] = set()
        for at, run in _RUN.findall(text):
            if not (bare or at):
                continue
            run = run.lower()
            if run in self.plain:
                found.add(run)
            # One trailing "." or "-" ends the handle ("@bob." is bob); two do not.
            if len(run) > 1 and run[-1] in ".-" and run[:-1] in self.plain:
                found.add(run[:-1])
        for b, (with_or_without_at, with_at) in self.other.items():
            if (with_or_without_at if bare else with_at).search(text):
                found.add(b)
        return found


def _roles(m: ChatMessage, named: _Named, buyers: dict, host: str) -> list[tuple[str, str]]:
    """(buyer, role) for each buyer in `buyers` the line is about, in `buyers` order: the
    buyer's own line, else one naming them ("@buyer"; the bare handle from the host or the
    platform)."""
    user = _handle(m.username)
    is_host = bool(host) and user == host
    role = "platform" if not user else "host" if is_host else "other"
    about = dict.fromkeys(named(m.raw_text or "", bare=not user or is_host), role)
    if user in buyers:
        about[user] = "buyer"
    return [(b, about[b]) for b in buyers if b in about] if len(about) > 1 else list(about.items())


def _line(m: ChatMessage, role: str, *, after: bool, names_lot: bool) -> dict:
    return {
        "seq": m.seq,
        "username": m.username,
        "body": m.body,
        "capturedAt": m.captured_at.isoformat(),
        "role": role,
        "after": after,
        "namesLot": names_lot,
    }


def _key(m: ChatMessage) -> tuple[str, str]:
    return (_handle(m.username), " ".join((m.body or "").split()).lower())


def _once(messages: list[ChatMessage], own: list[ChatMessage]) -> list[ChatMessage]:
    """The lines of several recordings as one chat, oldest first: a line two laptops both
    recorded counts once, and so does another laptop's copy of a win line (`own`: the win
    lines of the sales, which are evidence, not something the winner said). The same words
    twice in ONE recording are two lines: the person said it twice."""
    wins: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for m in own:
        wins[_key(m)].append(m.captured_at)
    seen: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    out = []
    for m in sorted(messages, key=lambda m: (m.captured_at, m.show_id, m.seq)):
        key, at = _key(m), m.captured_at
        if any(abs(at - t) <= SAME_LINE for t in wins.get(key, ())):
            continue
        if any(show_id != m.show_id and abs(at - t) <= SAME_LINE for t, show_id in seen[key]):
            continue
        seen[key].append((at, m.show_id))
        out.append(m)
    return out


def _lines(
    show_ids: list[int],
    buyers: list[str],
    host: str,
    since: datetime | None = None,
    own: list[ChatMessage] | None = None,
):
    """The chat lines of these recordings (one show) that may be about these buyers, in
    order."""
    candidates = or_(
        func.lower(ChatMessage.username).in_(buyers),
        ChatMessage.raw_text.contains("@"),
        ChatMessage.username == "",
        *([func.lower(ChatMessage.username) == host] if host else []),
    )
    q = select(ChatMessage).where(
        ChatMessage.show_id.in_(show_ids), ChatMessage.source == "chat", candidates
    )
    if since is not None:
        q = q.where(ChatMessage.captured_at >= since)
    if len(show_ids) == 1:
        return db.session.scalars(q.order_by(ChatMessage.seq)).all()
    return _once(list(db.session.scalars(q)), own or [])


def around(sales: list[Sale], *, limit: int | None = MAX_LINES) -> dict[int, list[dict]]:
    """Chat lines per sale id (oldest first), for sales whose win time is known. Pass all
    of a show's standing sales: which win a line goes to depends on the buyer's others.
    `limit` keeps the ones nearest the win; None keeps every one (the order slips)."""
    out: dict[int, list[dict]] = {s.id: [] for s in sales}
    asked = [s for s in sales if picks_from(s) is not None and s.buyer_username]
    if not asked:
        return out
    shows = {
        show.id: show
        for show in db.session.scalars(select(Show).where(Show.id.in_({s.show_id for s in asked})))
    }
    # One show on Whatnot is one chat, however many recordings it was made in.
    groups: dict[tuple, list[Sale]] = defaultdict(list)
    for s in asked:
        show = shows[s.show_id]
        whole = show.whatnot_show_id
        groups[("whatnot", whole) if whole is not None else ("recording", show.id)].append(s)
    recordings = {key: recordings_of(shows[group[0].show_id]) for key, group in groups.items()}
    state = clocks({i for ids in recordings.values() for i in ids})
    in_step = frozenset(source for source, clock in state.items() if clock.ok)
    for key, group in groups.items():
        show = shows[group[0].show_id]
        ids = recordings[key]
        everyone = group
        if len(ids) > 1:
            # A buyer's wins in the other recordings decide, with these, which win a line
            # goes to; only the sales asked for are answered.
            others = db.session.scalars(
                select(Sale)
                .where(
                    Sale.show_id.in_(ids),
                    Sale.status.in_(_STANDING),
                    Sale.id.not_in([s.id for s in group]),
                )
                .options(selectinload(Sale.event).selectinload(ParsedEvent.message))
            ).all()
            everyone = [
                *group,
                *(s for s in others if picks_from(s) is not None and s.buyer_username),
            ]
        _fill(show, ids, everyone, out, in_step)
    if limit is not None:
        for lines in out.values():
            del lines[limit:]
    return out


def _fill(
    show: Show,
    show_ids: list[int],
    sales: list[Sale],
    out: dict[int, list[dict]],
    in_step: frozenset,
) -> None:
    """`sales`: every sale whose wins decide where a line goes; `out` holds the ones asked
    for, and only those are answered. `in_step`: the clocks known to be right (`clocks`)."""
    host = _handle(show.organization.host_username or "")
    wins: dict[str, list[tuple[datetime, Sale]]] = defaultdict(list)
    for s in sales:
        wins[_handle(s.buyer_username)].append((picks_from(s, in_step), s))
    for w in wins.values():
        w.sort(key=lambda x: (x[0], x[1].id))
    lots = {s.id: lot_of(s) for s in sales}
    # A sale the app learnt of from Whatnot's order has no win line of its own.
    own = {s.event.message_id for s in sales if s.event is not None}
    win_lines = [s.event.message for s in sales if s.event and s.event.message]
    # No product and no word from the buyer since the win: their next line may be its pick.
    waiting = {s.id for s in sales if s.sku_id is None}
    since = min(picks_from(s, in_step) for s in sales) - BEFORE
    names = _Named(wins)
    for m in _lines(show_ids, list(wins), host, since, win_lines):
        if m.id in own:
            continue
        named = rules.lot_refs(m.body)
        for buyer, role in _roles(m, names, wins, host):
            for sale, after, by_lot in _owners(wins[buyer], m.captured_at, named, lots, waiting):
                if role == "buyer":
                    waiting.discard(sale.id)
                if sale.id in out:
                    out[sale.id].append(_line(m, role, after=after, names_lot=by_lot))


def _owners(
    wins: list[tuple[datetime, Sale]],
    at: datetime,
    named: set[int],
    lots: dict[int, int | None],
    waiting: set[int],
) -> list[tuple[Sale, bool, bool]]:
    """(sale, the line came after its win, the line named its lot) for each win of one
    buyer that a line at `at` belongs to (see the module docstring)."""
    if named:
        by_lot = [(t, s) for t, s in wins if lots[s.id] in named and at >= t - BEFORE]
        if by_lot:
            return [(s, at >= t, True) for t, s in by_lot]
    i = bisect_right([t for t, _ in wins], at)
    if i:
        latest = wins[i - 1][1]
        earlier = [
            (s, True, False) for t, s in wins[: i - 1] if s.id in waiting and at - t <= TOGETHER
        ]
        return [*earlier, (latest, True, False)]
    if wins and wins[0][0] - at <= BEFORE:
        return [(wins[0][1], False, False)]
    return []


def about_lots(show: Show, lots: list[WhatnotLot]) -> dict[int, list[dict]]:
    """For Whatnot lots the app has no win for (so no win time to look around): the lines
    that name the lot's number, from its buyer, or the host or platform about them."""
    out: dict[int, list[dict]] = {lot.id: [] for lot in lots}
    want: dict[str, list[WhatnotLot]] = defaultdict(list)
    for lot in lots:
        if lot.lot_number is not None and lot.recipient:
            want[_handle(lot.recipient)].append(lot)
    if not want:
        return out
    host = _handle(show.organization.host_username or "")
    names = _Named(want)
    for m in _lines(recordings_of(show), list(want), host):
        named = rules.lot_refs(m.body)
        if not named:
            continue
        for buyer, role in _roles(m, names, want, host):
            for lot in want[buyer]:
                if lot.lot_number in named:
                    out[lot.id].append(_line(m, role, after=True, names_lot=True))
    return out
