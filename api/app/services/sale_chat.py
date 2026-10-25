"""What a winner said in chat around their win, so a person can hold it against the sale.

A buyer who wants a different item or price says so in chat ("can I get the red one
instead"), which the rules leave as chatter. This only collects those lines next to the
sale; it decides nothing. A sale's win time is its line's capture time (for a player win,
when "won!" appeared).

A line belongs to a sale when it is the buyer's own, names "@buyer", or is a host or
platform line naming the buyer. Each line goes to one sale only: the buyer's latest win
before it (up to AFTER later), else their next win (up to BEFORE earlier).
"""

import re
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select

from ..db import db
from ..models import ChatMessage, Sale, Show

BEFORE = timedelta(minutes=2)
AFTER = timedelta(minutes=10)
MAX_LINES = 20  # per sale


def won_at(sale: Sale) -> datetime | None:
    return sale.event.message.captured_at if sale.event else None


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


def around(sales: list[Sale]) -> dict[int, list[dict]]:
    """Chat lines per sale id (oldest first), for sales whose win time is known."""
    out: dict[int, list[dict]] = {s.id: [] for s in sales}
    by_show: dict[int, list[Sale]] = defaultdict(list)
    for s in sales:
        if won_at(s) is not None and s.buyer_username:
            by_show[s.show_id].append(s)
    for show_id, group in by_show.items():
        _fill(db.session.get(Show, show_id), group, out)
    return out


def _fill(show: Show, sales: list[Sale], out: dict[int, list[dict]]) -> None:
    host = _handle(show.organization.host_username or "")
    wins: dict[str, list[tuple[datetime, Sale]]] = defaultdict(list)
    for s in sales:
        wins[_handle(s.buyer_username)].append((won_at(s), s))
    for w in wins.values():
        w.sort(key=lambda x: (x[0], x[1].id))
    times = [won_at(s) for s in sales]
    own = {s.event.message_id for s in sales}
    candidates = or_(
        func.lower(ChatMessage.username).in_(list(wins)),
        ChatMessage.raw_text.contains("@"),
        ChatMessage.username == "",
        *([func.lower(ChatMessage.username) == host] if host else []),
    )
    lines = db.session.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.show_id == show.id,
            ChatMessage.source == "chat",
            ChatMessage.captured_at >= min(times) - BEFORE,
            ChatMessage.captured_at <= max(times) + AFTER,
            candidates,
        )
        .order_by(ChatMessage.seq)
    ).all()
    for m in lines:
        if m.id in own:
            continue
        for buyer, w in wins.items():
            role = _role(m, buyer, host)
            if role is None:
                continue
            sale = _owner(w, m.captured_at)
            if sale is not None and len(out[sale.id]) < MAX_LINES:
                out[sale.id].append(
                    {
                        "seq": m.seq,
                        "username": m.username,
                        "body": m.body,
                        "capturedAt": m.captured_at.isoformat(),
                        "role": role,
                    }
                )


def _owner(wins: list[tuple[datetime, Sale]], at: datetime) -> Sale | None:
    i = bisect_right([t for t, _ in wins], at)
    if i and at - wins[i - 1][0] <= AFTER:
        return wins[i - 1][1]
    if i < len(wins) and wins[i][0] - at <= BEFORE:
        return wins[i][1]
    return None
