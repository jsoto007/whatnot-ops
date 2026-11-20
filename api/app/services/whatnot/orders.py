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
from datetime import UTC, datetime, timedelta

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
AUCTION = "auction"
IMPORT_ID = "whatnot"


class OrdersError(Exception):
    """Why Whatnot's orders cannot be compared or applied right now, in the shop's words."""

    status = 409


class ShowsChanged(OrdersError):
    """The app's shows of the Whatnot show changed while the apply waited for them (a made
    show folded into a recording): nothing was changed; start again."""

    def __init__(self):
        super().__init__("This show changed just now. Try again in a few seconds.")


def whatnot_show_of(show: Show) -> WhatnotShow | None:
    return db.session.get(WhatnotShow, show.whatnot_show_id) if show.whatnot_show_id else None


# The token no longer reads the shop's orders (or reads another seller's): a person has to
# act before any arrive.
BLIND = ("token_rejected", "no_permission", "wrong_account")


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
    buyer to speak of (`apply` refuses those, so no order would ever close the sale)."""
    conn = connection.active(show.organization_id)
    if conn is None or conn.orders_mode != "auto" or conn.problem in BLIND:
        return False
    ws = whatnot_show_of(show)
    if ws is None or sync.history(conn, ws):
        return False
    return not blind(ws)


# More nameless orders than this share (one in five) of a show's orders mean Whatnot is not
# telling the app who buys at all (a token that lost its permission to read customers), not
# that a buyer's account is gone.
NAMELESS_ONE_IN = 5


def is_blind(nameless: int, total: int) -> bool:
    return nameless > 0 and nameless * NAMELESS_ONE_IN > total


def blind(ws: WhatnotShow) -> bool:
    """The show's orders mostly say nothing about who bought them: none can be matched to a
    sale, so nothing is applied and no sale is called out as unpaid. A few nameless orders
    (a closed account) are left out one by one instead (`rows_for`)."""
    o = WhatnotOrder
    unnamed = func.coalesce(o.buyer_username, "") == ""
    nameless, total = db.session.execute(
        select(func.count().filter(unnamed), func.count()).where(
            o.organization_id == ws.organization_id,
            o.whatnot_show_id == ws.whatnot_id,
            o.transaction_type.is_distinct_from(GIVEAWAY),
        )
    ).one()
    return is_blind(nameless, total)


def _chunks(ids: list[int], size: int = 1000):
    """`ids` in slices small enough for one `IN (…)` each."""
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


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


# How many of the orders left out a Review item and the Payments tab name (the rest are
# counted): enough to find them on Whatnot, short enough to read.
NAMED_LEFT_OUT = 3


def _order_label(order: WhatnotOrder) -> str:
    """An order as Seller Hub shows it ("Order #…"), with its buyer when Whatnot named one."""
    buyer = (order.buyer_username or "").lstrip("@")
    return f"#{order.whatnot_id} from {buyer}" if buyer else f"#{order.whatnot_id}"


def rows_for(ws: WhatnotShow) -> tuple[list[Row], dict]:
    """Whatnot's orders for a show as payment rows, oldest first, and what was left out.
    Giveaways are not sales (the order slips list them as gifts)."""
    rows: list[Row] = []
    # noBuyer / unreadable: items left out for want of a buyer / of something the app can
    # read (a listing title, a state it knows). noBuyerPaid: of the first, those of a paid or
    # settling order: only they can hide a sale's payment (`apply`). noItems: orders the app
    # holds no items for (Whatnot answered the order before its items; `sync.repair_items`
    # asks again): they give no row at all; noItemsPaid: those of them a buyer paid or is
    # paying, which can hide a sale's payment just as a paid order with no buyer can
    # (`_may_hide`). leftOut: the orders with an unreadable item or
    # no items, oldest first; leftOutOrders names the first `NAMED_LEFT_OUT` of them.
    info = {
        "orders": 0,
        "giveaways": 0,
        "noBuyer": 0,
        "noBuyerPaid": 0,
        "unreadable": 0,
        "noItems": 0,
        "noItemsPaid": 0,
        "leftOut": 0,
        "leftOutOrders": [],
        "heldDeclines": 0,
        "held": [],
        "blind": False,
    }
    nameless = 0
    sku_for = catalog.resolver(ws.organization_id)

    def left_out(order: WhatnotOrder) -> None:
        info["leftOut"] += 1
        if len(info["leftOutOrders"]) < NAMED_LEFT_OUT:
            info["leftOutOrders"].append(_order_label(order))

    for order in _orders(ws):
        if order.transaction_type == GIVEAWAY:
            info["giveaways"] += 1
            continue
        info["orders"] += 1
        nameless += not order.buyer_username
        status = PAYMENT.get(order.status, "unknown")
        if not order.items:
            info["noItems"] += 1
            info["noItemsPaid"] += status in ("paid", "pending")
            left_out(order)
            continue
        unread = False  # this order has an item left out as unreadable (named once)
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
                auction=order.transaction_type == AUCTION,
                listing=item.product_id,
                discount_cents=order.discount_cents,
                order_lines=len(order.items),
            )
            row.sku_id, row.listing_off = sku_for(item)
            if not row.buyer:
                info["noBuyer"] += 1
                info["noBuyerPaid"] += status in ("paid", "pending")
                row.action, row.note = "skip", "Whatnot did not say who bought it"
            elif not row.item:
                info["unreadable"] += 1
                row.action, row.note = "skip", "the order names no listing"
                unread = True
            elif status == "unknown":
                info["unreadable"] += 1
                row.action, row.note = "skip", "an order state the app does not know"
                unread = True
            rows.append(row)
        if unread:
            left_out(order)
    info["blind"] = is_blind(nameless, info["orders"])
    _skip_retried(rows)
    held = [r for r in rows if r.note == MAYBE_RETRIED]
    info["heldDeclines"] = len(held)
    info["held"] = [f"{r.buyer}'s {r.item}" for r in held[:3]]  # named, so a person can check
    return rows, info


def _note_order_time(sale: Sale, row: Row) -> None:
    """The sale's order time is when Whatnot first recorded an order for the win: for a card
    declined and then paid, the declined attempt's time, which is when the lot was won. The
    winner's pick in chat is read from then on (services/sale_chat.py). It only ever moves
    earlier, so reading the orders again settles on the same time."""
    first = row.first_ordered_at or row.ordered_at
    if first is not None and (sale.ordered_at is None or first < sale.ordered_at):
        sale.ordered_at = first


def _derive_retied(sales: list[Sale]) -> None:
    """Sales just tied to their Whatnot order (its line, or its order number) after
    `reconcile.apply` derived the orders: a win with no product whose question a person
    ignored, on a listing "Not one of ours", is settled only once that tie exists
    (`orders.settled_unnamed_sales` reads it), so its order is derived again now, not at
    the next read. One query for all of them; nothing for a sale that has its product."""
    from .. import orders

    waiting = [s for s in sales if s.status is SaleStatus.detected and s.sku_id is None]
    derive = {s.order.id: s.order for s in waiting if s.order is not None}.values()
    if not derive:
        return
    db.session.flush()
    settled = orders.settled_unnamed_ids([s for o in derive for s in o.sales])
    for order in derive:
        orders.refresh(order, settled=settled)


def _unit_off(row: Row) -> tuple[int, int] | None:
    """What the row's order can have taken off one unit (`pipeline.unit_discount`): its price
    is stated after that, so two attempts at one win may differ by it."""
    return pipeline.unit_discount(row.discount_cents, row.quantity, row.order_lines)


def _retry_key(failed: Row, paid: Row) -> tuple | None:
    """Whether `paid` can be the retry of the declined attempt `failed`: the same win by
    `pipeline.same_win`, each order's price read after its own discount ($25.00 declined,
    $23.70 paid after $1.30 off is one win), and never across two listings."""
    if failed.listing and paid.listing and failed.listing != paid.listing:
        return None
    return pipeline.same_win(
        failed.item, failed.price_cents, failed.ordered_at,
        paid.item, paid.price_cents, paid.ordered_at,
        discount_a=_unit_off(failed), discount_b=_unit_off(paid),
    )  # fmt: skip


def _skip_retried(rows: list[Row]) -> None:
    """A card declined at the win and then paid on a retry is two orders on Whatnot: a failed
    one, then a paid (or settling) one for the same win (the 2026-11-13 show: 10 of 20 lots).
    The failed order is history, not a decline: left in, it would decline the win's sale while
    the paid order recorded the win a second time. `rows` is oldest first; one pass from the
    newest keeps each buyer's later payments, so the cost is linear in practice.

    The two prices may differ by a discount one of the orders carries (`_retry_key`). A
    payment at exactly the declined price is still the surer retry: it is taken over a nearer
    one that agrees only through its discount.

    A paid order with no buyer (a closed account, or a name Whatnot stopped giving) may be
    that retry: the failed order is then NOT a decline, because declining would take back a
    sale the buyer paid (`MAYBE_RETRIED`; a person is told in Review). Only where it can
    really be one (`_hold_for_nameless`): placed after the failed orders, and one such payment
    holds back one buyer's attempts at one win at most."""
    paid_later: dict[str, list[Row]] = {}
    unpaired: list[Row] = []
    for row in reversed(rows):
        if row.action:  # already left out
            continue
        buyer = row.buyer.lower()
        if row.status in ("paid", "pending"):
            paid_later.setdefault(buyer, []).append(row)
        elif row.status == "failed":
            if row.ordered_at is not None:
                # The payment nearest after it: with two wins of one listing by one buyer,
                # the declined attempt belongs to the first payment that follows, not the
                # latest. Two listings are two auctions (`_retry_key`), and both times are
                # known, so a retry is always within `same_win`'s half hour.
                retry = None
                for paid in reversed(paid_later.get(buyer, ())):
                    if paid.ordered_at is None:
                        continue
                    key = _retry_key(row, paid)
                    if key is None:
                        continue
                    if key[1] == 0:  # the same price exactly: the surest
                        retry = paid
                        break
                    retry = retry or paid  # only a discount makes them agree: unless exact
                if retry is not None:
                    paid = retry
                    # The win's sale may already be tied to the failed order: the payment
                    # is its.
                    if paid.sale_hint is None and row.sale_hint is not None:
                        paid.sale_hint, paid.hint_from_retry = row.sale_hint, True
                    # The win happened when the first order was placed, not when a retry
                    # paid.
                    paid.first_ordered_at = min(
                        paid.first_ordered_at or row.ordered_at, row.ordered_at
                    )
                    row.action, row.note = "skip", RETRIED
            if not row.action:
                unpaired.append(row)
    _hold_for_nameless(rows, unpaired)


