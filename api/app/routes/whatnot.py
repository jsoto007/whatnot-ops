"""Settings → Whatnot: connect the shop to its Whatnot seller account and see how it is doing."""

from flask import Blueprint, g, jsonify

from ..auth import require
from ..db import db
from ..services import ratelimit
from ..services.whatnot import WhatnotError, connection

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
