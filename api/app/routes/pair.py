"""POST /api/pair: the extension trades a pairing code for its device key.

No session and no cookies: the code itself is the credential, it is single-use
and short-lived (services/devices.py). Cross-origin calls are allowed only from
Chrome extensions, like /api/ingest.
"""

from flask import Blueprint, jsonify, request

from ..db import db
from ..services import audit
from ..services.devices import PairingError, redeem_pairing

bp = Blueprint("pair", __name__)


@bp.after_request
def cors(resp):
    origin = request.headers.get("Origin", "")
    if origin.startswith("chrome-extension://"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Max-Age"] = "600"
        resp.headers["Vary"] = "Origin"
    return resp


@bp.route("/pair", methods=["POST", "OPTIONS"])
def pair():
    if request.method == "OPTIONS":
        return ("", 204)
    if not request.is_json:
        return jsonify({"error": "body must be JSON"}), 400
    try:
        pairing, device, key = redeem_pairing((request.get_json(silent=True) or {}).get("code"))
    except PairingError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), e.status
    audit.record(
        pairing.organization_id,
        "device.paired",
        target_type="device",
        target_id=device.id,
        details={"name": device.name, "pairingId": pairing.id},
    )
    db.session.commit()
    # The key is returned exactly once, to the laptop that redeemed the code.
    return jsonify(
        {"key": key, "device": device.name, "organization": device.organization.name}
    ), 201
