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
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ...db import db
from ...models import (
    ChatMessage,
    OrderStatus,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShow,
)
from .. import jobs, pipeline, reconcile
from ..catalog import norm_alias
from ..orders import STANDING
from ..reconcile import Plan, Row
from . import catalog, connection, sync

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


# The token no longer reads the shop's orders: a person has to act before any arrive.
BLIND = ("token_rejected", "no_permission")


def decides(show: Show) -> bool:
    """Whether Whatnot's own orders say, with nobody applying them, which of this show's
    wins were paid: the shop applies orders as they arrive, the recording is matched to a
    show on Whatnot, that show is one the app follows (not one over before the shop chose
    to), the token can still read orders, and the orders say who bought them. A decline
    read from the stream then closes nothing (`pipeline.process_show`,
    `supervisor.audit_show`): the win's order will say, and a card declined at the win is
    often paid a minute later. It is flagged for a person instead (`pipeline.flag_decline`).

    False in every other case, where the stream's decline closes the sale as it always did:
    "Compare only", a recording not matched yet, a token Whatnot rejects, orders with no
    buyer (`apply` refuses those, so no order would ever close the sale)."""
    conn = connection.active(show.organization_id)
    if conn is None or conn.orders_mode != "auto" or conn.problem in BLIND:
        return False
    ws = whatnot_show_of(show)
    if ws is None or sync.history(conn, ws):
        return False
    nameless = db.session.scalar(
        select(WhatnotOrder.id)
        .where(
            WhatnotOrder.organization_id == ws.organization_id,
            WhatnotOrder.whatnot_show_id == ws.whatnot_id,
            WhatnotOrder.transaction_type.is_distinct_from(GIVEAWAY),
            func.coalesce(WhatnotOrder.buyer_username, "") == "",
        )
        .limit(1)
    )
    return nameless is None


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
    sku_for = catalog.resolver(ws.organization_id)
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
            row.sku_id, row.listing_off = sku_for(item)
            if not row.buyer:
                info["noBuyer"] += 1
                row.action, row.note = "skip", "Whatnot did not say who bought it"
            elif not row.item:
                row.action, row.note = "skip", "the order names no listing"
            elif status == "unknown":
                row.action, row.note = "skip", "an order state the app does not know"
            rows.append(row)
    _skip_retried(rows)
    return rows, info


def _note_order_time(sale: Sale, row: Row) -> None:
    """The sale's order time is when Whatnot first recorded an order for the win: for a card
    declined and then paid, the declined attempt's time, which is when the lot was won. The
    winner's pick in chat is read from then on (services/sale_chat.py). It only ever moves
    earlier, so reading the orders again settles on the same time."""
    first = row.first_ordered_at or row.ordered_at
    if first is not None and (sale.ordered_at is None or first < sale.ordered_at):
        sale.ordered_at = first


def _skip_retried(rows: list[Row]) -> None:
    """A card declined at the win and then paid on a retry is two orders on Whatnot: a failed
    one, then a paid (or settling) one for the same win (the 2026-11-13 show: 10 of 20 lots).
    The failed order is history, not a decline: left in, it would decline the win's sale while
    the paid order recorded the win a second time. `rows` is oldest first; one pass from the
    newest keeps each buyer's later payments, so the cost is linear in practice."""
    paid_later: dict[str, list[Row]] = {}
    for row in reversed(rows):
        if row.action:  # already left out
            continue
        buyer = row.buyer.lower()
        if row.status in ("paid", "pending"):
            paid_later.setdefault(buyer, []).append(row)
        elif row.status == "failed" and row.ordered_at is not None:
            # The payment nearest after it: with two wins of one listing by one buyer, the
            # declined attempt belongs to the first payment that follows, not the latest.
            for paid in reversed(paid_later.get(buyer, ())):
                # Both times known, so a retry is always within `same_win`'s half hour.
                if paid.ordered_at is not None and pipeline.same_win(
                    row.item, row.price_cents, row.ordered_at,
                    paid.item, paid.price_cents, paid.ordered_at,
                ) is not None:  # fmt: skip
                    # The win's sale may already be tied to the failed order: the payment is its.
                    paid.sale_hint = paid.sale_hint or row.sale_hint
                    # The win happened when the first order was placed, not when a retry paid.
                    paid.first_ordered_at = min(
                        paid.first_ordered_at or row.ordered_at, row.ordered_at
                    )
                    row.action, row.note = "skip", RETRIED
                    break


