"""Deleting what a laptop recorded for a show (owner only).

A `Show` row is one laptop's recording of a show. What that laptop collected is its chat
lines (and the wins it read off Whatnot's video player), the lines the parser read from
them, and the sales, orders and Review questions made from those lines. Deleting a
recording's laptop data removes exactly that and nothing that came from Whatnot itself:
its shows, orders, payments, shipments, ledger and the giveaways' products all stay, and
so does every sale tied to a Whatnot order (`external_order_id`): one the app made from
an order, or a win line that an order (or a payment file) has since vouched for. Such a
sale only loses its chat line; a person's "Ignore" of its "which product is that?"
question stays with it (it may settle the win, `orders.settled_unnamed_sales`). A
recording that still holds one, or a question about a Whatnot order or a payment file,
stays too, as a show with no laptop recording.

A win the laptop recorded that another laptop's recording of the same stream also saw is
not lost: when the other recording surely has the same win (the same buyer, the same lot
and price, within ten minutes, read from a host/platform line) and no sale of its own for
it, the sale MOVES there with its product, status and stock, and that line becomes its
evidence. A looser match, or a copy already waiting in Review, stops the delete until a
person decides it there.

Stock moves only through the usual paths: every other standing laptop sale is closed with
`pipeline.set_sale_status`, so a reservation is released and a sale paid at the win and
still on the shelf is put back, each through the ledger. A sale already in a package
(picked, packed or shipped) is not put back: as with any cancel after picking, whether it
is on the shelf is a person's call, and the "take it out of the package" question that
cancel opens stays in Review (without its sale).

Refused while the laptop may still be recording (chat in the last ten minutes) and while
the show's chat is being read. The recording's key goes into `deleted_recordings`: ingest
drops a later upload of it, so a laptop's outbox cannot bring the chat back.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import (
    AiCall,
    ChatMessage,
    DeletedRecording,
    EventKind,
    Job,
    JobStatus,
    Order,
    OrderStatus,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotLot,
    WhatnotShipment,
)
from ..models.orders import FULFILLMENT
from . import audit, orders, pipeline
from .inventory import Actor

# Chat this recent means the laptop may still be recording: deleting now would have its
# next upload start the recording again.
RECORDING_QUIET = timedelta(minutes=10)
# How every "possible duplicate" question names the sale it may be a copy of.
TWIN_SALE = re.compile(r"\bsale #(\d+)")
REASON = "laptop data deleted"
MOVED = "the other recording's sale moved here"
STANDING = (SaleStatus.detected, SaleStatus.reserved, SaleStatus.paid, SaleStatus.fulfilled)
# Only these `pipeline.same_win` ranks move a sale without a person: the same lot title, or
# the same title once the lot number is set aside. A looser match asks a person.
SURE_RANK = 1
# A host/platform or video-player line; anything less is not a win until a person says so.
SURE_LINE = 0.8
# Queued work about a recording's chat: pointless once the chat is gone.
CHAT_JOBS = ("parse_show", "ai_audit_show")
# "Take it out of the package" notes: they stand until a person has dealt with that unit.
PACKAGE_NOTES = ("closed_after_picking", pipeline.SWAPPED_AFTER_PICKING)


class RecordingError(Exception):
    """Why the laptop data cannot be deleted now, in the shop's words."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def was_deleted(org_id: int, show_key: str) -> bool:
    """The owner deleted this recording's laptop data: a late upload of it is dropped."""
    return (
        db.session.scalar(
            select(DeletedRecording.id).where(
                DeletedRecording.organization_id == org_id, DeletedRecording.show_key == show_key
            )
        )
        is not None
    )


def _laptop_sales(show: Show, *, lock: bool = False) -> list[Sale]:
    """The recording's sales that only its chat vouches for: made from a line here and not
    tied to any Whatnot order. A sale with an order (made from it, or a line an order or a
    payment file has since matched) is Whatnot's record and is kept."""
    q = (
        select(Sale)
        .where(
            Sale.show_id == show.id,
            Sale.event_id.is_not(None),
            Sale.external_order_id.is_(None),
        )
        .options(selectinload(Sale.event).selectinload(ParsedEvent.message))
        .order_by(Sale.id)
    )
    return list(db.session.scalars(q.with_for_update(of=Sale) if lock else q))


