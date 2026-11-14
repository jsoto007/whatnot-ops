"""A sale's products, as rows (stage 4 of the API-first plan).

A sale used to hold one product, in `Sale.sku_id`, with its stock keyed in the ledger by the
sale's own id. The ledger holds each kind of movement once per (product, source), so a
product could be changed once at most and never back, and a win could not hold two products.

Each product of a sale is now a `SaleItem` row, and stock is held row by row. The sale's
FIRST row keeps the sale's own ledger keys ("sale" / "sale_repaid", the sale's id), so every
sale named before rows existed, and every sale that never changes, moves stock exactly as it
always did. A row opened later (a product changed, a second product added) holds its stock
under its own id. `Sale.sku_id` is the sale's first open row's product: "the" product for
the many readers that show one, and how a sale says it has been named at all.

Nothing but this module makes or closes rows. Stock still moves only through
`inventory.move()` and its wrappers, with the keys from `key(row)`.
"""

from datetime import UTC, datetime

from sqlalchemy import case, func, select

from ..db import db
from ..models import MovementKind, Order, OrderStatus, Sale, SaleItem, SaleStatus, Sku
from . import inventory
from .inventory import Actor

# A win is one lot: a handful of products at most (a "two for one", a bundle).
MAX_PRODUCTS = 12


class ChangeError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def key(row: SaleItem) -> str:
    """The row's key in the stock ledger: the sale's own id for its first row (as before rows
    existed), "<sale id>:<row id>" for a row opened later."""
    return str(row.sale_id) if row.first else f"{row.sale_id}:{row.id}"


def sale_id_of(source_id: str) -> int | None:
    """The sale a ledger key belongs to: "12" and "12:34" (a row opened later) are sale 12."""
    head = (source_id or "").split(":", 1)[0]
    return int(head) if head.isdigit() else None


def _open(sale: Sale) -> list[SaleItem]:
    return list(
        db.session.scalars(
            select(SaleItem)
            .where(SaleItem.sale_id == sale.id, SaleItem.closed_at.is_(None))
            .order_by(SaleItem.id)
        )
    )


def rows(sale: Sale) -> list[SaleItem]:
    """The sale's products now: its open rows, oldest first. A sale that has a product and no
    row yet (named before rows existed, or being named right now) gets its row here. Empty
    for a sale with no product."""
    if sale.sku_id is None or sale.id is None:
        return []
    found = _open(sale)
    if not found:
        ever = db.session.scalar(
            select(func.count()).select_from(SaleItem).where(SaleItem.sale_id == sale.id)
        )
        row = SaleItem(
            organization_id=sale.organization_id,
            sale_id=sale.id,
            sku_id=sale.sku_id,
            quantity=sale.quantity,
            first=not ever,
        )
        db.session.add(row)
        db.session.flush()
        found = [row]
    return found


def ensure(sale: Sale) -> SaleItem | None:
    """The first of the sale's open rows (made if a named sale has none), or None."""
    found = rows(sale)
    return found[0] if found else None


def current(sale: Sale) -> SaleItem | None:
    """The sale's first open row without making one, or None."""
    found = _open(sale)
    return found[0] if found else None


def source_id(sale: Sale) -> str:
    """The ledger key of the sale's first product (the sale's own id unless it was changed)."""
    row = ensure(sale)
    return str(sale.id) if row is None else key(row)


def close_all(sale: Sale) -> None:
    """Close every open row: the sale is being given a new product from scratch. The caller
    has put the rows' stock back."""
    now = datetime.now(UTC)
    for row in _open(sale):
        row.closed_at = now
    db.session.flush()


def _lock(sale: Sale) -> None:
    """The order first, then the sale: the order in which shipping and the next picking step
    take them. Reading the order without its lock let a change slip in between "shipped"
    being written and the sale being marked fulfilled."""
    db.session.flush()
    if sale.order_id is not None:
        db.session.execute(select(Order.id).where(Order.id == sale.order_id).with_for_update())
    db.session.refresh(sale, with_for_update=True)
    if sale.order is not None:
        db.session.refresh(sale.order)


def _changeable(sale: Sale) -> None:
    if sale.sku_id is None:
        raise ChangeError("This sale has no product yet. Name it in Review.")
    if sale.status not in (SaleStatus.reserved, SaleStatus.paid):
        raise ChangeError("Only a sale that stands and has not shipped can be changed.")
    if sale.order is not None and sale.order.status is OrderStatus.shipped:
        raise ChangeError("This order has already shipped.")


def _usable(sale: Sale, sku: Sku) -> None:
    if sku.organization_id != sale.organization_id or not sku.active:
        raise ChangeError("That product is not in use.", 400)


