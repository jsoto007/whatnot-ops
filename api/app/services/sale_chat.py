"""What a winner said in chat around their win, so a person can hold it against the sale.

A winner names the product in chat after the win (a "CHOOSE ANY PRODUCT YOU WANT #73" lot
names none), may take a while to do it, and may change their mind ("actually the blue
one"). The rules leave all of that as chatter; this only collects those lines next to the
sale and decides nothing. A sale's win time is its line's capture time (for a player win,
when "won!" appeared).

A line belongs to a sale when it is the buyer's own, names "@buyer", or is a host or
platform line naming the buyer. Which of the buyer's wins it goes to:
- a line that names one of their lots ("#72 red please") goes to that win, when it comes
  after it or at most BEFORE ahead of it;
- otherwise to their latest win before it: a win's lines run until the buyer's next win,
  or the end of the show, however long they take;
- and also to their earlier wins from the last TOGETHER that have no product and no word
  from them yet (two quick wins, then "red one and blue one");
- a line before the buyer's first win goes to it when at most BEFORE ahead.
Each line says whether it came after the win (`after`) and whether it named the lot
(`namesLot`): the order slip prints only those (services/order_slips.py).
"""

import re
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from ..db import db
from ..models import ChatMessage, Sale, Show, WhatnotLot
from . import rules

BEFORE = timedelta(minutes=2)
TOGETHER = timedelta(minutes=15)
MAX_LINES = 20  # per sale on the show page and in the workbook: the ones nearest the win


def won_at(sale: Sale) -> datetime | None:
    return sale.event.message.captured_at if sale.event else None


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


def _lines(show: Show, buyers: list[str], host: str, since: datetime | None = None):
    """The show's chat lines that may be about these buyers, in order."""
    candidates = or_(
        func.lower(ChatMessage.username).in_(buyers),
        ChatMessage.raw_text.contains("@"),
        ChatMessage.username == "",
        *([func.lower(ChatMessage.username) == host] if host else []),
    )
    q = select(ChatMessage).where(
        ChatMessage.show_id == show.id, ChatMessage.source == "chat", candidates
    )
    if since is not None:
        q = q.where(ChatMessage.captured_at >= since)
    return db.session.scalars(q.order_by(ChatMessage.seq)).all()


def around(sales: list[Sale], *, limit: int | None = MAX_LINES) -> dict[int, list[dict]]:
    """Chat lines per sale id (oldest first), for sales whose win time is known. Pass all
    of a show's standing sales: which win a line goes to depends on the buyer's others.
    `limit` keeps the ones nearest the win; None keeps every one (the order slips)."""
    out: dict[int, list[dict]] = {s.id: [] for s in sales}
    by_show: dict[int, list[Sale]] = defaultdict(list)
    for s in sales:
        if won_at(s) is not None and s.buyer_username:
            by_show[s.show_id].append(s)
    for show_id, group in by_show.items():
        _fill(db.session.get(Show, show_id), group, out)
    if limit is not None:
        for lines in out.values():
            del lines[limit:]
    return out


def _fill(show: Show, sales: list[Sale], out: dict[int, list[dict]]) -> None:
    host = _handle(show.organization.host_username or "")
    wins: dict[str, list[tuple[datetime, Sale]]] = defaultdict(list)
    for s in sales:
        wins[_handle(s.buyer_username)].append((won_at(s), s))
    for w in wins.values():
        w.sort(key=lambda x: (x[0], x[1].id))
    lots = {s.id: lot_of(s) for s in sales}
    own = {s.event.message_id for s in sales}
    # No product and no word from the buyer since the win: their next line may be its pick.
    waiting = {s.id for s in sales if s.sku_id is None}
    since = min(won_at(s) for s in sales) - BEFORE
    names = _Named(wins)
    for m in _lines(show, list(wins), host, since):
        if m.id in own:
            continue
        named = rules.lot_refs(m.body)
        for buyer, role in _roles(m, names, wins, host):
            for sale, after, by_lot in _owners(wins[buyer], m.captured_at, named, lots, waiting):
                if role == "buyer":
                    waiting.discard(sale.id)
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
    for m in _lines(show, list(want), host):
        named = rules.lot_refs(m.body)
        if not named:
            continue
        for buyer, role in _roles(m, names, want, host):
            for lot in want[buyer]:
                if lot.lot_number in named:
                    out[lot.id].append(_line(m, role, after=True, names_lot=True))
    return out
