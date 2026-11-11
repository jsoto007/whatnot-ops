"""Webhook deliveries from Whatnot (Standard Webhooks).

A delivery is trusted only after its signature checks out: an HMAC-SHA256, keyed with the
app's webhook secret, over `{webhook-id}.{webhook-timestamp}.{raw body}`. Then it is stored
once (retries send the same `webhook-id`) and handed to a job; the route answers at once,
because Whatnot waits five seconds and treats anything slower as a failure.

A delivery is a hint, not the truth. Whatnot sends each event at least once and in no
particular order, and leaves the buyer's details out of the body. So a handler never acts
on the body: it reads the order, shipment or show again from the API and applies that.
Only the ids an event names are kept.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from ...db import db
from ...models import WhatnotEvent
from .. import jobs
from . import connection
from .client import NotConfigured, NotConnected, Unauthorized, when

log = logging.getLogger(__name__)

SECRET_PREFIX = "wn_wh_"
TOLERANCE_SECONDS = 5 * 60
MAX_BYTES = 512 * 1024  # an order or a shipment is a few KB

Handler = Callable[[WhatnotEvent], None]
HANDLERS: dict[str, Handler] = {}


def on(*topics: str):
    """Register the function that acts on these topics (it gets the stored event)."""

    def deco(fn: Handler) -> Handler:
        for t in topics:
            HANDLERS[t] = fn
        return fn

    return deco


def _key(secret: str) -> bytes | None:
    """The signing key inside the dashboard's secret. Whatnot prefixes it with `wn_wh_`;
    left on, base64 would quietly decode a different key and every check would fail."""
    try:
        return base64.b64decode(secret.strip().removeprefix(SECRET_PREFIX), validate=True)
    except (binascii.Error, ValueError):
        return None


def sign(secret: str, webhook_id: str, timestamp: str, raw: bytes) -> str:
    key = _key(secret)
    if key is None:
        raise ValueError("the webhook secret is not valid base64")
    content = f"{webhook_id}.{timestamp}.".encode() + raw
    return base64.b64encode(hmac.digest(key, content, hashlib.sha256)).decode()


def verify(raw: bytes, headers, secret: str, *, now: float | None = None) -> bool:
    """True only for a body Whatnot signed, sent within five minutes of our clock."""
    webhook_id = headers.get("webhook-id") or ""
    timestamp = headers.get("webhook-timestamp") or ""
    signatures = headers.get("webhook-signature") or ""
    if not webhook_id or len(webhook_id) > 80 or not timestamp.isascii() or not timestamp.isdigit():
        return False
    if abs((time.time() if now is None else now) - int(timestamp)) > TOLERANCE_SECONDS:
        return False
    try:
        expected = sign(secret, webhook_id, timestamp, raw)
    except (ValueError, UnicodeEncodeError):
        return False
    ok = False
    for entry in signatures.split(" ")[:8]:
        version, _, signature = entry.partition(",")
        if version == "v1" and signature.isascii():
            # No early exit: every candidate is compared in constant time.
            ok = hmac.compare_digest(signature, expected) or ok
    return ok


def _id(value) -> str | None:
    """An id from a payload: a short string of letters, digits and dashes, or nothing."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not 0 < len(value) <= 40:
        return None
    return value if all(c.isalnum() or c == "-" for c in value) and value.isascii() else None


def _ref(topic: str, data) -> dict:
    """The ids an event names, by topic. Anything else in the body is dropped."""
    data = data if isinstance(data, dict) else {}
    out: dict[str, str] = {}

    def take(name: str, value) -> None:
        if (v := _id(value)) is not None:
            out[name] = v

    if topic.startswith("orders."):
        take("order_id", data.get("id"))
        take("show_id", data.get("show_id"))
    elif topic in ("shows.created", "shows.updated"):
        take("show_id", data.get("id"))
    elif topic.startswith("shows."):
        take("show_id", data.get("show_id"))
    elif topic == "shipments.replaced":
        previous, replacement = data.get("previous_shipment"), data.get("replacement_shipment")
        take("shipment_id", previous.get("id") if isinstance(previous, dict) else None)
        take("replaced_by", replacement.get("id") if isinstance(replacement, dict) else None)
    elif topic in ("shipments.created", "shipments.cancelled"):
        take("shipment_id", data.get("id"))
    elif topic.startswith("shipments."):
        take("shipment_id", data.get("shipment_id"))
    elif topic.startswith("merchandising."):
        take("product_id", data.get("product_id"))
        take("show_id", data.get("show_id"))
    elif topic.startswith("products."):
        take("product_id", data.get("id"))
    return out


def accept(body, webhook_id: str) -> str:
    """Store a verified delivery once and queue its handling.
    Returns "queued", "duplicate", "unknown_seller" or "malformed"."""
    if not isinstance(body, dict):
        return "malformed"
    topic, seller_id = body.get("type"), body.get("seller_id")
    if not isinstance(topic, str) or not topic or len(topic) > 64 or not topic.isascii():
        return "malformed"
    seller_id = _id(seller_id)
    conn = connection.by_seller(seller_id) if seller_id else None
    if conn is None:
        # Not a shop here (or it disconnected). Acknowledged all the same: a refusal would
        # make Whatnot retry for days and then unsubscribe the address.
        log.warning("whatnot webhook %s for a seller no shop is connected as", topic)
        return "unknown_seller"
    event_id = db.session.execute(
        insert(WhatnotEvent)
        .values(
            organization_id=conn.organization_id,
            webhook_id=webhook_id,
            topic=topic,
            ref=_ref(topic, body.get("data")),
            event_at=when(body.get("timestamp")),
            status="queued",
        )
        .on_conflict_do_nothing(index_elements=[WhatnotEvent.webhook_id])
        .returning(WhatnotEvent.id)
    ).scalar()
    if event_id is None:
        db.session.rollback()
        return "duplicate"
    conn.last_event_at = datetime.now(UTC)
    # The delivery and the job that will act on it are stored together: there is never a
    # delivery nobody will handle. (Without a worker the job runs here and now instead.)
    jobs.enqueue("whatnot_event", {"event_id": event_id}, org_id=conn.organization_id)
    db.session.commit()
    return "queued"


@jobs.handler("whatnot_event")
def handle_event(payload: dict) -> None:
    """Act on one stored delivery. An error that a retry cannot fix (the shop
    disconnected, the token was rejected) settles the event as failed; anything else
    raises, and the job queue tries again with its usual backoff."""
    event = db.session.get(WhatnotEvent, payload.get("event_id"))
    if event is None or event.status != "queued":
        return
    fn = HANDLERS.get(event.topic)
    if fn is None:
        event.status = "ignored"  # a topic nothing handles (yet): stored, not acted on
    else:
        try:
            fn(event)
            event.status = "done"
        except (NotConnected, NotConfigured, Unauthorized) as e:
            org_id = event.organization_id
            db.session.rollback()
            connection.note_failure(org_id, e)
            event = db.session.get(WhatnotEvent, payload.get("event_id"))
            event.status, event.error = "failed", type(e).__name__
    event.processed_at = datetime.now(UTC)


def body_of(raw: bytes):
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None
