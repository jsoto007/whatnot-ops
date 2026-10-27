"""What the AI may do on its own. Everything else becomes a review item.

A proposal is auto-applied only when (a) its confidence reaches the threshold
for that action, (b) the action is reversible through the ledger, and (c) the
supervisor can tie it to a line from the host or the platform (see
supervisor._corroborated / _authoritative). Chat is typed by anyone watching,
so the model's confidence alone is never enough. The AI never marks a sale paid:
Whatnot charges every win (pipeline.autopay), and only a person or the payment file
restores a declined payment.
"""

AUTO_APPLY: dict[str, float] = {
    "cancel_sale": 0.9,  # reserved / paid (not picked yet) → cancelled (release / restock)
    "payment_failed": 0.9,  # same, → payment_failed; a person can mark it paid again
    "assign_sku": 0.95,  # unmatched sale → SKU + reserve
    "classify_sale": 0.95,  # a low-confidence line confirmed as a sale → create the sale
}
NEVER_AUTO = frozenset({"mark_paid", "merge_duplicate", "adjust_stock"})


def may_auto_apply(action: str, confidence: float) -> bool:
    if action in NEVER_AUTO:
        return False
    threshold = AUTO_APPLY.get(action)
    return threshold is not None and confidence >= threshold
