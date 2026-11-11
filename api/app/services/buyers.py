"""Buyer profiles: who a buyer is (Whatnot's account id and every name they have used), what
the team says about them (a note, a VIP or watch-list flag) and what they have bought.

What they bought is never stored here. It is worked out from the shop's sales by name
(`services/dashboard.py::buyer_figures`), and a person's names are added up. A buyer
nobody has said anything about is still listed, from their sales, under the name they
won with; a profile row exists once Whatnot has named the account on an order or someone
on the team has flagged or noted the buyer.

**One writer per shop.** Everything that changes who holds a name (a new profile, a
rename, two profiles turning out to be one account) runs under the shop's buyers lock,
in a short transaction of its own: `link()` after Whatnot's orders were read (its own
job, never inside the order sync), `annotate()` when someone saves a flag or a note.

Only Whatnot's user id and usernames are kept. No address, email or phone."""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import aliased

from ..db import db
from ..models import Buyer, BuyerName, Sale, Show, User, WhatnotOrder
from . import audit, dashboard, jobs, showdate
from .text import clean, typed

log = logging.getLogger(__name__)

FLAGS = ("vip", "watch")
NAME_MAX = 80
NOTE_MAX = 2000
PAGE = 100
MAX_LOOKUP = 50  # names one lookup may ask about (the laptop asks about one winner)
PROBLEMS_LIMIT = 50
# The class half of the (class, shop id) advisory lock; see pipeline.PARSE_LOCK.
BUYERS_LOCK = 0x4275_7972  # "Buyr"
STATE = "buyers:names"  # hidden from Settings (":"): bookkeeping, not a reading of Whatnot
FULL_EVERY = timedelta(days=1)
CURSOR_OVERLAP = 1000  # order rows re-examined below the cursor on every quick pass
KEEP = object()
SORTS = ("spent", "items", "shows", "recent", "problems", "name")


class BuyerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def name_of(value) -> str:
    """A username as it is kept: one clean line, no leading "@", at most 80 characters in
    either case (a few letters, such as "İ", grow when lower-cased)."""
    name = typed(value.lstrip("@ ") if isinstance(value, str) else value, NAME_MAX)
    while len(name.lower()) > NAME_MAX:
        name = name[:-1]
    return name


def _lock(org_id: int) -> None:
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :org)"), {"cls": BUYERS_LOCK, "org": org_id}
    )


def holders(org_id: int, names) -> dict[str, Buyer]:
    """lower-case name → the profile that holds it (names nobody holds are left out)."""
    lows = sorted({n.lower() for n in names if n})
    if not lows:
        return {}
    rows = db.session.execute(
        select(BuyerName.name_lower, Buyer)
        .join(Buyer, Buyer.id == BuyerName.buyer_id)
        .where(BuyerName.organization_id == org_id, BuyerName.name_lower.in_(lows))
    ).all()
    return {low: b for low, b in rows}


def _names(org_id: int, buyer_ids=None) -> dict[int, list[BuyerName]]:
    """profile id → its names, the most recently used first."""
    q = (
        select(BuyerName)
        .where(BuyerName.organization_id == org_id)
        .order_by(BuyerName.seen_at.desc().nulls_last(), BuyerName.id.desc())
    )
    if buyer_ids is not None:
        ids = sorted(buyer_ids)
        if not ids:
            return {}
        q = q.where(BuyerName.buyer_id.in_(ids))
    out: dict[int, list[BuyerName]] = {}
    for n in db.session.scalars(q):
        out.setdefault(n.buyer_id, []).append(n)
    return out


def names_for(org_id: int, username) -> list[str]:
    """Every name (lower case) of the person who holds this name; just the name itself
    for someone with no profile."""
    name = name_of(username)
    if not name:
        raise BuyerError("Which buyer?")
    buyer = holders(org_id, [name]).get(name.lower())
    if buyer is None:
        return [name.lower()]
    return [n.name_lower for n in _names(org_id, [buyer.id]).get(buyer.id, [])] or [name.lower()]


# ---------- who is who ----------


