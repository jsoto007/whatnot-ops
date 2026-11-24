"""The Orders page, one show at a time: every buyer of a show sits in one of three tabs, all
their wins of the show together.

- "review": something about the buyer waits on a person: a win with no product yet (the
  winner's pick), a card declined while Whatnot may still take the payment, a lot two buyers
  won, a giveaway with no product (or one Whatnot cancelled that still holds a product), or
  an order Whatnot cancelled (Whatnot is the source of truth; the page can hide those).
- "pack": nothing waits, or a manager marked the box ready to pack anyway (`PackOverride`).
  The box is to pick, being picked or packed. A buyer who won only a giveaway is handled
  like any order once its product is named, marked as a giveaway on screen.
- "shipped": the box left: the order shipped, or the giveaway-only buyer's parcel is with
  the carrier. That is done whatever is still open on it: a product can be named later, or
  the count fixed in Inventory by hand.

This is a view: it reads orders, Review questions and Whatnot's giveaways and changes
nothing but the override. A giveaway is never a sale here either (no price, no money).
Whatnot's data comes first: the giveaways are Whatnot's own orders (`giveaways.listing`)
and where a parcel is is Whatnot's word (`shipping.parcels_of`). Every count runs in a fixed
number of queries, however many shows and buyers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert

from ..db import db
from ..models import (
    Order,
    OrderStatus,
    PackOverride,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotShow,
)
from . import audit, buyers, giveaways
from .showdate import SHOP_TZ
from .whatnot import shipping

REVIEW, PACK, SHIPPED = "review", "pack", "shipped"
TABS = (REVIEW, PACK, SHIPPED)
# Open Review questions on a buyer's wins that keep the whole box out of packing. The rest
# (a price to check, a note for the packer) show on the row and hold nothing back.
HOLDS = frozenset(
    {
        "unmatched_item",  # which product: the winner's pick, or a lot nobody named
        "payment_declined",  # Whatnot's failed order closed it: a retry may still pay
        "decline_seen",  # the stream showed the card declined; Whatnot has not said yet
        "lot_won_twice",  # two buyers on one lot: Whatnot's paid order decides
        "possible_duplicate",  # the same win read twice: one unit may be a copy
        "no_payment_record",  # a sale Whatnot's orders do not list
    }
)
# Whatnot decides these (`orders._DOUBT`: picking is refused while one is open), so no
# person's "Mark ready to pack" covers them: a box packed past one is restocked behind the
# packer's back when Whatnot's word comes (money-safety review, 2026-11-24).
DOUBT = frozenset({"decline_seen", "lot_won_twice"})
# Work open on these orders keeps their show on the board whatever its age.
OPEN_WORK = (OrderStatus.open, OrderStatus.paid, OrderStatus.picking, OrderStatus.packed)
# A show whose work is done stays listed this long (the giveaways list's own window).
RECENT = giveaways.RECENT
# Where Whatnot says a parcel is once it has left the shop (`shipping.parcels_of` states).
LEFT = frozenset({"moving", "delivered", "failed"})


class BoardError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message, self.status = message, status


@dataclass
class Buyer:
    """What decides one buyer's tab in one show."""

    username: str
    order_id: int | None = None
    order_status: OrderStatus | None = None
    # Open Review questions on the order's sales, by reason code.
    reasons: set[str] = field(default_factory=set)
    # Whatnot's giveaways for this buyer in this show.
    waiting: int = 0  # no product yet
    take_off: int = 0  # Whatnot cancelled it and a product is still on it
    named: int = 0  # its product is named
    # Only for a buyer with no order here (giveaways alone, or an order Whatnot cancelled): where
    # Whatnot says the parcel is, one `shipping.parcels_of` state ("nolabel", "moving"…), or None.
    parcel: str | None = None
    # Everything that holds the box, each by its own key: an open question ("r<id>"), the open
    # order ("o<id>") and each win in it still waiting ("s<id>"), the cancel ("c<id>"), a giveaway
    # with no product ("gw<id>") or one to take a product off ("gx<id>").
    keys: set[str] = field(default_factory=set)
    # A manager's "Mark ready to pack": the keys it covered. Any other key (opened, read or
    # moved here since, however old) puts the box back in Need review.
    covered: set[str] | None = None

    @property
    def cancelled(self) -> bool:
        return self.order_status is OrderStatus.cancelled

    @property
    def held(self) -> bool:
        """Something about the buyer waits on a person."""
        return bool(self.keys)

    @property
    def doubt(self) -> bool:
        """Whatnot has yet to decide a card or a lot of theirs: nobody packs past it."""
        return bool(self.reasons & DOUBT)

    @property
    def overridden(self) -> bool:
        """Marked ready to pack, and nothing new since."""
        return self.covered is not None and self.keys <= self.covered and not self.doubt

    @property
    def ships(self) -> bool:
        """Something of theirs goes in a box: a standing order, or a giveaway with its product."""
        return (self.order_id is not None and not self.cancelled) or self.named > 0

    @property
    def hideable(self) -> bool:
        """A cancelled order with nothing else of theirs to do: the page may leave it out."""
        return self.cancelled and not (self.named or self.waiting or self.take_off)


