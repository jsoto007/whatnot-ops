"""Keep the app's copy of Whatnot's shows and orders current.

Three ways news arrives, all ending in the same upserts:
  * a webhook names an order or a show, and the job reads it again from the API;
  * when a show ends, every order placed since it began is read (`sync_show_orders`):
    the catch-up that makes a missed webhook harmless;
  * a timer (the worker's sweep) reads the show list, re-reads orders Whatnot has not
    settled yet, and polls a live show's orders when no webhook has come for a while.

Reading never changes a sale, an order of the app, a payment or stock. What the copy is
then allowed to do is decided in orders.py (compare, or apply).

List endpoints have no filter by show or date: "this show's orders" means paging back,
newest first, until the orders are older than the show.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert

from ...db import db
from ...models import (
    ChatMessage,
    Show,
    WhatnotConnection,
    WhatnotEvent,
    WhatnotOrder,
    WhatnotOrderItem,
    WhatnotShow,
    WhatnotSyncState,
)
from .. import jobs, text
from ..showdate import show_date
from . import connection, webhooks
from .client import (
    MAX_CENTS,
    Forbidden,
    Invalid,
    NotFound,
    Unauthorized,
    WhatnotClient,
    WhatnotError,
    cents,
    client_for,
    price_cents,
    when,
    worked,
)

log = logging.getLogger(__name__)

ACTIVE = ("live", "stopped")
OPEN_ORDER_STATUSES = ("pending", "pending_fulfillment")
# How far before a show's start an order can still be its own (clock skew, a show that
# went live before its scheduled time).
SHOW_LEAD = timedelta(hours=6)
# A recording is of a show when it was running while the show was live. Before the show's
# known start a laptop is often already recording: half an hour when we know the moment
# it went live, two hours when all we have is the time it was scheduled for.
RECORDING_LEAD = timedelta(minutes=30)
RECORDING_LEAD_SCHEDULED = timedelta(hours=2)
RECENT_SHOWS = timedelta(days=30)  # ended shows older than this are not re-read (the dashboard's
# money is the last 30 days: every show in it must be read)
OPEN_ORDER_AGE = timedelta(days=14)
# A show that ended this lately gets its parcels read in full by the timer (the first
# connection reads weeks of ended shows: only the latest need their Shipping page ready).
PARCELS_AFTER = timedelta(days=3)
# A parcel read that did not work is asked for again this much later, not on every pass:
# each failed read tells the whole app the connection stopped working.
PARCELS_RETRY = timedelta(minutes=15)
MAX_ORDER_PAGES = 60  # 3,000 orders in one catch-up; a show has a few hundred
# One catch-up read stores at most this many orders new to the app (each costs a call for its
# items). A catch-up that reaches it ends there, and the shows it did not get down to are read
# again by the next pass, which carries on past the orders the app already holds
# (`sync_orders_of`).
MAX_NEW_ORDERS = MAX_ORDER_PAGES * 50
# While a show is on air a pass may store only this many new orders: a catch-up's item calls
# must not eat the request budget the live show's orders need. It carries on after the show.
MAX_NEW_ORDERS_ON_AIR = 300
MAX_LISTED_PAGES = 400  # the most pages (50 orders each) one read walks, held or not
# Orders the app holds with no items read again after a pass, at most (`repair_items`).
MAX_ITEM_REPAIRS = 20
# A catch-up that walked its page limit and stored nothing new is not tried again for the
# older shows it did not reach for this long (it would walk the same orders again).
STALLED_RETRY = timedelta(hours=6)
# Orders Whatnot refuses one after another in the open-order re-read: it is not about those
# orders (a token that lost its permission), so the pass ends instead of asking for the rest.
REFUSED_IN_A_ROW = 5
# An ended show's orders are listed again this long after it ended, then once more each
# later time: a card declined at the win and retried is a NEW order, placed after the show
# ended, and the first read after the end can come before it.
LATE_READS = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
LATE_READS_WITHIN = timedelta(days=2)  # a show older than this has had its late reads
# A show Whatnot's own results count more orders for than the app holds is read again (at
# most this often): the repair for a catch-up that once marked shows read that it never
# reached, and for any order the reads missed.
SHORT_REREAD_AFTER = timedelta(hours=6)
MAX_OPEN_REFRESH = 150  # orders re-read one by one per timer run
SHOWS_EVERY = timedelta(minutes=15)
# A timed read that failed is due again this long after (`_run`), not a whole interval later.
RETRY_AFTER_FAILURE = timedelta(minutes=2)
OPEN_ORDERS_EVERY = timedelta(minutes=60)
# With webhooks arriving (one within this long), the open-order re-read is a safety net
# and runs this often instead.
OPEN_ORDERS_HOOKED = timedelta(hours=6)
# A show "live" or "stopped" on Whatnot for longer than this is not on air any more for the
# open-order re-read (a show left "stopped" must not hold it back for days).
ON_AIR_AT_MOST = timedelta(hours=12)
LIVE_POLL_QUIET = timedelta(minutes=10)  # no webhook for this long during a live show
# A show nobody recorded gets its own entry in Shows once it has been live this long (a
# laptop that starts recording a little late has had its chance to appear).
UNRECORDED_GRACE = timedelta(minutes=10)


def _str(value, limit: int) -> str | None:
    """An id or code from the API as a short clean string, or None."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = (text.clean(value) or "").strip()
    return value[:limit] or None


# ---------- shows ----------


def upsert_show(org_id: int, data: dict) -> WhatnotShow | None:
    whatnot_id = _str(data.get("id"), 40)
    if whatnot_id is None:
        return None
    row = db.session.scalar(
        select(WhatnotShow)
        .where(WhatnotShow.organization_id == org_id, WhatnotShow.whatnot_id == whatnot_id)
        .with_for_update()
    )
    if row is None:
        db.session.execute(
            insert(WhatnotShow)
            .values(organization_id=org_id, whatnot_id=whatnot_id)
            .on_conflict_do_nothing(constraint="uq_whatnot_shows_org_id")
        )
        row = db.session.scalar(
            select(WhatnotShow)
            .where(WhatnotShow.organization_id == org_id, WhatnotShow.whatnot_id == whatnot_id)
            .with_for_update()
        )
    status = _str(data.get("status"), 16)
    if status:
        row.status = status
    if isinstance(data.get("title"), str):
        row.title = (text.clean(data["title"]) or "").strip()[:300]
    row.scheduled_start_at = when(data.get("scheduled_start_at")) or row.scheduled_start_at
    row.ended_at = when(data.get("ended_at")) or row.ended_at
    row.url = text.whatnot_url(data.get("url") if isinstance(data.get("url"), str) else None)
    if row.url and len(row.url) > 512:
        row.url = None
    return row


