"""Special offers: how many products a win may hold (stage 5 of the API-first plan).

A win holds one product unless an offer says more. An offer is said in chat by the host or
by a moderator the shop trusts ("#45 is two for one", "#45 bogo": `rules.offer`), before or
after the lot is won, for that show only. A person can also set the count on one sale.

Chat is typed by anyone watching: a viewer's line never sets an offer. Only the host's name
and the names on the shop's list count, compared as plain ASCII (a look-alike is nobody).
"""

import re

from sqlalchemy import func, select

from ..db import db
from ..models import ChatMessage, Organization, Sale
from . import rules, sale_chat

MAX_MODS = 20
_NAME = re.compile(r"[a-z0-9_.-]{1,80}")


class OfferError(ValueError):
    status = 400


def clean_names(given) -> list[str]:
    """The trusted names as stored: lower case, no "@", plain ASCII, each once."""
    if not isinstance(given, list) or not all(isinstance(n, str) for n in given):
        raise OfferError("Send the names as a list.")
    out: list[str] = []
    for raw in given:
        name = raw.strip().lstrip("@").lower()
        if not name:
            continue
        if not _NAME.fullmatch(name):
            raise OfferError(f"{raw[:40]!r} is not a Whatnot name.")
        if name not in out:
            out.append(name)
    if len(out) > MAX_MODS:
        raise OfferError(f"At most {MAX_MODS} names.")
    return out


def trusted(org: Organization) -> set[str]:
    """Whose lines may set an offer: the host, and the shop's trusted moderators."""
    names = {n for n in (org.trusted_mods or []) if isinstance(n, str)}
    if org.host_username:
        names.add(org.host_username.strip().lstrip("@").lower())
    return names


def of_show(show_ids: list[int], org: Organization, until=None) -> dict[int, int]:
    """lot number → how many products its win may hold, from what the host and the trusted
    moderators typed in this show (every recording of it). The latest word on a lot stands.
    `until`: lines typed after that moment are not read."""
    names = trusted(org)
    if not names or not show_ids:
        return {}
    q = (
        select(ChatMessage)
        .where(
            ChatMessage.show_id.in_(show_ids),
            ChatMessage.source == "chat",
            func.lower(ChatMessage.username).in_(names),
        )
        .order_by(ChatMessage.captured_at, ChatMessage.show_id, ChatMessage.seq)
    )
    if until is not None:
        q = q.where(ChatMessage.captured_at <= until)
    out: dict[int, int] = {}
    for m in db.session.scalars(q):
        name = m.username or ""
        if not name.isascii() or name.lower() not in names:
            continue
        said = rules.offer(m.body)
        if said is not None:
            out[said[0]] = min(said[1], rules.OFFER_MAX)
    return out


def allowed(sale: Sale, offers: dict[int, int]) -> int:
    """How many products this win may hold: what a person set on it (one included: it
    overrules an offer typed in chat), else what an offer says for its lot, else one."""
    if sale.products_allowed is not None:
        return sale.products_allowed
    return offers.get(sale_chat.lot_of(sale), 1)


def set_allowed(sale: Sale, count) -> int | None:
    """A person sets how many products one sale may hold (1 to `rules.OFFER_MAX`), whatever
    was typed in chat; None goes back to what the show's offers say. Returns what it was."""
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= rules.OFFER_MAX
    ):
        raise OfferError(f"How many must be a whole number from 1 to {rules.OFFER_MAX}.")
    before = sale.products_allowed
    sale.products_allowed = count
    return before
