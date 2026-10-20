"""Deterministic chat-line classifier.

Pure functions over (username, body, raw_text): no database. Patterns are
ordered; the first match wins. Each rule names itself so an event can say
which rule produced it, and the corpus test in tests/test_rules.py reports
precision/recall per kind. Tune here, never in the pipeline.

The corpus so far is synthetic (built from Whatnot's visible conventions and
the team's sheet vocabulary) — see tests/fixtures/chat_corpus.jsonl. Rules
must be re-checked against real captured shows before relying on them.
"""

import re
from dataclasses import dataclass, field

from ..models import EventKind

USER = r"@?(?P<buyer>[A-Za-z0-9._-]{2,40})"
ITEM = r"(?P<item>.+?)"
PRICE = r"(?:\s*(?:for|@|at)?\s*\$\s?(?P<price>\d{1,5}(?:[.,]\d{2})?))"
QTY_IN_ITEM = re.compile(
    r"(?:^|\s)(?:x\s?(?P<a>\d{1,3})|(?P<b>\d{1,3})\s?x|\((?P<c>\d{1,3})\)|qty\s?(?P<d>\d{1,3}))(?=\s|$)",
    re.I,
)

# Order = priority when several words appear ("Red bundle" counts as red). The
# sheet tracks Red, Bundles and Ultra/Mega as separate tallies; confirm the
# precedence with the team once real shows are captured.
AUCTION_TYPES = [
    ("red", re.compile(r"\bred\b", re.I)),
    ("mega", re.compile(r"\bmega\b", re.I)),
    ("ultra", re.compile(r"\bultra\b", re.I)),
    ("bundle", re.compile(r"\bbundle\b", re.I)),
]


@dataclass
class Classification:
    kind: EventKind
    rule: str
    buyer: str = ""
    item: str = ""
    quantity: int = 1
    price_cents: int | None = None
    auction_type: str = ""
    confidence: float = 1.0
    details: dict = field(default_factory=dict)


def _price(s: str | None) -> int | None:
    if not s:
        return None
    return int(round(float(s.replace(",", ".")) * 100))


def _qty_and_item(item: str) -> tuple[int, str]:
    m = QTY_IN_ITEM.search(item)
    if not m:
        return 1, item.strip(" .!,-")
    qty = int(next(g for g in m.groups() if g))
    cleaned = (item[: m.start()] + " " + item[m.end() :]).strip(" .!,-")
    return max(1, qty), re.sub(r"\s+", " ", cleaned)


def auction_type(text: str) -> str:
    for name, rx in AUCTION_TYPES:
        if rx.search(text):
            return name
    return ""