def _complete(ws: WhatnotShow) -> bool:
    """Every order of the show has been read after it ended: only then does a sale with
    no order mean something."""
    return (
        ws.status == "ended"
        and ws.ended_at is not None
        and ws.orders_synced_at is not None
        and ws.orders_synced_at >= ws.ended_at
    )


# A plan row's action that only Whatnot's orders have: the declined win with no product
# goes back to waiting for one (`pipeline.await_product`).
REOPEN = "reopen"
# Why a failed order is left out: its win was paid in a later order, or the app never had
# a sale for it. The second is a declined payment nobody else would hear of (`_flag_unrecorded`).
RETRIED = "declined, then paid in a later order"
NEVER_RECORDED = "never recorded here, and not paid on Whatnot"
# The review item for a payment Whatnot declined when the app has no sale for the win.
ORDER_DECLINED = "order_declined"
# Items that doubt whether a standing sale was paid: the stream showed its card declined
# (`pipeline.flag_decline`), the AI read as much in chat, or no order was found for it. A
# paid order for the sale answers them.
ANSWERED_BY_PAYMENT = (pipeline.DECLINE_SEEN, "payment_failed", "no_payment_record")

HERE = {
    SaleStatus.cancelled: "cancelled here",
    SaleStatus.payment_failed: "marked payment declined here",
    SaleStatus.fulfilled: "already shipped here",
}
THERE = {
    "paid": "paid on Whatnot",
    "pending": "sold on Whatnot",
    "void": "cancelled on Whatnot",
    "failed": "not paid on Whatnot",
}


def _plan(show: Show, ws: WhatnotShow) -> tuple[Plan, dict, list[Show]]:
    recordings = sync.recordings_of(ws) or [show]
    rows, info = rows_for(ws)
    # A sale already tied to one of these order items may live in a show that is not a
    # recording of this Whatnot show (one laptop recording through two shows): it is still
    # that order's sale, and Whatnot's word on the payment still applies to it.
    hinted = {r.sale_hint for r in rows if r.sale_hint}
    plan = reconcile.plan(recordings[0], rows, shows=recordings, extra_sale_ids=hinted)
    sales = {
        s.id: s
        for s in db.session.scalars(
            select(Sale).where(Sale.id.in_([r.sale_id for r in plan.rows if r.sale_id] or [0]))
        )
    }
    for row in plan.rows:
        sale = sales.get(row.sale_id)
        # An order nobody has a sale for: cancelled or failed, there is nothing to record.
        if row.action == "unmatched" and row.sale_id is None and row.status in ("void", "failed"):
            row.action, row.note = "skip", NEVER_RECORDED
        elif (
            row.action == "unmatched"
            and sale is not None
            and row.status == "paid"
            and sale.status is SaleStatus.payment_failed
            and sale.sku_id is None
            # Its buyer's package already left without it: a person decides (as for a
            # declined sale with a product, which `_repay` refuses once the order shipped).
            and not (sale.order is not None and sale.order.status is OrderStatus.shipped)
        ):
            # Declined before anyone named its product, and paid after all (a retried card
            # whose failed order was applied before the retry went through): nothing was
            # held for it, so it simply waits for its product again. With a product, the
            # engine marks it paid again itself.
            row.action, row.note = REOPEN, "declined, then paid: it waits for its product again"
        elif row.action == "unmatched" and sale is not None:
            # The two records disagree about a sale already closed or shipped here: a
            # person decides. Said in words (the engine's own note is written for a file).
            here = HERE.get(sale.status, "closed here")
            row.note = f"{here}, but {THERE.get(row.status, 'different on Whatnot')}"
        elif (
            row.action == "mark_paid"
            and sale is not None
            and sale.status is SaleStatus.detected
            and sale.sku_id is None
        ):
            if pipeline.listed_sku(show.organization_id, row.sku_id) is not None:
                # Its listing on Whatnot is linked to a product here: applying names it.
                row.note = "its Whatnot listing names the product"
            else:
                # Paid on Whatnot, but nobody has said which product it is: its own review
                # item is already waiting, and naming the product is what marks it paid.
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


