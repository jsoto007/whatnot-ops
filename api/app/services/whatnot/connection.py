"""Which Whatnot seller a shop is, and whether calls to Whatnot are working.

The access token is the server's (`WHATNOT_API_TOKEN`, environment only). Connecting asks
Whatnot who that token belongs to and remembers the answer for the shop: from then on
webhooks for that seller reach this shop and the syncs run. Nothing here stores the token.
"""

import logging
from datetime import UTC, datetime

from flask import current_app
from sqlalchemy import select

from ...db import db
from ...models import Organization, User, WhatnotConnection, WhatnotEvent, WhatnotSyncState
from .. import audit, text
from .client import (
    Forbidden,
    NotConfigured,
    Unauthorized,
    Unavailable,
    WhatnotError,
    make_client,
)
from .throttle import acquire

log = logging.getLogger(__name__)


class ConnectionError(Exception):  # noqa: A001 - a route error, in the shop's words
    status = 409


def configured() -> bool:
    return bool(current_app.config.get("WHATNOT_API_TOKEN"))


def webhooks_configured() -> bool:
    return bool(current_app.config.get("WHATNOT_WEBHOOK_SECRET"))


def get(org_id: int) -> WhatnotConnection | None:
    return db.session.scalar(
        select(WhatnotConnection).where(WhatnotConnection.organization_id == org_id)
    )


def active(org_id: int) -> WhatnotConnection | None:
    conn = get(org_id)
    return conn if conn is not None and conn.active else None


def by_seller(seller_id: str) -> WhatnotConnection | None:
    conn = db.session.scalar(
        select(WhatnotConnection).where(WhatnotConnection.seller_id == seller_id)
    )
    return conn if conn is not None and conn.active else None


def _problem_code(e: WhatnotError) -> str:
    if isinstance(e, Unauthorized):
        return "token_rejected"
    if isinstance(e, Forbidden):
        return "no_permission"
    return "unreachable"


def note_ok(conn: WhatnotConnection) -> None:
    now = datetime.now(UTC)
    conn.last_checked_at = conn.last_ok_at = now
    conn.problem = conn.problem_at = None


def note_problem(conn: WhatnotConnection, e: WhatnotError) -> None:
    now = datetime.now(UTC)
    conn.last_checked_at = now
    code = _problem_code(e)
    if conn.problem != code:
        conn.problem, conn.problem_at = code, now


def note_failure(org_id: int, e: WhatnotError) -> None:
    """A sync hit an error that people must act on (a rejected token, a missing
    permission): remember it so every page can say so. Its own small transaction, because
    the caller is about to roll its work back."""
    if not isinstance(e, Unauthorized | Forbidden):
        return
    with db.engine.begin() as c:
        from sqlalchemy import update

        c.execute(
            update(WhatnotConnection)
            .where(WhatnotConnection.organization_id == org_id)
            .where(WhatnotConnection.problem.is_distinct_from(_problem_code(e)))
            .values(problem=_problem_code(e), problem_at=datetime.now(UTC))
        )


def _who(org_id: int) -> dict:
    """Ask Whatnot who the server's token belongs to."""
    client = make_client(throttle=lambda: acquire(org_id))
    me = client.me()
    seller_id = me.get("id")
    if not isinstance(seller_id, str | int) or not str(seller_id).strip():
        raise Unavailable("Whatnot answered /users/me without an id")
    return {
        "seller_id": str(seller_id).strip()[:32],
        "username": (text.clean(str(me.get("username") or "")) or "")[:80],
        "currency": (str(me.get("seller_currency") or "USD"))[:3].upper(),
    }


