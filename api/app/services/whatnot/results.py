"""What Whatnot itself says about a show's results, and its financial ledger.

Two read-only feeds, both needing the token's permission to read reports:

  * `GET /analytics/shows/{id}`: viewers, reach, buyers and sales of one show, once
    Whatnot has finished working them out (`calculation_status` GENERATED, some time
    after the show ends).
  * the financial report: a CSV Whatnot builds in the background, one line per ledger
    transaction (an order's earnings with the fees taken, a shipping charge, a refund, a
    carrier adjustment, a tip). The app asks for it, waits, downloads it and stores the
    lines by Whatnot's own transaction id, so importing a report twice, or two reports
    that overlap, changes nothing.

Nothing here changes a sale, an order or stock. The numbers are shown as Whatnot states
them. In particular the app does not work out a "net" of its own from the fee columns: the
net is the sum of Whatnot's own signed ledger amounts, and the fees are listed beside it.
(Whether an order's ledger amount is before or after its fees is not something the docs
say; `figures()` reports whether the two agree so the first real import shows it.)
"""

import csv
import io
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import BigInteger, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from ...db import db
from ...models import WhatnotLedgerEntry, WhatnotReport, WhatnotShow
from .. import jobs
from .. import text as text_safe
from . import connection, shipping, sync
from .client import (
    MAX_CENTS,
    Forbidden,
    NotFound,
    WhatnotClient,
    WhatnotError,
    cents,
    client_for,
    when,
)

log = logging.getLogger(__name__)

# ---------- a show's results ----------

FINAL = ("GENERATED", "WILL_NOT_BE_CALCULATED")
COUNTS = (
    "orders_count",
    "giveaway_orders_count",
    "show_impression_count",
    "buyers_count",
    "first_time_buyers_count",
    "returning_buyers_count",
    "giveaway_winners_count",
    "first_time_giveaway_winners_count",
    "returning_giveaway_winners_count",
    "buyer_shares_count",
    "stream_minutes",
    "max_concurrent_viewers",
    "total_views",
    "distinct_viewers_count",
)
MONEY = ("sales", "estimated_earnings", "average_order_value", "giveaway_spend")
RATES = ("show_clickthrough_rate", "average_rating")
RESULTS_TRIES = 36  # every 10 minutes for six hours after the show
RESULTS_EVERY_S = 600
RESULTS_WINDOW = timedelta(days=7)


def _count(value) -> int | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2_000_000_000
    return value if ok else None


def _rate(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 2) if -1e6 < value < 1e6 else None


def store_results(ws: WhatnotShow, data: dict) -> str:
    """Keep Whatnot's results for a show. Money becomes integer cents; anything unreadable
    is left out rather than guessed. Returns Whatnot's calculation status."""
    status = data.get("calculation_status")
    status = status if isinstance(status, str) and len(status) <= 24 else "PENDING"
    out: dict = {}
    for key in COUNTS:
        if (v := _count(data.get(key))) is not None:
            out[key] = v
    for key in MONEY:
        if (v := cents(data.get(key))) is not None:
            out[key + "_cents"] = v
    for key in RATES:
        if (v := _rate(data.get(key))) is not None:
            out[key] = v
    ws.analytics_status = status
    ws.analytics_at = datetime.now(UTC)
    if out or status in FINAL:
        ws.analytics = out or None
    return status


def read_results(org_id: int, client: WhatnotClient, ws: WhatnotShow) -> str | None:
    try:
        return store_results(ws, client.show_analytics(ws.whatnot_id))
    except NotFound:
        return None


def schedule_results(org_id: int, whatnot_show_id: str, *, tries: int = 0, delay_s: int = 0):
    return jobs.enqueue(
        "whatnot_show_results",
        {"org_id": org_id, "whatnot_show_id": whatnot_show_id, "tries": tries},
        org_id=org_id,
        dedupe_key=f"whatnot_show_results:{org_id}:{whatnot_show_id}",
        delay_s=delay_s,
    )


