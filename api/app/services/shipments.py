"""Whatnot's Seller Hub Shipments table, read by the scraper after a show. Its lots, with the
product each winner picked in chat, are the show's final pick list (services/order_slips.py).

The scraper runs on the seller's own laptop, in the Chrome that is signed in to Seller Hub,
and reads the table by its column headers (Recipient, Order Date, Items, Value, Status,
Tracking). It opens each shipment for its lots: listing title, Whatnot's order number, qty,
price, shipping and sale type (seen on the shop's account 2026-11-05).

Which show: the scraper sends Whatnot's show filter ("<title> Oct 22•1:44 AM EDT"); its
start time, in the shop zone with the 5 AM rollover, names the show date. Without it (older
scraper builds) each row's Order Date is used, which on the real page is only "Oct 22": the
year is the latest one not in the future.

Rows and lots are evidence only: they never create, pay, cancel or move anything.
"""

import hashlib
import re
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..db import db
from ..models import Device, Show, WhatnotLot, WhatnotShipment
from . import rules
from . import text as text_safe
from .reconcile import _cents
from .showdate import SHOP_TZ, show_date

MAX_ROWS = 2000  # one show is a few hundred shipments; a page read is capped well above that
MAX_LINES = 60
MAX_LOTS = 200  # per shipment
ORDER_ID = re.compile(r"^\d{1,20}$")
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
    lots = raw.get("lots")
    lots = [x for x in map(_lot, lots[:MAX_LOTS]) if x] if isinstance(lots, list) else []
    return {
        "lots": lots,
        "recipient": recipient,
        "order_date_text": _text(raw.get("orderDate"), 80),
        "items_text": _text(raw.get("items"), 1000),
        "item_lines": [x for x in lines if x] or None,
        "value": _text(raw.get("value"), 40),
        "status": _text(raw.get("status"), 80),
        "tracking": _text(raw.get("tracking"), 120),
    }


def _lot(raw) -> dict | None:
    """One lot of an expanded row. None without Whatnot's order number: that number is what
    makes a second read update the lot instead of adding one."""
    if not isinstance(raw, dict):
        return None
    order_id = _text(raw.get("orderId"), 20)
    if not ORDER_ID.match(order_id):
        return None
    title = _text(raw.get("title"), 240)
    qty = re.fullmatch(r"\s*(\d{1,3})\s*", _text(raw.get("quantity"), 20))
    return {
        "whatnot_order_id": order_id,
        "title": title,
        "lot_number": rules.lot_number(title),
        "quantity": max(1, int(qty[1])) if qty else 1,
        "price_cents": _cents(_text(raw.get("price"), 40)),
        "shipping_cents": _cents(_text(raw.get("shipping"), 40)),
        "sale_type": _text(raw.get("saleType"), 80),
    }


_COUNT = re.compile(r"^\s*(\d{1,4})\s*(?:items?|x)?\s*$|\b(\d{1,4})\s+items?\b", re.I)


def item_count(items_text: str, lines: list | None) -> int | None:
    """The Items cell as a number: "3", "3 items", or one line per item."""
    m = _COUNT.search(items_text or "")
    if m:
        return int(m[1] or m[2])
    return len(lines) if lines else None


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# "Oct 31, 2026", "Oct. 31", "October 31": a month name, then the day, maybe a year.
_NAMED = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(\d{1,2})\b"
    r"(?:,?\s+(\d{4}))?",
    re.I,
)
_NUMERIC_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y")
_NUMERIC = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}-\d{1,2}-\d{4})")
_TIME = re.compile(
    r"^\s*[•·,@-]?\s*(?:at\s+)?(\d{1,2}):(\d{2})\s*([AaPp][Mm])?(?:\s+([A-Z]{2,4})\b)?"
)
# What Seller Hub prints after a time; an unknown one is taken as the shop's zone.
_ZONES = {
    "EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "MDT": -6, "MST": -7,
    "PDT": -7, "PST": -8, "AKDT": -8, "AKST": -9, "HST": -10, "UTC": 0, "GMT": 0,
}  # fmt: skip
# A year-less date this far ahead of today is last year's (a show is read after it ends).
_AHEAD = timedelta(days=2)


def _today() -> date:
    return datetime.now(SHOP_TZ).date()


def _with_year(month: int, day: int, year: str | None, today: date) -> date | None:
    """The date in the year that makes it the latest one not past today + _AHEAD: "Jan 2"
    read on Dec 31 is next year's, "Dec 30" read on Jan 1 is last year's."""
    if year:
        try:
            return date(int(year), month, day)
        except ValueError:
            return None
    for y in (today.year + 1, today.year, today.year - 1):
        try:
            d = date(y, month, day)
        except ValueError:  # Feb 29 outside a leap year
            continue
        if d <= today + _AHEAD:
            return d
    return None


