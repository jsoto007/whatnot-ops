from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import serializers
from ..auth import require
from ..db import db
from ..models import ChatMessage, ReviewItem, ReviewStatus, Sale, Show
from ..services import audit, pipeline, sale_mode, shelves
from ..services.catalog import CatalogError
from ..services.inventory import InventoryError
from ..services.permissions import allowed
from ..services.pipeline import ReviewError
from ..services.whatnot import catalog as whatnot_catalog

bp = Blueprint("review", __name__)


@bp.errorhandler(ReviewError)
@bp.errorhandler(CatalogError)
@bp.errorhandler(InventoryError)
def review_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


def _context(item: ReviewItem, around: int = 3) -> dict:
    show = db.session.get(Show, item.show_id) if item.show_id else None
    show_date = show.show_date.isoformat() if show else None
    lines = []
    if item.message is not None:
        lines = db.session.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.show_id == item.message.show_id,
                ChatMessage.seq.between(item.message.seq - around, item.message.seq + around),
            )
            .order_by(ChatMessage.seq)
        ).all()
    return {"show_date": show_date, "lines": lines}


@bp.get("/ai-status")
@require("review.act")
def ai_status():
    from ..services import supervisor

    return jsonify(supervisor.status(g.u.organization_id))


@bp.get("")
@require("review.act", heavy=True)
def list_review():
    status = request.args.get("status", "open")
    q = select(ReviewItem).where(ReviewItem.organization_id == g.u.organization_id)
    if status != "all":
        try:
            q = q.where(ReviewItem.status == ReviewStatus(status))
        except ValueError:
            return jsonify({"error": f"unknown status {status!r}"}), 400
    items = db.session.scalars(
        q.options(
            selectinload(ReviewItem.message), selectinload(ReviewItem.sale).selectinload(Sale.sku)
        )
        .order_by(ReviewItem.id)
        .limit(200)
    ).all()
    # A win waiting for its product: the shelf spot its winner typed, on a show laid out.
    waiting = [
        i.sale for i in items if i.reason == "unmatched_item" and i.sale and not i.sale.sku_id
    ]
    spots = shelves.suggest(waiting) if waiting else {}
    sale_mode.prime({i.show_id for i in items if i.sale is not None})
    whatnot_catalog.prime_listings([s for s in waiting if s.external_order_id])
    return jsonify(
        {"items": [serializers.review_item(i, _context(i), spots.get(i.sale_id)) for i in items]}
    )


@bp.post("/<int:item_id>/resolve")
@require("review.act")
def resolve(item_id: int):
    item = db.session.get(ReviewItem, item_id)
    if item is None or item.organization_id != g.u.organization_id:
        return jsonify({"error": "review item not found"}), 404
    body = request.get_json(silent=True) or {}
    action = body.get("action", "")
    # "Also use it for the other lots": `applyTo` names the review items the person was shown;
    # `applyToSimilar` alone means every similar open lot of the show.
    ids = body.get("applyTo")
    only = None
    if isinstance(ids, list):
        if not all(isinstance(i, int) for i in ids) or len(ids) > pipeline.SIMILAR_LIMIT:
            return jsonify({"error": "applyTo must be a short list of review item ids"}), 400
        only = set(ids)
    widen = action == "assign_sku" and (only is not None or bool(body.get("applyToSimilar")))
    # "Always use this product for this listing": only where each listing is one product
    # (in a winner's-pick show the listing is a "choose any" lot), and only for someone who
    # may link listings.
    link_listing = (
        action == "assign_sku"
        and bool(body.get("linkListing"))
        and item.sale is not None
        and allowed(g.u.role, "inventory.write")
        and sale_mode.of_show_id(item.sale.show_id) == sale_mode.LISTING
    )
    if (widen or link_listing) and item.show_id is not None:
        pipeline.lock_similar(item.show_id)
    pipeline.resolve(item, action, body, user=g.u)
    also: list[ReviewItem] = []
    listed = None
    if link_listing:
        listed = whatnot_catalog.link_sale_listing(item.sale, item.resolution["skuId"], user=g.u)
    if listed is not None:
        # The show's other wins of that listing are named now, not at the next orders read.
        for other in whatnot_catalog.waiting_for_listing(listed, item.show_id):
            if other.id == item.id:
                continue
            try:
                pipeline.resolve(
                    other,
                    "assign_sku",
                    {"skuId": item.resolution["skuId"], "remember": False},
                    user=g.u,
                )
            except ReviewError:
                continue  # settled a moment ago by someone else
            also.append(other)
    if widen:
        also += pipeline.resolve_similar(
            item,
            item.resolution["skuId"],
            remember=bool(body.get("remember")),
            user=g.u,
            only=only,
        )
    for r in (item, *also):
        audit.record(
            g.u.organization_id,
            "review.resolved",
            user=g.u,
            target_type="review",
            target_id=r.id,
            details={"reason": r.reason, **(r.resolution or {})},
        )
    db.session.commit()
    return jsonify(
        {
            "item": serializers.review_item(item, _context(item)),
            "alsoResolved": len(also),
            "listingLinked": listed is not None,
        }
    )