@jobs.handler("whatnot_show_results")
def show_results_job(payload: dict) -> None:
    """Ask for a show's results; while Whatnot is still working them out, ask again later
    (a few times: after that the timer picks it up for a week)."""
    org_id, tries = payload["org_id"], int(payload.get("tries") or 0)
    if connection.active(org_id) is None:
        return
    ws = sync._show_row(org_id, payload.get("whatnot_show_id"))
    if ws is None:
        return
    status = sync._run(
        org_id, "analytics", lambda: read_results(org_id, client_for(org_id), ws), flag=False
    )
    db.session.commit()
    if status in (None, *FINAL) or tries + 1 >= RESULTS_TRIES or jobs.inline():
        return
    schedule_results(org_id, ws.whatnot_id, tries=tries + 1, delay_s=RESULTS_EVERY_S)


def results_due(org_id: int) -> list[str]:
    """Recently ended shows whose results Whatnot had not finished when we last asked."""
    if not sync._due(org_id, "analytics", timedelta(hours=1)):
        return []
    return list(
        db.session.scalars(
            select(WhatnotShow.whatnot_id).where(
                WhatnotShow.organization_id == org_id,
                WhatnotShow.status == "ended",
                WhatnotShow.ended_at >= datetime.now(UTC) - RESULTS_WINDOW,
                WhatnotShow.analytics_status.is_(None) | WhatnotShow.analytics_status.not_in(FINAL),
            )
        )
    )


# ---------- the financial ledger ----------

REPORT = "seller_financial_report"
FIRST_SPAN = timedelta(days=35)  # the first read
OVERLAP = timedelta(days=3)  # each later read starts a little before the last line we hold
LEDGER_EVERY = timedelta(days=1)
LEDGER_TRIES = 30  # every two minutes for an hour while Whatnot builds the file
LEDGER_EVERY_S = 120
OPEN_RUNS = ("pending", "in_progress")
RUN_GIVE_UP = timedelta(hours=6)
REQUIRED = ("ledger_transaction_id", "transaction_type", "transaction_amount")
# CSV column → ledger column, for the amounts ("in major currency units", so ×100).
AMOUNTS = {
    "transaction_amount": "amount_cents",
    "buyer_paid": "buyer_paid_cents",
    "original_item_price": "item_price_cents",
    "coupon_cost": "coupon_cents",
    "seller_shipping_charge": "seller_shipping_cents",
    "commission_fee": "commission_cents",
    "commission_tax": "commission_tax_cents",
    "processing_fee": "processing_fee_cents",
    "processing_fee_tax": "processing_fee_tax_cents",
    "cost_of_goods": "cost_of_goods_cents",
}
FEES = (
    "commission_cents",
    "commission_tax_cents",
    "processing_fee_cents",
    "processing_fee_tax_cents",
    "seller_shipping_cents",
    "coupon_cents",
)
MAX_REPORT_BYTES = 20 * 1024 * 1024  # some 50,000 lines; a month here is a few thousand


class LedgerError(Exception):
    """Why the ledger could not be read, in the shop's words."""

    status = 409


_AMOUNT = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)?(\.\d+)?$")


def to_cents(value) -> int | None:
    """A CSV amount in dollars as signed integer cents, rounded half up, never through a
    float. Read: "12.5", "-3.10", "1,234.00", "$7", the accountant's "(21.97)" and a
    typeset minus "−21.97" (both negative). None when empty, too large, or not clearly an
    amount: a comma that is not a thousands separator ("12,50", "1.234,56") is not
    guessed at, because guessing wrong is off by a hundredfold."""
    if not isinstance(value, str):
        return None
    t = value.replace("\u00a0", " ").strip().replace("$", "").replace(" ", "")
    negative = False
    if t.startswith("(") and t.endswith(")"):
        negative, t = True, t[1:-1]
    if t[:1] in "-\u2212\u2012\u2013":
        negative, t = not negative, t[1:]
    elif t[:1] == "+":
        t = t[1:]
    if not t or not _AMOUNT.match(t) or not any(c.isdigit() for c in t):
        return None
    try:
        d = Decimal(t.replace(",", ""))
        if d.adjusted() > 9:
            return None
        result = int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ArithmeticError, ValueError):
        return None
    if result > MAX_CENTS:
        return None
    return -result if negative else result


