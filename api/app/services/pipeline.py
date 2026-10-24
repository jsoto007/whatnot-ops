"""ChatMessage → ParsedEvent → Sale (+ inventory reserve) → ReviewItem when a person must decide.

Idempotent by construction: one event per message, one sale per event, one
reserve per sale. Running process_show twice changes nothing. Re-parsing
re-evaluates only messages whose event led nowhere (chatter / claims /
unresolved listings); sales that exist are never rewritten by rules.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy import func, select, text

from ..db import db
from ..models import (
    ChatMessage,
    EventKind,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    Sku,
)
from . import catalog, inventory, rules
from .inventory import Actor
from .jobs import enqueue, handler

log = logging.getLogger(__name__)
RULE_ACTOR = Actor("rule", None, "rules")
OPEN = (SaleStatus.detected, SaleStatus.reserved)
DEAD_END = (EventKind.chatter, EventKind.claim, EventKind.listing)
# The same player win seen in another show this close in time is a second laptop or a
# restarted recording of one auction, not a second sale.
PLAYER_DUPLICATE_WINDOW = timedelta(minutes=10)
# A chat line about a win this long before the player recorded it is probably the same win.
CHAT_DUPLICATE_WINDOW = timedelta(minutes=3)


def money(cents: int | None) -> str:
    return "$?" if cents is None else f"${cents // 100}.{cents % 100:02d}"


@dataclass
class Stats:
    parsed: int = 0
    sales: int = 0
    reviews: int = 0


# ---------- review helpers ----------


def open_review(
    org_id: int,
    reason: str,
    summary: str,
    *,
    show_id: int | None = None,
    message_id: int | None = None,
    event_id: int | None = None,
    sale_id: int | None = None,
    proposal: dict | None = None,
    source: str = "rule",
    dedupe: bool = True,
) -> ReviewItem:
    """One open item per (reason, message/sale); re-processing does not pile up duplicates.

    An item with neither a message nor a sale would match *every* such open item of the
    reason ("sale_id IS NULL"), so callers that key on something else (reconcile: buyer +
    item) pass dedupe=False and check for duplicates themselves."""
    if not dedupe and not message_id and not sale_id:
        return _new_review(
            org_id, reason, summary, show_id, message_id, event_id, sale_id, proposal, source
        )
    q = select(ReviewItem).where(
        ReviewItem.organization_id == org_id,
        ReviewItem.reason == reason,
        ReviewItem.status == ReviewStatus.open,
    )
    q = (
        q.where(ReviewItem.message_id == message_id)
        if message_id
        else q.where(ReviewItem.sale_id == sale_id)
    )
    existing = db.session.scalar(q)
    if existing is not None:
        return existing
    return _new_review(
        org_id, reason, summary, show_id, message_id, event_id, sale_id, proposal, source
    )


def _new_review(org_id, reason, summary, show_id, message_id, event_id, sale_id, proposal, source):
    item = ReviewItem(
        organization_id=org_id,
        show_id=show_id,
        message_id=message_id,
        event_id=event_id,
        sale_id=sale_id,
        reason=reason,
        summary=summary,
        proposal=proposal,
        source=source,
    )
    db.session.add(item)
    db.session.flush()
    if reason in ("low_confidence", "unmatched_item"):
        from .supervisor import schedule_review

        schedule_review(item)
    return item


# ---------- item resolution ----------


def match_sku(org_id: int, item_text: str) -> Sku | None:
    """Exact alias/code first, then the longest alias contained in the text (word-bounded)."""
    sku = catalog.resolve_alias(org_id, item_text)
    if sku is not None:
        return sku
    key = catalog.norm_alias(item_text)
    if not key:
        return None
    best: tuple[int, Sku] | None = None
    from ..models import ProductAlias

    for a in db.session.scalars(select(ProductAlias).where(ProductAlias.organization_id == org_id)):
        if f" {a.alias} " in f" {key} " and (best is None or len(a.alias) > best[0]):
            best = (len(a.alias), a.sku)
    return best[1] if best else None


# ---------- applying events ----------


def _reserve(sale: Sale) -> bool:
    """Hold stock for the sale. Never refused for lack of stock: an oversell leaves the
    SKU's `available` negative, and Inventory / the dashboard say how many to buy."""
    inventory.reserve(
        sale.sku_id, sale.quantity, source_type="sale", source_id=str(sale.id), actor=RULE_ACTOR
    )
    sale.status = SaleStatus.reserved
    sale.status_reason = None
    return True