def sync_show(org_id: int, client: WhatnotClient, whatnot_id: str) -> WhatnotShow | None:
    try:
        return upsert_show(org_id, client.show(whatnot_id))
    except NotFound:
        return None


def sync_shows(org_id: int, client: WhatnotClient) -> int:
    """Read the show list: everything live or scheduled, and the recently ended."""
    n = 0
    cutoff = datetime.now(UTC) - RECENT_SHOWS
    for status in ("live", "stopped", "scheduled", "ended", "cancelled"):
        for i, data in enumerate(client.shows(status)):
            row = upsert_show(org_id, data)
            n += row is not None
            old = row is not None and (row.scheduled_start_at or row.ended_at or cutoff) < cutoff
            if status in ("ended", "cancelled") and (old or i >= 40):
                break  # newest first: the rest is older still
            if i >= 200:
                break
    db.session.flush()
    # A show the list no longer calls live has ended, even if its webhook never came.
    link_recordings(org_id)
    return n


def _recording_window(show: Show) -> tuple[datetime, datetime]:
    last = db.session.scalar(
        select(func.max(ChatMessage.captured_at)).where(ChatMessage.show_id == show.id)
    )
    return show.started_at, max(last or show.started_at, show.started_at)


def _overlap(a_start, a_end, b_start, b_end) -> float:
    return (min(a_end, b_end) - max(a_start, b_start)).total_seconds()


_STREAM = re.compile(r"/live/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})", re.I)


def _stream_of(url: str | None) -> str | None:
    """The live stream a recording's address names (whatnot.com/live/<id>, or the seller's
    /dashboard/live/<id>), when it names one. On the live shop that id is the show's own."""
    found = _STREAM.search(url or "")
    return found.group(1).lower() if found else None


def tie(show: Show, whatnot_show_id: int | None, link: str | None) -> None:
    """Point a recording at a Whatnot show (or at none) and leave `Show.updated_at` alone.
    That column is when chat last arrived: the Live pill and the show's length are read
    from it, and an ordinary write stamps "now" on it (a days-old show then read as live).
    The statement names the column's own value, so a chat batch saved in the meantime
    keeps its newer time."""
    db.session.execute(
        update(Show)
        .where(Show.id == show.id)
        .values(whatnot_show_id=whatnot_show_id, whatnot_link=link, updated_at=Show.updated_at)
        .execution_options(synchronize_session=False)
    )
    db.session.expire(show, ["whatnot_show_id", "whatnot_link", "updated_at"])


def link_recordings(org_id: int) -> int:
    """Point each recent recording at the Whatnot show it is a recording of: the show its
    address names, else the show that was live while it was recording. A recording made on
    ANOTHER stream's page (someone watching a different seller) is never tied by the clock
    to a show that has a recording of its own. A link a person chose is never changed."""
    since = datetime.now(UTC) - RECENT_SHOWS
    recordings = db.session.scalars(
        select(Show).where(
            Show.organization_id == org_id,
            Show.started_at >= since,
            Show.whatnot_link.is_distinct_from("manual"),
            Show.whatnot_link.is_distinct_from("made"),
        )
    ).all()
    if not recordings:
        return 0
    candidates = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status.in_(ACTIVE + ("ended",)),
        )
    ).all()
    # The shows some recording was made on: its address names that show. Looked for over
    # twice the window, so a show's own recording does not age out before the others do.
    addresses = [
        url.lower()
        for url in db.session.scalars(
            select(Show.source_url).where(
                Show.organization_id == org_id,
                Show.started_at >= since - RECENT_SHOWS,
                Show.source_url.is_not(None),
            )
        )
    ]
    recorded = {
        ws.id for ws in candidates if any(ws.whatnot_id.lower() in url for url in addresses)
    }
    now = datetime.now(UTC)
    linked = 0
    sure: set[int] = set()  # Whatnot shows a recording is tied to by its own address
    for rec in recordings:
        # Its address names a stream: only that stream's show, or one nobody recorded.
        elsewhere = _stream_of(rec.source_url) is not None
        rec_start, rec_end = _recording_window(rec)
        best: tuple[float, WhatnotShow] | None = None
        address = (rec.source_url or "").lower()
        for ws in candidates:
            if address and ws.whatnot_id.lower() in address:
                best = (float("inf"), ws)
                break
            if elsewhere and ws.id in recorded:
                continue  # this show has its own recording, and this is not it
            # The earliest the show can have been live. `started_at` may only be its first
            # order (the show began at or before it); the scheduled time is a rough guess.
            began = ws.begins_at
            if began is None:
                continue
            exact = ws.started_at is not None and began == ws.started_at
            start = began - (RECORDING_LEAD if exact else RECORDING_LEAD_SCHEDULED)
            seconds = _overlap(rec_start, rec_end + timedelta(minutes=1), start, ws.ended_at or now)
            if seconds > 0 and (best is None or seconds > best[0]):
                best = (seconds, ws)
        target = best[1].id if best else None
        if target is not None and best[0] == float("inf"):
            sure.add(target)  # new or not: the address may have arrived after the tie
        if target is not None and rec.whatnot_show_id != target:
            tie(rec, target, "auto")
            linked += 1
        elif (
            target is None
            and rec.whatnot_link == "auto"
            and elsewhere
            and rec.whatnot_show_id in recorded
        ):
            # Tied by the clock earlier, before the show's own recording was known.
            tie(rec, None, None)
    made = (
        set(
            db.session.scalars(
                select(Show.whatnot_show_id).where(
                    Show.organization_id == org_id,
                    Show.whatnot_link == "made",
                    Show.whatnot_show_id.in_(sure),
                )
            )
        )
        if sure
        else set()
    )
    if made:
        # One show here per Whatnot show: a show made for it before this laptop started
        # recording folds into the recording (services/show_merge.py). Tried at every read
        # while both are there: the recording may have learnt its address after it was
        # tied, the made show may have been made just after, or a parse was running.
        from .. import show_merge

        for ws in db.session.scalars(select(WhatnotShow).where(WhatnotShow.id.in_(made))):
            show_merge.fold_made(ws)
    return linked


