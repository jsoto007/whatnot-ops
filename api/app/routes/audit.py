from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import AuditLog, Device, Sku, User

bp = Blueprint("audit", __name__)

# target_type -> (model, the column the sentence names it by); one query per type per page
TARGET_NAMES = {"user": (User, User.email), "sku": (Sku, Sku.code), "device": (Device, Device.name)}


def _target_names(org_id: int, rows: list[AuditLog]) -> dict[tuple[str, str], str]:
    names: dict[tuple[str, str], str] = {}
    for kind, (model, column) in TARGET_NAMES.items():
        ids = {
            int(a.target_id)
            for a in rows
            if a.target_type == kind and (a.target_id or "").isdigit()
        }
        if not ids:
            continue
        found = db.session.execute(
            select(model.id, column).where(model.id.in_(ids), model.organization_id == org_id)
        ).all()
        names.update({(kind, str(i)): name for i, name in found})
    return names


@bp.get("")
@require("audit.view")
def list_audit():
    limit = max(1, min(request.args.get("limit", 100, type=int) or 100, 500))
    before = request.args.get("before", type=int)
    q = select(AuditLog).where(AuditLog.organization_id == g.u.organization_id)
    if before:
        q = q.where(AuditLog.id < before)
    rows = db.session.scalars(q.order_by(AuditLog.id.desc()).limit(limit)).all()
    names = _target_names(g.u.organization_id, rows)
    return jsonify(
        {
            "entries": [
                serializers.audit_entry(a, names.get((a.target_type or "", a.target_id or ""), ""))
                for a in rows
            ]
        }
    )