def _existing_sale_for(org_id: int, row: Row, taken: set[int]) -> Sale | None:
    """A sale already tied to this order and listing anywhere in the shop (for example in
    a recording that was matched to another show since), not yet used by another item of
    this run: two items of one order with the same title are two sales, not one. A sale
    that stands comes before one that was closed."""
    found = [
        sale
        for sale in db.session.scalars(
            select(Sale)
            .where(Sale.organization_id == org_id, Sale.external_order_id == row.external_id)
            .order_by(Sale.id)
            .with_for_update()
        )
        if sale.id not in taken and pipeline.same_listing(sale.item_text, row.item) is not None
    ]
    found.sort(
        key=lambda s: (s.status not in STANDING, pipeline.same_listing(s.item_text, row.item))
    )
    return found[0] if found else None


def _unclaimed_win(org_id: int, show_ids: list[int], row: Row, taken: set[int]) -> Sale | None:
    """A sale a win line made for this very win, tied to no order yet: the same buyer and
    the same win by `pipeline.same_win` (lot, price, close in time). Looked for across the
    shop, like the win line's own search (`pipeline.claim_order_sale`): the recording that
    holds it may be matched to another show. Also what catches a sale the parser made
    while this run was planning (they are separate transactions)."""
    candidates = db.session.execute(
        select(Sale, ChatMessage.captured_at)
        .outerjoin(ParsedEvent, Sale.event_id == ParsedEvent.id)
        .outerjoin(ChatMessage, ParsedEvent.message_id == ChatMessage.id)
        .where(
            Sale.organization_id == org_id,
            Sale.external_order_id.is_(None),
            Sale.status.in_(pipeline.OPEN + (SaleStatus.paid,)),
            func.lower(Sale.buyer_username) == row.buyer.lower(),
        )
        .order_by(Sale.id)
        .with_for_update(of=Sale)
    ).all()
    best: tuple | None = None
    for sale, won_at in candidates:
        if sale.id in taken:
            continue
        if won_at is None and sale.show_id not in show_ids:
            continue  # nothing says when it was won: only this show's own
        key = pipeline.same_win(
            sale.item_text,
            pipeline.paid_price(sale),
            won_at,
            row.item,
            row.price_cents,
            row.ordered_at,
        )
        if key is not None and (best is None or key < best[0]):
            best = (key, sale)
    return best[1] if best else None


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
    # One at a time per show, and never while the parser is in the middle of the same
    # show: both make sales from the same wins. The parser's own lock is taken for every
    # recording of the show, in a fixed order, before anything is read.
    for show_id in sorted(s.id for s in (sync.recordings_of(ws) or [show])):
        pipeline._lock_show(show_id, wait=True)
    plan, info, recordings = _plan(show, ws)
    if info["noBuyer"]:
        # Without buyers nothing can be matched, and every sale would look unpaid.
        raise OrdersError(
            "Whatnot is not telling the app who bought each order, so they cannot be matched. "
            "The access token needs the permission to read customers."
        )
    primary = recordings[0]
    org_id = primary.organization_id
    show_ids = [s.id for s in recordings]
    taken = {row.sale_id for row in plan.rows if row.sale_id}
    created: list[int] = []

    def record(row: Row) -> bool:
        """A paid (or still settling) order with no sale: record the sale, once."""
        if row.status not in ("paid", "pending"):
            return False
        pipeline._lock_buyer(org_id, row.buyer)
        sale = _existing_sale_for(org_id, row, taken)
        if sale is not None and sale.status not in STANDING:
            # This order's sale exists but was closed here (in a show that is no longer a
            # recording of this one): the two records disagree, so a person decides.
            row.sale_id = sale.id
            row.note = f"{HERE.get(sale.status, 'closed here')}, but {THERE[row.status]}"
            taken.add(sale.id)
            return False
        if sale is None:
            sale = _unclaimed_win(org_id, show_ids, row, taken)
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
                won_at=row.first_ordered_at,
                settled=row.status == "paid",
                sku_id=row.sku_id,
                by_text=not row.listing_off,
            )
            created.append(sale.id)
            row.action = "recorded"
        else:
            row.action = "unchanged"
        _note_order_time(sale, row)
        row.sale_id = sale.id
        taken.add(sale.id)
        return True

    # A win still waiting for its product, whose listing is linked to one here: the listing
    # names it (the decision a person makes in Review, taken by Whatnot's record), and the
    # payment then applies as for any named sale.
    for row in plan.rows:
        if row.action != "mark_paid" or row.sale_id is None or row.sku_id is None:
            continue
        waiting = db.session.get(Sale, row.sale_id)
        if waiting is None or waiting.sku_id is not None:
            continue
        if waiting.status is SaleStatus.detected and not pipeline.name_from_listing(
            waiting, row.sku_id, price_cents=row.price_cents
        ):
            db.session.refresh(waiting)
            if waiting.sku_id is None:  # not named by a person meanwhile either
                row.action, row.note = "unchanged", "waiting for its product to be named"

    for row in plan.rows:
        if row.action != REOPEN:
            continue
        sale = db.session.get(Sale, row.sale_id)
        if sale is None or not pipeline.await_product(sale):
            # Named or closed by a person while the orders were being applied: theirs.
            row.action, row.note = "unchanged", "changed here a moment ago"
        else:
            if row.external_id:
                sale.external_order_id = row.external_id  # the paid order, not the failed one
            if sale.price_cents is None and row.price_cents is not None:
                sale.price_cents = row.price_cents
            if row.sku_id is not None:
                # Its listing is linked to a product here: named at once, like any such win.
                pipeline.name_from_listing(sale, row.sku_id, price_cents=row.price_cents)

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
            _note_order_time(sale, row)
            if sale.external_order_id is None and row.action != "unmatched":
                sale.external_order_id = row.external_id
    _flag_declines(primary, plan, show_ids)
    summary = plan.summary
    if not _complete(ws):
        summary.pop("uncovered", None)
    return {"summary": summary, "created": created, "info": info, "plan": plan}