def connect(org: Organization, user: User) -> WhatnotConnection:
    """Bind the shop to the seller the server's token belongs to."""
    if not configured():
        raise NotConfigured()
    who = _who(org.id)
    conn = get(org.id)
    taken = db.session.scalar(
        select(WhatnotConnection).where(
            WhatnotConnection.seller_id == who["seller_id"],
            WhatnotConnection.organization_id != org.id,
        )
    )
    if taken is not None:
        raise ConnectionError("That Whatnot account is already connected to another shop here.")
    if conn is not None and conn.seller_id != who["seller_id"]:
        # Orders and shipments already read belong to the first account; mixing two
        # sellers' numbers in one shop is never what anyone wants.
        raise ConnectionError(
            f"This shop was connected to @{conn.username} before, and the access token now "
            f"belongs to @{who['username']}. Ask your developer to switch accounts."
        )
    now = datetime.now(UTC)
    if conn is None:
        conn = WhatnotConnection(organization_id=org.id, seller_id=who["seller_id"])
        db.session.add(conn)
    conn.username, conn.currency = who["username"], who["currency"]
    conn.connected_by_id, conn.connected_at, conn.disconnected_at = user.id, now, None
    note_ok(conn)
    filled_host = False
    if not org.host_username and who["username"]:
        # The seller's own handle: their chat lines are the host's confirmations.
        org.host_username = who["username"]
        filled_host = True
    audit.record(
        org.id,
        "whatnot.connected",
        user=user,
        target_type="organization",
        target_id=org.id,
        details={"username": who["username"], "hostUsernameSet": filled_host},
    )
    db.session.flush()
    # The first read (shows, any unsettled orders) right away rather than at the next timer.
    from .. import jobs

    jobs.enqueue(
        "whatnot_sync", {"org_id": org.id}, org_id=org.id, dedupe_key=f"whatnot_sync:{org.id}"
    )
    return conn


def disconnect(org: Organization, user: User) -> None:
    conn = active(org.id)
    if conn is None:
        return
    conn.disconnected_at = datetime.now(UTC)
    audit.record(
        org.id,
        "whatnot.disconnected",
        user=user,
        target_type="organization",
        target_id=org.id,
        details={"username": conn.username},
    )


def check(org_id: int) -> WhatnotConnection | None:
    """Call Whatnot once and record whether it worked."""
    conn = active(org_id)
    if conn is None:
        return None
    try:
        who = _who(org_id)
    except NotConfigured:
        raise
    except WhatnotError as e:
        note_problem(conn, e)
        return conn
    if who["seller_id"] != conn.seller_id:
        # The token was replaced with another account's: treat it as rejected for this shop.
        note_problem(conn, Unauthorized())
        return conn
    conn.username, conn.currency = who["username"] or conn.username, who["currency"]
    note_ok(conn)
    return conn


def webhook_url() -> str | None:
    """Where Whatnot should deliver events: what the owner pastes in the developer
    dashboard. None when the server does not know its own public address."""
    import os

    base = os.environ.get("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    return f"{base.rstrip('/')}/api/webhooks/whatnot" if base else None


def sync_state(org_id: int, resource: str) -> WhatnotSyncState:
    """The shop's bookkeeping row for one kind of reading, made on first use."""
    row = db.session.scalar(
        select(WhatnotSyncState).where(
            WhatnotSyncState.organization_id == org_id, WhatnotSyncState.resource == resource
        )
    )
    if row is None:
        row = WhatnotSyncState(organization_id=org_id, resource=resource)
        db.session.add(row)
        db.session.flush()
    return row


def status(org_id: int) -> dict:
    """What Settings shows. No secret, no setting name, no vendor text."""
    conn = get(org_id)
    connected = conn is not None and conn.active
    out = {
        "configured": configured(),
        "connected": connected,
        "username": conn.username if conn is not None else None,
        "connectedAt": conn.connected_at.isoformat() if connected else None,
        "lastCheckedAt": conn.last_checked_at.isoformat()
        if connected and conn.last_checked_at
        else None,
        "problem": conn.problem if connected else None,
        "ordersMode": conn.orders_mode if connected else "shadow",
        "webhooks": {
            "configured": webhooks_configured(),
            "url": webhook_url(),
            "lastEventAt": conn.last_event_at.isoformat()
            if connected and conn.last_event_at
            else None,
        },
        "sync": [],
    }
    if connected:
        rows = db.session.scalars(
            select(WhatnotSyncState)
            # "shipments:<show>" rows are per-show bookkeeping, not a kind of reading.
            .where(
                WhatnotSyncState.organization_id == org_id, ~WhatnotSyncState.resource.contains(":")
            )
            .order_by(WhatnotSyncState.resource)
        ).all()
        out["sync"] = [
            {
                "resource": r.resource,
                "lastOkAt": r.last_ok_at.isoformat() if r.last_ok_at else None,
                "failing": bool(r.last_error),
            }
            for r in rows
        ]
        waiting = db.session.scalar(
            select(db.func.count())
            .select_from(WhatnotEvent)
            .where(WhatnotEvent.organization_id == org_id, WhatnotEvent.status == "queued")
        )
        out["webhooks"]["waiting"] = waiting or 0
    return out