def _put_back(sale: Sale, row: SaleItem, *, picked: bool, actor: Actor, why: str) -> None:
    """Return a row's stock as a cancel would: held → released; sold and still on the shelf →
    restocked. Picked: nothing moves, a person counts the unit back."""
    from . import pipeline

    round_ = pipeline._row_round(row)
    if sale.status is SaleStatus.reserved:
        inventory.release(
            row.sku_id,
            row.quantity,
            source_type=round_,
            source_id=key(row),
            actor=actor,
            reason=why,
        )
    elif not picked and not pipeline._row_moved(row, MovementKind.restock, round_):
        inventory.move(
            row.sku_id,
            MovementKind.restock,
            row.quantity,
            source_type=round_,
            source_id=key(row),
            actor=actor,
            reason=why,
        )
    row.closed_at = datetime.now(UTC)


def _take(sale: Sale, sku: Sku, quantity: int, *, actor: Actor, why: str) -> SaleItem:
    """Open a row for a product and hold it (sold too, if the sale is paid)."""
    row = SaleItem(
        organization_id=sale.organization_id,
        sale_id=sale.id,
        sku_id=sku.id,
        quantity=quantity,
        first=False,
    )
    db.session.add(row)
    db.session.flush()
    inventory.reserve(
        sku.id, quantity, source_type="sale", source_id=key(row), actor=actor, reason=why
    )
    if sale.status is SaleStatus.paid:
        inventory.commit(
            sku.id, quantity, source_type="sale", source_id=key(row), actor=actor, reason=why
        )
    return row


def _settle(sale: Sale, *, picked: bool) -> None:
    """After rows changed: the sale's own product is its first open row's, and its order
    follows."""
    from . import orders

    db.session.flush()
    first = current(sale)
    if first is not None and sale.sku_id != first.sku_id:
        sale.sku = db.session.get(Sku, first.sku_id)
        sale.sku_id = first.sku_id
    if picked:
        sale.flagged = True
    if sale.order is not None:
        orders.refresh(sale.order)


def _row(sale: Sale, row_id: int | None) -> SaleItem:
    found = rows(sale)
    if row_id is None:
        if len(found) != 1:
            raise ChangeError("This sale holds more than one product: say which one.", 400)
        return found[0]
    for row in found:
        if row.id == row_id:
            return row
    raise ChangeError("That product is not on this sale any more.", 404)


def change(
    sale: Sale, sku: Sku, *, actor: Actor, reason: str | None = None, row_id: int | None = None
) -> dict:
    """Give one of a sale's products another product, any number of times (A → B → A).

    The old product's stock goes back exactly as when the sale is cancelled (held: released;
    sold and still on the shelf: restocked), its row is closed, and the new product gets a
    row of its own with its own keys in the ledger, held or sold as the sale stands. The
    price the buyer paid does not change. `row_id` says which product, for a sale that
    holds several.

    Once the buyer's order is being picked the old unit may already be in a box, so nothing
    is put back by itself: the sale is flagged and the result says so (`picked`), for a
    note to the packer. A shipped, cancelled or declined sale cannot be changed, and a sale
    with no product yet is named in Review, not here."""
    from . import pipeline

    _lock(sale)
    _changeable(sale)
    _usable(sale, sku)
    row = _row(sale, row_id)
    before = row.sku_id
    if sku.id == before:
        return {"changed": False, "from": before, "to": before, "picked": False}
    picked = sale.status is SaleStatus.paid and pipeline._in_fulfilment(sale)
    why = reason or "product changed"
    _put_back(sale, row, picked=picked, actor=actor, why=why)
    _take(sale, sku, row.quantity, actor=actor, why=why)
    _settle(sale, picked=picked)
    return {"changed": True, "from": before, "to": sku.id, "picked": picked}


def add(
    sale: Sale, sku: Sku, *, actor: Actor, quantity: int = 1, reason: str | None = None
) -> dict:
    """Give a sale one more product: a "two for one", a bundle. It is held (and sold, if the
    sale is paid) under a row of its own; the price the buyer paid does not change. A sale
    with no product yet is named in Review first."""
    from . import pipeline

    _lock(sale)
    _changeable(sale)
    _usable(sale, sku)
    if not isinstance(quantity, int) or isinstance(quantity, bool) or not 1 <= quantity <= 99:
        raise ChangeError("How many must be a whole number from 1 to 99.", 400)
    if len(rows(sale)) >= MAX_PRODUCTS:
        raise ChangeError(f"A sale holds at most {MAX_PRODUCTS} products.", 400)
    picked = sale.status is SaleStatus.paid and pipeline._in_fulfilment(sale)
    row = _take(sale, sku, quantity, actor=actor, why=reason or "product added")
    _settle(sale, picked=picked)
    return {"rowId": row.id, "skuId": sku.id, "quantity": quantity, "picked": picked}