def create_sale(show: Show, ev: ParsedEvent, *, sku: Sku | None) -> Sale:
    sale = Sale(
        organization_id=show.organization_id,
        show_id=show.id,
        event_id=ev.id,
        buyer_username=ev.buyer_username,
        sku=sku,
        item_text=ev.item_text,
        quantity=ev.quantity,
        price_cents=ev.price_cents
        if ev.price_cents is not None
        else (sku.price_cents if sku else None),
        auction_type=ev.auction_type,
        status=SaleStatus.detected,
    )
    db.session.add(sale)
    db.session.flush()
    from . import orders

    orders.attach(sale)
    if sku is None:
        sale.flagged = True
        sale.status_reason = "item not recognised"
        open_review(
            show.organization_id,
            "unmatched_item",
            f'{ev.buyer_username} bought "{ev.item_text or "(no item named)"}"'
            " — which product is that?",
            show_id=show.id,
            message_id=ev.message_id,
            event_id=ev.id,
            sale_id=sale.id,
        )
    else:
        _reserve(sale)
    return sale


def close_sale(show: Show, ev: ParsedEvent, new_status: SaleStatus) -> Sale | None:
    """A cancel / payment-failed line: release the buyer's matching open sale."""
    q = select(Sale).where(Sale.show_id == show.id, Sale.status.in_(OPEN))
    if ev.buyer_username:
        q = q.where(func.lower(Sale.buyer_username) == ev.buyer_username.lower())
    candidates = list(db.session.scalars(q.order_by(Sale.id.desc())))
    if ev.item_text and len(candidates) > 1:
        sku = match_sku(show.organization_id, ev.item_text)
        narrowed = [
            s
            for s in candidates
            if (sku and s.sku_id == sku.id)
            or catalog.norm_alias(s.item_text) == catalog.norm_alias(ev.item_text)
        ]
        if narrowed:
            candidates = narrowed
    if not candidates:
        open_review(
            show.organization_id,
            "no_open_sale",
            f"{ev.kind.value.replace('_', ' ')} for {ev.buyer_username or 'someone'} "
            "but no open sale was found in this show",
            show_id=show.id,
            message_id=ev.message_id,
            event_id=ev.id,
        )
        return None
    if len(candidates) > 1:
        open_review(
            show.organization_id,
            "ambiguous_sale",
            f"{ev.kind.value.replace('_', ' ')} for {ev.buyer_username}: "
            f"which of their {len(candidates)} open sales?",
            show_id=show.id,
            message_id=ev.message_id,
            event_id=ev.id,
            proposal={
                "status": new_status.value,
                "candidates": [
                    {
                        "saleId": s.id,
                        "label": f"#{s.id} {s.quantity} × {s.item_text or (s.sku.code if s.sku else '?')}",  # noqa: E501
                    }
                    for s in candidates
                ],
            },
        )
        return None
    return set_sale_status(
        candidates[0], new_status, reason=f"chat line #{ev.message.seq}", actor=RULE_ACTOR
    )