def by_address(show: Show, ws: WhatnotShow) -> bool:
    """The recording's own page address names the Whatnot show."""
    return bool(show.source_url) and ws.whatnot_id.lower() in show.source_url.lower()


def is_sure(show: Show, ws: WhatnotShow) -> bool:
    """The recording is this Whatnot show's for good: a person said so, or its own page
    address names the show. A tie by the clock alone may be re-decided at the next read."""
    return show.whatnot_link == "manual" or by_address(show, ws)


def _rank(whatnot_id):
    """How sure a show is to stay this Whatnot show's (`is_sure` in SQL): 0 a recording a
    person matched or whose address names the show, 1 a show made from Whatnot's record,
    2 a recording tied by the clock alone."""
    named = func.strpos(func.lower(func.coalesce(Show.source_url, "")), func.lower(whatnot_id))
    return case(
        (Show.whatnot_link == "manual", 0),
        (named > 0, 0),
        (Show.whatnot_link == "made", 1),
        else_=2,
    )


def recordings_of(ws: WhatnotShow) -> list[Show]:
    """The app's shows that are this Whatnot show. Normally one (services/show_merge.py);
    while there are more, the first is where new order sales, Whatnot's lots and its
    giveaways go: a recording that is the show's for good (`is_sure`), else the show made
    from Whatnot's record, else a recording tied by the clock; then the oldest. Never into
    a clock tie while the made show is there: the clock may be re-decided, and the sales
    would leave the show with it. Never "the one holding most sales": that changed as
    sales arrived, and a buyer's wins, lots and giveaways then landed in both."""
    return list(
        db.session.scalars(
            select(Show)
            .where(Show.organization_id == ws.organization_id, Show.whatnot_show_id == ws.id)
            .order_by(_rank(ws.whatnot_id), Show.started_at, Show.id)
        )
    )


def first_recordings(org_id: int, ws_ids) -> dict[int, int]:
    """Whatnot show id → the first of its app shows (as `recordings_of` orders them), for
    many Whatnot shows in one query."""
    out: dict[int, int] = {}
    if not ws_ids:
        return out
    for show_id, ws_id in db.session.execute(
        select(Show.id, Show.whatnot_show_id)
        .join(WhatnotShow, WhatnotShow.id == Show.whatnot_show_id)
        .where(Show.organization_id == org_id, Show.whatnot_show_id.in_(set(ws_ids)))
        .order_by(Show.whatnot_show_id, _rank(WhatnotShow.whatnot_id), Show.started_at, Show.id)
    ):
        out.setdefault(ws_id, show_id)
    return out


def ensure_recording(ws: WhatnotShow) -> Show | None:
    """The app's show for a Whatnot show: its recording, or (when nobody recorded it and it
    has been live long enough that a late laptop would have appeared) one made for it, so
    its orders have somewhere to live. None while it is too early to tell."""
    existing = recordings_of(ws)
    if existing:
        return existing[0]
    link_recordings(ws.organization_id)
    existing = recordings_of(ws)
    if existing:
        return existing[0]
    start = ws.begins_at
    if start is None or ws.status not in ACTIVE + ("ended",):
        return None
    live_since = ws.started_at or start
    if ws.status != "ended" and datetime.now(UTC) - live_since < UNRECORDED_GRACE:
        return None
    key = f"wn:{ws.whatnot_id}"[:64]
    db.session.execute(
        insert(Show)
        .values(
            organization_id=ws.organization_id,
            show_key=key,
            started_at=ws.started_at or start,
            show_date=show_date(ws.started_at or start),
            whatnot_show_id=ws.id,
            whatnot_link="made",
        )
        .on_conflict_do_nothing(constraint="uq_shows_org_key")
    )
    return db.session.scalar(
        select(Show).where(Show.organization_id == ws.organization_id, Show.show_key == key)
    )


# ---------- orders ----------


def _order_values(org_id: int, data: dict) -> dict | None:
    whatnot_id = _str(data.get("id"), 20)
    status = _str(data.get("status"), 24)
    if whatnot_id is None or status is None:
        return None
    buyer = data.get("buyer") if isinstance(data.get("buyer"), dict) else {}
    amounts = data.get("amounts") if isinstance(data.get("amounts"), dict) else {}
    discount = amounts.get("applied_discount")
    total = amounts.get("total") if isinstance(amounts.get("total"), dict) else {}
    return {
        "organization_id": org_id,
        "whatnot_id": whatnot_id,
        "whatnot_show_id": _str(data.get("show_id"), 40),
        "status": status,
        "transaction_type": _str(data.get("transaction_type"), 16),
        "buyer_id": _str(buyer.get("id"), 32),
        "buyer_username": _str(buyer.get("username"), 80),
        "subtotal_cents": cents(amounts.get("subtotal")),
        "shipping_cents": cents(amounts.get("shipping")),
        "tax_cents": cents(amounts.get("tax")),
        "discount_cents": cents(discount.get("amount")) if isinstance(discount, dict) else None,
        "total_cents": cents(total),
        "currency": _str(total.get("currency"), 3),
        "ordered_at": when(data.get("created_at")),
        "whatnot_updated_at": when(data.get("updated_at")),
        "cancelled_at": when(data.get("cancelled_at")),
        "synced_at": datetime.now(UTC),
    }


