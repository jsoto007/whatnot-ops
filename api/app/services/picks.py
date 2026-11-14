"""The winner's pick, applied (stage 3 of the API-first plan).

In a winner's-pick show the winner names the product by typing a shelf spot in chat ("B4").
`shelves.said` reads each win's final pick. Ten minutes after the show ends on Whatnot,
`run` goes through the wins still waiting for their product:

- one spot with a product on it, and every safeguard below holds: the product is named
  through the decision a person makes in Review (`pipeline.resolve`, actor "pick"), so the
  unit is held and the sale paid exactly as when a person does it. Only when the shop has
  switched it on (`Organization.auto_pick`).
- anything else: the review item is told what was found (`proposal["pick"]`): the product
  the winner picked and why it was not assigned by itself, or that they did not pick, or
  the spot that has no product. A person decides, with one press when there is a product.

Which win a spot is for (the rule agreed in the plan; `_final`). A winner may pick at any
time until the show ends, so each winner's own lines are read in order:
1. a line that names a lot they won and one spot ("#45 C9") is for that lot;
2. otherwise the spot fills their oldest win that no line has been used for yet: one line,
   one win (a win already named used up its line too, so a second run, or a person who
   named the first win, never pushes one line on to the next win);
3. with no win waiting and exactly one win in the show, the spot replaces its pick (a
   change of mind);
4. with no win waiting and several wins, the app cannot tell which pick is being changed:
   none of that winner's picks is applied by itself;
and none of that winner's picks is applied by itself either when a line names more than one
spot, holds a spot-like word in a longer sentence ("ship it b4 friday": `_reads_as_pick`),
points at a win in words ("B4 for the first one"), or repeats the same spot onto another
win; a pick typed within half a minute before the winner's next win was recorded (it may be
for that one) is held too.

The safeguards. Chat is typed by anyone watching, and an assignment moves stock:
- the line is the win's own buyer's (a plain ASCII name equal to the buyer's: a look-alike
  is nobody), typed after the win and before the show ended (picks close with the show);
- Whatnot confirmed the win: the sale is tied to an order Whatnot holds as paid;
- every laptop that recorded the show's chat is KNOWN to be in step with the server
  (`sale_chat.clocks`): "cannot be told" is not enough, because a line stamped a minute off
  is handed to the buyer's other win;
- nothing about the sale is in doubt (a card seen declined, a lot with two winners).

Running it again changes nothing that was decided: a win already named is not waiting, and a
win a person named meanwhile is theirs.
"""

import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import (
    ChatMessage,
    Organization,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotOrder,
    WhatnotShow,
)
from . import audit, catalog, jobs, pipeline, rules, sale_chat, sale_mode, shelves
from .inventory import Actor

log = logging.getLogger(__name__)

PICK_ACTOR = Actor("pick", None, "the winner's pick")
# Picks close when the show ends; the worker reads them this long after, so the last lines
# and the last orders have reached the app.
CLOSE_AFTER = timedelta(minutes=10)
# The order states Whatnot holds as paid (services/whatnot/orders.PAYMENT).
PAID = ("pending_fulfillment", "completed")
DOUBTS = (pipeline.DECLINE_SEEN, pipeline.LOT_WON_TWICE)

# `proposal["pick"]["state"]`
PRODUCT, NONE, UNKNOWN_CODE, SEVERAL = "product", "none", "unknown_code", "several"
# The picks could not be read at all, so nothing is said about what anyone picked (it is not
# "did not pick"): a laptop's clock was out during the show, or the show has no shelf layout.
UNREAD, NO_LAYOUT = "unread", "no_layout"
# `proposal["pick"]["held"]`: why a product the winner picked was not assigned by itself.
OFF, UNCONFIRMED, CLOCK, DOUBT = "off", "unconfirmed", "clock", "doubt"
# The line may be for the winner's next win; the winner typed more than could be matched.
NEAR, UNCLEAR = "near", "unclear"
# A line this close before a win was recorded may be about that win: Whatnot stamps an order
# some seconds after the hammer, and a laptop counts as in step up to 15 seconds slow.
NEAR_WINDOW = timedelta(seconds=30)


def closes_at(ws: WhatnotShow | None) -> datetime | None:
    """When the worker reads the show's picks: ten minutes after it ended on Whatnot. None
    while the show is not over (or Whatnot gave no end time)."""
    if ws is None or ws.status != "ended" or ws.ended_at is None:
        return None
    return ws.ended_at + CLOSE_AFTER


