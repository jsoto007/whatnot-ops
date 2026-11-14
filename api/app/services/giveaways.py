"""Giveaways to assign.

A giveaway Whatnot lists among a show's orders is not a sale: it has no price and stays out
of sales counts and revenue. It still ships in the buyer's box, so a person says which
product it was, from its own list, whenever they know. Stock leaves the shelf when the
product is assigned, as for any sale, and comes back when the product is changed or taken
off: the count follows what the record says went in the box.

Only Whatnot's own orders make one. A viewer's "won the giveaway!" line never does, and the
pick worker never touches these (they are not sales). Everything here reads the orders as
stored: nothing calls Whatnot, and no sale, order or payment is changed.

A giveaway's product is a `GiveawayItem` row keyed by Whatnot's own id of the order line, so
reading the orders again never loses the choice. A changed product closes the row and opens
another: each row has its own key in the stock ledger ("giveaway", row id), so A → B → A
moves stock every time.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import (
    GiveawayItem,
    MovementKind,
    Show,
    Sku,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShow,
)
from . import inventory
from .inventory import Actor
from .whatnot import connection, sync
from .whatnot.orders import GIVEAWAY

SOURCE = "giveaway"
GONE = ("cancelled", "failed")  # Whatnot's word that nothing ships for it
RECENT = timedelta(days=60)  # how far back the list goes
MAX_UNITS = 99


class GiveawayError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _name(sku: Sku) -> str:
    product, variant = sku.product.name.strip(), (sku.name or "").strip()
    if variant and variant.lower() not in (product.lower(), "default"):
        return f"{product} - {variant}"
    return product


def _buyer(order: WhatnotOrder) -> str:
    return (order.buyer_username or "").lstrip("@")


def _lock(org_id: int, whatnot_item_id: str) -> None:
    """One change at a time per giveaway (transaction-scoped), so "is a product on it?" and
    the stock that follows are one step. The open-row unique index is the gate behind it."""
    digest = hashlib.sha256(f"giveaway:{org_id}:{whatnot_item_id}".encode()).digest()
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": int.from_bytes(digest[:8], "big", signed=True)},
    )


def _found(org_id: int, item_id: int) -> tuple[WhatnotOrderItem, WhatnotOrder]:
    row = db.session.execute(
        select(WhatnotOrderItem, WhatnotOrder)
        .join(WhatnotOrder, WhatnotOrder.id == WhatnotOrderItem.order_id)
        .where(
            WhatnotOrderItem.id == item_id,
            WhatnotOrderItem.organization_id == org_id,
            WhatnotOrder.transaction_type == GIVEAWAY,
        )
    ).first()
    if row is None:
        raise GiveawayError("That giveaway is not on Whatnot's list any more.", 404)
    return row[0], row[1]


def _open(org_id: int, whatnot_item_id: str) -> GiveawayItem | None:
    return db.session.scalar(
        select(GiveawayItem).where(
            GiveawayItem.organization_id == org_id,
            GiveawayItem.whatnot_item_id == whatnot_item_id,
            GiveawayItem.closed_at.is_(None),
        )
    )


def _put_back(row: GiveawayItem, *, actor: Actor, why: str) -> None:
    inventory.move(
        row.sku_id,
        MovementKind.restock,
        row.quantity,
        source_type=SOURCE,
        source_id=str(row.id),
        actor=actor,
        reason=why,
    )
    row.closed_at = datetime.now(UTC)
    db.session.flush()  # closed before another row opens: one open row per giveaway


def assign(
    org_id: int, item_id: int, sku: Sku, *, actor: Actor, user_id: int | None = None
) -> dict:
    """Say which product a giveaway was (or change it). The product leaves the shelf; an
    earlier choice goes back. Saying the same product again moves nothing."""
    item, order = _found(org_id, item_id)
    _lock(org_id, item.whatnot_id)
    db.session.refresh(order)
    if order.status in GONE:
        raise GiveawayError("Whatnot cancelled this giveaway, so nothing ships for it.")
    if sku.organization_id != org_id or not sku.active:
        raise GiveawayError("That product is not in use.", 400)
    row = _open(org_id, item.whatnot_id)
    if row is not None and row.sku_id == sku.id:
        return {"changed": False, "from": sku.id, "to": sku.id, "quantity": row.quantity}
    before = row.sku_id if row is not None else None
    if row is not None:
        _put_back(row, actor=actor, why="giveaway product changed")
    quantity = min(max(item.quantity or 1, 1), MAX_UNITS)
    new = GiveawayItem(
        organization_id=org_id,
        whatnot_item_id=item.whatnot_id,
        sku_id=sku.id,
        quantity=quantity,
        assigned_by_id=user_id,
    )
    db.session.add(new)
    db.session.flush()
    for step in (inventory.reserve, inventory.commit):
        step(
            sku.id,
            quantity,
            source_type=SOURCE,
            source_id=str(new.id),
            actor=actor,
            reason="giveaway",
        )
    return {"changed": True, "from": before, "to": sku.id, "quantity": quantity}


def clear(org_id: int, item_id: int, *, actor: Actor) -> dict:
    """Take the product off a giveaway: it goes back on the shelf and the giveaway waits
    again. Allowed for one Whatnot cancelled, which is how its unit comes back."""
    item, _ = _found(org_id, item_id)
    _lock(org_id, item.whatnot_id)
    row = _open(org_id, item.whatnot_id)
    if row is None:
        return {"changed": False, "from": None, "quantity": 0}
    _put_back(row, actor=actor, why="giveaway product removed")
    return {"changed": True, "from": row.sku_id, "quantity": row.quantity}


def _chosen(org_id: int, whatnot_item_ids: list[str]) -> dict[str, GiveawayItem]:
    if not whatnot_item_ids:
        return {}
    rows = db.session.scalars(
        select(GiveawayItem)
        .where(
            GiveawayItem.organization_id == org_id,
            GiveawayItem.whatnot_item_id.in_(whatnot_item_ids),
            GiveawayItem.closed_at.is_(None),
        )
        .options(selectinload(GiveawayItem.sku).selectinload(Sku.product))
    ).all()
    return {r.whatnot_item_id: r for r in rows}


def _items(org_id: int, *where) -> list[tuple[WhatnotOrderItem, WhatnotOrder]]:
    return [
        (item, order)
        for item, order in db.session.execute(
            select(WhatnotOrderItem, WhatnotOrder)
            .join(WhatnotOrder, WhatnotOrder.id == WhatnotOrderItem.order_id)
            .where(
                WhatnotOrder.organization_id == org_id,
                WhatnotOrder.transaction_type == GIVEAWAY,
                *where,
            )
            .order_by(WhatnotOrder.ordered_at, WhatnotOrderItem.id)
        ).all()
    ]


def for_show(show: Show) -> list[dict]:
    """A show's giveaways as Whatnot lists them, oldest first, for its order slips: who won,
    and the product a person chose (None while it waits). Only for the recording that holds
    the show (its first), so a show recorded on two laptops prints each giveaway once. One
    Whatnot cancelled does not ship and is left out."""
    if not show.whatnot_show_id:
        return []
    ws = db.session.get(WhatnotShow, show.whatnot_show_id)
    if ws is None:
        return []
    recordings = sync.recordings_of(ws)
    if not recordings or recordings[0].id != show.id:
        return []
    found = [
        (item, order)
        for item, order in _items(
            show.organization_id, WhatnotOrder.whatnot_show_id == ws.whatnot_id
        )
        if order.status not in GONE and _buyer(order)
    ]
    chosen = _chosen(show.organization_id, [item.whatnot_id for item, _ in found])
    out = []
    for item, order in found:
        row = chosen.get(item.whatnot_id)
        out.append(
            {
                "id": item.id,
                "order": order.whatnot_id,
                "buyer": _buyer(order),
                "quantity": row.quantity if row else min(max(item.quantity or 1, 1), MAX_UNITS),
                "product": _name(row.sku) if row else None,
            }
        )
    return out


def listing(org_id: int) -> dict:
    """ "Giveaways to assign": the recent giveaways of the shows the app follows, the ones
    still waiting for a product first (newest show first), then the ones a person named.
    One Whatnot cancelled is listed only while a product is still on it, to be taken off."""
    conn = connection.active(org_id)
    since = datetime.now(UTC) - RECENT
    found = _items(org_id, WhatnotOrder.ordered_at >= since)
    shows: dict[str, Show | None] = {}

    def show_of(whatnot_show_id: str | None) -> Show | None:
        if not whatnot_show_id:
            return None
        if whatnot_show_id not in shows:
            ws = db.session.scalar(
                select(WhatnotShow).where(
                    WhatnotShow.organization_id == org_id,
                    WhatnotShow.whatnot_id == whatnot_show_id,
                )
            )
            # A show over before the shop chose to follow Whatnot's orders is history.
            followed = ws is not None and not (conn is not None and sync.history(conn, ws))
            recordings = sync.recordings_of(ws) if followed else []
            shows[whatnot_show_id] = recordings[0] if recordings else None
        return shows[whatnot_show_id]

    chosen = _chosen(org_id, [item.whatnot_id for item, _ in found])
    rows = []
    for item, order in found:
        show = show_of(order.whatnot_show_id)
        row = chosen.get(item.whatnot_id)
        cancelled = order.status in GONE
        if show is None or not _buyer(order) or (cancelled and row is None):
            continue
        rows.append(
            {
                "id": item.id,
                "buyer": _buyer(order),
                "title": item.title or "Giveaway",
                "quantity": row.quantity if row else min(max(item.quantity or 1, 1), MAX_UNITS),
                "showId": show.id,
                "showDate": show.show_date.isoformat(),
                "wonAt": order.ordered_at.isoformat() if order.ordered_at else None,
                "cancelled": cancelled,
                "product": (
                    {
                        "id": row.sku.id,
                        "code": row.sku.code,
                        "name": row.sku.name,
                        "productName": row.sku.product.name,
                    }
                    if row
                    else None
                ),
            }
        )
    # Waiting first; inside each group the newest show first, then the order they were won.
    rows.sort(key=lambda r: (r["product"] is not None, -_day(r["showDate"]), r["wonAt"] or ""))
    waiting = sum(1 for r in rows if r["product"] is None)
    return {"giveaways": rows, "waiting": waiting}


def _day(iso: str) -> int:
    return datetime.fromisoformat(iso).toordinal()
