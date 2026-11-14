"""ChatMessage → ParsedEvent → Sale (+ inventory reserve) → ReviewItem when a person must decide.

Idempotent by construction: one event per message, one sale per event, one
reserve per sale. Running process_show twice changes nothing. Re-parsing
re-evaluates only messages whose event led nowhere (chatter / claims /
unresolved listings); sales that exist are never rewritten by rules.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import aliased

from ..db import db
from ..models import (
    ChatMessage,
    EventKind,
    MovementKind,
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
from .jobs import enqueue, handler, inline

log = logging.getLogger(__name__)
RULE_ACTOR = Actor("rule", None, "rules")
# Whatnot charges the winner's card when the auction ends; a decline arrives as its own line.
AUTOPAY_ACTOR = Actor("rule", None, "whatnot autopay")
AUTOPAY_REASON = "paid at the win (Whatnot charges the winner automatically)"
OPEN = (SaleStatus.detected, SaleStatus.reserved)
# What a cancel or payment-declined line may close: a sale still in the shop. Paid sales count,
# because every win is paid at once; a decline turns that payment back.
CLOSABLE = OPEN + (SaleStatus.paid,)
# The ledger's source type for a sale's second payment (a declined card that went through
# after all): its own source keeps the reserve / commit / restock of each round idempotent.
REPAID = "sale_repaid"
DEAD_END = (EventKind.chatter, EventKind.claim, EventKind.listing)
# The same player win seen in another show this close in time is a second laptop or a
# restarted recording of one auction, not a second sale.
PLAYER_DUPLICATE_WINDOW = timedelta(minutes=10)
# A chat line about a win this long before the player recorded it is probably the same win.
CHAT_DUPLICATE_WINDOW = timedelta(minutes=3)
# Two laptops can read one win's title differently (the next lot's title is already up on
# one of them): the same buyer at the same price this close in another show is that win.
PLAYER_NEAR_WINDOW = timedelta(minutes=2)
# The extension records a chat row again when Whatnot re-renders it (a new DOM node, a new
# id): the identical sale line this close is that copy, not a second sale.
CHAT_LINE_DUPLICATE_WINDOW = timedelta(minutes=10)


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
    """Exact alias/code first, then the longest alias contained in the text (word-bounded).

    A title that says the winner chooses ("CHOOSE ANY PRODUCT YOU WANT #73") names no
    product, whatever names were saved: the winner says which in chat."""
    if rules.is_choice_title(item_text):
        return None
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


def _sid(sale: Sale) -> str:
    """The stock ledger's key for the sale's product (services/sale_items.py): the sale's own
    id, as always, unless its product was changed or added later."""
    from . import sale_items

    return sale_items.source_id(sale)


def _reserve(sale: Sale) -> bool:
    """Hold stock for the sale. Never refused for lack of stock: an oversell leaves the
    SKU's `available` negative, and Inventory / the dashboard say how many to buy."""
    inventory.reserve(
        sale.sku_id, sale.quantity, source_type="sale", source_id=_sid(sale), actor=RULE_ACTOR
    )
    sale.status = SaleStatus.reserved
    sale.status_reason = None
    return True


def autopay(sale: Sale, *, at=None) -> Sale:
    """Whatnot charges the winner as the auction ends: a reserved sale is paid at once
    (stock committed). A decline that follows closes it again (set_sale_status)."""
    if sale.status is not SaleStatus.reserved:
        return sale
    set_sale_status(sale, SaleStatus.paid, reason=AUTOPAY_REASON, actor=AUTOPAY_ACTOR)
    sale.paid_at = sale.paid_at or at or datetime.now(UTC)
    return sale


def _won_at(ev: ParsedEvent):
    return ev.message.captured_at if ev.message is not None else None


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
        autopay(sale, at=_won_at(ev))
    return sale


WHATNOT_ACTOR = Actor("whatnot", None, "Whatnot")
ORDER_PAID_REASON = "paid on Whatnot"


# A win line and Whatnot's order for the same win are seconds apart; half an hour allows
# for a laptop's clock and a line captured late. Further apart, they are different wins.
SAME_WIN_WINDOW = timedelta(minutes=30)


def _titles_match(a: str | None, b: str | None) -> bool:
    """Two listing titles for the same lot: equal once case, spacing and punctuation are
    set aside. An empty title matches nothing."""
    return same_listing(a, b) == 0


def same_listing(a: str | None, b: str | None) -> int | None:
    """How surely two titles name the same lot: 0 the same text (case, spacing and
    punctuation aside), 1 the same once Whatnot's trailing lot number is set aside ("Red
    bundle" / "Red bundle #752"), 2 one contains the other. None: not the same lot, which
    always includes two different lot numbers ("… #7" and "… #8" are two wins)."""
    ka, kb = catalog.norm_alias(a or ""), catalog.norm_alias(b or "")
    if not ka or not kb:
        return None
    if ka == kb:
        return 0
    lot_a, lot_b = rules.lot_number(a or ""), rules.lot_number(b or "")
    if lot_a is not None and lot_b is not None and lot_a != lot_b:
        return None
    base_a = catalog.norm_alias(rules.item_title(a or ""))
    base_b = catalog.norm_alias(rules.item_title(b or ""))
    if base_a and base_a == base_b:
        return 1
    if base_a and base_b and (base_a in base_b or base_b in base_a):
        return 2
    return None


def paid_price(sale: Sale) -> int | None:
    """What the buyer paid, when the sale knows it: the price a win line or an order
    stated. A sale whose line named no price carries the product's list price instead,
    which says nothing about what was paid: None."""
    if sale.event is not None and sale.event.price_cents is None:
        return None
    return sale.price_cents


def won_when(sale: Sale):
    """When the lot was won: its win line's time, else when Whatnot says it was ordered."""
    if sale.event is not None and sale.event.message is not None:
        return sale.event.message.captured_at
    return sale.ordered_at


def same_win(
    title_a, price_a, at_a, title_b, price_b, at_b, *, window: timedelta = SAME_WIN_WINDOW
) -> tuple | None:
    """Whether a sale and a win line / order are one and the same win, as a sort key (the
    smaller, the surer) or None. Same lot by title, the same price when both are known,
    and close in time when both times are known. Used both ways round (a win line meeting
    the sale an order made; an order meeting the sale a win line made), so the two paths
    cannot disagree and make two sales of one win."""
    rank = same_listing(title_a, title_b)
    if rank is None:
        return None
    if None not in (price_a, price_b) and price_a != price_b:
        return None
    apart = abs((at_a - at_b).total_seconds()) if None not in (at_a, at_b) else None
    if apart is not None and apart > window.total_seconds():
        return None
    return (rank, apart if apart is not None else window.total_seconds())


