"""The show's "Order Slips": the per-show packing document staff used to build by hand from
Whatnot's shipments tab (first seen for the 10-31-2026 show).

A cover with the auction date, Total Buyers / Items / Gifts and a Username · Total Items ·
Total Gifts table (A-Z), then one slip per buyer (most items first): a row per item to pack
with Auction Date, Auction Number, Username, Product Checklist, Gift and Notes (the price).

- Every unit is its own row; a lot's price is on its first row and "-" on the others, as on
  the sheet for two-pick Ultras and Bundles.
- A giveaway the buyer won is a "Gift" row and counts as an item: it ships in the box.
- "Gift N" on a row is how many free gifts go in with that lot (`Sale.gift_count`, set by a
  person); a buyer's Total Gifts is their sum.
- Cancelled and declined sales do not ship and are left out.
- Once the scraper has read Whatnot's Shipments table for the show with its lots
  (services/shipments.py), the slips are the final pick list: one row per Whatnot lot (its
  title, order number and price), with the product from the matching sale: its SKU, else
  "No Pick" plus what the winner said in chat after the win, for the packer to pick (see
  `_said` and services/sale_chat.py). A lot the app never recorded is still a row
  (flagged), with the lines that name its number; a sale Whatnot does not list stays too,
  flagged "not on Whatnot". Matching never changes a sale: it is read-only.
- Without a lot read (or from an older scraper) the slips come from the app's sales, as
  before, with Whatnot's per-buyer counts beside them, and the same chat on "No Pick" rows.
- `only="ready"` keeps the buyers whose order is Ready to pick (every item paid and named,
  picking not started) and whose slip has nothing waiting for a person: no "No Pick" row (a
  lot the app never recorded has no product), and no sale Whatnot does not list. It is the
  Orders page's "Print ready slips". The cover's totals follow; the Whatnot check stays
  show-wide, and `leftOut` says how many buyers were skipped.
"""

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import (
    ChatMessage,
    EventKind,
    Order,
    OrderStatus,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleItem,
    SaleStatus,
    Show,
    Sku,
    WhatnotLot,
)
from . import giveaways, rules, sale_chat, sale_items, shipments
from .orders import STANDING
from .whatnot import connection

NO_PICK = "No Pick"
# `only=`: which buyers to keep. "ready" = the order is Ready to pick (the Orders page's queue).
READY = "ready"
FILTERS = frozenset({READY})
# One printed line per unit of a lot, up to this many; past it the buyer is flagged.
MAX_UNIT_ROWS = 50


def _sku_name(sku: Sku) -> str:
    product, variant = sku.product.name.strip(), (sku.name or "").strip()
    if variant and variant.lower() not in (product.lower(), "default"):
        return f"{product} - {variant}"
    return product


def product_label(sale: Sale) -> str:
    """The Product Checklist text: the matched product (and variant), "No Pick" when the
    title only names the lot, else what the line said."""
    if sale.sku is not None:
        # A win with several products ("two for one") lists them all in one text.
        return sale_items.label(sale, _sku_name)
    item = (sale.item_text or "").strip()
    return NO_PICK if not item or rules.is_lot_title(item) else item


def _lines(sale: Sale | None, product: str, units: int) -> list[str]:
    """The Product Checklist lines of one win, as the paper sheet has them: a line per unit
    of its product, or, when the win holds several products (or a count its own quantity
    does not say), a line per product with its count. The price goes on the first line."""
    if sale is not None and sale.sku is not None and sale_items.spelled(sale):
        return [
            _sku_name(p["sku"]) + (f" ×{p['quantity']}" if p["quantity"] > 1 else "")
            for p in sale_items.products(sale)
        ]
    return [product] * min(max(1, units), MAX_UNIT_ROWS)


def _won_at(sale: Sale):
    return sale.event.message.captured_at if sale.event else (sale.ordered_at or sale.created_at)


_SPACES = re.compile(r"\s+")
SAID_LINES = 4


def _same_title(a: str, b: str) -> bool:
    return (
        _SPACES.sub(" ", (a or "").strip()).lower() == _SPACES.sub(" ", (b or "").strip()).lower()
    )


def lot_product(sale: Sale | None, lot: WhatnotLot) -> str:
    """The Product Checklist text for a Whatnot lot: what the matching sale names, "No Pick"
    when nothing does (no sale, or its text is only the listing title)."""
    if sale is None:
        return NO_PICK
    if sale.sku is None and _same_title(sale.item_text, lot.title):
        return NO_PICK
    return product_label(sale)


