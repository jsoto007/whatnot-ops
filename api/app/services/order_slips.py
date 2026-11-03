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
- Once the scraper has read Whatnot's Shipments table for the show (services/shipments.py),
  each slip carries Whatnot's item count for that buyer, and the document lists buyers only
  one side knows. Shown on screen for a person; nothing is changed by it.
"""

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import ChatMessage, EventKind, Order, ParsedEvent, Sale, SaleStatus, Show, Sku
from . import rules, shipments
from .orders import STANDING

NO_PICK = "No Pick"


def product_label(sale: Sale) -> str:
    """The Product Checklist text: the matched product (and variant), "No Pick" when the
    title only names the lot, else what the line said."""
    if sale.sku is not None:
        product, variant = sale.sku.product.name.strip(), (sale.sku.name or "").strip()
        if variant and variant.lower() not in (product.lower(), "default"):
            return f"{product} - {variant}"
        return product
    item = (sale.item_text or "").strip()
    return NO_PICK if not item or rules.is_lot_title(item) else item


def _won_at(sale: Sale):
    return sale.event.message.captured_at if sale.event else sale.created_at


def build(show: Show) -> dict:
    sales = db.session.scalars(
        select(Sale)
        .where(Sale.show_id == show.id, Sale.status.in_(STANDING))
        .options(
            selectinload(Sale.sku).selectinload(Sku.product),
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

    for s in sorted(sales, key=lambda s: (_won_at(s), s.id)):
        b = buyer(s.buyer_username)
        b["needsReview"] |= s.status is SaleStatus.detected
        auction = rules.lot_label(s.item_text, s.auction_type)
        product = product_label(s)
        for unit in range(max(1, s.quantity)):
            first = unit == 0
            b["rows"].append(
                {
                    "kind": "sale",
                    "saleId": s.id,
                    "auction": auction,
                    "product": product,
                    "matched": s.sku_id is not None,
                    "giftCount": s.gift_count if first else 0,
                    # The lot's price once, on its first row; the sheet shows "-" after.
                    "priceCents": (
                        s.price_cents * s.quantity if first and s.price_cents is not None else None
                    ),
                    "continued": not first,
                }
            )
    for name, item, _ in gifts:
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
            }
        )

    whatnot = shipments.for_show(show)
    seen = whatnot["buyers"] if whatnot else {}
    slips = []
    for key, b in by_buyer.items():
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
    return {
        "whatnot": (
            {
                "readAt": whatnot["readAt"],
                "shipments": whatnot["shipments"],
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
