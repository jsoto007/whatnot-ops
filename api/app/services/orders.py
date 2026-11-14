"""Orders: one per buyer per show. Status follows the sales until a person starts fulfilment."""

from datetime import UTC, datetime

from sqlalchemy import func, select

from ..db import db
from ..models import (
    EventKind,
    Order,
    OrderStatus,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    Sku,
)
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


# The steps that put an item in a box (going back a step is always allowed).
_FORWARD = {
    OrderStatus.paid: (OrderStatus.picking,),
    OrderStatus.picking: (OrderStatus.packed,),
    OrderStatus.packed: (OrderStatus.shipped,),
}


_DOUBT = {
    "decline_seen": (
        "Whatnot showed a card declined for an item in this order, and its order has not "
        "said yet whether it was paid. Wait for it, or settle it in Review."
    ),
    "lot_won_twice": (
        "An item in this order is a lot that two buyers were shown winning. Wait for "
        "Whatnot's orders to settle it, or settle it in Review."
    ),
}


def in_doubt(order: Order) -> str | None:
    """Why nothing of this order goes in a box yet, or None. One of its standing sales has
    an open question only Whatnot's order (or a person) can answer: the stream showed its
    card declined (`pipeline.flag_decline`), or showed the lot won by two buyers
    (`pipeline.ask_which_win`). The sale still counts as paid meanwhile."""
    ids = [s.id for s in order.sales if s.status in STANDING]
    if not ids:
        return None
    return db.session.scalar(
        select(ReviewItem.reason)
        .where(
            ReviewItem.sale_id.in_(ids),
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.reason.in_(tuple(_DOUBT)),
        )
        .order_by(ReviewItem.id)
        .limit(1)
    )


def card_in_doubt(order: Order) -> bool:
    return in_doubt(order) is not None


def verified(order: Order) -> bool:
    standing = [s for s in order.sales if s.status in STANDING]
    return bool(standing) and all(
        s.status in (SaleStatus.paid, SaleStatus.fulfilled) for s in standing
    )


def ship_by_carrier(order: Order, *, tracking: str | None, actor_label: str = "Whatnot") -> Order:
    """The carrier has the buyer's package(s) (Whatnot's shipment status says so): the
    order is shipped, whichever step of picking or packing the app had it at. Nobody has
    to click through steps that already happened on the packing table. Its paid sales
    become fulfilled, as when a person marks it shipped; nothing moves in stock (the
    units left the shelf when they were paid)."""
    from . import pipeline
    from .inventory import Actor

    if order.status not in (OrderStatus.paid, OrderStatus.picking, OrderStatus.packed):
        raise OrderError(f"cannot mark a {order.status.value} order shipped")
    if not verified(order):
        raise OrderError("every item must be paid before fulfilment (see Review)")
    now = datetime.now(UTC)
    order.picked_at = order.picked_at or now
    order.packed_at = order.packed_at or now
    order.shipped_at = now
    order.status = OrderStatus.shipped
    order.tracking = (tracking or order.tracking or "").strip()[:120] or None
    for s in order.sales:
        if s.status is SaleStatus.paid:
            pipeline.set_sale_status(
                s,
                SaleStatus.fulfilled,
                reason="shipped (the carrier has it)",
                actor=Actor("whatnot", None, actor_label),
            )
    return order


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
    if new_status in _FORWARD.get(order.status, ()) and (why := in_doubt(order)) is not None:
        raise OrderError(_DOUBT[why])
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
