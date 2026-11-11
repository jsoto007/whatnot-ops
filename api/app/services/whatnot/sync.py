"""Keep the app's copy of Whatnot's shows and orders current.

Three ways news arrives, all ending in the same upserts:
  * a webhook names an order or a show, and the job reads it again from the API;
  * when a show ends, every order placed since it began is read (`sync_show_orders`):
    the catch-up that makes a missed webhook harmless;
  * a timer (the worker's sweep) reads the show list, re-reads orders Whatnot has not
    settled yet, and polls a live show's orders when no webhook has come for a while.

Reading never changes a sale, an order of the app, a payment or stock. What the copy is
then allowed to do is decided in orders.py (compare, or apply).

List endpoints have no filter by show or date: "this show's orders" means paging back,
newest first, until the orders are older than the show.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ...db import db
from ...models import (
    ChatMessage,
    Show,
    WhatnotConnection,
    WhatnotEvent,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShow,
)
from .. import jobs, text
from ..showdate import show_date
from . import connection, webhooks
from .client import NotFound, WhatnotClient, WhatnotError, cents, client_for, when

log = logging.getLogger(__name__)

ACTIVE = ("live", "stopped")
OPEN_ORDER_STATUSES = ("pending", "pending_fulfillment")
# How far before a show's start an order can still be its own (clock skew, a show that
# went live before its scheduled time).
SHOW_LEAD = timedelta(hours=6)
# A recording is of a show when it was running while the show was live. Before the show's
# known start a laptop is often already recording: half an hour when we know the moment
# it went live, two hours when all we have is the time it was scheduled for.
RECORDING_LEAD = timedelta(minutes=30)
RECORDING_LEAD_SCHEDULED = timedelta(hours=2)
RECENT_SHOWS = timedelta(days=21)  # ended shows older than this are not re-read
OPEN_ORDER_AGE = timedelta(days=14)
MAX_ORDER_PAGES = 60  # 3,000 orders in one catch-up; a show has a few hundred
MAX_OPEN_REFRESH = 150  # orders re-read one by one per timer run
SHOWS_EVERY = timedelta(minutes=15)
OPEN_ORDERS_EVERY = timedelta(minutes=60)
LIVE_POLL_QUIET = timedelta(minutes=10)  # no webhook for this long during a live show
# A show nobody recorded gets its own entry in Shows once it has been live this long (a
# laptop that starts recording a little late has had its chance to appear).
UNRECORDED_GRACE = timedelta(minutes=10)


def _str(value, limit: int) -> str | None:
    """An id or code from the API as a short clean string, or None."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = (text.clean(value) or "").strip()
    return value[:limit] or None


# ---------- shows ----------


def upsert_show(org_id: int, data: dict) -> WhatnotShow | None:
    whatnot_id = _str(data.get("id"), 40)
    if whatnot_id is None:
        return None
    row = db.session.scalar(
        select(WhatnotShow)
        .where(WhatnotShow.organization_id == org_id, WhatnotShow.whatnot_id == whatnot_id)
        .with_for_update()
    )
    if row is None:
        db.session.execute(
            insert(WhatnotShow)
            .values(organization_id=org_id, whatnot_id=whatnot_id)
            .on_conflict_do_nothing(constraint="uq_whatnot_shows_org_id")
        )
        row = db.session.scalar(
            select(WhatnotShow)
            .where(WhatnotShow.organization_id == org_id, WhatnotShow.whatnot_id == whatnot_id)
            .with_for_update()
        )
    status = _str(data.get("status"), 16)
    if status:
        row.status = status
    if isinstance(data.get("title"), str):
        row.title = (text.clean(data["title"]) or "").strip()[:300]
    row.scheduled_start_at = when(data.get("scheduled_start_at")) or row.scheduled_start_at
    row.ended_at = when(data.get("ended_at")) or row.ended_at
    row.url = text.whatnot_url(data.get("url") if isinstance(data.get("url"), str) else None)
    if row.url and len(row.url) > 512:
        row.url = None
    return row


def sync_show(org_id: int, client: WhatnotClient, whatnot_id: str) -> WhatnotShow | None:
    try:
        return upsert_show(org_id, client.show(whatnot_id))
    except NotFound:
        return None