def ensure(org_id: int, username) -> Buyer:
    """The profile that holds this name, made if nobody holds it yet. Takes the shop's
    buyers lock: the caller's transaction should be a short one."""
    name = name_of(username)
    if not name:
        raise BuyerError("Which buyer?")
    _lock(org_id)
    held = holders(org_id, [name]).get(name.lower())
    if held is not None:
        return held
    buyer = Buyer(organization_id=org_id, username=name)
    db.session.add(buyer)
    db.session.flush()
    db.session.add(
        BuyerName(organization_id=org_id, buyer_id=buyer.id, name_lower=name.lower(), username=name)
    )
    db.session.flush()
    # Whatnot may already have put this name on an order: the profile is then that
    # account's from the start (and a later rename follows it).
    latest = _latest_user(org_id, name.lower())
    if latest is not None:
        _seen(org_id, *latest)
        buyer = holders(org_id, [name])[name.lower()]
    return buyer


def _wins(at: datetime | None, known: datetime | None) -> bool:
    """A dated sighting that is strictly newer than what is known. An order with no date
    never changes who holds a name or what a buyer is called, and a tie changes nothing:
    two answers that are equally good must not take turns."""
    return at is not None and (known is None or at > known)


def _latest_user(org_id: int, low: str) -> tuple[str, str, datetime | None] | None:
    """The account that most recently used this name on a Whatnot order: (id, name as
    written, when), or None when no order carries it."""
    row = db.session.execute(
        select(WhatnotOrder.buyer_id, WhatnotOrder.buyer_username, WhatnotOrder.ordered_at)
        .where(
            WhatnotOrder.organization_id == org_id,
            func.lower(WhatnotOrder.buyer_username) == low,
            WhatnotOrder.buyer_id.is_not(None),
        )
        .order_by(WhatnotOrder.ordered_at.desc().nulls_last(), WhatnotOrder.id.desc())
        .limit(1)
    ).first()
    if row is None or not name_of(row[1]):
        return None
    return row[0], name_of(row[1]), row[2]


def _merge(org_id: int, src: Buyer, into: Buyer) -> None:
    """A profile the team made for a name turns out to be an account Whatnot already told
    us about: its names, flag and note move over and it is gone."""
    for n in db.session.scalars(select(BuyerName).where(BuyerName.buyer_id == src.id)):
        n.buyer_id = into.id
    if src.flag and (into.flag is None or src.flag == "watch"):
        into.flag = src.flag  # of two flags the cautious one stands
    if src.note and src.note not in (into.note or ""):
        into.note = (f"{into.note}\n{src.note}" if into.note else src.note)[:NOTE_MAX]
        if into.noted_at is None or (src.noted_at is not None and src.noted_at > into.noted_at):
            into.noted_by_id, into.noted_at = src.noted_by_id, src.noted_at
    audit.record(
        org_id,
        "buyer.merged",
        actor_type="whatnot",
        target_type="buyer",
        target_id=into.id,
        details={"buyer": into.username, "other": src.username},
    )
    db.session.flush()
    db.session.delete(src)
    db.session.flush()


