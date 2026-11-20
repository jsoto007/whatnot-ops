"""One laptop recording that runs through two Whatnot shows (services/show_split.py).

The laptop keeps one key from Start until "Clear this show": a host who presses Stop after
one show and Start on the next show's page, or who moves from one show's seller page to
the next, records both shows under one key. The app splits such a recording at the moment
between the two shows: what came after it (chat, the lines read from it, sales, buyers'
Orders, questions) becomes a show of its own for the second Whatnot show, by re-pointing,
never by cancelling and making again, and not a unit moves in stock. A buyer's box already
being picked, packed or shipped with items from the second show stops it, and a person is
told in Review.

Every buyer, seller and channel name here is made up. RED starts with 3 on the shelf.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text

from app.db import db
from app.models import (
    AuditLog,
    ChatMessage,
    DeletedRecording,
    Job,
    Order,
    OrderStatus,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Role,
    Sale,
    SaleStatus,
    Show,
    ShowSaleMode,
    ShowShelf,
    User,
    WhatnotConnection,
)
from app.services import activity, ingest, pipeline, recordings
from app.services.devices import create_device
from app.services.showdate import show_date
from app.services.whatnot import client_for, sync
from app.services.whatnot import orders as whatnot_orders
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_show_merge import _ledger, _orders, _quiet, _ws
from tests.test_whatnot_orders import LOT, win

shop = wo.shop  # the world's shop connected to Whatnot

H, M = timedelta(hours=1), timedelta(minutes=1)
KEY = "2026-11-18T19:50:00.000Z"  # the laptop's key for its recording
REASON = "recording_two_shows"

# One laptop, one key, two shows: the early show from 5 h to 3 h ago, the late show from
# 2 h to 10 min ago. The host pressed Stop after the first and Start on the second.
LINES = [
    (5 * H + 5 * M, "host_shop", "welcome to the early show"),
    (4 * H, *win("kat.c", "Red bundle")),
    (3 * H + 30 * M, "kat.c", "thanks!"),
    (2 * H + 5 * M, "host_shop", "late show starting soon"),  # the first line after the gap
    (90 * M, *win("pine.owl", LOT, "7.00")),
    (89 * M, "pine.owl", "the red one please"),
]
BOUNDARY = 2 * H + 5 * M  # before now: the late show's part begins at its first line


def _two_shows(shop, *, late_status="ended"):
    now, fake = shop["now"], shop["fake"]
    early = fake.add_show(title="Early show", start=now - 5 * H, ended=now - 3 * H)["id"]
    late = fake.add_show(
        title="Late show",
        status=late_status,
        start=now - 2 * H,
        ended=now - 10 * M if late_status == "ended" else None,
    )["id"]
    return early, late


def _say(show_id: int, now: datetime, lines) -> None:
    n = db.session.scalar(select(func.count()).select_from(ChatMessage)) or 0
    for i, (ago, user, body, *source) in enumerate(lines, start=n + 1):
        db.session.add(
            ChatMessage(
                show_id=show_id,
                client_id=f"{i:016x}",
                seq=i,
                username=user,
                body=body,
                raw_text=f"{user} {body}".strip(),
                source=source[0] if source else "chat",
                captured_at=now - ago,
            )
        )


def _recording(app, shop, lines, *, began, address=None, key=KEY, channel=None) -> int:
    """A laptop recording that started `began` ago, with `lines` (ago, user, body[, source])."""
    now = shop["now"]
    with app.app_context():
        rec = Show(
            organization_id=shop["org"],
            show_key=key,
            started_at=now - began,
            show_date=show_date(now - began),
            source_url=address,
            channel=channel,
        )
        db.session.add(rec)
        db.session.flush()
        _say(rec.id, now, lines)
        db.session.commit()
        return rec.id


def _read(app, shop, *orders_of):
    """The timer: read the shows (which ties and splits recordings), then these shows' orders."""
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        db.session.commit()
        for whatnot_id in orders_of:
            sync.sync_show_orders(shop["org"], client, _ws(whatnot_id))
            db.session.commit()


def _parts(app, key=KEY) -> list[int]:
    """The shows the laptop's recording `key` became: its own, then each part."""
    with app.app_context():
        return [
            s.id
            for s in db.session.scalars(
                select(Show)
                .where((Show.show_key == key) | (Show.recording_key == key))
                .order_by(Show.started_at, Show.id)
            )
        ]


def _bodies(show_id: int) -> list[str]:
    return list(
        db.session.scalars(
            select(ChatMessage.body)
            .where(ChatMessage.show_id == show_id)
            .order_by(ChatMessage.captured_at, ChatMessage.seq)
        )
    )


def _sales(app) -> dict[str, list[int]]:
    with app.app_context():
        out: dict[str, list[int]] = {}
        for s in db.session.scalars(select(Sale).order_by(Sale.id)):
            out.setdefault(s.buyer_username, []).append(s.show_id)
        return out


def _open_items(app, reason=REASON) -> list[ReviewItem]:
    with app.app_context():
        items = db.session.scalars(
            select(ReviewItem).where(
                ReviewItem.reason == reason, ReviewItem.status == ReviewStatus.open
            )
        ).all()
        db.session.expunge_all()
        return items


# ---------- the split ----------