def upsert_order(org_id: int, data: dict) -> tuple[int | None, bool]:
    """Store what Whatnot says about an order. Returns (row id, whether it is new here).
    An answer older than what is stored is ignored: two reads can finish out of order."""
    values = _order_values(org_id, data)
    if values is None:
        return None, False
    update = {k: v for k, v in values.items() if k not in ("organization_id", "whatnot_id")}
    if values["buyer_username"] is None:
        # A read without the buyer (a token that lost that permission) keeps who it was.
        update.pop("buyer_username")
        update.pop("buyer_id")
    stmt = insert(WhatnotOrder).values(**values)
    newer = stmt.excluded.whatnot_updated_at
    got = db.session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_whatnot_orders_org_id",
            set_=update,
            where=(
                WhatnotOrder.whatnot_updated_at.is_(None)
                | newer.is_(None)
                | (newer >= WhatnotOrder.whatnot_updated_at)
            ),
        ).returning(WhatnotOrder.id, literal_column("(xmax = 0)").label("inserted"))
    ).first()
    # New is what THIS statement did (an insert), not what a select before it saw: two
    # readers (a webhook and the timer) storing one new order at once would both think it new,
    # and the loser would take the winner's committed order out again if its items failed.
    inserted = bool(got is not None and got.inserted)
    row_id = got.id if got is not None else None
    if row_id is None:
        # Ours is not the newer copy: nothing was written; the order is the one held.
        row_id = db.session.scalar(
            select(WhatnotOrder.id).where(
                WhatnotOrder.organization_id == org_id,
                WhatnotOrder.whatnot_id == values["whatnot_id"],
            )
        )
    elif values["status"] in ("cancelled", "failed"):
        # Nothing to pack any more: its lots leave the order slips' pick list.
        from ...models import WhatnotLot

        db.session.execute(
            delete(WhatnotLot).where(
                WhatnotLot.organization_id == org_id,
                (WhatnotLot.whatnot_order_id == values["whatnot_id"])
                | WhatnotLot.whatnot_order_id.like(values["whatnot_id"] + "-%"),
            )
        )
    return row_id, inserted


def upsert_items(org_id: int, order_row_id: int, items: list[dict]) -> int:
    n = 0
    for data in items:
        whatnot_id = _str(data.get("id"), 20)
        if whatnot_id is None:
            continue
        bought = data.get("purchased_item") if isinstance(data.get("purchased_item"), dict) else {}
        quantity = data.get("quantity")
        values = {
            "organization_id": org_id,
            "order_id": order_row_id,
            "whatnot_id": whatnot_id,
            "title": ((text.clean(bought.get("title")) or "") if bought else "").strip()[:240],
            "sku_code": _str(bought.get("sku"), 80),
            "product_id": _str(data.get("product_id"), 20),
            "variant_id": _str(data.get("variant_id"), 40),
            "quantity": quantity
            if isinstance(quantity, int)
            and not isinstance(quantity, bool)
            and 0 < quantity < 10_000
            else 1,
            "unit_price_cents": price_cents(data.get("unit_price")),
            "whatnot_shipment_id": _str(data.get("shipment_id"), 20),
        }
        if (values["unit_price_cents"] or 0) * values["quantity"] > MAX_CENTS:
            # A line total no money column can hold (500 × $100,000): the price is unread,
            # so no sum built from it can fail later.
            values["unit_price_cents"] = None
        update = {
            k: v
            for k, v in values.items()
            if k not in ("organization_id", "whatnot_id", "order_id")
        }
        db.session.execute(
            insert(WhatnotOrderItem)
            .values(**values)
            .on_conflict_do_update(constraint="uq_whatnot_order_items_org_id", set_=update)
        )
        n += 1
    return n


def store_order(org_id: int, client: WhatnotClient, data: dict, *, items: bool | None = None):
    """Upsert an order and, for one new to us (or when asked), its items.
    Returns (row id, new). An order is stored whole or not at all: when its items cannot be
    read, an order that was new here is taken out again, so a later read finds it new and
    reads its items then (a stored order is never read for its items again unless asked)."""
    row_id, new = upsert_order(org_id, data)
    if row_id is None:
        return None, False
    if items if items is not None else new:
        try:
            lines = client.order_items(str(data["id"]))
        except WhatnotError:
            if new:
                db.session.execute(delete(WhatnotOrder).where(WhatnotOrder.id == row_id))
            raise
        upsert_items(org_id, row_id, lines)
    return row_id, new


def sync_order(org_id: int, client: WhatnotClient, whatnot_id: str) -> WhatnotOrder | None:
    """Read one order (and its items) again from Whatnot."""
    try:
        data = client.order(whatnot_id)
    except NotFound:
        return None
    row_id, _ = store_order(org_id, client, data, items=True)
    db.session.flush()
    return db.session.get(WhatnotOrder, row_id) if row_id else None


def sync_show_orders(org_id: int, client: WhatnotClient, ws: WhatnotShow) -> dict:
    """Read every order placed since the show began (newest first, until older than it).
    Orders of other shows met on the way are stored too."""
    return sync_orders_of(org_id, client, [ws])


def sync_orders_of(org_id: int, client: WhatnotClient, shows: list[WhatnotShow]) -> dict:
    """`sync_show_orders` for several shows in ONE newest-first read, down to the oldest
    show's start: reading them one by one would page through the newer shows' orders again
    for each older one.

    A show counts as read in full only when the read got down to its own start. A catch-up
    has a limit (`MAX_NEW_ORDERS` orders new to the app, `MAX_LISTED_PAGES` pages): when it
    ends there, the shows it did not get down to stay unread, and the next pass carries on
    (the orders already held are listed again, cheaply, and the new ones are those below).
    When a call fails part-way, the orders read before it are kept (each is whole, see
    `store_order`) and the shows are not marked read."""
    if not shows:
        return {"seen": 0, "new": 0}
    fallback = datetime.now(UTC) - timedelta(days=2)
    floors = {ws.id: (ws.begins_at - SHOW_LEAD) if ws.begins_at else fallback for ws in shows}
    floor = min(floors.values())
    began = datetime.now(UTC)
    air = on_air_now(org_id)
    limit = MAX_NEW_ORDERS_ON_AIR if air else MAX_NEW_ORDERS
    seen = new = 0
    oldest: datetime | None = None  # the oldest order time met: how far down the read got
    reached = True  # the floor, or the end of Whatnot's list
    try:
        for i, data in enumerate(client.orders()):
            made = when(data.get("created_at"))
            if made is not None and made < floor:
                break
            if i >= MAX_LISTED_PAGES * 50 or new >= limit:
                reached = False
                log.warning(
                    "whatnot order catch-up for show %s stopped at its limit",
                    ", ".join(str(ws.id) for ws in shows),
                )
                break
            _, was_new = store_order(org_id, client, data)
            seen += 1
            new += was_new
            if made is not None and (oldest is None or made < oldest):
                oldest = made
    except WhatnotError:
        # What was read is kept; the shows are not marked read, so the next pass reads them.
        db.session.commit()
        raise
    if not air:
        repair_items(org_id, client, [ws.whatnot_id for ws in shows])
    db.session.flush()
    firsts = dict(
        db.session.execute(
            select(WhatnotOrder.whatnot_show_id, func.min(WhatnotOrder.ordered_at))
            .where(
                WhatnotOrder.organization_id == org_id,
                WhatnotOrder.whatnot_show_id.in_([ws.whatnot_id for ws in shows]),
            )
            .group_by(WhatnotOrder.whatnot_show_id)
        ).all()
    )
    for ws in shows:
        first = firsts.get(ws.whatnot_id)
        if first is not None and (ws.started_at is None or first < ws.started_at):
            ws.started_at = first
        # Down to this show's own start? (A read that ended at its limit is down to the
        # shows it passed, no further: the orders of the older ones are not all here.)
        if not reached and (oldest is None or oldest >= floors[ws.id]):
            continue
        # When the read BEGAN: an order placed while it ran may have been missed, so only a
        # read begun after the show ended counts as its final one.
        ws.orders_synced_at = began
    # A walk that hit its page limit without storing anything new went through the same held
    # orders and will again: the caller spaces its next try (`STALLED_RETRY`).
    return {"seen": seen, "new": new, "complete": reached, "stalled": not reached and new == 0}


