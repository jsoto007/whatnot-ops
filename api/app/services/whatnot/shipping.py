"""Shipments, labels and carrier status from Whatnot's Seller API.

The rule the shop works by: for one show, one buyer gets ONE package and ONE label, with
everything they won in it. Whatnot does the bundling itself: while a buyer's shipment has
no label, each new win joins it. Making the label closes the bundle, so anything bought
afterwards ships (and is charged shipping) separately. Therefore:

  * a label is only asked for after the show has ended;
  * a buyer whose wins sit in two open packages is called out ("combine them in Seller
    Hub"): the API cannot merge shipments, and two labels is exactly what must not happen;
  * a package Whatnot wants something done about first (a pending cancellation, a package
    review) is held back, with the reason in words.

What is kept: the shipment's id, status, tracking, whether a label exists, the drop-off
date and Whatnot's required actions. Never the address, and never the label or its link:
a label is fetched from Whatnot at the moment someone prints it, and streamed through.

Reading shipments changes no sale, payment or stock. The one thing carrier news may do is
move the app's order to Shipped once every package of that buyer has been handed to the
carrier ("auto" mode, or a person pressing the button).
"""

import hashlib
import ipaddress
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import requests
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from ...db import db
from ...models import (
    Order,
    OrderStatus,
    Show,
    WhatnotEvent,
    WhatnotLot,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShipment,
    WhatnotShow,
)
from .. import jobs, rules, text
from . import connection, sync, webhooks
from .client import (
    Conflict,
    Forbidden,
    NotFound,
    Unauthorized,
    WhatnotClient,
    WhatnotError,
    client_for,
    when,
)

log = logging.getLogger(__name__)

OPEN = "created"  # no label yet: the buyer's bundle is still open
LABELLED = "label_generated"
WITH_CARRIER = ("dropped_off", "in_transit", "delivered")
GONE = (
    "cancelled",
    "cancelled_not_shipped",
    "cancelled_not_shipped_with_dropoff",
    "cancelled_tracking_stuck",
)
# The Status column staff read (and the scraper used to copy from Seller Hub).
STATUS_WORDS = {
    "created": "Needs label",
    "label_generated": "Label ready",
    "dropped_off": "Dropped off",
    "in_transit": "In transit",
    "delivered": "Delivered",
    "failed": "Delivery failed",
}
SALE_TYPE = {"auction": "Auction", "buy_it_now": "Buy It Now", "giveaway": "Giveaway"}
# Orders that still ship: cancelled and failed ones have nothing to pack.
SHIPPABLE = ("pending", "pending_fulfillment", "completed")
MAX_SHIPMENT_PAGES = 40
SHIPMENTS_EVERY = timedelta(minutes=30)
RECENT = timedelta(days=21)
MAX_LABEL_BYTES = 15 * 1024 * 1024
LABELS_PER_REQUEST = 400


class ShippingError(Exception):
    """Why a label cannot be made or printed right now, in the shop's words."""

    status = 409


# ---------- reading ----------


def _str(value, limit: int) -> str | None:
    return sync._str(value, limit)


def _row_key(org_id: int, whatnot_id: str) -> str:
    return hashlib.sha256(f"{org_id}|api|{whatnot_id}".encode()).hexdigest()


def _local_show_id(org_id: int, whatnot_show_id: str | None) -> int | None:
    ws = sync._show_row(org_id, whatnot_show_id)
    if ws is None:
        return None
    recordings = sync.recordings_of(ws)
    return recordings[0].id if recordings else None


def _status_text(data: dict, status: str) -> str:
    return "Cancelled" if status in GONE else STATUS_WORDS.get(status, "Other")


def _tracking_text(tracking: dict) -> str:
    parts = [tracking.get("carrier"), tracking.get("service"), tracking.get("tracking_code")]
    return " ".join(p for p in parts if isinstance(p, str) and p)[:120]


