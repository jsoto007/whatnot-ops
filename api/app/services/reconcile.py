"""Payment truth from Whatnot's Seller Hub export (or any CSV with buyer + item [+ status]).

Column names are matched loosely. Whatnot charges every win, so sales are
already paid when the file comes: a paid / completed row (or no status column)
confirms the sale, marks a still-open one paid, and restores one whose card was
declined at the win; a cancelled / refunded row cancels it, a failed / declined
row marks its payment failed. A row that matches nothing opens
a review item; a sale the file should have covered but did not is flagged.
Preview first, then apply. Re-applying the same file changes nothing.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select

from ..db import db
from ..models import ReviewItem, ReviewStatus, Sale, SaleStatus, Show
from . import catalog, pipeline
from . import text as text_safe
from .inventory import Actor

BUYER_COLS = ("buyer", "username", "buyer username", "customer", "user", "buyer name")
ITEM_COLS = ("product", "item", "listing", "title", "product name", "listing title")
QTY_COLS = ("quantity", "qty")
# A unit price fills Sale.price_cents (which is per unit) directly; a line total only when it
# divides exactly by the quantity.
PRICE_COLS = ("sold price", "price", "sale price", "unit price")
TOTAL_COLS = ("total", "amount", "order total", "line total")
STATUS_COLS = ("status", "order status", "payment status")
ORDER_COLS = ("order id", "order #", "order number", "order")
PAID = re.compile(r"paid|complete|success|shipped|fulfilled|delivered", re.I)
VOID = re.compile(r"cancel|refund|void|fail|declin", re.I)
DECLINED = re.compile(r"fail|declin", re.I)


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
    status: str = "paid"  # paid | void | failed | unknown
    external_id: str | None = None
    action: str = ""  # mark_paid | cancel | decline | unchanged | unmatched | skip
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


MAX_CENTS = 10_000_000  # $100,000 per item: anything larger is a misread, and int4-safe
_CURRENCY = re.compile(r"(?i)usd|cad|aud|eur|gbp|us\$|[$€£¥]")


def _cents(s: str) -> int | None:
    """Money in integer cents from a whole cell, or None when the cell is not clearly a
    non-negative amount (the row then keeps its chat or SKU price).

    Accepted: "$25", "25.00", "$1,234.56", "12,500", "USD 1,050", "12,50" / "1000,50"
    (decimal comma), "1.234,56" (European). Refused: negatives in any notation
    ("-5", "-$5", "(5.00)", "−5"), anything with other text ("2 for $10"), "$.99",
    "1e3", and amounts over MAX_CENTS. Rounds half up to the cent."""
    t = (s if isinstance(s, str) else "").replace("\u00a0", " ").strip()
    if not t or (t.startswith("(") and t.endswith(")")):
        return None
    t = _CURRENCY.sub("", t).strip()
    if not t or t[0] in "-\u2212\u2012\u2013":
        return None
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
        num = t.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+,\d+", t):
        num = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+,\d{1,2}", t):
        num = t.replace(",", ".")
    elif re.fullmatch(r"\d+(\.\d+)?", t):
        num = t
    else:
        return None
    try:
        cents = int((Decimal(num) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None
    return cents if cents <= MAX_CENTS else None


def parse_csv(text: str) -> list[Row]:
    # An uploaded file is plain text: no controls or invisible marks reach a name or a review.
    reader = csv.reader(io.StringIO(text_safe.clean_file(text)))
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
    q, p, tot, st, o = (
        _col(header, QTY_COLS),
        _col(header, PRICE_COLS),
        _col(header, TOTAL_COLS),
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
            row.quantity = min(10_000, max(1, int(float(cell(q) or "1"))))
        except (ValueError, OverflowError):
            row.quantity = 1
        if p is not None:
            row.price_cents = _cents(cell(p))
        elif tot is not None:
            total = _cents(cell(tot))
            # Sale.price_cents is per unit: only a total that splits evenly can fill it.
            exact = total is not None and total % row.quantity == 0
            row.price_cents = total // row.quantity if exact else None
        status = cell(st)
        row.status = (
            "failed"
            if DECLINED.search(status)
            else "void"
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
                if sale.status is SaleStatus.payment_failed and sale.sku_id is not None:
                    # Declined at the win, paid another way before the show ended.
                    if pipeline._round(sale) == pipeline.REPAID:
                        row.action, row.note = (
                            "unmatched",
                            f"sale #{sale.id} was declined twice; paid in the file",
                        )
                elif sale.status in (SaleStatus.cancelled, SaleStatus.payment_failed):
                    row.action, row.note = (
                        "unmatched",
                        f"sale #{sale.id} is {sale.status.value}; paid in the file",
                    )
            else:
                row.action = (
                    "unchanged"
                    if sale.status in (SaleStatus.cancelled, SaleStatus.payment_failed)
                    else "decline"
                    if row.status == "failed"
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
        if s.id not in taken
        and (
            s.status in (SaleStatus.detected, SaleStatus.reserved)
            # Paid only because Whatnot charges at the win: the file should list it, and a
            # sale missing from it may be a decline nobody saw.
            or (s.status is SaleStatus.paid and s.status_reason == pipeline.AUTOPAY_REASON)
        )
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
        # A sale an earlier file already tied to another order is that order's, not this one's
        # (a re-export lists the paid lot again next to the refunded one).
        candidates = [s for s in candidates if not s.external_order_id]

    def priced(found: list[Sale]) -> Sale | None:
        # One buyer can win several lots of one listing at different prices: the price
        # tells them apart before falling back to the oldest.
        if not found:
            return None
        paid = row.price_cents
        # Same price first, then a sale still open over one already closed, then the oldest.
        return min(
            found,
            key=lambda s: (
                not (paid is not None and s.price_cents == paid),
                s.status not in pipeline.CLOSABLE,
                s.id,
            ),
        )

    # Exact item text first: it carries the lot number ("Red bundle #752"), the SKU does not.
    exact = priced([s for s in candidates if key and catalog.norm_alias(s.item_text) == key])
    if exact is not None:
        return exact
    by_sku = priced([s for s in candidates if sku is not None and s.sku_id == sku.id])
    if by_sku is not None:
        return by_sku
    loose = priced(  # one contains the other
        [
            s
            for s in candidates
            if (a := catalog.norm_alias(s.item_text)) and key and (a in key or key in a)
        ]
    )
    if loose is not None:
        return loose
    if len(candidates) == 1 and (sku is None or candidates[0].sku_id is None):
        return candidates[0]  # only one open purchase by this buyer; the item wording just differs
    return None


def _player_price_differs(sale: Sale, row: Row) -> bool:
    """The sale's price came from the video player and the payment file disagrees. Chat
    sales are left alone: their price is often the SKU's list price, not what was paid."""
    if sale.price_cents is None or row.price_cents is None or sale.price_cents == row.price_cents:
        return False
    return sale.event is not None and sale.event.rule == "player_win"