@dataclass
class Plan:
    # Standing laptop sales another recording surely also saw: (sale, that recording's line).
    moves: list[tuple[Sale, ParsedEvent]] = field(default_factory=list)
    # Wins that may be in another recording too and need a person first.
    ask: int = 0
    # Of those, wins already in a package here (picked, packed or shipped).
    packed: int = 0


def _plan(show: Show, sales: list[Sale]) -> Plan:
    """Which standing laptop sales move to another recording of the same stream, and how
    many wins need a person first. Looks at the other recordings that were running while
    these wins happened, at their win lines with no sale of their own."""
    plan = Plan()
    standing = [s for s in sales if s.status in STANDING]
    if not standing:
        return plan
    by_buyer: dict[str, list[Sale]] = {}
    for sale in standing:
        by_buyer.setdefault((sale.buyer_username or "").lower(), []).append(sale)
    times = [t for s in standing if (t := pipeline.won_when(s)) is not None]
    window = pipeline.PLAYER_DUPLICATE_WINDOW
    copies: list[ParsedEvent] = []
    if times:
        lo, hi = min(times) - window, max(times) + window
        others = select(Show.id).where(
            Show.organization_id == show.organization_id,
            Show.id != show.id,
            Show.started_at <= hi,
            Show.updated_at >= lo,
        )
        copies = list(
            db.session.scalars(
                select(ParsedEvent)
                .where(
                    ParsedEvent.show_id.in_(others),
                    ParsedEvent.kind == EventKind.sale,
                    # A line a viewer could have typed is not a win until a person says so;
                    # its own Review question (below) is how it can stop a delete.
                    ParsedEvent.confidence >= SURE_LINE,
                    func.lower(ParsedEvent.buyer_username).in_(list(by_buyer)),
                    ~exists().where(Sale.event_id == ParsedEvent.id),
                )
                .options(selectinload(ParsedEvent.message))
                .order_by(ParsedEvent.id)
            )
        )
    pairs = sorted(
        (key, ev.id, sale.id, ev, sale)
        for ev in copies
        if ev.message is not None
        for sale in by_buyer.get((ev.buyer_username or "").lower(), [])
        if (
            key := pipeline.same_win(
                sale.item_text,
                sale.price_cents,
                pipeline.won_when(sale),
                ev.item_text,
                ev.price_cents,
                ev.message.captured_at,
                window=window,
            )
        )
        is not None
    )
    used_ev: set[int] = set()
    used_sale: set[int] = set()
    for key, ev_id, sale_id, ev, sale in pairs:
        if ev_id in used_ev or sale_id in used_sale:
            continue
        used_ev.add(ev_id)
        used_sale.add(sale_id)
        sure = key[0] <= SURE_RANK
        if sure and _in_package(sale):
            # Its box here is being packed or has left: moving it would make a new, unpacked
            # order on the other recording for a unit already handled.
            choice = _when_packed(sale)
            if choice == "move":
                plan.moves.append((sale, ev))
            elif choice == "ask":
                plan.ask += 1
                plan.packed += 1
            continue  # "close": closed here like any other laptop sale
        target = db.session.scalar(
            select(Order.status).where(
                Order.show_id == ev.show_id,
                func.lower(Order.buyer_username) == (sale.buyer_username or "").lower(),
            )
        )
        if sure and target not in FULFILLMENT:
            plan.moves.append((sale, ev))
        else:
            plan.ask += 1  # a loose match, or the buyer's box there is already being packed
    # A copy already waiting in another recording's Review, that no move settles.
    ids = {s.id: s for s in standing}
    for item in db.session.scalars(
        select(ReviewItem).where(
            ReviewItem.organization_id == show.organization_id,
            ReviewItem.status == ReviewStatus.open,
            or_(ReviewItem.show_id.is_(None), ReviewItem.show_id != show.id),
            or_(ReviewItem.sale_id.in_(list(ids)), ReviewItem.reason == "possible_duplicate"),
        )
    ):
        if item.event_id in used_ev:
            continue
        if item.sale_id in ids:
            plan.ask += 1
            continue
        ev = db.session.get(ParsedEvent, item.event_id) if item.event_id else None
        buyer = (ev.buyer_username if ev is not None else "").lower()
        named = {int(n) for n in TWIN_SALE.findall(item.summary or "")} & ids.keys()
        if any((ids[n].buyer_username or "").lower() == buyer for n in named):
            plan.ask += 1
    return plan