def sync_shows(org_id: int, client: WhatnotClient) -> int:
    """Read the show list: everything live or scheduled, and the recently ended."""
    n = 0
    cutoff = datetime.now(UTC) - RECENT_SHOWS
    for status in ("live", "stopped", "scheduled", "ended", "cancelled"):
        for i, data in enumerate(client.shows(status)):
            row = upsert_show(org_id, data)
            n += row is not None
            old = row is not None and (row.scheduled_start_at or row.ended_at or cutoff) < cutoff
            if status in ("ended", "cancelled") and (old or i >= 40):
                break  # newest first: the rest is older still
            if i >= 200:
                break
    db.session.flush()
    # A show the list no longer calls live has ended, even if its webhook never came.
    link_recordings(org_id)
    return n


def _recording_window(show: Show) -> tuple[datetime, datetime]:
    last = db.session.scalar(
        select(func.max(ChatMessage.captured_at)).where(ChatMessage.show_id == show.id)
    )
    return show.started_at, max(last or show.started_at, show.started_at)


def _overlap(a_start, a_end, b_start, b_end) -> float:
    return (min(a_end, b_end) - max(a_start, b_start)).total_seconds()


def link_recordings(org_id: int) -> int:
    """Point each recent recording at the Whatnot show it is a recording of: the show that
    was live while it was recording. A link a person chose is never changed."""
    since = datetime.now(UTC) - RECENT_SHOWS
    recordings = db.session.scalars(
        select(Show).where(
            Show.organization_id == org_id,
            Show.started_at >= since,
            Show.whatnot_link.is_distinct_from("manual"),
            Show.whatnot_link.is_distinct_from("made"),
        )
    ).all()
    if not recordings:
        return 0
    candidates = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status.in_(ACTIVE + ("ended",)),
        )
    ).all()
    now = datetime.now(UTC)
    linked = 0
    for rec in recordings:
        rec_start, rec_end = _recording_window(rec)
        best: tuple[float, WhatnotShow] | None = None
        for ws in candidates:
            if rec.source_url and ws.whatnot_id in rec.source_url:
                best = (float("inf"), ws)
                break
            if ws.started_at is not None:
                start = ws.started_at - RECORDING_LEAD
            elif ws.scheduled_start_at is not None:
                start = ws.scheduled_start_at - RECORDING_LEAD_SCHEDULED
            else:
                continue
            seconds = _overlap(rec_start, rec_end + timedelta(minutes=1), start, ws.ended_at or now)
            if seconds > 0 and (best is None or seconds > best[0]):
                best = (seconds, ws)
        target = best[1].id if best else None
        if target is not None and rec.whatnot_show_id != target:
            rec.whatnot_show_id, rec.whatnot_link = target, "auto"
            linked += 1
    return linked


def recordings_of(ws: WhatnotShow) -> list[Show]:
    """The app's shows that are this Whatnot show, the one holding most of its sales first."""
    from ...models import Sale

    rows = db.session.execute(
        select(Show, func.count(Sale.id))
        .outerjoin(Sale, Sale.show_id == Show.id)
        .where(Show.organization_id == ws.organization_id, Show.whatnot_show_id == ws.id)
        .group_by(Show.id)
        .order_by(func.count(Sale.id).desc(), Show.id)
    ).all()
    return [show for show, _ in rows]


def ensure_recording(ws: WhatnotShow) -> Show | None:
    """The app's show for a Whatnot show: its recording, or (when nobody recorded it and it
    has been live long enough that a late laptop would have appeared) one made for it, so
    its orders have somewhere to live. None while it is too early to tell."""
    existing = recordings_of(ws)
    if existing:
        return existing[0]
    link_recordings(ws.organization_id)
    existing = recordings_of(ws)
    if existing:
        return existing[0]
    start = ws.begins_at
    if start is None or ws.status not in ACTIVE + ("ended",):
        return None
    live_since = ws.started_at or start
    if ws.status != "ended" and datetime.now(UTC) - live_since < UNRECORDED_GRACE:
        return None
    key = f"wn:{ws.whatnot_id}"[:64]
    db.session.execute(
        insert(Show)
        .values(
            organization_id=ws.organization_id,
            show_key=key,
            started_at=ws.started_at or start,
            show_date=show_date(ws.started_at or start),
            whatnot_show_id=ws.id,
            whatnot_link="made",
        )
        .on_conflict_do_nothing(constraint="uq_shows_org_key")
    )
    return db.session.scalar(
        select(Show).where(Show.organization_id == ws.organization_id, Show.show_key == key)
    )


# ---------- orders ----------