def test_a_recording_through_two_shows_becomes_two_shows_and_not_a_unit_moves(app, shop):
    _quiet(app, shop)
    early, late = _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M, channel="shop_channel")
    run(app, rec)
    ledger, red = _ledger(app), level(app, shop["red"])

    _read(app, shop)

    parts = _parts(app)
    assert len(parts) == 2 and parts[0] == rec
    part = parts[1]
    now = shop["now"]
    with app.app_context():
        r, p = db.session.get(Show, rec), db.session.get(Show, part)
        ws_early, ws_late = _ws(early), _ws(late)
        # Each half is its Whatnot show's, for good (the clock never re-ties them).
        assert (r.whatnot_show_id, p.whatnot_show_id) == (ws_early.id, ws_late.id)
        assert r.whatnot_link == "split" and p.whatnot_link == "split"
        assert p.recording_key == KEY and p.show_key != KEY and not p.show_key.startswith("wn:")
        assert p.started_at == now - BOUNDARY
        assert p.show_date == show_date(ws_late.started_at or ws_late.begins_at)
        assert p.channel == "shop_channel" and p.source_url is None
        assert r.show_date == show_date(now - (5 * H + 10 * M))  # the first half keeps its own
        assert _bodies(rec) == [
            "welcome to the early show",
            "kat.c won the auction for Red bundle for $25.00",
            "thanks!",
        ]
        assert _bodies(part) == [
            "late show starting soon",
            f"pine.owl won the auction for {LOT} for $7.00",
            "the red one please",
        ]
        # The lines read from the chat go with their line (or the parser reads them again).
        events = db.session.scalars(
            select(ParsedEvent)
            .join(ChatMessage, ChatMessage.id == ParsedEvent.message_id)
            .where(ChatMessage.show_id.in_([rec, part]))
        ).all()
        assert len(events) == len(LINES)
        assert all(e.show_id == e.message.show_id for e in events)
        # pine.owl's "which product?" question goes with the sale; every question with it.
        pine = db.session.scalar(select(Sale).where(Sale.buyer_username == "pine.owl"))
        asked = db.session.scalars(select(ReviewItem).where(ReviewItem.sale_id.is_not(None))).all()
        assert {i.show_id for i in asked if i.sale_id == pine.id} == {part}
        assert all(i.show_id == i.sale.show_id for i in asked)
        split = db.session.scalar(select(AuditLog).where(AuditLog.action == "show.split"))
        assert split is not None and split.actor_type == "system"
        words = activity.describe(split.action, split.details)
        assert "Early show" in words and "Late show" in words
        for internal in ("show_id", "recording_key", "whatnot_link", "made", "None"):
            assert internal not in words
    assert _sales(app) == {"kat.c": [rec], "pine.owl": [part]}
    assert [(b, s) for b, s, _st, _ids in _orders(app)] == [("kat.c", rec), ("pine.owl", part)]
    assert _ledger(app) == ledger and level(app, shop["red"]) == red

    # Read again: nothing changes.
    before = (_parts(app), _orders(app), _ledger(app), _sales(app))
    _read(app, shop)
    _read(app, shop)
    assert (_parts(app), _orders(app), _ledger(app), _sales(app)) == before


def test_a_buyer_in_both_shows_gets_one_order_in_each_and_a_box_being_packed_stops_it(app, shop):
    """kat.c won in both shows: one Order in the recording. Split, the early win stays on
    it and the late win gets an Order of its own in the late show. While that Order's box
    is being picked the split waits, and a person is told in Review, once."""
    _quiet(app, shop)
    _two_shows(shop)
    lines = [*LINES[:4], (90 * M, *win("kat.c", LOT, "7.00")), (89 * M, "kat.c", "blue one")]
    rec = _recording(app, shop, lines, began=5 * H + 10 * M)
    run(app, rec)
    with app.app_context():
        order = db.session.scalar(select(Order))
        assert len(order.sales) == 2
        order.status = OrderStatus.picking
        db.session.commit()
    ledger = _ledger(app)

    _read(app, shop)
    _read(app, shop)
    assert _parts(app) == [rec]
    items = _open_items(app)
    assert len(items) == 1 and items[0].show_id == rec
    words = items[0].summary
    assert "kat.c" in words and "picked" in words and "two" in words.lower()
    for internal in ("show_id", "recording_key", "whatnot_link", "FULFILLMENT"):
        assert internal not in words
    assert _sales(app) == {"kat.c": [rec, rec]}

    with app.app_context():
        db.session.scalar(select(Order)).status = OrderStatus.paid
        db.session.commit()
    _read(app, shop)
    parts = _parts(app)
    assert len(parts) == 2
    assert _sales(app) == {"kat.c": [rec, parts[1]]}
    kat = [(s, ids) for b, s, _st, ids in _orders(app) if b == "kat.c"]
    assert [s for s, _ in kat] == sorted([rec, parts[1]])
    assert all(len(ids) == 1 for _, ids in kat)
    assert _open_items(app) == []  # settled by the split itself
    assert _ledger(app) == ledger


def test_the_show_made_for_the_second_show_folds_into_its_part(app, shop):
    """The recording's address names the early show, so the late show had nobody's
    recording and the app made it a show, with its orders. Split, that made show folds
    into the late part: one show, one Order per buyer, nothing moved in stock."""
    _quiet(app, shop)
    early, late = _two_shows(shop)
    now = shop["now"]
    fake = shop["fake"]
    fake.add_order(
        show_id=early, buyer="kat.c", items=[{"title": "Red bundle", "price_cents": 2500}],
        created=now - 4 * H,
    )  # fmt: skip
    fake.add_order(
        show_id=late, buyer="pine.owl", items=[{"title": LOT, "price_cents": 700}],
        created=now - 90 * M,
    )  # fmt: skip
    fake.add_order(
        show_id=late, buyer="lani808", items=[{"title": "Red bundle", "price_cents": 2500}],
        created=now - 80 * M,
    )  # fmt: skip
    rec = _recording(
        app, shop, LINES, began=5 * H + 10 * M, address=f"https://www.whatnot.com/live/{early}"
    )
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    _read(app, shop, early, late)  # nothing parsed yet: nothing says two shows
    assert _parts(app) == [rec]
    with app.app_context():
        for whatnot_id in (early, late):
            whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": whatnot_id})
        made = [s.id for s in sync.recordings_of(_ws(late))]
        assert len(made) == 1 and db.session.get(Show, made[0]).whatnot_link == "made"
    run(app, rec)
    ledger, red = _ledger(app), level(app, shop["red"])

    _read(app, shop)

    parts = _parts(app)
    assert len(parts) == 2
    part = parts[1]
    with app.app_context():
        assert db.session.get(Show, made[0]) is None
        assert [s.id for s in sync.recordings_of(_ws(late))] == [part]
        assert [s.id for s in sync.recordings_of(_ws(early))] == [rec]
        assert db.session.get(Show, rec).whatnot_link == "auto"  # its address keeps it
    assert _sales(app) == {"kat.c": [rec], "pine.owl": [part], "lani808": [part]}
    assert [(b, s) for b, s, _st, _ids in _orders(app)] == [
        ("kat.c", rec),
        ("lani808", part),
        ("pine.owl", part),
    ]
    assert _ledger(app) == ledger and level(app, shop["red"]) == red