def _mirror_items(org_id: int, client: WhatnotClient, items: list[dict]):
    """The mirrored order items a shipment holds (reading an order we have not seen yet)."""
    out: list[tuple[WhatnotOrder, WhatnotOrderItem]] = []
    for it in items:
        order_id, item_id = _str(it.get("order_id"), 20), _str(it.get("order_item_id"), 20)
        if not order_id or not item_id:
            continue
        row = db.session.execute(
            select(WhatnotOrder, WhatnotOrderItem)
            .join(WhatnotOrderItem, WhatnotOrderItem.order_id == WhatnotOrder.id)
            .where(
                WhatnotOrderItem.organization_id == org_id, WhatnotOrderItem.whatnot_id == item_id
            )
        ).first()
        if row is None:
            sync.sync_order(org_id, client, order_id)
            row = db.session.execute(
                select(WhatnotOrder, WhatnotOrderItem)
                .join(WhatnotOrderItem, WhatnotOrderItem.order_id == WhatnotOrder.id)
                .where(
                    WhatnotOrderItem.organization_id == org_id,
                    WhatnotOrderItem.whatnot_id == item_id,
                )
            ).first()
        if row is not None:
            out.append((row[0], row[1]))
    return out


def _adopt_scraped_row(org_id: int, order_ids: list[str]) -> WhatnotShipment | None:
    """A row the Seller Hub scraper already made for this same parcel (it holds one of the
    parcel's orders): it becomes the API's row, so the buyer is not counted twice."""
    if not order_ids:
        return None
    return db.session.scalar(
        select(WhatnotShipment)
        .join(WhatnotLot, WhatnotLot.shipment_id == WhatnotShipment.id)
        .where(
            WhatnotShipment.organization_id == org_id,
            WhatnotShipment.whatnot_id.is_(None),
            WhatnotLot.whatnot_order_id.in_(order_ids),
        )
        .order_by(WhatnotShipment.id)
        .limit(1)
        .with_for_update(of=WhatnotShipment)
    )


def store_shipment(
    org_id: int, client: WhatnotClient, data: dict, *, items: list[dict] | None = None
) -> WhatnotShipment | None:
    """Store what Whatnot says about one shipment; with `items`, also which lots it holds.
    An answer older than what is stored is ignored."""
    whatnot_id, status = _str(data.get("id"), 20), _str(data.get("status"), 40)
    if whatnot_id is None or status is None:
        return None
    row = db.session.scalar(
        select(WhatnotShipment)
        .where(WhatnotShipment.organization_id == org_id, WhatnotShipment.whatnot_id == whatnot_id)
        .with_for_update()
    )
    updated = when(data.get("updated_at"))
    if row is not None and None not in (updated, row.whatnot_updated_at):
        if updated < row.whatnot_updated_at:
            return row
    held = _mirror_items(org_id, client, items) if items is not None else []
    recipient = data.get("recipient") if isinstance(data.get("recipient"), dict) else {}
    name = _str(recipient.get("username"), 80) or next(
        (o.buyer_username for o, _ in held if o.buyer_username), None
    )
    if row is None:
        row = _adopt_scraped_row(org_id, [o.whatnot_id for o, _ in held])
        if row is None:
            if not name:
                # Nothing says whose parcel this is (no customer permission, items not
                # read): it cannot be put on a buyer's slip. The next full read retries.
                return None
            db.session.execute(
                insert(WhatnotShipment)
                .values(
                    organization_id=org_id,
                    row_key=_row_key(org_id, whatnot_id),
                    recipient=name.lstrip("@"),
                    whatnot_id=whatnot_id,
                )
                .on_conflict_do_nothing()
            )
            row = db.session.scalar(
                select(WhatnotShipment)
                .where(
                    WhatnotShipment.organization_id == org_id,
                    WhatnotShipment.whatnot_id == whatnot_id,
                )
                .with_for_update()
            )
        row.whatnot_id = whatnot_id
    tracking = data.get("tracking") if isinstance(data.get("tracking"), dict) else {}
    documents = data.get("documents") if isinstance(data.get("documents"), dict) else {}
    actions = data.get("required_actions")
    if name:
        row.recipient = name.lstrip("@")
    row.api_status = status
    row.status = _status_text(data, status)
    row.carrier = _str(tracking.get("carrier"), 60)
    row.tracking_code = _str(tracking.get("tracking_code"), 80)
    row.tracking = _tracking_text(tracking)
    # With the customer permission Whatnot sends the links; without it only the status
    # tells us a label exists.
    row.label_ready = bool(documents.get("label_url") or documents.get("bundled_url")) or (
        status in (LABELLED,) + WITH_CARRIER
    )
    row.dropoff_due = when(data.get("dropoff_due"))
    row.required_actions = [a for a in actions if isinstance(a, str)][:8] if actions else None
    row.fulfillment_method = _str(data.get("fulfillment_method"), 16)
    row.replaced_by = _str(data.get("replacement_shipment_id"), 20)
    url = data.get("url") if isinstance(data.get("url"), str) else None
    row.url = text.whatnot_url(url) if url and len(url) <= 512 else None
    row.whatnot_updated_at = updated or row.whatnot_updated_at
    row.updated_at = datetime.now(UTC)
    if status != OPEN:
        row.label_problem = None
    if items is not None:
        _store_lots(org_id, row, held)
    return row


