import uuid

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import serializers
from ..auth import require
from ..db import db
from ..models import Order, OrderStatus, ParsedEvent, ReviewItem, ReviewStatus, Sale, Show, Sku
from ..services import (
    audit,
    buyers,
    catalog,
    dashboard,
    order_slips,
    orders,
    pipeline,
    ratelimit,
    reconcile,
    sale_items,
    sale_mode,
)
from ..services.buyers import BuyerError
from ..services.inventory import Actor, InventoryError
from ..services.orders import OrderError
from ..services.reconcile import ReconcileError
from ..services.whatnot import shipping

bp = Blueprint("orders", __name__)
BUYER_HISTORY_LIMIT = 200  # newest orders; a regular buyer's full history is not one page
# The most orders one list answers with. Past it the oldest are left out (never the newest
# show) and the answer says so (`truncated`), so the page can tell people to pick a show.
ORDER_LIST_LIMIT = 500


@bp.errorhandler(BuyerError)
@bp.errorhandler(OrderError)
@bp.errorhandler(ReconcileError)
@bp.errorhandler(InventoryError)
def order_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


def _loaded():
    return select(Order).options(
        selectinload(Order.show),
        selectinload(Order.sales).selectinload(Sale.sku).selectinload(Sku.product),
        selectinload(Order.sales).selectinload(Sale.event).selectinload(ParsedEvent.message),
        selectinload(Order.sales).selectinload(Sale.show),
    )


# When a sale has more than one open decision, the row acts on the one a person can settle
# from the Orders page: the unknown item first, then a declined payment.
_REVIEW_RANK = {
    "unmatched_item": 0,
    "insufficient_stock": 1,
    "payment_declined": 2,
    "decline_seen": 3,  # the stream showed the card declined; Whatnot's order has not said yet
    "lot_won_twice": 4,  # the stream showed the lot won by two buyers
}


def _open_reviews(rows) -> dict[int, ReviewItem]:
    """sale id → the open review item waiting on it, for every sale of these orders (one query)."""
    sale_ids = [s.id for o in rows for s in o.sales]
    if not sale_ids:
        return {}
    items = db.session.scalars(
        select(ReviewItem)
        .where(ReviewItem.sale_id.in_(sale_ids), ReviewItem.status == ReviewStatus.open)
        .order_by(ReviewItem.id)
    ).all()
    best: dict[int, ReviewItem] = {}
    for r in items:
        held = best.get(r.sale_id)
        if held is None or _REVIEW_RANK.get(r.reason, 9) < _REVIEW_RANK.get(held.reason, 9):
            best[r.sale_id] = r
    return best


def _order_or_404(order_id: int) -> Order | None:
    o = db.session.scalar(_loaded().where(Order.id == order_id))
    return o if o and o.organization_id == g.u.organization_id else None


@bp.get("")
@require("orders.view", heavy=True)
def list_orders():
    q = _loaded().where(Order.organization_id == g.u.organization_id)
    status = request.args.get("status")
    if status == "queue":
        q = q.where(Order.status.in_((OrderStatus.paid, OrderStatus.picking, OrderStatus.packed)))
    elif status and status != "all":
        try:
            q = q.where(Order.status == OrderStatus(status))
        except ValueError:
            return jsonify({"error": f"unknown status {status!r}"}), 400
    if show_id := request.args.get("show", type=int):
        q = q.where(Order.show_id == show_id)
    if buyer := (request.args.get("buyer") or "").strip().lower():
        q = q.where(func.lower(Order.buyer_username) == buyer)
    # Work still to do first (the status enum runs open → paid → … → shipped → cancelled),
    # and the newest orders within each: when the list is cut, what falls off is the oldest
    # shipped and cancelled history, then the oldest waiting orders, never the latest show.
    rows = db.session.scalars(
        q.order_by(Order.status, Order.id.desc()).limit(ORDER_LIST_LIMIT + 1)
    ).all()
    truncated = len(rows) > ORDER_LIST_LIMIT
    rows = rows[:ORDER_LIST_LIMIT]
    with_sales = status != "all"
    reviews = _open_reviews(rows) if with_sales else {}
    if with_sales:
        sale_mode.prime({o.show_id for o in rows})  # each sale says how its show sells
    parcels = _parcels(rows)
    return jsonify(
        {
            "orders": [
                serializers.order(
                    o, with_sales=with_sales, reviews=reviews, parcel=parcels.get(o.id)
                )
                for o in rows
            ],
            "truncated": truncated,
        }
    )


