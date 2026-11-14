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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import ParsedEvent, ReviewItem, ReviewStatus, Sale, SaleStatus, Show
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
    # paid | void | failed | unknown, and "pending" from Whatnot's own orders: the order
    # exists but Whatnot has not settled the payment (never read from a file).
    status: str = "paid"
    external_id: str | None = None
    action: str = ""  # mark_paid | cancel | decline | unchanged | unmatched | skip
    sale_id: int | None = None
    note: str | None = None
    # Set when the row is one of Whatnot's own order items (services/whatnot/orders.py):
    # the item's row id, the sale it was matched to before, and when it was ordered.
    ref: int | None = None
    sale_hint: int | None = None
    ordered_at: datetime | None = None
    # For a card declined at the win and paid on a retry: when the first, failed order was
    # placed. That is when the lot was won; `ordered_at` is when the payment went through.
    first_ordered_at: datetime | None = None
    # The product the listing itself names (a Whatnot listing linked to a product here).
    # None lets the listing's text decide, as for a payment file.
    sku_id: int | None = None
    # A person said the listing is none of the shop's products: its title must not name
    # one either (the winner says what they want, as for a "choose any" lot).
    listing_off: bool = False


@dataclass(frozen=True)
class Words:
    """How the payment record is named in reasons and review items: a file someone
    uploaded, or Whatnot's own orders read through its API."""

    paid: Callable[[str], str]
    void: Callable[[str, str], str]
    confirmed: Callable[[str], str]
    not_listed: str
    says: str  # "the payment file says" …
    unmatched: str  # "Payment file" …


FILE = Words(
    paid=lambda import_id: f"payment file {import_id}",
    void=lambda import_id, status: f"payment file {import_id}: {status}",
    confirmed=lambda import_id: f"paid at the win; in payment file {import_id}",
    not_listed="is not in the payment file",
    says="the payment file says",
    unmatched="Payment file",
)
WHATNOT = Words(
    paid=lambda import_id: "paid on Whatnot",
    void=lambda import_id, status: (
        "payment failed on Whatnot" if status == "failed" else "cancelled on Whatnot"
    ),
    confirmed=lambda import_id: "paid at the win; confirmed by Whatnot's order",
    not_listed="is not among Whatnot's orders for this show",
    says="Whatnot's order says",
    unmatched="Whatnot",
)


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


def plan(
    show: Show,
    rows: list[Row],
    *,
    shows: list[Show] | None = None,
    extra_sale_ids: set[int] | None = None,
) -> Plan:
    """Match file rows to the show's sales. Each sale is used at most once. `shows` widens
    the search to every recording of the same Whatnot show (two laptops, a restart).
    `extra_sale_ids` are sales in other shows already tied to one of these rows (a row's
    `sale_hint`): they can be matched and changed, but are never "uncovered" here."""
    show_ids = [s.id for s in shows] if shows else [show.id]
    where = Sale.show_id.in_(show_ids)
    if extra_sale_ids:
        where = where | (
            Sale.id.in_(extra_sale_ids) & (Sale.organization_id == show.organization_id)
        )
    sales = db.session.scalars(
        select(Sale)
        .where(where)
        .options(selectinload(Sale.event).selectinload(ParsedEvent.message))
        .order_by(Sale.id)
    ).all()
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
            if row.status == "pending":
                # Whatnot has the order but has not settled it: the sale stands as it is.
                row.action, row.note = "unchanged", "Whatnot is still taking the payment"
            elif row.status == "paid":
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
        and s.show_id in show_ids
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
    sku = pipeline.listed_sku(show.organization_id, row.sku_id) or (
        None if row.listing_off else pipeline.match_sku(show.organization_id, row.item)
    )
    key = catalog.norm_alias(row.item)
    candidates = [s for s in sales if s.id not in taken and s.buyer_username.lower() == buyer]
    if row.sale_hint is not None:
        for s in candidates:
            if s.id == row.sale_hint:
                return s  # matched to this very order item before
    if row.external_id:
        for s in candidates:
            if s.external_order_id == row.external_id:
                return s
        # A sale an earlier file already tied to another order is that order's, not this one's
        # (a re-export lists the paid lot again next to the refunded one).
        candidates = [s for s in candidates if not s.external_order_id]
    if row.ref is not None:
        return _same_win_sale(row, candidates, sku)

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


def _same_win_sale(row: Row, candidates: list[Sale], sku) -> Sale | None:
    """The sale one of Whatnot's own order items is, or None. Stricter than for a file: a
    file's rows are a person's export of one show's payments, so the wording is forgiven
    and a buyer's only purchase is assumed to be the one. Whatnot's orders include wins
    the app never saw, so "the buyer's only sale" may be a different win altogether, and
    matching it would pay, decline or cancel the wrong sale. A match needs the same win
    (`pipeline.same_win`: the lot by title, the price when the sale knows what was paid,
    and the order placed within half an hour of the win line), or failing the title, the
    same known product under those same price and time rules."""
    best: tuple | None = None
    for sale in candidates:
        price, won = pipeline.paid_price(sale), pipeline.won_when(sale)
        key = pipeline.same_win(
            sale.item_text, price, won, row.item, row.price_cents, row.ordered_at
        )
        if key is None and sku is not None and sale.sku_id == sku.id:
            # Worded differently, but both name the same product of the shop's.
            key = pipeline.same_win("x", price, won, "x", row.price_cents, row.ordered_at)
            key = (3, key[1]) if key is not None else None
        if key is None:
            continue
        # The surest first, then a sale still open over one already closed, then the oldest.
        rank = (key, sale.status not in pipeline.CLOSABLE, sale.id)
        if best is None or rank < best[0]:
            best = (rank, sale)
    return best[1] if best else None


