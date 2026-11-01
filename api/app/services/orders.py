"""Orders: one per buyer per show. Status follows the sales until a person starts fulfilment."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from ..db import db
from ..models import EventKind, Order, OrderStatus, ParsedEvent, Sale, SaleStatus, Show, Sku
from ..models.orders import FULFILLMENT

STANDING = (SaleStatus.detected, SaleStatus.reserved, SaleStatus.paid, SaleStatus.fulfilled)


class OrderError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def attach(sale: Sale) -> Order:
    """Put a sale on its buyer's order for the show (creating it), then refresh the order status."""
    order = db.session.scalar(
        select(Order)
        .where(Order.show_id == sale.show_id, Order.buyer_username == sale.buyer_username)
        .with_for_update()
    )
    if order is None:
        order = Order(
            organization_id=sale.organization_id,
            show_id=sale.show_id,
            buyer_username=sale.buyer_username,
        )
        db.session.add(order)
        db.session.flush()
    sale.order = order
    sale.order_id = order.id
    refresh(order)
    return order


def refresh(order: Order) -> Order:
    """Derive open / paid / cancelled from the sales; never touch picking / packed / shipped.
    Whatnot charges every win, so an order is paid (ready to pick) as soon as its items are
    known; it stays open only while a sale still needs a person (item not recognised)."""
    if order.status in FULFILLMENT:
        return order
    standing = [s for s in order.sales if s.status in STANDING]
    if not standing:
        order.status = OrderStatus.cancelled
    elif all(s.status in (SaleStatus.paid, SaleStatus.fulfilled) for s in standing):
        order.status = OrderStatus.paid
    else:
        order.status = OrderStatus.open
    return order


def verified(order: Order) -> bool:
    standing = [s for s in order.sales if s.status in STANDING]
    return bool(standing) and all(
        s.status in (SaleStatus.paid, SaleStatus.fulfilled) for s in standing
    )


def advance(
    order: Order,
    new_status: OrderStatus,
    *,
    actor_label: str,
    tracking: str | None = None,
    packed_by: str | None = None,
) -> Order:
    """picking → packed → shipped, only for verified orders; shipped marks the sales fulfilled.
    `packed_by` (the packer's name) is written on the first move to packed, as on the paper slip."""
    from . import pipeline
    from .inventory import Actor

    allowed = {
        OrderStatus.paid: {OrderStatus.picking},
        OrderStatus.picking: {OrderStatus.packed, OrderStatus.paid},
        OrderStatus.packed: {OrderStatus.shipped, OrderStatus.picking},
        OrderStatus.shipped: set(),
        OrderStatus.open: set(),
        OrderStatus.cancelled: set(),
    }
    if new_status not in allowed[order.status]:
        if order.status is OrderStatus.open:
            raise OrderError("an item in this order still needs a decision in Review")
        raise OrderError(f"cannot go from {order.status.value} to {new_status.value}")
    if not verified(order):
        raise OrderError("every item must be paid before fulfilment (see Review)")
    now = datetime.now(UTC)
    order.status = new_status
    if new_status is OrderStatus.picking:
        order.picked_at = now
    elif new_status is OrderStatus.packed:
        order.packed_at = now
        order.packed_by = order.packed_by or (packed_by or "").strip()[:80] or None
    elif new_status is OrderStatus.shipped:
        order.shipped_at = now
        order.tracking = (tracking or order.tracking or "").strip() or None
        for s in order.sales:
            if s.status is SaleStatus.paid:
                pipeline.set_sale_status(
                    s,
                    SaleStatus.fulfilled,
                    reason="shipped",
                    actor=Actor("user", None, actor_label),
                )
    return order


def gifts_for(order: Order) -> list[dict]:
    """Gift lines the host or platform gave this buyer in the order's show (the slip's Gift
    rows). Gifts are events, not sales: they hold no stock and carry no price."""
    rows = db.session.execute(
        select(ParsedEvent.item_text, ParsedEvent.message_id)
        .where(
            ParsedEvent.show_id == order.show_id,
            ParsedEvent.kind == EventKind.gift,
            ParsedEvent.confidence >= 0.8,
            func.lower(ParsedEvent.buyer_username) == order.buyer_username.lower(),
        )
        .order_by(ParsedEvent.id)
    ).all()
    from ..models import ChatMessage

    seqs = dict(
        db.session.execute(
            select(ChatMessage.id, ChatMessage.seq).where(
                ChatMessage.id.in_([m for _, m in rows] or [0])
            )
        ).all()
    )
    return [{"item": item, "seq": seqs.get(mid)} for item, mid in rows]


def pick_list(show: Show) -> list[dict]:
    """Per-SKU quantities to pull for the show's paid-but-not-shipped orders."""
    rows = db.session.execute(
        select(Sku, func.sum(Sale.quantity), func.count(func.distinct(Sale.order_id)))
        .join(Sale, Sale.sku_id == Sku.id)
        .join(Order, Order.id == Sale.order_id)
        .where(
            Sale.show_id == show.id,
            Sale.status.in_((SaleStatus.paid, SaleStatus.fulfilled)),
            Order.status.in_((OrderStatus.paid, OrderStatus.picking, OrderStatus.packed)),
        )
        .group_by(Sku.id)
        .order_by(Sku.code)
    ).all()
    return [
        {
            "skuId": sku.id,
            "code": sku.code,
            "product": sku.product.name,
            "variant": sku.name,
            "quantity": int(q),
            "orders": int(n),
        }
        for sku, q, n in rows
    ]