def match_lots(lots: list[WhatnotLot], sales: list[Sale]) -> dict[int, Sale]:
    """Whatnot lot id → the app's sale for it, each sale used once. Tried in order: Whatnot's
    order number on the sale (from the payment file), then the same buyer and lot number
    with the same price, then the same buyer and lot number."""
    free = {s.id: s for s in sales}
    out: dict[int, Sale] = {}
    by_order = {s.external_order_id: s for s in sales if s.external_order_id}
    for lot in lots:
        s = by_order.get(lot.whatnot_order_id)
        if s is not None and s.id in free:
            out[lot.id] = free.pop(s.id)
    # Indexes keyed by exactly what a match compares, each list in `sales` order, so the
    # first free entry is the one a front-to-back scan would have found.
    by_buyer_lot: dict[tuple[str, int], list[Sale]] = defaultdict(list)
    by_buyer_lot_price: dict[tuple[str, int, int], list[Sale]] = defaultdict(list)
    for s in sales:
        n = sale_chat.lot_of(s)
        if n is None:
            continue
        buyer = (s.buyer_username or "").lower()
        by_buyer_lot[(buyer, n)].append(s)
        if s.price_cents is not None:
            by_buyer_lot_price[(buyer, n, s.price_cents)].append(s)

    def take(lot: WhatnotLot, same_price: bool) -> None:
        buyer = (lot.recipient or "").lower()
        if same_price:
            found = by_buyer_lot_price.get((buyer, lot.lot_number, lot.price_cents), ())
        else:
            found = by_buyer_lot.get((buyer, lot.lot_number), ())
        for s in found:
            if s.id in free:
                out[lot.id] = free.pop(s.id)
                return

    for same_price in (True, False):
        for lot in lots:
            if lot.id not in out and lot.lot_number is not None:
                take(lot, same_price)
    return out


def _said(lines: list[dict]) -> tuple[list[str], int]:
    """Where the pick is: what the winner (or the host, about them) said after the win, and
    before it only when naming the lot. At most SAID_LINES are printed: with more, the
    first one (the answer to the win) and the last ones (a changed mind comes last), plus
    how many were left out between them."""
    said = [
        c["body"] for c in lines if c["role"] in ("buyer", "host") and (c["after"] or c["namesLot"])
    ]
    if len(said) <= SAID_LINES:
        return said, 0
    kept = said[:1] + said[-(SAID_LINES - 1) :]
    return kept, len(said) - len(kept)


_GIVEAWAY = re.compile(r"give\s*-?\s*away|gift", re.I)


def _ready(b: dict) -> bool:
    """A slip that can be printed and picked as it is: the buyer's order is Ready to pick,
    every row has its product, and every sale on it is one Whatnot lists (when its lots were
    read). A gift row never holds a slip back. A slip in doubt is not ready: the stream
    showed a card declined, or a lot won by two buyers, and Whatnot's order has not said yet
    (`orders.advance` refuses to pick that order for the same reason)."""
    return (
        b["orderStatus"] == OrderStatus.paid.value
        and not b["needsReview"]
        and not b.get("cardInDoubt")
        and all(r["onWhatnot"] for r in b["rows"] if r["kind"] == "sale")
    )


