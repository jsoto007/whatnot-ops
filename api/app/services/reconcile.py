"""Payment truth from Whatnot's Seller Hub export (or any CSV with buyer + item [+ status]).

Column names are matched loosely. A row with status paid / completed (or no
status column) marks the matching open sale paid and commits stock; a row
with status cancelled / refunded cancels it. A row that matches nothing opens
a review item; a sale the file should have covered but did not is flagged.
Preview first, then apply. Re-applying the same file changes nothing.
"""

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from ..db import db
from ..models import ReviewItem, ReviewStatus, Sale, SaleStatus, Show
from . import catalog, pipeline
from .inventory import Actor

BUYER_COLS = ("buyer", "username", "buyer username", "customer", "user", "buyer name")
ITEM_COLS = ("product", "item", "listing", "title", "product name", "listing title")
QTY_COLS = ("quantity", "qty")
PRICE_COLS = ("sold price", "price", "total", "amount", "sale price")
STATUS_COLS = ("status", "order status", "payment status")
ORDER_COLS = ("order id", "order #", "order number", "order")
PAID = re.compile(r"paid|complete|success|shipped|fulfilled|delivered", re.I)
VOID = re.compile(r"cancel|refund|void|fail|declin", re.I)


class ReconcileError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Row:
    buyer: str
    item: str
    quantity: int = 1
    price_cents: int | None = None
    status: str = "paid"  # paid | void | unknown
    external_id: str | None = None
    action: str = ""  # mark_paid | cancel | unchanged | unmatched | skip
    sale_id: int | None = None
    note: str | None = None


@dataclass
class Plan:
    rows: list[Row] = field(default_factory=list)
    uncovered: list[int] = field(default_factory=list)  # standing sales the file did not mention

    @property
    def summary(self) -> dict:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.action] = out.get(r.action, 0) + 1
        if self.uncovered:
            out["uncovered"] = len(self.uncovered)
        return out