def _flag_declines(show: Show, plan: Plan, show_ids: list[int]) -> None:
    """A declined payment is always put to a person, and the item clears by itself when the
    payment goes through:
    - a sale Whatnot's order just closed gets the "payment declined" item a decline read
      from the stream leaves (`pipeline.review_decline`);
    - a sale that still stands and that Whatnot says is paid has its "the stream showed the
      card declined" items answered;
    - a failed order with no sale here (`_flag_unrecorded`);
    - a second win of the same listing that the paid order did not cover (`_flag_twin`)."""
    by_itself = decides(show)
    for row in plan.rows:
        sale = db.session.get(Sale, row.sale_id) if row.sale_id else None
        if sale is None:
            continue
        if row.action == "decline" and sale.status is SaleStatus.payment_failed:
            pipeline.review_decline(sale, by_itself=by_itself)
        elif (
            row.status == "paid"
            and row.action in ("unchanged", "mark_paid", REOPEN)
            and sale.status in STANDING
        ):
            for item in db.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.sale_id == sale.id,
                    ReviewItem.status == ReviewStatus.open,
                    ReviewItem.reason.in_(ANSWERED_BY_PAYMENT),
                )
            ):
                item.status = ReviewStatus.resolved
                item.resolution = {"by": "paid on Whatnot"}
                item.resolved_at = datetime.now(UTC)
    _flag_unrecorded(show, plan, show_ids)
    _flag_twin(show, plan)


