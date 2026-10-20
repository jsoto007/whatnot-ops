"""ChatMessage → ParsedEvent → Sale (+ inventory reserve) → ReviewItem when a person must decide.

Idempotent by construction: one event per message, one sale per event, one
reserve per sale. Running process_show twice changes nothing. Re-parsing
re-evaluates only messages whose event led nowhere (chatter / claims /
unresolved listings); sales that exist are never rewritten by rules.
"""

import logging
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import func, select

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
from .inventory import Actor, InsufficientStock
from .jobs import enqueue, handler

log = logging.getLogger(__name__)
RULE_ACTOR = Actor("rule", None, "rules")
OPEN = (SaleStatus.detected, SaleStatus.reserved)
DEAD_END = (EventKind.chatter, EventKind.claim, EventKind.listing)


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
) -> ReviewItem:
    """One open item per (reason, message/sale); re-processing does not pile up duplicates."""
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
    try:
        inventory.reserve(
            sale.sku_id, sale.quantity, source_type="sale", source_id=str(sale.id), actor=RULE_ACTOR
        )
    except InsufficientStock as e:
        sale.status = SaleStatus.detected
        sale.status_reason = str(e)
        sale.flagged = True
        open_review(
            sale.organization_id,
            "insufficient_stock",
            f"{sale.buyer_username} bought {sale.quantity} × "
            f"{sale.item_text or sale.sku.code} but {e}",
            show_id=sale.show_id,
            message_id=sale.event.message_id if sale.event else None,
            event_id=sale.event_id,
            sale_id=sale.id,
        )
        return False
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
        elif sale.status is SaleStatus.paid:
            # Paid stock already left; cancelling after payment is a restock decision, flag it.
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
    for m in messages:
        c = rules.classify(m.username, m.body, m.raw_text, host=host)
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
        if c.kind is EventKind.listing:
            current_listing = c.item
        elif c.kind is EventKind.sale:
            if not ev.item_text and current_listing:
                ev.item_text = current_listing
                ev.auction_type = rules.auction_type(current_listing)
            if c.confidence < 0.8:
                open_review(
                    show.organization_id,
                    "low_confidence",
                    f'"{m.raw_text}" looks like a sale but was not said by the host '
                    "or the platform",
                    show_id=show.id,
                    message_id=m.id,
                    event_id=ev.id,
                )
                stats.reviews += 1
                continue
            sku = match_sku(show.organization_id, ev.item_text) if ev.item_text else None
            create_sale(show, ev, sku=sku)
            stats.sales += 1
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


def resolve(item: ReviewItem, action: str, body: dict, *, user) -> ReviewItem:
    if item.status is not ReviewStatus.open:
        raise ReviewError("already resolved", 409)
    actor = Actor.user(user)
    if action == "dismiss":
        item.status = ReviewStatus.dismissed
        item.resolution = {"action": "dismiss"}
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