def lines(text: str):
    """The report's lines one at a time: a ledger row, or None for a line left out (no id,
    no amount). Raises LedgerError, before the first line, when the file is not the report
    we know. A generator, so a large file is never held as rows in memory."""
    text = text_safe.clean_file(text)
    head = text[:4000].split("\n")
    # The file may open with a title line: the header is the line that names the id column.
    start = next((i for i, line in enumerate(head[:5]) if "ledger_transaction_id" in line), None)
    if start is None:
        raise LedgerError("Whatnot's report is not in the format the app knows.")
    stream = io.StringIO(text)
    for _ in range(start):
        stream.readline()
    reader = csv.DictReader(stream)
    names = {(n or "").strip().lower() for n in (reader.fieldnames or [])}
    if any(col not in names for col in REQUIRED):
        raise LedgerError("Whatnot's report is not in the format the app knows.")
    return _rows(reader)


def _rows(reader):
    for raw in reader:
        cell = {(k or "").strip().lower(): (v or "") for k, v in raw.items() if k}
        if not any(isinstance(v, str) and v.strip() for v in cell.values()):
            continue
        txn = sync._str(cell.get("ledger_transaction_id"), 64)
        amount = to_cents(cell.get("transaction_amount"))
        kind = sync._str(cell.get("transaction_type"), 48)
        if txn is None or amount is None or kind is None:
            yield None
            continue
        quantity = cell.get("quantity_sold", "").strip()[:5]  # at most 99,999 of one item
        row = {
            "ledger_transaction_id": txn,
            "transaction_type": kind.upper(),
            "completed_at": when(cell.get("transaction_completed_at_utc", "").strip()),
            "order_placed_at": when(cell.get("order_placed_at_utc", "").strip()),
            "whatnot_order_id": sync._str(cell.get("order_id"), 20),
            "whatnot_show_id": sync._str(cell.get("livestream_id"), 40),
            "whatnot_shipment_id": sync._str(cell.get("shipment_id"), 20),
            "listing_title": sync._str(cell.get("listing_title"), 240),
            "sku_code": sync._str(cell.get("sku"), 80),
            "buy_format": sync._str(cell.get("buy_format"), 24),
            "quantity": int(quantity) if quantity.isascii() and quantity.isdigit() else None,
            "buyer_username": sync._str(cell.get("buyer_name"), 80),
            "currency": (sync._str(cell.get("transaction_currency"), 3) or "").upper() or None,
        }
        for column, field in AMOUNTS.items():
            row[field] = amount if field == "amount_cents" else to_cents(cell.get(column))
        yield row


def parse(text: str) -> tuple[list[dict], int]:
    """Every ledger row of a report, and how many lines were left out."""
    got = list(lines(text))
    return [r for r in got if r is not None], sum(1 for r in got if r is None)


def _lines_held(org_id: int) -> int:
    return (
        db.session.scalar(
            select(func.count())
            .select_from(WhatnotLedgerEntry)
            .where(WhatnotLedgerEntry.organization_id == org_id)
        )
        or 0
    )


