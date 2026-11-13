"""Numbers for the dashboard and the reports, in one place so tiles and spreadsheets agree."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, func, select

from ..db import db
from ..models import (
    BuyerName,
    ChatMessage,
    EventKind,
    Order,
    OrderStatus,
    ParsedEvent,
    Product,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    Sku,
    WhatnotConnection,
)

STANDING = (SaleStatus.detected, SaleStatus.reserved, SaleStatus.paid, SaleStatus.fulfilled)
PAID = (SaleStatus.paid, SaleStatus.fulfilled)
LOST = (SaleStatus.cancelled, SaleStatus.payment_failed)
TYPES = ("red", "bundle", "ultra", "mega", "quick_hit")
LIVE_WINDOW = timedelta(minutes=10)


def _qty(status_filter):
    return func.coalesce(
        func.sum(case((Sale.status.in_(status_filter), Sale.quantity), else_=0)), 0
    )


def _rev(status_filter):
    return func.coalesce(
        func.sum(
            case(
                (
                    Sale.status.in_(status_filter),
                    Sale.quantity * func.coalesce(Sale.price_cents, 0),
                ),
                else_=0,
            )
        ),
        0,
    )


def show_tiles(show: Show) -> dict:
    """The sheet's tiles for one show, plus money and queue counts."""
    row = db.session.execute(
        select(
            _qty(STANDING).label("items"),
            func.count(func.distinct(case((Sale.status.in_(STANDING), Sale.buyer_username)))).label(
                "buyers"
            ),
            _rev(PAID).label("paid"),
            # Whatnot charges at the win, so only sales recorded before that wait for the
            # payment file; an unrecognised item (detected) waits in Review, not for money.
            _rev((SaleStatus.reserved,)).label("pending"),
            func.coalesce(func.sum(case((Sale.status.in_(LOST), 1), else_=0)), 0).label("lost"),
            *[
                func.coalesce(
                    func.sum(
                        case(
                            ((Sale.status.in_(STANDING)) & (Sale.auction_type == t), Sale.quantity),
                            else_=0,
                        )
                    ),
                    0,
                ).label(t)
                for t in TYPES
            ],
        ).where(Sale.show_id == show.id)
    ).one()
    # Gifts and no-winner auctions are lines, not sales; only host/platform lines count,
    # or a viewer typing "won the giveaway!" would move the tile.
    events = dict(
        db.session.execute(
            select(ParsedEvent.kind, func.count())
            .where(ParsedEvent.show_id == show.id, ParsedEvent.confidence >= 0.8)
            .group_by(ParsedEvent.kind)
        ).all()
    )
    # The sheet's "No-Picks": buyers who won and then picked nothing (the sale names no
    # item), not auctions that ended without a winner (that is `noWinner`).
    no_picks = (
        db.session.scalar(
            select(func.count())
            .select_from(Sale)
            .where(
                Sale.show_id == show.id,
                Sale.status.in_(STANDING),
                Sale.sku_id.is_(None),
                Sale.item_text == "",
            )
        )
        or 0
    )
    orders = dict(
        db.session.execute(
            select(Order.status, func.count())
            .where(Order.show_id == show.id)
            .group_by(Order.status)
        ).all()
    )
    messages = (
        db.session.scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.show_id == show.id)
        )
        or 0
    )
    open_reviews = (
        db.session.scalar(
            select(func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.show_id == show.id, ReviewItem.status == ReviewStatus.open)
        )
        or 0
    )
    return {
        "showId": show.id,
        "date": show.show_date.isoformat(),
        "startedAt": show.started_at.isoformat(),
        "updatedAt": show.updated_at.isoformat(),
        "live": (datetime.now(UTC) - show.updated_at) < timedelta(minutes=10),
        "messages": messages,
        "itemsSold": int(row.items),
        "buyers": int(row.buyers),
        "red": int(row.red),
        "bundles": int(row.bundle),
        "ultra": int(row.ultra),
        "mega": int(row.mega),
        "quickHit": int(row.quick_hit),
        "gifts": _whatnot_giveaways(show) or int(events.get(EventKind.gift, 0)),
        "noPicks": int(no_picks),
        "noWinner": int(events.get(EventKind.no_pick, 0)),
        "cancelled": int(row.lost),
        "revenuePaidCents": int(row.paid),
        "revenuePendingCents": int(row.pending),
        "orders": {k.value: int(v) for k, v in orders.items()},
        "toFulfil": int(
            sum(
                v
                for k, v in orders.items()
                if k in (OrderStatus.paid, OrderStatus.picking, OrderStatus.packed)
            )
        ),
        "openReviews": int(open_reviews),
    }