def test_whatnots_orders_applied_while_the_recording_splits_land_in_the_new_half(
    app, shop, monkeypatch
):
    """Whatnot's orders of the late show are being applied to the show made for it when
    the shows are read and the recording splits (the made show folds into the late half).
    The apply never records a sale in a show whose parse lock it does not hold: it starts
    again, and the sale lands in the late half, under its lock."""
    _quiet(app, shop)
    early, late = _two_shows(shop)
    now, fake = shop["now"], shop["fake"]
    fake.add_order(
        show_id=late, buyer="pine.owl", items=[{"title": LOT, "price_cents": 700}],
        created=now - 90 * M,
    )  # fmt: skip
    rec = _recording(
        app, shop, LINES, began=5 * H + 10 * M, address=f"https://www.whatnot.com/live/{early}"
    )
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    _read(app, shop, early, late)
    with app.app_context():
        whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": late})
        made = [s.id for s in sync.recordings_of(_ws(late))]
        assert db.session.get(Show, made[0]).whatnot_link == "made"
    run(app, rec)
    fake.add_order(
        show_id=late, buyer="moss.fern", items=[{"title": LOT, "price_cents": 900}],
        created=now - 30 * M,
    )  # fmt: skip
    with app.app_context():
        sync.sync_show_orders(shop["org"], client_for(shop["org"]), _ws(late))
        db.session.commit()

    real = pipeline._lock_show
    state = {"raced": False}

    def racing(show_id, *, wait=False):
        if not state["raced"]:
            state["raced"] = True
            # Meanwhile, in another transaction: the shows are read and the recording splits.
            with app.app_context():
                sync.sync_shows(shop["org"], client_for(shop["org"]))
                db.session.commit()
        return real(show_id, wait=wait)

    held = []
    real_create = pipeline.create_sale_from_order

    def create(show, **kw):
        held.append(
            (
                show.id,
                bool(
                    db.session.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                            "AND pid = pg_backend_pid() AND classid = :c AND objid = :s"
                        ),
                        {"c": pipeline.PARSE_LOCK, "s": show.id},
                    )
                ),
            )
        )
        return real_create(show, **kw)

    monkeypatch.setattr(pipeline, "_lock_show", racing)
    monkeypatch.setattr(pipeline, "create_sale_from_order", create)
    with app.app_context():
        whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": late})
    parts = _parts(app)
    assert state["raced"] and len(parts) == 2
    assert held == [(parts[1], True)]
    with app.app_context():
        assert db.session.get(Show, made[0]) is None
    assert _sales(app)["moss.fern"] == [parts[1]]


def test_a_recording_the_clock_tied_to_the_later_show_hands_each_show_its_own(app, shop):
    """The usual shape with no address: the clock tied the recording to the longer, later
    show, so that show's orders made their sales and Orders in the recording, and the
    earlier show got a show made from its orders. kat.c bought in both. Split, every sale
    and Order is in its own show's half, the made show folds into the first half, and not a
    unit moves in stock."""
    _quiet(app, shop)
    now, fake = shop["now"], shop["fake"]
    early = fake.add_show(title="Early show", start=now - 5 * H, ended=now - 4 * H)["id"]
    late = fake.add_show(title="Late show", start=now - 3 * H, ended=now - 10 * M)["id"]
    for show_id, buyer, title, price, ago in (
        (early, "kat.c", "Red bundle", 2500, 4 * H + 30 * M),
        (early, "lani808", "Red bundle", 2500, 4 * H + 20 * M),
        (late, "pine.owl", LOT, 700, 2 * H),
        (late, "kat.c", LOT, 900, H),
    ):
        fake.add_order(
            show_id=show_id, buyer=buyer, items=[{"title": title, "price_cents": price}],
            created=now - ago,
        )  # fmt: skip
    lines = [
        (5 * H + 5 * M, "host_shop", "welcome to the early show"),
        (4 * H + 30 * M, *win("kat.c", "Red bundle")),
        (3 * H + 5 * M, "host_shop", "late show in five"),
        (2 * H, *win("pine.owl", LOT, "7.00")),
        (H, *win("kat.c", LOT, "9.00")),
    ]
    rec = _recording(app, shop, lines, began=5 * H + 10 * M)
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    _read(app, shop, early, late)  # nothing read from the chat yet: nothing says two shows
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_show_id == _ws(late).id  # the clock's tie
        for whatnot_id in (early, late):
            whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": whatnot_id})
        made = [s.id for s in sync.recordings_of(_ws(early))]
        assert len(made) == 1 and db.session.get(Show, made[0]).whatnot_link == "made"
    run(app, rec)
    assert _sales(app) == {
        "kat.c": [made[0], rec],
        "lani808": [made[0]],
        "pine.owl": [rec],
    }
    ledger, red = _ledger(app), level(app, shop["red"])

    _read(app, shop)

    parts = _parts(app)
    assert len(parts) == 2 and parts[0] == rec
    part = parts[1]
    with app.app_context():
        assert db.session.get(Show, made[0]) is None
        assert [s.id for s in sync.recordings_of(_ws(early))] == [rec]
        assert [s.id for s in sync.recordings_of(_ws(late))] == [part]
        kat = {
            s.item_text: (s.show_id, s.order.show_id)
            for s in db.session.scalars(select(Sale).where(Sale.buyer_username == "kat.c"))
        }
        assert kat == {"Red bundle": (rec, rec), LOT: (part, part)}
    assert _sales(app) == {"kat.c": [rec, part], "lani808": [rec], "pine.owl": [part]}
    assert [(b, s) for b, s, _st, _ids in _orders(app)] == sorted(
        [("kat.c", rec), ("kat.c", part), ("lani808", rec), ("pine.owl", part)]
    )
    assert _ledger(app) == ledger and level(app, shop["red"]) == red


# ---------- what Whatnot's orders told a person, with no sale or line to follow ----------


def _clock_tied_to_the_late_show(app, shop):
    """The shape above: the early show 5 h to 4 h ago, the late show 3 h to 10 min ago, a
    win line and its paid order in each, orders applied as they arrive. Read before the chat
    is, the clock ties the recording to the longer, later show."""
    _quiet(app, shop)
    now, fake = shop["now"], shop["fake"]
    early = fake.add_show(title="Early show", start=now - 5 * H, ended=now - 4 * H)["id"]
    late = fake.add_show(title="Late show", start=now - 3 * H, ended=now - 10 * M)["id"]
    for show_id, buyer, title, price, ago in (
        (early, "kat.c", "Red bundle", 2500, 4 * H + 30 * M),
        (late, "pine.owl", LOT, 700, 2 * H),
    ):
        fake.add_order(
            show_id=show_id, buyer=buyer, items=[{"title": title, "price_cents": price}],
            created=now - ago,
        )  # fmt: skip
    lines = [
        (5 * H + 5 * M, "host_shop", "welcome to the early show"),
        (4 * H + 30 * M, *win("kat.c", "Red bundle")),
        (3 * H + 5 * M, "host_shop", "late show in five"),
        (2 * H, *win("pine.owl", LOT, "7.00")),
    ]
    rec = _recording(app, shop, lines, began=5 * H + 10 * M)
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    return early, late, rec


