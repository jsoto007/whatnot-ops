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
PRICE = r"(?:(?:\s+(?:for|@|at))?\s*\$\s?(?P<price>\d{1,5}(?:[.,]\d{2})?))"
QTY_IN_ITEM = re.compile(
    r"(?:^|\s)(?:x\s?(?P<a>\d{1,3})|(?P<b>\d{1,3})\s?x|\((?P<c>\d{1,3})\)|qty\s?(?P<d>\d{1,3}))(?=\s|$)",
    re.I,
)

ITEM_MAX = 240  # ParsedEvent.item_text / Sale.item_text
LINE_MAX = 1000  # what classify() reads of one chat line
BUYER_MAX = 80  # buyer_username

# Order = priority when several words appear ("Red bundle" counts as red). The
# sheet tracks Red, Bundles and Ultra/Mega as separate tallies; confirm the
# precedence with the team once real shows are captured.
AUCTION_TYPES = [
    ("red", re.compile(r"\bred\b", re.I)),
    ("mega", re.compile(r"\bmega\b", re.I)),
    ("ultra", re.compile(r"\bultra\b", re.I)),
    ("bundle", re.compile(r"\bbundle\b", re.I)),
    # On the shop's packing sheet ("Quick Hit 188"); how it reads in chat is unconfirmed.
    ("quick_hit", re.compile(r"\bquick\s*-?\s*hits?\b", re.I)),
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


# ---- the "Auction Number" column of the order slips ("Quick Hit 2", "Ultra 19", "Listing") ----
LOT_LABELS = {
    "red": "Red",
    "mega": "Mega",
    "ultra": "Ultra",
    "bundle": "Bundle",
    "quick_hit": "Quick Hit",
}
_LOT_WORDS = r"(?:quick\s*-?\s*hits?|ultra|mega|red|bundles?|flash|auction|lot|listing)"
_LOT_NUM = re.compile(rf"\b{_LOT_WORDS}\s*#?\s*(?P<n>\d{{1,6}})\b|#\s?(?P<h>\d{{1,6}})\s*$", re.I)
# A title that names only the lot ("Quick Hit 188", "Red bundle #752"): no product in it.
_LOT_ONLY = re.compile(rf"^(?:{_LOT_WORDS}[\s#:–-]*)+\d{{0,6}}[\s!.]*$", re.I)
_FLASH = re.compile(r"^\s*flash\b", re.I)


def lot_label(item: str, auction_type_: str = "") -> str:
    """How the shop's sheet names the lot a sale came from: the auction type and Whatnot's
    lot number from the listing title when it has one, else "Listing" as on the sheet."""
    item = item or ""
    name = LOT_LABELS.get(auction_type_) or ("Flash" if _FLASH.match(item) else "Listing")
    m = _LOT_NUM.search(item)
    num = (m["n"] or m["h"]) if m else None
    return f"{name} {int(num)}" if num else name


# A pick-your-own listing ("CHOOSE ANY PRODUCT YOU WANT #73"): the winner names the product
# in chat, so the title is no product either.
_CHOICE = re.compile(r"^(?:choose|pick)\b.{0,60}#\s?\d{1,6}[\s!.]*$", re.I)


def is_lot_title(item: str) -> bool:
    """True when the text is only a lot's title, so nobody has said which product it is."""
    t = (item or "").strip()
    return bool(_LOT_ONLY.match(t) or _CHOICE.match(t))


def lot_number(item: str) -> int | None:
    """Whatnot's lot number in a listing title ("Quick Hit 2", "… YOU WANT #73"), if any."""
    m = _LOT_NUM.search(item or "")
    return int(m["n"] or m["h"]) if m else None


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
# The split case (see classify): only the unmistakable platform shapes are rejoined, so a
# viewer's "won last week too" or "got it, thanks" stays chatter instead of filling Review.
SPLIT_VERB = re.compile(r"^(?:won|purchased|bought)\b", re.I)
SPLIT_PATTERNS = [
    (EventKind.gift, "won_giveaway", dict(GIFT_PATTERNS)["won_giveaway"]),
    (EventKind.sale, "won_auction", dict(SALE_PATTERNS)["won_auction"]),
    (EventKind.sale, "won_item_for", dict(SALE_PATTERNS)["won_item_for"]),  # needs a price
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


# ---- a win read from Whatnot's video player (ChatMessage.source == "player") ----
# The extension writes this line itself (extension/src/capture/player.ts, winBody): the
# item is the listing title and the price is always "$<dollars>.<cents>". Player rows are
# read only this way and chat rows never are, so a viewer typing the same sentence in the
# chat is still just a chat line.
PLAYER_WIN = re.compile(
    r"^(?P<buyer>[A-Za-z0-9._-]{1,80}) won the auction for (?P<item>.+) for "
    r"\$(?P<dollars>[0-9]{1,7})\.(?P<cents>[0-9]{2})$",
    re.ASCII,
)
# A card declined after a win, also written by the extension (declineBody): the same winner,
# title and price as the win's own line, so the pipeline finds that sale exactly. Read from
# Whatnot's player or the show's Sold tab, never from the chat.
PLAYER_DECLINE = re.compile(
    r"^(?P<buyer>[A-Za-z0-9._-]{1,80}) payment declined for (?P<item>.+) for "
    r"\$(?P<dollars>[0-9]{1,7})\.(?P<cents>[0-9]{2})$",
    re.ASCII,
)
# A decline the extension saw but could not tie to a recorded win: a person decides.
PLAYER_DECLINE_UNTIED = re.compile(r"^payment declined \(not tied to a win\): (?P<text>.+)$", re.S)
# "... SAM #760": Whatnot numbers repeated listings. Recorded as a detail; the item keeps the
# full title, because the number can also be the product ("Charizard #4"). A product named
# "Red bundle" still matches "Red bundle #752" (match_sku looks for the name inside the text).
LOT = re.compile(r"\s*#\s?(?P<lot>[0-9]{1,6})$", re.ASCII)
# The payment-file import treats more than $100,000 per item as a misread; so does this.
PLAYER_MAX_CENTS = 10_000_000


def _player_win(body: str) -> Classification:
    m = PLAYER_WIN.match(body)
    if not m and (d := PLAYER_DECLINE.match(body)):
        return Classification(
            kind=EventKind.payment_failed,
            rule="player_payment_declined",
            buyer=d["buyer"][:BUYER_MAX],
            item=re.sub(r"\s+", " ", d["item"]).strip()[:ITEM_MAX],
            price_cents=int(d["dollars"]) * 100 + int(d["cents"]),
            confidence=1.0,
        )
    if not m and (u := PLAYER_DECLINE_UNTIED.match(body)):
        # Not applied to any sale: the pipeline opens a review with the text it saw.
        return Classification(
            kind=EventKind.chatter,
            rule="player_decline_untied",
            details={"text": u["text"][:ITEM_MAX]},
            confidence=1.0,
        )
    if not m:
        # Not a shape the extension writes: never a sale, and never silently dropped
        # (the pipeline opens a review for this rule).
        return Classification(kind=EventKind.chatter, rule="player_unreadable", confidence=1.0)
    item = re.sub(r"\s+", " ", m["item"]).strip()[:ITEM_MAX]
    lot = LOT.search(item)
    cents = int(m["dollars"]) * 100 + int(m["cents"])
    over = cents > PLAYER_MAX_CENTS
    return Classification(
        kind=EventKind.sale,
        # A price over the cap waits in Review (confidence < 0.8) instead of being trusted.
        rule="player_win_price_check" if over else "player_win",
        buyer=m["buyer"][:BUYER_MAX],
        item=item,
        quantity=1,
        price_cents=cents,
        auction_type=auction_type(item),
        confidence=0.6 if over else 1.0,
        details={"lot": lot["lot"]} if lot else {},
    )


def _build(
    kind: EventKind, rule: str, m: re.Match, *, confidence: float = 1.0, text: str = ""
) -> Classification:
    g = m.groupdict()
    qty, item = _qty_and_item(g.get("item") or "")
    # Chat is free text: keep what we store within the columns (parsed_events / sales),
    # or one long line would fail the insert and stop parsing the whole show.
    item = item[:ITEM_MAX]
    return Classification(
        kind=kind,
        rule=rule,
        buyer=(g.get("buyer") or "").lstrip("@")[:BUYER_MAX],
        item=item,
        quantity=qty,
        price_cents=_price(g.get("price")),
        auction_type=auction_type(item or text),
        confidence=confidence,
        details={k: v for k, v in g.items() if v and k not in ("buyer", "item", "price")},
    )


def classify(
    username: str, body: str, raw_text: str, *, host: str | None = None, source: str = "chat"
) -> Classification:
    """Classify one chat line. `host` is the seller's username when known (host lines carry
    more weight); `source` is "player" for a win read from the video player."""
    if source == "player":
        return _player_win((body or "").strip())
    # One space between words and a bounded length before any pattern runs: long runs of
    # spaces made the lazy item / price patterns backtrack for minutes (one viewer line could
    # stall the worker). Real chat lines are far shorter.
    text = " ".join((body or raw_text or "").split())[:LINE_MAX]
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
    # The extension splits "<name> won the auction!" into username=<name>, body="won the
    # auction!" when the platform renders the winner's handle in its own element. Rejoin
    # them, but only with viewer authority: from here the line is indistinguishable from a
    # viewer typing "won the auction!" themselves, so the pipeline sends it to Review
    # (confidence < 0.8) instead of creating a sale. A real capture will tell us whether
    # platform rows carry a marker the extension can forward (see audit 2026-10-24).
    if username and not is_host and SPLIT_VERB.match(text):
        joined = " ".join(f"{username} {text}".split())[:LINE_MAX]
        for kind, rule, rx in SPLIT_PATTERNS:
            m = rx.match(joined)
            if m:
                return _build(kind, f"{rule}_split", m, confidence=0.6, text=joined)
    for rule, rx in CLAIM_PATTERNS:
        m = rx.match(text)
        if m:
            c = _build(EventKind.claim, rule, m, confidence=0.9, text=text)
            c.buyer = (username or "")[:BUYER_MAX]
            c.quantity = int(m.groupdict().get("qty") or 1)
            return c
    return Classification(
        kind=EventKind.chatter, rule="none", buyer=(username or "")[:BUYER_MAX], confidence=1.0
    )