def remove(sale: Sale, row_id: int, *, actor: Actor, reason: str | None = None) -> dict:
    """Take one product off a sale that holds several. Its stock goes back as on a cancel
    (nothing by itself once the order is being picked). The last product cannot be taken
    off: change it, or cancel the sale."""
    from . import pipeline

    _lock(sale)
    _changeable(sale)
    row = _row(sale, row_id)
    if len(rows(sale)) == 1:
        raise ChangeError("This is the sale's only product. Change it, or cancel the sale.", 400)
    picked = sale.status is SaleStatus.paid and pipeline._in_fulfilment(sale)
    _put_back(sale, row, picked=picked, actor=actor, why=reason or "product removed")
    _settle(sale, picked=picked)
    return {"rowId": row.id, "skuId": row.sku_id, "quantity": row.quantity, "picked": picked}


def split(price_cents: int | None, parts: int) -> list[int | None]:
    """A win's price over its products (Javier's call, 2026-11-13): evenly in cents, the odd
    cents to the first products, so the parts always add up to the price. The show's totals
    do not change; this is only how a multi-product win reads product by product."""
    if parts <= 0:
        return []
    if price_cents is None:
        return [None] * parts
    each, extra = divmod(price_cents, parts)
    return [each + (1 if i < extra else 0) for i in range(parts)]


def products(sale: Sale) -> list[dict]:
    """The sale's products for a reader: [{"rowId", "sku", "quantity", "shareCents"}], the
    first one first. `shareCents` is that product's part of what the sale is worth (price ×
    quantity) under `split`. A sale named before rows existed, or built without the
    pipeline, reads as its own one product. Empty for a sale with no product."""
    found = list(sale.open_items)
    total = None if sale.price_cents is None else sale.price_cents * sale.quantity
    if not found:
        if sale.sku is None:
            return []
        return [{"rowId": None, "sku": sale.sku, "quantity": sale.quantity, "shareCents": total}]
    shares = split(total, len(found))
    # A sale's only row, when it is its first, stands for the sale itself: its count is the
    # sale's (its stock was moved at the sale's quantity, under the sale's own key).
    alone = len(found) == 1 and found[0].first
    return [
        {
            "rowId": row.id,
            "sku": row.sku,
            "quantity": sale.quantity if alone else row.quantity,
            "shareCents": share,
        }
        for row, share in zip(found, shares, strict=True)
    ]


def spelled(sale: Sale) -> bool:
    """Whether the sale's product text must carry the counts itself: it holds several
    products, or its one product's count is not the sale's own quantity (a product was
    taken off). A reader that prints "quantity × product" from the sale alone would
    otherwise print the wrong number of units to pack."""
    found = products(sale)
    return len(found) > 1 or (len(found) == 1 and found[0]["quantity"] != sale.quantity)


def label(sale: Sale, name) -> str:
    """The sale's products as one text; `name(sku)` words one product. One product whose
    count is the sale's own quantity reads as its name alone (the quantity is shown where
    the sale is). Otherwise every count is spelled: "Red bundle + Blue bundle ×2"."""
    found = products(sale)
    counts = spelled(sale)
    return " + ".join(
        name(p["sku"]) + (f" ×{p['quantity']}" if counts and p["quantity"] > 1 else "")
        for p in found
    )


def lines():
    """Every product of every sale as a subquery: (sale_id, sku_id, quantity, share), for the
    queries that count units or money per product. `share` follows `split`: the sale's worth
    (price × quantity) evenly over its products, the odd cents to the first. A named sale
    with no row counts as its own one product."""
    n = func.count().over(partition_by=SaleItem.sale_id)
    rn = func.row_number().over(partition_by=SaleItem.sale_id, order_by=SaleItem.id)
    total = func.coalesce(Sale.price_cents, 0) * Sale.quantity
    with_rows = (
        select(
            SaleItem.sale_id.label("sale_id"),
            SaleItem.sku_id.label("sku_id"),
            # A sale's only row, when it is its first, stands for the sale itself.
            case((SaleItem.first & (n == 1), Sale.quantity), else_=SaleItem.quantity).label(
                "quantity"
            ),
            # Whole cents: `//` (a plain `/` divides to a fraction, and the parts of a
            # sale then add up to more than its price).
            (total // n + case((rn <= total % n, 1), else_=0)).label("share"),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(SaleItem.closed_at.is_(None))
    )
    open_rows = select(SaleItem.id).where(SaleItem.sale_id == Sale.id, SaleItem.closed_at.is_(None))
    without = select(
        Sale.id.label("sale_id"),
        Sale.sku_id.label("sku_id"),
        Sale.quantity.label("quantity"),
        total.label("share"),
    ).where(Sale.sku_id.is_not(None), ~open_rows.exists())
    return with_rows.union_all(without).subquery("sale_lines")