def _when_packed(sale: Sale) -> str:
    """What happens to a laptop sale already picked, packed or shipped (`_in_package`) when
    another laptop's recording surely saw the same win: "move" it there, "ask" a person
    first (the delete is refused), or "close" it here like any other laptop sale (its stock
    is not counted back and its "take it out of the package" note stays in Review).

    Never "move": the move would make a new, unpacked order on the other recording for a
    unit already in a box (packed twice). While its box is still being picked or packed, a
    person finishes it first ("ask"). Once shipped the unit is gone and Whatnot tracks the
    sale itself (an order Whatnot vouched for is never a laptop sale here), so the
    laptop-only record closes like any other ("close")."""
    shipped = sale.status is SaleStatus.fulfilled or (
        sale.order is not None and sale.order.status is OrderStatus.shipped
    )
    return "close" if shipped else "ask"


def _in_package(sale: Sale) -> bool:
    """Picked, packed or shipped: cancelling it does not put it back on the shelf."""
    return sale.status is SaleStatus.fulfilled or (
        sale.status is SaleStatus.paid and pipeline._in_fulfilment(sale)
    )


def _blockers(show: Show, plan: Plan, messages: int) -> list[str]:
    out = []
    if messages and datetime.now(UTC) - show.updated_at < RECORDING_QUIET:
        out.append(
            "A laptop sent chat for this show in the last 10 minutes, so it may still be "
            "recording. Stop the recording on the laptop, then try again in 10 minutes."
        )
    if plan.packed:
        n = plan.packed
        out.append(
            f"{n} {'win' if n == 1 else 'wins'} here {'is' if n == 1 else 'are'} being picked "
            "or packed, and another laptop's recording of this show also has "
            f"{'it' if n == 1 else 'them'}. Finish packing (or take "
            f"{'it' if n == 1 else 'them'} out of the box) first, then delete."
        )
    if plan.ask > plan.packed:
        n = plan.ask - plan.packed
        out.append(
            f"{n} {'win' if n == 1 else 'wins'} here may also be in another laptop's "
            "recording of this show. Open that show's Review and record "
            f"{'it' if n == 1 else 'each'} as a sale there if {'it is' if n == 1 else 'they are'} "
            "a real win, then delete this one; otherwise a real win could be lost."
        )
    return out


def _laptop_question(show: Show, laptop_ids: list[int]):
    """A Review question made from this recording's chat lines or about one of its laptop
    sales. Any other question on the show (about a sale tied to a Whatnot order, a Whatnot
    order itself, a payment file) is not the laptop's and stays."""
    return or_(
        ReviewItem.message_id.in_(select(ChatMessage.id).where(ChatMessage.show_id == show.id)),
        ReviewItem.event_id.in_(select(ParsedEvent.id).where(ParsedEvent.show_id == show.id)),
        ReviewItem.sale_id.in_(laptop_ids),
    )


def _kept_decision(laptop_ids: list[int]):
    """A person's "Ignore" of the "which product is that?" question about a sale that stays
    (one tied to a Whatnot order). It may have been made from this recording's chat line,
    but it is the sale's decision, and whether that win is settled with no product reads it
    (`orders.settled_unnamed_sales`): it stays, detached from the line that goes."""
    return (
        ReviewItem.sale_id.is_not(None)
        & ReviewItem.sale_id.not_in(laptop_ids)
        & (ReviewItem.reason == "unmatched_item")
        & (ReviewItem.status == ReviewStatus.dismissed)
    )


def _messages(show: Show) -> int:
    return (
        db.session.scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.show_id == show.id)
        )
        or 0
    )