def set_sale_status(
    sale: Sale, new_status: SaleStatus, *, reason: str | None, actor: Actor
) -> Sale:
    """The one place a sale changes state; moves inventory accordingly."""
    if sale.status == new_status:
        return sale
    if new_status in (SaleStatus.detected, SaleStatus.reserved):
        # Going backwards would need a fresh reservation; the ledger keys reserves by sale
        # id, so re-reserving would silently move nothing. Refuse rather than drift.
        raise ReviewError(
            f"sale {sale.id} is {sale.status.value}; it cannot be moved back to "
            f"{new_status.value}. Record a new sale instead.",
            409,
        )
    if new_status in (SaleStatus.cancelled, SaleStatus.payment_failed):
        if sale.status is SaleStatus.reserved:
            inventory.release(
                sale.sku_id,
                sale.quantity,
                source_type="sale",
                source_id=str(sale.id),
                actor=actor,
                reason=reason,
            )
        elif sale.status in (SaleStatus.paid, SaleStatus.fulfilled):
            # Paid stock already left (maybe shipped); cancelling now is a restock decision.
            sale.flagged = True
    elif new_status is SaleStatus.paid:
        if sale.status is not SaleStatus.reserved:
            raise ValueError(
                f"sale {sale.id} is {sale.status.value}, only reserved sales can be paid"
            )
        inventory.commit(
            sale.sku_id,
            sale.quantity,
            source_type="sale",
            source_id=str(sale.id),
            actor=actor,
            reason=reason,
        )
    elif new_status is SaleStatus.fulfilled:
        if sale.status is not SaleStatus.paid:
            raise ValueError(
                f"sale {sale.id} is {sale.status.value}, only paid sales can be fulfilled"
            )
    sale.status = new_status
    sale.status_reason = reason
    if sale.order is not None:
        from . import orders

        orders.refresh(sale.order)
    for item in db.session.scalars(
        select(ReviewItem).where(
            ReviewItem.sale_id == sale.id, ReviewItem.status == ReviewStatus.open
        )
    ):
        if new_status in (SaleStatus.cancelled, SaleStatus.payment_failed):
            item.status = ReviewStatus.dismissed
            item.resolution = {"by": "sale closed"}
    return sale


# ---------- the pass over a show ----------