def clocks_sure(show_ids: list[int]) -> bool:
    """Every clock that stamped these recordings' chat is known to be in step with the
    server. A show with no chat at all is not sure of anything."""
    state = sale_chat.clocks(show_ids)
    stamped = sale_chat._sources_of(show_ids)
    sources = {src for i in show_ids for src in stamped[i]}
    return bool(sources) and all(state.get(src, sale_chat.UNKNOWN).ok is True for src in sources)


def _waiting(show_ids: list[int]) -> list[tuple[ReviewItem, Sale]]:
    """The wins still waiting for their product, with the open decision each one has."""
    return list(
        db.session.execute(
            select(ReviewItem, Sale)
            .join(Sale, ReviewItem.sale_id == Sale.id)
            .where(
                Sale.show_id.in_(show_ids),
                Sale.status == SaleStatus.detected,
                Sale.sku_id.is_(None),
                ReviewItem.status == ReviewStatus.open,
                ReviewItem.reason == "unmatched_item",
            )
            .order_by(Sale.id, ReviewItem.id)
        ).all()
    )


def _buyers_lines(show_ids: list[int], buyers: set[str], since, until) -> dict[str, list]:
    """Each buyer's own chat lines in the show (every recording, a line two laptops recorded
    counted once), oldest first. Only a plain ASCII name equal to the buyer's counts."""
    rows = db.session.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.show_id.in_(show_ids),
            ChatMessage.source == "chat",
            func.lower(ChatMessage.username).in_(buyers),
            ChatMessage.captured_at >= since,
            ChatMessage.captured_at <= until,
        )
        .order_by(ChatMessage.captured_at, ChatMessage.show_id, ChatMessage.seq)
    ).all()
    out: dict[str, list] = defaultdict(list)
    for m in sale_chat._once(list(rows), []):
        name = m.username or ""
        if name.isascii() and name.lower() in buyers:
            out[name.lower()].append(m)
    return out


# Words a pick line may hold besides the spot: "B4 please", "actually C3", "I'll take b4".
_FILLER = frozenset(
    "a actually after all and can change could get gimme go have i id ill instead it lets "
    "like lot make me mine my no number of oh ok okay on one pick please pls plz put shelf "
    "sorry spot switch take thank thanks that the to ty want wanna wait with would yes you".split()
)
# Words that point at one of several wins. The app does not read them: the line is held.
_POINTING = frozenset(
    "first second third last latest other both each earlier previous prior next same "
    "1st 2nd 3rd former latter".split()
)
_WORD = re.compile(r"[a-z0-9']+")
MAX_OTHER_WORDS = 1


def _reads_as_pick(body: str, spots: list[str]) -> bool:
    """Whether a line that names a spot is a pick, not chatter that happens to hold a
    letter and a number ("can u ship it b4 friday", "is the i7 any good"). A pick line is
    the spot with little else: after the spot, any lot number and a few filler words are
    set aside, at most one word is left, and it does not point at one of several wins
    ("the first one"). A question ("is B4 still there?") is not a pick either."""
    if "?" in (body or ""):
        return False
    words = _WORD.findall((body or "").lower().replace("’", "'"))
    left = [
        w.replace("'", "")
        for w in words
        if not w.isdigit() and (shelves._spot(w) or "") not in spots
    ]
    left = [w for w in left if w and w not in _FILLER]
    return not (set(left) & _POINTING) and len(left) <= MAX_OTHER_WORDS