def _recordings_of_the_same_show(show: Show) -> list[int]:
    """This show and every other recording of the same Whatnot show."""
    if show.whatnot_show_id is None:
        return [show.id]
    return list(
        db.session.scalars(
            select(Show.id).where(
                Show.organization_id == show.organization_id,
                Show.whatnot_show_id == show.whatnot_show_id,
            )
        )
    ) or [show.id]


def auction_of(title: str | None) -> str | None:
    """The auction a listing title names within one show, or None. Whatnot numbers every
    lot it auctions ("$1 BEAUTY PULLS #520", "CHOOSE ANY PRODUCT YOU WANT #6"), and **an
    auction has one win** (Javier's rule, 2026-11-13): the same buyer on the same lot again
    is the same win, and a different buyer on it has taken the win. A title with no lot
    number names no auction: the same listing may be won many times in a show."""
    if not title or rules.lot_number(title) is None:
        return None
    return catalog.norm_alias(title) or None


def holders(show: Show, title: str | None, at: datetime | None) -> list[Sale]:
    """The sales that hold this auction now: standing and not shipped, in every recording
    of the show, won within `SAME_WIN_WINDOW` of `at`, oldest first. Empty for a title that
    names no auction.

    The window matters: lot numbers start again in every show, and one recording can run
    through two shows, so "#6" three hours ago is another auction. A sale whose win time is
    not known is left out for the same reason."""
    key = auction_of(title)
    if key is None or at is None:
        return []
    rows = db.session.scalars(
        select(Sale)
        .where(
            Sale.show_id.in_(_recordings_of_the_same_show(show)),
            Sale.status.in_(CLOSABLE),
            func.lower(Sale.item_text) == (title or "").lower(),
        )
        .order_by(Sale.id)
    ).all()
    return [
        s
        for s in rows
        if auction_of(s.item_text) == key
        and (won := won_when(s)) is not None
        and abs(won - at) <= SAME_WIN_WINDOW
    ]


TAKEN_OVER = "the auction went to another buyer"


def give_way(sale: Sale, *, winner: str, actor: Actor, where: str = "") -> Sale:
    """Another win of the same auction is this sale's end: a different buyer has the win
    now, or it is the same buyer's win recorded a second time. Cancelled like any sale
    (stock released or returned; picked already: a person takes it out). Only Whatnot's
    own orders decide this (`whatnot/orders._one_win`); the stream only asks."""
    same = sale.buyer_username.lower() == winner.lower()
    reason = (
        f"the same win, recorded twice{where}"
        if same
        else f"the auction was won by {winner}{where}"
    )
    set_sale_status(sale, SaleStatus.cancelled, reason=reason[:240], actor=actor)
    settle_lot_question(sale)
    return sale


# The review item for one lot that the banner showed won by two buyers.
LOT_WON_TWICE = "lot_won_twice"
# How a sale that gave way says so (`give_way`); Whatnot's later paid order for the buyer
# then records the win again instead of meeting a cancelled sale.
GAVE_WAY = ("the auction was won by ", "the same win, recorded twice")


def gave_way(sale: Sale) -> bool:
    return sale.status is SaleStatus.cancelled and (sale.status_reason or "").startswith(GAVE_WAY)


def ask_which_win(show: Show, earlier: Sale, later: Sale) -> list[ReviewItem]:
    """Whatnot's banner showed the same lot won by two buyers. An auction has one win, and
    the banner alone cannot say which stands: the lot may have been run again after a
    declined card (the later buyer has it), or the earlier win's banner may have reached
    the app late. A person is asked, on each of the two sales Whatnot has not confirmed, so
    neither buyer's order is picked meanwhile (`orders.in_doubt`). Left alone, Whatnot's
    orders settle it (`whatnot/orders._one_win`), and a paid order for a doubted sale
    answers its question."""
    asked = []
    for doubted, other in ((earlier, later), (later, earlier)):
        if doubted.external_order_id:
            continue  # Whatnot has this one's order: it is not the one in doubt
        asked.append(
            open_review(
                show.organization_id,
                LOT_WON_TWICE,
                f"{doubted.item_text} was won by {earlier.buyer_username} and then by "
                f"{later.buyer_username}. An auction has one winner: if "
                f"{other.buyer_username} has it, cancel {doubted.buyer_username}'s sale. Or "
                "leave it: Whatnot's orders settle it by themselves when they are applied.",
                show_id=doubted.show_id,
                sale_id=doubted.id,
            )
        )
    return asked


def settle_lot_question(closed: Sale) -> None:
    """One of the two sales of a lot that was asked about is closed: the question on the
    other one ("if the other buyer has it, cancel this sale") is answered."""
    if auction_of(closed.item_text) is None:
        return
    show = db.session.get(Show, closed.show_id)
    for item in db.session.scalars(
        select(ReviewItem)
        .join(Sale, ReviewItem.sale_id == Sale.id)
        .where(
            Sale.show_id.in_(_recordings_of_the_same_show(show)),
            Sale.id != closed.id,
            func.lower(Sale.item_text) == (closed.item_text or "").lower(),
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.reason == LOT_WON_TWICE,
        )
    ):
        item.status = ReviewStatus.resolved
        item.resolution = {"by": "the other sale of the lot was closed"}
        item.resolved_at = datetime.now(UTC)


def answer_declines_of(show_ids: list[int], title: str | None, *, winner: str, at) -> None:
    """Whatnot says the auction went to `winner` and was paid: what was open about ANOTHER
    buyer's declined payment for that lot (won within `SAME_WIN_WINDOW` of `at`) is
    answered. Their sale stays declined; nobody has to chase it."""
    key = auction_of(title)
    if key is None or at is None:
        return
    for item, sale in db.session.execute(
        select(ReviewItem, Sale)
        .join(Sale, ReviewItem.sale_id == Sale.id)
        .where(
            Sale.show_id.in_(show_ids),
            Sale.status == SaleStatus.payment_failed,
            func.lower(Sale.buyer_username) != winner.lower(),
            func.lower(Sale.item_text) == (title or "").lower(),
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.reason == "payment_declined",
        )
    ).all():
        won = won_when(sale)
        if won is None or abs(won - at) > SAME_WIN_WINDOW:
            continue
        item.status = ReviewStatus.resolved
        item.resolution = {"by": TAKEN_OVER}
        item.resolved_at = datetime.now(UTC)


def listed_sku(org_id: int, sku_id: int | None) -> Sku | None:
    """The product a Whatnot listing is linked to, if it is (still) one of this shop's."""
    if sku_id is None:
        return None
    sku = db.session.get(Sku, sku_id)
    return sku if sku is not None and sku.organization_id == org_id and sku.active else None