def _store_lots(org_id: int, row: WhatnotShipment, held) -> None:
    """The lots a shipment holds right now, as the order slips read them, and on each
    mirrored order item which shipment it is in."""
    item_ids = [item.id for _, item in held]
    # Items that were in this shipment and are not any more (re-bundled, split).
    db.session.execute(
        update(WhatnotOrderItem)
        .where(
            WhatnotOrderItem.organization_id == org_id,
            WhatnotOrderItem.whatnot_shipment_id == row.whatnot_id,
            WhatnotOrderItem.id.not_in(item_ids or [0]),
        )
        .values(whatnot_shipment_id=None)
    )
    nth: dict[int, int] = defaultdict(int)
    shows: dict[int | None, int] = defaultdict(int)
    for order, item in sorted(held, key=lambda p: (p[0].id, p[1].id)):
        item.whatnot_shipment_id = row.whatnot_id
        nth[order.id] += 1
        if order.status not in SHIPPABLE:
            continue  # cancelled or failed: nothing to pack (sync drops its lots)
        show_id = _local_show_id(org_id, order.whatnot_show_id)
        shows[show_id] += 1
        key = order.whatnot_id if nth[order.id] == 1 else f"{order.whatnot_id}-{nth[order.id]}"
        values = {
            "organization_id": org_id,
            "show_id": show_id,
            "shipment_id": row.id,
            "whatnot_order_id": key[:20],
            "recipient": row.recipient,
            "title": item.title,
            "lot_number": rules.lot_number(item.title),
            "quantity": item.quantity,
            "price_cents": item.unit_price_cents,
            "shipping_cents": order.shipping_cents if nth[order.id] == 1 else None,
            "sale_type": SALE_TYPE.get(order.transaction_type or "", ""),
        }
        stmt = insert(WhatnotLot).values(**values)
        db.session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_whatnot_lots_org_order",
                set_={k: stmt.excluded[k] for k in values if k not in ("organization_id",)}
                | {"updated_at": datetime.now(UTC)},
            )
        )
    row.item_count = sum(item.quantity for _, item in held)
    priced = [item.unit_price_cents * item.quantity for _, item in held if item.unit_price_cents]
    row.value_cents = sum(priced) if priced else row.value_cents
    known = {k: v for k, v in shows.items() if k is not None}
    if known:
        # The show most of its lots belong to (a bundle can span two shows).
        row.show_id = max(known, key=lambda k: (known[k], -k))


def sync_shipment(org_id: int, client: WhatnotClient, whatnot_id: str) -> WhatnotShipment | None:
    """Read one shipment and its items again, following it to its replacement if it has one."""
    seen: set[str] = set()
    row = None
    while whatnot_id and whatnot_id not in seen and len(seen) < 5:
        seen.add(whatnot_id)
        try:
            data = client.shipment(whatnot_id)
        except NotFound:
            return row
        row = store_shipment(org_id, client, data, items=client.shipment_items(whatnot_id))
        whatnot_id = _str(data.get("replacement_shipment_id"), 20) or ""
    db.session.flush()
    return row


