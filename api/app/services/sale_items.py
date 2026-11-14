"""A sale's products, as rows (stage 4 of the API-first plan).

A sale used to hold one product, in `Sale.sku_id`, with its stock keyed in the ledger by the
sale's own id. The ledger holds each kind of movement once per (product, source), so a
product could be changed once at most and never back, and a win could not hold two products.

Each product of a sale is now a `SaleItem` row. The sale's FIRST row keeps the sale's own
ledger keys ("sale" / "sale_repaid", the sale's id), so every sale named before rows existed,
and every sale that never changes, moves stock exactly as it always did. A row opened later
(a product changed, a second product added) holds its stock under its own id. `Sale.sku_id`
stays the sale's product while it has one row, which is what every reader still reads.

Nothing but this module makes or closes rows. Stock still moves only through
`inventory.move()` and its wrappers, called by `pipeline` with the keys from `source_id`.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select

from ..db import db
from ..models import MovementKind, Order, OrderStatus, Sale, SaleItem, SaleStatus, Sku
from . import inventory
from .inventory import Actor


class ChangeError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def current(sale: Sale) -> SaleItem | None:
    """The sale's open row (its product now), or None for a sale with no product."""
    return db.session.scalar(
        select(SaleItem)
        .where(SaleItem.sale_id == sale.id, SaleItem.closed_at.is_(None))
        .order_by(SaleItem.id.desc())
        .limit(1)
    )


def ensure(sale: Sale) -> SaleItem | None:
    """The open row of a sale that has a product, made if it is not there yet: a sale named
    before rows existed, or one being named right now. The row follows the sale's product
    and quantity. None for a sale with no product."""
    if sale.sku_id is None or sale.id is None:
        return None
    row = current(sale)
    if row is None:
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
    elif row.sku_id != sale.sku_id or row.quantity != sale.quantity:
        row.sku_id, row.quantity = sale.sku_id, sale.quantity
    return row


def source_id(sale: Sale) -> str:
    """The ledger key of the sale's product: the sale's own id for its first row (as before
    rows existed), "<sale id>:<row id>" for a row opened later."""
    row = ensure(sale)
    return str(sale.id) if row is None or row.first else f"{sale.id}:{row.id}"


def close(sale: Sale) -> None:
    """Close the sale's open row: its product is being changed or taken off. The caller has
    put the row's stock back (or decided not to); the next product gets a row of its own."""
    row = current(sale)
    if row is not None:
        row.closed_at = datetime.now(UTC)
        db.session.flush()


def change(sale: Sale, sku: Sku, *, actor: Actor, reason: str | None = None) -> dict:
    """Give a sale another product, any number of times (A → B → A).

    The old product's stock goes back exactly as when the sale is cancelled (held: released;
    sold and still on the shelf: restocked), its row is closed, and the new product gets a
    row of its own with its own keys in the ledger, held or sold as the sale stands. The
    price the buyer paid does not change.

    Once the buyer's order is being picked the old unit may already be in a box, so nothing
    is put back by itself: the sale is flagged and the result says so (`picked`), for a
    note to the packer. A shipped, cancelled or declined sale cannot be changed, and a sale
    with no product yet is named in Review, not here."""
    from . import orders, pipeline

    db.session.flush()
    if sale.order_id is not None:
        # The order first, then the sale: the order in which shipping and the next picking
        # step take them. Reading the order without its lock let a change slip in between
        # "shipped" being written and the sale being marked fulfilled.
        db.session.execute(select(Order.id).where(Order.id == sale.order_id).with_for_update())
    db.session.refresh(sale, with_for_update=True)
    if sale.order is not None:
        db.session.refresh(sale.order)
    if sale.sku_id is None:
        raise ChangeError("This sale has no product yet. Name it in Review.")
    if sale.status not in (SaleStatus.reserved, SaleStatus.paid):
        raise ChangeError(
            "Only a sale that stands and has not shipped can get another product.", 409
        )
    if sale.order is not None and sale.order.status is OrderStatus.shipped:
        raise ChangeError("This order has already shipped.")
    if sku.organization_id != sale.organization_id or not sku.active:
        raise ChangeError("That product is not in use.", 400)
    before = sale.sku_id
    if sku.id == before:
        return {"changed": False, "from": before, "to": before, "picked": False}
    picked = sale.status is SaleStatus.paid and pipeline._in_fulfilment(sale)
    key, round_ = source_id(sale), pipeline._round(sale)
    why = reason or "product changed"
    if sale.status is SaleStatus.reserved:
        inventory.release(
            before, sale.quantity, source_type=round_, source_id=key, actor=actor, reason=why
        )
    elif not picked and not pipeline._moved(sale, MovementKind.restock, round_):
        inventory.move(
            before,
            MovementKind.restock,
            sale.quantity,
            source_type=round_,
            source_id=key,
            actor=actor,
            reason=why,
        )
    close(sale)
    row = SaleItem(
        organization_id=sale.organization_id,
        sale_id=sale.id,
        sku_id=sku.id,
        quantity=sale.quantity,
        first=False,
    )
    db.session.add(row)
    sale.sku = sku
    sale.sku_id = sku.id
    db.session.flush()
    new_key = source_id(sale)
    inventory.reserve(
        sku.id, sale.quantity, source_type="sale", source_id=new_key, actor=actor, reason=why
    )
    if sale.status is SaleStatus.paid:
        inventory.commit(
            sku.id, sale.quantity, source_type="sale", source_id=new_key, actor=actor, reason=why
        )
    if picked:
        sale.flagged = True
    if sale.order is not None:
        orders.refresh(sale.order)
    return {"changed": True, "from": before, "to": sku.id, "picked": picked}


def sale_id_of(source_id: str) -> int | None:
    """The sale a ledger key belongs to: "12" and "12:34" (a row opened later) are sale 12."""
    head = (source_id or "").split(":", 1)[0]
    return int(head) if head.isdigit() else None