def _final(wins: list[tuple[datetime, Sale]], lines: list, layout: dict) -> tuple[dict, bool]:
    """One buyer's picks at the end of the show: sale id → what was picked for it, and
    whether anything they typed could not be matched to a win (then none of their picks is
    applied by itself). `wins`: every standing sale of the buyer in the show with when a line
    can first be about it, oldest first. `lines`: their own lines, oldest first. The module's
    docstring has the rule."""
    picks: dict[int, dict] = {}
    settled = {sale.id for _, sale in wins if sale.sku_id is not None}  # named already
    # A line is used up by the win it is for, named already or not: a win a person (or an
    # earlier run of this worker) named took its line, which must not move on to the next.
    taken: set[int] = set()
    last: tuple[str, int] | None = None  # the spot of the previous pick line, and its win
    unclear = False
    for m in lines:
        codes = shelves.typed(m.body)
        if not codes:
            continue
        at = m.captured_at
        won = [sale for start, sale in wins if start <= at]
        soon = [sale for start, sale in wins if at < start <= at + NEAR_WINDOW]
        waiting = [s for s in won if s.id not in taken]
        if not won and not soon:
            continue  # typed before they had won anything: not a pick
        if len(codes) > 1 or not _reads_as_pick(m.body, codes):
            # "B4 or C3?", or chatter that holds a spot-like word: never guessed, and
            # never ignored either. The win it would have filled says what was typed.
            target = (waiting or soon or [None])[0]
            if len(codes) > 1 and target is not None and target.id not in settled:
                picks[target.id] = {"state": SEVERAL, "spots": codes}
            unclear = True
            continue
        spot = codes[0]
        lots = rules.lot_refs(m.body)
        if lots:
            named = [(start, sale) for start, sale in wins if sale_chat.lot_of(sale) in lots]
            if len(lots) != 1 or len(named) != 1 or at < named[0][0] - NEAR_WINDOW:
                # More than one lot, a lot they did not win (or won twice), or a lot they
                # had not won yet when they typed it.
                unclear = True
                continue
            target, near = named[0][1], at < named[0][0]
        elif waiting:
            target, near = waiting[0], bool(soon)
        elif not won:
            target, near = soon[0], True
        elif len(wins) == 1:
            target, near = won[0], False  # their only win: a change of mind
        else:
            unclear = True  # several wins and none waiting: which pick is being changed?
            continue
        if not lots and last is not None and last[0] == spot and last[1] != target.id:
            # The same spot again, landing on another win: said twice for one win (the host
            # did not hear it), or one for each? Not guessed.
            unclear = True
        last = (spot, target.id)
        taken.add(target.id)
        if target.id in settled:
            continue  # named already: the line was its own, and changes nothing
        known = layout.get(spot)
        picks[target.id] = (
            {"state": PRODUCT, "spot": spot, "skuId": known[0], "name": known[1], "near": near}
            if known
            else {"state": UNKNOWN_CODE, "spot": spot}
        )
    return picks, unclear