def sync_recent_shipments(org_id: int, client: WhatnotClient, since: datetime) -> dict:
    """Read every shipment made since `since` (newest first). The list carries each one's
    status and tracking; its items are read only when it is new or has changed."""
    seen = changed = 0
    for i, data in enumerate(client.shipments()):
        made = when(data.get("created_at"))
        if (made is not None and made < since) or i >= MAX_SHIPMENT_PAGES * 50:
            break
        whatnot_id = _str(data.get("id"), 20)
        if whatnot_id is None:
            continue
        seen += 1
        known = db.session.execute(
            select(WhatnotShipment.whatnot_updated_at, WhatnotShipment.item_count).where(
                WhatnotShipment.organization_id == org_id, WhatnotShipment.whatnot_id == whatnot_id
            )
        ).first()
        updated = when(data.get("updated_at"))
        fresh = known is not None and known[1] is not None and known[0] == updated
        if fresh and updated is not None:
            continue  # nothing about it has changed since we read it
        store_shipment(org_id, client, data, items=client.shipment_items(whatnot_id))
        changed += 1
    db.session.flush()
    return {"seen": seen, "changed": changed}


# ---------- one package per buyer ----------


def _state(packages: list[dict], unplaced: int, ended: bool) -> str:
    """Where a buyer's shipping stands, as one code the page turns into words."""
    live = [p for p in packages if p["status"] not in GONE]
    if any(p["holds"] for p in live):
        return "hold"
    if not live:
        return "none"
    open_ = [p for p in live if p["status"] == OPEN]
    if len(open_) > 1:
        return "split"  # two open packages: two labels unless they are combined first
    if unplaced:
        return "none"
    if open_:
        if not ended:
            return "wait"
        return "second" if len(live) > 1 else "ready"
    if all(p["status"] in WITH_CARRIER for p in live):
        return "shipped"
    if any(p["status"] == "failed" for p in live):
        return "failed"
    return "labelled"


def packages(show: Show) -> dict:
    """Every buyer of the show with their package(s) on Whatnot, in pick order (most items
    first, then A-Z, as on the order slips)."""
    org_id = show.organization_id
    conn = connection.active(org_id)
    ws = db.session.get(WhatnotShow, show.whatnot_show_id) if show.whatnot_show_id else None
    if conn is None or ws is None:
        return {"connected": conn is not None, "matched": False, "buyers": [], "totals": {}}
    rows = db.session.execute(
        select(WhatnotOrder, WhatnotOrderItem)
        .join(WhatnotOrderItem, WhatnotOrderItem.order_id == WhatnotOrder.id)
        .where(
            WhatnotOrder.organization_id == org_id,
            WhatnotOrder.whatnot_show_id == ws.whatnot_id,
            WhatnotOrder.status.in_(SHIPPABLE),
        )
    ).all()
    shipment_ids = {item.whatnot_shipment_id for _, item in rows if item.whatnot_shipment_id}
    shipments = {
        s.whatnot_id: s
        for s in db.session.scalars(
            select(WhatnotShipment).where(
                WhatnotShipment.organization_id == org_id,
                WhatnotShipment.whatnot_id.in_(shipment_ids or [""]),
            )
        )
    }
    # How many items each of those shipments holds in all (a bundle can span two shows).
    totals_in = dict(
        db.session.execute(
            select(WhatnotOrderItem.whatnot_shipment_id, func.sum(WhatnotOrderItem.quantity))
            .where(
                WhatnotOrderItem.organization_id == org_id,
                WhatnotOrderItem.whatnot_shipment_id.in_(shipment_ids or [""]),
            )
            .group_by(WhatnotOrderItem.whatnot_shipment_id)
        ).all()
    )
    recordings = [s.id for s in sync.recordings_of(ws)] or [show.id]
    orders = {
        o.buyer_username.lower(): o
        for o in db.session.scalars(
            select(Order).where(Order.show_id.in_(recordings)).order_by(Order.id.desc())
        )
    }
    ended = ws.status == "ended"
    by_buyer: dict[str, dict] = {}
    for order, item in rows:
        name = (order.buyer_username or "").lstrip("@")
        b = by_buyer.setdefault(
            name.lower(), {"buyer": name, "items": 0, "unplaced": 0, "in": defaultdict(int)}
        )
        b["items"] += item.quantity
        if item.whatnot_shipment_id and item.whatnot_shipment_id in shipments:
            b["in"][item.whatnot_shipment_id] += item.quantity
        else:
            b["unplaced"] += item.quantity
    out = []
    for key, b in by_buyer.items():
        packs = []
        for whatnot_id, n in b["in"].items():
            s = shipments[whatnot_id]
            packs.append(
                {
                    "id": s.id,
                    "status": s.api_status or "",
                    "items": n,
                    # Items in the same parcel that belong to another show.
                    "otherItems": max(0, int(totals_in.get(whatnot_id) or 0) - n),
                    "tracking": s.tracking_code,
                    "carrier": s.carrier,
                    "labelReady": s.label_ready,
                    "dueAt": s.dropoff_due.isoformat() if s.dropoff_due else None,
                    "holds": list(s.required_actions or []),
                    "pickup": s.fulfillment_method == "pickup",
                    "url": s.url,
                    "labelRequestedAt": s.label_requested_at.isoformat()
                    if s.label_requested_at
                    else None,
                    "labelProblem": s.label_problem,
                }
            )
        packs.sort(key=lambda p: p["id"])
        local = orders.get(key)
        out.append(
            {
                "buyer": b["buyer"] or None,
                "items": b["items"],
                "unplaced": b["unplaced"],
                "packages": packs,
                "state": _state(packs, b["unplaced"], ended),
                "orderId": local.id if local else None,
                "orderStatus": local.status.value if local else None,
            }
        )
    out.sort(key=lambda b: (-b["items"], (b["buyer"] or "").lower()))
    count = defaultdict(int)
    for b in out:
        count[b["state"]] += 1
    return {
        "connected": True,
        "matched": True,
        "mode": conn.orders_mode,
        "show": {"title": ws.title, "status": ws.status, "ended": ended},
        "buyers": out,
        "totals": {"buyers": len(out), **count},
        "toMarkShipped": len(_carrier_has(show, out)),
    }