def apply(show: Show, p: Plan, *, actor: Actor, user=None, import_id: str) -> Plan:
    for row in p.rows:
        if row.action == "unchanged" and row.status == "paid":
            # Already paid (Whatnot charges at the win): the file only confirms it.
            sale = db.session.get(Sale, row.sale_id)
            if _player_price_differs(sale, row):
                _review_price(show, sale, row)  # not confirmed until a person checks it
            elif sale.status_reason == pipeline.AUTOPAY_REASON:
                # The file agrees: the next file need not list it again.
                sale.status_reason = f"paid at the win; in payment file {import_id}"
                sale.external_order_id = sale.external_order_id or row.external_id
        if row.action not in ("mark_paid", "cancel", "decline"):
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
                    continue  # reviewed with the other unmatched rows below
                pipeline._reserve(sale)  # never refused; an oversell shows as a shortfall
            pipeline.set_sale_status(
                sale, SaleStatus.paid, reason=f"payment file {import_id}", actor=actor
            )
            sale.paid_at = sale.paid_at or db.func.now()
            if row.external_id:
                sale.external_order_id = row.external_id
            if sale.price_cents is None and row.price_cents is not None:
                sale.price_cents = row.price_cents
            elif _player_price_differs(sale, row):
                _review_price(show, sale, row)
        else:
            pipeline.set_sale_status(
                sale,
                SaleStatus.payment_failed if row.action == "decline" else SaleStatus.cancelled,
                reason=f"payment file {import_id}: {row.status}",
                actor=actor,
            )
    seen: dict[tuple[str, str], int] = {}
    for row in p.rows:
        if row.action == "unmatched":
            k = (row.buyer.lower(), row.item.lower())
            seen[k] = seen.get(k, 0) + 1
            _review_unmatched(show, row, import_id, occurrence=seen[k])
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


def _review_price(show: Show, sale: Sale, row: Row) -> None:
    pipeline.open_review(
        show.organization_id,
        "price_mismatch",
        f"{sale.buyer_username}'s {sale.item_text}: the video player read "
        f"{pipeline.money(sale.price_cents)}, the payment file says "
        f"{pipeline.money(row.price_cents)} — check which one is right",
        show_id=show.id,
        sale_id=sale.id,
        source="reconcile",
    )


def _orders_of(show: Show):
    from ..models import Order

    return db.session.scalars(select(Order).where(Order.show_id == show.id)).all()


def _review_unmatched(show: Show, row: Row, import_id: str, *, occurrence: int = 1) -> None:
    """One open review per paid order nobody could match. The key is the order id when the
    file has one; otherwise buyer + item (case-insensitive) + which occurrence in the file,
    so two separate "Mystery Pack" wins by the same buyer stay two items, while applying
    the same file again adds nothing."""
    key = (
        f"order:{row.external_id}"
        if row.external_id
        else f"{row.buyer.lower()}|{row.item.lower()}|{occurrence}"
    )
    existing = db.session.scalar(
        select(ReviewItem).where(
            ReviewItem.show_id == show.id,
            ReviewItem.reason == "unmatched_payment",
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.proposal["dedupeKey"].as_string() == key,
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
            "dedupeKey": key,
            "quantity": row.quantity,
            "priceCents": row.price_cents,
        },
        source="reconcile",
        dedupe=False,  # the structured buyer + item check above is the dedupe
    )