def _flag_twin(show: Show, plan: Plan) -> None:
    """One buyer, two wins of one listing at one price, a failed order and a paid one: the
    orders alone cannot say whether the paid one is the first win's retry or the second
    win's own payment (`_skip_retried` reads it as a retry). So when such a paid order is
    applied while the buyer has another standing win of that listing with no order of its
    own, that win is put to a person at once, not only when the show is over: one of the
    two was not paid. An order of its own arriving later answers the item."""
    retried = [r for r in plan.rows if r.status == "paid" and r.first_ordered_at and r.sale_id]
    if not retried or not plan.uncovered:
        return
    waiting = db.session.scalars(select(Sale).where(Sale.id.in_(plan.uncovered))).all()
    for row in retried:
        for other in waiting:
            if (
                other.id == row.sale_id
                or other.buyer_username.lower() != row.buyer.lower()
                or pipeline.same_win(
                    other.item_text, pipeline.paid_price(other), pipeline.won_when(other),
                    row.item, row.price_cents, row.ordered_at,
                ) is None
            ):  # fmt: skip
                continue
            pipeline.open_review(
                show.organization_id,
                "no_payment_record",
                f"{other.buyer_username} won {other.item_text or 'this listing'} twice at "
                f"{pipeline.money(row.price_cents)}, and Whatnot has one failed and one paid "
                "order for the two: one of them was not paid. Check which, and mark it declined.",
                show_id=other.show_id,
                sale_id=other.id,
                source="reconcile",
            )


def _flag_unrecorded(show: Show, plan: Plan, show_ids: list[int]) -> None:
    """A payment Whatnot declined for a win the app has no sale for (a shop whose wins reach
    it as orders: a failed order records nothing). There is nothing to close, but a person
    is told, once per order item: an item a person dismissed is never opened again. It
    clears by itself when the win is paid in a later order, or gets a sale here (whose own
    item then says it)."""
    standing: dict[int, Row] = {}
    settled: dict[int, str] = {}
    # A failed order with no time is never paired with its retry (`_skip_retried` has
    # nothing to bound it by): a payment for the same buyer, listing and price answers it.
    paid = {
        (r.buyer.lower(), norm_alias(r.item), r.price_cents)
        for r in plan.rows
        if r.status in ("paid", "pending") and r.action != "skip"
    }
    for row in plan.rows:
        if row.status != "failed" or row.ref is None:
            continue
        untimed_retry = (
            row.ordered_at is None
            and (row.buyer.lower(), norm_alias(row.item), row.price_cents) in paid
        )
        if untimed_retry:
            settled[row.ref] = "paid on Whatnot"
        elif row.action == "skip" and row.sale_id is None and row.note == NEVER_RECORDED:
            standing[row.ref] = row
        else:
            settled[row.ref] = "paid on Whatnot" if row.note == RETRIED else "its sale says so"
    if not standing and not settled:
        return
    known: dict[int, list[ReviewItem]] = {}
    for item in db.session.scalars(
        select(ReviewItem).where(
            ReviewItem.organization_id == show.organization_id,
            ReviewItem.show_id.in_(show_ids),
            ReviewItem.reason == ORDER_DECLINED,
        )
    ):
        known.setdefault((item.proposal or {}).get("orderItem"), []).append(item)
    for ref, row in standing.items():
        if ref in known:
            continue
        pipeline.open_review(
            show.organization_id,
            ORDER_DECLINED,
            f"Whatnot declined {row.buyer}'s payment for {row.quantity} × {row.item} "
            f"({pipeline.money(row.price_cents)}). No sale was recorded for it here, so there "
            "is nothing to undo. If they pay, Whatnot's paid order records the sale and this "
            "clears by itself.",
            show_id=show.id,
            proposal={
                "action": "none",
                "orderItem": ref,
                "orderId": row.external_id,
                "buyer": row.buyer,
                "item": row.item,
                "priceCents": row.price_cents,
            },
            source="reconcile",
            dedupe=False,  # keyed by the order item, checked above
        )
    for ref, why in settled.items():
        for item in known.get(ref, ()):
            if item.status is ReviewStatus.open:
                item.status = ReviewStatus.resolved
                item.resolution = {"by": why}
                item.resolved_at = datetime.now(UTC)


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
    if ws is None or sync.history(conn, ws):
        return  # over before the shop connected: a person may apply it, nothing else does
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
