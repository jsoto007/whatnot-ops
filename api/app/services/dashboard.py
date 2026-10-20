"""Numbers for the dashboard and the reports, in one place so tiles and spreadsheets agree."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, func, select

from ..db import db
from ..models import (
    ChatMessage,
    EventKind,
    Order,
    OrderStatus,
    ParsedEvent,
    Product,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    Sku,
)

STANDING = (SaleStatus.detected, SaleStatus.reserved, SaleStatus.paid, SaleStatus.fulfilled)
PAID = (SaleStatus.paid, SaleStatus.fulfilled)
LOST = (SaleStatus.cancelled, SaleStatus.payment_failed)
TYPES = ("red", "bundle", "ultra", "mega")


def _qty(status_filter):
    return func.coalesce(
        func.sum(case((Sale.status.in_(status_filter), Sale.quantity), else_=0)), 0
    )


def _rev(status_filter):
    return func.coalesce(
        func.sum(
            case(
                (
                    Sale.status.in_(status_filter),
                    Sale.quantity * func.coalesce(Sale.price_cents, 0),
                ),
                else_=0,
            )
        ),
        0,
    )


def show_tiles(show: Show) -> dict:
    """The sheet's tiles for one show, plus money and queue counts."""
    row = db.session.execute(
        select(
            _qty(STANDING).label("items"),
            func.count(func.distinct(case((Sale.status.in_(STANDING), Sale.buyer_username)))).label(
                "buyers"
            ),
            _rev(PAID).label("paid"),
            _rev((SaleStatus.detected, SaleStatus.reserved)).label("pending"),
            func.coalesce(func.sum(case((Sale.status.in_(LOST), 1), else_=0)), 0).label("lost"),
            *[
                func.coalesce(
                    func.sum(
                        case(
                            ((Sale.status.in_(STANDING)) & (Sale.auction_type == t), Sale.quantity),
                            else_=0,
                        )
                    ),
                    0,
                ).label(t)
                for t in TYPES
            ],
        ).where(Sale.show_id == show.id)
    ).one()
    events = dict(
        db.session.execute(
            select(ParsedEvent.kind, func.count())
            .where(ParsedEvent.show_id == show.id)
            .group_by(ParsedEvent.kind)
        ).all()
    )
    orders = dict(
        db.session.execute(
            select(Order.status, func.count())
            .where(Order.show_id == show.id)
            .group_by(Order.status)
        ).all()
    )
    messages = (
        db.session.scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.show_id == show.id)
        )
        or 0
    )
    open_reviews = (
        db.session.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.show_id == show.id, ReviewItem.status == ReviewStatus.open)
        )
        or 0
    )
    return {
        "showId": show.id,
        "date": show.show_date.isoformat(),
        "startedAt": show.started_at.isoformat(),
        "updatedAt": show.updated_at.isoformat(),
        "live": (datetime.now(UTC) - show.updated_at) < timedelta(minutes=10),
        "messages": messages,
        "itemsSold": int(row.items),
        "buyers": int(row.buyers),
        "red": int(row.red),
        "bundles": int(row.bundle),
        "ultra": int(row.ultra),
        "mega": int(row.mega),
        "gifts": int(events.get(EventKind.gift, 0)),
        "noPicks": int(events.get(EventKind.no_pick, 0)),
        "cancelled": int(row.lost),
        "revenuePaidCents": int(row.paid),
        "revenuePendingCents": int(row.pending),
        "orders": {k.value: int(v) for k, v in orders.items()},
        "toFulfil": int(
            sum(
                v
                for k, v in orders.items()
                if k in (OrderStatus.paid, OrderStatus.picking, OrderStatus.packed)
            )
        ),
        "openReviews": int(open_reviews),
    }


def top_buyers(org_id: int, *, show_id: int | None = None, limit: int = 10) -> list[dict]:
    q = (
        select(
            Sale.buyer_username,
            func.sum(Sale.quantity),
            _rev(PAID),
            func.count(func.distinct(Sale.show_id)),
        )
        .where(Sale.organization_id == org_id, Sale.status.in_(STANDING))
        .group_by(Sale.buyer_username)
        .order_by(func.sum(Sale.quantity).desc(), Sale.buyer_username)
        .limit(limit)
    )
    if show_id:
        q = q.where(Sale.show_id == show_id)
    return [
        {"buyer": b, "items": int(n), "paidCents": int(r), "shows": int(s)}
        for b, n, r, s in db.session.execute(q).all()
    ]


def top_skus(org_id: int, *, show_id: int | None = None, limit: int = 10) -> list[dict]:
    q = (
        select(Sku, Product.name, func.sum(Sale.quantity), _rev(PAID))
        .join(Sale, Sale.sku_id == Sku.id)
        .join(Product, Product.id == Sku.product_id)
        .where(Sale.organization_id == org_id, Sale.status.in_(STANDING))
        .group_by(Sku.id, Product.name)
        .order_by(func.sum(Sale.quantity).desc(), Sku.code)
        .limit(limit)
    )
    if show_id:
        q = q.where(Sale.show_id == show_id)
    out = []
    for sku, pname, n, r in db.session.execute(q).all():
        sold = int(n)
        out.append(
            {
                "skuId": sku.id,
                "code": sku.code,
                "product": pname,
                "variant": sku.name,
                "sold": sold,
                "paidCents": int(r),
                "available": sku.available,
                "sellThrough": round(sold / (sold + sku.available), 3)
                if sold + sku.available
                else None,
            }
        )
    return out


def shows_table(
    org_id: int, *, limit: int = 30, since: date | None = None, until: date | None = None
) -> list[dict]:
    q = select(Show).where(Show.organization_id == org_id)
    if since:
        q = q.where(Show.show_date >= since)
    if until:
        q = q.where(Show.show_date <= until)
    shows = db.session.scalars(q.order_by(Show.started_at.desc()).limit(limit)).all()
    return [show_tiles(s) for s in shows]


def totals(rows: list[dict]) -> dict:
    keys = (
        "messages",
        "itemsSold",
        "buyers",
        "red",
        "bundles",
        "ultra",
        "mega",
        "gifts",
        "noPicks",
        "cancelled",
        "revenuePaidCents",
        "revenuePendingCents",
        "toFulfil",
        "openReviews",
    )
    return {k: sum(r[k] for r in rows) for k in keys} | {"shows": len(rows)}


def overview(org_id: int) -> dict:
    rows = shows_table(org_id)
    return {
        "shows": rows,
        "totals": totals(rows),
        "topBuyers": top_buyers(org_id),
        "topSkus": top_skus(org_id),
        "lowStock": [
            {"skuId": s.id, "code": s.code, "product": s.product.name, "available": s.available}
            for s in db.session.scalars(
                select(Sku)
                .where(
                    Sku.organization_id == org_id,
                    Sku.active.is_(True),
                    Sku.on_hand - Sku.reserved <= Sku.low_stock_at,
                )
                .order_by(Sku.code)
                .limit(20)
            )
        ],
    }