def _seen(org_id: int, uid: str, name: str, at: datetime | None) -> None:
    """Whatnot put this account id and this name on an order placed at `at`. Under the
    shop's buyers lock, so reading and then writing is one step.

    Between two accounts a name goes to the one that used it on the newer order, and only
    on a strictly newer one. A sighting that loses changes nothing and makes no profile."""
    low = name.lower()
    me = db.session.scalar(
        select(Buyer).where(Buyer.organization_id == org_id, Buyer.whatnot_user_id == uid)
    )
    held = db.session.scalar(
        select(BuyerName).where(BuyerName.organization_id == org_id, BuyerName.name_lower == low)
    )
    holder = db.session.get(Buyer, held.buyer_id) if held is not None else None
    if holder is not None and (me is None or holder.id != me.id):
        if holder.whatnot_user_id is None:
            # A profile the team made for the name: it is the account's that uses the
            # name now, not an earlier owner's whose old order happens to be read first.
            latest = _latest_user(org_id, low)
            if latest is not None and latest[0] != uid:
                _seen(org_id, *latest)
                return
        elif not _wins(at, held.seen_at):
            return  # an older (or undated) order of an account the name has since left
    if me is None:
        if holder is not None and holder.whatnot_user_id is None:
            holder.whatnot_user_id = uid  # the profile the team made for this name is theirs
            me = holder
        else:
            me = Buyer(organization_id=org_id, whatnot_user_id=uid, username=name, username_at=at)
            db.session.add(me)
            db.session.flush()
    if held is None:
        held = BuyerName(
            organization_id=org_id, buyer_id=me.id, name_lower=low, username=name, seen_at=at
        )
        db.session.add(held)
    elif held.buyer_id == me.id:
        if _wins(at, held.seen_at):
            held.seen_at, held.username = at, name
    elif holder.whatnot_user_id is None:
        _merge(org_id, holder, me)
        held.seen_at, held.username = at, name
    else:
        # Another account used this name before; Whatnot says it is this one's now. That
        # account keeps its flag and note, and is known again when it orders under a new
        # name. What was won under the name before today's show date is not this
        # buyer's: `since_date` keeps it out of their figures and their history.
        held.buyer_id, held.seen_at, held.username = me.id, at, name
        held.since_date = showdate.show_date(at)
        audit.record(
            org_id,
            "buyer.name_moved",
            actor_type="whatnot",
            target_type="buyer",
            target_id=me.id,
            details={"name": name},
        )
    if me.username != name and _wins(at, me.username_at):
        if me.username.lower() != low:
            audit.record(
                org_id,
                "buyer.renamed",
                actor_type="whatnot",
                target_type="buyer",
                target_id=me.id,
                details={"before": me.username, "after": name},
            )
        me.username = name
    if _wins(at, me.username_at):
        me.username_at = at
    db.session.flush()


def _unlinked(org_id: int, after: int) -> list[tuple[str, str, datetime | None]]:
    """(account id, name, when last used) on Whatnot's orders that `_seen` would act on:
    a name nobody holds, a name held by a profile the team made, a name another account
    holds that this one used on a strictly newer order, or a name that is theirs and
    newer than what we call them. A pair that can change nothing is never returned, so
    reading everything again settles."""
    name = func.lower(WhatnotOrder.buyer_username)
    at = WhatnotOrder.ordered_at
    holder = aliased(Buyer)
    rows = db.session.execute(
        select(WhatnotOrder.buyer_id, WhatnotOrder.buyer_username, func.max(at))
        .outerjoin(
            Buyer,
            and_(Buyer.organization_id == org_id, Buyer.whatnot_user_id == WhatnotOrder.buyer_id),
        )
        .outerjoin(
            BuyerName, and_(BuyerName.organization_id == org_id, BuyerName.name_lower == name)
        )
        .outerjoin(holder, holder.id == BuyerName.buyer_id)
        .where(
            WhatnotOrder.organization_id == org_id,
            WhatnotOrder.id > after,
            WhatnotOrder.buyer_id.is_not(None),
            WhatnotOrder.buyer_username.is_not(None),
            or_(
                BuyerName.id.is_(None),
                and_(
                    # Theirs already: only a newer order changes what we call them.
                    BuyerName.buyer_id == Buyer.id,
                    Buyer.username != WhatnotOrder.buyer_username,  # a new spelling counts
                    at.is_not(None),
                    or_(Buyer.username_at.is_(None), at > Buyer.username_at),
                ),
                and_(
                    # Someone else's: a team-made profile is there to be claimed; an
                    # account's name moves only on a strictly newer, dated order.
                    or_(Buyer.id.is_(None), BuyerName.buyer_id != Buyer.id),
                    or_(
                        holder.whatnot_user_id.is_(None),
                        and_(
                            at.is_not(None),
                            or_(BuyerName.seen_at.is_(None), at > BuyerName.seen_at),
                        ),
                    ),
                ),
            ),
        )
        .group_by(WhatnotOrder.buyer_id, WhatnotOrder.buyer_username)
    ).all()
    floor = datetime.min.replace(tzinfo=UTC)
    return sorted(((u, n, t) for u, n, t in rows), key=lambda r: (r[2] or floor, r[0], r[1]))