def _order_values(org_id: int, data: dict) -> dict | None:
    whatnot_id = _str(data.get("id"), 20)
    status = _str(data.get("status"), 24)
    if whatnot_id is None or status is None:
        return None
    buyer = data.get("buyer") if isinstance(data.get("buyer"), dict) else {}
    amounts = data.get("amounts") if isinstance(data.get("amounts"), dict) else {}
    discount = amounts.get("applied_discount")
    total = amounts.get("total") if isinstance(amounts.get("total"), dict) else {}
    return {
        "organization_id": org_id,
        "whatnot_id": whatnot_id,
        "whatnot_show_id": _str(data.get("show_id"), 40),
        "status": status,
        "transaction_type": _str(data.get("transaction_type"), 16),
        "buyer_id": _str(buyer.get("id"), 32),
        "buyer_username": _str(buyer.get("username"), 80),
        "subtotal_cents": cents(amounts.get("subtotal")),
        "shipping_cents": cents(amounts.get("shipping")),
        "tax_cents": cents(amounts.get("tax")),
        "discount_cents": cents(discount.get("amount")) if isinstance(discount, dict) else None,
        "total_cents": cents(total),
        "currency": _str(total.get("currency"), 3),
        "ordered_at": when(data.get("created_at")),
        "whatnot_updated_at": when(data.get("updated_at")),
        "cancelled_at": when(data.get("cancelled_at")),
        "synced_at": datetime.now(UTC),
    }


def upsert_order(org_id: int, data: dict) -> tuple[int | None, bool]:
    """Store what Whatnot says about an order. Returns (row id, whether it is new here).
    An answer older than what is stored is ignored: two reads can finish out of order."""
    values = _order_values(org_id, data)
    if values is None:
        return None, False
    existing = db.session.execute(
        select(WhatnotOrder.id, WhatnotOrder.buyer_username).where(
            WhatnotOrder.organization_id == org_id,
            WhatnotOrder.whatnot_id == values["whatnot_id"],
        )
    ).first()
    update = {k: v for k, v in values.items() if k not in ("organization_id", "whatnot_id")}
    if values["buyer_username"] is None:
        # A read without the buyer (a token that lost that permission) keeps who it was.
        update.pop("buyer_username")
        update.pop("buyer_id")
    stmt = insert(WhatnotOrder).values(**values)
    newer = stmt.excluded.whatnot_updated_at
    row_id = db.session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_whatnot_orders_org_id",
            set_=update,
            where=(
                WhatnotOrder.whatnot_updated_at.is_(None)
                | newer.is_(None)
                | (newer >= WhatnotOrder.whatnot_updated_at)
            ),
        ).returning(WhatnotOrder.id)
    ).scalar()
    if row_id is None and existing is not None:
        row_id = existing.id  # ours is the newer copy; nothing written
    return row_id, existing is None


def upsert_items(org_id: int, order_row_id: int, items: list[dict]) -> int:
    n = 0
    for data in items:
        whatnot_id = _str(data.get("id"), 20)
        if whatnot_id is None:
            continue
        bought = data.get("purchased_item") if isinstance(data.get("purchased_item"), dict) else {}
        quantity = data.get("quantity")
        values = {
            "organization_id": org_id,
            "order_id": order_row_id,
            "whatnot_id": whatnot_id,
            "title": ((text.clean(bought.get("title")) or "") if bought else "").strip()[:240],
            "sku_code": _str(bought.get("sku"), 80),
            "product_id": _str(data.get("product_id"), 20),
            "variant_id": _str(data.get("variant_id"), 40),
            "quantity": quantity
            if isinstance(quantity, int)
            and not isinstance(quantity, bool)
            and 0 < quantity < 10_000
            else 1,
            "unit_price_cents": cents(data.get("unit_price")),
            "whatnot_shipment_id": _str(data.get("shipment_id"), 20),
        }
        update = {
            k: v
            for k, v in values.items()
            if k not in ("organization_id", "whatnot_id", "order_id")
        }
        db.session.execute(
            insert(WhatnotOrderItem)
            .values(**values)
            .on_conflict_do_update(constraint="uq_whatnot_order_items_org_id", set_=update)
        )
        n += 1
    return n


def store_order(org_id: int, client: WhatnotClient, data: dict, *, items: bool | None = None):
    """Upsert an order and, for one new to us (or when asked), its items.
    Returns (row id, new)."""
    row_id, new = upsert_order(org_id, data)
    if row_id is None:
        return None, False
    if items if items is not None else new:
        upsert_items(org_id, row_id, client.order_items(str(data["id"])))
    return row_id, new