def process_show(show_id: int, *, reparse: bool = False, host: str | None = None) -> Stats:
    show = db.session.get(Show, show_id)
    if show is None:
        raise ValueError(f"show {show_id} not found")
    stats = Stats()
    host = host or show.organization.host_username
    if reparse:
        dead = db.session.scalars(
            select(ParsedEvent).where(
                ParsedEvent.show_id == show.id, ParsedEvent.kind.in_(DEAD_END)
            )
        ).all()
        for ev in dead:
            db.session.delete(ev)
        db.session.flush()
    parsed_ids = select(ParsedEvent.message_id).where(ParsedEvent.show_id == show.id)
    messages = db.session.scalars(
        select(ChatMessage)
        .where(ChatMessage.show_id == show.id, ChatMessage.id.not_in(parsed_ids))
        .order_by(ChatMessage.seq)
    ).all()
    current_listing: str = ""
    # Once the video player is recording wins for this show, a chat line about a win is at
    # best the same win again (the host typing "sold to X"): it waits for a person.
    has_player = (
        db.session.scalar(
            select(ChatMessage.id)
            .where(ChatMessage.show_id == show.id, ChatMessage.source == "player")
            .limit(1)
        )
        is not None
    )
    for m in messages:
        c = rules.classify(m.username, m.body, m.raw_text, host=host, source=m.source)
        ev = ParsedEvent(
            organization_id=show.organization_id,
            show_id=show.id,
            message_id=m.id,
            kind=c.kind,
            buyer_username=c.buyer,
            item_text=c.item,
            quantity=c.quantity,
            price_cents=c.price_cents,
            auction_type=c.auction_type,
            rule=c.rule,
            confidence=c.confidence,
            details=c.details or None,
        )
        db.session.add(ev)
        db.session.flush()
        stats.parsed += 1
        if c.rule == "player_unreadable":
            open_review(
                show.organization_id,
                "player_unreadable",
                f'A win from the video player could not be read: "{(m.raw_text or "")[:200]}"',
                show_id=show.id,
                message_id=m.id,  # not the event: a reparse deletes chatter events
            )
            continue
        if c.kind is EventKind.listing:
            # Only the host or the platform announces what is up next; a viewer typing
            # "Now selling - <expensive item>" must not decide what the next win was.
            if c.confidence >= 0.8:
                current_listing = c.item
        elif c.kind is EventKind.sale:
            from_player = m.source == "player"
            if not from_player and not ev.item_text and current_listing:
                ev.item_text = current_listing
                ev.auction_type = rules.auction_type(current_listing)
            if not from_player and has_player:
                open_review(
                    show.organization_id,
                    "possible_duplicate",
                    f'"{m.raw_text}" says {c.buyer} won, but this show\'s wins come from the '
                    "video player — record it only if it is a separate sale",
                    show_id=show.id,
                    message_id=m.id,
                    event_id=ev.id,
                )
                stats.reviews += 1
                continue
            if c.confidence < 0.8:
                if c.rule == "player_win_price_check":
                    summary = (
                        f"The video player says {c.buyer} won {ev.item_text} for "
                        f"{money(c.price_cents)}, more than $100,000 — check the price "
                        "before recording it"
                    )
                elif c.rule.endswith("_split"):
                    summary = (
                        f'"{m.raw_text}" reads like the platform\'s winner line for '
                        f"{c.buyer}, but the name arrived as the sender — confirm it is "
                        "the platform, not a viewer"
                    )
                else:
                    summary = (
                        f'"{m.raw_text}" looks like a sale but was not said by the '
                        "host or the platform"
                    )
                open_review(
                    show.organization_id,
                    "low_confidence",
                    summary,
                    show_id=show.id,
                    message_id=m.id,
                    event_id=ev.id,
                )
                stats.reviews += 1
                continue
            # Two parses (the worker and "Parse now", or two laptops' shows) must not both
            # miss each other's sale for the same buyer: one at a time per buyer.
            _lock_buyer(show.organization_id, c.buyer)
            if not from_player and (win := _player_win_near(show, c.buyer, m)) is not None:
                open_review(
                    show.organization_id,
                    "possible_duplicate",
                    f'"{m.raw_text}" says {c.buyer} won, but the video player already recorded '
                    f"{c.buyer} winning (sale #{win.id}) — record it only if it is a separate sale",
                    show_id=show.id,
                    message_id=m.id,
                    event_id=ev.id,
                )
                stats.reviews += 1
                continue
            if from_player and (other := _same_win_elsewhere(show, ev, m)) is not None:
                open_review(
                    show.organization_id,
                    "possible_duplicate",
                    f"{c.buyer} won {ev.item_text} for {money(c.price_cents)} — the same win "
                    f"is already sale #{other.id} in another recording of this show (a second "
                    "laptop, or recording restarted); record it only if it is a separate sale",
                    show_id=show.id,
                    message_id=m.id,
                    event_id=ev.id,
                )
                stats.reviews += 1
                continue
            sku = match_sku(show.organization_id, ev.item_text) if ev.item_text else None
            sale = create_sale(show, ev, sku=sku)
            stats.sales += 1
            if from_player:
                _flag_chat_sale_for_the_same_win(show, sale, m)
        elif c.kind in (EventKind.cancel, EventKind.payment_failed):
            if c.confidence >= 0.8:
                close_sale(
                    show,
                    ev,
                    SaleStatus.cancelled
                    if c.kind is EventKind.cancel
                    else SaleStatus.payment_failed,
                )
    stats.reviews = (
        db.session.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.show_id == show.id, ReviewItem.status == ReviewStatus.open)
        )
        or 0
    )
    show.updated_at = func.now()
    if stats.parsed:
        from .supervisor import schedule_audit

        schedule_audit(show)
    return stats


def _lock_buyer(org_id: int, buyer: str) -> None:
    """Transaction-scoped lock on (shop, buyer) around the duplicate checks and the sale
    insert, so the check and the insert are one step (the atomic-gate rule)."""
    digest = hashlib.sha256(f"win:{org_id}:{(buyer or '').lower()}".encode()).digest()
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": int.from_bytes(digest[:8], "big", signed=True)},
    )


