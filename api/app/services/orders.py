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
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotVariant,
)
from ..models.orders import FULFILLMENT

STANDING = (SaleStatus.detected, SaleStatus.reserved, SaleStatus.paid, SaleStatus.fulfilled)
# The order states Whatnot holds as paid (services/whatnot/orders.PAYMENT).
WHATNOT_PAID = ("pending_fulfillment", "completed")


def _paid_unnamed():
    """SQL: detected, no product named, and Whatnot's own order for it is paid."""
    return (
        (Sale.status == SaleStatus.detected)
        & Sale.sku_id.is_(None)
        & select(WhatnotOrder.id)
        .where(
            WhatnotOrder.organization_id == Sale.organization_id,
            WhatnotOrder.whatnot_id == Sale.external_order_id,
            WhatnotOrder.status.in_(WHATNOT_PAID),
        )
        .exists()
    )


def _listing_off():
    """SQL: a person marked the sale's Whatnot listing "Not one of ours" (link "off")."""
    return (
        select(WhatnotOrderItem.id)
        .join(
            WhatnotVariant,
            (WhatnotVariant.organization_id == WhatnotOrderItem.organization_id)
            & (WhatnotVariant.whatnot_id == WhatnotOrderItem.variant_id),
        )
        .where(WhatnotOrderItem.sale_id == Sale.id, WhatnotVariant.link == "off")
        .exists()
    )


def to_pick_sales(*where) -> list[Sale]:
    """The sales (narrowed by `where`) that are wins Whatnot says are paid whose product
    nobody has named yet ("CHOOSE ANY…": the winner picks in chat), oldest first. Such a
    sale stays `detected` here (it holds no stock and is not paid in the app until its
    product is named, `pipeline.resolve`); the screens show it as "paid on Whatnot, product
    to pick". Derived on every read from Whatnot's own order, never stored.

    The one rule for such a win: the tiles, the orders, the pick list, the refused "Ignore"
    (`pipeline.must_name`) and the question asked again (`pipeline.ask_for_product`) all
    read it. A listing a person marked "Not one of ours" takes its wins out (the shop said
    it is no product of its own: they may be ignored), except a title that says the winner
    chooses (`rules.is_choice_title`): that listing is a lot, never one product, so "not one
    of ours" says nothing about the product the winner picks from the shop's shelves.
    One query; the few candidates are filtered here so the title rule stays in rules.py."""
    from . import rules

    off = _listing_off().label("off")
    rows = db.session.execute(
        select(Sale, off)
        .where(_paid_unnamed(), *where)
        .order_by(Sale.ordered_at.nulls_last(), Sale.id)
    ).all()
    return [sale for sale, is_off in rows if not is_off or rules.is_choice_title(sale.item_text)]


def to_pick_ids(sale_ids) -> set[int]:
    """Which of these sales are paid on Whatnot with their product still to pick (one query)."""
    ids = list(sale_ids)
    if not ids:
        return set()
    return {s.id for s in to_pick_sales(Sale.id.in_(ids))}


class OrderError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def attach(sale: Sale) -> Order:
    """Put a sale on its buyer's order for the show (creating it), then refresh the order status.
    A name is one person whatever its case (chat says "Lani808", Whatnot "lani808"), as it is
    everywhere else the app looks a buyer up; the order keeps the spelling it was made with."""
    order = db.session.scalar(
        select(Order)
        .where(
            Order.show_id == sale.show_id,
            func.lower(Order.buyer_username) == sale.buyer_username.lower(),
        )
        .order_by(Order.id)
        .limit(1)
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
    if order.status is OrderStatus.shipped and sale.status in STANDING:
        _won_after_shipping(sale)
    refresh(order)
    return order


def _won_after_shipping(sale: Sale) -> None:
    """A win joined an order whose package already left. Nothing moves it into a package (the
    pick list leaves shipped orders out, and `refresh` never touches fulfilment), so a person
    is told: pack it as its own parcel, or tell the buyer."""
    from . import pipeline

    pipeline.open_review(
        sale.organization_id,
        "won_after_shipping",
        f"{sale.buyer_username} won {sale.item_text or 'another lot'} after their order had "
        "already shipped. It is in no package and on no pick list: send it on its own or "
        "tell the buyer.",
        show_id=sale.show_id,
        sale_id=sale.id,
    )


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
            if to_pick_ids(s.id for s in order.sales):
                # Paid, but nothing is held for a win until its product is named: the packer
                # cannot pull it yet.
                raise OrderError(
                    "An item in this order is paid on Whatnot but its product is not named "
                    "yet: name the winner's pick in Review first."
                )
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
    """Per-SKU quantities to pull for the show's paid items not shipped yet. A paid item is
    pulled even while its order is still open because another win of the buyer waits for
    its product (that win is listed by `to_pick`): leaving it out made a buyer's named
    items vanish from the list until the last one was named."""
    from . import sale_items

    line = sale_items.lines()  # a win with two products pulls two products
    rows = db.session.execute(
        select(Sku, func.sum(line.c.quantity), func.count(func.distinct(Sale.order_id)))
        .join(line, line.c.sku_id == Sku.id)
        .join(Sale, Sale.id == line.c.sale_id)
        .join(Order, Order.id == Sale.order_id)
        .where(
            Sale.show_id == show.id,
            Sale.status.in_((SaleStatus.paid, SaleStatus.fulfilled)),
            Order.status.in_(
                (OrderStatus.open, OrderStatus.paid, OrderStatus.picking, OrderStatus.packed)
            ),
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


def to_pick(show: Show) -> list[dict]:
    """The show's wins Whatnot says are paid whose product is still to pick (`to_pick_sales`),
    oldest first, each with what the pick worker found in the winner's chat, if it looked.
    The pick list shows them as rows to settle instead of leaving them out."""
    from . import rules

    sales = to_pick_sales(Sale.show_id == show.id)
    asked: dict[int, tuple[int, dict | None]] = {}
    if sales:
        for sale_id, review_id, proposal in db.session.execute(
            select(ReviewItem.sale_id, ReviewItem.id, ReviewItem.proposal)
            .where(
                ReviewItem.sale_id.in_([s.id for s in sales]),
                ReviewItem.status == ReviewStatus.open,
                ReviewItem.reason == "unmatched_item",
            )
            .order_by(ReviewItem.id)
        ):
            asked.setdefault(sale_id, (review_id, proposal))
    out = []
    for sale in sales:
        review_id, proposal = asked.get(sale.id, (None, None))
        pick = (proposal or {}).get("pick") if isinstance(proposal, dict) else None
        out.append(
            {
                "saleId": sale.id,
                "orderId": sale.order_id,
                "buyer": sale.buyer_username,
                "lot": rules.lot_label(sale.item_text or "", sale.auction_type or ""),
                "item": sale.item_text,
                "quantity": sale.quantity,
                "priceCents": sale.price_cents,
                "reviewId": review_id,
                # What the winner typed, as the pick worker read it (services/picks.py).
                "pick": (
                    {k: pick.get(k) for k in ("state", "spot", "name") if pick.get(k) is not None}
                    if isinstance(pick, dict)
                    else None
                ),
            }
        )
    return out