def _hold_for_nameless(rows: list[Row], failed: list[Row]) -> None:
    """Failed orders no named payment answered, held back as `MAYBE_RETRIED` where a paid
    order with no buyer can be their retry: the same win (`_retry_key`: each price after its
    own order's discount, never across two listings) and ONE buyer's. A card declined twice
    and then paid is two failed orders and one payment, so a payment holds every attempt of
    that one buyer's win once one of them was placed before it (a retry comes later), and
    never the failed orders of two buyers (one payment cannot have paid two buyers' wins):
    the buyer whose attempt is the closest before it (payments taken oldest first). The
    others are declines as usual.

    A payment with no order time cannot be placed after anything. It holds the failed
    orders of its win only when they are all one buyer's (as before 2026-11-19: undoing a
    sale the buyer paid is worse than a decline a person chases); with two buyers' it holds
    none. Payments with a time are taken first."""
    nameless = [r for r in rows if not r.buyer and r.status in ("paid", "pending")]
    timed = sorted((r for r in nameless if r.ordered_at is not None), key=lambda r: r.ordered_at)
    untimed = [r for r in nameless if r.ordered_at is None]

    def same_win(a: Row, b: Row) -> bool:
        return _retry_key(a, b) is not None

    def hold(held: list[Row]) -> None:
        for row in held:
            row.action, row.note = "skip", MAYBE_RETRIED

    for paid in timed:
        same = [row for row in failed if not row.action and same_win(row, paid)]
        before = [
            row for row in same if row.ordered_at is not None and row.ordered_at < paid.ordered_at
        ]
        if not before:
            # A failed order with no time cannot be placed before anything: held as for a
            # payment with no time, when such orders are all one buyer's.
            unplaced = [row for row in same if row.ordered_at is None]
            if unplaced and len({row.buyer.lower() for row in unplaced}) == 1:
                hold(unplaced)
            continue
        closest = max(before, key=lambda r: r.ordered_at)
        buyer = closest.buyer.lower()
        # That buyer's attempts at the one win: each also the same win as the closest. Once
        # the payment may be this buyer's, an attempt Whatnot dates after it (or not at all)
        # is held too: the payment cannot claim the sale, so that attempt would decline it.
        hold(
            [
                row
                for row in same
                if row.buyer.lower() == buyer and (row is closest or same_win(row, closest))
            ]
        )
    for paid in untimed:
        could_be = [row for row in failed if not row.action and same_win(row, paid)]
        if could_be and len({row.buyer.lower() for row in could_be}) == 1:
            hold(could_be)


