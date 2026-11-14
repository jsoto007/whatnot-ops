"""The activity log in the shop's words.

`audit_log` keeps the raw action code and details for the developer (psql,
support). The browser only ever gets the sentence built here, so no action
code ("invite.created"), JSON key, enum value or server setting name reaches
the page. An action with no sentence reads "Made a change", never its code;
details of an unexpected shape fall back to the action's plain label.

Names typed by people (products, aliases, notes, laptops) appear as written;
the page shows them as plain text.
"""

from collections.abc import Callable
from decimal import Decimal

from .permissions import role_label

ORDER_STATUS = {
    "open": "needs review",
    "paid": "ready",
    "picking": "picking",
    "packed": "packed",
    "shipped": "shipped",
    "cancelled": "cancelled",
}
IMPORT_ACTION = {"create": "new", "set": "updated", "unchanged": "unchanged", "skip": "skipped"}
RECONCILE_ACTION = {
    "mark_paid": "marked paid",
    "cancel": "cancelled",
    "decline": "payment declined",
    "unchanged": "unchanged",
    "unmatched": "no matching sale",
    "skip": "skipped",
    "uncovered": "not in the file",
    "record": "sale to record",
    "recorded": "sale recorded",
}


# What the pick worker found for the wins still waiting (services/picks.py).
PICK_WORDS = {
    "assigned": "assigned from the winner's shelf spot",
    "suggested": "picked, left for a person to confirm",
    "none": "did not pick",
    "unknown_code": "typed a spot with no product",
    "several": "typed more spots than the win takes",
    "unread": "not read because a laptop's clock was out",
    "no_layout": "not read because the show has no shelf layout",
}


WHATNOT_ORDER_ACTION = {
    "mark_paid": "marked paid",
    "cancel": "cancelled",
    "decline": "payment declined",
    "recorded": "sales recorded",
    "reopen": "waiting for a product again after a declined card was paid",
    "gave_way": "cancelled because the auction went to another win",
    "unmatched": "for a person to check",
    "uncovered": "with no order on Whatnot",
}


SALE_STATUS = {
    "detected": "needs a decision",
    "reserved": "reserved",
    "paid": "paid",
    "cancelled": "cancelled",
    "payment_failed": "payment declined",
    "fulfilled": "shipped",
}
MOVEMENT_KIND = {
    "reserve": "held for a sale",
    "commit": "paid",
    "release": "sale cancelled",
    "restock": "restocked",
    "adjust": "adjusted",
}
MOVEMENT_SOURCE = {
    "demo": "demo data",
    "receive": "delivery",
    "count": "stock count",
    "import": "sheet import",
    "manual": "by hand",
    "giveaway": "giveaway",
}


def word(table: dict[str, str], code) -> str:
    """A code in the shop's words; an unknown one reads "other", never as itself."""
    return table.get(getattr(code, "value", code) or "", "other")


def lot(auction_type: str | None) -> str:
    from .rules import LOT_LABELS

    return LOT_LABELS.get(auction_type or "", "")


def movement_source(source_type: str, source_id: str) -> str:
    if source_type in ("sale", "sale_repaid"):
        again = " (paid after a decline)" if source_type == "sale_repaid" else ""
        # "12:34" is sale 12's product after a change: the key's second half is internal.
        sale_id = source_id.split(":", 1)[0]
        return (f"sale #{sale_id}" if sale_id else "sale") + again
    return MOVEMENT_SOURCE.get(source_type, "other")


def user_names(org_id: int, ids) -> dict[int, str]:
    """People's names (not emails) for stock history, which every role can read."""
    from sqlalchemy import select

    from ..db import db
    from ..models import User

    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = db.session.execute(
        select(User.id, User.name).where(User.id.in_(ids), User.organization_id == org_id)
    ).all()
    return dict(rows)


def movement_actor(actor_type: str | None, actor_id, label: str | None, names: dict) -> str:
    """Who moved stock: a person by name (every role sees the ledger, so never an email)."""
    if actor_type == "user":
        return names.get(actor_id) or "Team member"
    return actor(actor_type, label)


# Rows written with no signed-in person, where "Automatic" would mislead: someone typed a
# password or asked for a reset on the sign-in page, or a laptop acted with its own key.
WHO = {
    "login.failed": "Sign-in page",
    "password.reset_requested": "Sign-in page",
    "device.paired": "Laptop",
    "device.disconnected": "Laptop",
}