def _player_price_differs(sale: Sale, row: Row) -> bool:
    """The sale's price came from the video player and the payment file disagrees. Chat
    sales are left alone: their price is often the SKU's list price, not what was paid."""
    if sale.price_cents is None or row.price_cents is None or sale.price_cents == row.price_cents:
        return False
    return sale.event is not None and sale.event.rule == "player_win"


def apply(
    show: Show,
    p: Plan,
    *,
    actor: Actor,
    user=None,
    import_id: str,
    words: Words = FILE,
    on_unmatched: Callable[[Row], bool] | None = None,
    flag_uncovered: bool = True,
) -> Plan:
    """Apply a plan. `words` names the record in reasons and review items. `on_unmatched`
    may settle a paid row no sale matched (Whatnot's own orders record the sale) and
    returns True when it did; otherwise the row goes to Review. `flag_uncovered` is False
    while the record cannot be complete yet (a show still live)."""
    for row in p.rows:
        if row.action == "unchanged" and row.status == "paid":
            # Already paid (Whatnot charges at the win): the file only confirms it.
            sale = db.session.get(Sale, row.sale_id)
            if _player_price_differs(sale, row):
                _review_price(show, sale, row, words)  # not confirmed until a person checks it
            elif sale.status_reason == pipeline.AUTOPAY_REASON:
                # The file agrees: the next file need not list it again.
                sale.status_reason = words.confirmed(import_id)
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
                sale, SaleStatus.paid, reason=words.paid(import_id), actor=actor
            )
            sale.paid_at = sale.paid_at or db.func.now()
            if row.external_id:
                sale.external_order_id = row.external_id
            if sale.price_cents is None and row.price_cents is not None:
                sale.price_cents = row.price_cents
            elif _player_price_differs(sale, row):
                _review_price(show, sale, row, words)
        else:
            pipeline.set_sale_status(
                sale,
                SaleStatus.payment_failed if row.action == "decline" else SaleStatus.cancelled,
                reason=words.void(import_id, row.status),
                actor=actor,
            )
    seen: dict[tuple[str, str], int] = {}
    for row in p.rows:
        if row.action == "unmatched":
            if on_unmatched is not None and row.sale_id is None and on_unmatched(row):
                continue  # settled: the record itself is the sale (see `on_unmatched`)
            k = (row.buyer.lower(), row.item.lower())
            seen[k] = seen.get(k, 0) + 1
            _review_unmatched(show, row, import_id, occurrence=seen[k], words=words)
    for sale_id in p.uncovered if flag_uncovered else ():
        sale = db.session.get(Sale, sale_id)
        sale.flagged = True
        pipeline.open_review(
            show.organization_id,
            "no_payment_record",
            f"{sale.buyer_username}'s {sale.quantity} × "
            f"{sale.item_text or (sale.sku.code if sale.sku else '?')} {words.not_listed}",
            show_id=sale.show_id,
            sale_id=sale.id,
            source="reconcile",
        )
    from . import orders

    touched = {row.sale_id for row in p.rows if row.sale_id} | set(p.uncovered)
    for order in _orders_of(show, touched):
        orders.refresh(order)
    return p


def _review_price(show: Show, sale: Sale, row: Row, words: Words = FILE) -> None:
    pipeline.open_review(
        show.organization_id,
        "price_mismatch",
        f"{sale.buyer_username}'s {sale.item_text}: the video player read "
        f"{pipeline.money(sale.price_cents)}, {words.says} "
        f"{pipeline.money(row.price_cents)} — check which one is right",
        show_id=sale.show_id,
        sale_id=sale.id,
        source="reconcile",
    )


def _orders_of(show: Show, sale_ids: set[int] | None = None):
    """The show's orders, and the orders of any sale the plan touched in another recording
    of the same Whatnot show."""
    from ..models import Order

    orders = set(db.session.scalars(select(Order).where(Order.show_id == show.id)).all())
    if sale_ids:
        orders |= set(
            db.session.scalars(
                select(Order).join(Sale, Sale.order_id == Order.id).where(Sale.id.in_(sale_ids))
            ).all()
        )
    return sorted(orders, key=lambda o: o.id)


def _review_unmatched(
    show: Show, row: Row, import_id: str, *, occurrence: int = 1, words: Words = FILE
) -> None:
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
        f"{words.unmatched}: {row.buyer} paid for {row.quantity} × {row.item} but no matching "
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
