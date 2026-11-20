"""One Sales Ops show per Whatnot show.

A Whatnot show nobody records gets a show of its own here once it has been live a while
(`sync.ensure_recording`, link "made"), so its orders have somewhere to live. When a laptop
then starts recording the same show, the two are the same show: the made show is FOLDED
into the laptop's recording and deleted. The recording is the one that stays, because the
laptop keeps uploading to it by its key and its chat is unique per recording; a made show
has no chat at all.

What moves, all by re-pointing (never by cancelling and making again): the sales (same ids,
so the same stock ledger keys: not a unit moves in stock), the buyers' Orders (one per
buyer: two Orders of one buyer become one, notes kept), the Review questions, the Whatnot
lots and parcels read for the show, the AI's call log, and the show's selling way and
shelf layout when the made show's were the ones in use (the oldest recording's are).

A buyer whose box is being picked, packed or shipped in one half and who has an Order in
the other stops the merge: adding unpicked items to that box would pack them twice or call
them picked (`pipeline._in_fulfilment` reads a win paid before picking began as "in the
box"). A person takes the box back a step (or finishes it), then merges.

Only a show with no laptop chat is ever folded. Two laptops that both recorded the show
are two recordings by design (services/recordings.py) and stay so.

When: on its own, at each read of Whatnot's shows (`sync.link_recordings`), while a
recording whose own page address names the Whatnot show and a show the app MADE for it are
both there (`fold_made`: only made shows, never another recording, and never a recording
tied by the clock alone: the clock may be re-decided later). The same when the app splits a
laptop recording that ran through two shows (services/show_split.py): each half takes the
show made for its own Whatnot show. Everything else is the
owner's "Merge" on the show page, with its preview and confirmation: a recording a person
matched by hand ("Match" never merges), a recording tied by the clock, a recording whose
laptop data was deleted. Nothing sweeps old shows: a split that already exists waits for
the owner.
"""

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.exc import OperationalError

from ..db import db
from ..models import (
    AiCall,
    ChatMessage,
    Order,
    ParsedEvent,
    ReviewItem,
    Sale,
    Show,
    ShowSaleMode,
    ShowShelf,
    WhatnotLot,
    WhatnotShipment,
    WhatnotShow,
)
from ..models.orders import FULFILLMENT
from . import audit, orders, pipeline

log = logging.getLogger(__name__)

ACTION = "show.merged"


class MergeError(Exception):
    """Why the shows cannot be merged now, in the shop's words."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass
class Plan:
    keeper: Show | None = None
    # Shows with no laptop chat that fold into the keeper.
    folded: list[Show] = field(default_factory=list)
    # Laptop recordings besides the keeper: they stay recordings of their own.
    laptops: list[Show] = field(default_factory=list)
    sales: int = 0
    orders: int = 0  # Orders that move to the keeper
    joined: int = 0  # of those, Orders that become one with the buyer's Order there
    reviews: int = 0
    lots: int = 0
    # Buyers with a box being picked, packed or shipped and an Order in the other half.
    clashes: list[str] = field(default_factory=list)


def _shows(ws: WhatnotShow) -> list[Show]:
    from .whatnot import sync

    return sync.recordings_of(ws)


def _with_chat(ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return set(
        db.session.scalars(
            select(Show.id).where(
                Show.id.in_(ids),
                exists().where(ChatMessage.show_id == Show.id)
                | exists().where(ParsedEvent.show_id == Show.id),
            )
        )
    )


def _plan(ws: WhatnotShow) -> Plan:
    shows = _shows(ws)
    plan = Plan()
    if not shows:
        return plan
    chatty = _with_chat([s.id for s in shows])
    # The laptop's recording stays (the oldest, if two laptops recorded); with none, the
    # oldest show.
    plan.keeper = next((s for s in shows if s.id in chatty), shows[0])
    plan.folded = [s for s in shows if s is not plan.keeper and s.id not in chatty]
    plan.laptops = [s for s in shows if s is not plan.keeper and s.id in chatty]
    if not plan.folded:
        return plan
    gone = [s.id for s in plan.folded]
    count = lambda model: (  # noqa: E731
        db.session.scalar(select(func.count()).select_from(model).where(model.show_id.in_(gone)))
        or 0
    )
    plan.sales, plan.reviews, plan.lots = count(Sale), count(ReviewItem), count(WhatnotLot)
    every = db.session.scalars(
        select(Order).where(Order.show_id.in_([plan.keeper.id, *gone])).order_by(Order.id)
    ).all()
    plan.orders = sum(1 for o in every if o.show_id != plan.keeper.id)
    # A name is one buyer whatever its case, as `orders.attach` reads it ("Kat.C" in chat,
    # "kat.c" on Whatnot): grouped by its lower case, named as the first Order spells it.
    by_buyer: dict[str, list[Order]] = {}
    for o in every:
        by_buyer.setdefault(o.buyer_username.lower(), []).append(o)
    for _key, held in sorted(by_buyer.items()):
        if len(held) < 2:
            continue
        plan.joined += len(held) - 1
        if _target(held) is None:
            plan.clashes.append(held[0].buyer_username)
    return plan


def _target(held: list[Order]) -> Order | None:
    """Which of one buyer's Orders the others join, or None when they cannot be joined now:
    one in a box (picking, packed, shipped) takes only Orders with nothing standing (all
    cancelled or declined); otherwise the first."""
    boxed = [o for o in held if o.status in FULFILLMENT]
    if not boxed:
        return held[0]
    if len(boxed) > 1:
        return None
    rest = [o for o in held if o is not boxed[0]]
    if any(s.status in orders.STANDING for o in rest for s in o.sales):
        return None
    return boxed[0]


def _blockers(plan: Plan) -> list[str]:
    out = []
    if plan.clashes:
        n = len(plan.clashes)
        names = ", ".join(plan.clashes[:5]) + ("…" if n > 5 else "")
        out.append(
            f"{n} {'buyer has' if n == 1 else 'buyers have'} a box being picked, packed or "
            f"shipped in one of these shows and more items in the other ({names}). Put "
            f"{'that box' if n == 1 else 'those boxes'} back to ready to pick first, then "
            "merge (a box that has shipped cannot take more items)."
        )
    return out


def split_of(show: Show) -> dict | None:
    """For the show page: the app shows that are this show's Whatnot show when there is
    more than one and the owner's merge would fold some ({"keeper": the one that stays,
    "shows": all of them}), else None."""
    if show.whatnot_show_id is None:
        return None
    ws = db.session.get(WhatnotShow, show.whatnot_show_id)
    if ws is None:
        return None
    shows = _shows(ws)
    if len(shows) < 2:
        return None
    plan = _plan(ws)
    if not plan.folded:
        return None  # two laptops' recordings: two shows by design, nothing to merge
    return {"keeper": plan.keeper.id, "shows": [s.id for s in shows]}


def preview(ws: WhatnotShow) -> dict:
    """What merging the Whatnot show's app shows would do. Changes nothing."""
    plan = _plan(ws)
    return {
        "keeper": plan.keeper.id if plan.keeper else None,
        "folded": [s.id for s in plan.folded],
        "laptops": [s.id for s in plan.laptops],
        "sales": plan.sales,
        "orders": plan.orders,
        "joined": plan.joined,
        "reviews": plan.reviews,
        "lots": plan.lots,
        "blockers": _blockers(plan),
    }