def _complete(ws: WhatnotShow) -> bool:
    """Every order of the show has been read after it ended (Whatnot's end, or the one
    showend.py works out when Whatnot gives none): only then does a sale with no order mean
    something."""
    from . import showend

    end = showend.end_of(ws)
    return end is not None and ws.orders_synced_at is not None and ws.orders_synced_at >= end


# A show Whatnot gave no end time for, and no laptop saw end, is over by the app's own clock
# (showend.py: 12 hours after it began). Once that end is older than this, its applies no
# longer make a sale give way to another win of its lot (`_one_win`): the first pass after the
# 12-hour rule came in reads a month of such shows in full, and days after a show nobody is
# watching it; a person decides instead. The same days the timer reads a show's parcels.
GIVE_WAY_WITHIN = timedelta(days=3)


def _ended_long_ago(ws: WhatnotShow) -> bool:
    """The show's end, whoever said it (Whatnot, the 12-hour rule, a laptop's word), is more
    than `GIVE_WAY_WITHIN` old. An apply this late never makes a paid sale give way by
    itself: cancelling a sale and moving stock days after the show, with no person involved,
    is worse than asking. The win keeps its "no order on Whatnot" item and a person decides.
    Keyed on the end's age, not on who ended it: an end time Whatnot fills in days later, or
    a first full read of an old show, must not undo what a person was asked to decide."""
    from . import showend

    now = datetime.now(UTC)
    at = showend.end_of(ws, now)
    return at is not None and at < now - GIVE_WAY_WITHIN