# Why a winner has nothing to pack, in the order the team cares about.
WALKAWAY = {
    "no_item": "Won, nothing picked",
    "cancelled": "Cancelled",
    "payment_failed": "Payment failed",
    "unpaid": "Not in the payment file",
    "unconfirmed": "Win not confirmed (in Review)",
}


def _whatnot_giveaways(show: Show) -> int:
    """Giveaways Whatnot's own orders list for the show (0 until they are read). The chat names
    a giveaway winner only when the host or Whatnot types it; on 2026-11-13 nobody did (0 here
    against Whatnot's 8). Like the order slips, Whatnot's count replaces the chat's when it
    has one."""
    from ..models import WhatnotOrder, WhatnotShow
    from .whatnot.orders import GIVEAWAY

    if not show.whatnot_show_id:
        return 0
    return int(
        db.session.scalar(
            select(func.count())
            .select_from(WhatnotOrder)
            .join(
                WhatnotShow,
                (WhatnotShow.organization_id == WhatnotOrder.organization_id)
                & (WhatnotShow.whatnot_id == WhatnotOrder.whatnot_show_id),
            )
            .where(
                WhatnotShow.id == show.whatnot_show_id,
                WhatnotOrder.transaction_type == GIVEAWAY,
                WhatnotOrder.status.not_in(("cancelled", "failed")),
            )
        )
        or 0
    )


def winners_without_purchase(show: Show) -> list[dict]:
    """Every buyer who won something in the show and has nothing to pack for it: a win with
    no item picked, a cancelled or failed sale, a sale the payment file did not cover, or a
    win the rules could not trust (a viewer-shaped line waiting in Review). One row per
    sale (or per unconfirmed line), chat line number included so it can be checked."""
    unpaid = set(
        db.session.scalars(
            select(ReviewItem.sale_id).where(
                ReviewItem.show_id == show.id,
                ReviewItem.reason == "no_payment_record",
                ReviewItem.status == ReviewStatus.open,
            )
        )
    )
    rows: list[dict] = []
    sales = db.session.scalars(select(Sale).where(Sale.show_id == show.id).order_by(Sale.id)).all()
    for s in sales:
        if s.status in LOST:
            outcome = s.status.value
        elif s.sku_id is None and not s.item_text and s.status in STANDING:
            outcome = "no_item"  # also absent from the payment file, but this says why
        elif s.id in unpaid:
            outcome = "unpaid"
        else:
            continue
        rows.append(
            {
                "buyer": s.buyer_username,
                "outcome": outcome,
                "label": WALKAWAY[outcome],
                "item": s.sku.product.name if s.sku else s.item_text,
                "quantity": s.quantity,
                "seq": s.event.message.seq if s.event else None,
                "saleId": s.id,
                "note": s.status_reason or "",
            }
        )
    confirmed = select(Sale.event_id).where(Sale.show_id == show.id, Sale.event_id.is_not(None))
    pending = db.session.scalars(
        select(ParsedEvent)
        .where(
            ParsedEvent.show_id == show.id,
            ParsedEvent.kind == EventKind.sale,
            ParsedEvent.confidence < 0.8,
            ParsedEvent.id.not_in(confirmed),
        )
        .order_by(ParsedEvent.id)
    ).all()
    # A chat line about a win, for a buyer the video player already recorded winning, is the
    # same win told twice (it waits in Review as a possible duplicate), not a walk-away.
    player_buyers = {
        s.buyer_username.lower()
        for s in sales
        if s.event is not None and s.event.rule == "player_win" and s.status in STANDING
    }
    for ev in pending:
        if ev.message.source == "chat" and ev.buyer_username.lower() in player_buyers:
            continue
        rows.append(
            {
                "buyer": ev.buyer_username,
                "outcome": "unconfirmed",
                "label": WALKAWAY["unconfirmed"],
                "item": ev.item_text,
                "quantity": ev.quantity,
                "seq": ev.message.seq,
                "saleId": None,
                "note": ev.message.raw_text,
            }
        )
    order = list(WALKAWAY)
    rows.sort(key=lambda r: (order.index(r["outcome"]), r["seq"] or 0))
    return rows


def top_buyers(org_id: int, *, show_id: int | None = None, limit: int = 10) -> list[dict]:
    q = (
        select(
            Sale.buyer_username,
            func.sum(Sale.quantity),
            _rev(PAID),
            func.count(func.distinct(Sale.show_id)),
        )
        .where(Sale.organization_id == org_id, Sale.status.in_(STANDING))
        .group_by(Sale.buyer_username)
        .order_by(func.sum(Sale.quantity).desc(), Sale.buyer_username)
        .limit(limit)
    )
    if show_id:
        q = q.where(Sale.show_id == show_id)
    return [
        {"buyer": b, "items": int(n), "paidCents": int(r), "shows": int(s)}
        for b, n, r, s in db.session.execute(q).all()
    ]