def build(show: Show, *, only: str | None = None) -> dict:
    if only is not None and only not in FILTERS:
        raise ValueError("unknown filter")
    sales = db.session.scalars(
        select(Sale)
        .where(Sale.show_id == show.id, Sale.status.in_(STANDING))
        .options(
            selectinload(Sale.sku).selectinload(Sku.product),
            selectinload(Sale.open_items).selectinload(SaleItem.sku).selectinload(Sku.product),
            selectinload(Sale.event).selectinload(ParsedEvent.message),
        )
    ).all()
    gifts = db.session.execute(
        select(ParsedEvent.buyer_username, ParsedEvent.item_text, ChatMessage.captured_at)
        .join(ChatMessage, ChatMessage.id == ParsedEvent.message_id)
        .where(
            ParsedEvent.show_id == show.id,
            ParsedEvent.kind == EventKind.gift,
            ParsedEvent.confidence >= 0.8,
            ParsedEvent.buyer_username != "",
        )
        .order_by(ChatMessage.captured_at, ParsedEvent.id)
    ).all()
    orders = {
        name.lower(): (oid, status)
        for oid, name, status in db.session.execute(
            select(Order.id, Order.buyer_username, Order.status).where(Order.show_id == show.id)
        ).all()
    }

    by_buyer: dict[str, dict] = {}

    def buyer(name: str) -> dict:
        key = name.lower()
        if key not in by_buyer:
            oid, status = orders.get(key, (None, None))
            by_buyer[key] = {
                "username": name,
                "orderId": oid,
                "orderStatus": status.value if status else None,
                "needsReview": False,
                "rows": [],
            }
        return by_buyer[key]

    whatnot = shipments.for_show(show)
    lots = whatnot["lots"] if whatnot else []
    matched = match_lots(lots, list(sales)) if lots else {}
    used = {s.id for s in matched.values()}
    # Every standing sale: which win a line goes to depends on the buyer's other wins.
    chat = sale_chat.around(list(sales), limit=None)
    no_win = [lot for lot in lots if lot.id not in matched]
    lot_chat = sale_chat.about_lots(show, no_win) if no_win else {}

    def pick_chat(lines: list[dict], product: str, first: bool) -> dict:
        kept, more = _said(lines) if first and product == NO_PICK else ([], 0)
        return {"said": kept, "saidMore": more}

    # Whatnot's own giveaways (its orders), each with the product a person chose for it.
    given = giveaways.for_show(show)
    named = {gv["order"]: gv["product"] for gv in given if gv["product"]}
    whatnot_gifts = False
    for lot in lots:
        s = matched.get(lot.id)
        b = buyer(lot.recipient)
        gift = bool(_GIVEAWAY.search(lot.sale_type))
        whatnot_gifts |= gift
        product = lot_product(s, lot)
        chosen = named.get(lot.whatnot_order_id) if gift else None
        if gift and product == NO_PICK:
            product = chosen or "Gift"
        b["needsReview"] |= (
            (product == NO_PICK)
            or (s is not None and s.status is SaleStatus.detected)
            # More units than a box holds is a misread Qty column, not 999 lines to print.
            or lot.quantity > MAX_UNIT_ROWS
        )
        for unit, line in enumerate(_lines(s, product, lot.quantity)):
            first = unit == 0
            b["rows"].append(
                {
                    "kind": "gift" if gift else "sale",
                    "saleId": s.id if s else None,
                    "auction": "Gift"
                    if gift
                    else rules.lot_label(lot.title, rules.auction_type(lot.title)),
                    "product": line,
                    "matched": bool(s and s.sku_id is not None) or bool(chosen),
                    "giftCount": s.gift_count if s and first else 0,
                    "priceCents": lot.price_cents if first else None,
                    "continued": not first,
                    "whatnotOrder": lot.whatnot_order_id,
                    "inApp": s is not None,
                    "onWhatnot": True,
                    **pick_chat(
                        chat.get(s.id, []) if s else lot_chat.get(lot.id, []), product, first
                    ),
                }
            )

    for s in sorted(sales, key=lambda s: (_won_at(s), s.id)):
        if s.id in used:
            continue
        b = buyer(s.buyer_username)
        b["needsReview"] |= s.status is SaleStatus.detected or s.quantity > MAX_UNIT_ROWS
        auction = rules.lot_label(s.item_text, s.auction_type)
        product = product_label(s)
        for unit, line in enumerate(_lines(s, product, s.quantity)):
            first = unit == 0
            b["rows"].append(
                {
                    "kind": "sale",
                    "saleId": s.id,
                    "auction": auction,
                    "product": line,
                    "matched": s.sku_id is not None,
                    "giftCount": s.gift_count if first else 0,
                    # The lot's price once, on its first row; the sheet shows "-" after.
                    "priceCents": (
                        s.price_cents * s.quantity if first and s.price_cents is not None else None
                    ),
                    "continued": not first,
                    "whatnotOrder": None,
                    "inApp": True,
                    # With a lot read, a sale Whatnot does not list is flagged on the slip.
                    "onWhatnot": not lots,
                    **pick_chat(chat.get(s.id, []), product, first),
                }
            )
    # Whatnot's own giveaways replace the ones read from chat: its lots (the Shipments table)
    # when that lists any, else its orders. A row per unit, with the product once chosen.
    for gv in [] if whatnot_gifts else given:
        for unit in range(min(gv["quantity"], MAX_UNIT_ROWS)):
            buyer(gv["buyer"])["rows"].append(
                {
                    "kind": "gift",
                    "saleId": None,
                    "auction": "Gift",
                    "product": gv["product"] or "Gift",
                    "matched": gv["product"] is not None,
                    "giftCount": 0,
                    "priceCents": None,
                    "continued": unit > 0,
                    "whatnotOrder": gv["order"],
                    "inApp": True,
                    "onWhatnot": True,
                    "said": [],
                    "saidMore": 0,
                }
            )
    for name, item, _ in [] if whatnot_gifts or given else gifts:
        buyer(name)["rows"].append(
            {
                "kind": "gift",
                "saleId": None,
                "auction": "Gift",
                "product": (item or "").strip() or "Gift",
                "matched": False,
                "giftCount": 0,
                "priceCents": None,
                "continued": False,
                "whatnotOrder": None,
                "inApp": True,
                "onWhatnot": not lots,
                "said": [],
                "saidMore": 0,
            }
        )

    seen = whatnot["buyers"] if whatnot else {}
    from .pipeline import DECLINE_SEEN, LOT_WON_TWICE

    in_doubt = set(
        db.session.scalars(
            select(Sale.order_id)
            .join(ReviewItem, ReviewItem.sale_id == Sale.id)
            .where(
                Sale.show_id == show.id,
                Sale.status.in_(STANDING),
                ReviewItem.status == ReviewStatus.open,
                ReviewItem.reason.in_((DECLINE_SEEN, LOT_WON_TWICE)),
            )
        )
    )
    slips = []
    for key, b in by_buyer.items():
        b["cardInDoubt"] = b["orderId"] in in_doubt
        b["items"] = len(b["rows"])
        b["gifts"] = sum(r["giftCount"] for r in b["rows"])
        w = seen.get(key)
        # Whatnot's Shipments table for this buyer: None = not read yet, or not listed there.
        b["whatnot"] = (
            {"items": None if w["unknownItems"] else w["items"], "statuses": w["statuses"]}
            if w
            else None
        )
        slips.append(b)
    slips.sort(key=lambda b: (-b["items"], b["username"].lower()))
    left_out = 0
    ready = sum(1 for b in slips if _ready(b))
    if only == READY:
        kept = [b for b in slips if _ready(b)]
        left_out = len(slips) - len(kept)
        slips = kept
    return {
        "only": only,
        # Buyers of the show the filter skipped (in progress, shipped, in Review, gift only,
        # or with a row that still needs a person).
        "leftOut": left_out,
        # How many of the show's slips are ready to pick, whichever buyers this document holds.
        "ready": ready,
        "whatnot": (
            {
                "readAt": whatnot["readAt"],
                "shipments": whatnot["shipments"],
                "lots": len(lots),
                # Lots with no sale in the app (a win chat never showed) and the reverse.
                "lotsNotInApp": sum(1 for lot in lots if lot.id not in matched),
                "salesNotOnWhatnot": (sum(1 for s in sales if s.id not in used) if lots else 0),
                # On Whatnot's list but without a slip here: a win the app never recorded.
                "missingHere": sorted(
                    (w["username"] for k, w in seen.items() if k not in by_buyer), key=str.lower
                ),
                # A slip here that Whatnot does not list: cancelled there, or misread here.
                "notOnWhatnot": sorted(
                    (b["username"] for k, b in by_buyer.items() if k not in seen), key=str.lower
                ),
            }
            if whatnot
            else None
        ),
        "showId": show.id,
        "showDate": show.show_date.isoformat(),
        # The app reads the show's parcels from Whatnot itself (the shop is connected), so
        # nobody has to send them from the laptop.
        "parcelsFromWhatnot": connection.active(show.organization_id) is not None,
        "totals": {
            "buyers": len(slips),
            "items": sum(b["items"] for b in slips),
            "gifts": sum(b["gifts"] for b in slips),
        },
        "summary": [
            {"username": b["username"], "items": b["items"], "gifts": b["gifts"]}
            for b in sorted(slips, key=lambda b: b["username"].lower())
        ],
        "slips": slips,
    }


MAX_GIFTS = 20


def set_gift_count(sale: Sale, count: int) -> int:
    """How many free gifts go in with this lot. Returns the previous count."""
    if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= MAX_GIFTS:
        raise ValueError(f"gifts must be a whole number from 0 to {MAX_GIFTS}")
    before = sale.gift_count
    sale.gift_count = count
    return before