def link(org_id: int, *, full: bool = False) -> int:
    """Make the profiles agree with the buyers Whatnot named on its orders: a new account
    gets a profile, a new name joins its account's profile (a buyer who renamed
    themselves keeps their flag, note and history), and a profile the team made for a
    name is tied to the account that uses it. Returns how many (account, name) pairs
    it had to act on.

    Reads only orders stored since the last run, and everything once a day (an order read
    again can name a buyer it did not name before). The caller commits."""
    from .whatnot import connection
    from .whatnot.client import when

    _lock(org_id)
    state = connection.sync_state(org_id, STATE)
    cursor = state.cursor or {}
    now = datetime.now(UTC)
    last_full = when(cursor.get("full_at"))
    full = full or last_full is None or now - last_full >= FULL_EVERY
    top = (
        db.session.scalar(
            select(func.max(WhatnotOrder.id)).where(WhatnotOrder.organization_id == org_id)
        )
        or 0
    )
    # A little below where the last run stopped: an order stored by a transaction that
    # committed late has a lower id than one already read. Looking twice costs nothing.
    after = 0 if full else max(0, int(cursor.get("order_id") or 0) - CURSOR_OVERLAP)
    pairs = _unlinked(org_id, after)
    for uid, username, at in pairs:
        name = name_of(username)
        if name:
            _seen(org_id, uid, name, at)
    state.cursor = {"order_id": top, "full_at": (now if full else last_full).isoformat()}
    state.last_started_at = state.last_ok_at = now
    return len(pairs)


@jobs.handler("buyers_link")
def link_job(payload: dict) -> None:
    link(payload["org_id"])
    db.session.commit()


def schedule_link(org_id: int) -> None:
    """Soon, once: Whatnot's orders changed, so the profiles may be behind."""
    jobs.enqueue(
        "buyers_link",
        {"org_id": org_id},
        org_id=org_id,
        dedupe_key=f"buyers_link:{org_id}",
        delay_s=5,
    )


# ---------- what the team says ----------


def annotate(org_id: int, username, *, user: User, flag=KEEP, note=KEEP) -> Buyer | None:
    """Set a buyer's flag and / or note (None clears it). The caller commits. Clearing
    what was never set makes no profile: None."""
    if flag is not KEEP and flag is not None and flag not in FLAGS:
        raise BuyerError("Pick VIP, the watch list, or no flag.")
    if note is not KEEP:
        if note is not None and not isinstance(note, str):
            raise BuyerError("A note is text.")
        note = (clean(note, single_line=False) or "").strip() or None
        if note is not None and len(note) > NOTE_MAX:
            raise BuyerError(f"A note can be up to {NOTE_MAX} characters.")
    name = name_of(username)
    if not name:
        raise BuyerError("Which buyer?")
    _lock(org_id)  # one writer at a time: read, then write, is one step
    buyer = holders(org_id, [name]).get(name.lower())
    if buyer is None:
        if flag in (KEEP, None) and note in (KEEP, None):
            return None
        buyer = ensure(org_id, name)
    if flag is not KEEP and flag != buyer.flag:
        audit.record(
            org_id,
            "buyer.flagged",
            user=user,
            target_type="buyer",
            target_id=buyer.id,
            details={"buyer": buyer.username, "before": buyer.flag, "after": flag},
        )
        buyer.flag = flag
    if note is not KEEP and note != buyer.note:
        audit.record(
            org_id,
            "buyer.noted",
            user=user,
            target_type="buyer",
            target_id=buyer.id,
            details={"buyer": buyer.username, "before": buyer.note, "after": note},
        )
        buyer.note, buyer.noted_by_id, buyer.noted_at = note, user.id, datetime.now(UTC)
    db.session.flush()
    return buyer