def _when(text: str, today: date, *, last: bool = False) -> tuple[date | None, bool]:
    """(show date, whether a time of day was read) from the first (or `last`) date in the
    text. With a time the 5 AM rollover applies, in the zone printed after it, else the
    shop's."""
    text = text or ""
    named = list(_NAMED.finditer(text))
    d = end = None
    for m in reversed(named) if last else named:
        d = _with_year(_MONTHS.index(m[1].lower()[:3]) + 1, int(m[2]), m[3], today)
        if d:
            end = m.end()
            break
    if d is None and (m := _NUMERIC.search(text)):
        for fmt in _NUMERIC_FORMATS:
            try:
                d = datetime.strptime(m[1], fmt).date()
                end = m.end()
                break
            except ValueError:
                continue
    if d is None:
        return None, False
    t = _TIME.match(text[end:])
    if not t:
        return d, False
    hour, minute = int(t[1]), int(t[2])
    if t[3]:
        if hour > 12:
            return d, False
        hour = hour % 12 + (12 if t[3].lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return d, False
    offset = _ZONES.get((t[4] or "").upper())
    tz = timezone(timedelta(hours=offset)) if offset is not None else SHOP_TZ
    return show_date(datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)), True


def ordered_on(order_date_text: str, today: date | None = None) -> tuple[date | None, bool]:
    """A row's Order Date ("Oct 22", "Oct 31, 2026 11:42 PM") as (show date, timed)."""
    return _when(order_date_text, today or _today())


def show_on(label: str, today: date | None = None) -> date | None:
    """The show date in Whatnot's show filter label ("<title> Oct 22•1:44 AM EDT"). Only a
    label with a start time counts: a title may hold other numbers ("$10,000", "Mega 25")."""
    d, timed = _when(label, today or _today(), last=True)
    return d if timed else None


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
    """What identifies a shipment: its first Whatnot order number once its lots were read
    (a box keeps its lots), else what the row shows."""
    if r["lots"]:
        first = min(r["lots"], key=lambda x: (len(x["whatnot_order_id"]), x["whatnot_order_id"]))
        ident = "|".join([str(org_id), r["recipient"].lower(), "lot", first["whatnot_order_id"]])
        return hashlib.sha256(ident.encode()).hexdigest()
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
    today = _today()
    label = _text(payload.get("show"), 300)
    # The show picked in Whatnot's filter names the show for every row of this read.
    label_date = show_on(label, today) if label else None
    label_show = _match_show(org_id, label_date, True) if label_date else None
    shows: dict[int, int] = {}
    stored = skipped = unmatched = lots = 0
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
        d, timed = ordered_on(r["order_date_text"], today)
        if label_date:
            show_id = label_show
        else:
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
            "show_label": label,
        }
        stmt = insert(WhatnotShipment).values(**values)
        shipment_id, show_id = db.session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_whatnot_shipments_org_key",
                set_={
                    k: stmt.excluded[k]
                    for k in (
                        "device_id",
                        "item_count",
                        "item_lines",
                        "status",
                        "tracking",
                        "show_label",
                    )
                }
                # A read that cannot place the row (its date now has two shows, say, after
                # "Start fresh") keeps where an earlier read filed it; never un-files it. A
                # wrong earlier filing is corrected only by a read that names another show.
                | {"show_id": func.coalesce(stmt.excluded.show_id, WhatnotShipment.show_id)}
                | {"updated_at": datetime.now(UTC)},
            ).returning(WhatnotShipment.id, WhatnotShipment.show_id)
        ).one()
        for lot in r["lots"]:
            lots += _store_lot(org_id, show_id, shipment_id, r["recipient"], lot)
        stored += 1
        if show_id is None:
            unmatched += 1
        else:
            shows[show_id] = shows.get(show_id, 0) + 1
    filed = db.session.get(Show, label_show) if label_show else None
    return {
        "stored": stored,
        "lots": lots,
        "skipped": skipped,
        "unmatched": unmatched,
        "shows": shows,
        # The app's show the filter named, so the scraper can say where the read went.
        "show": {"id": filed.id, "date": filed.show_date.isoformat()} if filed else None,
    }


def _store_lot(org_id: int, show_id: int | None, shipment_id: int, recipient: str, lot: dict):
    """Insert or refresh one lot by Whatnot's order number. Returns 1."""
    stmt = insert(WhatnotLot).values(
        organization_id=org_id,
        show_id=show_id,
        shipment_id=shipment_id,
        recipient=recipient,
        **lot,
    )
    db.session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_whatnot_lots_org_order",
            set_={
                k: stmt.excluded[k]
                for k in (
                    "shipment_id",
                    "recipient",
                    "title",
                    "lot_number",
                    "quantity",
                    "price_cents",
                    "shipping_cents",
                    "sale_type",
                )
            }
            | {"show_id": func.coalesce(stmt.excluded.show_id, WhatnotLot.show_id)}
            | {"updated_at": datetime.now(UTC)},
        )
    )
    return 1


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
    lots = db.session.scalars(
        select(WhatnotLot)
        .where(WhatnotLot.show_id == show.id)
        # Whatnot's order numbers grow as the show goes on: this is win order.
        .order_by(func.length(WhatnotLot.whatnot_order_id), WhatnotLot.whatnot_order_id)
    ).all()
    return {
        "readAt": max(r.updated_at for r in rows).isoformat(),
        "shipments": len(rows),
        "buyers": buyers,
        "lots": list(lots),
    }