def tab_of(b: Buyer) -> str | None:
    """REVIEW, PACK or SHIPPED for a buyer of one show (all their wins of it together)."""
    # The box left: done, whatever is still open on it (Javier's call, 2026-11-24).
    if b.order_status is OrderStatus.shipped:
        return SHIPPED
    if (b.order_id is None or b.cancelled) and b.parcel in LEFT:
        return SHIPPED  # what ships of theirs is a giveaway, and its parcel left
    # A box with nothing in it (a cancelled order, no giveaway) is never one to pack, override
    # or not: it stays in Need review, where the page can hide it. Nor is one Whatnot has yet
    # to decide (`DOUBT`).
    if b.held and not (b.overridden and b.ships):
        return REVIEW
    return PACK


def may_override(b: Buyer) -> bool:
    """Whether "Mark ready to pack" is offered: the buyer waits in Need review and something
    of theirs would go in a box."""
    return tab_of(b) == REVIEW and b.ships and not b.doubt


def key(name: str | None) -> str:
    """One buyer however Whatnot or the chat spelled the name ("@Ana" is "ana")."""
    return (name or "").strip().lstrip("@").lower()


@dataclass
class _Alone:
    """A buyer with giveaways and no order here, shaped as `shipping.parcels_of` reads one."""

    id: str
    show_id: int
    buyer_username: str
    status: OrderStatus | None = None
    shipped_at: datetime | None = None