def sync_order(org_id: int, client: WhatnotClient, whatnot_id: str) -> WhatnotOrder | None:
    """Read one order (and its items) again from Whatnot."""
    try:
        data = client.order(whatnot_id)
    except NotFound:
        return None
    row_id, _ = store_order(org_id, client, data, items=True)
    db.session.flush()
    return db.session.get(WhatnotOrder, row_id) if row_id else None


def sync_show_orders(org_id: int, client: WhatnotClient, ws: WhatnotShow) -> dict:
    """Read every order placed since the show began (newest first, until older than it).
    Orders of other shows met on the way are stored too."""
    start = ws.begins_at
    floor = (start - SHOW_LEAD) if start else datetime.now(UTC) - timedelta(days=2)
    seen = new = 0
    for i, data in enumerate(client.orders()):
        made = when(data.get("created_at"))
        if made is not None and made < floor:
            break
        if i >= MAX_ORDER_PAGES * 50:
            log.warning("whatnot order catch-up for show %s stopped at its cap", ws.id)
            break
        _, was_new = store_order(org_id, client, data)
        seen += 1
        new += was_new
    db.session.flush()
    first = db.session.scalar(
        select(func.min(WhatnotOrder.ordered_at)).where(
            WhatnotOrder.organization_id == org_id, WhatnotOrder.whatnot_show_id == ws.whatnot_id
        )
    )
    if first is not None and (ws.started_at is None or first < ws.started_at):
        ws.started_at = first
    ws.orders_synced_at = datetime.now(UTC)
    return {"seen": seen, "new": new}


def refresh_open_orders(org_id: int, client: WhatnotClient) -> int:
    """Re-read the orders Whatnot had not settled when we last looked: a status change on
    an old order does not bring it back to the top of the list."""
    since = datetime.now(UTC) - OPEN_ORDER_AGE
    ids = db.session.scalars(
        select(WhatnotOrder.whatnot_id)
        .where(
            WhatnotOrder.organization_id == org_id,
            WhatnotOrder.status.in_(OPEN_ORDER_STATUSES),
            WhatnotOrder.ordered_at >= since,
        )
        .order_by(WhatnotOrder.synced_at)
        .limit(MAX_OPEN_REFRESH)
    ).all()
    for whatnot_id in ids:
        try:
            upsert_order(org_id, client.order(whatnot_id))
        except NotFound:
            continue
    return len(ids)


# ---------- what a read is followed by ----------


def _after_orders(org_id: int, show_ids: set[str | None]) -> None:
    """Orders of these Whatnot shows changed: in "auto" mode, apply them (soon, once)."""
    conn = connection.active(org_id)
    if conn is None or conn.orders_mode != "auto":
        return
    for whatnot_show_id in show_ids:
        if whatnot_show_id:
            jobs.enqueue(
                "whatnot_apply_show",
                {"org_id": org_id, "whatnot_show_id": whatnot_show_id},
                org_id=org_id,
                dedupe_key=f"whatnot_apply:{org_id}:{whatnot_show_id}",
                delay_s=5,
            )


def _show_row(org_id: int, whatnot_show_id: str | None) -> WhatnotShow | None:
    if not whatnot_show_id:
        return None
    return db.session.scalar(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id, WhatnotShow.whatnot_id == whatnot_show_id
        )
    )


# ---------- webhooks ----------


@webhooks.on("orders.created", "orders.updated")
def _on_order(event: WhatnotEvent) -> None:
    order_id = (event.ref or {}).get("order_id")
    if not order_id:
        return
    org_id = event.organization_id
    client = client_for(org_id)
    order = sync_order(org_id, client, order_id)
    if order is None:
        return
    if order.whatnot_show_id and _show_row(org_id, order.whatnot_show_id) is None:
        ws = sync_show(org_id, client, order.whatnot_show_id)  # a show we had not heard of
        if ws is not None:
            link_recordings(org_id)
    _after_orders(org_id, {order.whatnot_show_id})


@webhooks.on("shows.created", "shows.updated", "shows.started", "shows.ended", "shows.cancelled")
def _on_show(event: WhatnotEvent) -> None:
    show_id = (event.ref or {}).get("show_id")
    if not show_id:
        return
    org_id = event.organization_id
    ws = sync_show(org_id, client_for(org_id), show_id)
    if ws is None:
        return
    if event.topic == "shows.started" and event.event_at is not None:
        # A stopped show that resumes sends this again: keep the first start.
        ws.started_at = min(ws.started_at or event.event_at, event.event_at)
    db.session.flush()
    link_recordings(org_id)
    if event.topic == "shows.ended":
        # Every order of the show, read once more now that no more can come.
        jobs.enqueue(
            "whatnot_sync_show",
            {"org_id": org_id, "whatnot_show_id": ws.whatnot_id},
            org_id=org_id,
            dedupe_key=f"whatnot_sync_show:{org_id}:{ws.whatnot_id}",
            delay_s=30,
        )


