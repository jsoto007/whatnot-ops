"""Whatnot's orders against the app's sales: compare them, and apply them.

The copy of Whatnot's orders (sync.py) is run through the same engine as an uploaded
payment file (services/reconcile.py): each order item is a row with a buyer, a listing
title, a price and Whatnot's word on the payment. So one tested set of rules decides what
a paid, failed or cancelled order does to a sale, and every change still goes through
`pipeline.set_sale_status()` and the inventory ledger, with Whatnot as the actor.

What differs from a file:
  * a paid order no sale matches IS the sale (Whatnot vouches for it): the app records it,
    exactly like a win, instead of asking a person whether it happened;
  * an order Whatnot has not settled yet ("pending") leaves its sale as it stands;
  * a sale with no order is only called out once the show has ended and every order
    since it began has been read. Before that the record is not complete.

"shadow" (the default) only compares, on the show's page; a person may apply. "auto"
applies as orders arrive. A viewer's chat line still never pays or cancels anything:
this is Whatnot's own record, read with the shop's token.
"""

import logging
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ...db import db
from ...models import Sale, SaleStatus, Show, WhatnotOrder, WhatnotOrderItem, WhatnotShow
from .. import jobs, pipeline, reconcile
from ..reconcile import Plan, Row
from . import connection, sync

log = logging.getLogger(__name__)

# Whatnot's order status → what it means for the payment (reconcile.Row.status).
PAYMENT = {
    "pending": "pending",
    "pending_fulfillment": "paid",
    "completed": "paid",
    "cancelled": "void",
    "failed": "failed",
}
GIVEAWAY = "giveaway"
IMPORT_ID = "whatnot"


class OrdersError(Exception):
    """Why Whatnot's orders cannot be compared or applied right now, in the shop's words."""

    status = 409


def whatnot_show_of(show: Show) -> WhatnotShow | None:
    return db.session.get(WhatnotShow, show.whatnot_show_id) if show.whatnot_show_id else None


def _orders(ws: WhatnotShow) -> list[WhatnotOrder]:
    return db.session.scalars(
        select(WhatnotOrder)
        .where(
            WhatnotOrder.organization_id == ws.organization_id,
            WhatnotOrder.whatnot_show_id == ws.whatnot_id,
        )
        .options(selectinload(WhatnotOrder.items))
        # Oldest first, so the oldest order meets the oldest sale. Whatnot's ids are
        # numbers kept as text: shorter first, then by digits, is their numeric order.
        .order_by(
            WhatnotOrder.ordered_at, func.length(WhatnotOrder.whatnot_id), WhatnotOrder.whatnot_id
        )
    ).all()


def rows_for(ws: WhatnotShow) -> tuple[list[Row], dict]:
    """Whatnot's orders for a show as payment rows, oldest first, and what was left out.
    Giveaways are not sales (the order slips list them as gifts)."""
    rows: list[Row] = []
    info = {"orders": 0, "giveaways": 0, "noBuyer": 0}
    for order in _orders(ws):
        if order.transaction_type == GIVEAWAY:
            info["giveaways"] += 1
            continue
        info["orders"] += 1
        status = PAYMENT.get(order.status, "unknown")
        for item in order.items:
            row = Row(
                buyer=(order.buyer_username or "").lstrip("@"),
                item=item.title,
                quantity=item.quantity,
                price_cents=item.unit_price_cents,
                status=status,
                external_id=order.whatnot_id,
                ref=item.id,
                sale_hint=item.sale_id,
                ordered_at=order.ordered_at,
            )
            if not row.buyer:
                info["noBuyer"] += 1
                row.action, row.note = "skip", "Whatnot did not say who bought it"
            elif not row.item:
                row.action, row.note = "skip", "the order names no listing"
            elif status == "unknown":
                row.action, row.note = "skip", "an order state the app does not know"
            rows.append(row)
    return rows, info


def _complete(ws: WhatnotShow) -> bool:
    """Every order of the show has been read after it ended: only then does a sale with
    no order mean something."""
    return (
        ws.status == "ended"
        and ws.ended_at is not None
        and ws.orders_synced_at is not None
        and ws.orders_synced_at >= ws.ended_at
    )


def _plan(show: Show, ws: WhatnotShow) -> tuple[Plan, dict, list[Show]]:
    recordings = sync.recordings_of(ws) or [show]
    rows, info = rows_for(ws)
    plan = reconcile.plan(recordings[0], rows, shows=recordings)
    unnamed = set(
        db.session.scalars(
            select(Sale.id).where(
                Sale.id.in_([r.sale_id for r in plan.rows if r.action == "mark_paid"] or [0]),
                Sale.status == SaleStatus.detected,
                Sale.sku_id.is_(None),
            )
        )
    )
    for row in plan.rows:
        # An order nobody has a sale for: cancelled or failed, there is nothing to record.
        if row.action == "unmatched" and row.sale_id is None and row.status in ("void", "failed"):
            row.action, row.note = "skip", "never recorded here, and not paid on Whatnot"
        elif row.action == "mark_paid" and row.sale_id in unnamed:
            # Paid on Whatnot, but nobody has said which product it is: its own review item
            # is already waiting, and naming the product is what marks it paid.
            row.action, row.note = "unchanged", "waiting for its product to be named"
    return plan, info, recordings


def compare(show: Show) -> dict:
    """What applying Whatnot's orders to this show would do. Changes nothing."""
    ws = whatnot_show_of(show)
    if ws is None:
        return {"plan": None, "info": None, "complete": False}
    plan, info, _ = _plan(show, ws)
    for row in plan.rows:
        if row.action == "unmatched" and row.sale_id is None:
            # Paid (or settling) on Whatnot with no sale here: applying records the sale.
            row.action, row.note = "record", None
    return {"plan": plan, "info": info, "complete": _complete(ws)}