def _bare(shop, show_id: str, buyer: str, ago: timedelta) -> None:
    """A paid order Whatnot lists with no items yet (left out of the apply)."""
    order = shop["fake"].add_order(
        show_id=show_id, buyer=buyer, items=[{"title": "Green bundle", "price_cents": 800}],
        created=shop["now"] - ago,
    )  # fmt: skip
    for item in shop["fake"].items_of(order["id"]):
        del shop["fake"].order_items[item["id"]]


def _apply(app, shop, *whatnot_ids) -> None:
    with app.app_context():
        for whatnot_id in whatnot_ids:
            whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": whatnot_id})


def _notices(app, reason: str, *names: str) -> dict[int, tuple[int, str, str | None]]:
    """Every Review item for `reason`, by id: its show, its status, and whom it is about (the
    buyer it was written for, else the first of `names` its words mention)."""
    with app.app_context():
        out = {}
        for i in db.session.scalars(select(ReviewItem).where(ReviewItem.reason == reason)):
            named = next((n for n in names if n in i.summary), None)
            out[i.id] = (i.show_id, i.status.value, (i.proposal or {}).get("buyer") or named)
        db.session.rollback()
        return out


def _ids(notices: dict) -> dict[str | None, int]:
    return {who: i for i, (_show, _status, who) in notices.items()}


def _dismiss(app, shop, item_id: int) -> None:
    """A person's "Got it" in Review."""
    with app.app_context():
        person = db.session.scalar(select(User).where(User.organization_id == shop["org"]))
        if person is None:
            person = User(
                organization_id=shop["org"],
                email="owner@shop.test",
                name="Olive",
                password_hash="x",
                role=Role.owner,
                session_token="t" * 32,
            )
            db.session.add(person)
            db.session.flush()
        pipeline.resolve(db.session.get(ReviewItem, item_id), "dismiss", {}, user=person)
        db.session.commit()


def _again(app, shop, early: str, late: str) -> None:
    """Later reads: both shows' orders read and applied, the shows read (the split asked
    again), and applied once more."""
    _read(app, shop, early, late)
    _apply(app, shop, late, early)
    _read(app, shop)
    _apply(app, shop, late, early)


def test_a_declined_payment_of_the_later_show_goes_to_its_half_as_it_was(app, shop):
    """Whatnot declined two buyers' payments in the late show, wins with no line here (no
    sale): each is a Review item on the show the late show's orders were applied to, the
    recording the clock tied to it. A person said "Got it" to one. Split, both go to the
    late show's half as they were: its next applies know them and ask nothing again (a
    dismissed one is never opened again), and the open one clears there when the buyer pays
    after all."""
    early, late, rec = _clock_tied_to_the_late_show(app, shop)
    now, fake = shop["now"], shop["fake"]
    for buyer, title, price, ago in (
        ("moss.fern", "Green bundle", 800, 90 * M),
        ("fern.moss", "Blue bundle", 600, 80 * M),
    ):
        fake.add_order(
            show_id=late, buyer=buyer, items=[{"title": title, "price_cents": price}],
            created=now - ago, status="failed",
        )  # fmt: skip
    _read(app, shop, early, late)
    _apply(app, shop, early, late)
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_show_id == _ws(late).id  # the clock's tie
    declined = _notices(app, "order_declined")
    assert sorted(declined.values()) == [(rec, "open", "fern.moss"), (rec, "open", "moss.fern")]
    ids = _ids(declined)
    _dismiss(app, shop, ids["moss.fern"])
    run(app, rec)

    _read(app, shop)

    parts = _parts(app)
    assert len(parts) == 2 and parts[0] == rec
    part = parts[1]
    told = {
        ids["moss.fern"]: (part, "dismissed", "moss.fern"),
        ids["fern.moss"]: (part, "open", "fern.moss"),
    }
    assert _notices(app, "order_declined") == told
    _again(app, shop, early, late)
    assert _parts(app) == parts
    assert _notices(app, "order_declined") == told
    # fern.moss pays after all: a retried card, a paid order for the same lot and price.
    fake.add_order(
        show_id=late, buyer="fern.moss", items=[{"title": "Blue bundle", "price_cents": 600}],
        created=now - 79 * M,
    )  # fmt: skip
    _read(app, shop, late)
    _apply(app, shop, late, early)
    assert _notices(app, "order_declined") == {
        **told,
        ids["fern.moss"]: (part, "resolved", "fern.moss"),
    }
    with app.app_context():
        item = db.session.get(ReviewItem, ids["fern.moss"])
        assert item.resolution == {"by": "paid on Whatnot"}
    assert _sales(app)["fern.moss"] == [part]


@pytest.mark.parametrize("late_said", ["open", "dismissed"])
def test_each_shows_orders_left_out_notice_stays_with_its_own_half(app, shop, late_said):
    """Whatnot lists a paid order with no items in each show: one "orders left out" notice
    per show, the early show's on the show made from its orders, the late show's on the
    recording the clock tied to it (open, or dismissed by a person). Split, the late show's
    goes to its half as it was before the made show folds into the first half: each half
    holds its own show's notice, and neither show's next apply asks again or settles the
    other show's."""
    early, late, rec = _clock_tied_to_the_late_show(app, shop)
    _bare(shop, early, "early.bare", 4 * H + 40 * M)
    _bare(shop, late, "late.bare", 90 * M)
    _read(app, shop, early, late)
    _apply(app, shop, early, late)
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_show_id == _ws(late).id  # the clock's tie
        made = [s.id for s in sync.recordings_of(_ws(early))]
        assert len(made) == 1 and made[0] != rec
    left_out = _notices(app, "orders_left_out", "early.bare", "late.bare")
    assert sorted(left_out.values()) == sorted(
        [(made[0], "open", "early.bare"), (rec, "open", "late.bare")]
    )
    ids = _ids(left_out)
    if late_said == "dismissed":
        _dismiss(app, shop, ids["late.bare"])
    run(app, rec)

    _read(app, shop)

    parts = _parts(app)
    assert len(parts) == 2 and parts[0] == rec
    part = parts[1]
    with app.app_context():
        assert db.session.get(Show, made[0]) is None  # folded into the first half
    told = {
        ids["early.bare"]: (rec, "open", "early.bare"),
        ids["late.bare"]: (part, late_said, "late.bare"),
    }
    assert _notices(app, "orders_left_out", "early.bare", "late.bare") == told
    _again(app, shop, early, late)
    assert _parts(app) == parts
    assert _notices(app, "orders_left_out", "early.bare", "late.bare") == told


