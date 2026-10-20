from flask import Blueprint, g, jsonify, request

from ..db import db
from ..models import Show
from ..services.devices import device_for_key, touch
from ..services.ingest import BatchInvalid, ingest, message_count

bp = Blueprint("ingest", __name__)


@bp.after_request
def cors(resp):
    """The extension's service worker calls cross-site; auth is the API key, never cookies."""
    origin = request.headers.get("Origin", "")
    if origin.startswith("chrome-extension://"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Max-Age"] = "600"
        resp.headers["Vary"] = "Origin"
    return resp


@bp.before_request
def auth():
    if request.method == "OPTIONS":
        return ("", 204)
    header = request.headers.get("Authorization", "")
    key = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else None
    device = device_for_key(key)
    if device is None:
        return jsonify({"error": "invalid or revoked device key"}), 401
    touch(device)
    g.device = device
    return None


@bp.get("/ingest/ping")
def ping():
    db.session.commit()  # persists last_seen_at
    d = g.device
    return jsonify({"ok": True, "device": d.name, "organization": d.organization.name})


@bp.post("/ingest")
def post_batch():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "body must be JSON"}), 400
    try:
        result = ingest(g.device, payload)
    except BatchInvalid as e:
        db.session.rollback()
        return jsonify({"error": "invalid batch", "details": e.errors}), 400
    return jsonify(
        {
            "accepted": result.accepted,
            "duplicates": result.duplicates,
            "show": {
                "id": result.show.id,
                "key": result.show.show_key,
                "messageCount": result.message_count,
            },
        }
    )


@bp.get("/ingest/shows/<path:show_key>")
def show_status(show_key: str):
    db.session.commit()
    show = (
        db.session.query(Show)
        .filter_by(organization_id=g.device.organization_id, show_key=show_key)
        .first()
    )
    if show is None:
        return jsonify({"error": "unknown show"}), 404
    return jsonify(
        {
            "id": show.id,
            "key": show.show_key,
            "date": show.show_date.isoformat(),
            "messageCount": message_count(show),
        }
    )