# A plan row's action that only Whatnot's orders have: the declined win with no product
# goes back to waiting for one (`pipeline.await_product`).
REOPEN = "reopen"
# Why a failed order is left out: its win was paid in a later order, or the app never had
# a sale for it. The second is a declined payment nobody else would hear of (`_flag_unrecorded`).
RETRIED = "declined, then paid in a later order"


def _may_hide(info: dict) -> bool:
    """A paid (or settling) order the app cannot tie to a buyer or to a listing yet may be
    the payment of a sale that looks unpaid: while one exists, no sale of the show is called
    out as having no order and none gives way to another win of its lot."""
    return bool(info["noBuyerPaid"] or info.get("noItemsPaid"))


MAYBE_RETRIED = "declined, and an order with no buyer may be its retry"
NEVER_RECORDED = "never recorded here, and not paid on Whatnot"
# Declined twice here (paid again by a person in between) and now paid on Whatnot: the
# ledger holds one "paid again" round per sale, so the payment is recorded as a new sale.
RECORDED_AGAIN = "declined twice here, then paid on Whatnot: recorded as a new sale"
# Paid on Whatnot, no product named yet: the win waits for its product (the winner's pick).
WAITING = "waiting for its product to be named"
# The review item for a payment Whatnot declined when the app has no sale for the win.
ORDER_DECLINED = "order_declined"
# The item for a standing sale with no order among Whatnot's (once the show is over and read
# in full): any order for the sale answers it, one still settling too.
NOT_LISTED = "no_payment_record"
# Items that doubt whether a standing sale was paid: the stream showed its card declined
# (`pipeline.flag_decline`), the AI read as much in chat, or no order was found for it. A
# paid order for the sale answers them.
ANSWERED_BY_PAYMENT = (
    pipeline.DECLINE_SEEN,
    pipeline.LOT_WON_TWICE,
    "payment_failed",
    NOT_LISTED,
)

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


def _own_win_first(rows: list[Row], show_ids: list[int]) -> None:
    """A paid order that was paired with an earlier failed one would go to that failed
    order's sale and pay it again. That is right for a retried card. It is wrong when the
    buyer has ANOTHER win of the listing still standing with no order of its own: the
    payment is that win's, and the failed order's sale stays declined (one buyer, the same
    listing twice, the first card declined and never retried). The pairing cannot see
    sales; this can. The payment's price is after its order's discount, as in the pairing
    (`_retry_key`), so a win at the price before it is still that same win."""
    retried = [r for r in rows if r.hint_from_retry and r.sale_hint is not None]
    if not retried:
        return
    buyers = {r.buyer.lower() for r in retried}
    standing = db.session.scalars(
        select(Sale)
        .where(
            Sale.show_id.in_(show_ids),
            Sale.status.in_(STANDING),
            Sale.external_order_id.is_(None),
            func.lower(Sale.buyer_username).in_(buyers),
        )
        .options(selectinload(Sale.event).selectinload(ParsedEvent.message))
    ).all()
    for row in retried:
        for sale in standing:
            if (
                sale.id != row.sale_hint
                and sale.buyer_username.lower() == row.buyer.lower()
                and pipeline.same_win(
                    sale.item_text, pipeline.paid_price(sale), pipeline.won_when(sale),
                    row.item, row.price_cents, row.ordered_at, discount_b=_unit_off(row),
                ) is not None
            ):  # fmt: skip
                row.sale_hint, row.hint_from_retry = None, False
                break


