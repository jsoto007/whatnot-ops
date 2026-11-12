"""Settings → Whatnot: connect the shop to its Whatnot seller account and see how it is doing."""

from datetime import UTC, datetime, timedelta

from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_, select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import WhatnotShow
from ..services import audit, dashboard, ratelimit
from ..services.whatnot import WhatnotError, catalog, connection, results

bp = Blueprint("whatnot", __name__)

# Each of these calls Whatnot once.
CHECKS_PER_10_MIN = 20
# The Shows page lists what is coming next on Whatnot, not the seller's whole calendar.
UPCOMING_SHOWN = 5
UPCOMING_LATE = timedelta(hours=3)  # a show still "scheduled" this long after its time is stale


@bp.errorhandler(WhatnotError)
def whatnot_error(e: WhatnotError):
    db.session.rollback()
    # A person reads this: the plain words only, never Whatnot's own text or a setting name.
    return jsonify({"error": e.words}), e.http_status


@bp.errorhandler(catalog.CatalogError)
def catalog_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


@bp.errorhandler(connection.ConnectionError)
def connection_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


@bp.get("")
@require("whatnot.view")
def get_status():
    return jsonify(connection.status(g.u.organization_id))


@bp.post("/connect")
@require("whatnot.manage")
def connect():
    ratelimit.per_user("whatnot-check", g.u.id, CHECKS_PER_10_MIN, 600)
    connection.connect(g.u.organization, g.u)
    db.session.commit()
    return jsonify(connection.status(g.u.organization_id))


@bp.post("/disconnect")
@require("whatnot.manage")
def disconnect():
    connection.disconnect(g.u.organization, g.u)
    db.session.commit()
    return jsonify(connection.status(g.u.organization_id))


@bp.post("/check")
@require("whatnot.view")
def check():
    ratelimit.per_user("whatnot-check", g.u.id, CHECKS_PER_10_MIN, 600)
    connection.check(g.u.organization_id)
    db.session.commit()
    return jsonify(connection.status(g.u.organization_id))


@bp.patch("/settings")
@require("whatnot.manage")
def settings():
    """ordersMode: "shadow" (compare; a person applies) or "auto" (apply as they arrive)."""
    conn = connection.active(g.u.organization_id)
    if conn is None:
        return jsonify({"error": "Connect the shop to Whatnot first."}), 409
    mode = (request.get_json(silent=True) or {}).get("ordersMode")
    if mode not in ("shadow", "auto"):
        return jsonify({"error": "choose how Whatnot's orders are used"}), 400
    if conn.orders_mode != mode:
        conn.orders_mode = mode
        # From now on: a show that was over before this is left to a person.
        conn.auto_since = datetime.now(UTC) if mode == "auto" else None
        audit.record(
            g.u.organization_id,
            "whatnot.mode",
            user=g.u,
            target_type="organization",
            target_id=g.u.organization_id,
            details={"mode": mode},
        )
    db.session.commit()
    return jsonify(connection.status(g.u.organization_id))


@bp.get("/shows")
@require("shows.view")
def shows():
    """What is live on Whatnot now and what is scheduled next."""
    if connection.active(g.u.organization_id) is None:
        return jsonify({"connected": False, "live": [], "upcoming": []})
    mine = WhatnotShow.organization_id == g.u.organization_id
    live = db.session.scalars(
        select(WhatnotShow)
        .where(mine, WhatnotShow.status.in_(("live", "stopped")))
        .order_by(WhatnotShow.scheduled_start_at.asc().nulls_last(), WhatnotShow.id)
        .limit(10)
    ).all()
    # A seller's list holds dozens of placeholders, many left "scheduled" long after their
    # date: only the next few that are still to come (or due within the last hours).
    upcoming = db.session.scalars(
        select(WhatnotShow)
        .where(
            mine,
            WhatnotShow.status == "scheduled",
            or_(
                WhatnotShow.scheduled_start_at >= datetime.now(UTC) - UPCOMING_LATE,
                WhatnotShow.scheduled_start_at.is_(None),  # no time set yet: listed last
            ),
        )
        .order_by(WhatnotShow.scheduled_start_at.asc().nulls_last(), WhatnotShow.id)
        .limit(UPCOMING_SHOWN)
    ).all()
    return jsonify(
        {
            "connected": True,
            "live": [serializers.whatnot_show(w) for w in live],
            "upcoming": [serializers.whatnot_show(w) for w in upcoming],
        }
    )


# A press asks Whatnot to build a report only when none is being built; otherwise it
# just looks whether the one in progress is ready.
LEDGER_READS_PER_HOUR = 30


@bp.get("/ledger")
@require("reports.view", heavy=True)
def ledger():
    """Whatnot's financial ledger as the app holds it: fees, refunds, tips, net per show."""
    org_id = g.u.organization_id
    if connection.active(org_id) is None:
        return jsonify({"connected": False})
    return jsonify({"connected": True, **dashboard.whatnot_ledger(org_id)})


@bp.post("/ledger/read")
@require("whatnot.view")
def read_ledger():
    """Ask Whatnot for the ledger now (it builds the file in the background)."""
    org_id = g.u.organization_id
    if connection.active(org_id) is None:
        return jsonify({"error": "Connect the shop to Whatnot first."}), 409
    ratelimit.per_org("whatnot-ledger", org_id, LEDGER_READS_PER_HOUR, 3600)
    results.schedule_ledger(org_id, user_id=g.u.id)
    db.session.commit()
    return jsonify({"connected": True, **dashboard.whatnot_ledger(org_id)})


CATALOG_READS_PER_HOUR = 12  # each reads the shop's whole Whatnot catalog


@bp.get("/catalog")
@require("inventory.view", heavy=True)
def catalog_page():
    """The shop's Whatnot listings next to its own products."""
    return jsonify(catalog.listings(g.u.organization_id))


@bp.post("/catalog/read")
@require("inventory.write")
def catalog_read():
    """Read the catalog from Whatnot now (queued; the page asks again shortly)."""
    if connection.active(g.u.organization_id) is None:
        return jsonify({"error": "Connect the shop to Whatnot first."}), 409
    ratelimit.per_org("whatnot-catalog", g.u.organization_id, CATALOG_READS_PER_HOUR, 3600)
    catalog.schedule(g.u.organization_id)
    db.session.commit()
    return jsonify(catalog.listings(g.u.organization_id))


@bp.patch("/catalog/<int:variant_id>/link")
@require("inventory.write")
def catalog_link(variant_id: int):
    """Say which product a listing is ({"skuId": 12}), or that it is none of ours (null)."""
    d = request.get_json(silent=True)
    sku_id = d.get("skuId") if isinstance(d, dict) else None
    if sku_id is not None and (isinstance(sku_id, bool) or not isinstance(sku_id, int)):
        return jsonify({"error": "pick a product"}), 400
    catalog.link(g.u.organization_id, variant_id, sku_id, user=g.u)
    db.session.commit()
    return jsonify(catalog.listings(g.u.organization_id))