def repair_items(org_id: int, client: WhatnotClient, whatnot_show_ids: list[str]) -> int:
    """Read again the items of orders of these shows that the app holds with none (Whatnot can
    answer an order before its items exist, and an order stored is not asked for its items
    again). A few per pass, taken in turn (a cursor on the order's id, so an order that really
    has none costs a call now and then and never keeps the others from being asked), never
    while a show is on air. This is a repair: an order Whatnot refuses or cannot answer is
    left as it is and never stops the read it rides on."""
    if not whatnot_show_ids:
        return 0
    state = connection.sync_state(org_id, "repair:items")  # (":" keeps it off Settings)
    after = int((state.cursor or {}).get("after") or 0)

    def bare(*conditions):
        return db.session.execute(
            select(WhatnotOrder.id, WhatnotOrder.whatnot_id)
            .where(
                WhatnotOrder.organization_id == org_id,
                WhatnotOrder.whatnot_show_id.in_(whatnot_show_ids),
                WhatnotOrder.status.not_in(("cancelled", "failed")),
                ~select(WhatnotOrderItem.id)
                .where(WhatnotOrderItem.order_id == WhatnotOrder.id)
                .exists(),
                *conditions,
            )
            .order_by(WhatnotOrder.id)
            .limit(MAX_ITEM_REPAIRS)
        ).all()

    todo = bare(WhatnotOrder.id > after)
    if len(todo) < MAX_ITEM_REPAIRS:  # the end of the line: start again from the first
        todo += [row for row in bare() if row not in todo][: MAX_ITEM_REPAIRS - len(todo)]
    done = 0
    for row_id, whatnot_id in todo:
        after = row_id
        try:
            upsert_items(org_id, row_id, client.order_items(whatnot_id))
            done += 1
        except (NotFound, Invalid, Forbidden) as e:
            log.warning("whatnot order %s: items not read again: %s", whatnot_id, e)
        except WhatnotError as e:
            log.warning("whatnot items not read again (%s): they wait for the next pass", e)
            break
    state.cursor = {"after": after if len(todo) >= MAX_ITEM_REPAIRS else 0}
    return done


def refresh_open_orders(org_id: int, client: WhatnotClient) -> int:
    """Re-read the orders Whatnot had not settled when we last looked: a status change on
    an old order does not bring it back to the top of the list. An order Whatnot refuses to
    give (a request error about that one order) is skipped and put at the back of the line,
    never the end of the pass; a failure that is not about one order (the token, Whatnot
    being down, five refusals in a row) ends the pass, keeping the orders already re-read."""
    since = datetime.now(UTC) - OPEN_ORDER_AGE
    ids = db.session.scalars(
        select(WhatnotOrder.whatnot_id)
        .where(
            WhatnotOrder.organization_id == org_id,
            WhatnotOrder.status.in_(OPEN_ORDER_STATUSES),
            WhatnotOrder.ordered_at >= since,
        )
        .order_by(WhatnotOrder.synced_at)
        .limit(MAX_OPEN_REFRESH)
    ).all()
    refused = 0  # in a row: five means it is not about one order
    forbidden = 0  # in all
    last: Forbidden | None = None
    for whatnot_id in ids:
        try:
            upsert_order(org_id, client.order(whatnot_id))
            refused = 0
        except NotFound:
            continue
        except (Invalid, Forbidden) as e:
            refused += 1
            if isinstance(e, Forbidden):
                forbidden += 1
                last = e
            if refused >= REFUSED_IN_A_ROW:
                db.session.commit()  # the orders re-read so far are kept
                raise
            log.warning("whatnot order %s could not be re-read: %s", whatnot_id, e)
            db.session.execute(
                update(WhatnotOrder)
                .where(
                    WhatnotOrder.organization_id == org_id, WhatnotOrder.whatnot_id == whatnot_id
                )
                .values(synced_at=datetime.now(UTC))
            )
        except WhatnotError:
            db.session.commit()  # the orders re-read so far are kept
            raise
    if last is not None and forbidden == len(ids):
        # Every order was refused for want of permission (fewer than five of them): the token
        # cannot read orders; that is not a pass that worked. (One order Whatnot finds
        # invalid, alone, is that order's own trouble: it is skipped.)
        raise last
    return len(ids)


# ---------- what a read is followed by ----------


def _after_orders(org_id: int, show_ids: set[str | None], client=None) -> None:
    """Orders of these Whatnot shows changed: a listing they are for that the app has not
    seen is read (one made during the show), the buyer profiles catch up with who placed
    them (soon, once, in a job of their own), and in "auto" mode the orders are applied."""
    from .. import buyers
    from . import catalog

    if client is not None:
        try:
            # A SAVEPOINT: a listing that cannot be stored undoes only itself, never the
            # orders the caller has just read.
            with db.session.begin_nested():
                catalog.learn_listings(org_id, client, show_ids)
        except Exception:  # noqa: BLE001 - a listing not read never stops the orders
            log.exception("whatnot listings of new orders not read for shop %s", org_id)
    buyers.schedule_link(org_id)
    queue_apply(org_id, show_ids)