def _plan(show: Show, ws: WhatnotShow) -> tuple[Plan, dict, list[Show]]:
    recordings = sync.recordings_of(ws) or [show]
    rows, info = rows_for(ws)
    # A sale already tied to one of these order items may live in a show that is not a
    # recording of this Whatnot show (one laptop recording through two shows): it is still
    # that order's sale, and Whatnot's word on the payment still applies to it.
    _own_win_first(rows, [s.id for s in recordings])
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
        elif (
            row.action == "unmatched"
            and sale is not None
            and row.status == "paid"
            and pipeline.gave_way(sale)
        ):
            # The buyer's sale gave way to another win of the lot, and now Whatnot has a
            # paid order of the buyer's own for it (it arrived late, or the listing sold
            # twice): the cancelled sale cannot come back, so the win is recorded again.
            row.sale_id, row.sale_hint = None, None
            row.note = "its first sale gave way to another win; recorded again"
        elif (
            row.action == "unmatched"
            and sale is not None
            and row.status == "paid"
            and sale.status is SaleStatus.payment_failed
            and sale.sku_id is not None
            and pipeline._round(sale) == pipeline.REPAID
            and pipeline.needs_round(sale)
            and pipeline.may_record_again(sale)
        ):
            # Restored by a person (a decline Whatnot had no order for yet), then declined by
            # Whatnot's failed order, and now paid on Whatnot: Whatnot decides, so the win
            # counts. Its units are back on the shelf and its one "paid again" round is used,
            # so the payment is recorded as a new sale (as for a sale that gave way), and
            # the declined one stays closed. Never for a payment file: only Whatnot's word.
            row.sale_id, row.sale_hint = None, None
            row.note = RECORDED_AGAIN
            plan.record_again[row.ref] = sale.id
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
                row.action, row.note = "unchanged", WAITING
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
            discount_b=pipeline.unit_discount(row.discount_cents, row.quantity, row.order_lines),
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
    locked = sorted(s.id for s in (sync.recordings_of(ws) or [show]))
    for show_id in locked:
        pipeline._lock_show(show_id, wait=True)
    plan, info, recordings = _plan(show, ws)
    if not {s.id for s in recordings} <= set(locked):
        # A show was folded into a recording while this waited: sales made now would go
        # into a show whose parse this does not hold. Taking that lock now, out of order,
        # could deadlock; the caller starts again (the job at once, a person by pressing).
        raise ShowsChanged()
    if info["blind"]:
        # Without buyers nothing can be matched, and every sale would look unpaid.
        raise OrdersError(
            "Whatnot is not telling the app who bought these orders, so they cannot be "
            "matched. Ask your developer to check the connection to Whatnot."
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
        again_id = plan.record_again.get(row.ref) if row.ref is not None else None
        again = db.session.get(Sale, again_id) if again_id else None
        if again is not None and again.external_order_id == row.external_id:
            # The declined sale stays closed: this paid order is a sale of its own, and it
            # takes the order's id (one sale per order item on the slips and the tiles).
            again.external_order_id = None
            if sale is not None and sale.id == again.id:
                sale = None
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
                # The product named for the declined sale is this win's too, unless the
                # Whatnot listing itself is linked to a product (that link decides).
                named=(
                    again.sku
                    if again is not None and pipeline.listed_sku(org_id, row.sku_id) is None
                    else None
                ),
            )
            created.append(sale.id)
            row.action = "recorded"
        else:
            row.action = "unchanged"
        if again is not None and sale.id != again.id:
            pipeline.settle_recorded_again(again, sale)
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
                row.action, row.note = "unchanged", WAITING

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

    # A paid win waiting for its product must keep its "which product is that?" question:
    # it is the only way it gets named. One left with none (ignored before "Ignore" was
    # refused for it) gets it back here; never one on a listing a person marked "none of
    # ours", which may be ignored for good (`pipeline.must_name` decides, row by row).
    waiting = {
        row.sale_id
        for row in plan.rows
        if row.action == "unchanged"
        and row.note == WAITING
        and row.sale_id is not None
        and not row.listing_off
    }
    if waiting:  # the few with no open question are found in one query, not one per row
        asked = set(
            db.session.scalars(
                select(ReviewItem.sale_id).where(
                    ReviewItem.sale_id.in_(waiting),
                    ReviewItem.status == ReviewStatus.open,
                    ReviewItem.reason == "unmatched_item",
                )
            )
        )
        # One query says which must be named; `reconcile.apply` below derives their orders.
        pipeline.ask_for_products(list(reconcile.load_sales(waiting - asked).values()))

    reconcile.apply(
        primary,
        plan,
        actor=pipeline.WHATNOT_ACTOR,
        user=user,
        import_id=IMPORT_ID,
        words=reconcile.WHATNOT,
        on_unmatched=record,
        flag_uncovered=_complete(ws) and not _may_hide(info),
    )
    # Remember which sale each order item is, and when it was ordered. The items and sales
    # are read in one query each, not one per row.
    tied = [row for row in plan.rows if row.ref is not None and row.sale_id is not None]
    items = (
        {
            i.id: i
            for i in db.session.scalars(
                select(WhatnotOrderItem).where(WhatnotOrderItem.id.in_({r.ref for r in tied}))
            )
        }
        if tied
        else {}
    )
    sales = reconcile.load_sales(r.sale_id for r in tied)
    retied: set[int] = set()
    for row in tied:
        item = items.get(row.ref)
        if item is not None and item.sale_id != row.sale_id:
            item.sale_id = row.sale_id
            retied.add(row.sale_id)
        sale = sales.get(row.sale_id)
        if sale is not None:
            _note_order_time(sale, row)
            if sale.external_order_id is None and row.action != "unmatched":
                sale.external_order_id = row.external_id
                retied.add(sale.id)
    _derive_retied([sales[i] for i in retied if i in sales])
    del items, sales  # the steps below read what they need themselves
    _flag_declines(primary, plan, show_ids)
    # While an order that may be paid has no buyer, or no items yet, no sale is called out as
    # unpaid and none gives way to another win of its lot: that order may be the one that
    # pays it.
    whole = _complete(ws) and not _may_hide(info)
    # A show over only by the app's clock, days ago: no sale gives way by itself. A win with
    # no order stays called out as such ("no payment record"), and a person decides.
    gave_way = _one_win(primary, plan, show_ids, whole and not _ended_long_ago(ws))
    # A sale that gave way is no longer "a sale with no order": it is settled.
    plan.uncovered = [i for i in plan.uncovered if i not in gave_way]
    summary = plan.summary
    if gave_way:
        summary["gave_way"] = len(gave_way)
    if not whole:
        summary.pop("uncovered", None)
    _flag_left_out(primary, info, show_ids)
    return {"summary": summary, "created": created, "info": info, "plan": plan}