def flags(org_id: int) -> dict[str, dict]:
    """Every name of every buyer the team flagged or wrote a note about, so any page can
    mark a buyer wherever a name is shown. lower-case name → flag, whether there is a note."""
    rows = db.session.execute(
        select(BuyerName.name_lower, Buyer.flag, Buyer.note)
        .join(Buyer, Buyer.id == BuyerName.buyer_id)
        .where(
            BuyerName.organization_id == org_id,
            or_(Buyer.flag.is_not(None), Buyer.note.is_not(None)),
        )
    ).all()
    return {low: {"flag": flag, "note": bool(note)} for low, flag, note in rows}


# ---------- what they bought ----------


def _total(figures: list[dict]) -> dict:
    """One person's figures: their names added up, a show date counted once."""
    dates = set().union(*(f["dates"] for f in figures)) if figures else set()
    return {
        "items": sum(f["items"] for f in figures),
        "paidCents": sum(f["paidCents"] for f in figures),
        "shows": len(dates),
        "firstShow": min(dates).isoformat() if dates else None,
        "lastShow": max(dates).isoformat() if dates else None,
        "cancelled": sum(f["cancelled"] for f in figures),
        "failed": sum(f["failed"] for f in figures),
    }


def _others(names: list[BuyerName], username: str) -> list[str]:
    """The names a buyer has also gone by, newest first, without the one shown."""
    return [n.username for n in names if n.name_lower != username.lower()]


_SORT = {
    "spent": lambda r: (-r["paidCents"], -r["items"]),
    "items": lambda r: (-r["items"], -r["paidCents"]),
    "shows": lambda r: (-r["shows"], -r["paidCents"]),
    "recent": lambda r: (r["lastShow"] is None, _neg_date(r["lastShow"]), -r["paidCents"]),
    "problems": lambda r: (-(r["cancelled"] + r["failed"]), -r["paidCents"]),
    "name": lambda r: (),
}


def _neg_date(iso: str | None) -> int:
    return -date.fromisoformat(iso).toordinal() if iso else 0


def directory(
    org_id: int,
    *,
    q: str = "",
    flag: str | None = None,
    sort: str = "spent",
    limit: int = PAGE,
    offset: int = 0,
) -> dict:
    """Everyone who has won something, plus anyone the team flagged or noted, one row per
    person. `flag` is "vip", "watch" or "noted"; `q` matches any name they have used."""
    figures = dashboard.buyer_figures(org_id)
    names = _names(org_id)
    profiles = {
        b.id: b for b in db.session.scalars(select(Buyer).where(Buyer.organization_id == org_id))
    }
    held = {n.name_lower: bid for bid, ns in names.items() for n in ns}
    people: dict[int | str, list[dict]] = {}
    for low, f in figures.items():
        people.setdefault(held.get(low, low), []).append(f)
    rows = []
    for key, figs in people.items():
        b = profiles.get(key) if isinstance(key, int) else None
        username = b.username if b is not None else figs[0]["username"]
        rows.append(
            {
                "username": username,
                "names": _others(names.get(b.id, []), username) if b is not None else [],
                "flag": b.flag if b is not None else None,
                "hasNote": bool(b is not None and b.note),
                **_total(figs),
            }
        )
    for b in profiles.values():
        # Nothing bought (yet), but the team said something about them. A profile with
        # no name left (its name went to another account) cannot be met again by name.
        if b.id not in people and (b.flag or b.note) and names.get(b.id):
            rows.append(
                {
                    "username": b.username,
                    "names": _others(names[b.id], b.username),
                    "flag": b.flag,
                    "hasNote": bool(b.note),
                    **_total([]),
                }
            )
    needle = name_of(q).lower()
    if needle:
        rows = [
            r
            for r in rows
            if needle in r["username"].lower() or any(needle in n.lower() for n in r["names"])
        ]
    counts = {
        "all": len(rows),
        "vip": sum(r["flag"] == "vip" for r in rows),
        "watch": sum(r["flag"] == "watch" for r in rows),
        "noted": sum(r["hasNote"] for r in rows),
    }
    if flag in FLAGS:
        rows = [r for r in rows if r["flag"] == flag]
    elif flag == "noted":
        rows = [r for r in rows if r["hasNote"]]
    by = _SORT.get(sort, _SORT["spent"])
    rows.sort(key=lambda r: (*by(r), r["username"].lower()))
    limit = max(1, min(int(limit), PAGE))
    offset = max(0, int(offset))
    return {
        "buyers": rows[offset : offset + limit],
        "total": len(rows),
        "counts": counts,
        "limit": limit,
        "offset": offset,
    }