def queue_apply(org_id: int, show_ids) -> None:
    """In "auto" mode, queue the apply of each of these Whatnot shows' orders (one queued
    job per show). Whoever stores an order the app had not seen calls this: the order is
    applied whether it came from a list read, a webhook or a parcel."""
    conn = connection.active(org_id)
    if conn is None or conn.orders_mode != "auto":
        return
    for whatnot_show_id in show_ids:
        if whatnot_show_id:
            jobs.enqueue(
                "whatnot_apply_show",
                {"org_id": org_id, "whatnot_show_id": whatnot_show_id},
                org_id=org_id,
                dedupe_key=f"whatnot_apply:{org_id}:{whatnot_show_id}",
                delay_s=5,
            )


def _show_row(org_id: int, whatnot_show_id: str | None) -> WhatnotShow | None:
    if not whatnot_show_id:
        return None
    return db.session.scalar(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id, WhatnotShow.whatnot_id == whatnot_show_id
        )
    )


# ---------- webhooks ----------


@webhooks.on("orders.created", "orders.updated")
def _on_order(event: WhatnotEvent) -> None:
    order_id = (event.ref or {}).get("order_id")
    if not order_id:
        return
    org_id = event.organization_id
    client = client_for(org_id)
    order = sync_order(org_id, client, order_id)
    if order is None:
        return
    if order.whatnot_show_id and _show_row(org_id, order.whatnot_show_id) is None:
        ws = sync_show(org_id, client, order.whatnot_show_id)  # a show we had not heard of
        if ws is not None:
            link_recordings(org_id)
    _after_orders(org_id, {order.whatnot_show_id}, client)


@webhooks.on("shows.created", "shows.updated", "shows.started", "shows.ended", "shows.cancelled")
def _on_show(event: WhatnotEvent) -> None:
    show_id = (event.ref or {}).get("show_id")
    if not show_id:
        return
    org_id = event.organization_id
    ws = sync_show(org_id, client_for(org_id), show_id)
    if ws is None:
        return
    if event.topic == "shows.started" and event.event_at is not None:
        # A stopped show that resumes sends this again: keep the first start.
        ws.started_at = min(ws.started_at or event.event_at, event.event_at)
    db.session.flush()
    link_recordings(org_id)
    if event.topic == "shows.ended":
        # Every order of the show, read once more now that no more can come.
        jobs.enqueue(
            "whatnot_sync_show",
            {"org_id": org_id, "whatnot_show_id": ws.whatnot_id},
            org_id=org_id,
            dedupe_key=f"whatnot_sync_show:{org_id}:{ws.whatnot_id}",
            delay_s=30,
        )


# ---------- jobs ----------


def _run(org_id: int, resource: str, fn, *, flag: bool = True, spaced: timedelta | None = None):
    """Run one kind of reading and keep its bookkeeping: when it last worked, or why not.
    `flag=False` is for a reading the shop can live without (a show's results, the fee
    ledger): a missing permission is recorded for its own page and swallowed, instead of
    telling every page that the connection stopped working. `spaced` is for a reading the
    timer spaces out by this interval (`_due` looks at when it STARTED): when it fails it is
    due again `RETRY_AFTER_FAILURE` later, not a whole interval later for a call that never
    worked. (Not at once: the job's own retry, seconds on, must find it spaced so the pass
    goes on to the live show's orders.)"""
    state = connection.sync_state(org_id, resource)
    state.last_started_at = datetime.now(UTC)
    db.session.commit()
    answered = worked()
    try:
        counts = fn()
    except WhatnotError as e:
        db.session.rollback()
        optional = not flag and isinstance(e, Forbidden)
        if not optional:
            connection.note_failure(org_id, e, resource)
        state = connection.sync_state(org_id, resource)
        state.last_error = f"{type(e).__name__} {e.problem}".strip()[:240]
        if spaced is not None:
            state.last_started_at = datetime.now(UTC) - spaced + RETRY_AFTER_FAILURE
        db.session.commit()
        if optional:
            return None
        raise
    state = connection.sync_state(org_id, resource)
    before = state.last_error
    state.last_ok_at, state.last_error = datetime.now(UTC), None
    if isinstance(counts, dict):
        state.counts = counts
    # A rejected token or a missing permission that this kind of read had hit is over.
    connection.settle(org_id, resource, before, worked=worked() > answered)
    return counts


def _attempt(org_id: int, resource: str, fn, spaced: timedelta | None):
    """`_run` for a reading the timer's pass can do without: a failure is recorded (and the
    connection flagged when it is the token's), the reading is due again in minutes, and the
    pass carries on. Returns the failure, or None. A token Whatnot rejects is not carried on
    from: nothing behind it can work, and the job's retry is how it is asked again."""
    try:
        _run(org_id, resource, fn, spaced=spaced)
    except Unauthorized:
        raise
    except WhatnotError as e:
        log.warning("whatnot %s read failed for shop %s: %s", resource, org_id, e)
        return e
    return None


@jobs.handler("whatnot_sync_show")
def sync_show_job(payload: dict) -> None:
    org_id, whatnot_show_id = payload["org_id"], payload["whatnot_show_id"]
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)

    def read():
        ws = sync_show(org_id, client, whatnot_show_id)
        if ws is None:
            return {"seen": 0, "new": 0}
        db.session.flush()
        link_recordings(org_id)
        return sync_show_orders(org_id, client, ws)

    _run(org_id, "orders", read)
    _after_orders(org_id, {whatnot_show_id}, client)
    from . import results, shipping

    shipping.schedule(org_id, whatnot_show_id)  # and the parcels those orders are in
    results.schedule_results(org_id, whatnot_show_id)  # and how Whatnot says the show did


def history(conn: WhatnotConnection, ws: WhatnotShow) -> bool:
    """The show was over before the shop chose "Apply as they arrive". Its orders are still
    read (they keep changing for days: shipped, delivered) and a person may apply them
    from the show's Payments tab, but they are never applied on their own: "as they
    arrive" means from that choice on, not weeks of old shows turning into sales to sort
    out. Reconnecting does not move the line (a show already being followed stays
    followed). An ended show Whatnot gives no end time for is judged by its start, and
    one with no time at all is history."""
    if ws.status in ACTIVE:
        return False
    over = ws.ended_at or ws.begins_at
    return over is None or over < (conn.auto_since or conn.connected_at)