def _player_sales_near(org_id: int, buyer: str, at, window):
    return (
        select(Sale, ChatMessage)
        .join(ParsedEvent, Sale.event_id == ParsedEvent.id)
        .join(ChatMessage, ParsedEvent.message_id == ChatMessage.id)
        .where(
            Sale.organization_id == org_id,
            Sale.status.in_(OPEN + (SaleStatus.paid, SaleStatus.fulfilled)),
            func.lower(Sale.buyer_username) == (buyer or "").lower(),
            ChatMessage.captured_at.between(at - window, at + window),
        )
    )


def _player_win_near(show: Show, buyer: str, m: ChatMessage) -> Sale | None:
    """A player-win sale for this buyer, in any show of the shop, close in time: a chat line
    in a recording without player rows (another laptop) is then the same win again."""
    q = _player_sales_near(show.organization_id, buyer, m.captured_at, CHAT_DUPLICATE_WINDOW)
    q = q.where(ParsedEvent.rule == "player_win").order_by(Sale.id).limit(1)
    found = db.session.execute(q).first()
    return found[0] if found else None


def _same_win_elsewhere(show: Show, ev: ParsedEvent, m: ChatMessage) -> Sale | None:
    """A player-win sale in another show of the shop with the same buyer, item and price,
    captured within PLAYER_DUPLICATE_WINDOW: two laptops (each laptop's show key is its own
    start time) or a restarted recording saw one auction."""
    return db.session.scalar(
        select(Sale)
        .join(ParsedEvent, Sale.event_id == ParsedEvent.id)
        .join(ChatMessage, ParsedEvent.message_id == ChatMessage.id)
        .where(
            Sale.organization_id == show.organization_id,
            Sale.show_id != show.id,
            ParsedEvent.rule == "player_win",
            func.lower(Sale.buyer_username) == ev.buyer_username.lower(),
            func.lower(Sale.item_text) == (ev.item_text or "").lower(),
            Sale.price_cents == ev.price_cents,
            ChatMessage.captured_at.between(
                m.captured_at - PLAYER_DUPLICATE_WINDOW, m.captured_at + PLAYER_DUPLICATE_WINDOW
            ),
        )
        .order_by(Sale.id)
        .limit(1)
    )


def _flag_chat_sale_for_the_same_win(show: Show, sale: Sale, m: ChatMessage) -> None:
    """A sale the rules made from a chat line close in time to this player win, in this show
    or another laptop's, is probably the same win: ask, never cancel."""
    q = _player_sales_near(
        show.organization_id, sale.buyer_username, m.captured_at, CHAT_DUPLICATE_WINDOW
    )
    q = q.where(Sale.id != sale.id, ChatMessage.source == "chat", Sale.status.in_(OPEN))
    for chat_sale, chat_line in db.session.execute(q).all():
        if chat_sale.show_id == show.id and chat_line.seq > m.seq:
            continue  # after the win in this show: that line was routed to Review already
        open_review(
            show.organization_id,
            "possible_duplicate",
            f"The video player recorded {sale.buyer_username} winning {sale.item_text} for "
            f"{money(sale.price_cents)} (sale #{sale.id}); this chat line made sale "
            f"#{chat_sale.id} too — cancel it if it is the same win",
            show_id=chat_sale.show_id,
            message_id=chat_line.id,
            sale_id=chat_sale.id,
        )


@handler("parse_show")
def _parse_show_job(payload: dict) -> None:
    process_show(int(payload["show_id"]), reparse=bool(payload.get("reparse")))


def schedule_parse(show: Show, *, reparse: bool = False, delay_s: int = 3):
    return enqueue(
        "parse_show",
        {"show_id": show.id, "reparse": reparse},
        org_id=show.organization_id,
        dedupe_key=f"parse_show:{show.id}" if not reparse else None,
        delay_s=delay_s,
    )


# ---------- review resolution ----------


