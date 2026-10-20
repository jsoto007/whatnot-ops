"""What the AI may do on its own. Everything else becomes a review item.

A proposal is auto-applied only when (a) its confidence reaches the threshold
for that action, (b) the action is reversible through the ledger, and (c) the
supervisor can tie it to a line from the host or the platform (see
supervisor._corroborated / _authoritative). Chat is typed by anyone watching,
so the model's confidence alone is never enough. Marking a sale paid is never
automatic: payment truth comes from reconciliation.
"""

AUTO_APPLY: dict[str, float] = {
    "cancel_sale": 0.9,  # reserved → cancelled (release); reversible by re-reserving
    "payment_failed": 0.9,  # reserved → payment_failed (release)
    "assign_sku": 0.95,  # unmatched sale → SKU + reserve
    "classify_sale": 0.95,  # a low-confidence line confirmed as a sale → create the sale
}
NEVER_AUTO = frozenset({"mark_paid", "merge_duplicate", "adjust_stock"})


def may_auto_apply(action: str, confidence: float) -> bool:
    if action in NEVER_AUTO:
        return False
    threshold = AUTO_APPLY.get(action)
    return threshold is not None and confidence >= threshold