def who(action: str, actor_type: str | None, label: str | None) -> str:
    """The activity log's "Who" column."""
    if actor_type not in ("user", "device", "ai", "whatnot", "pick") and action in WHO:
        return WHO[action]
    return actor(actor_type, label)


def actor(actor_type: str | None, label: str | None) -> str:
    """Who did it: a person's email or a laptop's name as they are; anything the
    server did on its own is "AI" or "Automatic", never an internal label."""
    if actor_type in ("user", "device") and label:
        return label
    if actor_type == "ai":
        return "AI"
    if actor_type == "whatnot":
        return "Whatnot"  # what Whatnot's own records said (an order paid, a parcel sent)
    if actor_type == "pick":
        return "The winner's pick"  # the shelf spot the winner typed in chat
    return "Automatic"


def _q(text) -> str:
    return f"“{text}”" if text else ""


def _money(cents) -> str:
    return "no price" if cents is None else f"${cents / 100:,.2f}"


def _weight(oz) -> str:
    """4.5 → "4.5 oz", 20 → "1 lb 4 oz", 32 → "2 lb" (how a US postal scale reads)."""
    if oz is None:
        return "not weighed"
    lb, rest = divmod(Decimal(str(oz)), 16)
    rest_text = f"{format(rest.normalize(), 'f')} oz" if rest else ""
    return " ".join(p for p in (f"{int(lb)} lb" if lb else "", rest_text) if p)


def _counts(summary: dict, words: dict[str, str]) -> str:
    parts = [f"{summary[k]} {w}" for k, w in words.items() if summary.get(k)]
    return ", ".join(parts)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _with_ip(text: str, d: dict) -> str:
    return f"{text} from {d['ip']}" if d.get("ip") else text


def _user_updated(d: dict, target: str) -> str:
    who = target or "a team member"
    parts = []
    if "role" in d:
        old, new = d["role"]
        parts.append(f"changed {who}'s role from {role_label(old)} to {role_label(new)}")
    if "disabled" in d:
        parts.append(f"{'turned off' if d['disabled'][1] else 'turned back on'} {who}'s access")
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


def _product_updated(d: dict, _target: str) -> str:
    before, after = d["before"], d["after"]
    parts = []
    if before["name"] != after["name"]:
        parts.append(f"renamed {_q(before['name'])} to {_q(after['name'])}")
    if before["active"] != after["active"]:
        parts.append(f"{'turned on' if after['active'] else 'turned off'} {_q(after['name'])}")
    return ("Product: " + "; ".join(parts)) if parts else f"Edited product {_q(after['name'])}"


def _sku_updated(d: dict, _target: str) -> str:
    before, after = d["before"], d["after"]
    parts = []
    if before["code"] != after["code"]:
        parts.append(f"code {before['code']} → {after['code']}")
    if before["priceCents"] != after["priceCents"]:
        parts.append(f"price {_money(before['priceCents'])} → {_money(after['priceCents'])}")
    # Entries written before weights existed have no "weightOz": both read as not weighed.
    if before.get("weightOz") != after.get("weightOz"):
        parts.append(f"weight {_weight(before.get('weightOz'))} → {_weight(after.get('weightOz'))}")
    if before["lowStockAt"] != after["lowStockAt"]:
        parts.append(f"low-stock level {before['lowStockAt']} → {after['lowStockAt']}")
    return f"Edited item {after['code']}" + (f": {', '.join(parts)}" if parts else "")


def _received(d: dict, _target: str) -> str:
    lines = ", ".join(f"{code} ×{qty}" for code, qty in d.get("lines", [])[:8])
    text = f"Received {_plural(d['units'], 'unit')}" + (f" ({lines})" if lines else "")
    return text + (f" · {d['note']}" if d.get("note") else "")


def _counted(d: dict, _target: str) -> str:
    changed = d.get("changed", [])
    lines = ", ".join(f"{code} {old} → {new}" for code, old, new in changed[:8])
    text = f"Counted stock: {_plural(len(changed), 'item')} changed" + (
        f" ({lines})" if lines else ""
    )
    return text + (f" · {d['note']}" if d.get("note") else "")


def _adjusted(d: dict, target: str) -> str:
    delta = d.get("delta", 0)
    if not delta:
        return "Checked stock" + (f" of {target}" if target else "") + ": no change"
    text = f"{'Added' if delta >= 0 else 'Removed'} {abs(delta)}"
    text += f" {target}" if target else ""
    return text + (f" · {d['reason']}" if d.get("reason") else "")