# ---------- labels ----------


def _label_code(e: WhatnotError) -> str:
    if e.problem == "seller-verification-required":
        return "needs_verification"
    if isinstance(e, Forbidden):
        return "no_permission"
    if isinstance(e, Conflict):
        return "not_now"
    return "failed"


# Whatnot makes a label in the background: for this long after asking, a second press
# does not ask again (unless Whatnot refused the first).
LABEL_IN_FLIGHT = timedelta(minutes=15)


def _in_flight(package: dict, now: datetime) -> bool:
    asked = when(package["labelRequestedAt"])
    return asked is not None and not package["labelProblem"] and now - asked < LABEL_IN_FLIGHT


def request_labels(show: Show, *, user, buyers: list[str] | None = None) -> dict:
    """Ask Whatnot to make the label of every buyer who is ready for one: the show has
    ended, and all their wins are in one open package with nothing held. Never for a buyer
    with two open packages. Queued as a job: Whatnot makes labels in the background."""
    view = packages(show)
    if not view["matched"]:
        raise ShippingError("This show is not matched to a show on Whatnot yet.")
    if not view["show"]["ended"]:
        raise ShippingError(
            "Wait until the show has ended. Making a label closes the buyer's bundle: "
            "anything they win afterwards would ship, and be charged shipping, separately."
        )
    wanted = {b.lower().lstrip("@") for b in buyers} if buyers else None
    now = datetime.now(UTC)
    ids = []
    for b in view["buyers"]:
        if wanted is not None and (b["buyer"] or "").lower() not in wanted:
            continue
        if b["state"] in ("ready", "second"):
            ids += [
                p["id"]
                for p in b["packages"]
                if p["status"] == OPEN and not p["holds"] and not _in_flight(p, now)
            ]
    ids = ids[:LABELS_PER_REQUEST]
    if not ids:
        return {"requested": 0}
    db.session.execute(
        update(WhatnotShipment)
        .where(WhatnotShipment.id.in_(ids), WhatnotShipment.organization_id == show.organization_id)
        .values(label_requested_at=now, label_requested_by_id=user.id, label_problem=None)
    )
    jobs.enqueue(
        "whatnot_labels",
        {"org_id": show.organization_id, "shipment_ids": ids},
        org_id=show.organization_id,
    )
    return {"requested": len(ids)}


