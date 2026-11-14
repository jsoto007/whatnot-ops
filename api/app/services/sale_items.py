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

from sqlalchemy import func, select

from ..db import db
from ..models import Sale, SaleItem


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
