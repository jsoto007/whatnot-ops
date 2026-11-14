"""How winners get their product, show by show.

Two ways of selling (Javier's call, 2026-11-13):

- "winners_pick": a lot's title names no product. The winner says which one in chat, by its
  shelf spot ("A5": shelf A, item 5). The laptop is needed, to record the chat.
- "listing": one product per listing. Whatnot's order names the product through the
  listing's link (services/whatnot/catalog.py). No chat, and no laptop, is needed.

The shop has a usual way (`Organization.sale_mode`), which a show follows until a person
chooses otherwise for it (`ShowSaleMode`). One Whatnot show can be several recordings here
(two laptops, or recording restarted): a choice made on one is written to all of them, and a
recording that appears later follows the others.

A listing linked to a product names its sale in either mode. The mode says what a win with
no product waits for, never what a linked listing means.
"""

from flask import g, has_request_context
from sqlalchemy import delete, select

from ..db import db
from ..models import Organization, Show, ShowSaleMode

WINNERS_PICK = "winners_pick"
LISTING = "listing"
MODES = (WINNERS_PICK, LISTING)


class ModeError(ValueError):
    status = 400


def parse(value) -> str:
    if value not in MODES:
        raise ModeError("Choose how it is sold: winners pick in chat, or one product per listing.")
    return value


def _recordings(show: Show) -> list[int]:
    """This show and every other recording of the same Whatnot show, oldest first."""
    if show.whatnot_show_id is None:
        return [show.id]
    ids = db.session.scalars(
        select(Show.id)
        .where(
            Show.organization_id == show.organization_id,
            Show.whatnot_show_id == show.whatnot_show_id,
        )
        .order_by(Show.id)
    ).all()
    return list(ids) or [show.id]


def of_shows(shows: list[Show]) -> dict[int, tuple[str, bool]]:
    """show id → (how it sells, whether that was chosen for the show). Three queries however
    many shows there are.

    Every recording of one Whatnot show gives the same answer: the choice held by its oldest
    recording that has one. So two recordings that each had a choice before they were matched
    to the same Whatnot show still agree, and the next choice (set_show) rewrites them all."""
    if not shows:
        return {}
    usual = dict(
        db.session.execute(
            select(Organization.id, Organization.sale_mode).where(
                Organization.id.in_({s.organization_id for s in shows})
            )
        ).all()
    )
    alone = [s.id for s in shows if s.whatnot_show_id is None]
    own = (
        dict(
            db.session.execute(
                select(ShowSaleMode.show_id, ShowSaleMode.mode).where(
                    ShowSaleMode.show_id.in_(alone)
                )
            ).all()
        )
        if alone
        else {}
    )
    matched = {s.whatnot_show_id for s in shows if s.whatnot_show_id is not None}
    same_show: dict[int, str] = {}
    if matched:
        for whatnot_show_id, mode in db.session.execute(
            select(Show.whatnot_show_id, ShowSaleMode.mode)
            .join(ShowSaleMode, ShowSaleMode.show_id == Show.id)
            .where(
                Show.organization_id.in_(list(usual)),
                Show.whatnot_show_id.in_(matched),
            )
            .order_by(Show.id.desc())  # read newest first, so the oldest recording's is kept
        ):
            same_show[whatnot_show_id] = mode
    out = {}
    for s in shows:
        chosen = own.get(s.id) if s.whatnot_show_id is None else same_show.get(s.whatnot_show_id)
        out[s.id] = (chosen or usual.get(s.organization_id, WINNERS_PICK), chosen is not None)
    return out


def of_show(show: Show) -> tuple[str, bool]:
    """(how the show sells, whether that was chosen for this show)."""
    return of_shows([show])[show.id]


def prime(show_ids) -> None:
    """Look up many shows at once before a list of their sales is written out, so
    `of_show_id` asks the database nothing more (a page of orders can span dozens of shows)."""
    if not has_request_context():
        return
    cache = g.setdefault("_sale_modes", {})
    missing = {i for i in show_ids if i is not None and i not in cache}
    if not missing:
        return
    shows = db.session.scalars(select(Show).where(Show.id.in_(missing))).all()
    for show_id, (mode, _) in of_shows(list(shows)).items():
        cache[show_id] = mode


def of_show_id(show_id: int) -> str:
    """How a show sells, for payloads that meet the same show many times in one request
    (see `prime`)."""
    cache = g.setdefault("_sale_modes", {}) if has_request_context() else {}
    if show_id not in cache:
        show = db.session.get(Show, show_id)
        cache[show_id] = of_show(show)[0] if show is not None else WINNERS_PICK
    return cache[show_id]


def set_show(show: Show, mode: str | None) -> tuple[str, str]:
    """Choose how this show sells; None follows the shop's usual way again. Written to every
    recording of the same Whatnot show. Returns (the mode before, the mode now)."""
    if mode is not None:
        mode = parse(mode)
    ids = _recordings(show)
    # Two people choosing at once: the second waits, then replaces (never a key clash).
    # Held until this request commits; a chat upload for these recordings waits that moment.
    db.session.execute(
        select(Show.id).where(Show.id.in_(ids)).order_by(Show.id).with_for_update(key_share=True)
    )
    before = of_show(show)[0]
    db.session.execute(delete(ShowSaleMode).where(ShowSaleMode.show_id.in_(ids)))
    if mode is not None:
        db.session.add_all(ShowSaleMode(show_id=i, mode=mode) for i in ids)
    db.session.flush()
    if has_request_context():
        g.pop("_sale_modes", None)
    return before, of_show(show)[0]


def set_shop(org: Organization, mode) -> tuple[str, str]:
    """The shop's usual way. Shows with a choice of their own keep it."""
    before = org.sale_mode
    org.sale_mode = parse(mode)
    return before, org.sale_mode


def winners_from_whatnot(org_id: int) -> bool:
    """Whether wins reach the app from Whatnot's own orders with nobody applying them: the
    shop is connected and its orders are applied as they arrive. The laptop then only needs
    the chat, so the seller's own page (which shows no winners) is as good as any."""
    from .whatnot import connection

    conn = connection.active(org_id)
    return conn is not None and conn.orders_mode == "auto"
