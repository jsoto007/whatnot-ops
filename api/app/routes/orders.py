import uuid

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import serializers
from ..auth import require
from ..db import db
from ..models import Order, OrderStatus, ParsedEvent, ReviewItem, ReviewStatus, Sale, Show
from ..services import audit, order_slips, orders, ratelimit, reconcile
from ..services.inventory import Actor, InventoryError
from ..services.orders import OrderError
from ..services.reconcile import ReconcileError

bp = Blueprint("orders", __name__)
BUYER_HISTORY_LIMIT = 200  # newest orders; a regular buyer's full history is not one page


@bp.errorhandler(OrderError)
@bp.errorhandler(ReconcileError)
@bp.errorhandler(InventoryError)
def order_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


def _loaded():
    return select(Order).options(
        selectinload(Order.show),
        selectinload(Order.sales).selectinload(Sale.sku),
        selectinload(Order.sales).selectinload(Sale.event).selectinload(ParsedEvent.message),
        selectinload(Order.sales).selectinload(Sale.show),
    )


# When a sale has more than one open decision, the row acts on the one a person can settle
# from the Orders page: the unknown item first, then a declined payment.
_REVIEW_RANK = {"unmatched_item": 0, "insufficient_stock": 1, "payment_declined": 2}


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
    rows = db.session.scalars(q.order_by(Order.status, Order.id).limit(500)).all()
    with_sales = status != "all"
    reviews = _open_reviews(rows) if with_sales else {}
    return jsonify(
        {"orders": [serializers.order(o, with_sales=with_sales, reviews=reviews) for o in rows]}
    )


@bp.get("/<int:order_id>")
@require("orders.view")
def get_order(order_id: int):
    o = _order_or_404(order_id)
    if o is None:
        return jsonify({"error": "order not found"}), 404
    return jsonify({"order": serializers.order(o, with_gifts=True, reviews=_open_reviews([o]))})


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
    return jsonify({"order": serializers.order(o, with_gifts=True, reviews=_open_reviews([o]))})


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
    return jsonify({"order": serializers.order(o, with_gifts=True, reviews=_open_reviews([o]))})


@bp.get("/buyers/<path:username>")
@require("orders.view", heavy=True)
def buyer_history(username: str):
    rows = db.session.scalars(
        _loaded()
        .where(
            Order.organization_id == g.u.organization_id,
            func.lower(Order.buyer_username) == username.lower(),
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