def on_air_now(org_id: int) -> bool:
    """Whether a show of this shop is on air right now (see `on_air`)."""
    return on_air(
        db.session.scalars(
            select(WhatnotShow).where(
                WhatnotShow.organization_id == org_id, WhatnotShow.status.in_(ACTIVE)
            )
        ).all()
    )


def on_air(shows, now: datetime | None = None) -> bool:
    """Whether any of these shows (live or stopped on Whatnot) is on air: begun lately
    enough that it is not one left "stopped" for days (`ON_AIR_AT_MOST`)."""
    now = now or datetime.now(UTC)
    return any((ws.started_at or ws.begins_at or now) >= now - ON_AIR_AT_MOST for ws in shows)


def begin_repair(org_id: int) -> datetime | None:
    """The moment of the shop's one-time repair of reads the old catch-up cap spoiled, made
    the first time it is asked for; None for a shop that never asked (tests that look at
    `_unread_shows` alone). Until 2026-11-19 a catch-up that reached its 3,000-order cap
    marked EVERY show it was reading as read, including those it never got down to, so a show
    can hold a few of its orders, or none, and look complete (the dashboard's 30-day sales
    then fall short of Whatnot's). Nothing says which shows those are (Whatnot's own order
    count only comes for shows of the last week), so `_unread_shows` lists each ended show of
    the last `RECENT_SHOWS` once more if it was last read before this moment. Orders already
    held cost nothing (no items call); a catch-up that stops at its limit carries on next
    pass (`sync_orders_of`). These re-reads wait while a show is on air, like the late reads,
    and nothing about the shows is changed here: they stay read until the re-read."""
    state = connection.sync_state(org_id, "repair:capped-reads")  # (":" keeps it off Settings)
    if state.last_ok_at is None:
        state.last_ok_at = datetime.now(UTC)
        db.session.commit()
    return state.last_ok_at


def _unread_shows(org_id: int, live: list[WhatnotShow]) -> list[WhatnotShow]:
    """Ended shows whose orders the timer reads in full: never read since they ended, or
    (once, at each of `LATE_READS` after the end) not read since then, or short of the
    orders Whatnot's own results count. A card declined at the win and retried is a new order
    that can be placed after the show ended and after the first read of everything. The
    late and repair reads wait while a show is on air: they are not the first read of
    anything."""
    now = datetime.now(UTC)
    shows = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status == "ended",
            WhatnotShow.ended_at >= now - RECENT_SHOWS,
        )
    ).all()
    late_ok = not on_air(live, now)
    repair = connection.sync_state(org_id, "repair:capped-reads").last_ok_at
    held = dict(
        db.session.execute(
            select(WhatnotOrder.whatnot_show_id, func.count())
            .where(
                WhatnotOrder.organization_id == org_id,
                WhatnotOrder.whatnot_show_id.in_([ws.whatnot_id for ws in shows]),
            )
            .group_by(WhatnotOrder.whatnot_show_id)
        ).all()
    )
    out = []
    for ws in shows:
        read = ws.orders_synced_at
        if read is None or read < ws.ended_at:
            out.append(ws)
            continue
        if not late_ok:
            continue
        if repair is not None and read < repair:
            out.append(ws)  # the one-time repair (`begin_repair`)
            continue
        owed = [ws.ended_at + after for after in LATE_READS if ws.ended_at + after <= now]
        if owed and read < max(owed) and ws.ended_at >= now - LATE_READS_WITHIN:
            out.append(ws)
            continue
        counted = (ws.analytics or {}).get("orders_count")
        if (
            isinstance(counted, int)
            and held.get(ws.whatnot_id, 0) < counted
            and read < now - SHORT_REREAD_AFTER
        ):
            out.append(ws)  # Whatnot counts more orders than the app holds
    return out


def _due(org_id: int, resource: str, every: timedelta) -> bool:
    last = connection.sync_state(org_id, resource).last_started_at
    return last is None or datetime.now(UTC) - last >= every


def _asked_lately(state: WhatnotSyncState | None, now: datetime) -> bool:
    """The timer already asked for this show's parcels and should not ask again yet.
    `state.last_started_at` is when it last asked; `state.last_ok_at` is the last read
    that worked (None if none has)."""
    asked = state.last_started_at if state is not None else None
    return asked is not None and now - asked < PARCELS_RETRY


def _parcels_owed(org_id: int, now: datetime) -> list[WhatnotShow]:
    """Shows that ended lately with every order read since, whose parcels have not been
    read in full after the end: the Shipping page offers no label until they are
    (`shipping._complete`). Asked of what is stored, not of what this pass happened to
    read: a show's orders can be read after it ended while the app still has it as live
    (the show list is read every 15 minutes), and then it is never "unread" again."""
    shows = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status == "ended",
            WhatnotShow.ended_at >= now - PARCELS_AFTER,
            WhatnotShow.orders_synced_at >= WhatnotShow.ended_at,
        )
    ).all()
    if not shows:
        return []
    states = {
        state.resource: state
        for state in db.session.scalars(
            select(WhatnotSyncState).where(
                WhatnotSyncState.organization_id == org_id,
                WhatnotSyncState.resource.in_([f"shipments:{ws.id}" for ws in shows]),
            )
        )
    }
    owed = []
    for ws in shows:
        state = states.get(f"shipments:{ws.id}")
        read = state is not None and state.last_ok_at is not None
        if read and state.last_ok_at >= ws.ended_at:
            continue
        if not _asked_lately(state, now):
            owed.append(ws)
    return owed