def run(show: Show, *, now: datetime | None = None) -> dict | None:
    """Read the picks of the show (every recording of it) and settle the wins still waiting.
    None while the picks are not closed yet. Otherwise how many wins went each way; the
    caller commits."""
    from .whatnot import orders as whatnot_orders
    from .whatnot import sync

    ws = whatnot_orders.whatnot_show_of(show)
    at = closes_at(ws)
    if at is None or (now or datetime.now(UTC)) < at:
        return None
    recordings = sync.recordings_of(ws) or [show]
    ids = sorted(s.id for s in recordings)
    # One at a time per show, and never while its chat is being parsed or its orders
    # applied: all three name sales of the same wins.
    for show_id in ids:
        pipeline._lock_show(show_id, wait=True)
    modes = sale_mode.of_shows(recordings)
    picking = [i for i in ids if modes[i][0] == sale_mode.WINNERS_PICK]
    waiting = _waiting(picking) if picking else []
    if not waiting:
        return {}
    org = db.session.get(Organization, show.organization_id)
    state = sale_chat.clocks(ids)
    sure = clocks_sure(ids)
    unread = bool(sale_chat.unsafe(recordings))
    layout = shelves.spots(recordings[0])
    found: dict[int, dict] = {}
    unclear: set[str] = set()
    if layout and not unread:
        # Every standing sale of the buyers who are waiting: a line goes to one of a
        # buyer's wins, so all of them decide where it goes.
        buyers = {sale.buyer_username.lower() for _, sale in waiting}
        in_step = frozenset(source for source, clock in state.items() if clock.ok)
        wins: dict[str, list[tuple[datetime, Sale]]] = defaultdict(list)
        for sale in db.session.scalars(
            select(Sale)
            .where(
                Sale.show_id.in_(ids),
                Sale.status.in_(sale_chat._STANDING),
                func.lower(Sale.buyer_username).in_(buyers),
            )
            .options(selectinload(Sale.event).selectinload(ParsedEvent.message))
            .order_by(Sale.id)
        ):
            start = sale_chat.picks_from(sale, in_step)
            if start is None:
                unclear.add(sale.buyer_username.lower())  # a win nobody can place in time
                continue
            wins[sale.buyer_username.lower()].append((start, sale))
        since = min((start for starts in wins.values() for start, _ in starts), default=None)
        lines = (
            # Picks close when the show ends: a line typed afterwards is not a pick.
            _buyers_lines(ids, buyers, since - NEAR_WINDOW, ws.ended_at)
            if since is not None
            else {}
        )
        for buyer, theirs in wins.items():
            theirs.sort(key=lambda w: (w[0], w[1].id))
            picked, vague = _final(theirs, lines.get(buyer, []), layout)
            found.update(picked)
            if vague:
                unclear.add(buyer)
    paid = set(
        db.session.scalars(
            select(WhatnotOrder.whatnot_id).where(
                WhatnotOrder.organization_id == ws.organization_id,
                WhatnotOrder.whatnot_show_id == ws.whatnot_id,
                WhatnotOrder.status.in_(PAID),
            )
        )
    )
    in_doubt = set(
        db.session.scalars(
            select(ReviewItem.sale_id).where(
                ReviewItem.sale_id.in_([sale.id for _, sale in waiting]),
                ReviewItem.status == ReviewStatus.open,
                ReviewItem.reason.in_(DOUBTS),
            )
        )
    )
    summary: Counter = Counter()
    for item, sale in waiting:
        if unread or not layout:
            pick = {"state": UNREAD if unread else NO_LAYOUT}
        else:
            pick = dict(found.get(sale.id) or {"state": NONE})
        if pick["state"] == PRODUCT:
            near = pick.pop("near", False)
            pick["held"] = [
                why
                for why, holds in (
                    (UNCONFIRMED, sale.external_order_id not in paid),
                    (CLOCK, not sure),
                    (DOUBT, sale.id in in_doubt),
                    (NEAR, near),
                    (UNCLEAR, sale.buyer_username.lower() in unclear),
                )
                if holds
            ]
        if pick["state"] == PRODUCT and not pick["held"] and org.auto_pick:
            try:
                pipeline.resolve(
                    item,
                    "assign_sku",
                    {"skuId": pick["skuId"], "remember": False},
                    user=None,
                    actor=PICK_ACTOR,
                )
            except (pipeline.ReviewError, catalog.CatalogError):
                continue  # a person decided this win a moment ago: theirs
            # The evidence stays with the decision: which spot the winner typed.
            item.resolution = {**(item.resolution or {}), "by": "pick", "spot": pick["spot"]}
            summary["assigned"] += 1
            continue
        if pick["state"] == PRODUCT and not pick["held"]:
            pick["held"] = [OFF]
        before = (item.proposal or {}).get("pick")
        # An AI's suggestion on the item is left as it is; ours is refreshed.
        if item.proposal is None or "pick" in item.proposal:
            item.proposal = {
                "action": "assign_sku" if pick["state"] == PRODUCT else "none",
                **({"skuId": pick["skuId"]} if pick["state"] == PRODUCT else {}),
                "pick": pick,
            }
            if before != pick:
                summary["changed"] += 1
        summary["suggested" if pick["state"] == PRODUCT else pick["state"]] += 1
    return dict(summary)


def schedule(ws: WhatnotShow | None) -> None:
    """Queue the reading of a show's picks for when they close. Once per show at a time;
    queueing it again after it ran (late orders) reads again, which is safe."""
    at = closes_at(ws)
    if at is None:
        return
    wait = max(0, int((at - datetime.now(UTC)).total_seconds()))
    jobs.enqueue(
        "pick_show",
        {"org_id": ws.organization_id, "whatnot_show_id": ws.whatnot_id},
        org_id=ws.organization_id,
        dedupe_key=f"pick_show:{ws.organization_id}:{ws.whatnot_id}",
        delay_s=wait + 5 if wait else 0,
    )


@jobs.handler("pick_show")
def pick_show_job(payload: dict) -> None:
    from .whatnot import sync

    ws = db.session.scalar(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == payload["org_id"],
            WhatnotShow.whatnot_id == payload["whatnot_show_id"],
        )
    )
    recordings = sync.recordings_of(ws) if ws is not None else []
    at = closes_at(ws)
    if not recordings or at is None:
        return
    if datetime.now(UTC) < at:
        if not jobs.inline():  # inline, the next apply or the timer comes back to it
            schedule(ws)
        return
    summary = run(recordings[0])
    if summary and (summary.get("assigned") or summary.get("changed")):
        audit.record(
            ws.organization_id,
            "picks.read",
            actor_type="pick",
            target_type="show",
            target_id=recordings[0].id,
            details={k: v for k, v in summary.items() if k != "changed"},
        )