def _one_win(show: Show, plan: Plan, show_ids: list[int], complete: bool) -> set[int]:
    """An auction has one win (`pipeline.auction_of`), and Whatnot's paid order for the lot
    says who has it.

    - What was open about ANOTHER buyer's declined payment for that lot is answered.
    - Once every order of the show has been read after it ended (`complete`), every other
      sale still standing for the lot with no order of its own gives way: another buyer's
      (the lot was run again after their card was declined), or a second copy of the same
      win. Before that a missing order may simply not have arrived, and a sale is never
      cancelled on a guess; it is the stream that asks a person meanwhile. Nor for a show
      whose end is more than three days old (`_ended_long_ago`): the caller
      passes `complete` False, and a win with no order keeps its "no payment record" item.

    Only auction orders that are paid, only titles that carry a lot number, and only sales
    won within half an hour of the order: a "buy it now" listing sells many times, a title
    with no number names no one auction, and lot numbers start again in the next show a
    recording runs into. Returns the sales that gave way."""
    has_order = {r.sale_id for r in plan.rows if r.sale_id and r.status in ("paid", "pending")}
    candidates = [
        row
        for row in plan.rows
        if row.auction
        and row.status == "paid"
        and row.sale_id
        and row.action not in ("skip", "unmatched")
        and pipeline.auction_of(row.item) is not None
    ]
    sales = reconcile.load_sales(row.sale_id for row in candidates)
    winners = []
    for row in candidates:
        sale = sales.get(row.sale_id)
        if sale is not None and sale.status in STANDING:
            winners.append((row, sale))
    del sales
    if not winners:
        return set()
    gone: set[int] = set()
    pool = (
        db.session.scalars(
            select(Sale)
            .where(Sale.show_id.in_(show_ids), Sale.status.in_(pipeline.CLOSABLE))
            .options(selectinload(Sale.event).selectinload(ParsedEvent.message))
            .order_by(Sale.id)
        ).all()
        if complete
        else []
    )
    # The pool by title: only a sale with the very same title (`same_listing` 0) can be
    # another win of the lot, so each winner looks at those alone, in the same order.
    by_title: dict[str, list[Sale]] = {}
    for other in pool:
        if other.external_order_id:
            continue
        key = norm_alias(other.item_text or "")
        if key:
            by_title.setdefault(key, []).append(other)
    # Open "declined, no sale here" items of these shows, read once (none are made below),
    # by title: only the very same title (`same_listing` 0) can be the winner's auction.
    declined_by_title: dict[str, list[ReviewItem]] = {}
    for item in db.session.scalars(
        select(ReviewItem)
        .where(
            ReviewItem.organization_id == show.organization_id,
            ReviewItem.show_id.in_(show_ids),
            ReviewItem.reason == ORDER_DECLINED,
            ReviewItem.status == ReviewStatus.open,
        )
        .order_by(ReviewItem.id)
    ):
        key = norm_alias(str((item.proposal or {}).get("item") or ""))
        if key:
            declined_by_title.setdefault(key, []).append(item)
    # Whether any other buyer's declined payment is still open here: when none is (the usual
    # case), there is nothing for `answer_declines_of` to answer.
    any_declined = (
        db.session.scalar(
            select(ReviewItem.id)
            .join(Sale, ReviewItem.sale_id == Sale.id)
            .where(
                Sale.show_id.in_(show_ids),
                Sale.status == SaleStatus.payment_failed,
                ReviewItem.status == ReviewStatus.open,
                ReviewItem.reason == "payment_declined",
            )
            .limit(1)
        )
        is not None
    )
    for row, sale in winners:
        title = norm_alias(row.item or "")
        for other in by_title.get(title, ()):
            if other.id == sale.id or other.id in has_order or other.id in gone:
                continue
            if other.external_order_id or pipeline.same_listing(other.item_text, row.item) != 0:
                continue
            if pipeline.same_win(
                other.item_text, None, pipeline.won_when(other), row.item, None, row.ordered_at
            ) is None or pipeline.won_when(other) is None or row.ordered_at is None:  # fmt: skip
                continue  # not known to be the same auction (another show's lot number)
            pipeline.give_way(
                other, winner=row.buyer, actor=pipeline.WHATNOT_ACTOR, where=" on Whatnot"
            )
            gone.add(other.id)
        if any_declined:
            pipeline.answer_declines_of(show_ids, row.item, winner=row.buyer, at=row.ordered_at)
        for item in declined_by_title.get(title, ()):
            if item.status != ReviewStatus.open:
                continue  # answered for an earlier winner of this run
            said = item.proposal or {}
            then = said.get("orderedAt")
            if (
                str(said.get("buyer") or "").lower() != row.buyer.lower()
                and pipeline.same_listing(said.get("item"), row.item) == 0
                # The same auction, not the same lot number an hour on (or in the next show).
                and then
                and row.ordered_at is not None
                and abs(datetime.fromisoformat(then) - row.ordered_at) <= pipeline.SAME_WIN_WINDOW
            ):
                item.status = ReviewStatus.resolved
                item.resolution = {"by": pipeline.TAKEN_OVER}
                item.resolved_at = datetime.now(UTC)
    return gone


