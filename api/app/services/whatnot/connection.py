"""Which Whatnot seller a shop is, and whether calls to Whatnot are working.

The access token is the server's (`WHATNOT_API_TOKEN`, environment only). Connecting asks
Whatnot who that token belongs to and remembers the answer for the shop: from then on
webhooks for that seller reach this shop and the syncs run. Nothing here stores the token.
"""

import logging
from datetime import UTC, datetime

from flask import current_app
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

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


# The token works, but for another Whatnot account than the shop's. No read can end this
# (the reads of that other account succeed): only a check that finds the right account does.
WRONG_ACCOUNT = "wrong_account"


def _forget_rejections(org_id: int) -> None:
    """The token was just accepted: a read that failed because it was not is over too, and
    must not keep saying "cannot read Whatnot" until that kind of read happens to run (the
    daily ledger would hold it all night)."""
    db.session.execute(
        update(WhatnotSyncState)
        .where(
            WhatnotSyncState.organization_id == org_id,
            WhatnotSyncState.last_error.startswith("Unauthorized"),
        )
        .values(last_error=None)
    )


def note_ok(conn: WhatnotConnection) -> None:
    now = datetime.now(UTC)
    conn.last_checked_at = conn.last_ok_at = now
    conn.problem = conn.problem_at = None
    _forget_rejections(conn.organization_id)


def note_problem(
    conn: WhatnotConnection, e: WhatnotError | None = None, *, code: str | None = None
):
    """Remember why a check failed. A wrong account is never swapped for another problem:
    reads that work (for the other account) would then clear it."""
    now = datetime.now(UTC)
    conn.last_checked_at = now
    if conn.problem == WRONG_ACCOUNT:
        return
    code = code or _problem_code(e)
    if conn.problem != code:
        conn.problem, conn.problem_at = code, now


def note_failure(org_id: int, e: WhatnotError, resource: str | None = None) -> None:
    """A sync hit an error that people must act on (a rejected token, a missing
    permission): remember it so every page can say so. Its own small transaction, because
    the caller is about to roll its work back. `resource` (a kind of reading, as in
    `WhatnotSyncState`) is the one that failed: it is what later clears the problem, when a
    read of that kind works again (`settle`)."""
    if not isinstance(e, Unauthorized | Forbidden):
        return
    with db.engine.begin() as c:
        c.execute(
            update(WhatnotConnection)
            .where(WhatnotConnection.organization_id == org_id)
            .where(WhatnotConnection.problem.is_distinct_from(_problem_code(e)))
            .where(WhatnotConnection.problem.is_distinct_from(WRONG_ACCOUNT))
            .values(problem=_problem_code(e), problem_at=datetime.now(UTC))
        )
        if resource:
            why = f"{type(e).__name__} {e.problem}".strip()[:240]
            c.execute(
                insert(WhatnotSyncState)
                .values(organization_id=org_id, resource=resource, last_error=why)
                .on_conflict_do_update(
                    constraint="uq_whatnot_sync_org_resource", set_={"last_error": why}
                )
            )


# The readings whose missing permission flags the connection (a show's results, the fee
# ledger and the catalog swallow one), and the error a permission problem comes from.
FLAGGING = ("shows", "orders", "open_orders", "shipments")
_ERROR_OF = {"no_permission": "Forbidden"}


def settle(org_id: int, resource: str, previous_error: str | None, *, worked: bool = False) -> None:
    """A read of `resource` just worked. Only when it really had an answer from Whatnot
    (`worked`: some reads, say the refresh of open orders with none open, make no call at
    all and so show nothing). A rejected token is the whole token's, so any read
    that works shows it is not rejected: clear the problem and every old rejection the other
    readings still hold (a rarely run one would keep it for hours), so the banner goes and the
    orders Whatnot sends decide again. The same for "could not be reached". A missing
    permission is one kind of read's: it ends when a read of the kind that hit it works and no
    other flagging reading still hits it. A token that belongs to another account stays until a
    check finds the right one."""
    conn = active(org_id)
    if not worked or conn is None or conn.problem is None or conn.problem == WRONG_ACCOUNT:
        return
    if conn.problem in ("token_rejected", "unreachable"):
        conn.problem = conn.problem_at = None
        _forget_rejections(org_id)
        return
    kind = _ERROR_OF.get(conn.problem)
    if kind is None or not (previous_error or "").startswith(kind):
        return
    if resource not in FLAGGING:
        return  # a reading that never flags says nothing about one that did
    elsewhere = db.session.scalar(
        select(db.func.count())
        .select_from(WhatnotSyncState)
        .where(
            WhatnotSyncState.organization_id == org_id,
            WhatnotSyncState.resource != resource,
            WhatnotSyncState.last_error.startswith(kind),
            WhatnotSyncState.resource.in_(FLAGGING),
        )
    )
    if not elsewhere:
        conn.problem = conn.problem_at = None


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
        # The token was replaced with another account's: its reads would work, for the wrong
        # seller. Blind for this shop until a check finds the right account.
        note_problem(conn, code=WRONG_ACCOUNT)
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