def _imported(d: dict, _target: str) -> str:
    counts = _counts(d, IMPORT_ACTION)
    return "Imported the stock sheet" + (f": {counts}" if counts else "")


def _reconciled(d: dict, _target: str) -> str:
    counts = _counts(d, RECONCILE_ACTION)
    return "Matched the payment file" + (f": {counts}" if counts else "")


def _parsed(d: dict, _target: str) -> str:
    return f"Re-read a show's chat: {_plural(d['sales'], 'sale')}, {d['reviews']} to review"


BUYER_FLAG = {"vip": "a VIP", "watch": "on the watch list"}


def _buyer_flagged(d: dict, _target: str) -> str:
    buyer = _q(d["buyer"])
    if d.get("after") == "watch":
        return f"Put buyer {buyer} on the watch list"
    if d.get("after") == "vip":
        return f"Marked buyer {buyer} as a VIP"
    was = BUYER_FLAG.get(d.get("before"))
    return f"Buyer {buyer} is no longer {was}" if was else f"Cleared the flag on buyer {buyer}"


def _order_updated(d: dict, _target: str) -> str:
    before, after = d["before"], d["after"]
    words = {"notes": "notes", "tracking": "tracking number", "packedBy": "packed by"}
    changed = [w for k, w in words.items() if before.get(k) != after.get(k)]
    return "Updated an order's " + " and ".join(changed) if changed else "Edited an order"


def _org_updated(d: dict, _target: str) -> str:
    parts = []
    if "name" in d:
        parts.append(f"renamed the shop to {_q(d['name'][1])}")
    if "hostUsername" in d:
        new = d["hostUsername"][1]
        parts.append(
            f"set the host's Whatnot username to @{new}" if new else "cleared the host's username"
        )
    if "saleMode" in d:
        parts.append(f"set how the shop usually sells to {_q(sale_mode_words(d['saleMode'][1]))}")
    if "trustedMods" in d:
        n = d["trustedMods"][1]
        parts.append(
            f"set the trusted moderators ({n} name{'' if n == 1 else 's'})"
            if n
            else "cleared the trusted moderators"
        )
    if "autoPick" in d:
        parts.append(
            "switched on assigning winners' picks after each show"
            if d["autoPick"][1]
            else "switched off assigning winners' picks (they are suggested again)"
        )
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


SALE_MODES = {"winners_pick": "winners pick in chat", "listing": "one product per listing"}


def sale_mode_words(mode) -> str:
    return SALE_MODES.get(mode, "another way")


def _show_mode(d: dict, _target: str) -> str:
    words = sale_mode_words(d.get("mode"))
    if not d.get("own"):
        return f"Set a show back to the shop's usual way of selling ({words})"
    return f"Set how a show sells to {_q(words)}"


def _report(d: dict, _target: str) -> str:
    if d.get("from") and d.get("to"):
        return f"Downloaded the report for {d['from']} to {d['to']}"
    return ""


def _review_resolved(d: dict, _target: str) -> str:
    return "Dismissed a review item" if d.get("action") == "dismiss" else ""


Describe = Callable[[dict, str], str]