def _col(header: list[str], names: tuple[str, ...]) -> int | None:
    low = [h.strip().lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def _cents(s: str) -> int | None:
    m = re.search(r"-?\d+(?:[.,]\d{1,2})?", s or "")
    return int(round(float(m.group().replace(",", ".")) * 100)) if m else None


def parse_csv(text: str) -> list[Row]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ReconcileError("the file is empty") from None
    b, i = _col(header, BUYER_COLS), _col(header, ITEM_COLS)
    if b is None or i is None:
        raise ReconcileError(
            "need a buyer column (Buyer / Username / Customer) and an item column "
            "(Product / Item / Listing)"
        )
    q, p, st, o = (
        _col(header, QTY_COLS),
        _col(header, PRICE_COLS),
        _col(header, STATUS_COLS),
        _col(header, ORDER_COLS),
    )
    rows: list[Row] = []
    for raw in reader:
        if not any(c.strip() for c in raw):
            continue

        def cell(k, raw=raw):
            return raw[k].strip() if k is not None and k < len(raw) else ""

        row = Row(buyer=cell(b).lstrip("@"), item=cell(i), external_id=cell(o) or None)
        if not row.buyer or not row.item:
            row.action, row.note = "skip", "missing buyer or item"
            rows.append(row)
            continue
        try:
            row.quantity = max(1, int(float(cell(q) or "1")))
        except ValueError:
            row.quantity = 1
        row.price_cents = _cents(cell(p)) if p is not None else None
        status = cell(st)
        row.status = (
            "void"
            if VOID.search(status)
            else "paid"
            if (not status or PAID.search(status))
            else "unknown"
        )
        if row.status == "unknown":
            row.action, row.note = "skip", f"status {status!r} not understood"
        rows.append(row)
    return rows


def plan(show: Show, rows: list[Row]) -> Plan:
    """Match file rows to the show's sales. Each sale is used at most once."""
    sales = db.session.scalars(select(Sale).where(Sale.show_id == show.id).order_by(Sale.id)).all()
    taken: set[int] = set()
    out = Plan()
    for row in rows:
        if row.action == "skip":
            out.rows.append(row)
            continue
        sale = _match(show, row, sales, taken)
        if sale is None:
            row.action, row.note = "unmatched", "no sale for this buyer and item in the show"
        else:
            taken.add(sale.id)
            row.sale_id = sale.id
            if row.status == "paid":
                row.action = (
                    "unchanged"
                    if sale.status in (SaleStatus.paid, SaleStatus.fulfilled)
                    else "mark_paid"
                )
                if sale.status in (SaleStatus.cancelled, SaleStatus.payment_failed):
                    row.action, row.note = (
                        "unmatched",
                        f"sale #{sale.id} is {sale.status.value}; paid in the file",
                    )
            else:
                row.action = (
                    "unchanged"
                    if sale.status in (SaleStatus.cancelled, SaleStatus.payment_failed)
                    else "cancel"
                )
                if sale.status is SaleStatus.fulfilled:
                    row.action, row.note = (
                        "unmatched",
                        f"sale #{sale.id} already shipped; {row.status} in the file",
                    )
        out.rows.append(row)
    out.uncovered = [
        s.id
        for s in sales
        if s.id not in taken and s.status in (SaleStatus.detected, SaleStatus.reserved)
    ]
    return out


def _match(show: Show, row: Row, sales: list[Sale], taken: set[int]) -> Sale | None:
    buyer = row.buyer.lower()
    sku = pipeline.match_sku(show.organization_id, row.item)
    key = catalog.norm_alias(row.item)
    candidates = [s for s in sales if s.id not in taken and s.buyer_username.lower() == buyer]
    if row.external_id:
        for s in candidates:
            if s.external_order_id == row.external_id:
                return s
    for s in candidates:  # exact SKU or exact item text
        if (sku is not None and s.sku_id == sku.id) or (
            key and catalog.norm_alias(s.item_text) == key
        ):
            return s
    for s in candidates:  # loose: one contains the other
        a, b = catalog.norm_alias(s.item_text), key
        if a and b and (a in b or b in a):
            return s
    if len(candidates) == 1 and (sku is None or candidates[0].sku_id is None):
        return candidates[0]  # only one open purchase by this buyer; the item wording just differs
    return None


def apply(show: Show, p: Plan, *, actor: Actor, user=None, import_id: str) -> Plan:
    for row in p.rows:
        if row.action not in ("mark_paid", "cancel"):
            continue
        sale = db.session.get(Sale, row.sale_id)
        if row.action == "mark_paid":
            if sale.status is SaleStatus.detected:
                # Paid but never reserved (unknown item): it still counts as sold.
                if sale.sku_id is None:
                    row.action, row.note = (
                        "unmatched",
                        f"sale #{sale.id} has no SKU; assign one in Review first",
                    )
                    _review_unmatched(show, row, import_id)
                    continue
                pipeline._reserve(sale)  # never refused; an oversell shows as a shortfall
            pipeline.set_sale_status(
                sale, SaleStatus.paid, reason=f"payment file {import_id}", actor=actor
            )
            sale.paid_at = sale.paid_at or db.func.now()
            if row.external_id:
                sale.external_order_id = row.external_id
            if sale.price_cents is None and row.price_cents is not None:
                sale.price_cents = row.price_cents
        else:
            pipeline.set_sale_status(
                sale,
                SaleStatus.cancelled,
                reason=f"payment file {import_id}: {row.status}",
                actor=actor,
            )
    for row in p.rows:
        if row.action == "unmatched":
            _review_unmatched(show, row, import_id)
    for sale_id in p.uncovered:
        sale = db.session.get(Sale, sale_id)
        sale.flagged = True
        pipeline.open_review(
            show.organization_id,
            "no_payment_record",
            f"{sale.buyer_username}'s {sale.quantity} × "
            f"{sale.item_text or (sale.sku.code if sale.sku else '?')} is not in the payment file",
            show_id=show.id,
            sale_id=sale.id,
            source="reconcile",
        )
    from . import orders

    for order in _orders_of(show):
        orders.refresh(order)
    return p


def _orders_of(show: Show):
    from ..models import Order

    return db.session.scalars(select(Order).where(Order.show_id == show.id)).all()


def _review_unmatched(show: Show, row: Row, import_id: str) -> None:
    existing = db.session.scalar(
        select(ReviewItem).where(
            ReviewItem.show_id == show.id,
            ReviewItem.reason == "unmatched_payment",
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.summary.like(f"%{row.buyer}%{row.item}%"),
        )
    )
    if existing is not None:
        return
    pipeline.open_review(
        show.organization_id,
        "unmatched_payment",
        f"Payment file: {row.buyer} paid for {row.quantity} × {row.item} but no matching "
        f"sale was found ({row.note or ''})",
        show_id=show.id,
        proposal={
            "action": "none",
            "import": import_id,
            "buyer": row.buyer,
            "item": row.item,
            "quantity": row.quantity,
            "priceCents": row.price_cents,
        },
        source="reconcile",
    )
