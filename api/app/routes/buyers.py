"""Buyers: who bought, how often, what fell through, and what the team says about them."""

from flask import Blueprint, g, jsonify, request
from sqlalchemy import func

from .. import serializers
from ..auth import require
from ..db import db
from ..models import Order, Show
from ..services import buyers, dashboard
from ..services.buyers import BuyerError
from .orders import BUYER_HISTORY_LIMIT, _loaded

bp = Blueprint("buyers", __name__)


@bp.errorhandler(BuyerError)
def buyer_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


@bp.get("")
@require("buyers.view", heavy=True)
def list_buyers():
    flag = request.args.get("flag") or None
    if flag is not None and flag not in (*buyers.FLAGS, "noted"):
        return jsonify({"error": "unknown filter"}), 400
    sort = request.args.get("sort") or "spent"
    if sort not in buyers.SORTS:
        return jsonify({"error": "unknown order"}), 400
    return jsonify(
        buyers.directory(
            g.u.organization_id,
            q=request.args.get("q") or "",
            flag=flag,
            sort=sort,
            offset=request.args.get("offset", default=0, type=int) or 0,
        )
    )


@bp.get("/flags")
@require("buyers.view")
def buyer_flags():
    """Every flagged or noted buyer's names: small, and asked for by every page that
    shows a buyer."""
    return jsonify({"flags": buyers.flags(g.u.organization_id)})


@bp.get("/by-name/<path:username>")
@require("buyers.view", heavy=True)
def get_buyer(username: str):
    data, lows = buyers.profile(g.u.organization_id, username)
    rows = db.session.scalars(
        _loaded()
        .join(Show, Show.id == Order.show_id)
        .where(
            Order.organization_id == g.u.organization_id,
            func.lower(Order.buyer_username).in_(lows),
            # Not what an earlier account won under a name this buyer took over.
            dashboard.held_since(g.u.organization_id, Order.buyer_username, Show.show_date),
        )
        .order_by(Order.id.desc())
        .limit(BUYER_HISTORY_LIMIT)
    ).all()
    return jsonify(
        {"buyer": data, "orders": [serializers.order(o, with_sales=False) for o in rows]}
    )


@bp.patch("/by-name/<path:username>")
@require("buyers.manage")
def update_buyer(username: str):
    d = request.get_json(silent=True) or {}
    changes = {}
    if "flag" in d:
        changes["flag"] = None if d["flag"] == "" else d["flag"]  # anything else odd is refused
    if "note" in d:
        changes["note"] = d["note"]
    if not changes:
        return jsonify({"error": "nothing to change"}), 400
    buyers.annotate(g.u.organization_id, username, user=g.u, **changes)
    db.session.commit()
    data, _ = buyers.profile(g.u.organization_id, username)
    return jsonify({"buyer": data})