def _lock(ws: WhatnotShow, *, wait: bool) -> None:
    """The parse lock of every show of the Whatnot show, in a fixed order (as Whatnot's
    apply and the parser take them, so neither makes a sale in a half being folded), then
    the show rows."""
    ids = sorted(
        db.session.scalars(
            select(Show.id).where(
                Show.organization_id == ws.organization_id, Show.whatnot_show_id == ws.id
            )
        )
    )
    for show_id in ids:
        if not pipeline._lock_show(show_id, wait=wait):
            raise MergeError("This show's chat is being read right now. Try again in a minute.")
    db.session.execute(select(Show.id).where(Show.id.in_(ids)).order_by(Show.id).with_for_update())


def _carry_settings(keeper: Show, gone: Show, mode_before: tuple[str, bool]) -> None:
    """The selling way and shelf layout in use stay in use: both are read from the oldest
    recording that has one, often the made show."""
    from . import sale_mode, shelves

    if shelves._sources([keeper.id]).get(keeper.id) == gone.id:
        rows = db.session.execute(
            select(ShowShelf.code, ShowShelf.sku_id).where(ShowShelf.show_id == gone.id)
        ).all()
        db.session.execute(delete(ShowShelf).where(ShowShelf.show_id == keeper.id))
        if rows:
            db.session.execute(
                insert(ShowShelf),
                [{"show_id": keeper.id, "code": c, "sku_id": k} for c, k in rows],
            )
    db.session.execute(delete(ShowSaleMode).where(ShowSaleMode.show_id == gone.id))
    db.session.flush()
    db.session.expire(keeper)
    if mode_before[1] and sale_mode.of_show(keeper) != mode_before:
        db.session.execute(delete(ShowSaleMode).where(ShowSaleMode.show_id == keeper.id))
        db.session.add(ShowSaleMode(show_id=keeper.id, mode=mode_before[0]))
        db.session.flush()