def name_from_listing(sale: Sale, sku_id: int | None, *, price_cents: int | None = None) -> bool:
    """A win nobody has named a product for, whose Whatnot order is for a listing linked
    to a product here: the listing names it. Done through the same decision a person
    makes in Review (assign_sku: reserve, paid at the win, the order follows), with
    Whatnot as the actor. False when there is nothing to do, no open decision to take,
    or a person settled it a moment ago.

    `price_cents` is what the order says was paid per item. A win whose line carried no
    price takes it, not the product's list price: Whatnot knows what it charged."""
    sku = listed_sku(sale.organization_id, sku_id)
    if sku is None or sale.sku_id is not None or sale.status is not SaleStatus.detected:
        return False
    item = db.session.scalar(
        select(ReviewItem)
        .where(
            ReviewItem.sale_id == sale.id,
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.reason == "unmatched_item",
        )
        .order_by(ReviewItem.id)
        .limit(1)
    )
    if item is None:
        return False
    if sale.price_cents is None and price_cents is not None:
        sale.price_cents = price_cents
    try:
        resolve(item, "assign_sku", {"skuId": sku.id}, user=None, actor=WHATNOT_ACTOR)
    except ReviewError:
        return False  # a person decided this very item while the orders were being applied
    return True


def create_sale_from_order(
    show: Show,
    *,
    buyer: str,
    item_text: str,
    quantity: int,
    price_cents: int | None,
    external_order_id: str,
    ordered_at: datetime | None,
    settled: bool,
    sku_id: int | None = None,
    by_text: bool = True,
    won_at: datetime | None = None,
) -> Sale:
    """A sale the app learnt of from Whatnot's own order record, not from a chat line or
    the video player (nobody was recording, or the win was missed). Whatnot vouches that
    it was sold: the same path as a win (SKU match, reserve, paid at the win), or an
    unmatched_item review when the listing does not name a product the shop knows.

    The caller holds the buyer's lock (`_lock_buyer`) and has checked that no sale stands
    for this order already. `settled` says Whatnot has taken the payment; otherwise the
    sale is paid "at the win" like any other and a later decline closes it."""
    # A listing linked to a product names it; otherwise its text may, unless a person
    # said the listing is none of the shop's products (`by_text` False).
    sku = listed_sku(show.organization_id, sku_id) or (
        match_sku(show.organization_id, item_text) if item_text and by_text else None
    )
    sale = Sale(
        organization_id=show.organization_id,
        show_id=show.id,
        event_id=None,
        buyer_username=buyer,
        sku=sku,
        item_text=item_text[:240],
        quantity=quantity,
        price_cents=price_cents if price_cents is not None else (sku.price_cents if sku else None),
        auction_type=rules.auction_type(item_text or ""),
        status=SaleStatus.detected,
        external_order_id=external_order_id,
        # When the lot was won: for a card declined and then paid, the declined attempt's
        # time (`won_at`), not when the retry went through (`ordered_at`, kept for paid_at).
        ordered_at=won_at or ordered_at,
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
            f'{buyer} bought "{item_text or "(no item named)"}" — which product is that?',
            show_id=show.id,
            sale_id=sale.id,
        )
        return sale
    _reserve(sale)
    if settled:
        set_sale_status(sale, SaleStatus.paid, reason=ORDER_PAID_REASON, actor=WHATNOT_ACTOR)
        sale.paid_at = sale.paid_at or ordered_at or datetime.now(UTC)
    else:
        autopay(sale, at=ordered_at)
    return sale


def claim_order_sale(show: Show, ev: ParsedEvent) -> Sale | None:
    """The win a chat or video-player line is about, when Whatnot's order for it already
    made the sale (create_sale_from_order): the same buyer and the same win (`same_win`:
    lot, price, and close in time), with no line tied to it yet. The line becomes that
    sale's evidence instead of a second sale. Looked for across the whole shop, not only
    this recording: one laptop can record through two shows, and the order's sale then
    lives in another show here. The surest match wins, then the oldest, so three wins of
    one listing claim three sales. The order's quantity stands (it is Whatnot's record).
    Caller holds the buyer's lock."""
    if not ev.buyer_username:
        return None
    at = _won_at(ev)
    same_show = set(_recordings_of_the_same_show(show))
    candidates = db.session.scalars(
        select(Sale)
        .where(
            Sale.organization_id == show.organization_id,
            Sale.event_id.is_(None),
            Sale.external_order_id.is_not(None),
            Sale.status.in_(OPEN + (SaleStatus.paid, SaleStatus.fulfilled)),
            func.lower(Sale.buyer_username) == ev.buyer_username.lower(),
        )
        .order_by(Sale.id)
        .with_for_update()
    ).all()
    best: tuple | None = None
    for sale in candidates:
        if sale.ordered_at is None and sale.show_id not in same_show:
            continue  # nothing says when it was won: only this show's own
        key = same_win(
            sale.item_text, sale.price_cents, sale.ordered_at, ev.item_text, ev.price_cents, at
        )
        if key is not None and (best is None or key < best[0]):
            best = (key, sale)
    if best is None:
        return None
    sale = best[1]
    sale.event_id = ev.id
    if sale.price_cents is None:
        sale.price_cents = ev.price_cents
    return sale


def close_sale(
    show: Show, ev: ParsedEvent, new_status: SaleStatus, *, flag_only: bool = False
) -> Sale | None:
    """A cancel / payment-failed line: close the buyer's matching sale (release or restock).

    `flag_only` is for a declined payment in a show where Whatnot's own order says whether
    the win was paid (`whatnot/orders.decides`): the sale is left standing and flagged for
    a person (`flag_decline`); the order closes it, or clears the flag."""
    q = select(Sale).where(Sale.show_id == show.id, Sale.status.in_(CLOSABLE))
    if ev.buyer_username:
        q = q.where(func.lower(Sale.buyer_username) == ev.buyer_username.lower())
    candidates = list(db.session.scalars(q.order_by(Sale.id.desc())))
    if ev.rule == "player_payment_declined":
        found = _declined_win(show, ev, quiet=flag_only)
        if found is None or isinstance(found, Sale):
            return found
        candidates = found
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
        if flag_only:
            # Nothing recorded for the win: Whatnot's failed order is what gets flagged
            # (whatnot/orders.py), and a paid one records the sale.
            return None
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
    if flag_only:
        flag_decline(candidates[0], ev)
        return candidates[0]
    sale = set_sale_status(
        candidates[0], new_status, reason=f"chat line #{ev.message.seq}", actor=RULE_ACTOR
    )
    if new_status is SaleStatus.payment_failed:
        review_decline(sale, message_id=ev.message_id, event_id=ev.id)
    return sale