def test_a_payment_no_sale_here_answers_goes_to_the_later_shows_half(app, shop):
    """fen.moss's paid order in the late show made a sale here (no win line), and a person
    cancelled that sale: the late show's next apply asks a person about the payment ("paid,
    but no matching sale"), an item that names the order, on the recording the clock tied
    to the late show. Split, it goes to the late show's half with the order's sale, and the
    late show's next applies know it."""
    early, late, rec = _clock_tied_to_the_late_show(app, shop)
    now, fake = shop["now"], shop["fake"]
    paid = fake.add_order(
        show_id=late, buyer="fen.moss", items=[{"title": "Red bundle", "price_cents": 2500}],
        created=now - 100 * M,
    )  # fmt: skip
    _read(app, shop, early, late)
    _apply(app, shop, early, late)
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_show_id == _ws(late).id  # the clock's tie
        sale = db.session.scalar(select(Sale).where(Sale.buyer_username == "fen.moss"))
        assert sale.show_id == rec
        pipeline.set_sale_status(
            sale, SaleStatus.cancelled, reason="a person cancelled it", actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
    _apply(app, shop, late)
    unmatched = _notices(app, "unmatched_payment")
    assert list(unmatched.values()) == [(rec, "open", "fen.moss")]
    with app.app_context():
        item = db.session.get(ReviewItem, next(iter(unmatched)))
        assert item.proposal["dedupeKey"] == f"order:{paid['id']}"
    run(app, rec)

    _read(app, shop)

    parts = _parts(app)
    assert len(parts) == 2 and parts[0] == rec
    told = {i: (parts[1], "open", "fen.moss") for i in unmatched}
    assert _notices(app, "unmatched_payment") == told
    assert _sales(app)["fen.moss"] == [parts[1]]
    _again(app, shop, early, late)
    assert _parts(app) == parts
    assert _notices(app, "unmatched_payment") == told


def test_whatnots_notices_stay_put_when_the_clock_tied_the_recording_to_the_early_show(app, shop):
    """The mirror: the recording overlaps the early show longer, so the clock tied it to the
    early show. The early show's declined payment and orders-left-out notice are on it and
    stay there; the late show's are on the show made from its orders and come to the late
    half as that show folds into it. Nothing is asked twice."""
    _quiet(app, shop)
    now, fake = shop["now"], shop["fake"]
    early = fake.add_show(title="Early show", start=now - 5 * H, ended=now - 90 * M)["id"]
    late = fake.add_show(title="Late show", start=now - 60 * M, ended=now - 5 * M)["id"]
    for show_id, buyer, title, price, ago, status in (
        (early, "kat.c", "Red bundle", 2500, 4 * H + 30 * M, "pending_fulfillment"),
        (early, "dune.wren", "Green bundle", 800, 4 * H, "failed"),
        (late, "pine.owl", LOT, 700, 40 * M, "pending_fulfillment"),
        (late, "kat.c", LOT, 900, 20 * M, "pending_fulfillment"),
        (late, "fern.moss", "Blue bundle", 600, 30 * M, "failed"),
    ):
        fake.add_order(
            show_id=show_id, buyer=buyer, items=[{"title": title, "price_cents": price}],
            created=now - ago, status=status,
        )  # fmt: skip
    _bare(shop, early, "early.bare", 4 * H + 10 * M)
    _bare(shop, late, "late.bare", 35 * M)
    lines = [
        (5 * H + 5 * M, "host_shop", "welcome to the early show"),
        (4 * H + 30 * M, *win("kat.c", "Red bundle")),
        (65 * M, "host_shop", "late show in five"),
        (40 * M, *win("pine.owl", LOT, "7.00")),
        (20 * M, *win("kat.c", LOT, "9.00")),
    ]
    rec = _recording(app, shop, lines, began=5 * H + 10 * M)
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    _read(app, shop, early, late)
    _apply(app, shop, early, late)
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_show_id == _ws(early).id  # the clock's tie
        made = [s.id for s in sync.recordings_of(_ws(late))]
        assert len(made) == 1 and made[0] != rec
    names = ("early.bare", "late.bare")
    before = {r: _notices(app, r, *names) for r in ("order_declined", "orders_left_out")}
    assert sorted(before["order_declined"].values()) == sorted(
        [(rec, "open", "dune.wren"), (made[0], "open", "fern.moss")]
    )
    assert sorted(before["orders_left_out"].values()) == sorted(
        [(rec, "open", "early.bare"), (made[0], "open", "late.bare")]
    )
    run(app, rec)

    _read(app, shop)

    parts = _parts(app)
    assert len(parts) == 2 and parts[0] == rec
    with app.app_context():
        assert db.session.get(Show, made[0]) is None  # folded into the late half
    half = {rec: rec, made[0]: parts[1]}
    told = {
        r: {i: (half[s], status, who) for i, (s, status, who) in items.items()}
        for r, items in before.items()
    }
    for reason, items in told.items():
        assert _notices(app, reason, *names) == items
    _again(app, shop, early, late)
    assert _parts(app) == parts
    for reason, items in told.items():
        assert _notices(app, reason, *names) == items


def test_a_late_line_not_read_yet_is_read_in_its_half(app, shop):
    _quiet(app, shop)
    _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    run(app, rec)
    with app.app_context():
        _say(rec, shop["now"], [(80 * M, "pine.owl", "and a blue one")])  # not read yet
        db.session.query(Job).delete()
        db.session.commit()
    _read(app, shop)
    part = _parts(app)[1]
    with app.app_context():
        assert [
            j.payload["show_id"]
            for j in db.session.scalars(select(Job).where(Job.kind == "parse_show"))
        ] == [part]


def test_the_split_and_the_read_of_its_new_half_run_at_once_without_a_worker(
    app, shop, inline_jobs
):
    """Development and the browser tests run jobs at once: the new half's lines are read
    as soon as it is made, inside the read of Whatnot's shows."""
    _quiet(app, shop)
    _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    run(app, rec)
    with app.app_context():
        _say(rec, shop["now"], [(80 * M, *win("lark.hill", LOT, "9.00"))])  # not read yet
        db.session.commit()
    _read(app, shop)
    part = _parts(app)[1]
    with app.app_context():
        unread = db.session.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(
                ChatMessage.show_id == part,
                ~select(ParsedEvent.id).where(ParsedEvent.message_id == ChatMessage.id).exists(),
            )
        )
        assert unread == 0
    assert _sales(app)["lark.hill"] == [part]