def _parcels(orders: list[Order]) -> dict[int, dict]:
    """Where the parcel of each shipped order is, as Whatnot last said."""
    shipped = [o for o in orders if o.status is OrderStatus.shipped]
    return shipping.parcels_of(g.u.organization_id, shipped) if shipped else {}


def _one(o: Order) -> dict:
    """An order as its own page shows it."""
    return serializers.order(
        o, with_gifts=True, reviews=_open_reviews([o]), parcel=_parcels([o]).get(o.id)
    )


@bp.get("/<int:order_id>")
@require("orders.view")
def get_order(order_id: int):
    o = _order_or_404(order_id)
    if o is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify({"order": _one(o)})


@bp.post("/<int:order_id>/status")
@require("orders.fulfill")
def set_status(order_id: int):
    o = _order_or_404(order_id)
    if o is None:
        return jsonify({"error": "order not found"}), 404
    d = request.get_json(silent=True) or {}
    try:
        status = OrderStatus(d.get("status", ""))
    except ValueError:
        return jsonify({"error": "unknown status"}), 400
    orders.advance(
        o,
        status,
        actor_label=g.u.email,
        tracking=d.get("tracking"),
        packed_by=d.get("packedBy") if isinstance(d.get("packedBy"), str) else g.u.name,
    )
    if "notes" in d:
        o.notes = (d.get("notes") or "").strip() or None
    audit.record(
        g.u.organization_id,
        "order.status",
        user=g.u,
        target_type="order",
        target_id=o.id,
        details={"status": status.value},
    )
    db.session.commit()
    o = _order_or_404(o.id)
    return jsonify({"order": _one(o)})


@bp.patch("/<int:order_id>")
@require("orders.fulfill")
def update_order(order_id: int):
    o = _order_or_404(order_id)
    if o is None:
        return jsonify({"error": "order not found"}), 404
    d = request.get_json(silent=True) or {}
    before = {"notes": o.notes, "tracking": o.tracking, "packedBy": o.packed_by}
    if "notes" in d:
        v = d.get("notes")
        o.notes = (v.strip()[:2000] or None) if isinstance(v, str) else None
    if "tracking" in d:
        v = d.get("tracking")
        o.tracking = (v.strip()[:120] or None) if isinstance(v, str) else None
    if "packedBy" in d:
        v = d.get("packedBy")
        o.packed_by = (v.strip()[:80] or None) if isinstance(v, str) else None
    audit.record(
        g.u.organization_id,
        "order.updated",
        user=g.u,
        target_type="order",
        target_id=o.id,
        details={
            "before": before,
            "after": {"notes": o.notes, "tracking": o.tracking, "packedBy": o.packed_by},
        },
    )
    db.session.commit()
    return jsonify({"order": _one(o)})


@bp.get("/buyers/<path:username>")
@require("orders.view", heavy=True)
def buyer_history(username: str):
    # Every name the buyer has used: someone who renamed themselves has one history.
    names = buyers.names_for(g.u.organization_id, username)
    rows = db.session.scalars(
        _loaded()
        .join(Show, Show.id == Order.show_id)
        .where(
            Order.organization_id == g.u.organization_id,
            func.lower(Order.buyer_username).in_(names),
            dashboard.held_since(g.u.organization_id, Order.buyer_username, Show.show_date),
        )
        .order_by(Order.id.desc())
        .limit(BUYER_HISTORY_LIMIT)
    ).all()
    reviews = _open_reviews(rows)
    return jsonify(
        {"buyer": username, "orders": [serializers.order(o, reviews=reviews) for o in rows]}
    )


@bp.get("/shows/<int:show_id>/pick-list")
@require("orders.view", heavy=True)
def pick_list(show_id: int):
    show = db.session.get(Show, show_id)
    if show is None or show.organization_id != g.u.organization_id:
        return jsonify({"error": "show not found"}), 404
    return jsonify({"items": orders.pick_list(show)})