def store(org_id: int, rows, report_id: int | None = None) -> dict:
    """Insert or refresh ledger lines by Whatnot's transaction id, 500 at a time (`rows`
    may be a generator, with None for a line left out). Returns how many lines were
    stored, were new to the app, were left out (no readable id or amount), and repeated a
    transaction id already seen in this same file.

    A repeated id is not added a second time. Whether Whatnot ever splits one transaction
    over several lines is not something its docs say; if it does, those lines would be
    missing from the sums, which is why they are counted and said on the page instead of
    being dropped in silence."""
    before = _lines_held(org_id)
    now = datetime.now(UTC)
    out = {"stored": 0, "new": 0, "skipped": 0, "repeated": 0}
    seen: set[str] = set()
    batch: list[dict] = []

    def flush() -> None:
        if not batch:
            return
        stmt = insert(WhatnotLedgerEntry).values(batch)
        keep = ("organization_id", "ledger_transaction_id", "imported_at")
        db.session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_whatnot_ledger_org_txn",
                set_={k: stmt.excluded[k] for k in batch[0] if k not in keep},
            )
        )
        batch.clear()

    for row in rows:
        if row is None:
            out["skipped"] += 1
            continue
        if row["ledger_transaction_id"] in seen:
            out["repeated"] += 1
            continue
        seen.add(row["ledger_transaction_id"])
        out["stored"] += 1
        batch.append({**row, "organization_id": org_id, "report_id": report_id, "imported_at": now})
        if len(batch) >= 500:
            flush()
    flush()
    out["new"] = _lines_held(org_id) - before
    return out


def _span(org_id: int) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    last = db.session.scalar(
        select(func.max(WhatnotLedgerEntry.completed_at)).where(
            WhatnotLedgerEntry.organization_id == org_id,
            # A line dated in the future (a bad date) must not push every later read past
            # today, where nothing would ever be found again.
            WhatnotLedgerEntry.completed_at <= now,
        )
    )
    start = (last - OVERLAP) if last is not None else now - FIRST_SPAN
    return max(start, now - timedelta(days=90)), now


def _expire_stale_runs(org_id: int) -> None:
    """A report Whatnot never finished must not block the ledger for good: after six hours
    it is given up, and the next read asks for a new one."""
    db.session.execute(
        update(WhatnotReport)
        .where(
            WhatnotReport.organization_id == org_id,
            WhatnotReport.report_type == REPORT,
            WhatnotReport.status.in_(OPEN_RUNS),
            WhatnotReport.requested_at < datetime.now(UTC) - RUN_GIVE_UP,
        )
        .values(status="expired")
    )


def open_runs(org_id: int) -> list[WhatnotReport]:
    _expire_stale_runs(org_id)
    return list(
        db.session.scalars(
            select(WhatnotReport)
            .where(
                WhatnotReport.organization_id == org_id,
                WhatnotReport.report_type == REPORT,
                WhatnotReport.status.in_(OPEN_RUNS),
            )
            .order_by(WhatnotReport.id)
        )
    )


def request(org_id: int, client: WhatnotClient, *, user=None) -> WhatnotReport:
    """Ask Whatnot to build the ledger since the last line we hold (or the last five
    weeks). While one is still being built, that one is returned instead."""
    waiting = open_runs(org_id)
    if waiting:
        return waiting[-1]
    start, end = _span(org_id)
    body = {
        "report_parameters": {
            "report_type": REPORT,
            "start_date": start.isoformat().replace("+00:00", "Z"),
            "end_date": end.isoformat().replace("+00:00", "Z"),
        }
    }
    # Whatnot returns the run already in flight for an identical request, so sending this
    # twice asks for nothing twice.
    data = client.request("POST", "/reports", json=body, safe=True) or {}
    whatnot_id = sync._str(data.get("id"), 40)
    if whatnot_id is None:
        raise LedgerError("Whatnot did not start the report. Try again in a minute.")
    db.session.execute(
        insert(WhatnotReport)
        .values(
            organization_id=org_id,
            whatnot_id=whatnot_id,
            report_type=REPORT,
            status=sync._str(data.get("status"), 16) or "pending",
            period_start=start,
            period_end=end,
            requested_by_id=user.id if user is not None else None,
        )
        .on_conflict_do_nothing(constraint="uq_whatnot_reports_org_id")
    )
    return db.session.scalar(
        select(WhatnotReport).where(
            WhatnotReport.organization_id == org_id, WhatnotReport.whatnot_id == whatnot_id
        )
    )