# Flags that say "the stream showed this win's card declined" while the sale still stands.
# Whatnot's paid order for the win clears them (whatnot/orders.apply).
DECLINE_SEEN = "decline_seen"
# The packer's note when a sale's product was changed after its order was being picked: the
# old unit may be in the box and is counted back by a person (services/sale_items.change).
SWAPPED_AFTER_PICKING = "swapped_after_picking"


def flag_decline(sale: Sale, ev: ParsedEvent) -> ReviewItem:
    """The stream showed this win's card declined, in a show where Whatnot's order says
    whether it was paid. The sale stands; a person is told, and may close the sale
    themself; nothing of the buyer's order is picked meanwhile (`orders.card_in_doubt`).
    The order then settles it: paid clears the flag, failed closes the sale (and leaves its
    own "payment declined" item).

    Once per sale, ever: the Sold tab shows again what the player showed, and a flag a
    person answered, or that the paid order cleared, does not come back."""
    existing = db.session.scalar(
        select(ReviewItem)
        .where(ReviewItem.sale_id == sale.id, ReviewItem.reason == DECLINE_SEEN)
        .order_by(ReviewItem.id)
        .limit(1)
    )
    if existing is not None:
        return existing
    return open_review(
        sale.organization_id,
        DECLINE_SEEN,
        f"Whatnot showed {sale.buyer_username}'s card declined for {sale.quantity} × "
        f"{sale.item_text or (sale.sku.code if sale.sku else '?')} ({money(sale.price_cents)}). "
        "The sale stands until Whatnot's order says whether it was paid: if it was, this "
        "clears by itself; if not, the order closes the sale. Close it now only if you are "
        "sure they will not pay.",
        show_id=sale.show_id,
        message_id=ev.message_id,
        event_id=ev.id,
        sale_id=sale.id,
    )


def _declined_win(show: Show, ev: ParsedEvent, *, quiet: bool = False):
    """The win a decline read from Whatnot's own UI is about: the same buyer, at exactly that
    price, on that show day (another laptop's recording may hold it), the same title first.
    Never a sale at another price. Returns the sale already declined (the same decline seen
    again: nothing to do), None after opening a review, or the candidates to close.
    `quiet`: no review when no sale at that price exists (see `close_sale`'s `flag_only`)."""
    rows = list(
        db.session.scalars(
            select(Sale)
            .join(Show, Sale.show_id == Show.id)
            .where(
                Sale.organization_id == show.organization_id,
                Show.show_date == show.show_date,
                func.lower(Sale.buyer_username) == ev.buyer_username.lower(),
                Sale.price_cents == ev.price_cents,
                Sale.status.in_(CLOSABLE + (SaleStatus.payment_failed,)),
            )
            .order_by(Sale.show_id != show.id, Sale.id)
        )
    )
    titled = [s for s in rows if (s.item_text or "").lower() == (ev.item_text or "").lower()]
    rows = titled or rows
    open_ = [s for s in rows if s.status in CLOSABLE]
    done = [s for s in rows if s.status is SaleStatus.payment_failed]
    if done and not open_:
        return done[0]
    if open_ and not done:
        here = [s for s in open_ if s.show_id == show.id]
        return here or open_[:1]  # another laptop's copy of this win: close the one sale
    if quiet and not rows:
        return None
    summary = (
        f"Whatnot showed {ev.buyer_username}'s payment declined for {ev.item_text} "
        f"({money(ev.price_cents)}), but "
        + (
            "no sale at that price was found"
            if not rows
            else "one such sale is already declined and another is not — which one was it?"
        )
    )
    open_review(
        show.organization_id,
        "no_open_sale" if not rows else "ambiguous_sale",
        summary,
        show_id=show.id,
        message_id=ev.message_id,
        event_id=ev.id,
        proposal=None
        if not rows
        else {
            "status": SaleStatus.payment_failed.value,
            "candidates": [
                {"saleId": s.id, "label": f"#{s.id} {s.item_text} ({s.status.value})"}
                for s in open_
            ],
        },
    )
    return None


def review_decline(
    sale: Sale,
    *,
    message_id: int | None = None,
    event_id: int | None = None,
    by_itself: bool = False,
):
    """Whatnot gives a declined buyer until the end of the show to pay another way. The
    decline stands, and this item lets a person put the sale back if the payment went through.
    `by_itself`: Whatnot's orders are applied as they arrive, so a payment that goes through
    puts the sale back with nobody stepping in; the item is then for the person to know of
    the decline and to chase it."""
    back = sale.sku_id is not None and units_out(sale) == 0
    return open_review(
        sale.organization_id,
        "payment_declined",
        f"{sale.buyer_username}'s payment was declined for {sale.quantity} × "
        f"{sale.item_text or (sale.sku.code if sale.sku else '?')} ({money(sale.price_cents)}). "
        + ("The item is back in stock. " if back else "")
        + (
            "If they pay another way, the sale comes back by itself when Whatnot's order says so."
            if by_itself
            else "If they pay another way before the show ends, mark it paid."
        ),
        show_id=sale.show_id,
        message_id=message_id,
        event_id=event_id,
        sale_id=sale.id,
    )


