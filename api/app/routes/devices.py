from datetime import UTC, datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import Device, DevicePairing
from ..services import audit
from ..services.devices import PairingError, create_device, start_pairing

bp = Blueprint("devices", __name__)


@bp.errorhandler(PairingError)
def pairing_error(e: PairingError):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


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


@bp.post("/pairings")
@require("devices.manage")
def create_pairing():
    """A short code for one laptop; the extension trades it for a key at /api/pair."""
    name = (request.get_json(silent=True) or {}).get("name") or ""
    pairing, code = start_pairing(g.u.organization, name, user=g.u)
    audit.record(
        g.u.organization_id,
        "device.pairing_started",
        user=g.u,
        target_type="pairing",
        target_id=pairing.id,
        details={"name": pairing.name},
    )
    db.session.commit()
    return jsonify(
        {
            "pairingId": pairing.id,
            "name": pairing.name,
            "code": code,
            "expiresAt": pairing.expires_at.isoformat(),
        }
    ), 201


@bp.get("/pairings/<int:pairing_id>")
@require("devices.manage")
def pairing_status(pairing_id: int):
    """The portal polls this while the code is on screen to say "Connected"."""
    p = db.session.get(DevicePairing, pairing_id)
    if p is None or p.organization_id != g.u.organization_id:
        return jsonify({"error": "pairing not found"}), 404
    if p.device_id is not None:
        status = "connected"
    elif p.expires_at <= datetime.now(UTC):
        status = "expired"
    else:
        status = "waiting"
    return jsonify(
        {
            "status": status,
            "expiresAt": p.expires_at.isoformat(),
            "device": serializers.device(p.device) if p.device else None,
        }
    )