# action code -> (plain label, optional sentence from the details and the target's name)
ACTIONS: dict[str, tuple[str, Describe | None]] = {
    "org.created": ("Created the shop", None),
    "licence.accepted": (
        "Accepted the Sales Ops License and Data Terms",
        lambda d, t: _with_ip(
            f"Accepted the Sales Ops License and Data Terms, version {d['version']}", d
        ),
    ),
    "login": ("Signed in", lambda d, t: _with_ip("Signed in", d)),
    "login.failed": (
        "Wrong password",
        lambda d, t: _with_ip(f"Wrong password for {t}" if t else "Wrong password", d),
    ),
    "user.logout_everywhere": ("Signed out on every device", None),
    "password.reset_requested": (
        "Asked for a password reset",
        lambda d, t: f"Password reset asked for {t}" if t else "",
    ),
    "password.reset": ("Changed their password", None),
    "invite.created": (
        "Sent an invite",
        lambda d, t: f"Invited {d['email']} as {role_label(d['role'])}",
    ),
    "invite.accepted": (
        "Joined the shop",
        lambda d, t: f"Joined the shop as {role_label(d['role'])}",
    ),
    "invite.revoked": ("Withdrew an invite", None),
    "user.updated": ("Changed a team member", _user_updated),
    "user.admin_restored": (
        "Restored the shop owner",
        lambda d, t: f"Restored {t} as an owner" if t else "",
    ),
    "support.created": ("Added the IT support account", None),
    "support.restored": ("Restored the IT support account", None),
    "product.created": ("Added a product", lambda d, t: f"Added product {_q(d['name'])}"),
    "product.updated": ("Edited a product", _product_updated),
    "sku.updated": ("Edited an item", _sku_updated),
    "alias.added": (
        "Added a chat name",
        lambda d, t: f"Added chat name {_q(d['alias'])}" + (f" to {t}" if t else ""),
    ),
    "alias.removed": ("Removed a chat name", None),
    "inventory.received": ("Received stock", _received),
    "inventory.counted": ("Counted stock", _counted),
    "inventory.adjusted": ("Adjusted stock", _adjusted),
    "inventory.imported": ("Imported the stock sheet", _imported),
    "show.parsed": ("Re-read a show's chat", _parsed),
    "show.ai_audit_requested": ("Asked the AI to check a show", None),
    "show.mode": ("Changed how a show sells", _show_mode),
    "sale.product_changed": (
        "Changed a sale's product",
        lambda d, t: (
            f"Changed {d.get('buyer', 'a buyer')}'s product from {_q(d['from'])} to {_q(d['to'])}"
            + (" after picking had begun" if d.get("picked") else "")
        ),
    ),
    "sale.products_allowed": (
        "Set how many products a sale may hold",
        lambda d, t: (
            f"Set {d.get('buyer', 'a buyer')}'s sale to hold up to {d.get('after') or 1} "
            f"product{'' if (d.get('after') or 1) == 1 else 's'}"
        ),
    ),
    "giveaway.product_set": (
        "Named a giveaway's product",
        lambda d, t: (
            f"Named {d.get('buyer') or 'a buyer'}'s giveaway: "
            f"{d.get('quantity', 1)} × {_q(d['product'])}"
            + (f" (was {_q(d['from'])})" if d.get("from") else "")
        ),
    ),
    "giveaway.product_cleared": (
        "Took the product off a giveaway",
        lambda d, t: (
            f"Took {_q(d['product'])} off {d.get('buyer') or 'a buyer'}'s giveaway; "
            "it is back in stock"
        ),
    ),
    "sale.product_added": (
        "Added a product to a sale",
        lambda d, t: (
            f"Added {d.get('quantity', 1)} × {_q(d['product'])} "
            f"to {d.get('buyer', 'a buyer')}'s sale"
            + (" after picking had begun" if d.get("picked") else "")
        ),
    ),
    "sale.product_removed": (
        "Took a product off a sale",
        lambda d, t: (
            f"Took {_q(d['product'])} off {d.get('buyer', 'a buyer')}'s sale"
            + (" after picking had begun" if d.get("picked") else "")
        ),
    ),
    "show.shelves": (
        "Laid out the shelves for a show",
        lambda d, t: (
            f"Laid out {d.get('spots', 0)} shelf spots for a show"
            + (", copied from the last show" if d.get("copied") else "")
        ),
    ),
    "device.created": ("Added a laptop", lambda d, t: f"Added laptop {_q(d['name'])}"),
    "device.pairing_started": (
        "Started pairing a laptop",
        lambda d, t: f"Started pairing laptop {_q(d['name'])}",
    ),
    "device.paired": ("Paired a laptop", lambda d, t: f"Paired laptop {_q(d['name'])}"),
    "device.revoked": (
        "Disconnected a laptop",
        lambda d, t: f"Disconnected laptop {_q(t)}" if t else "",
    ),
    "device.disconnected": (
        "A laptop disconnected itself",
        lambda d, t: f"Laptop {_q(d['name'])} disconnected itself",
    ),
    "review.resolved": ("Resolved a review item", _review_resolved),
    "order.status": (
        "Moved an order",
        lambda d, t: f"Moved an order to {ORDER_STATUS[d['status']]}",
    ),
    "order.updated": ("Edited an order", _order_updated),
    "sale.gifts": (
        "Changed a sale's gifts",
        lambda d, t: f"Changed a sale's gifts from {d['before']} to {d['after']}",
    ),
    "sale.autopaid": ("Marked a sale paid at the win", None),
    "buyer.flagged": ("Changed a buyer's flag", _buyer_flagged),
    "buyer.noted": (
        "Changed a buyer's note",
        lambda d, t: (
            f"{'Wrote' if not d.get('before') else 'Changed'} the note on buyer {_q(d['buyer'])}"
            if d.get("after")
            else f"Removed the note on buyer {_q(d['buyer'])}"
        ),
    ),
    "buyer.renamed": (
        "A buyer changed their name",
        lambda d, t: f"Buyer {_q(d['before'])} now goes by {_q(d['after'])} on Whatnot",
    ),
    "buyer.merged": (
        "Joined two buyer profiles",
        lambda d, t: (
            f"Buyer {_q(d['other'])} and buyer {_q(d['buyer'])} are one Whatnot account:"
            " their profiles were joined"
        ),
    ),
    "buyer.name_moved": (
        "A buyer's name went to another account",
        lambda d, t: f"The name {_q(d['name'])} is now used by a different Whatnot account",
    ),
    "payments.reconciled": ("Matched the payment file", _reconciled),
    "organization.updated": ("Changed the shop settings", _org_updated),
    "report.downloaded": ("Downloaded a show report", _report),
    "whatnot.connected": (
        "Connected the shop to Whatnot",
        lambda d, t: (
            f"Connected the shop to Whatnot as @{d['username']}"
            + (" and set it as the host's username" if d.get("hostUsernameSet") else "")
        ),
    ),
    "whatnot.disconnected": (
        "Disconnected the shop from Whatnot",
        lambda d, t: f"Disconnected the shop from Whatnot (@{d['username']})",
    ),
    "picks.read": (
        "Read the winners' picks after a show",
        lambda d, t: (
            "Read the winners' picks after a show"
            + (f": {c}" if (c := _counts(d, PICK_WORDS)) else "")
        ),
    ),
    "whatnot.orders_applied": (
        "Applied Whatnot's orders to a show",
        lambda d, t: (
            "Applied Whatnot's orders to a show"
            + (f": {c}" if (c := _counts(d, WHATNOT_ORDER_ACTION)) else ": nothing to change")
        ),
    ),
    "whatnot.show_linked": (
        "Matched a show to Whatnot",
        lambda d, t: (
            f"Matched a show to the Whatnot show {_q(d['title'])}"
            if d.get("title")
            else "Unmatched a show from Whatnot"
        ),
    ),
    "whatnot.mode": (
        "Changed how Whatnot's orders are used",
        lambda d, t: (
            "Whatnot's orders are now applied as they arrive"
            if d["mode"] == "auto"
            else "Whatnot's orders are now only compared until a person applies them"
        ),
    ),
    "whatnot.listing_linked": (
        "Linked a Whatnot listing",
        lambda d, t: (
            f"Linked the Whatnot listing {_q(d['listing'])} to {_q(d['product'])}"
            if d.get("product")
            else f"Said the Whatnot listing {_q(d['listing'])} is none of the shop's products"
        ),
    ),
    "whatnot.orders_shipped": (
        "Marked orders shipped",
        lambda d, t: (
            f"Marked {_plural(d['orders'], 'order')} shipped: the carrier has them"
            if d.get("orders")
            else "Marked orders shipped: the carrier has them"
        ),
    ),
    "labels.requested": (
        "Asked Whatnot for shipping labels",
        lambda d, t: f"Asked Whatnot to make {_plural(d['packages'], 'shipping label')}",
    ),
    "label.printed": ("Opened a shipping label to print", None),
    "labels.printed": (
        "Opened a show's shipping labels to print",
        lambda d, t: (
            f"Opened {_plural(d.get('printed', 0), 'shipping label')} of a show to print"
            + (f"; Whatnot did not send {_plural(d['failed'], 'label')}" if d.get("failed") else "")
        ),
    ),
    "ai.sale_confirmed": ("Confirmed a sale", None),
    "ai.applied": ("Applied a suggestion", None),
}
UNKNOWN = "Made a change"


def describe(action: str, details: dict | None, target: str = "") -> str:
    label, fn = ACTIONS.get(action, (UNKNOWN, None))
    if fn is None:
        return label
    try:
        return fn(details or {}, target) or label
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return label  # an older or unexpected details shape: the plain label is still true