def set_sale_status(
    sale: Sale, new_status: SaleStatus, *, reason: str | None, actor: Actor
) -> Sale:
    """The one place a sale changes state; moves inventory accordingly."""
    if sale.id is not None:
        # One change at a time per sale, decided on its committed state (a decline racing the
        # payment file or a person must not both act on a stale status).
        db.session.flush()
        db.session.refresh(sale, with_for_update=True)
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
    picked = None
    if new_status in (SaleStatus.cancelled, SaleStatus.payment_failed):
        if sale.status is SaleStatus.reserved:
            inventory.release(
                sale.sku_id,
                sale.quantity,
                source_type=_round(sale),
                source_id=_sid(sale),
                actor=actor,
                reason=reason,
            )
        elif (
            sale.status is SaleStatus.paid
            and not _in_fulfilment(sale)
            and not _moved(sale, MovementKind.restock, _round(sale))
        ):
            # Paid at the win and still on the shelf: the stock comes back.
            inventory.move(
                sale.sku_id,
                MovementKind.restock,
                sale.quantity,
                source_type=_round(sale),
                source_id=_sid(sale),
                actor=actor,
                reason=reason,
            )
        elif sale.status in (SaleStatus.paid, SaleStatus.fulfilled):
            # Picked, packed or shipped: whether it goes back on the shelf is a person's call.
            sale.flagged = True
            picked = sale.status
    elif new_status is SaleStatus.paid:
        if sale.status is SaleStatus.payment_failed:
            _repay(sale, actor=actor, reason=reason)
        elif sale.status is not SaleStatus.reserved:
            raise ReviewError(
                f"sale {sale.id} is {sale.status.value}; only a reserved sale, or a declined "
                "one with a product, can be marked paid",
                409,
            )
        else:
            inventory.commit(
                sale.sku_id,
                sale.quantity,
                source_type="sale",
                source_id=_sid(sale),
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
        if item.reason == SWAPPED_AFTER_PICKING:
            # A note about a unit that may be in a box: it stands until a person has dealt
            # with that unit, whatever happens to the sale.
            continue
        if new_status in (SaleStatus.cancelled, SaleStatus.payment_failed):
            item.status = ReviewStatus.dismissed
            item.resolution = {"by": "sale closed"}
        elif new_status is SaleStatus.paid and item.reason in (
            "payment_declined",
            "closed_after_picking",
        ):
            item.status = ReviewStatus.resolved
            item.resolution = {"by": "sale paid"}
    if picked is not None:
        stage = "shipped" if picked is SaleStatus.fulfilled else sale.order.status.value
        what = {"picking": "being picked"}.get(stage, stage)
        open_review(
            sale.organization_id,
            "closed_after_picking",
            f"{sale.buyer_username}'s {sale.quantity} × "
            f"{sale.item_text or (sale.sku.code if sale.sku else '?')} is "
            f"{new_status.value.replace('_', ' ')} but its order was already {what}. Take it "
            "out of the package and count it back in Inventory if it is not going out.",
            show_id=sale.show_id,
            sale_id=sale.id,
        )
    return sale


def await_product(sale: Sale) -> bool:
    """A win that was declined before anyone named its product, and paid after all: it
    waits for its product again, exactly as it did before the decline, with its "which
    product is that?" decision open again.

    `set_sale_status` refuses every step back, because a named sale's stock is keyed by the
    sale in the ledger and a second reservation would move nothing. A win with no product
    never held or released anything, so this one step back is safe; a declined sale WITH a
    product is paid again through `set_sale_status` (`_repay`) instead. False, having
    changed nothing, when the sale is no longer such a win (named or closed meanwhile) or
    its buyer's order has already shipped."""
    from .orders import OrderStatus

    db.session.flush()
    db.session.refresh(sale, with_for_update=True)
    if sale.status is not SaleStatus.payment_failed or sale.sku_id is not None:
        return False
    if sale.order is not None and sale.order.status is OrderStatus.shipped:
        return False  # the buyer's package left without it: a person decides (as `_repay`)
    sale.status = SaleStatus.detected
    sale.flagged = True
    sale.status_reason = "item not recognised"
    if sale.order is not None:
        from . import orders

        orders.refresh(sale.order)
    for item in db.session.scalars(
        select(ReviewItem).where(
            ReviewItem.sale_id == sale.id,
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.reason == "payment_declined",
        )
    ):
        item.status = ReviewStatus.resolved
        item.resolution = {"by": "sale paid"}
    open_review(
        sale.organization_id,
        "unmatched_item",
        f'{sale.buyer_username} bought "{sale.item_text or "(no item named)"}"'
        " — which product is that?",
        show_id=sale.show_id,
        message_id=sale.event.message_id if sale.event is not None else None,
        event_id=sale.event_id,
        sale_id=sale.id,
    )
    return True


def _in_fulfilment(sale: Sale) -> bool:
    """The sale's unit may already be in a box: its order is being picked or later, and the
    sale was won before picking started (a later win on the same order was never pulled)."""
    from .orders import FULFILLMENT

    order = sale.order
    if order is None or order.status not in FULFILLMENT:
        return False
    return order.picked_at is None or sale.paid_at is None or sale.paid_at <= order.picked_at


def _moved(sale: Sale, kind: MovementKind, source_type: str) -> bool:
    from ..models import InventoryMovement

    return (
        db.session.scalar(
            select(InventoryMovement.id)
            .where(
                InventoryMovement.sku_id == sale.sku_id,
                InventoryMovement.kind == kind,
                InventoryMovement.source_type == source_type,
                InventoryMovement.source_id == _sid(sale),
            )
            .limit(1)
        )
        is not None
    )


def units_out(sale: Sale) -> int:
    """How many of the sale's units the ledger counts as gone from the shelf: committed by
    any payment round, minus what a decline or cancel put back."""
    from ..models import InventoryMovement

    if sale.sku_id is None:
        return 0
    rows = db.session.execute(
        select(InventoryMovement.kind, func.sum(InventoryMovement.quantity))
        .where(
            InventoryMovement.sku_id == sale.sku_id,
            InventoryMovement.source_type.in_(("sale", REPAID)),
            InventoryMovement.source_id == _sid(sale),
            InventoryMovement.kind.in_((MovementKind.commit, MovementKind.restock)),
        )
        .group_by(InventoryMovement.kind)
    ).all()
    n = {k: int(q or 0) for k, q in rows}
    return n.get(MovementKind.commit, 0) - n.get(MovementKind.restock, 0)


def _repay(sale: Sale, *, actor: Actor, reason: str | None) -> None:
    """A declined buyer paid another way. The unit leaves the shelf again only if the decline
    had put it back; one such round per sale (a ledger source holds each kind once)."""
    from .orders import OrderStatus

    if sale.sku_id is None:
        raise ReviewError(
            f"sale {sale.id} has no product yet; choose one in Review, then mark it paid", 409
        )
    if sale.order is not None and sale.order.status is OrderStatus.shipped:
        raise ReviewError(
            f"sale {sale.id}'s order already shipped without it; record a new sale to send "
            "it separately",
            409,
        )
    if units_out(sale) < sale.quantity:
        if _moved(sale, MovementKind.commit, REPAID):
            raise ReviewError(
                f"sale {sale.id} was already paid again after a decline once; "
                "record a new sale instead",
                409,
            )
        for kind in (MovementKind.reserve, MovementKind.commit):
            inventory.move(
                sale.sku_id,
                kind,
                sale.quantity,
                source_type=REPAID,
                source_id=_sid(sale),
                actor=actor,
                reason=reason,
            )
        sale.paid_at = datetime.now(UTC)


def _round(sale: Sale) -> str:
    """The ledger source of the sale's current payment: "sale", or REPAID once a declined
    payment went through after all."""
    from ..models import InventoryMovement

    again = db.session.scalar(
        select(InventoryMovement.id)
        .where(
            InventoryMovement.kind == MovementKind.commit,
            InventoryMovement.source_type == REPAID,
            InventoryMovement.source_id == _sid(sale),
        )
        .limit(1)
    )
    return REPAID if again is not None else "sale"


# ---------- the pass over a show ----------


class ParseBusy(Exception):
    """Another transaction is parsing this show right now (the worker, a person's "Parse
    now", or a second worker while a deploy overlaps the old one)."""


# The class half of the (class, show id) advisory lock: the two-int key space never meets
# `_lock_buyer`'s single 64-bit keys.
PARSE_LOCK = 0x5061_7273  # "Pars"


def _lock_show(show_id: int, *, wait: bool = False) -> bool:
    """Take this show's parse lock for the transaction; without `wait`, only if it is free.
    Two parses of one show at once would both read the same unparsed lines; the second then
    waited for the first's whole transaction and died on uq_parsed_events_message (a 500 for
    "Parse now")."""
    if wait:
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:cls, :show)"), {"cls": PARSE_LOCK, "show": show_id}
        )
        return True
    return bool(
        db.session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:cls, :show)"),
            {"cls": PARSE_LOCK, "show": show_id},
        )
    )