def _existing_sale_for(org_id: int, row: Row) -> Sale | None:
    """A standing sale already tied to this order and listing anywhere in the shop (for
    example in a recording that was linked to another show since)."""
    for sale in db.session.scalars(
        select(Sale)
        .where(Sale.organization_id == org_id, Sale.external_order_id == row.external_id)
        .order_by(Sale.id)
    ):
        if pipeline._titles_match(sale.item_text, row.item):
            return sale
    return None


def _unclaimed_win(show_ids: list[int], row: Row, taken: set[int]) -> Sale | None:
    """A sale a win line made for this very lot while we were planning (the parser and
    this run are separate transactions): same buyer, title, price and quantity, tied to
    no order yet."""
    for sale in db.session.scalars(
        select(Sale)
        .where(
            Sale.show_id.in_(show_ids),
            Sale.external_order_id.is_(None),
            Sale.status.in_(pipeline.OPEN + (SaleStatus.paid,)),
            func.lower(Sale.buyer_username) == row.buyer.lower(),
        )
        .order_by(Sale.id)
        .with_for_update()
    ):
        if sale.id in taken or sale.quantity != row.quantity:
            continue
        if not pipeline._titles_match(sale.item_text, row.item):
            continue
        if None not in (sale.price_cents, row.price_cents) and sale.price_cents != row.price_cents:
            continue
        return sale
    return None


def apply(show: Show, *, user=None) -> dict:
    """Apply Whatnot's orders to the show's sales, in the caller's transaction."""
    ws = whatnot_show_of(show)
    if ws is None:
        raise OrdersError("This show is not matched to a show on Whatnot yet.")
    if ws.orders_synced_at is None and not db.session.scalar(
        select(WhatnotOrder.id)
        .where(
            WhatnotOrder.organization_id == ws.organization_id,
            WhatnotOrder.whatnot_show_id == ws.whatnot_id,
        )
        .limit(1)
    ):
        raise OrdersError("Whatnot's orders for this show have not been read yet. Try again soon.")
    plan, info, recordings = _plan(show, ws)
    if info["noBuyer"]:
        # Without buyers nothing can be matched, and every sale would look unpaid.
        raise OrdersError(
            "Whatnot is not telling the app who bought each order, so they cannot be matched. "
            "The access token needs the permission to read customers."
        )
    primary = recordings[0]
    show_ids = [s.id for s in recordings]
    taken = {row.sale_id for row in plan.rows if row.sale_id}
    created: list[int] = []

    def record(row: Row) -> bool:
        """A paid (or still settling) order with no sale: record the sale, once."""
        if row.status not in ("paid", "pending"):
            return False
        pipeline._lock_buyer(primary.organization_id, row.buyer)
        sale = _existing_sale_for(primary.organization_id, row)
        if sale is None:
            sale = _unclaimed_win(show_ids, row, taken)
            if sale is not None:
                sale.external_order_id = row.external_id
        if sale is None:
            sale = pipeline.create_sale_from_order(
                primary,
                buyer=row.buyer,
                item_text=row.item,
                quantity=row.quantity,
                price_cents=row.price_cents,
                external_order_id=row.external_id,
                ordered_at=row.ordered_at,
                settled=row.status == "paid",
            )
            created.append(sale.id)
            row.action = "recorded"
        else:
            row.action = "unchanged"
        sale.ordered_at = sale.ordered_at or row.ordered_at
        row.sale_id = sale.id
        taken.add(sale.id)
        return True

    reconcile.apply(
        primary,
        plan,
        actor=pipeline.WHATNOT_ACTOR,
        user=user,
        import_id=IMPORT_ID,
        words=reconcile.WHATNOT,
        on_unmatched=record,
        flag_uncovered=_complete(ws),
    )
    # Remember which sale each order item is, and when it was ordered.
    for row in plan.rows:
        if row.ref is None or row.sale_id is None:
            continue
        item = db.session.get(WhatnotOrderItem, row.ref)
        if item is not None and item.sale_id != row.sale_id:
            item.sale_id = row.sale_id
        sale = db.session.get(Sale, row.sale_id)
        if sale is not None:
            sale.ordered_at = sale.ordered_at or row.ordered_at
            if sale.external_order_id is None and row.action != "unmatched":
                sale.external_order_id = row.external_id
    summary = plan.summary
    if not _complete(ws):
        summary.pop("uncovered", None)
    return {"summary": summary, "created": created, "info": info, "plan": plan}


@jobs.handler("whatnot_apply_show")
def apply_show_job(payload: dict) -> None:
    """ "auto" mode: apply a show's orders after they changed. Skipped quietly when the
    mode was switched back, the show has no place in the app yet, or orders lack buyers."""
    org_id = payload["org_id"]
    conn = connection.active(org_id)
    if conn is None or conn.orders_mode != "auto":
        return
    ws = db.session.scalar(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.whatnot_id == payload["whatnot_show_id"],
        )
    )
    if ws is None:
        return
    show = sync.ensure_recording(ws)
    if show is None:
        return  # too early to tell whether a laptop is recording it; the next order retries
    try:
        result = apply(show)
    except OrdersError as e:
        db.session.rollback()
        log.warning("whatnot orders not applied for show %s: %s", show.id, e)
        return
    changed = {k: v for k, v in result["summary"].items() if k not in ("unchanged", "skip")}
    if changed:
        from .. import audit

        audit.record(
            org_id,
            "whatnot.orders_applied",
            actor_type="whatnot",
            target_type="show",
            target_id=show.id,
            details=result["summary"],
        )


def last_read(ws: WhatnotShow) -> datetime | None:
    return ws.orders_synced_at