# ---- platform / host confirmation lines (a sale) ----
SALE_PATTERNS = [
    # "X won the auction!" (no item named; the pipeline fills it from the last listing)
    ("won_auction", re.compile(rf"^(?:🎉\s*)?{USER}\s+won\s+the\s+auction{PRICE}?\s*[!.]*$", re.I)),
    (
        "won_item_for",
        re.compile(
            rf"^(?:🎉\s*)?{USER}\s+(?:won|purchased|bought|got)\s+(?:the\s+)?(?:auction\s+for\s+)?{ITEM}{PRICE}\s*[!.]*$",
            re.I,
        ),
    ),
    (
        "won_item",
        re.compile(
            rf"^(?:🎉\s*)?{USER}\s+(?:won|purchased|bought|got)\s+(?:the\s+)?(?:auction\s+for\s+)?{ITEM}\s*[!.]*$",
            re.I,
        ),
    ),
    (
        "sold_to",
        re.compile(rf"^(?:sold|goes)\s+to\s+{USER}(?:\s*[-:–]\s*{ITEM})?{PRICE}?\s*[!.]*$", re.I),
    ),
    ("item_sold_to", re.compile(rf"^{ITEM}\s+(?:sold|goes)\s+to\s+{USER}{PRICE}?\s*[!.]*$", re.I)),
]
GIFT_PATTERNS = [
    ("won_giveaway", re.compile(rf"^(?:🎉\s*)?{USER}\s+won\s+the\s+giveaway\s*[!.]*$", re.I)),
    (
        "gift_to",
        re.compile(
            rf"^(?:gift(?:ed|ing)?|free(?:bie)?)\s+(?:to\s+|for\s+)?{USER}(?:\s*[-:–]\s*{ITEM})?\s*[!.]*$",
            re.I,
        ),
    ),
    (
        "user_gift",
        re.compile(
            rf"^{USER}\s+(?:gets?|wins?)\s+(?:a\s+)?(?:gift|freebie)\b(?:\s*[-:–]\s*{ITEM})?\s*[!.]*$",
            re.I,
        ),
    ),
]
CANCEL_PATTERNS = [
    (
        "cancelled",
        re.compile(
            rf"^(?:order\s+)?(?:cancel(?:led|ed)?|refund(?:ed)?)\s+(?:for\s+)?{USER}(?:\s*[-:–]\s*{ITEM})?\s*[!.]*$",
            re.I,
        ),
    ),
    (
        "user_cancelled",
        re.compile(
            rf"^{USER}\s+(?:cancel(?:led|ed)?|backed\s+out|refunded)(?:\s+{ITEM})?\s*[!.]*$", re.I
        ),
    ),
]
PAYMENT_PATTERNS = [
    (
        "payment_failed",
        re.compile(
            rf"^(?:payment\s+(?:failed|declined)|unpaid|didn'?t\s+pay|no\s+payment)\s+(?:for\s+|from\s+)?{USER}(?:\s*[-:–]\s*{ITEM})?\s*[!.]*$",
            re.I,
        ),
    ),
    (
        "user_payment_failed",
        re.compile(
            rf"^{USER}\s*[-:–]?\s*(?:payment\s+(?:failed|declined)|unpaid|didn'?t\s+pay|card\s+declined)\s*[!.]*$",
            re.I,
        ),
    ),
]
NO_PICK_PATTERNS = [
    (
        "no_pick",
        re.compile(
            r"^(?:no\s*-?\s*pick|no\s+(?:bids?|winner|takers)|auction\s+ended\s+with\s+no\s+winner|passed)(?:\s*[-:–]\s*(?P<item>.+))?\s*[!.]*$",
            re.I,
        ),
    ),
]
LISTING_PATTERNS = [
    (
        "listing",
        re.compile(
            r"^(?:auction|now\s+selling|up\s+next|next\s+up|selling|lot)\s*(?:#?\s*(?P<num>\d{1,4}))?\s*[-:–]\s*(?P<item>.+?)\s*$",
            re.I,
        ),
    ),
]
CLAIM_PATTERNS = [
    (
        "claim_qty",
        re.compile(
            r"^(?:i(?:'?ll| will)?\s+take|take|i want|gimme|give me|claim|claiming)\s+(?P<qty>\d{1,3})(?:\s+(?:please|pls|plz))?\s*[!.]*$",
            re.I,
        ),
    ),
    (
        "claim",
        re.compile(
            r"^(?:mine|me|sold|claim|claimed|dibs|i(?:'?ll| will)? take it|take it|i want it|yes|yup|yes please)\s*[!.]*$",
            re.I,
        ),
    ),
    ("claim_number", re.compile(r"^(?P<qty>\d{1,3})\s*(?:please|pls|plz)?\s*[!.]*$", re.I)),
]


def _build(
    kind: EventKind, rule: str, m: re.Match, *, confidence: float = 1.0, text: str = ""
) -> Classification:
    g = m.groupdict()
    qty, item = _qty_and_item(g.get("item") or "")
    return Classification(
        kind=kind,
        rule=rule,
        buyer=(g.get("buyer") or "").lstrip("@"),
        item=item,
        quantity=qty,
        price_cents=_price(g.get("price")),
        auction_type=auction_type(item or text),
        confidence=confidence,
        details={k: v for k, v in g.items() if v and k not in ("buyer", "item", "price")},
    )


def classify(username: str, body: str, raw_text: str, *, host: str | None = None) -> Classification:
    """Classify one chat line. `host` is the seller's username when known (host lines carry more weight)."""
    text = (body or raw_text or "").strip()
    is_system = not username  # platform lines have no username element
    is_host = bool(host) and username.lower() == (host or "").lower()
    authority = 1.0 if (is_system or is_host) else 0.6

    for groups, kind in (
        (GIFT_PATTERNS, EventKind.gift),  # "won the giveaway" before "won …"
        (SALE_PATTERNS, EventKind.sale),
        (CANCEL_PATTERNS, EventKind.cancel),
        (PAYMENT_PATTERNS, EventKind.payment_failed),
        (NO_PICK_PATTERNS, EventKind.no_pick),
        (LISTING_PATTERNS, EventKind.listing),
    ):
        for rule, rx in groups:
            m = rx.match(text)
            if m:
                return _build(kind, rule, m, confidence=authority, text=text)
    for rule, rx in CLAIM_PATTERNS:
        m = rx.match(text)
        if m:
            c = _build(EventKind.claim, rule, m, confidence=0.9, text=text)
            c.buyer = username
            c.quantity = int(m.groupdict().get("qty") or 1)
            return c
    return Classification(kind=EventKind.chatter, rule="none", buyer=username, confidence=1.0)