def _flag_declines(show: Show, plan: Plan, show_ids: list[int]) -> None:
    """A declined payment is always put to a person, and the item clears by itself when the
    payment goes through:
    - a sale Whatnot's order just closed gets the "payment declined" item a decline read
      from the stream leaves (`pipeline.review_decline`);
    - a sale that still stands and that Whatnot says is paid has its "the stream showed the
      card declined" items answered;
    - a sale that still stands and that Whatnot has an order for that is still settling has
      its "not among Whatnot's orders" item answered (only that one: whether it is paid is
      still open, and a decline of that order is put to a person as any other);
    - a failed order with no sale here (`_flag_unrecorded`);
    - a second win of the same listing that the paid order did not cover (`_flag_twin`)."""
    by_itself = decides(show)
    sales = reconcile.load_sales(row.sale_id for row in plan.rows)
    answered: list[int] = []  # standing sales Whatnot says are paid
    listed: list[int] = []  # standing sales Whatnot has an order for, still settling
    for row in plan.rows:
        sale = sales.get(row.sale_id) if row.sale_id else None
        if sale is None:
            continue
        if row.action == "decline" and sale.status is SaleStatus.payment_failed:
            pipeline.review_decline(sale, by_itself=by_itself)
        elif (
            row.status == "paid"
            and row.action in ("unchanged", "mark_paid", REOPEN)
            and sale.status in STANDING
        ):
            answered.append(sale.id)
        elif row.status == "pending" and row.action == "unchanged" and sale.status in STANDING:
            listed.append(sale.id)
    # Their open doubts about the payment, in one query for all of them.
    for ids, reasons, by in (
        (answered, ANSWERED_BY_PAYMENT, "paid on Whatnot"),
        (listed, (NOT_LISTED,), THERE["pending"]),
    ):
        for chunk in _chunks(ids):
            for item in db.session.scalars(
                select(ReviewItem).where(
                    ReviewItem.sale_id.in_(chunk),
                    ReviewItem.status == ReviewStatus.open,
                    ReviewItem.reason.in_(reasons),
                )
            ):
                item.status = ReviewStatus.resolved
                item.resolution = {"by": by}
                item.resolved_at = datetime.now(UTC)
    del sales
    _flag_unrecorded(show, plan, show_ids)
    _flag_twin(show, plan)