def held_since(org_id: int, name_col, date_col):
    """True for a row that belongs to whoever holds its buyer name now. A name that came
    to its holder from a different Whatnot account (`BuyerName.since_date`) leaves what
    was won under it before that show date out: those wins were the earlier account's."""
    return ~(
        select(BuyerName.id)
        .where(
            BuyerName.organization_id == org_id,
            BuyerName.name_lower == func.lower(name_col),
            BuyerName.since_date.is_not(None),
            date_col < BuyerName.since_date,
        )
        .exists()
    )


def buyer_figures(
    org_id: int, names: list[str] | None = None, *, before: date | None = None
) -> dict[str, dict]:
    """What each buyer name won, paid for and lost, keyed by the name in lower case (a
    buyer who changed their name is two keys; `services/buyers.py` adds them up).

    Items are what still stands (as on the dashboard), spend is what was paid, and a
    "show" is a show date: two recordings of one evening count once. `before` keeps only
    shows dated earlier than it ("had they bought before tonight?")."""
    name = func.lower(Sale.buyer_username)
    standing = Sale.status.in_(STANDING)
    q = (
        select(
            name,
            func.max(Sale.buyer_username),
            _qty(STANDING),
            _rev(PAID),
            func.array_agg(func.distinct(Show.show_date)).filter(standing),
            func.count().filter(Sale.status == SaleStatus.cancelled),
            func.count().filter(Sale.status == SaleStatus.payment_failed),
        )
        .join(Show, Show.id == Sale.show_id)
        .where(
            Sale.organization_id == org_id,
            Sale.buyer_username != "",
            held_since(org_id, Sale.buyer_username, Show.show_date),
        )
        .group_by(name)
    )
    if names is not None:
        q = q.where(name.in_(names))
    if before is not None:
        q = q.where(Show.show_date < before)
    return {
        key: {
            "username": written,
            "items": int(items),
            "paidCents": int(paid),
            "dates": set(dates or ()),
            "cancelled": int(cancelled),
            "failed": int(failed),
        }
        for key, written, items, paid, dates, cancelled, failed in db.session.execute(q).all()
    }


def buyer_gifts(org_id: int, names: list[str]) -> int:
    """Giveaways these names won. Only what the host or the platform said, like the
    dashboard's tile: a viewer typing "won the giveaway!" is not a win."""
    if not names:
        return 0
    return int(
        db.session.scalar(
            select(func.count())
            .select_from(ParsedEvent)
            # By show, which the (show, kind) index covers; never the whole table.
            .join(Show, Show.id == ParsedEvent.show_id)
            .where(
                Show.organization_id == org_id,
                ParsedEvent.kind == EventKind.gift,
                ParsedEvent.confidence >= 0.8,
                func.lower(ParsedEvent.buyer_username).in_(names),
                held_since(org_id, ParsedEvent.buyer_username, Show.show_date),
            )
        )
        or 0
    )


def top_skus(org_id: int, *, show_id: int | None = None, limit: int = 10) -> list[dict]:
    q = (
        select(Sku, Product.name, func.sum(Sale.quantity), _rev(PAID))
        .join(Sale, Sale.sku_id == Sku.id)
        .join(Product, Product.id == Sku.product_id)
        .where(Sale.organization_id == org_id, Sale.status.in_(STANDING))
        .group_by(Sku.id, Product.name)
        .order_by(func.sum(Sale.quantity).desc(), Sku.code)
        .limit(limit)
    )
    if show_id:
        q = q.where(Sale.show_id == show_id)
    out = []
    for sku, pname, n, r in db.session.execute(q).all():
        sold = int(n)
        left = max(0, sku.available)  # an oversold SKU is 100% sold through, not >100%
        out.append(
            {
                "skuId": sku.id,
                "code": sku.code,
                "product": pname,
                "variant": sku.name,
                "sold": sold,
                "paidCents": int(r),
                "available": sku.available,
                "short": sku.short,
                "sellThrough": round(sold / (sold + left), 3) if sold + left else None,
            }
        )
    return out


def shows_table(
    org_id: int, *, limit: int = 30, since: date | None = None, until: date | None = None
) -> list[dict]:
    q = select(Show).where(Show.organization_id == org_id)
    if since:
        q = q.where(Show.show_date >= since)
    if until:
        q = q.where(Show.show_date <= until)
    shows = db.session.scalars(q.order_by(Show.started_at.desc()).limit(limit)).all()
    return [show_tiles(s) for s in shows]


