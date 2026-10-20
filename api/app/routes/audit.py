from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import AuditLog

bp = Blueprint("audit", __name__)


@bp.get("")
@require("audit.view")
def list_audit():
    limit = min(int(request.args.get("limit", 100)), 500)
    before = request.args.get("before", type=int)
    q = select(AuditLog).where(AuditLog.organization_id == g.u.organization_id)
    if before:
        q = q.where(AuditLog.id < before)
    rows = db.session.scalars(q.order_by(AuditLog.id.desc()).limit(limit)).all()
    return jsonify({"entries": [serializers.audit_entry(a) for a in rows]})