def _flag_twin(show: Show, plan: Plan) -> None:
    """One buyer, two wins of one listing at one price, a failed order and a paid one: the
    orders alone cannot say whether the paid one is the first win's retry or the second
    win's own payment (`_skip_retried` reads it as a retry). So when such a paid order is
    applied while the buyer has another standing win of that listing with no order of its
    own, that win is put to a person at once, not only when the show is over: one of the
    two was not paid. An order of its own arriving later answers the item. The paid order's
    price is after its discount, as in the pairing (`_retry_key`); the item names the win's
    own price when the win knows it."""
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
                    row.item, row.price_cents, row.ordered_at, discount_b=_unit_off(row),
                ) is None
            ):  # fmt: skip
                continue
            won_for = pipeline.paid_price(other)
            pipeline.open_review(
                show.organization_id,
                "no_payment_record",
                f"{other.buyer_username} won {other.item_text or 'this listing'} twice at "
                f"{pipeline.money(row.price_cents if won_for is None else won_for)}, and "
                "Whatnot has one failed and one paid "
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
            settled[row.ref] = {
                RETRIED: "paid on Whatnot",
                MAYBE_RETRIED: "may be paid on Whatnot",
            }.get(row.note, "its sale says so")
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
                "orderedAt": row.ordered_at.isoformat() if row.ordered_at else None,
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


LEFT_OUT = "orders_left_out"


def _named(info: dict) -> str:
    """The orders left out, the first few by number and buyer, then how many more."""
    named = info["leftOutOrders"]
    more = info["leftOut"] - len(named)
    return ", ".join(named) + (f" and {more} more" if more > 0 else "")


def _flag_left_out(
    show: Show, info: dict | None, show_ids: list[int], *, why: str | None = None
) -> None:
    """Tell a person that some of Whatnot's orders were not applied, and why in the shop's
    words: no buyer (a closed account), nothing the app can read, no items yet, or (`why`)
    the whole show refused. The orders left out for something unreadable or for want of
    items are named, the first few by Seller Hub's order number, the rest counted. One open
    item per show, rewritten as the figures change and resolved when nothing is left out;
    the names keep the orders' own order, so applying the same orders again says the same
    thing (a notice a person dismissed is not opened again for it). Never blames the access
    token for a few orders."""
    if why is None:
        parts = []
        if info and info["noBuyer"]:
            n = info["noBuyer"]
            parts.append(f"Whatnot did not say who bought {n} item{'s' if n != 1 else ''}")
        if info and info["unreadable"]:
            n = info["unreadable"]
            parts.append(
                f"{n} item{'s' if n != 1 else ''} named no listing or an order state the app "
                "does not know"
            )
        if info and info.get("noItems"):
            n = info["noItems"]
            parts.append(f"Whatnot listed no items for {n} order{'s' if n != 1 else ''}")
        summary = None
        if parts:
            summary = (
                "; ".join(parts) + ". They are left out; every other order of the show was applied."
            )
            if info.get("leftOut"):
                summary += f" Check {_named(info)} on Whatnot."
            if info["heldDeclines"]:
                n = info["heldDeclines"]
                summary += (
                    f" {n} declined payment{'s were' if n != 1 else ' was'} not undone ("
                    + ", ".join(info["held"])
                    + (", …" if n > len(info["held"]) else "")
                    + "): a payment with no buyer may be the retry that paid. Check them."
                )
            if _may_hide(info):
                what = " or ".join(
                    w
                    for w, n in (
                        ("no buyer", info["noBuyerPaid"]),
                        ("no items", info.get("noItemsPaid")),
                    )
                    if n
                )
                summary += (
                    f" While a paid order has {what}, sales with no order are not called out "
                    "for this show."
                )
    else:
        summary = f"None of this show's orders could be applied. {why}"
    items = db.session.scalars(
        select(ReviewItem).where(
            ReviewItem.organization_id == show.organization_id,
            ReviewItem.show_id.in_(show_ids),
            ReviewItem.reason == LEFT_OUT,
        )
    ).all()
    open_items = [i for i in items if i.status is ReviewStatus.open]
    if summary is None:
        for item in open_items:
            item.status = ReviewStatus.resolved
            item.resolution = {"by": "every order read and applied"}
            item.resolved_at = datetime.now(UTC)
        return
    if open_items:
        open_items[0].summary = summary
        return
    if any(i.summary == summary and i.resolved_by_id is not None for i in items):
        return  # a person already said "got it" to these very figures: not asked again
    pipeline.open_review(
        show.organization_id,
        LEFT_OUT,
        summary,
        show_id=show.id,
        proposal={"action": "none"},
        source="reconcile",
        dedupe=False,  # one per show, checked above
    )


# How long an apply whose shows kept changing (or whose made show was merged away) waits
# before it runs again: the merge that changed them is over in well under this.
APPLY_AGAIN_AFTER_S = 15


def _apply_again(org_id: int, whatnot_show_id: str) -> None:
    """Queue the apply of this show once more, a little later (the same job `sync.queue_apply`
    queues: one waiting per show), instead of giving up on orders nobody else would apply."""
    jobs.enqueue(
        "whatnot_apply_show",
        {"org_id": org_id, "whatnot_show_id": whatnot_show_id},
        org_id=org_id,
        dedupe_key=f"whatnot_apply:{org_id}:{whatnot_show_id}",
        delay_s=APPLY_AGAIN_AFTER_S,
    )
    db.session.commit()


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
    whatnot_show_id = payload["whatnot_show_id"]
    for attempt in range(3):
        show = sync.ensure_recording(ws)
        if show is None:
            return  # too early to tell whether a laptop is recording it; the next order retries
        show_id = show.id
        try:
            result = apply(show)
        except ShowsChanged:
            db.session.rollback()  # a made show folded meanwhile: once more, on what is there
            if attempt == 2:
                log.warning(
                    "whatnot orders not applied for show %s: its shows kept changing; queued again",
                    whatnot_show_id,
                )
                _apply_again(org_id, whatnot_show_id)
                return
            continue
        except OrdersError as e:
            db.session.rollback()
            # Read again after the rollback: a made show may have been folded into a
            # recording (and deleted) while this ran. Then the next run says it there.
            show = db.session.get(Show, show_id)
            if show is None:
                log.info(
                    "whatnot orders for show %s not applied: its made show was merged; "
                    "queued again",
                    whatnot_show_id,
                )
                _apply_again(org_id, whatnot_show_id)
                return
            log.warning("whatnot orders not applied for show %s: %s", show_id, e)
            # Not only a log line: a person is told in Review (it clears when they apply).
            _flag_left_out(show, None, [s.id for s in sync.recordings_of(ws) or [show]], why=str(e))
            db.session.commit()
            return
        break
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
    # The show is over: its winners' picks are read once they close (services/picks.py).
    from .. import picks

    db.session.commit()
    picks.schedule(ws)


def last_read(ws: WhatnotShow) -> datetime | None:
    return ws.orders_synced_at