def totals(rows: list[dict]) -> dict:
    keys = (
        "messages",
        "itemsSold",
        "buyers",
        "red",
        "bundles",
        "ultra",
        "mega",
        "quickHit",
        "gifts",
        "noPicks",
        "noWinner",
        "cancelled",
        "revenuePaidCents",
        "revenuePendingCents",
        "toFulfil",
        "openReviews",
    )
    return {k: sum(r[k] for r in rows) for k in keys} | {"shows": len(rows)}


FULFIL = (OrderStatus.paid, OrderStatus.picking, OrderStatus.packed)


def overview(org_id: int) -> dict:
    rows = shows_table(org_id)
    low = _low_stock(org_id)
    # Shows with money still on the table: older sales still reserved, no payment file applied.
    unreconciled = [
        {
            "showId": r["showId"],
            "date": r["date"],
            "pendingCents": r["revenuePendingCents"],
            "openSales": r["orders"].get("open", 0),
        }
        for r in rows
        if r["revenuePendingCents"] > 0 and not r["live"]
    ]
    live = next((r for r in rows if r["live"]), None)
    if live is not None:
        live = {**live, "pace": chat_pace(live["showId"])}
    fulfil_items, fulfil_oldest = fulfil_summary(org_id)
    return {
        "shows": rows,
        "totals": totals(rows),
        "topBuyers": top_buyers(org_id),
        "topSkus": top_skus(org_id),
        "lowStock": low,
        "attention": {
            "openReviews": sum(r["openReviews"] for r in rows),
            "reviewReasons": review_reasons(org_id),
            "toFulfil": sum(r["toFulfil"] for r in rows),
            "fulfilItems": fulfil_items,
            "fulfilOldestShow": fulfil_oldest,
            "lowStock": len(low),
            "oversold": [s for s in low if s["short"]],
            "unreconciledShows": unreconciled,
            "live": live,
        },
    }


def counts(org_id: int) -> dict:
    """Cheap numbers for the sidebar badges (every page loads them)."""
    reviews = db.session.scalar(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.organization_id == org_id, ReviewItem.status == ReviewStatus.open)
    )
    to_fulfil = db.session.scalar(
        select(func.count())
        .select_from(Order)
        .where(Order.organization_id == org_id, Order.status.in_(FULFIL))
    )
    cutoff = datetime.now(UTC) - LIVE_WINDOW
    live = db.session.scalar(
        select(func.count())
        .select_from(Show)
        .where(Show.organization_id == org_id, Show.updated_at >= cutoff)
    )
    # The Whatnot connection stopped working (a rejected token, a missing permission):
    # every page says so, because sales and shipments then stop arriving on their own.
    whatnot_problem = db.session.scalar(
        select(func.count())
        .select_from(WhatnotConnection)
        .where(
            WhatnotConnection.organization_id == org_id,
            WhatnotConnection.disconnected_at.is_(None),
            WhatnotConnection.problem.in_(("token_rejected", "no_permission")),
        )
    )
    return {
        "openReviews": reviews or 0,
        "toFulfil": to_fulfil or 0,
        "live": bool(live),
        "whatnotProblem": bool(whatnot_problem),
    }


def review_reasons(org_id: int) -> dict[str, int]:
    rows = db.session.execute(
        select(ReviewItem.reason, func.count())
        .where(ReviewItem.organization_id == org_id, ReviewItem.status == ReviewStatus.open)
        .group_by(ReviewItem.reason)
    ).all()
    return {reason: int(n) for reason, n in rows}


def fulfil_summary(org_id: int) -> tuple[int, str | None]:
    """Items in paid-but-unshipped orders, and the date of the oldest show they come from."""
    items, oldest = db.session.execute(
        select(func.coalesce(func.sum(Sale.quantity), 0), func.min(Show.show_date))
        .join(Order, Order.id == Sale.order_id)
        .join(Show, Show.id == Sale.show_id)
        .where(Order.organization_id == org_id, Order.status.in_(FULFIL), Sale.status.in_(PAID))
    ).one()
    return int(items), oldest.isoformat() if oldest else None