def profile(org_id: int, username) -> tuple[dict, list[str]]:
    """One buyer's page, and the lower-case names their orders are filed under. A name
    nobody has won under or said anything about is answered too (`known` False), so the
    team can flag someone before their first win."""
    name = name_of(username)
    if not name:
        raise BuyerError("Which buyer?")
    low = name.lower()
    buyer = holders(org_id, [low]).get(low)
    names = _names(org_id, [buyer.id]).get(buyer.id, []) if buyer is not None else []
    lows = [n.name_lower for n in names] or [low]
    figures = dashboard.buyer_figures(org_id, lows)
    gifts = dashboard.buyer_gifts(org_id, lows)
    if buyer is not None:
        shown = buyer.username
    else:
        shown = figures[low]["username"] if low in figures else name
    noted_by = db.session.get(User, buyer.noted_by_id) if buyer and buyer.noted_by_id else None
    return (
        {
            "username": shown,
            "names": _others(names, shown),
            "known": bool(buyer is not None or figures or gifts),
            # Whatnot named the account: a new username will follow them here by itself.
            "followed": bool(buyer is not None and buyer.whatnot_user_id),
            "flag": buyer.flag if buyer is not None else None,
            "note": buyer.note if buyer is not None else None,
            "notedBy": noted_by.name if noted_by is not None else None,
            "notedAt": buyer.noted_at.isoformat() if buyer and buyer.noted_at else None,
            **_total(list(figures.values())),
            "gifts": gifts,
            "problems": problems(org_id, lows),
        },
        lows,
    )


def problems(org_id: int, lows: list[str]) -> list[dict]:
    """Wins that fell through (cancelled, or the payment failed), newest first."""
    rows = db.session.execute(
        select(Sale, Show.show_date)
        .join(Show, Show.id == Sale.show_id)
        .where(
            Sale.organization_id == org_id,
            func.lower(Sale.buyer_username).in_(lows),
            Sale.status.in_(dashboard.LOST),
            dashboard.held_since(org_id, Sale.buyer_username, Show.show_date),
        )
        .order_by(Sale.id.desc())
        .limit(PROBLEMS_LIMIT)
    ).all()
    return [
        {
            "saleId": s.id,
            "showId": s.show_id,
            "showDate": show_date.isoformat(),
            "item": s.item_text,
            "quantity": s.quantity,
            "priceCents": s.price_cents,
            "status": s.status.value,
        }
        for s, show_date in rows
    ]


def badges(org_id: int, names, *, before: date) -> dict[str, dict]:
    """For each name asked about (lower-case key): the team's flag, and what the person
    had bought on show dates before `before`, under any name they have used. `firstTime`
    means nothing of theirs stood before that date."""
    lows = sorted({n.lower() for n in (name_of(n) for n in names) if n})[:MAX_LOOKUP]
    held = holders(org_id, lows)
    by_buyer = _names(org_id, {b.id for b in held.values()})
    mine = {
        low: [n.name_lower for n in by_buyer.get(held[low].id, [])] if low in held else [low]
        for low in lows
    }
    wanted = sorted({n for ns in mine.values() for n in ns})
    figures = dashboard.buyer_figures(org_id, wanted, before=before) if wanted else {}
    out = {}
    for low in lows:
        total = _total([figures[n] for n in mine[low] if n in figures])
        b = held.get(low)
        out[low] = {
            "flag": b.flag if b is not None else None,
            "firstTime": total["shows"] == 0,
            "shows": total["shows"],
            "items": total["items"],
        }
    return out