def _buyers(org_id: int, show_ids: set[int]) -> tuple[dict[int, dict[str, Buyer]], list[dict]]:
    """show id → buyer key → Buyer, and the shows' giveaway rows (as "Giveaways to assign"
    lists them). A fixed number of queries however many shows."""
    out: dict[int, dict[str, Buyer]] = defaultdict(dict)
    if not show_ids:
        return out, []

    def get(show_id: int, name: str) -> Buyer:
        k = key(name)
        b = out[show_id].get(k)
        if b is None:
            b = out[show_id][k] = Buyer(username=name.strip().lstrip("@"))
        return b

    for order_id, show_id, name, status in db.session.execute(
        select(Order.id, Order.show_id, Order.buyer_username, Order.status).where(
            Order.organization_id == org_id, Order.show_id.in_(show_ids)
        )
    ):
        b = get(show_id, name)
        b.order_id, b.order_status = order_id, status
        if status is OrderStatus.open:
            b.keys.add(f"o{order_id}")
        elif status is OrderStatus.cancelled:
            b.keys.add(f"c{order_id}")
    # Each win of an open order still waiting (no product, or not paid): a new one is new.
    for sale_id, show_id, name in db.session.execute(
        select(Sale.id, Order.show_id, Order.buyer_username)
        .join(Order, Order.id == Sale.order_id)
        .where(
            Order.organization_id == org_id,
            Order.show_id.in_(show_ids),
            Order.status == OrderStatus.open,
            Sale.status.in_((SaleStatus.detected, SaleStatus.reserved)),
        )
    ):
        get(show_id, name).keys.add(f"s{sale_id}")
    for show_id, name, reason, item_id in db.session.execute(
        select(Order.show_id, Order.buyer_username, ReviewItem.reason, ReviewItem.id)
        .join(Sale, Sale.order_id == Order.id)
        .join(ReviewItem, ReviewItem.sale_id == Sale.id)
        .where(
            Order.organization_id == org_id,
            Order.show_id.in_(show_ids),
            ReviewItem.status == ReviewStatus.open,
        )
    ):
        b = get(show_id, name)
        b.reasons.add(reason)
        if reason in HOLDS:
            b.keys.add(f"r{item_id}")
    # Whatnot's giveaways, under the show that prints them (a show's first recording).
    rows = [r for r in giveaways.listing(org_id)["giveaways"] if r["showId"] in show_ids]
    for r in rows:
        b = get(r["showId"], r["buyer"])
        if r["cancelled"]:
            b.take_off += 1  # listed only while a product is still on it
            b.keys.add(f"gx{r['id']}")
        elif r["product"] is None:
            b.waiting += 1
            b.keys.add(f"gw{r['id']}")
        else:
            b.named += 1
    for show_id, buyer_key, covered in db.session.execute(
        select(PackOverride.show_id, PackOverride.buyer_key, PackOverride.covered).where(
            PackOverride.organization_id == org_id, PackOverride.show_id.in_(show_ids)
        )
    ):
        if (b := out.get(show_id, {}).get(buyer_key)) is not None:
            b.covered = set(covered or ())
    # A buyer whose only box is giveaways (no order here, or one Whatnot cancelled) has no
    # order to say it shipped: Whatnot's parcel does.
    alone = [
        _Alone(id=f"{show_id}:{k}", show_id=show_id, buyer_username=b.username)
        for show_id, found in out.items()
        for k, b in found.items()
        if b.order_id is None or b.cancelled
    ]
    if alone:
        where = shipping.parcels_of(org_id, alone)
        for a in alone:
            if a.id in where:
                out[a.show_id][key(a.buyer_username)].parcel = where[a.id]["state"]
    return out, rows


def counts(found: dict[str, Buyer]) -> dict[str, int]:
    tally = dict.fromkeys(TABS, 0)
    for b in found.values():
        tally[tab_of(b)] += 1
    # Cancelled orders wait in Need review too; the page can leave them out of its count, but
    # never one whose giveaway still needs a person (`hideable`).
    tally["cancelled"] = sum(1 for b in found.values() if tab_of(b) == REVIEW and b.hideable)
    return tally


def summary(org_id: int) -> list[dict]:
    """The shows on the Orders page, newest first, with how many buyers are in each tab: every
    show with work left whatever its age, and every show of the last RECENT days. A show with
    nobody in any tab is left out."""
    since = (datetime.now(SHOP_TZ) - RECENT).date()
    open_work = select(Order.show_id).where(
        Order.organization_id == org_id, Order.status.in_(OPEN_WORK)
    )
    shows = db.session.execute(
        select(Show.id, Show.show_date, WhatnotShow.title)
        .outerjoin(WhatnotShow, WhatnotShow.id == Show.whatnot_show_id)
        .where(
            Show.organization_id == org_id,
            or_(Show.show_date >= since, Show.id.in_(open_work)),
        )
        .order_by(Show.show_date.desc(), Show.id.desc())
    ).all()
    found, _ = _buyers(org_id, {s.id for s in shows})
    out = []
    for show_id, show_date, title in shows:
        tally = counts(found.get(show_id, {}))
        if any(tally[t] for t in TABS):
            out.append(
                {"showId": show_id, "showDate": show_date.isoformat(), "title": title, **tally}
            )
    return out