def test_got_it_on_a_split_that_cannot_happen_is_not_asked_again(app, shop):
    """kat.c's box with a win from the second show has shipped: the recording can never be
    split. A person says "Got it" once; later reads do not ask again."""
    _quiet(app, shop)
    _two_shows(shop)
    lines = [*LINES[:4], (90 * M, *win("kat.c", LOT, "7.00"))]
    rec = _recording(app, shop, lines, began=5 * H + 10 * M)
    run(app, rec)
    with app.app_context():
        db.session.scalar(select(Order)).status = OrderStatus.shipped
        owner = User(
            organization_id=shop["org"],
            email="owner@shop.test",
            name="Olive",
            password_hash="x",
            role=Role.owner,
            session_token="t" * 32,
        )
        db.session.add(owner)
        db.session.commit()
        owner_id = owner.id
    _read(app, shop)
    items = _open_items(app)
    assert len(items) == 1 and "shipped" in items[0].summary
    with app.app_context():
        item = db.session.get(ReviewItem, items[0].id)
        pipeline.resolve(item, "dismiss", {}, user=db.session.get(User, owner_id))
        db.session.commit()
    _read(app, shop)
    _read(app, shop)
    assert _open_items(app) == [] and _parts(app) == [rec]


def test_a_sale_made_from_a_whatnot_order_goes_where_the_order_says(app, shop):
    """A sale the app made from one of Whatnot's orders has no chat line: Whatnot's word on
    which show it was sold in decides its half, not the time Whatnot stamped on it. Here
    the late show's first order is stamped five minutes before the laptop's quiet moment
    between the shows (the laptop's clock and Whatnot's are not the same clock)."""
    _quiet(app, shop)
    early, late = _two_shows(shop)
    now, fake = shop["now"], shop["fake"]
    late_order = fake.add_order(
        show_id=late, buyer="lani808", items=[{"title": "Red bundle", "price_cents": 2500}],
        created=now - (BOUNDARY + 5 * M),
    )  # fmt: skip
    early_order = fake.add_order(
        show_id=early, buyer="moss.fern", items=[{"title": LOT, "price_cents": 900}],
        created=now - (4 * H + 10 * M),
    )  # fmt: skip
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    _read(app, shop, early, late)  # nothing read from the chat yet: nothing says two shows
    assert _parts(app) == [rec]
    with app.app_context():
        for buyer, order, ago in (
            ("lani808", late_order, BOUNDARY + 5 * M),
            ("moss.fern", early_order, 4 * H + 10 * M),
        ):
            db.session.add(
                Sale(
                    organization_id=shop["org"],
                    show_id=rec,
                    buyer_username=buyer,
                    item_text="Red bundle",
                    price_cents=2500,
                    external_order_id=str(order["id"]),
                    ordered_at=now - ago,
                )
            )
        db.session.commit()
    run(app, rec)
    _read(app, shop)
    parts = _parts(app)
    assert len(parts) == 2
    with app.app_context():
        assert db.session.get(Show, parts[1]).started_at == now - BOUNDARY
    assert _sales(app) == {
        "lani808": [parts[1]],
        "moss.fern": [rec],
        "kat.c": [rec],
        "pine.owl": [parts[1]],
    }


def test_a_recording_through_three_shows_becomes_three(app, shop):
    _quiet(app, shop)
    now, fake = shop["now"], shop["fake"]
    ids = [
        fake.add_show(title=title, start=now - begin, ended=now - begin + H)["id"]
        for title, begin in (("First show", 7 * H), ("Second show", 5 * H), ("Third show", 3 * H))
    ]
    lines = [
        (7 * H + 5 * M, "host_shop", "hello first"),
        (6 * H + 30 * M, *win("kat.c", "Red bundle")),
        (5 * H + 5 * M, "host_shop", "hello second"),
        (4 * H + 30 * M, *win("pine.owl", LOT, "7.00")),
        (3 * H + 5 * M, "host_shop", "hello third"),
        (2 * H + 30 * M, *win("lark.hill", LOT, "9.00")),
    ]
    rec = _recording(app, shop, lines, began=7 * H + 10 * M)
    run(app, rec)
    _read(app, shop)
    parts = _parts(app)
    assert len(parts) == 3 and parts[0] == rec
    with app.app_context():
        assert [db.session.get(Show, p).whatnot_show_id for p in parts] == [_ws(i).id for i in ids]
        assert [_bodies(p)[0] for p in parts] == ["hello first", "hello second", "hello third"]
    assert _sales(app) == {"kat.c": [parts[0]], "pine.owl": [parts[1]], "lark.hill": [parts[2]]}


@pytest.mark.parametrize(("late_hour", "late_minute", "next_day"), [(5, 30, True), (4, 30, False)])
def test_the_second_show_gets_its_own_date_after_5_am(app, shop, late_hour, late_minute, next_day):
    """The show date rolls at 5 AM in the shop's zone: a late show that begins at 5:30 AM is
    the next day's show, one that begins at 4:30 AM is still the night before's."""
    _quiet(app, shop)
    zone = ZoneInfo("America/Chicago")
    # Four days back: clear of the fixture's own show, two days ago, at any hour of the run.
    day = (shop["now"].astimezone(zone) - timedelta(days=4)).date()
    at = lambda d, h, m: datetime(d.year, d.month, d.day, h, m, tzinfo=zone).astimezone(UTC)  # noqa: E731
    early_start = at(day, 22, 0)
    early_end = early_start + 3 * H
    late_start = at(day + timedelta(days=1), late_hour, late_minute)
    fake, now = shop["fake"], shop["now"]
    fake.add_show(title="Night show", start=early_start, ended=early_end)
    fake.add_show(title="Morning show", start=late_start, ended=late_start + 90 * M)
    ago = lambda t: now - t  # noqa: E731
    lines = [
        (ago(early_start + 5 * M), "host_shop", "welcome night owls"),
        (ago(early_start + 60 * M), *win("kat.c", "Red bundle")),
        (ago(late_start - 10 * M), "host_shop", "good morning"),
        (ago(late_start + 30 * M), *win("pine.owl", LOT, "7.00")),
    ]
    rec = _recording(app, shop, lines, began=now - (early_start - 10 * M))
    run(app, rec)
    _read(app, shop)
    parts = _parts(app)
    assert len(parts) == 2
    with app.app_context():
        assert db.session.get(Show, rec).show_date == day
        expected = day + timedelta(days=1) if next_day else day
        assert db.session.get(Show, parts[1]).show_date == expected


# ---------- what the laptop sends after the split ----------