def chat_pace(show_id: int, minutes: int = 30) -> list[int]:
    """Messages per minute over the last `minutes` of the show's chat, oldest first."""
    last = db.session.scalar(
        select(func.max(ChatMessage.captured_at)).where(ChatMessage.show_id == show_id)
    )
    buckets = [0] * minutes
    if last is None:
        return buckets
    start = last - timedelta(minutes=minutes)
    stamps = db.session.scalars(
        select(ChatMessage.captured_at).where(
            ChatMessage.show_id == show_id, ChatMessage.captured_at > start
        )
    ).all()
    for ts in stamps:
        i = min(minutes - 1, int((ts - start).total_seconds() // 60))
        buckets[i] += 1
    return buckets


def _low_stock(org_id: int) -> list[dict]:
    """SKUs at or under their low line, most short first (oversold SKUs have available < 0)."""
    skus = db.session.scalars(
        select(Sku)
        .where(
            Sku.organization_id == org_id,
            Sku.active.is_(True),
            Sku.on_hand - Sku.reserved <= Sku.low_stock_at,
        )
        .order_by(Sku.on_hand - Sku.reserved, Sku.code)
        .limit(20)
    ).all()
    sold = dict(
        db.session.execute(
            select(Sale.sku_id, func.sum(Sale.quantity))
            .where(Sale.sku_id.in_([s.id for s in skus] or [0]), Sale.status.in_(STANDING))
            .group_by(Sale.sku_id)
        ).all()
    )
    return [
        {
            "skuId": s.id,
            "code": s.code,
            "product": s.product.name,
            "available": s.available,
            "short": s.short,
            "lowAt": s.low_stock_at,
            "sold": int(sold.get(s.id, 0)),
        }
        for s in skus
    ]


# ---------- Whatnot's own numbers (services/whatnot/results.py reads them) ----------


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


def whatnot_show(show: Show) -> dict | None:
    """What Whatnot itself says about the show: its results (viewers, reach, buyers, sales)
    and its ledger (earnings, fees, refunds, tips). None when the show is not matched to a
    show on Whatnot. Shown as Whatnot states them; nothing here is worked out by the app
    except sums of Whatnot's own lines."""
    from ..models import WhatnotShow
    from .whatnot import connection, results

    ws = db.session.get(WhatnotShow, show.whatnot_show_id) if show.whatnot_show_id else None
    if ws is None:
        return None
    state = connection.sync_state(show.organization_id, "analytics")
    return {
        "title": ws.title,
        # The access token may not read reports: the page says so instead of "not yet".
        "resultsNotAllowed": (state.last_error or "").startswith("Forbidden"),
        "resultsStatus": ws.analytics_status,
        "resultsAt": ws.analytics_at.isoformat() if ws.analytics_at else None,
        "results": {_camel(k): v for k, v in (ws.analytics or {}).items()} or None,
        "ledger": results.figures(show.organization_id, whatnot_show_id=ws.whatnot_id),
    }


def whatnot_ledger(org_id: int, *, days: int = 30, shows: int = 12) -> dict:
    """Reports → Whatnot's ledger: the last `days` days as Whatnot states them, and the
    net of each recent show."""
    from ..models import WhatnotLedgerEntry, WhatnotShow
    from .whatnot import results

    since = datetime.now(UTC) - timedelta(days=days)
    start = func.coalesce(WhatnotShow.started_at, WhatnotShow.scheduled_start_at)
    rows = db.session.execute(
        select(WhatnotShow.id, WhatnotShow.whatnot_id, WhatnotShow.title, start)
        .where(
            WhatnotShow.organization_id == org_id,
            select(WhatnotLedgerEntry.id)
            .where(
                WhatnotLedgerEntry.organization_id == org_id,
                WhatnotLedgerEntry.whatnot_show_id == WhatnotShow.whatnot_id,
            )
            .exists(),
        )
        .order_by(start.desc().nulls_last(), WhatnotShow.id.desc())
        .limit(shows)
    ).all()
    # Each show's figures exactly as its own page shows them (results.figures), so the
    # list and the page can never disagree.
    nets = {ws_id: results.figures(org_id, whatnot_show_id=wid) for ws_id, wid, _, _ in rows}
    # The app's own page for each (its first recording), when there is one.
    local = dict(
        db.session.execute(
            select(Show.whatnot_show_id, func.min(Show.id))
            .where(Show.organization_id == org_id, Show.whatnot_show_id.in_([r[0] for r in rows]))
            .group_by(Show.whatnot_show_id)
        ).all()
    )
    return {
        **results.status(org_id),
        "days": days,
        "recent": results.figures(org_id, since=since),
        "shows": [
            {
                "showId": local.get(ws_id),
                "title": title,
                "startedAt": began.isoformat() if began else None,
                "netCents": nets[ws_id]["netCents"],
                "lines": sum(k["lines"] for k in nets[ws_id]["kinds"]),
            }
            for ws_id, _, title, began in rows
            if nets[ws_id] is not None
        ],
    }