def process_show(
    show_id: int, *, reparse: bool = False, host: str | None = None, wait: bool = False
) -> Stats:
    """Parse the show's unparsed lines in the caller's transaction. When another transaction
    is parsing the same show: raises ParseBusy having changed nothing, or with `wait` waits
    for it to finish (inline jobs, which have no queue to come back through)."""
    show = db.session.get(Show, show_id)
    if show is None:
        raise ValueError(f"show {show_id} not found")
    if not _lock_show(show.id, wait=wait):
        raise ParseBusy(f"show {show_id} is being parsed")
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
    decided: bool | None = None  # asked once, and only if a decline turns up

    def left_to_whatnot() -> bool:
        """Whether Whatnot's own order for a win says, with nobody applying it, that the
        payment failed. A decline read from the stream (the player, the Sold tab, a host's
        line) then closes nothing: half the cards declined at the win are paid a minute
        later, and the order is what knows. It is flagged for a person all the same."""
        nonlocal decided
        if decided is None:
            from .whatnot import orders as whatnot_orders

            decided = whatnot_orders.decides(show)
        return decided

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
        if c.rule == "player_decline_untied":
            _review_untied_decline(show, m, (c.details or {}).get("text", ""))
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
                # A copy of a line a person already recorded must say so, or confirming
                # this "Not sure" item would sell the same win twice.
                if not from_player and (copy := _same_chat_line_near(show, m, ev)) is not None:
                    _review_chat_copy(show, m, ev, *copy)
                    stats.reviews += 1
                    continue
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
            if not from_player and (copy := _same_chat_line_near(show, m, ev)) is not None:
                _review_chat_copy(show, m, ev, *copy)
                stats.reviews += 1
                continue
            if from_player and (other := _same_win_elsewhere(show, ev, m)) is not None:
                read_as = (
                    ""
                    if (other.item_text or "").lower() == (ev.item_text or "").lower()
                    else f' (read there as "{other.item_text}")'
                )
                open_review(
                    show.organization_id,
                    "possible_duplicate",
                    f"{c.buyer} won {ev.item_text} for {money(c.price_cents)} — the same win "
                    f"is already sale #{other.id}{read_as} in another recording of this show "
                    "(a second laptop, or recording restarted); record it only if it is a "
                    "separate sale",
                    show_id=show.id,
                    message_id=m.id,
                    event_id=ev.id,
                )
                stats.reviews += 1
                continue
            if claim_order_sale(show, ev) is not None:
                continue  # Whatnot's order already made this sale; the line is its evidence
            # One win per auction. Only Whatnot's own "won the auction" banner counts: a
            # typed line names a lot too loosely.
            held = holders(show, ev.item_text, m.captured_at) if c.rule == "player_win" else []
            if any(s.buyer_username.lower() == c.buyer.lower() for s in held):
                continue  # this buyer's win of the lot is already recorded
            sku = match_sku(show.organization_id, ev.item_text) if ev.item_text else None
            sale = create_sale(show, ev, sku=sku)
            stats.sales += 1
            if held:
                # Other buyers hold the lot. The banner cannot say which win stands, so
                # nothing is cancelled here: a person is asked, and Whatnot's order decides.
                # The question sits on every sale in doubt, so neither order is picked.
                for earlier in held:
                    ask_which_win(show, earlier, sale)
                stats.reviews += 1
            if from_player:
                _flag_chat_sale_for_the_same_win(show, sale, m)
        elif c.kind in (EventKind.cancel, EventKind.payment_failed):
            if c.confidence >= 0.8:
                declined = c.kind is EventKind.payment_failed
                close_sale(
                    show,
                    ev,
                    SaleStatus.payment_failed if declined else SaleStatus.cancelled,
                    flag_only=declined and left_to_whatnot(),
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


# Untied declines open at once per show; more is noise (or someone typing variations).
MAX_UNTIED_REVIEWS = 10


def _review_untied_decline(show: Show, m: ChatMessage, text: str) -> None:
    """A decline the extension saw but could not tie to a win changes nothing: a person picks
    the sale. The sales whose buyer the text names come first as choices."""
    open_now = db.session.scalar(
        select(func.count())
        .select_from(ReviewItem)
        .where(
            ReviewItem.show_id == show.id,
            ReviewItem.reason == "decline_untied",
            ReviewItem.status == ReviewStatus.open,
        )
    )
    if (open_now or 0) >= MAX_UNTIED_REVIEWS:
        return
    words = {w.lstrip("@").lower() for w in text.split()}
    sales = list(
        db.session.scalars(
            select(Sale)
            .where(Sale.show_id == show.id, Sale.status.in_(CLOSABLE))
            .order_by(Sale.id.desc())
        )
    )
    named = [s for s in sales if s.buyer_username.lower() in words]
    choices = (named or sales)[:8]
    open_review(
        show.organization_id,
        "decline_untied",
        "Whatnot showed a declined payment the extension could not tie to a win: "
        f'"{text[:200]}" — pick the sale it is about, or ignore it',
        show_id=show.id,
        message_id=m.id,
        proposal={
            "status": SaleStatus.payment_failed.value,
            "candidates": [
                {
                    "saleId": s.id,
                    "label": f"#{s.id} {s.buyer_username}: {s.item_text or '?'} "
                    f"{money(s.price_cents)}",
                }
                for s in choices
            ],
        },
    )


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
    """A player-win sale in another show of the shop that is this win: two laptops (each
    laptop's show key is its own start time) or a restarted recording saw one auction.

    Same buyer, item and price within PLAYER_DUPLICATE_WINDOW; failing that, same buyer and
    price within PLAYER_NEAR_WINDOW whatever the item, because each laptop takes the title
    from its own first "won!" frame and one may already show the next lot's. The near match
    skips a sale this show already has its exact twin of (the other laptop missed a lot,
    and this is that second lot), and the same laptop's earlier recording (one laptop reads
    one display the same way; a restart never re-records the win on screen)."""
    twin = aliased(ParsedEvent)
    twin_line = aliased(ChatMessage)
    claimed = (
        select(twin.id)
        .join(twin_line, twin.message_id == twin_line.id)
        .where(
            twin.show_id == show.id,
            # Only a twin of that same auction: titles without a lot number repeat all show.
            twin_line.captured_at.between(
                ChatMessage.captured_at - PLAYER_DUPLICATE_WINDOW,
                ChatMessage.captured_at + PLAYER_DUPLICATE_WINDOW,
            ),
            twin.id != ev.id,
            twin.rule == "player_win",
            func.lower(twin.buyer_username) == func.lower(Sale.buyer_username),
            func.lower(twin.item_text) == func.lower(Sale.item_text),
            twin.price_cents == Sale.price_cents,
        )
        .exists()
    )
    near = [~claimed]
    if m.device_id is not None:
        near.append(or_(ChatMessage.device_id.is_(None), ChatMessage.device_id != m.device_id))

    def find(window, *same):
        return db.session.scalar(
            select(Sale)
            .join(ParsedEvent, Sale.event_id == ParsedEvent.id)
            .join(ChatMessage, ParsedEvent.message_id == ChatMessage.id)
            .where(
                Sale.organization_id == show.organization_id,
                Sale.show_id != show.id,
                ParsedEvent.rule == "player_win",
                func.lower(Sale.buyer_username) == ev.buyer_username.lower(),
                Sale.price_cents == ev.price_cents,
                ChatMessage.captured_at.between(m.captured_at - window, m.captured_at + window),
                *same,
            )
            .order_by(
                func.abs(func.extract("epoch", ChatMessage.captured_at - m.captured_at)), Sale.id
            )
            .limit(1)
        )

    exact = find(
        PLAYER_DUPLICATE_WINDOW, func.lower(Sale.item_text) == (ev.item_text or "").lower()
    )
    return exact if exact is not None else find(PLAYER_NEAR_WINDOW, *near)


def _same_chat_line_near(show: Show, m: ChatMessage, ev: ParsedEvent):
    """(sale, its line's captured_at) for a sale made from the same chat line (sender and
    text, ignoring case) within CHAT_LINE_DUPLICATE_WINDOW, in this show or another
    laptop's: the extension recorded the line again (a re-render gives the row a new id).
    Any status counts, or a re-rendered sale line would bring a cancelled sale back.

    The same text names the same item and price. A line that names no item takes it from
    the listing on screen, which does not tell a copy from a second win: a re-rendered row
    comes back after newer rows, under the next listing. So a copy under another listing
    is still asked about, and the summary shows both items."""
    conds = [
        Sale.organization_id == show.organization_id,
        ChatMessage.id != m.id,
        ChatMessage.source == "chat",
        ChatMessage.username == m.username,
        func.lower(ChatMessage.raw_text) == (m.raw_text or "").lower(),
        ChatMessage.captured_at.between(
            m.captured_at - CHAT_LINE_DUPLICATE_WINDOW,
            m.captured_at + CHAT_LINE_DUPLICATE_WINDOW,
        ),
    ]
    row = db.session.execute(
        select(Sale, ChatMessage.captured_at)
        .join(ParsedEvent, Sale.event_id == ParsedEvent.id)
        .join(ChatMessage, ParsedEvent.message_id == ChatMessage.id)
        .where(*conds)
        .order_by(Sale.id)
        .limit(1)
    ).first()
    return tuple(row) if row else None


def _review_chat_copy(show: Show, m: ChatMessage, ev: ParsedEvent, prev: Sale, prev_at) -> None:
    """Ask about a chat line that reads like an earlier sale's line, showing both, so a
    person can tell a re-captured line from a second win by the same buyer."""
    secs = int(abs((m.captured_at - prev_at).total_seconds()))
    when = "earlier" if prev_at <= m.captured_at else "later"
    this = f"{ev.item_text or 'no item named'}, {money(ev.price_cents)}"
    that = f"{prev.item_text or 'no item named'}, {money(prev.price_cents)}"
    other_listing = (
        " It came under another listing."
        if ev.item_text and prev.item_text and ev.item_text.lower() != prev.item_text.lower()
        else ""
    )
    open_review(
        show.organization_id,
        "possible_duplicate",
        f'"{m.raw_text}" ({this}) reads the same as the line that made sale #{prev.id} '
        f"({that}, {prev.status.value}) {secs} s {when}: either a copy (the chat re-rendered, "
        f"or a second laptop) or a second win by the same buyer.{other_listing} Record it "
        "if it is a separate sale",
        show_id=show.id,
        message_id=m.id,
        event_id=ev.id,
    )


def _flag_chat_sale_for_the_same_win(show: Show, sale: Sale, m: ChatMessage) -> None:
    """A sale the rules made from a chat line close in time to this player win, in this show
    or another laptop's, is probably the same win: ask, never cancel."""
    q = _player_sales_near(
        show.organization_id, sale.buyer_username, m.captured_at, CHAT_DUPLICATE_WINDOW
    )
    q = q.where(Sale.id != sale.id, ChatMessage.source == "chat", Sale.status.in_(CLOSABLE))
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
    show_id, reparse = int(payload["show_id"]), bool(payload.get("reparse"))
    try:
        # Inline (dev, e2e) there is no queue to come back through: wait for the holder.
        process_show(show_id, reparse=reparse, wait=inline())
    except ParseBusy:
        # Someone else holds the show: come back shortly rather than spend an attempt. The
        # holder may not see lines committed after it started, so this run is still needed.
        db.session.rollback()
        show = db.session.get(Show, show_id)
        if show is not None:
            schedule_parse(show, reparse=reparse, delay_s=BUSY_RETRY_S)


BUSY_RETRY_S = 5


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


def _id(value) -> int:
    """An id from a request body: a whole number, or a 400 (never a 500)."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ReviewError("expected a numeric id")
    try:
        return int(value)
    except ValueError:
        raise ReviewError("expected a numeric id") from None


def _status(value) -> SaleStatus:
    try:
        return SaleStatus(value)
    except ValueError:
        raise ReviewError(f"unknown sale status {str(value)[:40]!r}") from None


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


def resolve(item: ReviewItem, action: str, body: dict, *, user, actor=None) -> ReviewItem:
    """A decision on a review item. `actor` is given only when it is not a person's:
    Whatnot's own listing naming a product (`name_from_listing`); `user` is then None."""
    # One decision at a time per item, taken on its committed state: two people settling the
    # same lot with different products must not both move stock. The item and its sale are
    # locked before anything is checked, so the second person gets a 409 and moves nothing.
    db.session.refresh(item, with_for_update=True)
    if item.status is not ReviewStatus.open:
        raise ReviewError("already resolved", 409)
    if item.sale is not None:
        db.session.refresh(item.sale, with_for_update=True)
    actor = actor or Actor.user(user)
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
            "reserved": sale.status in (SaleStatus.reserved, SaleStatus.paid),
        }
    elif action == "assign_sku":
        sale = item.sale
        if sale is None:
            raise ReviewError("this item has no sale to assign")
        if sale.status not in OPEN:
            raise ReviewError(f"sale {sale.id} is already {sale.status.value}", 409)
        sku = catalog.get_sku(item.organization_id, _id(body.get("skuId")))
        if sale.status is SaleStatus.reserved and sale.sku_id and sale.sku_id != sku.id:
            inventory.release(
                sale.sku_id,
                sale.quantity,
                source_type="sale",
                source_id=_sid(sale),
                actor=actor,
                reason="reassigned",
            )
            from . import sale_items

            sale_items.close(sale)  # the next product gets a row, and ledger keys, of its own
        sale.sku = sku
        sale.sku_id = sku.id  # _reserve reads the FK before the relationship is flushed
        sale.flagged = False
        if sale.price_cents is None:
            sale.price_cents = sku.price_cents
        # A title that names one lot of one show is never learned: lot numbers start again
        # in every show, so the next show's #520 would get this product with nobody looking.
        if body.get("remember") and sale.item_text and not rules.names_a_lot(sale.item_text):
            catalog.add_alias(item.organization_id, sku, sale.item_text, quiet=True)
        ok = _reserve(sale)
        autopay(sale, at=_won_at(sale.event) if sale.event else None)
        item.status = ReviewStatus.resolved
        item.resolution = {"action": "assign_sku", "skuId": sku.id, "reserved": ok}
    elif action == "pick_sale":
        # Only one of the sales this item offered: the choice is the item's, not any sale's.
        offered = {
            c.get("saleId")
            for c in (item.proposal or {}).get("candidates", [])
            if isinstance(c, dict)
        }
        sale_id = _id(body.get("saleId"))
        if item.reason not in ("ambiguous_sale", "decline_untied") or sale_id not in offered:
            raise ReviewError("that sale is not one of this item's choices", 400)
        sale = db.session.get(Sale, sale_id)
        if sale is None or sale.organization_id != item.organization_id:
            raise ReviewError("sale not found", 404)
        status = SaleStatus((item.proposal or {}).get("status", "cancelled"))
        set_sale_status(sale, status, reason=f"review #{item.id}", actor=actor)
        if status is SaleStatus.payment_failed:
            review_decline(sale)
        item.status = ReviewStatus.resolved
        item.resolution = {"action": "pick_sale", "saleId": sale.id, "status": status.value}
    elif action == "apply_proposal":
        from .supervisor import apply_proposal

        apply_proposal(item, actor=actor, user=user)
    elif action == "set_status":
        sale = item.sale
        if sale is None:
            raise ReviewError("this item has no sale")
        if body.get("skuId") and sale.sku_id is None and sale.status is SaleStatus.payment_failed:
            # A declined win whose item was never recognised: say what it was, then restore it.
            sku = catalog.get_sku(item.organization_id, _id(body["skuId"]))
            sale.sku = sku
            sale.sku_id = sku.id
            sale.flagged = False
            if sale.price_cents is None:
                sale.price_cents = sku.price_cents
        set_sale_status(sale, _status(body.get("status")), reason=f"review #{item.id}", actor=actor)
        item.status = ReviewStatus.resolved
        item.resolution = {"action": "set_status", "status": sale.status.value}
        if item.reason == LOT_WON_TWICE and sale.status not in CLOSABLE:
            settle_lot_question(sale)
    else:
        raise ReviewError(f"unknown action {action!r}")
    item.resolved_by_id = user.id if user is not None else None
    item.resolved_at = datetime.now(UTC)
    return item