def _row(i: int, at: datetime, body: str, user: str = "chatter", source: str = "chat") -> dict:
    row = {
        "id": f"{i:016x}",
        "showId": KEY,
        "username": "" if source == "player" else user,
        "body": body,
        "rawText": f"{user} {body}".strip(),
        "capturedAt": at.isoformat().replace("+00:00", "Z"),
        "seq": i,
    }
    return {**row, "source": "player"} if source == "player" else row


def _batch(rows, **show) -> dict:
    return {
        "version": 1,
        "show": {"id": KEY, "startedAt": KEY, "url": "/user/shop_channel", **show},
        "rows": rows,
    }


def _laptop_rows(now: datetime) -> list[dict]:
    rows = []
    for i, (ago, user, body, *source) in enumerate(LINES, start=1):
        rows.append(_row(i, now - ago, body, user, *(source or ["chat"])))
    return rows


def test_the_laptop_keeps_sending_one_key_and_each_line_lands_in_its_own_show(app, client, shop):
    _quiet(app, shop)
    _two_shows(shop)
    now = shop["now"]
    with app.app_context():
        from app.models import Organization

        _, key = create_device(db.session.get(Organization, shop["org"]), "show laptop")
        db.session.commit()
    auth = {"Authorization": "Bearer " + key}
    first = _batch(_laptop_rows(now))
    r = client.post("/api/ingest", json=first, headers=auth)
    assert r.status_code == 200 and r.get_json()["accepted"] == len(LINES)
    rec = _parts(app)[0]
    with app.app_context():
        db.session.get(Show, rec).started_at = now - (5 * H + 10 * M)
        db.session.commit()
    run(app, rec)
    _read(app, shop)
    rec_, part = _parts(app)
    assert rec_ == rec
    with app.app_context():
        counts = {s: len(_bodies(s)) for s in (rec, part)}
    assert counts == {rec: 3, part: 3}

    # A lost reply: the same batch again. Nothing lands twice, in either half.
    r = client.post("/api/ingest", json=first, headers=auth)
    assert r.status_code == 200
    assert (r.get_json()["accepted"], r.get_json()["duplicates"]) == (0, len(LINES))
    with app.app_context():
        assert {s: len(_bodies(s)) for s in (rec, part)} == counts
        db.session.query(Job).delete()
        db.session.commit()

    # A new line of the late show: in the late show, and the late show is read.
    r = client.post(
        "/api/ingest", json=_batch([_row(50, now - 5 * M, "one more please")]), headers=auth
    )
    assert r.status_code == 200 and r.get_json()["accepted"] == 1
    # An early-show line that never got through before: in the early show.
    r = client.post(
        "/api/ingest",
        json=_batch([_row(51, now - (3 * H + 40 * M), "late hello from the early show")]),
        headers=auth,
    )
    assert r.status_code == 200 and r.get_json()["accepted"] == 1
    with app.app_context():
        assert _bodies(part)[-1] == "one more please"
        assert "late hello from the early show" in _bodies(rec)
        parsed = sorted(
            j.payload["show_id"]
            for j in db.session.scalars(select(Job).where(Job.kind == "parse_show"))
        )
        assert parsed == sorted([rec, part])
        # The late show is where chat last arrived (the Live pill): a late line of the early
        # show does not make the early show live again.
        assert db.session.get(Show, part).updated_at > db.session.get(Show, rec).updated_at

    # The page moved on: said of the show the laptop is on now.
    r = client.post("/api/ingest", json=_batch([], movedOnAt=now.isoformat()), headers=auth)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(Show, part).moved_on_at is not None
        assert db.session.get(Show, rec).moved_on_at is None

    # The panel asks how the show sells by the laptop's key: the late show's answer.
    with app.app_context():
        db.session.add(ShowSaleMode(show_id=part, mode="listing"))
        db.session.commit()
    r = client.get(f"/api/ingest/stock?show={KEY}", headers=auth)
    assert r.status_code == 200 and r.get_json()["show"]["saleMode"] == "listing"


def test_the_shelves_and_selling_way_in_use_carry_on_in_the_second_show(app, shop):
    """The host set the shelves while recording: after the split the late show's lines are
    read with the same layout and selling way (a shelf code must not stop naming a product
    mid-show)."""
    _quiet(app, shop)
    _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    with app.app_context():
        db.session.add(ShowShelf(show_id=rec, code="B4", sku_id=shop["red"]))
        db.session.add(ShowSaleMode(show_id=rec, mode="listing"))
        db.session.commit()
    run(app, rec)
    _read(app, shop)
    part = _parts(app)[1]
    with app.app_context():
        assert [
            (s.code, s.sku_id)
            for s in db.session.scalars(select(ShowShelf).where(ShowShelf.show_id == part))
        ] == [("B4", shop["red"])]
        from app.services import sale_mode

        assert sale_mode.of_show(db.session.get(Show, part)) == ("listing", True)
        assert sale_mode.of_show(db.session.get(Show, rec)) == ("listing", True)


def test_deleting_one_half_waits_while_the_other_half_is_still_recording(app, shop):
    """The laptop uploads both halves under one key: the early half's data is not deleted
    while the late half still gets chat, and once a half's data is deleted, a late upload
    of the recording is dropped like any deleted recording's."""
    _quiet(app, shop)
    _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    run(app, rec)
    _read(app, shop)
    rec_, part = _parts(app)
    now = shop["now"]
    with app.app_context():
        db.session.get(Show, rec).updated_at = now - 3 * H  # its own chat stopped long ago
        db.session.get(Show, part).updated_at = now - 2 * M  # the laptop is on this one
        db.session.commit()
        blockers = recordings.preview(db.session.get(Show, rec))["blockers"]
        assert any("may still be recording" in b for b in blockers)
        db.session.get(Show, part).updated_at = now - H
        db.session.commit()
        assert not any(
            "may still be recording" in b
            for b in recordings.preview(db.session.get(Show, rec))["blockers"]
        )
        owner = User(
            organization_id=shop["org"],
            email="owner@shop.test",
            name="Olive",
            password_hash="x",
            role=Role.owner,
            session_token="t" * 32,
        )
        db.session.add(owner)
        db.session.commit()
        part_key = db.session.get(Show, part).show_key
        recordings.delete_laptop_data(shop["org"], part, user=owner)
        db.session.commit()
        assert recordings.was_deleted(shop["org"], part_key)
        assert recordings.was_deleted(shop["org"], KEY)
        from app.models import Organization

        dev, _ = create_device(db.session.get(Organization, shop["org"]), "show laptop")
        db.session.commit()
        before = db.session.scalar(select(func.count()).select_from(ChatMessage))
        out = ingest.ingest(dev, _batch([_row(60, now - 3 * M, "still here")]))
        assert out.deleted
        assert db.session.scalar(select(func.count()).select_from(ChatMessage)) == before


