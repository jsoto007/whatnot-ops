"""Whatnot's Seller Hub Shipments table, read by the scraper after a show, as a cross-check
for the show's order slips.

The scraper runs on the seller's own laptop, in the Chrome that is signed in to Seller Hub,
and reads the table by its column headers (Recipient, Order Date, Items, Value, Status,
Tracking). Each row is matched to a show by its order date (shop zone, 5 AM rollover, as
for chat). The rows are evidence only: they never create, pay, cancel or move anything. The
slips page shows where Whatnot and the app disagree, for a person to look at.

Nobody has captured a filled Shipments page yet (the first account checked had no shows),
so what the Items and Order Date cells hold is read loosely and kept as text too.
"""

import hashlib
import re
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db import db
from ..models import Device, Show, WhatnotShipment
from . import text as text_safe
from .reconcile import _cents
from .showdate import SHOP_TZ, show_date

MAX_ROWS = 2000  # one show is a few hundred shipments; a page read is capped well above that
MAX_LINES = 60
USERNAME = re.compile(r"^@?[A-Za-z0-9._-]{1,80}$")


class ShipmentsInvalid(ValueError):
    def __init__(self, message: str):
        super().__init__(message)


def _text(v, limit: int) -> str:
    return (text_safe.clean(v) or "")[:limit] if isinstance(v, str) else ""


def _row(raw) -> dict | None:
    """One row from the extension, cleaned and cut to the columns. None when it names no
    buyer: a Shipments row is useless without one."""
    if not isinstance(raw, dict):
        return None
    recipient = _text(raw.get("recipient"), 80).lstrip("@")
    if not recipient or not USERNAME.match(recipient):
        return None
    lines = raw.get("itemLines")
    lines = [_text(x, 240) for x in lines[:MAX_LINES]] if isinstance(lines, list) else []
    return {
        "recipient": recipient,
        "order_date_text": _text(raw.get("orderDate"), 80),
        "items_text": _text(raw.get("items"), 1000),
        "item_lines": [x for x in lines if x] or None,
        "value": _text(raw.get("value"), 40),
        "status": _text(raw.get("status"), 80),
        "tracking": _text(raw.get("tracking"), 120),
    }


_COUNT = re.compile(r"^\s*(\d{1,4})\s*(?:items?|x)?\s*$|\b(\d{1,4})\s+items?\b", re.I)


def item_count(items_text: str, lines: list | None) -> int | None:
    """The Items cell as a number: "3", "3 items", or one line per item."""
    m = _COUNT.search(items_text or "")
    if m:
        return int(m[1] or m[2])
    return len(lines) if lines else None


_DATE_FORMATS = ("%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y")
_DATE = re.compile(
    r"([A-Za-z]{3,9}\.? \d{1,2},? \d{4}"  # Oct 31, 2026
    r"|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2}-\d{4})"
)
_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])?")


def ordered_on(order_date_text: str) -> tuple[date | None, bool]:
    """(show date, whether a time of day was read). With a time the 5 AM rollover applies
    (the page shows the seller's local time; the shop's zone is assumed)."""
    m = _DATE.search(order_date_text or "")
    if not m:
        return None, False
    raw = " ".join(m[1].replace(".", " ").replace(",", " ").split())
    d = None
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    if d is None:
        return None, False
    t = _TIME.search(order_date_text[m.end() :])
    if not t:
        return d, False
    hour, minute = int(t[1]), int(t[2])
    if t[3]:
        hour = hour % 12 + (12 if t[3].lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return d, False
    local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=SHOP_TZ)
    return show_date(local), True


def _match_show(org_id: int, d: date | None, timed: bool) -> int | None:
    """The one show on that date. Without a time an order after midnight carries the next
    day's date, so the night before is tried too."""
    if d is None:
        return None
    for day in (d,) if timed else (d, d - timedelta(days=1)):
        ids = db.session.scalars(
            select(Show.id).where(Show.organization_id == org_id, Show.show_date == day)
        ).all()
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            return None  # two shows that day: a person has to say which
    return None


def _key(org_id: int, r: dict) -> str:
    ident = "|".join(
        [str(org_id), r["recipient"].lower(), r["order_date_text"], r["items_text"], r["value"]]
    )
    return hashlib.sha256(ident.encode()).hexdigest()


def ingest(device: Device, payload) -> dict:
    """Store (or refresh) the rows one page read sent. Returns counts per show."""
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ShipmentsInvalid("rows must be a list")
    if len(rows) > MAX_ROWS:
        raise ShipmentsInvalid(f"at most {MAX_ROWS} rows per read")
    org_id = device.organization_id
    shows: dict[int, int] = {}
    stored = skipped = unmatched = 0
    seen: set[str] = set()
    for raw in rows:
        r = _row(raw)
        if r is None:
            skipped += 1
            continue
        key = _key(org_id, r)
        if key in seen:
            continue
        seen.add(key)
        d, timed = ordered_on(r["order_date_text"])
        show_id = _match_show(org_id, d, timed)
        values = {
            "organization_id": org_id,
            "row_key": key,
            "show_id": show_id,
            "device_id": device.id,
            "recipient": r["recipient"],
            "order_date_text": r["order_date_text"],
            "ordered_on": d,
            "item_count": item_count(r["items_text"], r["item_lines"]),
            "items_text": r["items_text"],
            "item_lines": r["item_lines"],
            "value_cents": _cents(r["value"]),
            "status": r["status"],
            "tracking": r["tracking"],
        }
        stmt = insert(WhatnotShipment).values(**values)
        db.session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_whatnot_shipments_org_key",
                set_={
                    k: stmt.excluded[k]
                    for k in (
                        "show_id",
                        "device_id",
                        "item_count",
                        "item_lines",
                        "status",
                        "tracking",
                    )
                }
                | {"updated_at": datetime.now(UTC)},
            )
        )
        stored += 1
        if show_id is None:
            unmatched += 1
        else:
            shows[show_id] = shows.get(show_id, 0) + 1
    return {"stored": stored, "skipped": skipped, "unmatched": unmatched, "shows": shows}


def for_show(show: Show) -> dict | None:
    """What Whatnot's Shipments table said about this show's buyers, or None if never read."""
    rows = db.session.scalars(
        select(WhatnotShipment)
        .where(WhatnotShipment.show_id == show.id)
        .order_by(WhatnotShipment.recipient, WhatnotShipment.id)
    ).all()
    if not rows:
        return None
    buyers: dict[str, dict] = {}
    for r in rows:
        b = buyers.setdefault(
            r.recipient.lower(),
            {
                "username": r.recipient,
                "shipments": 0,
                "items": 0,
                "unknownItems": False,
                "statuses": [],
            },
        )
        b["shipments"] += 1
        if r.item_count is None:
            b["unknownItems"] = True
        else:
            b["items"] += r.item_count
        if r.status and r.status not in b["statuses"]:
            b["statuses"].append(r.status)
    return {
        "readAt": max(r.updated_at for r in rows).isoformat(),
        "shipments": len(rows),
        "buyers": buyers,
    }