def _fold(keeper: Show, gone: Show) -> tuple[int, int]:
    """Re-point everything of `gone` at `keeper`, join each buyer's Orders, delete `gone`.
    Returns (Orders moved, Orders joined)."""
    from . import sale_mode

    mode_before = sale_mode.of_show(keeper)
    moved = joined = 0
    touched: set[int] = set()
    for o in db.session.scalars(
        select(Order).where(Order.show_id == gone.id).order_by(Order.id).with_for_update()
    ).all():
        moved += 1
        there = db.session.scalar(
            select(Order)
            .where(
                Order.show_id == keeper.id,
                func.lower(Order.buyer_username) == o.buyer_username.lower(),
            )
            .order_by(Order.id)
            .limit(1)
            .with_for_update()
        )
        if there is None:
            o.show_id = keeper.id
            touched.add(o.id)
            continue
        joined += 1
        target = _target([there, o])
        if target is None:  # checked before anything moved; a race lost here changes nothing
            raise MergeError(_blockers(Plan(clashes=[o.buyer_username]))[0])
        other = o if target is there else there
        db.session.execute(update(Sale).where(Sale.order_id == other.id).values(order_id=target.id))
        notes = [n for n in (target.notes, other.notes) if n]
        target.notes = "\n".join(dict.fromkeys(notes)) or None
        target.tracking = target.tracking or other.tracking
        target.packed_by = target.packed_by or other.packed_by
        db.session.flush()
        # A bulk delete: the ORM's own delete would first clear the moved sales' link.
        db.session.execute(
            delete(Order).where(Order.id == other.id).execution_options(synchronize_session=False)
        )
        db.session.expunge(other)
        target.show_id = keeper.id  # the buyer's other Order is gone before this one moves
        touched.add(target.id)
    db.session.flush()
    for model in (Sale, ReviewItem, WhatnotLot, WhatnotShipment, AiCall):
        db.session.execute(update(model).where(model.show_id == gone.id).values(show_id=keeper.id))
    _carry_settings(keeper, gone, mode_before)
    db.session.flush()
    db.session.execute(  # its shelves and selling way go with it (ON DELETE CASCADE)
        delete(Show).where(Show.id == gone.id).execution_options(synchronize_session=False)
    )
    db.session.expunge(gone)
    db.session.expire_all()
    for order in db.session.scalars(select(Order).where(Order.id.in_(touched or {0}))):
        orders.refresh(order)
    return moved, joined


def merge(ws: WhatnotShow, *, user=None, wait: bool = True, made_only: bool = False) -> dict:
    """Fold every show of the Whatnot show with no laptop chat into its laptop recording
    (see the module docstring). The caller commits; raises MergeError, having changed
    nothing, when it may not be done now. A person's merge also pins the recording to the
    Whatnot show (link "manual"): the clock never re-decides it. `made_only` (the merge on
    its own): only shows the app made fold, into a recording its address names or a split
    tied (`sync.pinned`)."""
    from .whatnot import sync

    _lock(ws, wait=wait)
    plan = _plan(ws)
    if plan.keeper is None or not plan.folded:
        raise MergeError(
            "This Whatnot show is already one show here."
            if not plan.laptops
            else "Each of these shows was recorded by a laptop, so they stay apart."
        )
    if made_only and (
        not sync.pinned(plan.keeper, ws) or any(s.whatnot_link != "made" for s in plan.folded)
    ):
        raise MergeError("Only the owner merges these shows.")
    if blockers := _blockers(plan):
        raise MergeError(blockers[0])
    keeper = plan.keeper
    details = {
        "date": keeper.show_date.isoformat(),
        "title": ws.title or "",
        "shows": len(plan.folded),
        "sales": plan.sales,
        "orders": plan.orders,
        "joined": plan.joined,
        "reviews": plan.reviews,
        "lots": plan.lots,
        "from": [s.id for s in plan.folded],
    }
    for gone in plan.folded:
        _fold(keeper, gone)
    if user is not None and keeper.whatnot_link == "auto":
        sync.tie(keeper, ws.id, "manual")
    audit.record(
        ws.organization_id,
        ACTION,
        user=user,
        target_type="show",
        target_id=keeper.id,
        details=details,
    )
    return {"showId": keeper.id, **details}


def fold_made(ws: WhatnotShow, *, wait: bool = False) -> dict | None:
    """On its own, at a read of Whatnot's shows (`sync.link_recordings`): fold the shows the
    app made for the Whatnot show into the recording its own page address names (or a half
    of a split recording, `sync.pinned`). Only made shows, and only into such a recording:
    a recording a person matched, one the clock tied and one whose laptop data was deleted
    wait for the owner's "Merge". None when there is
    nothing to fold or it cannot be done now; the next read tries again (a parse running
    is not waited for). Never raises: the reading of Whatnot's shows must go on."""
    from .whatnot import sync

    plan = _plan(ws)
    if plan.keeper is None or not plan.folded:
        return None
    if not sync.pinned(plan.keeper, ws) or any(s.whatnot_link != "made" for s in plan.folded):
        return None
    try:
        with db.session.begin_nested():
            return merge(ws, wait=wait, made_only=True)
    except MergeError as e:
        log.info("shows of Whatnot show %s not merged: %s", ws.id, e)
    except OperationalError:
        # A deadlock with the parser or another apply: only this savepoint is undone, and
        # the next read tries again.
        log.warning("shows of Whatnot show %s not merged just now", ws.id, exc_info=True)
    return None


def date_words(d: dict) -> str:
    try:
        when = date.fromisoformat(d.get("date") or "")
    except ValueError:
        return "a show"
    return f"the {when:%b} {when.day} show"