def check(org_id: int, client: WhatnotClient) -> dict:
    """Look at each report still being built; download and import the ones that are done."""
    out = {"imported": 0, "new": 0, "waiting": 0}
    for run in open_runs(org_id):
        try:
            data = client.report(run.whatnot_id)
        except NotFound:
            run.status = "failed"
            continue
        run.checked_at = datetime.now(UTC)
        status = sync._str(data.get("status"), 16) or run.status
        if status in OPEN_RUNS:
            run.status = status
            out["waiting"] += 1
            continue
        if status != "completed":
            run.status = status  # failed | cancelled: the next request starts a new one
            continue
        url = shipping._safe_url(data.get("report_url"))
        if url is None:
            run.status = "unreadable"
            continue
        try:
            raw = shipping._fetch(url, max_bytes=MAX_REPORT_BYTES)
            with db.session.begin_nested():  # a file that turns out unreadable stores nothing
                got = store(org_id, lines(raw.decode("utf-8-sig", "replace")), run.id)
        except (shipping.ShippingError, LedgerError, csv.Error, ValueError):
            # Not the file we know, or one the CSV reader cannot get through: settled as
            # unreadable so it is neither half-stored nor downloaded again on every tick.
            run.status = "unreadable"
            continue
        out["new"] += got["new"]
        run.status, run.imported_at = "imported", datetime.now(UTC)
        run.rows, run.skipped, run.repeated = got["stored"], got["skipped"], got["repeated"]
        out["imported"] += 1
    return out


def schedule_ledger(org_id: int, *, tries: int = 0, delay_s: int = 0, user_id: int | None = None):
    return jobs.enqueue(
        "whatnot_ledger",
        {"org_id": org_id, "tries": tries, "user_id": user_id},
        org_id=org_id,
        dedupe_key=f"whatnot_ledger:{org_id}",
        delay_s=delay_s,
    )


@jobs.handler("whatnot_ledger")
def ledger_job(payload: dict) -> None:
    """Ask for the ledger when it is time, import what is ready, and come back while
    Whatnot is still building a file."""
    org_id, tries = payload["org_id"], int(payload.get("tries") or 0)
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)

    def run():
        if tries == 0 and not open_runs(org_id):
            request(org_id, client)
            db.session.commit()
        return check(org_id, client)

    result = sync._run(org_id, "ledger", run, flag=False)
    db.session.commit()
    if result and result["waiting"] and tries + 1 < LEDGER_TRIES and not jobs.inline():
        schedule_ledger(org_id, tries=tries + 1, delay_s=LEDGER_EVERY_S)


def ledger_due(org_id: int) -> bool:
    """The timer: a report is still being built, or a day has passed since the last ask."""
    if open_runs(org_id):
        return True
    return sync._due(org_id, "ledger", LEDGER_EVERY)


# ---------- the numbers, as the pages show them ----------