class ReviewError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def record_sale_from_event(item: ReviewItem) -> Sale:
    """Turn the sale-shaped line behind a review item into a real sale: the same
    create_sale() the rules use (SKU match, reserve, or an unmatched_item review).
    Idempotent: a line that already has a sale is refused, never duplicated."""
    ev = db.session.get(ParsedEvent, item.event_id) if item.event_id else None
    show = db.session.get(Show, item.show_id) if item.show_id else None
    if ev is None or show is None or ev.kind is not EventKind.sale:
        raise ReviewError("this line is not a sale line; nothing to record")
    existing = db.session.scalar(select(Sale.id).where(Sale.event_id == ev.id))
    if existing is not None:
        raise ReviewError(f"this line is already sale #{existing}", 409)
    if not ev.buyer_username:
        raise ReviewError("the line names no buyer; nothing to record")
    sku = match_sku(show.organization_id, ev.item_text) if ev.item_text else None
    return create_sale(show, ev, sku=sku)


def resolve(item: ReviewItem, action: str, body: dict, *, user) -> ReviewItem:
    if item.status is not ReviewStatus.open:
        raise ReviewError("already resolved", 409)
    actor = Actor.user(user)
    if action == "dismiss":
        item.status = ReviewStatus.dismissed
        item.resolution = {"action": "dismiss"}
    elif action == "confirm_sale":
        # A person vouches for a sale-shaped line the rules could not trust (a viewer-shaped
        # line, or the platform's winner line that arrived with the winner as the sender).
        sale = record_sale_from_event(item)
        item.status = ReviewStatus.resolved
        item.resolution = {
            "action": "confirm_sale",
            "saleId": sale.id,
            "skuId": sale.sku_id,
            "reserved": sale.status is SaleStatus.reserved,
        }
    elif action == "assign_sku":
        sale = item.sale
        if sale is None:
            raise ReviewError("this item has no sale to assign")
        sku = catalog.get_sku(item.organization_id, int(body.get("skuId") or 0))
        if sale.status is SaleStatus.reserved and sale.sku_id and sale.sku_id != sku.id:
            inventory.release(
                sale.sku_id,
                sale.quantity,
                source_type="sale",
                source_id=str(sale.id),
                actor=actor,
                reason="reassigned",
            )
        sale.sku = sku
        sale.sku_id = sku.id  # _reserve reads the FK before the relationship is flushed
        sale.flagged = False
        if sale.price_cents is None:
            sale.price_cents = sku.price_cents
        if body.get("remember") and sale.item_text:
            catalog.add_alias(item.organization_id, sku, sale.item_text, quiet=True)
        ok = _reserve(sale)
        item.status = ReviewStatus.resolved
        item.resolution = {"action": "assign_sku", "skuId": sku.id, "reserved": ok}
    elif action == "pick_sale":
        sale = db.session.get(Sale, int(body.get("saleId") or 0))
        if sale is None or sale.organization_id != item.organization_id:
            raise ReviewError("sale not found", 404)
        status = SaleStatus((item.proposal or {}).get("status", "cancelled"))
        set_sale_status(sale, status, reason=f"review #{item.id}", actor=actor)
        item.status = ReviewStatus.resolved
        item.resolution = {"action": "pick_sale", "saleId": sale.id, "status": status.value}
    elif action == "apply_proposal":
        from .supervisor import apply_proposal

        apply_proposal(item, actor=actor, user=user)
    elif action == "set_status":
        sale = item.sale
        if sale is None:
            raise ReviewError("this item has no sale")
        set_sale_status(
            sale, SaleStatus(body.get("status", "")), reason=f"review #{item.id}", actor=actor
        )
        item.status = ReviewStatus.resolved
        item.resolution = {"action": "set_status", "status": sale.status.value}
    else:
        raise ReviewError(f"unknown action {action!r}")
    from datetime import datetime

    item.resolved_by_id = user.id
    item.resolved_at = datetime.now(UTC)
    return item
