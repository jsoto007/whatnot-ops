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
import socket
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import requests
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

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
    WhatnotSyncState,
)
from .. import jobs, rules, text
from . import connection, sync, webhooks
from .client import (
    MAX_CENTS,
    Busy,
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
FETCH_SECONDS = 45  # the whole download, not each read
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
    """ "USPS Ground Advantage 9400…", as Seller Hub prints it. Cleaned like every other
    text from outside: it is shown on the order slips."""
    parts = [_str(tracking.get(k), 60) for k in ("carrier", "service", "tracking_code")]
    return " ".join(p for p in parts if p)[:120]


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
        # Another reader may have stored this same shipment while we waited for the row:
        # the "older answer loses" check again, now that the row is ours.
        if None not in (updated, row.whatnot_updated_at) and updated < row.whatnot_updated_at:
            return row
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
        if len(key) > 20:
            continue  # cut short it would overwrite the order's first lot (ids are ~10 digits)
        values = {
            "organization_id": org_id,
            "show_id": show_id,
            "shipment_id": row.id,
            "whatnot_order_id": key,
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
    row.value_cents = min(sum(priced), MAX_CENTS) if priced else row.value_cents
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


def _state(packages: list[dict], unplaced: int, ended: bool, complete: bool) -> str:
    """Where a buyer's shipping stands, as one code the page turns into words. `complete`
    says every order and every parcel of the show was read after it ended: before that a
    last win may still be on its way into a second package, so nobody is "ready"."""
    live = [p for p in packages if p["status"] not in GONE]
    if any(p["holds"] for p in live):
        return "hold"
    if not live:
        return "none"
    to_ship = [p for p in live if not p["pickup"]]
    if not to_ship:
        return "pickup"  # handed over in person: no label, ever
    open_ = [p for p in to_ship if p["status"] == OPEN]
    if len(open_) > 1:
        return "split"  # two open packages: two labels unless they are combined first
    if unplaced:
        return "none"
    if open_:
        if not ended:
            return "wait"
        if open_[0]["liveElsewhere"]:
            # The same bundle is still filling in another show that is live right now.
            return "shared"
        if not complete:
            return "reading"
        return "second" if len(to_ship) > 1 else "ready"
    if all(p["status"] in WITH_CARRIER for p in to_ship):
        return "shipped"
    if any(p["status"] == "failed" for p in to_ship):
        return "failed"
    if all(p["status"] in (LABELLED,) + WITH_CARRIER for p in to_ship):
        return "labelled"
    return "other"  # a status this app does not know: never called "label ready"


def _shipments_read_at(org_id: int, ws: WhatnotShow) -> datetime | None:
    state = db.session.scalar(
        select(WhatnotSyncState).where(
            WhatnotSyncState.organization_id == org_id,
            WhatnotSyncState.resource == f"shipments:{ws.id}",
        )
    )
    return state.last_ok_at if state is not None else None


def _complete(org_id: int, ws: WhatnotShow) -> bool:
    """The show has ended, and all its orders and all its parcels were read after that."""
    if ws.status != "ended" or ws.ended_at is None:
        return False
    if ws.orders_synced_at is None or ws.orders_synced_at < ws.ended_at:
        return False
    read_at = _shipments_read_at(org_id, ws)
    return read_at is not None and read_at >= ws.ended_at


# An order of a show we have no record of yet is taken to be a show in progress if it is
# this recent (the show list is read every 15 minutes; a show does not run longer).
UNKNOWN_SHOW_IS_LIVE = timedelta(hours=12)


def _in_a_show_still_live(org_id: int, ws: WhatnotShow, shipment_ids: set[str]) -> set[str]:
    """The shipments that also hold a win from ANOTHER show that has not ended. Back-to-back
    shows share a buyer's bundle: a label made for the first would close it while the
    buyer is still winning in the second."""
    if not shipment_ids:
        return set()
    rows = db.session.execute(
        select(
            WhatnotOrderItem.whatnot_shipment_id,
            WhatnotOrder.whatnot_show_id,
            func.max(WhatnotOrder.ordered_at),
        )
        .join(WhatnotOrder, WhatnotOrder.id == WhatnotOrderItem.order_id)
        .where(
            WhatnotOrderItem.organization_id == org_id,
            WhatnotOrderItem.whatnot_shipment_id.in_(shipment_ids),
            WhatnotOrder.whatnot_show_id.is_not(None),
            WhatnotOrder.whatnot_show_id != ws.whatnot_id,
            WhatnotOrder.status.in_(SHIPPABLE),
        )
        .group_by(WhatnotOrderItem.whatnot_shipment_id, WhatnotOrder.whatnot_show_id)
    ).all()
    if not rows:
        return set()
    status = dict(
        db.session.execute(
            select(WhatnotShow.whatnot_id, WhatnotShow.status).where(
                WhatnotShow.organization_id == org_id,
                WhatnotShow.whatnot_id.in_({r[1] for r in rows}),
            )
        ).all()
    )
    recent = datetime.now(UTC) - UNKNOWN_SHOW_IS_LIVE
    out = set()
    for shipment_id, other_show, newest in rows:
        known = status.get(other_show)
        if known in ("ended", "cancelled"):
            continue
        if known is None and (newest is None or newest < recent):
            continue
        out.add(shipment_id)
    return out


def packages(show: Show) -> dict:
    """Every buyer of the show with their package(s) on Whatnot, in pick order (most items
    first, then A-Z, as on the order slips)."""
    return _view(show)[0]


def _view(show: Show) -> tuple[dict, list[tuple[int, str | None]]]:
    """`packages()` and, for mark_shipped, the (order id, tracking) pairs the carrier has."""
    org_id = show.organization_id
    conn = connection.active(org_id)
    ws = db.session.get(WhatnotShow, show.whatnot_show_id) if show.whatnot_show_id else None
    if conn is None or ws is None:
        return {"connected": conn is not None, "matched": False, "buyers": [], "totals": {}}, []
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
    live_elsewhere = _in_a_show_still_live(org_id, ws, shipment_ids)
    recordings = [s.id for s in sync.recordings_of(ws)] or [show.id]
    # A buyer has one order per recording of the show (two laptops: two orders).
    orders: dict[str, list[Order]] = defaultdict(list)
    for o in db.session.scalars(
        select(Order)
        .where(Order.show_id.in_(recordings))
        .options(selectinload(Order.sales))
        .order_by(Order.id)
        .execution_options(populate_existing=True)
    ):
        orders[o.buyer_username.lower()].append(o)
    ended = ws.status == "ended"
    complete = _complete(org_id, ws)
    now = datetime.now(UTC)
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
                    # …and that other show is live now: the bundle is still filling.
                    "liveElsewhere": whatnot_id in live_elsewhere,
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
        state = _state(packs, b["unplaced"], ended, complete)
        for p in packs:
            # Exactly the packages "Make labels" would send for right now.
            p["askable"] = state in ("ready", "second") and _askable(p, now)
        local = orders.get(key, [])
        # The order still waiting to be picked, packed or marked shipped comes first.
        shown = next((o for o in local if o.status.value in ("paid", "picking", "packed")), None)
        shown = shown or (local[-1] if local else None)
        out.append(
            {
                "buyer": b["buyer"] or None,
                "items": b["items"],
                "unplaced": b["unplaced"],
                "packages": packs,
                "state": state,
                "orderId": shown.id if shown else None,
                "orderStatus": shown.status.value if shown else None,
                "_orders": local,
            }
        )
    out.sort(key=lambda b: (-b["items"], (b["buyer"] or "").lower()))
    count = defaultdict(int)
    for b in out:
        count[b["state"]] += 1
    to_ship = _carrier_has(out)
    for b in out:
        del b["_orders"]
    return {
        "connected": True,
        "matched": True,
        "mode": conn.orders_mode,
        "show": {"title": ws.title, "status": ws.status, "ended": ended, "complete": complete},
        "buyers": out,
        "totals": {"buyers": len(out), **count},
        "labelsToMake": sum(1 for b in out for p in b["packages"] if p["askable"]),
        "toMarkShipped": len(to_ship),
    }, to_ship


# ---------- labels ----------


# Why Whatnot did not make a label, as codes the page turns into words. The first three are
# definite: Whatnot answered "no", so no label is being made and asking again is safe.
REFUSED = ("needs_verification", "no_permission", "not_now")
# The request may or may not have reached Whatnot (a timeout, a 5xx): asking again could
# make a second label, so the app waits as long as for one it knows was accepted.
UNKNOWN = "unknown"
# Whatnot makes a label in the background and says nothing when that fails. For this long
# after a request it accepted (or may have), the app does not ask again.
LABEL_IN_FLIGHT = timedelta(minutes=60)


def _label_code(e: WhatnotError) -> str:
    if e.problem == "seller-verification-required":
        return "needs_verification"
    if isinstance(e, Forbidden):
        return "no_permission"
    if isinstance(e, Conflict):
        return "not_now"
    return UNKNOWN


def _askable(package: dict, now: datetime) -> bool:
    """May a label be asked for this package now? Open, shipped (not pickup), nothing held,
    not shared with a show still live, and no request in flight. The request itself
    checks again under the row's lock (_claim)."""
    if package["status"] != OPEN or package["holds"] or package["pickup"]:
        return False
    if package["liveElsewhere"]:
        return False
    asked = when(package["labelRequestedAt"])
    if asked is None or package["labelProblem"] in REFUSED:
        return True
    return now - asked >= LABEL_IN_FLIGHT


def _claim(org_id: int, row_id: int, user_id: int | None) -> bool:
    """Take the right to send ONE label request for this shipment. The row is locked and
    read fresh, every rule is checked on what it holds now, and the claim is committed
    before the request is sent (the atomic-gate rule). Two people pressing the button, a
    job run twice, a worker restarted mid-list: only one of them gets True, and a crash
    between the claim and the request errs toward not asking twice. A hold Whatnot put on
    the parcel since the button was pressed stops it here."""
    row = db.session.scalar(
        select(WhatnotShipment)
        .where(WhatnotShipment.id == row_id, WhatnotShipment.organization_id == org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = datetime.now(UTC)
    ok = (
        row is not None
        and bool(row.whatnot_id)
        and row.api_status == OPEN
        and row.fulfillment_method != "pickup"
        and not row.required_actions
        and (
            row.label_requested_at is None
            or row.label_problem in REFUSED
            or now - row.label_requested_at >= LABEL_IN_FLIGHT
        )
    )
    if ok:
        row.label_requested_at, row.label_requested_by_id, row.label_problem = now, user_id, None
    db.session.commit()  # releases the lock either way
    return ok


def request_labels(show: Show, *, user, buyers: list[str] | None = None) -> dict:
    """Ask Whatnot to make the label of every buyer who is ready for one: the show has
    ended and been read in full, and all their wins are in one open package with nothing
    held. Never for a buyer with two open packages, never for a pickup. Queued as a job
    (Whatnot makes labels in the background); the job claims each shipment before it
    sends, so nothing is asked for twice."""
    view = packages(show)
    if not view["matched"]:
        raise ShippingError("This show is not matched to a show on Whatnot yet.")
    if not view["show"]["ended"]:
        raise ShippingError(
            "Wait until the show has ended. Making a label closes the buyer's bundle: "
            "anything they win afterwards would ship, and be charged shipping, separately."
        )
    if not view["show"]["complete"]:
        raise ShippingError(
            "The show's final orders and packages have not been read from Whatnot yet. "
            "Press Read again from Whatnot, then make the labels."
        )
    wanted = {b.lower().lstrip("@") for b in buyers} if buyers else None
    ids = [
        p["id"]
        for b in view["buyers"]
        if wanted is None or (b["buyer"] or "").lower() in wanted
        for p in b["packages"]
        if p["askable"]
    ][:LABELS_PER_REQUEST]
    if not ids:
        return {"requested": 0}
    jobs.enqueue(
        "whatnot_labels",
        {"org_id": show.organization_id, "shipment_ids": ids, "user_id": user.id},
        org_id=show.organization_id,
    )
    return {"requested": len(ids)}


@jobs.handler("whatnot_labels")
def labels_job(payload: dict) -> None:
    """Send each label request, once. Each shipment is claimed (and the claim committed)
    before its request goes out, so running this job again, or two of them at once, sends
    nothing a second time. A refusal is recorded on the shipment and the rest go on."""
    org_id = payload["org_id"]
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)
    for row_id in payload.get("shipment_ids", []):
        if not _claim(org_id, row_id, payload.get("user_id")):
            continue  # asked for already, labelled meanwhile, or not ours
        whatnot_id = db.session.scalar(
            select(WhatnotShipment.whatnot_id).where(WhatnotShipment.id == row_id)
        )
        problem = None
        try:
            client.request("POST", f"/shipments/{whatnot_id}/generate_label")
        except WhatnotError as e:
            if isinstance(e, Unauthorized):
                connection.note_failure(org_id, e)  # the token itself stopped working
            # A 429 was never processed (the client retried it first): definitely not made.
            problem = "not_now" if isinstance(e, Busy) else _label_code(e)
        if problem is not None:
            db.session.execute(
                update(WhatnotShipment)
                .where(WhatnotShipment.id == row_id)
                .values(label_problem=problem)
            )
        db.session.commit()
    # Whatnot says "queued", not "done": the label arrives by webhook, or at the next read.


_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".corp")


def _safe_url(url) -> str | None:
    """A link Whatnot gave us, fetched only if it is plain https to a named public host:
    no credentials, no address literal in any spelling (dotted, decimal, hex), no local
    name. The names it resolves to are checked again when it is fetched (_public)."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        return None
    if not host or host.endswith(".") or "." not in host or host.endswith(_LOCAL_SUFFIXES):
        return None
    if host == "localhost" or ":" in host:
        return None
    last = host.rsplit(".", 1)[-1]
    if not last.isalpha():  # 127.0.0.1, 127.1, 0x7f.1, 2130706433.: a number, not a name
        return None
    return url


def _public(host: str) -> bool:
    """Every address the name resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except ValueError:
        return False


def _fetch(url: str, *, max_bytes: int = MAX_LABEL_BYTES) -> bytes:
    from flask import current_app

    fetch = current_app.config.get("WHATNOT_FETCH")
    if fetch is not None:
        return fetch(url)
    if current_app.config.get("TESTING"):
        raise RuntimeError("a test tried to download a label: install a WHATNOT_FETCH")
    failed = ShippingError("Whatnot's label could not be downloaded. Try again.")
    if not _public(urlsplit(url).hostname or ""):
        raise failed
    deadline = time.monotonic() + FETCH_SECONDS
    chunks, size = [], 0
    try:
        with requests.get(url, timeout=(5, 15), stream=True, allow_redirects=False) as r:
            if r.status_code != 200:
                raise failed
            for chunk in r.iter_content(64 * 1024):
                size += len(chunk)
                # A response that is too large, or dribbles in forever, is cut off.
                if size > max_bytes or time.monotonic() > deadline:
                    raise failed
                chunks.append(chunk)
    except requests.RequestException:
        raise failed from None
    return b"".join(chunks)


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


def _carrier_has(buyers: list[dict]) -> list[tuple[int, str | None]]:
    """(order id, tracking) for each order of a buyer whose every package is with the
    carrier while the order still waits to be picked, packed or marked shipped. An order
    with an item nobody has settled (not every standing sale paid) is left for a person."""
    from .. import orders as orders_service

    out = []
    for b in buyers:
        if b["state"] != "shipped":
            continue
        tracking = next((p["tracking"] for p in b["packages"] if p["tracking"]), None)
        for order in b["_orders"]:
            if order.status.value in ("paid", "picking", "packed") and orders_service.verified(
                order
            ):
                out.append((order.id, tracking))
    return out


def mark_shipped(show: Show, *, actor_label: str = "Whatnot") -> int:
    """Move to Shipped the orders whose packages the carrier already has. Each order on its
    own: one that cannot be shipped (it changed a moment ago) is skipped, not the rest."""
    from .. import orders as orders_service

    n = 0
    for order_id, tracking in _view(show)[1]:
        order = db.session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if order is None or order.status not in (
            OrderStatus.paid,
            OrderStatus.picking,
            OrderStatus.packed,
        ):
            continue
        try:
            with db.session.begin_nested():
                orders_service.ship_by_carrier(order, tracking=tracking, actor_label=actor_label)
        except (orders_service.OrderError, ValueError):
            continue  # an item still needs a person; the order stays where it is
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
    began = datetime.now(UTC)
    out = sync_recent_shipments(org_id, client, start - sync.SHOW_LEAD)
    # When this show's parcels were last read in full: labels wait for a read that began
    # after the show ended (see _complete).
    connection.sync_state(org_id, f"shipments:{ws.id}").last_ok_at = began
    return out


@jobs.handler("whatnot_sync_shipments")
def sync_shipments_job(payload: dict) -> None:
    org_id = payload["org_id"]
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)
    whatnot_show_id = payload.get("whatnot_show_id")

    def read():
        ws = sync._show_row(org_id, whatnot_show_id)
        if ws is not None:
            return read_show(org_id, client, ws)
        return sync_recent_shipments(org_id, client, datetime.now(UTC) - RECENT)

    sync._run(org_id, "shipments", read)
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
