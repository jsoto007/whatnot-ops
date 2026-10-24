import json
from collections import Counter
from pathlib import Path

from app.models import EventKind
from app.services.rules import classify

CORPUS = Path(__file__).parent / "fixtures" / "chat_corpus.jsonl"
HOST = "host_shop"


def load():
    return [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]


def test_every_corpus_line_is_classified_as_labelled():
    rows = load()
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    failures = []
    for r in rows:
        c = classify(r["u"], r["b"], f"{r['u']} {r['b']}".strip(), host=HOST)
        expected = EventKind(r["kind"])
        if c.kind is expected:
            tp[expected] += 1
        else:
            fp[c.kind] += 1
            fn[expected] += 1
            failures.append(f"{r['b']!r}: expected {expected.value}, got {c.kind.value} ({c.rule})")
        if c.kind is expected and expected is not EventKind.chatter:
            for key, attr in (
                ("buyer", "buyer"),
                ("item", "item"),
                ("qty", "quantity"),
                ("price", "price_cents"),
                ("type", "auction_type"),
            ):
                if key in r and getattr(c, attr) != r[key]:
                    failures.append(
                        f"{r['b']!r}: {attr} = {getattr(c, attr)!r}, expected {r[key]!r}"
                    )
    print("\nrule precision / recall on the (synthetic) corpus:")
    for kind in EventKind:
        n_tp, n_fp, n_fn = tp[kind], fp[kind], fn[kind]
        if n_tp + n_fp + n_fn == 0:
            continue
        p = n_tp / (n_tp + n_fp) if n_tp + n_fp else 0.0
        rc = n_tp / (n_tp + n_fn) if n_tp + n_fn else 0.0
        print(f"  {kind.value:15s} precision {p:5.0%} recall {rc:5.0%} (n={n_tp + n_fn})")
    assert not failures, "\n".join(failures)
    sale_p = tp[EventKind.sale] / (tp[EventKind.sale] + fp[EventKind.sale])
    assert sale_p >= 0.99


def test_sale_claims_by_random_buyers_are_not_trusted():
    c = classify("randomguy", "sold to randomguy", "randomguy sold to randomguy", host=HOST)
    assert c.kind is EventKind.sale and c.confidence < 0.8  # the pipeline sends this to review


def test_quantity_price_and_type_extraction():
    c = classify("", "kat.c bought Ultra pack x3 for $12.50", "", host=HOST)
    assert (c.buyer, c.item, c.quantity, c.price_cents, c.auction_type) == (
        "kat.c",
        "Ultra pack",
        3,
        1250,
        "ultra",
    )


def test_split_winner_lines_are_recognised_but_never_trusted():
    """The extension may hand us username="lani808", body="won the auction!" for a platform
    line. The rules rejoin it (so the win is not lost) at viewer authority (so a viewer typing
    the same words cannot make themselves a sale)."""
    c = classify("lani808", "won the auction!", "lani808 won the auction!", host=HOST)
    assert (c.kind, c.rule, c.buyer) == (EventKind.sale, "won_auction_split", "lani808")
    assert c.confidence == 0.6
    c = classify("kat.c", "won the auction for Red bundle for $25", "", host=HOST)
    assert (c.kind, c.item, c.price_cents) == (EventKind.sale, "Red bundle", 2500)
    assert c.confidence == 0.6
    assert classify("kat.c", "won the giveaway!", "", host=HOST).rule == "won_giveaway_split"
    # ordinary chat that happens to start with the same verbs stays chatter
    for body in ("won last week too", "got it, thanks", "bought one yesterday", "won Red bundle"):
        assert classify("cardguy", body, "", host=HOST).kind is EventKind.chatter, body
    # the host's own name never becomes the buyer
    assert classify(HOST, "won the auction!", "", host=HOST).kind is EventKind.chatter