def figures(org_id: int, *, whatnot_show_id: str | None = None, since: datetime | None = None):
    """Whatnot's ledger for one show (or since a date): its net (the sum of Whatnot's own
    signed amounts), each kind of line, and the fees it lists. None when it holds no line."""
    scope = [WhatnotLedgerEntry.organization_id == org_id]
    if whatnot_show_id is not None:
        # A refund or a shipping line may name its order but no show: it belongs to the
        # show its order's earnings line names.
        earned = aliased(WhatnotLedgerEntry)
        of_this_show = select(earned.whatnot_order_id).where(
            earned.organization_id == org_id,
            earned.whatnot_show_id == whatnot_show_id,
            earned.whatnot_order_id.is_not(None),
        )
        scope.append(
            or_(
                WhatnotLedgerEntry.whatnot_show_id == whatnot_show_id,
                WhatnotLedgerEntry.whatnot_show_id.is_(None)
                & WhatnotLedgerEntry.whatnot_order_id.in_(of_this_show),
            )
        )
    if since is not None:
        scope.append(WhatnotLedgerEntry.completed_at >= since)
    # Amounts in different currencies are never added together: the figures are in the
    # shop's own currency, and lines in any other are counted and said, not summed.
    conn = connection.get(org_id)
    currency = (conn.currency if conn is not None else None) or "USD"
    mine = WhatnotLedgerEntry.currency.is_(None) | (WhatnotLedgerEntry.currency == currency)
    where = [*scope, mine]
    other = (
        db.session.scalar(select(func.count()).select_from(WhatnotLedgerEntry).where(*scope, ~mine))
        or 0
    )
    kinds = db.session.execute(
        select(
            WhatnotLedgerEntry.transaction_type,
            func.count(),
            func.sum(WhatnotLedgerEntry.amount_cents),
        )
        .where(*where)
        .group_by(WhatnotLedgerEntry.transaction_type)
        .order_by(func.sum(WhatnotLedgerEntry.amount_cents).desc())
    ).all()
    if not kinds and not other:
        return None
    units = func.coalesce(WhatnotLedgerEntry.quantity, 1)
    fee_sums = db.session.execute(
        select(
            *[func.coalesce(func.sum(getattr(WhatnotLedgerEntry, f)), 0) for f in FEES],
            # Both are per item in Whatnot's file: times the quantity sold.
            func.coalesce(
                func.sum(cast(WhatnotLedgerEntry.item_price_cents, BigInteger) * units), 0
            ),
            func.coalesce(
                func.sum(cast(WhatnotLedgerEntry.cost_of_goods_cents, BigInteger) * units), 0
            ),
            func.count(WhatnotLedgerEntry.cost_of_goods_cents),
            func.max(WhatnotLedgerEntry.completed_at),
        ).where(*where)
    ).one()
    fees = {name.removesuffix("_cents"): int(v) for name, v in zip(FEES, fee_sums, strict=False)}
    through = fee_sums[len(FEES) + 3]
    return {
        "currency": currency,
        "otherCurrencyLines": other,
        "netCents": int(sum(total for _, _, total in kinds)),
        "kinds": [{"kind": k, "lines": n, "cents": int(total)} for k, n, total in kinds],
        "fees": fees,
        "itemPricesCents": int(fee_sums[len(FEES)]),
        # What the seller recorded as its own cost, when Whatnot's file carries it.
        "costOfGoodsCents": int(fee_sums[len(FEES) + 1]) if fee_sums[len(FEES) + 2] else None,
        "through": through.astimezone(UTC).isoformat() if through else None,
    }


def status(org_id: int) -> dict:
    """Reports → Whatnot's ledger: where the reading stands."""
    runs = db.session.scalars(
        select(WhatnotReport)
        .where(WhatnotReport.organization_id == org_id, WhatnotReport.report_type == REPORT)
        .order_by(WhatnotReport.id.desc())
        .limit(5)
    ).all()
    state = connection.sync_state(org_id, "ledger")
    return {
        "lines": db.session.scalar(
            select(func.count())
            .select_from(WhatnotLedgerEntry)
            .where(WhatnotLedgerEntry.organization_id == org_id)
        )
        or 0,
        "building": any(r.status in OPEN_RUNS for r in runs),
        "lastImportedAt": next(
            (r.imported_at.isoformat() for r in runs if r.imported_at is not None), None
        ),
        # The token may not read reports: said in words on the page.
        "notAllowed": (state.last_error or "").startswith(Forbidden.__name__),
        "lastFailed": bool(state.last_error),
        "unreadable": bool(runs) and runs[0].status == "unreadable",
        # Lines of the latest imported report that are not in the figures.
        "skippedLines": next((r.skipped or 0 for r in runs if r.status == "imported"), 0),
        "repeatedLines": next((r.repeated or 0 for r in runs if r.status == "imported"), 0),
    }


__all__ = ["LedgerError", "WhatnotError", "figures", "status"]