def test_a_part_whose_data_was_deleted_takes_no_more_lines(app, shop):
    """Even with only the part's own key deleted, a line for it is dropped, never stored in
    the early half instead."""
    _quiet(app, shop)
    _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    run(app, rec)
    _read(app, shop)
    rec_, part = _parts(app)
    now = shop["now"]
    with app.app_context():
        part_key = db.session.get(Show, part).show_key
        db.session.add(DeletedRecording(organization_id=shop["org"], show_key=part_key))
        from app.models import Organization

        dev, _ = create_device(db.session.get(Organization, shop["org"]), "show laptop")
        db.session.commit()
        before = {s: len(_bodies(s)) for s in (rec, part)}
        out = ingest.ingest(dev, _batch([_row(60, now - 3 * M, "still here")]))
        assert out.deleted
        early = ingest.ingest(dev, _batch([_row(61, now - 4 * H, "an early line")]))
        assert early.accepted == 1
        after = {s: len(_bodies(s)) for s in (rec, part)}
        assert after == {rec: before[rec] + 1, part: before[part]}


# ---------- when it is not split ----------


def test_a_laptop_that_starts_early_on_the_second_shows_page_is_not_split(app, shop):
    """Twenty-five minutes before the late show, on the late show's own page: two of the
    early show's buyers chat there while the early show is still on air. The recording is
    the late show's (its address says so): nothing is split off for the early show."""
    _quiet(app, shop)
    now, fake = shop["now"], shop["fake"]
    early = fake.add_show(title="Early show", start=now - 4 * H, ended=now - 2 * H - 10 * M)["id"]
    late = fake.add_show(title="Late show", start=now - 2 * H, ended=now - 10 * M)["id"]
    for buyer, ago in (("kat.c", 3 * H), ("lani808", 150 * M)):
        fake.add_order(
            show_id=early, buyer=buyer, items=[{"title": "Red bundle", "price_cents": 2500}],
            created=now - ago,
        )  # fmt: skip
    lines = [
        (2 * H + 25 * M, "host_shop", "starting in a bit"),
        (2 * H + 20 * M, "kat.c", "hi again"),
        (2 * H + 15 * M, "lani808", "back for more"),
        (90 * M, *win("pine.owl", LOT, "7.00")),
        (80 * M, *win("lark.hill", LOT, "9.00")),
    ]
    rec = _recording(
        app, shop, lines, began=2 * H + 25 * M, address=f"https://www.whatnot.com/live/{late}"
    )
    run(app, rec)
    _read(app, shop, early, late)
    _read(app, shop)
    assert _parts(app) == [rec]
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_show_id == _ws(late).id


def test_a_show_whatnot_never_ended_does_not_split_the_next_shows_recording(app, shop):
    """The early show was left live on Whatnot (12 hours count as on air). The laptop only
    recorded the late show, from half an hour before it began: one show."""
    _quiet(app, shop)
    now, fake = shop["now"], shop["fake"]
    fake.add_show(title="Never ended", status="live", start=now - 10 * H)
    fake.add_show(title="Late show", start=now - 2 * H, ended=now - 10 * M)
    lines = [
        (2 * H + 30 * M, "host_shop", "setting up"),
        (2 * H + 20 * M, "chatter", "first!"),
        (90 * M, *win("pine.owl", LOT, "7.00")),
        (80 * M, *win("lark.hill", LOT, "9.00")),
    ]
    rec = _recording(app, shop, lines, began=2 * H + 35 * M)
    run(app, rec)
    _read(app, shop)
    _read(app, shop)
    assert _parts(app) == [rec]
    assert _sales(app) == {"pine.owl": [rec], "lark.hill": [rec]}


def test_two_laptops_on_one_show_stay_two_recordings_and_neither_splits(app, shop):
    _quiet(app, shop)
    _early, late = _two_shows(shop)
    lines = [(100 * M, "host_shop", "hello"), (90 * M, *win("pine.owl", LOT, "7.00"))]
    one = _recording(
        app, shop, lines, began=2 * H, key="laptop-one",
        address=f"https://www.whatnot.com/live/{late}",
    )  # fmt: skip
    two = _recording(app, shop, lines, began=2 * H, key="laptop-two")
    run(app, one)
    run(app, two)
    _read(app, shop)
    assert _parts(app, "laptop-one") == [one] and _parts(app, "laptop-two") == [two]


def test_a_recording_whose_address_names_the_second_show_is_never_split(app, shop):
    """Its own address names the late show: the early show's wins in it do not make the app
    take part of it away (the address is the laptop's first page; a person decides)."""
    _quiet(app, shop)
    _early, late = _two_shows(shop)
    rec = _recording(
        app, shop, LINES, began=5 * H + 10 * M, address=f"https://www.whatnot.com/live/{late}"
    )
    run(app, rec)
    _read(app, shop)
    _read(app, shop)
    assert _parts(app) == [rec]
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_show_id == _ws(late).id


def test_a_person_matched_recording_is_never_split(app, shop):
    """A person matched it to the late show: their word stands, nothing is split off."""
    _quiet(app, shop)
    _early, late = _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    with app.app_context():
        sync.tie(db.session.get(Show, rec), _ws_id(app, late), "manual")
        db.session.commit()
    run(app, rec)
    _read(app, shop)
    _read(app, shop)
    assert _parts(app) == [rec]
    with app.app_context():
        r = db.session.get(Show, rec)
        assert r.whatnot_link == "manual" and r.whatnot_show_id == _ws(late).id


def _ws_id(app, whatnot_id: str) -> int:
    with app.app_context():
        client = client_for(db.session.scalar(select(WhatnotConnection)).organization_id)
        ws = sync.sync_show(
            db.session.scalar(select(WhatnotConnection)).organization_id, client, whatnot_id
        )
        db.session.commit()
        return ws.id


def test_a_chat_being_read_right_now_waits_for_the_next_read(app, shop, monkeypatch):
    _quiet(app, shop)
    _two_shows(shop)
    rec = _recording(app, shop, LINES, began=5 * H + 10 * M)
    run(app, rec)
    real = pipeline._lock_show
    monkeypatch.setattr(pipeline, "_lock_show", lambda show_id, *, wait=False: False)
    _read(app, shop)  # never raises: the reading of Whatnot's shows goes on
    assert _parts(app) == [rec]
    assert _open_items(app) == []
    monkeypatch.setattr(pipeline, "_lock_show", real)
    _read(app, shop)
    assert len(_parts(app)) == 2