@jobs.handler("whatnot_labels")
def labels_job(payload: dict) -> None:
    """Send each label request. One that Whatnot refuses is recorded on its shipment and
    the rest go on; the job is not retried as a whole (a label must not be asked for twice
    because a later one failed)."""
    org_id = payload["org_id"]
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)
    for row_id in payload.get("shipment_ids", []):
        row = db.session.get(WhatnotShipment, row_id)
        if row is None or row.organization_id != org_id or not row.whatnot_id:
            continue
        if row.api_status != OPEN:
            continue  # it got its label some other way in the meantime
        try:
            client.request("POST", f"/shipments/{row.whatnot_id}/generate_label")
        except WhatnotError as e:
            if isinstance(e, Unauthorized):
                connection.note_failure(org_id, e)  # the token itself stopped working
            row = db.session.get(WhatnotShipment, row_id)
            row.label_problem = _label_code(e)
        db.session.commit()
    # Whatnot says "queued", not "done": the label arrives by webhook, or at the next read.


def _safe_url(url) -> str | None:
    """A link Whatnot gave us, fetched only if it is plain https to a public host."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or host == "localhost" or host.endswith(".local"):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass  # a name, not an address
    return url


def _fetch(url: str) -> bytes:
    from flask import current_app

    fetch = current_app.config.get("WHATNOT_FETCH")
    if fetch is not None:
        return fetch(url)
    if current_app.config.get("TESTING"):
        raise RuntimeError("a test tried to download a label: install a WHATNOT_FETCH")
    try:
        with requests.get(url, timeout=(5, 30), stream=True, allow_redirects=False) as r:
            if r.status_code != 200:
                raise ShippingError("Whatnot's label could not be downloaded. Try again.")
            data = r.raw.read(MAX_LABEL_BYTES + 1, decode_content=True)
    except requests.RequestException:
        raise ShippingError("Whatnot's label could not be downloaded. Try again.") from None
    return data


def label_pdf(row: WhatnotShipment) -> tuple[bytes, str]:
    """The shipment's label (with Whatnot's packing slip when it has the two combined),
    fetched from Whatnot right now. Returns (pdf bytes, which). Nothing is kept: the file
    shows the buyer's address, and the link to it is short-lived on purpose."""
    if not row.whatnot_id:
        raise ShippingError("This shipment was read from Seller Hub, not from Whatnot's API.")
    data = client_for(row.organization_id).shipment(row.whatnot_id)
    documents = data.get("documents") if isinstance(data.get("documents"), dict) else None
    if documents is None:
        if (data.get("status") or "") == OPEN:
            raise ShippingError("This package has no label yet.")
        raise ShippingError(
            "Whatnot did not send the label. The access token needs the permission to read "
            "customers, because a label shows the buyer's address."
        )
    for which in ("bundled_url", "label_url"):
        url = _safe_url(documents.get(which))
        if url is None:
            continue
        pdf = _fetch(url)
        if len(pdf) > MAX_LABEL_BYTES or not pdf.startswith(b"%PDF"):
            raise ShippingError("What Whatnot sent is not a label that can be printed.")
        return pdf, "label and slip" if which == "bundled_url" else "label"
    raise ShippingError("This package has no label yet.")


# ---------- carrier news → the app's orders ----------


def _carrier_has(show: Show, buyers: list[dict]) -> list[tuple[int, str | None]]:
    """(order id, tracking) for each buyer whose every package is with the carrier while
    the app's order is still waiting to be picked, packed or marked shipped."""
    out = []
    for b in buyers:
        if b["state"] != "shipped" or b["orderId"] is None:
            continue
        if b["orderStatus"] not in ("paid", "picking", "packed"):
            continue
        tracking = next((p["tracking"] for p in b["packages"] if p["tracking"]), None)
        out.append((b["orderId"], tracking))
    return out


