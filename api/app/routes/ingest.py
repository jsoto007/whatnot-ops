from datetime import UTC, datetime

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select

from ..db import db
from ..models import Show
from ..services import buyers, ratelimit, sale_mode, shipments, showdate, stock, text
from ..services.devices import device_for_key, touch
from ..services.ingest import BatchInvalid, ingest, message_count
from ..services.pipeline import schedule_parse
from .extension import latest_version as extension_version

bp = Blueprint("ingest", __name__)
WRONG_KEYS_PER_10_MIN = 30
DEVICE_CALLS_PER_MIN = 600


@bp.after_request
def cors(resp):
    """The extension's service worker calls cross-site; auth is the API key, never cookies."""
    origin = request.headers.get("Origin", "")
    if origin.startswith("chrome-extension://"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, X-Extension-Version"
        )
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
        # Wrong keys count per address, so keys cannot be guessed or sprayed without end.
        ratelimit.per_ip("device-key", WRONG_KEYS_PER_10_MIN, 600)
        return jsonify({"error": "invalid or revoked device key"}), 401
    # A laptop flushes about every 1.5 s, pings and polls stock: far below this. A leaked
    # key, or an extension stuck in a loop, cannot flood the database.
    ratelimit.per_device("ingest", device.id, DEVICE_CALLS_PER_MIN, 60)
    touch(device, request.headers.get("X-Extension-Version"))
    g.device = device
    return None


@bp.get("/ingest/ping")
def ping():
    db.session.commit()  # persists last_seen_at
    d = g.device
    return jsonify(
        {
            "ok": True,
            "device": d.name,
            "organization": d.organization.name,
            # The version this portal serves, so the extension's Settings can say it is behind.
            "latestExtension": extension_version(),
        }
    )


@bp.post("/ingest/disconnect")
def disconnect():
    """The extension's Disconnect button: the laptop revokes its own key."""
    from datetime import UTC, datetime

    from ..services import audit

    d = g.device
    d.revoked_at = datetime.now(UTC)
    audit.record(
        d.organization_id,
        "device.disconnected",
        target_type="device",
        target_id=d.id,
        details={"name": d.name, "by": "the laptop"},
    )
    db.session.commit()
    return ("", 204)


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
    schedule_parse(result.show)
    db.session.commit()
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


@bp.post("/ingest/shipments")
def post_shipments():
    """Rows the scraper read off Whatnot's Seller Hub Shipments table, after a show. Stored
    as evidence for the show's order slips; nothing about a sale or stock changes."""
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "body must be JSON"}), 400
    # A read is one click for a person; this only stops a loop.
    ratelimit.per_device("shipments", g.device.id, 30, 600)
    try:
        result = shipments.ingest(g.device, payload)
    except shipments.ShipmentsInvalid as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400
    db.session.commit()
    return jsonify(result)


@bp.get("/ingest/stock")
def running_low():
    """What is down to 3 or fewer, so the scraper panel can warn the host mid-show.
    `?show=<key>` narrows it to what that show has sold; without it (older extension
    builds) it is the whole shop. Read-only: stock moves only through the pipeline
    after a batch is parsed."""
    db.session.commit()  # persists last_seen_at
    show_key = request.args.get("show")
    if show_key is not None and (not 0 < len(show_key) <= 64 or text.has_hidden(show_key)):
        return jsonify({"error": "bad show key"}), 400
    return jsonify(
        {
            "alertAt": stock.RUNNING_LOW,
            "items": stock.running_low(g.device.organization_id, show_key=show_key),
            **_show_needs(show_key),
        }
    )


def _show_needs(show_key: str | None) -> dict:
    """What the show needs from the laptop, for the panel's own words: how it sells, and
    whether winners reach the app from Whatnot (then the laptop only records the chat, and
    the seller's own page is as good as any). Nothing for a show the app has not seen."""
    if show_key is None:
        return {}
    show = db.session.scalar(
        select(Show).where(
            Show.organization_id == g.device.organization_id, Show.show_key == show_key
        )
    )
    if show is None:
        return {}
    return {
        "show": {
            "saleMode": sale_mode.of_show(show)[0],
            "winnersFromWhatnot": sale_mode.winners_from_whatnot(show.organization_id),
        }
    }


@bp.post("/ingest/buyers")
def known_buyers():
    """Who just won, for the host: the team's flag, and whether they have bought before
    tonight (how many items, over how many shows). Asked with the winner's name as the
    video player shows it. Read-only; no note text, no money."""
    db.session.commit()  # persists last_seen_at
    d = request.get_json(silent=True)
    names = d.get("names") if isinstance(d, dict) else None
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return jsonify({"error": "names must be a list of usernames"}), 400
    if len(names) > buyers.MAX_LOOKUP:
        return jsonify({"error": "too many names"}), 400
    show_key = d.get("show")
    show = None
    if isinstance(show_key, str) and 0 < len(show_key) <= 64 and not text.has_hidden(show_key):
        show = (
            db.session.query(Show)
            .filter_by(organization_id=g.device.organization_id, show_key=show_key)
            .first()
        )
    # "Before tonight": the show's own date, or today's for a show whose first lines
    # are still on their way up.
    before = show.show_date if show is not None else showdate.show_date(datetime.now(UTC))
    found = buyers.badges(g.device.organization_id, names, before=before)
    out = []
    for n in names:
        name = buyers.name_of(n)
        if name.lower() in found:
            out.append({"name": name, **found[name.lower()]})
    return jsonify({"buyers": out})


@bp.get("/ingest/shows/<path:show_key>")
def show_status(show_key: str):
    db.session.commit()
    if len(show_key) > 64 or text.has_hidden(show_key):
        return jsonify({"error": "unknown show"}), 404
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