def similar_open_items(item: ReviewItem) -> list[ReviewItem]:
    """The other open "item not recognised" decisions in the same show whose lot title is this
    one's without the lot number ("$1 BEAUTY PULLS #521" next to "#520"). Only a person may
    treat them as one product; the rules never assume it (lot #761 may be another bottle)."""
    sale = item.sale
    if sale is None or item.show_id is None or not sale.item_text:
        return []
    key = rules.item_title(sale.item_text).lower()
    if not key:
        return []
    others = db.session.scalars(
        select(ReviewItem)
        .where(
            ReviewItem.organization_id == item.organization_id,
            ReviewItem.show_id == item.show_id,
            ReviewItem.status == ReviewStatus.open,
            ReviewItem.reason == "unmatched_item",
            ReviewItem.id != item.id,
        )
        .order_by(ReviewItem.id)
    ).all()
    return [
        o
        for o in others
        if o.sale is not None
        and o.sale.sku_id is None
        and o.sale.status in OPEN
        and rules.item_title(o.sale.item_text or "").lower() == key
    ]


# One request names at most this many extra lots: each one is a reserve, a payment and an
# order refresh inside a single transaction that holds the SKU row.
SIMILAR_LIMIT = 300
# The class half of the (class, show id) advisory lock for "name it once" runs.
SIMILAR_LOCK = 0x5369_6D69  # "Simi"


def lock_similar(show_id: int) -> None:
    """One "name it once" run per show at a time (taken before the first lot is resolved, so a
    second run waits and then finds its lots already settled instead of deadlocking)."""
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :show)"), {"cls": SIMILAR_LOCK, "show": show_id}
    )


def resolve_similar(
    item: ReviewItem, sku_id: int, *, remember: bool, user, only: set[int] | None = None
) -> list[ReviewItem]:
    """After `item` got its SKU: give the same SKU to the similar open lots of the show, each
    through resolve(), so stock, payment and the order move exactly as for a single pick.
    `only` narrows that to the review items the person was shown (a lot that appeared in
    the meantime is left alone); a lot in `only` that is not similar any more is skipped."""
    similar = similar_open_items(item)
    if only is not None:
        similar = [o for o in similar if o.id in only]
    if len(similar) > SIMILAR_LIMIT:
        raise ReviewError(f"name up to {SIMILAR_LIMIT} other lots at a time", 400)
    done = []
    for other in similar:
        done.append(
            resolve(other, "assign_sku", {"skuId": sku_id, "remember": remember}, user=user)
        )
    return done