@bp.get("/shows/<int:show_id>/order-slips")
@require("orders.view", heavy=True)
def show_order_slips(show_id: int):
    show = db.session.get(Show, show_id)
    if show is None or show.organization_id != g.u.organization_id:
        return jsonify({"error": "show not found"}), 404
    only = request.args.get("only") or None
    if only is not None and only not in order_slips.FILTERS:
        return jsonify({"error": "unknown filter"}), 400
    return jsonify(order_slips.build(show, only=only))


@bp.patch("/sales/<int:sale_id>/gifts")
@require("orders.fulfill")
def set_sale_gifts(sale_id: int):
    """How many free gifts go in with one lot ("Gift N" on the order slips)."""
    sale = db.session.get(Sale, sale_id)
    if sale is None or sale.organization_id != g.u.organization_id:
        return jsonify({"error": "sale not found"}), 404
    d = request.get_json(silent=True) or {}
    try:
        before = order_slips.set_gift_count(sale, d.get("giftCount"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.record(
        g.u.organization_id,
        "sale.gifts",
        user=g.u,
        target_type="sale",
        target_id=sale.id,
        details={"before": before, "after": sale.gift_count},
    )
    db.session.commit()
    return jsonify({"saleId": sale.id, "giftCount": sale.gift_count})


@bp.patch("/sales/<int:sale_id>/product")
@require("review.act")
def change_sale_product(sale_id: int):
    """Give a standing sale another product: the old one goes back to stock, the new one
    leaves it. Once the order is being picked nothing is put back by itself and a note is
    left for the packer."""
    sale = db.session.get(Sale, sale_id)
    if sale is None or sale.organization_id != g.u.organization_id:
        return jsonify({"error": "sale not found"}), 404
    d = request.get_json(silent=True) or {}
    try:
        sku_id = int(d.get("skuId") or 0)
    except (TypeError, ValueError):
        sku_id = 0
    if not sku_id:
        return jsonify({"error": "Choose a product."}), 400
    try:
        sku = catalog.get_sku(g.u.organization_id, sku_id)
        result = sale_items.change(sale, sku, actor=Actor.user(g.u), reason="product changed")
    except (catalog.CatalogError, sale_items.ChangeError) as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), getattr(e, "status", 400)
    if result["changed"]:
        names = {
            i: n
            for i, n in db.session.execute(
                select(Sku.id, Sku.code).where(Sku.id.in_([result["from"], result["to"]]))
            )
        }
        if result["picked"]:
            pipeline.open_review(
                sale.organization_id,
                pipeline.SWAPPED_AFTER_PICKING,
                f"{sale.buyer_username}'s {sale.quantity} × {names[result['from']]} was changed "
                f"to {names[result['to']]} after its order was already being picked. Swap it "
                f"in the package, and count the {names[result['from']]} back in Inventory.",
                show_id=sale.show_id,
                sale_id=sale.id,
                dedupe=False,  # one note per change: each is another unit to count back
            )
        audit.record(
            g.u.organization_id,
            "sale.product_changed",
            user=g.u,
            target_type="sale",
            target_id=sale.id,
            details={
                "buyer": sale.buyer_username,
                "from": names[result["from"]],
                "to": names[result["to"]],
                "picked": result["picked"],
            },
        )
    db.session.commit()
    return jsonify({"sale": serializers.sale(sale), **result})


@bp.post("/shows/<int:show_id>/reconcile")
@require("orders.reconcile")
def reconcile_show(show_id: int):
    show = db.session.get(Show, show_id)
    if show is None or show.organization_id != g.u.organization_id:
        return jsonify({"error": "show not found"}), 404
    ratelimit.per_user("reconcile", g.u.id, 30, 600)
    d = request.get_json(silent=True) or {}
    text = d.get("csv") or ""
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "paste or upload the payment CSV"}), 400
    p = reconcile.plan(show, reconcile.parse_csv(text))
    if d.get("apply"):
        import_id = uuid.uuid4().hex[:12]
        reconcile.apply(show, p, actor=Actor.user(g.u), user=g.u, import_id=import_id)
        audit.record(
            g.u.organization_id,
            "payments.reconciled",
            user=g.u,
            target_type="show",
            target_id=show.id,
            details=p.summary,
        )
        db.session.commit()
    else:
        db.session.rollback()
    return jsonify(
        {
            "rows": [serializers.reconcile_row(r) for r in p.rows],
            "uncovered": p.uncovered,
            "summary": p.summary,
            "applied": bool(d.get("apply")),
        }
    )