def preview(show: Show) -> dict:
    """What deleting the recording's laptop data would remove, move and keep, and what
    stops it now. Changes nothing."""
    messages = _messages(show)
    sales = _laptop_sales(show)
    plan = _plan(show, sales)
    moved = {s.id for s, _ in plan.moves}
    closing = [s for s in sales if s.status in STANDING and s.id not in moved]
    laptop_ids = [s.id for s in sales if s.id not in moved]
    kept = (
        db.session.scalar(
            select(func.count())
            .select_from(Sale)
            .where(
                Sale.show_id == show.id,
                or_(Sale.event_id.is_(None), Sale.external_order_id.is_not(None)),
            )
        )
        or 0
    )
    # Orders left with nothing once this recording's sales are closed or moved away.
    emptied = {s.order_id for s in sales if s.order_id is not None}
    staying = set(
        db.session.scalars(
            select(Sale.order_id).where(
                Sale.order_id.in_(emptied), Sale.id.not_in([s.id for s in sales] or [0])
            )
        )
    )
    reviews = (
        db.session.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(
                ReviewItem.show_id == show.id,
                _laptop_question(show, laptop_ids),
                # Kept: a moved sale's questions go with it, a package note stays.
                ReviewItem.sale_id.not_in(list(moved) or [0]) | ReviewItem.sale_id.is_(None),
                ~(
                    ReviewItem.reason.in_(PACKAGE_NOTES)
                    & (ReviewItem.status == ReviewStatus.open)
                    & ReviewItem.sale_id.in_(laptop_ids or [0])
                ),
                ~_kept_decision(laptop_ids),  # a kept sale's "Ignore" stays with it
            )
        )
        or 0
    )
    blockers = _blockers(show, plan, messages)
    if not messages and not sales:
        blockers.append("Nothing on this show came from a laptop.")
    return {
        "messages": messages,
        "sales": len(laptop_ids),
        "standing": len(closing),
        "inPackage": sum(1 for s in closing if _in_package(s)),
        "moved": len(plan.moves),
        "orders": len(emptied - staying),
        "reviews": reviews,
        "keptFromWhatnot": kept,
        "blockers": blockers,
    }


def _move(sale: Sale, ev: ParsedEvent) -> None:
    """The other recording's line becomes the sale's evidence, and the sale joins its
    buyer's order there. Nothing moves in stock: the sale, its products and its ledger
    keys stay the same."""
    old = sale.order
    sale.show_id = ev.show_id
    sale.event_id = ev.id
    sale.order = None
    sale.order_id = None
    db.session.flush()
    orders.attach(sale)
    if old is not None:
        db.session.refresh(old)
        orders.refresh(old)
    # The other laptop's questions about its copy of this win are settled by the move...
    for item in db.session.scalars(
        select(ReviewItem).where(
            ReviewItem.event_id == ev.id,
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.sale_id.is_distinct_from(sale.id),
        )
    ):
        item.status = ReviewStatus.dismissed
        item.resolution = {"by": MOVED, "saleId": sale.id}
    # ...and the sale's own open questions (which product, a declined card) go with it,
    # onto that line: a person still decides them there.
    db.session.execute(
        update(ReviewItem)
        .where(ReviewItem.sale_id == sale.id, ReviewItem.status == ReviewStatus.open)
        .values(show_id=ev.show_id, message_id=ev.message_id, event_id=ev.id)
    )