def mark_shipped(show: Show, *, actor_label: str = "Whatnot") -> int:
    """Move to Shipped the orders whose packages the carrier already has."""
    from .. import orders as orders_service

    n = 0
    for order_id, tracking in _carrier_has(show, packages(show)["buyers"]):
        order = db.session.get(Order, order_id, with_for_update=True)
        if order is None or order.status not in (
            OrderStatus.paid,
            OrderStatus.picking,
            OrderStatus.packed,
        ):
            continue
        orders_service.ship_by_carrier(order, tracking=tracking, actor_label=actor_label)
        n += 1
    return n


def _after_shipments(org_id: int, show_ids: set[int | None]) -> None:
    conn = connection.active(org_id)
    if conn is None or conn.orders_mode != "auto":
        return
    for show_id in show_ids:
        show = db.session.get(Show, show_id) if show_id else None
        if show is not None and mark_shipped(show):
            from .. import audit

            audit.record(
                org_id,
                "whatnot.orders_shipped",
                actor_type="whatnot",
                target_type="show",
                target_id=show.id,
            )


# ---------- webhooks, jobs, the timer ----------


@webhooks.on(
    "shipments.created",
    "shipments.label_generated",
    "shipments.tracking_code_added",
    "shipments.parcel_updated",
    "shipments.items.added",
    "shipments.items.removed",
    "shipments.cancelled",
    "shipments.replaced",
)
def _on_shipment(event: WhatnotEvent) -> None:
    ref = event.ref or {}
    org_id = event.organization_id
    client = client_for(org_id)
    shows: set[int | None] = set()
    for whatnot_id in (ref.get("shipment_id"), ref.get("replaced_by")):
        if whatnot_id:
            row = sync_shipment(org_id, client, whatnot_id)
            shows.add(row.show_id if row is not None else None)
    _after_shipments(org_id, shows)


def read_show(org_id: int, client: WhatnotClient, ws: WhatnotShow) -> dict:
    """Every shipment made since the show began."""
    start = ws.begins_at or datetime.now(UTC) - timedelta(days=2)
    return sync_recent_shipments(org_id, client, start - sync.SHOW_LEAD)


@jobs.handler("whatnot_sync_shipments")
def sync_shipments_job(payload: dict) -> None:
    org_id = payload["org_id"]
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)
    ws = sync._show_row(org_id, payload.get("whatnot_show_id"))
    since = (
        (ws.begins_at or datetime.now(UTC)) - sync.SHOW_LEAD
        if ws is not None
        else datetime.now(UTC) - RECENT
    )
    sync._run(org_id, "shipments", lambda: sync_recent_shipments(org_id, client, since))
    db.session.commit()
    shows = set(
        db.session.scalars(
            select(WhatnotShipment.show_id)
            .where(
                WhatnotShipment.organization_id == org_id,
                WhatnotShipment.whatnot_id.is_not(None),
                WhatnotShipment.updated_at >= datetime.now(UTC) - timedelta(minutes=10),
            )
            .distinct()
        )
    )
    _after_shipments(org_id, shows)


def schedule(org_id: int, whatnot_show_id: str | None = None, *, delay_s: int = 0):
    return jobs.enqueue(
        "whatnot_sync_shipments",
        {"org_id": org_id, "whatnot_show_id": whatnot_show_id},
        org_id=org_id,
        dedupe_key=f"whatnot_sync_shipments:{org_id}:{whatnot_show_id or 'recent'}",
        delay_s=delay_s,
    )


def due(org_id: int) -> bool:
    """The timer: is it time to look at recent shipments again? Only while some are still
    on their way (or a show ended recently and its labels are not made)."""
    if not sync._due(org_id, "shipments", SHIPMENTS_EVERY):
        return False
    since = datetime.now(UTC) - RECENT
    moving = db.session.scalar(
        select(WhatnotShipment.id)
        .where(
            WhatnotShipment.organization_id == org_id,
            WhatnotShipment.whatnot_id.is_not(None),
            WhatnotShipment.api_status.in_((OPEN, LABELLED, "dropped_off", "in_transit")),
            WhatnotShipment.captured_at >= since,
        )
        .limit(1)
    )
    recent_show = db.session.scalar(
        select(WhatnotShow.id)
        .where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status == "ended",
            WhatnotShow.ended_at >= datetime.now(UTC) - timedelta(days=3),
        )
        .limit(1)
    )
    return moving is not None or recent_show is not None
