"""Settings → Whatnot: connect the shop to its Whatnot seller account and see how it is doing."""

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import WhatnotShow
from ..services import audit, dashboard, ratelimit
from ..services.whatnot import WhatnotError, connection, results

bp = Blueprint("whatnot", __name__)

# Each of these calls Whatnot once.
CHECKS_PER_10_MIN = 20


@bp.errorhandler(WhatnotError)
def whatnot_error(e: WhatnotError):
    db.session.rollback()
    # A person reads this: the plain words only, never Whatnot's own text or a setting name.
    return jsonify({"error": e.words}), e.http_status


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
    rows = db.session.scalars(
        select(WhatnotShow)
        .where(
            WhatnotShow.organization_id == g.u.organization_id,
            WhatnotShow.status.in_(("live", "stopped", "scheduled")),
        )
        .order_by(WhatnotShow.scheduled_start_at.asc().nulls_last(), WhatnotShow.id)
        .limit(50)
    ).all()
    return jsonify(
        {
            "connected": True,
            "live": [serializers.whatnot_show(w) for w in rows if w.status != "scheduled"],
            "upcoming": [serializers.whatnot_show(w) for w in rows if w.status == "scheduled"],
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