def delete_laptop_data(org_id: int, show_id: int, *, user) -> dict:
    """Delete the recording's laptop data (see the module docstring). The caller commits;
    raises RecordingError, having changed nothing, when it may not be done now."""
    show = db.session.scalar(
        select(Show).where(Show.id == show_id, Show.organization_id == org_id).with_for_update()
    )
    if show is None:
        raise RecordingError("show not found", 404)
    if not pipeline._lock_show(show.id):
        raise RecordingError("This show's chat is being read right now. Try again in a minute.")
    # The buyer locks the parser and Whatnot's orders take before they make or claim a sale,
    # so neither acts on a sale this is moving or closing. The parser takes them in chat
    # order, so the two can deadlock: Postgres then fails one, and the route says "try again".
    buyers = db.session.scalars(
        select(func.lower(Sale.buyer_username))
        .where(Sale.show_id == show.id, Sale.event_id.is_not(None))
        .distinct()
    ).all()
    for buyer in sorted(buyers):
        pipeline._lock_buyer(org_id, buyer)
    messages = _messages(show)
    sales = _laptop_sales(show, lock=True)
    plan = _plan(show, sales)
    blockers = _blockers(show, plan, messages)
    if blockers:
        raise RecordingError(blockers[0])
    if not messages and not sales:
        raise RecordingError("Nothing on this show came from a laptop.")

    # Orders these sales are on now: tidied at the end (a move can empty one too).
    emptied = {s.order_id for s in sales if s.order_id is not None}
    for sale, ev in plan.moves:
        _move(sale, ev)
    moved = {s.id for s, _ in plan.moves}
    sales = [s for s in sales if s.id not in moved]
    actor = Actor.user(user)
    cancelled = in_package = 0
    for sale in sales:
        if sale.status not in STANDING:
            continue
        in_package += _in_package(sale)
        pipeline.set_sale_status(sale, SaleStatus.cancelled, reason=REASON, actor=actor)
        cancelled += 1
    db.session.flush()

    laptop_ids = [s.id for s in sales]
    our_events = select(ParsedEvent.id).where(ParsedEvent.show_id == show.id)
    if laptop_ids:
        # "Take it out of the package" notes outlive the sale: a person still has a unit
        # to deal with. They keep their words and lose only their links.
        db.session.execute(
            update(ReviewItem)
            .where(
                ReviewItem.sale_id.in_(laptop_ids),
                ReviewItem.reason.in_(PACKAGE_NOTES),
                ReviewItem.status == ReviewStatus.open,
            )
            .values(sale_id=None, event_id=None, message_id=None, show_id=None)
        )
        # Closed questions elsewhere that name one of these sales keep their history.
        db.session.execute(
            update(ReviewItem)
            .where(
                ReviewItem.sale_id.in_(laptop_ids),
                or_(ReviewItem.show_id.is_(None), ReviewItem.show_id != show.id),
            )
            .values(sale_id=None)
        )
    # A kept sale's "Ignore" stays with the sale, on its show, without the line that goes.
    for item in db.session.scalars(
        select(ReviewItem)
        .where(_laptop_question(show, laptop_ids), _kept_decision(laptop_ids))
        .options(selectinload(ReviewItem.sale))
    ):
        item.event_id = item.message_id = None
        item.show_id = item.sale.show_id
    db.session.flush()
    gone = delete(ReviewItem).where(_laptop_question(show, laptop_ids))
    reviews = db.session.execute(gone).rowcount
    if laptop_ids:
        # Their products (sale_items) go with them; Whatnot's order lines that pointed at
        # one keep their own data and lose only the link (ON DELETE SET NULL).
        db.session.execute(delete(Sale).where(Sale.id.in_(laptop_ids)))
    # A sale tied to a Whatnot order keeps everything but its line here (on this show, or
    # one an order made elsewhere that a line here claimed).
    db.session.execute(update(Sale).where(Sale.event_id.in_(our_events)).values(event_id=None))
    db.session.expire_all()
    removed_orders = 0
    for order in db.session.scalars(select(Order).where(Order.id.in_(emptied)).order_by(Order.id)):
        if order.sales:
            orders.refresh(order)  # what is left on it came from Whatnot
        else:
            db.session.delete(order)
            removed_orders += 1
    db.session.flush()
    db.session.execute(delete(ParsedEvent).where(ParsedEvent.show_id == show.id))
    # The AI's call log and the Seller Hub rows the laptop read stay; only the link goes.
    db.session.execute(update(AiCall).where(AiCall.show_id == show.id).values(show_id=None))
    db.session.execute(update(WhatnotLot).where(WhatnotLot.show_id == show.id).values(show_id=None))
    db.session.execute(
        update(WhatnotShipment).where(WhatnotShipment.show_id == show.id).values(show_id=None)
    )
    db.session.execute(delete(ChatMessage).where(ChatMessage.show_id == show.id))
    db.session.execute(
        delete(Job).where(
            Job.kind.in_(CHAT_JOBS),
            Job.status == JobStatus.queued,
            Job.payload["show_id"].as_integer() == show.id,
        )
    )
    db.session.execute(
        insert(DeletedRecording)
        .values(organization_id=org_id, show_key=show.show_key, deleted_by_id=user.id)
        .on_conflict_do_nothing(constraint="uq_deleted_recordings_org_key")
    )
    left = db.session.scalar(
        select(
            exists().where(Sale.show_id == show.id)
            | exists().where(ReviewItem.show_id == show.id)
            | exists().where(Order.show_id == show.id)
        )
    )
    details = {
        "date": show.show_date.isoformat(),
        "messages": messages,
        "sales": len(sales),
        "cancelled": cancelled,
        "inPackage": in_package,
        "moved": len(moved),
        "orders": removed_orders,
        "reviews": reviews,
        "kept": bool(left),
    }
    if left:
        # What came from Whatnot stays on a show with no laptop recording. `updated_at` is
        # kept: it is read as the time chat last arrived ("live").
        db.session.execute(
            update(Show)
            .where(Show.id == show.id)
            .values(whatnot_link="made", updated_at=Show.updated_at)
        )
    else:
        db.session.delete(show)  # its shelf layout and selling mode go with it
    audit.record(
        org_id,
        "show.laptop_data_deleted",
        user=user,
        target_type="show",
        target_id=show_id,
        details=details,
    )
    return {"showId": show_id if left else None, **details}
