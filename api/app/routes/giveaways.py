"""Giveaways to assign: Whatnot's giveaways waiting for a person to say which product each
was (services/giveaways.py)."""

from flask import Blueprint, g, jsonify, request

from ..auth import require
from ..db import db
from ..services import audit, catalog, giveaways
from ..services.giveaways import GiveawayError
from ..services.inventory import Actor, InventoryError

bp = Blueprint("giveaways", __name__)


@bp.errorhandler(GiveawayError)
@bp.errorhandler(InventoryError)
def giveaway_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), getattr(e, "status", 400)


@bp.get("")
@require("orders.view")
def list_giveaways():
    return jsonify(giveaways.listing(g.u.organization_id))


def _one(org_id: int, item_id: int) -> dict | None:
    return next((r for r in giveaways.listing(org_id)["giveaways"] if r["id"] == item_id), None)


@bp.put("/<int:item_id>/product")
@require("review.act")
def set_product(item_id: int):
    """Say which product a giveaway was: it leaves the shelf; an earlier choice goes back."""
    d = request.get_json(silent=True)
    sku_id = d.get("skuId") if isinstance(d, dict) else None
    # A whole number only: 1.9 or true would quietly name some other product.
    if not isinstance(sku_id, int) or isinstance(sku_id, bool) or not 0 < sku_id < 2**31:
        return jsonify({"error": "Choose a product."}), 400
    org_id = g.u.organization_id
    try:
        sku = catalog.get_sku(org_id, sku_id)
    except catalog.CatalogError:
        return jsonify({"error": "That product is not in use."}), 400
    result = giveaways.assign(org_id, item_id, sku, actor=Actor.user(g.u), user_id=g.u.id)
    row = _one(org_id, item_id)
    if result["changed"]:
        before = catalog.get_sku(org_id, result["from"]).code if result["from"] else None
        audit.record(
            org_id,
            "giveaway.product_set",
            user=g.u,
            target_type="giveaway",
            target_id=item_id,
            details={
                "buyer": row["buyer"] if row else "",
                "product": sku.code,
                "from": before,
                "quantity": result["quantity"],
            },
        )
    db.session.commit()
    return jsonify({"giveaway": row, **result})


@bp.delete("/<int:item_id>/product")
@require("review.act")
def clear_product(item_id: int):
    """Take the product off a giveaway: it goes back on the shelf and the giveaway waits."""
    org_id = g.u.organization_id
    before = _one(org_id, item_id)
    result = giveaways.clear(org_id, item_id, actor=Actor.user(g.u))
    if result["changed"]:
        audit.record(
            org_id,
            "giveaway.product_cleared",
            user=g.u,
            target_type="giveaway",
            target_id=item_id,
            details={
                "buyer": before["buyer"] if before else "",
                "product": catalog.get_sku(org_id, result["from"]).code,
                "quantity": result["quantity"],
            },
        )
    db.session.commit()
    return jsonify({"giveaway": _one(org_id, item_id), **result})
