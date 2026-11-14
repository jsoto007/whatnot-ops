"""Where each product sits for one show, so a winner's pick ("B4": shelf B, spot 4) can be read.

Optional, per show: a person lays the shelves out before the show when they want to (Javier's
call, 2026-11-13). The layout only suggests a product in Review; a person still picks it.
Chat is attacker-controlled, so a line never names a product on its own.

One show on Whatnot can be several recordings here (two laptops, or recording started again).
The layout is the show's: saving it writes it to every recording, and a recording that
appears later uses the oldest one's (`_sources`).
"""

import re
from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import ParsedEvent, Product, Sale, Show, ShowShelf, Sku
from . import sale_chat
from .dashboard import STANDING

_SPOT = re.compile(r"([A-Za-z])0*([0-9]{1,2})")
# A spot as a winner types it: "B4", "b04" (not inside a longer word or number).
_IN_CHAT = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][0-9]{1,3})(?![A-Za-z0-9])")
MAX_SPOTS = 300


class ShelfError(ValueError):
    status = 400


# A product as the shop names it: the product, then the SKU's own name when it has one.
_NAME = func.trim(func.concat(Product.name, " ", Sku.name))


def _spot(text: str) -> str | None:
    """ "b04" → "B4"; None for anything that is not a letter and a number."""
    m = _SPOT.fullmatch(text.strip())
    return f"{m[1].upper()}{m[2]}" if m else None


def _few(codes: list[str]) -> str:
    return ", ".join(c[:64] for c in codes[:5])


def _sources(show_ids) -> dict[int, int]:
    """show id → the recording whose layout it uses. Every recording of one Whatnot show
    uses the same one: the oldest of them that has a layout (so two recordings that were
    each laid out before they were matched to the show still agree). A recording not
    matched to a Whatnot show uses its own. A show with none is left out."""
    shows = db.session.execute(
        select(Show.id, Show.whatnot_show_id, Show.organization_id).where(
            Show.id.in_(set(show_ids))
        )
    ).all()
    whole = {w for _, w, _ in shows if w is not None}
    recordings: dict[int, list[int]] = defaultdict(list)
    if whole:
        for show_id, w in db.session.execute(
            select(Show.id, Show.whatnot_show_id)
            .where(
                Show.organization_id.in_({org for _, _, org in shows}),
                Show.whatnot_show_id.in_(whole),
            )
            .order_by(Show.id)
        ):
            recordings[w].append(show_id)
    every = {i for i, _, _ in shows} | {i for ids in recordings.values() for i in ids}
    laid_out = set(
        db.session.scalars(select(ShowShelf.show_id).where(ShowShelf.show_id.in_(every)).distinct())
    )
    out = {}
    for show_id, w, _ in shows:
        if w is not None:
            source = next((i for i in recordings[w] if i in laid_out), None)
        else:
            source = show_id if show_id in laid_out else None
        if source is not None:
            out[show_id] = source
    return out


def read(show: Show) -> list[dict]:
    source = _sources([show.id]).get(show.id)
    rows = db.session.execute(
        select(ShowShelf.code, Sku.id, Sku.code, _NAME)
        .join(Sku, Sku.id == ShowShelf.sku_id)
        .join(Product, Product.id == Sku.product_id)
        .where(ShowShelf.show_id == source)
    ).all()
    spots = [{"spot": c, "skuId": i, "code": code, "name": name} for c, i, code, name in rows]
    return sorted(spots, key=lambda r: (r["spot"][0], int(r["spot"][1:])))