@jobs.handler("whatnot_sync")
def sync_job(payload: dict) -> None:
    """The timer's pass for one shop: the show list, unsettled orders, and a live show's
    orders when Whatnot has been quiet (webhooks not set up, or not arriving)."""
    org_id = payload["org_id"]
    conn = connection.active(org_id)
    if conn is None:
        return
    client = client_for(org_id)
    if _due(org_id, "shows", SHOWS_EVERY):
        # A failed list is recorded and tried again in minutes (`_run`); the pass goes on with
        # the shows it already knows, so a list that keeps failing never starves the orders,
        # results and parcels behind it.
        _attempt(org_id, "shows", lambda: {"shows": sync_shows(org_id, client)}, SHOWS_EVERY)
        db.session.commit()
    conn = connection.active(org_id)
    quiet = conn.last_event_at is None or datetime.now(UTC) - conn.last_event_at > LIVE_POLL_QUIET
    live = db.session.scalars(
        select(WhatnotShow).where(
            WhatnotShow.organization_id == org_id, WhatnotShow.status.in_(ACTIVE)
        )
    ).all()
    # An ended show whose orders were never read in full (its webhook never came), or not
    # again since a late order can have been placed (`LATE_READS`).
    begin_repair(org_id)
    unread = _unread_shows(org_id, live)
    changed: set[str | None] = set()
    now = datetime.now(UTC)
    if not _due(org_id, "orders:stalled", STALLED_RETRY):
        # The last catch-up walked its limit and got nowhere: only the shows of the last few
        # days (a short walk) are read until it is due again.
        unread = [ws for ws in unread if ws.ended_at and ws.ended_at >= now - LATE_READS_WITHIN]
    to_read = (live if quiet else []) + unread
    orders_failure = None
    over: list[str] = []  # ended shows read again this pass: a late order can bring a parcel
    if to_read:
        # One read for all of them (`sync_orders_of`), not one newest-first read per show.
        # A failure does not stop the rest of the pass: it is raised at the end, so the job
        # is retried and nothing behind it (open orders, results, parcels) is starved.
        orders_failure = _attempt(
            org_id, "orders", lambda: sync_orders_of(org_id, client, to_read), None
        )
        if orders_failure is None:
            if (connection.sync_state(org_id, "orders").counts or {}).get("stalled"):
                connection.sync_state(org_id, "orders:stalled").last_started_at = now
            for ws in to_read:
                changed.add(ws.whatnot_id)
                if ws.status == "ended" and ws.ended_at and ws.ended_at >= now - PARCELS_AFTER:
                    over.append(ws.whatnot_id)
        db.session.commit()
    # After the orders: a show this pass has just read in full is owed its parcels too. Not
    # while a show is on air (its orders need the request budget): nothing is marked asked,
    # so they are still owed, and asked for, at the first pass after it (`PARCELS_AFTER` is
    # days, a show on air hours at most), with the one apply of a show that is over.
    aired = on_air(live, now)
    owed = [] if aired else _parcels_owed(org_id, datetime.now(UTC))
    if aired:
        over = []  # an ended show read for the first time is owed its parcels after the show
    for ws in owed:
        state = connection.sync_state(org_id, f"shipments:{ws.id}")
        if state.last_started_at is None:
            # Its orders are applied once more as those of a show that is over (what was
            # missing is flagged, an auction settles on one win): the last apply may have
            # run while the app still had it as live. Once, not on every retry of the read.
            changed.add(ws.whatnot_id)
        state.last_started_at = datetime.now(UTC)
    # Old orders Whatnot had not settled. Not while a show is on air: the show's orders
    # need the request budget, and these can wait an hour. While webhooks are arriving
    # they bring each change, so this safety net runs every few hours instead of hourly.
    hooked = conn.last_event_at is not None and now - conn.last_event_at <= OPEN_ORDERS_HOOKED
    if not aired and _due(
        org_id, "open_orders", OPEN_ORDERS_HOOKED if hooked else OPEN_ORDERS_EVERY
    ):
        before = _open_by_show(org_id)
        # Orders re-read before a failure are kept (`refresh_open_orders`), so the shows whose
        # orders moved are found and applied even when the pass then failed; and the failure
        # does not stop the rest of this pass.
        _attempt(
            org_id,
            "open_orders",
            lambda: {"orders": refresh_open_orders(org_id, client)},
            OPEN_ORDERS_HOOKED if hooked else OPEN_ORDERS_EVERY,
        )
        db.session.commit()
        after = _open_by_show(org_id)
        # Any order that moved (settled, cancelled, or from one open state to the other).
        changed |= {show for show in before if before[show] != after.get(show)}
    _after_orders(org_id, changed, client)
    from . import results, shipping

    for ws in owed:
        # What the "show ended" webhook starts, for a shop whose webhooks are not set up
        # (or did not arrive): this show's parcels read in full after it ended, so its
        # Shipping page is ready without anyone pressing "Read again".
        shipping.schedule(org_id, ws.whatnot_id)
        from .. import picks

        picks.schedule(ws)
    for whatnot_show_id in over:
        if whatnot_show_id not in {ws.whatnot_id for ws in owed}:
            shipping.schedule(org_id, whatnot_show_id)
    if shipping.due(org_id):
        shipping.schedule(org_id)
    for whatnot_show_id in results.results_due(org_id):
        results.schedule_results(org_id, whatnot_show_id)
    if results.ledger_due(org_id):
        results.schedule_ledger(org_id)
    from . import catalog

    if catalog.due(org_id):
        catalog.schedule(org_id)
    # The orders older than this timer reads, and a daily top-up of the last days (the
    # orders of a buy-now between shows): never while a show is on air; see backfill.py.
    from . import backfill

    if backfill.due(org_id):
        backfill.schedule(org_id)
    if orders_failure is not None:
        db.session.commit()  # what the rest of the pass queued is kept; only the job retries
        raise orders_failure  # the job is retried (everything else was spaced or done)


def _open_by_show(org_id: int) -> dict:
    """Each Whatnot show's open orders with their status: comparing before and after an
    order refresh tells which shows had an order move, not only how many are open."""
    rows = db.session.execute(
        select(WhatnotOrder.whatnot_show_id, WhatnotOrder.whatnot_id, WhatnotOrder.status).where(
            WhatnotOrder.organization_id == org_id, WhatnotOrder.status.in_(OPEN_ORDER_STATUSES)
        )
    ).all()
    out: dict = {}
    for show, order_id, status in rows:
        out.setdefault(show, set()).add((order_id, status))
    return out


def schedule_all() -> int:
    """Queue the timer's pass for every connected shop (the worker's sweep calls this)."""
    orgs = db.session.scalars(
        select(WhatnotConnection.organization_id).where(WhatnotConnection.disconnected_at.is_(None))
    ).all()
    for org_id in orgs:
        jobs.enqueue(
            "whatnot_sync", {"org_id": org_id}, org_id=org_id, dedupe_key=f"whatnot_sync:{org_id}"
        )
    db.session.commit()
    return len(orgs)
