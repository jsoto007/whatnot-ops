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


def _names(text: str, buyer: str, *, bare: bool) -> bool:
    """`text` names `buyer` as a whole handle: "@buyer" always, the bare handle when `bare`."""
    at = "@" if not bare else "@?"
    # Handles may hold "." and "-", so "@bob." ends at "bob" but "@bob.x" is someone else.
    end = r"(?!\w|[.-][\w.-])"
    return re.search(rf"(?<![\w.@-]){at}{re.escape(buyer)}{end}", text, re.I) is not None


def _role(m: ChatMessage, buyer: str, host: str) -> str | None:
    user = _handle(m.username)
    if user == buyer:
        return "buyer"
    is_host = bool(host) and user == host
    if not _names(m.raw_text, buyer, bare=not user or is_host):
        return None
    return "platform" if not user else "host" if is_host else "other"


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
    for m in _lines(show, list(wins), host, since):
        if m.id in own:
            continue
        named = rules.lot_refs(m.body)
        for buyer, w in wins.items():
            role = _role(m, buyer, host)
            if role is None:
                continue
            for sale, after, by_lot in _owners(w, m.captured_at, named, lots, waiting):
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
    for m in _lines(show, list(want), host):
        named = rules.lot_refs(m.body)
        if not named:
            continue
        for buyer, mine in want.items():
            role = _role(m, buyer, host)
            if role is None:
                continue
            for lot in mine:
                if lot.lot_number in named:
                    out[lot.id].append(_line(m, role, after=True, names_lot=True))
    return out