def save(show: Show, lines: list[tuple[str, str]]) -> list[dict]:
    """Replace the show's layout. `lines` are (spot, product code) pairs as typed; all or
    nothing. Returns the new layout."""
    if len(lines) > MAX_SPOTS:
        raise ShelfError(f"A show can have at most {MAX_SPOTS} shelf spots.")
    wanted: dict[str, str] = {}
    for typed, code in lines:
        spot = _spot(typed)
        if spot is None:
            raise ShelfError(
                f"“{typed.strip()[:8]}” is not a shelf spot: a letter and a number, like B4."
            )
        if spot in wanted:
            raise ShelfError(f"{spot} is listed twice.")
        if not code.strip():
            raise ShelfError(f"{spot} needs a product code after it.")
        wanted[spot] = code.strip()
    found: dict[str, list[int]] = defaultdict(list)
    for code, sku_id in db.session.execute(
        select(func.lower(Sku.code), Sku.id).where(
            Sku.organization_id == show.organization_id,
            Sku.active,
            func.lower(Sku.code).in_({c.lower() for c in wanted.values()}),
        )
    ):
        found[code].append(sku_id)
    missing = sorted({c for c in wanted.values() if not found.get(c.lower())})
    if missing:
        raise ShelfError(f"No product in use has the code {_few(missing)}.")
    twice = sorted({c for c in wanted.values() if len(found[c.lower()]) > 1})
    if twice:
        raise ShelfError(f"More than one product has the code {_few(twice)}.")
    # The layout is the show's, whichever recording it was typed on: every one gets it.
    ids = sale_chat.recordings_of(show)
    # Two saves at once for one show: the second waits, then replaces (never a key clash).
    db.session.execute(
        select(Show.id).where(Show.id.in_(ids)).order_by(Show.id).with_for_update(key_share=True)
    )
    db.session.execute(delete(ShowShelf).where(ShowShelf.show_id.in_(ids)))
    db.session.add_all(
        ShowShelf(show_id=show_id, code=spot, sku_id=found[code.lower()][0])
        for show_id in ids
        for spot, code in wanted.items()
    )
    db.session.flush()
    return read(show)


def suggest(sales: list[Sale]) -> dict[int, dict]:
    """sale id → the spot its winner named, as {"spot", "skuId", "name"}: from the winner's
    latest own chat line after the win that names a spot, when it names exactly one and that
    spot is laid out with a product in use. Only for shows with a layout. Which win a line
    belongs to is sale_chat's rule, so it needs every standing sale of those shows."""
    sources = _sources({s.show_id for s in sales})
    if not sources:
        return {}
    laid: dict[int, dict[str, tuple[int, str]]] = defaultdict(dict)
    for show_id, spot, sku_id, name in db.session.execute(
        select(ShowShelf.show_id, ShowShelf.code, Sku.id, _NAME)
        .join(Sku, Sku.id == ShowShelf.sku_id)
        .join(Product, Product.id == Sku.product_id)
        .where(ShowShelf.show_id.in_(set(sources.values())), Sku.active)
    ):
        laid[show_id][spot] = (sku_id, name)
    # Each show asked about, with the layout of the recording it uses.
    layout = {show_id: laid[source] for show_id, source in sources.items() if laid.get(source)}
    if layout:
        # A laptop whose clock is out stamps a line a minute from when it was typed: the pick
        # would be handed to the wrong win. Such a show gets no suggestion; a person reads it.
        shows = db.session.scalars(select(Show).where(Show.id.in_(list(layout)))).all()
        for show_id in sale_chat.unsafe(list(shows)):
            layout.pop(show_id, None)
    if not layout:
        return {}
    standing = db.session.scalars(
        select(Sale)
        .where(Sale.show_id.in_(list(layout)), Sale.status.in_(STANDING))
        # sale_chat reads each win's line: one query for all, not two per sale.
        .options(selectinload(Sale.event).selectinload(ParsedEvent.message))
    ).all()
    lines = sale_chat.around(list(standing), limit=None)
    out: dict[int, dict] = {}
    for sale in sales:
        spots = layout.get(sale.show_id)
        for line in reversed(lines.get(sale.id, []) if spots else []):
            if line["role"] != "buyer" or not line["after"]:
                continue
            named = {spot for spot in map(_spot, _IN_CHAT.findall(line["body"])) if spot}
            if not named:
                continue
            # The latest line naming a spot decides: "actually C9" after "B4" drops B4, even
            # when C9 is not laid out; two spots in one line suggest nothing.
            if len(named) == 1 and (spot := next(iter(named))) in spots:
                out[sale.id] = {"spot": spot, "skuId": spots[spot][0], "name": spots[spot][1]}
            break
    return out
