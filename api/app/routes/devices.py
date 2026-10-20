from datetime import UTC, datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import Device
from ..services import audit
from ..services.devices import create_device

bp = Blueprint("devices", __name__)


@bp.get("")
@require("devices.manage")
def list_devices():
    rows = db.session.scalars(
        select(Device)
        .where(Device.organization_id == g.u.organization_id)
        .order_by(Device.created_at)
    ).all()
    return jsonify({"devices": [serializers.device(d) for d in rows]})


@bp.post("")
@require("devices.manage")
def create():
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"error": "give the laptop a name"}), 400
    device, key = create_device(g.u.organization, name)
    audit.record(
        g.u.organization_id,
        "device.created",
        user=g.u,
        target_type="device",
        target_id=device.id,
        details={"name": name},
    )
    db.session.commit()
    # The key is returned exactly once.
    return jsonify({"device": serializers.device(device), "key": key}), 201


@bp.delete("/<int:device_id>")
@require("devices.manage")
def revoke(device_id: int):
    device = db.session.get(Device, device_id)
    if device is None or device.organization_id != g.u.organization_id:
        return jsonify({"error": "device not found"}), 404
    if device.revoked_at is None:
        device.revoked_at = datetime.now(UTC)
        audit.record(
            g.u.organization_id,
            "device.revoked",
            user=g.u,
            target_type="device",
            target_id=device.id,
        )
        db.session.commit()
    return ("", 204)