def board(show: Show) -> dict:
    """One show's buyers, each with its tab, its order id (None for a giveaway-only buyer),
    its giveaways and its badge. The route adds the orders."""
    found, rows = _buyers(show.organization_id, {show.id})
    mine = found.get(show.id, {})
    given: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        given[key(r["buyer"])].append(r)
    badge = buyers.tiers(show.organization_id, [b.username for b in mine.values()])
    out = []
    for k, b in mine.items():
        figures = badge.get(k.lower(), {})
        out.append(
            {
                "username": b.username,
                "tab": tab_of(b),
                "orderId": b.order_id,
                "giveaways": given.get(k, []),
                # Won only giveaways: handled like an order, marked as a giveaway on screen.
                "giveawayOnly": b.order_id is None,
                "cancelled": b.cancelled,
                "hideable": tab_of(b) == REVIEW and b.hideable,
                "parcel": b.parcel,
                "readyMarked": b.overridden,
                "mayMarkReady": may_override(b),
                "tier": figures.get("tier"),
                "shows": figures.get("shows", 0),
            }
        )
    out.sort(key=lambda x: x["username"].lower())
    return {"buyers": out, "counts": counts(mine)}


def names_in(show: Show, tab: str) -> dict[str, bool]:
    """The buyers of a show in one tab, by `key`, each with whether a manager marked the box
    ready to pack: the order slips print just these."""
    found, _ = _buyers(show.organization_id, {show.id})
    return {k: b.overridden for k, b in found.get(show.id, {}).items() if tab_of(b) == tab}


def mark_ready(show: Show, username: str, *, user) -> bool:
    """A manager's "Mark ready to pack" for one buyer of one show. True when it was set now;
    a second press changes nothing. Refused for a buyer not waiting in Need review or with
    nothing that goes in a box. Audited; the caller commits."""
    k = key(username)
    found, _ = _buyers(show.organization_id, {show.id})
    b = found.get(show.id, {}).get(k)
    if b is None:
        raise BoardError("That buyer has nothing in this show.", 404)
    if b.overridden and b.ships:
        return False
    if not may_override(b):
        raise BoardError(
            "Only a buyer waiting in Need review, with something to pack, can be marked."
        )
    # One per show and buyer: a conditional INSERT decides between two presses at once, and
    # an older mark that something has since undone is moved to now.
    row = db.session.scalar(
        select(PackOverride)
        .where(PackOverride.show_id == show.id, PackOverride.buyer_key == k)
        .with_for_update()
    )
    now = datetime.now(UTC)
    if row is None:
        done = db.session.execute(
            insert(PackOverride)
            .values(
                organization_id=show.organization_id,
                show_id=show.id,
                buyer_key=k,
                covered=sorted(b.keys),
                by_id=user.id,
                created_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_pack_overrides_show_buyer")
        ).rowcount
        if not done:
            return False
    else:
        # An older mark something has since undone: it now covers what is open now.
        row.created_at, row.by_id, row.covered = now, user.id, sorted(b.keys)
    audit.record(
        show.organization_id,
        "orders.marked_ready",
        user=user,
        target_type="show",
        target_id=show.id,
        details={
            "buyer": b.username,
            "showDate": show.show_date.isoformat(),
            "covered": sorted(b.reasons & HOLDS) + (["giveaway"] if b.waiting else []),
        },
    )
    return True


def unmark_ready(show: Show, username: str, *, user) -> bool:
    """Takes a manager's "Mark ready to pack" back: the buyer returns to Need review if
    something still waits. True when there was one. Audited; the caller commits."""
    k = key(username)
    row = db.session.scalar(
        select(PackOverride).where(PackOverride.show_id == show.id, PackOverride.buyer_key == k)
    )
    if row is None:
        return False
    db.session.delete(row)
    audit.record(
        show.organization_id,
        "orders.unmarked_ready",
        user=user,
        target_type="show",
        target_id=show.id,
        details={"buyer": username.strip().lstrip("@"), "showDate": show.show_date.isoformat()},
    )
    return True