# ---------- jobs ----------


def _run(org_id: int, resource: str, fn):
    """Run one kind of reading and keep its bookkeeping: when it last worked, or why not."""
    state = connection.sync_state(org_id, resource)
    state.last_started_at = datetime.now(UTC)
    db.session.commit()
    try:
        counts = fn()
    except WhatnotError as e:
        db.session.rollback()
        connection.note_failure(org_id, e)
        state = connection.sync_state(org_id, resource)
        state.last_error = f"{type(e).__name__} {e.problem}".strip()[:240]
        db.session.commit()
        raise
    state = connection.sync_state(org_id, resource)
    state.last_ok_at, state.last_error = datetime.now(UTC), None
    if isinstance(counts, dict):
        state.counts = counts
    return counts


@jobs.handler("whatnot_sync_show")
def sync_show_job(payload: dict) -> None:
    org_id, whatnot_show_id = payload["org_id"], payload["whatnot_show_id"]
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)

    def read():
        ws = sync_show(org_id, client, whatnot_show_id)
        if ws is None:
            return {"seen": 0, "new": 0}
        db.session.flush()
        link_recordings(org_id)
        return sync_show_orders(org_id, client, ws)

    _run(org_id, "orders", read)
    _after_orders(org_id, {whatnot_show_id})


def _due(org_id: int, resource: str, every: timedelta) -> bool:
    last = connection.sync_state(org_id, resource).last_started_at
    return last is None or datetime.now(UTC) - last >= every


@jobs.handler("whatnot_sync")
def sync_job(payload: dict) -> None:
    """The timer's pass for one shop: the show list, unsettled orders, and a live show's
    orders when Whatnot has been quiet (webhooks not set up, or not arriving)."""
    org_id = payload["org_id"]
    conn = connection.active(org_id)
    if conn is None:
        return
    client = client_for(org_id)
    if _due(org_id, "shows", SHOWS_EVERY):
        _run(org_id, "shows", lambda: {"shows": sync_shows(org_id, client)})
        db.session.commit()
    conn = connection.active(org_id)
    quiet = conn.last_event_at is None or datetime.now(UTC) - conn.last_event_at > LIVE_POLL_QUIET
    live = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id, WhatnotShow.status.in_(ACTIVE)
        )
    ).all()
    # An ended show whose orders were never read in full (its webhook never came).
    unread = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status == "ended",
            WhatnotShow.ended_at >= datetime.now(UTC) - RECENT_SHOWS,
            WhatnotShow.orders_synced_at.is_(None)
            | (WhatnotShow.orders_synced_at < WhatnotShow.ended_at),
        )
    ).all()
    changed: set[str | None] = set()
    for ws in (live if quiet else []) + unread:
        _run(org_id, "orders", lambda ws=ws: sync_show_orders(org_id, client, ws))
        changed.add(ws.whatnot_id)
        db.session.commit()
    if _due(org_id, "open_orders", OPEN_ORDERS_EVERY):
        before = _open_by_show(org_id)
        _run(org_id, "open_orders", lambda: {"orders": refresh_open_orders(org_id, client)})
        db.session.commit()
        after = _open_by_show(org_id)
        changed |= {show for show in before if before[show] != after.get(show)}
    _after_orders(org_id, changed)


def _open_by_show(org_id: int) -> dict:
    rows = db.session.execute(
        select(WhatnotOrder.whatnot_show_id, func.count())
        .where(WhatnotOrder.organization_id == org_id, WhatnotOrder.status.in_(OPEN_ORDER_STATUSES))
        .group_by(WhatnotOrder.whatnot_show_id)
    ).all()
    return {show: n for show, n in rows}


def schedule_all() -> int:
    """Queue the timer's pass for every connected shop (the worker's sweep calls this)."""
    orgs = db.session.scalars(
        select(WhatnotConnection.organization_id).where(WhatnotConnection.disconnected_at.is_(None))
    ).all()
    for org_id in orgs:
        jobs.enqueue(
            "whatnot_sync", {"org_id": org_id}, org_id=org_id, dedupe_key=f"whatnot_sync:{org_id}"
        )
    db.session.commit()
    return len(orgs)
