"""One Sales Ops show per Whatnot show (services/show_merge.py).

A Whatnot show nobody recorded gets a show of its own once it has been live a while
(`sync.ensure_recording`, "made"). When a laptop then starts recording the same show,
the made show is folded into the laptop's recording: one show, one Order per buyer, the
lots, giveaways and questions with it, and not a unit of stock moved. A split that already
happened is merged by the owner, by hand.

Every buyer name here is made up. RED starts with 3 on the shelf (the `world` fixture).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text

from app.db import db
from app.models import (
    AuditLog,
    ChatMessage,
    InventoryMovement,
    Order,
    OrderStatus,
    ReviewItem,
    Sale,
    Show,
    ShowSaleMode,
    ShowShelf,
    WhatnotConnection,
    WhatnotLot,
    WhatnotShow,
)
from app.services import (
    activity,
    dashboard,
    order_slips,
    orders,
    pipeline,
    sale_mode,
    show_merge,
)
from app.services import giveaways as giveaway_service
from app.services.whatnot import client_for, shipping, sync
from app.services.whatnot import orders as whatnot_orders
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import LOT, win

shop = wo.shop  # the world's shop connected to Whatnot


def _quiet(app, shop):
    """The shop's own recording and its Whatnot show are two days old: nobody is recording
    the show that goes live below, yet."""
    now, fake = shop["now"], shop["fake"]
    fake.shows[shop["wshow"]]["scheduled_start_at"] = (now - timedelta(days=2, hours=2)).isoformat()
    fake.shows[shop["wshow"]]["ended_at"] = (now - timedelta(days=2)).isoformat()
    with app.app_context():
        db.session.get(Show, shop["show"]).started_at = now - timedelta(days=2, hours=1)
        db.session.query(ChatMessage).update({"captured_at": now - timedelta(days=2)})
        db.session.commit()


def _live(shop) -> str:
    """A show live on Whatnot for forty minutes."""
    return shop["fake"].add_show(
        title="Late start", status="live", start=shop["now"] - timedelta(minutes=40)
    )["id"]


def _ws(live_id: str) -> WhatnotShow:
    return db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == live_id))


def _read(app, shop, live_id):
    """The timer: read the shows (which links recordings), then this show's orders."""
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        sync.sync_show_orders(shop["org"], client, _ws(live_id))
        db.session.commit()


def _order(shop, live_id, buyer, title, price=2500, minutes=35, **kw):
    return shop["fake"].add_order(
        show_id=live_id,
        buyer=buyer,
        items=[{"title": title, "price_cents": price}],
        created=shop["now"] - timedelta(minutes=minutes),
        **kw,
    )


def _made(app, shop, live_id) -> int:
    """Nobody recorded the show after the grace: the app makes it a show and applies its
    orders there (what the "auto" apply job does)."""
    _read(app, shop, live_id)
    with app.app_context():
        made = sync.ensure_recording(_ws(live_id))
        assert made is not None and made.whatnot_link == "made"
        db.session.commit()
        whatnot_orders.apply(made)
        db.session.commit()
        return made.id


def _laptop(app, shop, live_id, *, address: bool, lines=(), key="late-laptop") -> int:
    """A laptop that starts recording twenty minutes in, on the show's own page when
    `address` (else its address names no show, and only the clock ties it)."""
    now = shop["now"]
    with app.app_context():
        rec = Show(
            organization_id=shop["org"],
            show_key=key,
            started_at=now - timedelta(minutes=20),
            show_date=now.date(),
            source_url=f"https://www.whatnot.com/live/{live_id}" if address else None,
        )
        db.session.add(rec)
        db.session.flush()
        n = db.session.scalar(select(func.count()).select_from(ChatMessage)) or 0
        for i, (user, body, *source) in enumerate(
            [("host_shop", "welcome back everyone"), *lines], start=n + 1
        ):
            db.session.add(
                ChatMessage(
                    show_id=rec.id,
                    client_id=f"{i:016x}",
                    seq=i,
                    username=user,
                    body=body,
                    raw_text=f"{user} {body}".strip(),
                    source=source[0] if source else "chat",
                    captured_at=now - timedelta(minutes=10) + timedelta(seconds=i),
                )
            )
        db.session.commit()
        return rec.id


def _shows_of(app, live_id) -> list[int]:
    with app.app_context():
        return [s.id for s in sync.recordings_of(_ws(live_id))]


def _ledger(app):
    with app.app_context():
        return [
            (m.sku_id, m.kind.value, m.quantity, m.source_type, m.source_id)
            for m in db.session.scalars(select(InventoryMovement).order_by(InventoryMovement.id))
        ]


def _orders(app):
    """(buyer, show id, status, sale ids) of every Order."""
    with app.app_context():
        return sorted(
            (o.buyer_username, o.show_id, o.status.value, sorted(s.id for s in o.sales))
            for o in db.session.scalars(select(Order))
        )


def _merge(app, live_id, **kw):
    with app.app_context():
        out = show_merge.merge(_ws(live_id), **kw)
        db.session.commit()
        return out


# ---------- the made show folds into a late laptop's recording ----------


def test_a_laptop_that_starts_late_takes_over_the_made_show(app, shop):
    """2026-11-15 in miniature: no laptop for the first part of the show, so the app made a
    show and put the first orders there; the laptop started later on the show's own page.
    One show is left: every sale, Order and question in the recording, nothing in stock
    moved, and the buyer who won before and after the laptop started has ONE Order."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle", minutes=35)
    made = _made(app, shop, live)
    assert level(app, shop["red"]) == (2, 0)  # paid at the win on Whatnot: off the shelf
    ledger = _ledger(app)

    rec = _laptop(app, shop, live, address=True, lines=[win("lani808", "Red bundle")])
    _read(app, shop, live)  # the show list is read: the recording is matched, and folded

    assert _shows_of(app, live) == [rec]
    with app.app_context():
        assert db.session.get(Show, made) is None
        assert {s.show_id for s in db.session.scalars(select(Sale))} == {rec}
        assert {r.show_id for r in db.session.scalars(select(ReviewItem))} <= {rec, None}
        merged = db.session.scalar(select(AuditLog).where(AuditLog.action == "show.merged"))
        assert merged is not None and merged.actor_type == "system"
        assert merged.details["sales"] == 1 and merged.details["orders"] == 1
        words = activity.describe(merged.action, merged.details)
        assert "made" not in words and "show_id" not in words
        assert words.startswith("Merged ")
    assert _ledger(app) == ledger and level(app, shop["red"]) == (2, 0)

    # The laptop's own win, and a later order of the same buyer: all in the one show.
    run(app, rec)
    _order(shop, live, "kat.c", LOT, 700, minutes=5)
    _read(app, shop, live)
    with app.app_context():
        whatnot_orders.apply(db.session.get(Show, rec))
        db.session.commit()
    with app.app_context():
        assert {s.show_id for s in db.session.scalars(select(Sale))} == {rec}
    kat = [o for o in _orders(app) if o[0] == "kat.c"]
    assert len(kat) == 1 and kat[0][1] == rec and len(kat[0][3]) == 2
    assert [o[0] for o in _orders(app)] == ["kat.c", "lani808"]

    # Nothing left to fold: running it again changes nothing.
    before = (_orders(app), _ledger(app))
    with app.app_context():
        assert show_merge.fold_made(_ws(live)) is None
        db.session.commit()
    assert (_orders(app), _ledger(app)) == before


def test_a_recording_tied_only_by_the_clock_is_not_folded_on_its_own(app, shop):
    """A recording whose address names no show may be matched to another show later (the
    clock is re-decided), so the app never moves Whatnot's sales into it by itself: the
    show page offers the merge to a person instead."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    made = _made(app, shop, live)
    rec = _laptop(app, shop, live, address=False)
    _read(app, shop, live)
    assert sorted(_shows_of(app, live)) == sorted([made, rec])
    with app.app_context():
        assert show_merge.split_of(db.session.get(Show, made)) == {
            "keeper": rec,
            "shows": [made, rec],
        }


def test_the_show_orders_go_to_does_not_change_as_sales_counts_change(app, shop):
    """Which app show a new order's sale is recorded in never depends on "whichever holds
    most sales right now" (A2), and never is a recording tied by the clock alone while the
    show made from Whatnot's record is there: the clock may be re-decided, and the sales
    would leave the show with it."""
    _quiet(app, shop)
    live = _live(shop)
    for buyer in ("kat.c", "moss.tea", "rook.b"):
        _order(shop, live, buyer, LOT, 700)
    made = _made(app, shop, live)  # three sales, before any laptop
    rec = _laptop(
        app,
        shop,
        live,
        address=False,
        lines=[win(b, "Blue bundle", "7.00") for b in ("ash.v", "fen.q", "gil.r", "hal.s")],
    )
    _read(app, shop, live)
    run(app, rec)  # the recording now holds more sales than the made show
    assert _shows_of(app, live) == [made, rec]
    _order(shop, live, "pine.owl", LOT, 700, minutes=3)
    _read(app, shop, live)
    with app.app_context():
        assert sync.ensure_recording(_ws(live)).id == made
        whatnot_orders.apply(db.session.get(Show, made))
        db.session.commit()
        new = db.session.scalar(select(Sale).where(Sale.buyer_username == "pine.owl"))
        assert new.show_id == made
    assert _shows_of(app, live) == [made, rec]


def test_a_sale_from_whatnot_never_goes_into_a_recording_the_clock_may_untie(app, shop):
    """A recording made on another stream's page is tied to the show by the clock while the
    show has only its made show. A new order's sale goes into the made show, so when the
    show's own laptop starts and the other recording is untied, the sale is still in the
    show (it folds into the laptop's recording with the rest)."""
    _quiet(app, shop)
    live = _live(shop)
    for buyer in ("kat.c", "moss.tea"):
        _order(shop, live, buyer, LOT, 700)
    _made(app, shop, live)
    now = shop["now"]
    with app.app_context():
        foreign = Show(
            organization_id=shop["org"],
            show_key="other-stream",
            started_at=now - timedelta(minutes=20),
            show_date=now.date(),
            source_url="https://www.whatnot.com/live/00000000-0000-4000-8000-00000000abcd",
        )
        db.session.add(foreign)
        db.session.flush()
        db.session.add(
            ChatMessage(
                show_id=foreign.id,
                client_id="f" * 16,
                seq=1,
                username="someone",
                body="hi",
                raw_text="someone hi",
                source="chat",
                captured_at=now - timedelta(minutes=1),
            )
        )
        db.session.commit()
        foreign_id = foreign.id
    _read(app, shop, live)
    assert foreign_id in _shows_of(app, live)  # tied by the clock
    _order(shop, live, "pine.owl", LOT, 700, minutes=3)
    _read(app, shop, live)
    with app.app_context():
        show = sync.ensure_recording(_ws(live))
        whatnot_orders.apply(show)
        db.session.commit()
    own = _laptop(app, shop, live, address=True, key="own-laptop")
    _read(app, shop, live)
    assert _shows_of(app, live) == [own]
    with app.app_context():
        assert db.session.get(Show, foreign_id).whatnot_show_id is None
        pine = db.session.scalar(select(Sale).where(Sale.buyer_username == "pine.owl"))
        assert pine.show_id == own


def test_a_recording_that_learns_its_address_later_takes_over_the_made_show(app, shop):
    """The laptop's first batches carried no address, so the clock tied it; a later batch
    names the show's page. The next read of the shows folds the made show into it."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    made = _made(app, shop, live)
    rec = _laptop(app, shop, live, address=False)
    _read(app, shop, live)
    assert _shows_of(app, live) == [made, rec]
    with app.app_context():
        db.session.get(Show, rec).source_url = f"https://www.whatnot.com/live/{live}"
        db.session.commit()
    _read(app, shop, live)
    assert _shows_of(app, live) == [rec]
    with app.app_context():
        assert {s.show_id for s in db.session.scalars(select(Sale))} == {rec}


def test_whatnots_orders_applied_while_the_shows_fold_wait_and_go_to_the_show_that_stays(
    app, shop, monkeypatch
):
    """Whatnot's orders are being applied to the made show when the laptop's recording is
    matched by its address and the made show folds into it. The apply never records a sale
    in a show whose parse lock it does not hold (the parser may be making sales there): it
    starts again, and the sale lands in the recording, under its lock."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    made = _made(app, shop, live)
    rec = _laptop(app, shop, live, address=True)  # recording, not matched yet
    _order(shop, live, "pine.owl", LOT, 700, minutes=3)
    with app.app_context():
        sync.sync_show_orders(shop["org"], client_for(shop["org"]), _ws(live))
        conn = db.session.scalar(select(WhatnotConnection))
        conn.orders_mode = "auto"
        db.session.commit()

    real = pipeline._lock_show
    state = {"raced": False}

    def racing(show_id, *, wait=False):
        if not state["raced"]:
            state["raced"] = True
            # Meanwhile, in another transaction: the shows are read, the recording is
            # matched by its address, and the made show folds into it.
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
        whatnot_orders.apply_show_job({"org_id": shop["org"], "whatnot_show_id": live})
    assert state["raced"] and _shows_of(app, live) == [rec]
    assert held == [(rec, True)]
    with app.app_context():
        assert db.session.get(Show, made) is None
        pine = db.session.scalar(select(Sale).where(Sale.buyer_username == "pine.owl"))
        assert pine.show_id == rec


# ---------- the owner merges a split show by hand ----------


def _split(app, shop):
    """A made show with one paid sale and a laptop recording tied by the clock: split."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    made = _made(app, shop, live)
    rec = _laptop(app, shop, live, address=False, lines=[win("lani808", "Red bundle")])
    _read(app, shop, live)
    run(app, rec)
    return live, made, rec


def test_the_owner_merges_a_split_show_once_and_a_second_press_changes_nothing(
    app, client, shop, sign_in
):
    live, made, rec = _split(app, shop)
    ledger, levels = _ledger(app), level(app, shop["red"])
    h = sign_in(shop["org"], "manager", email="m@x.test")
    assert client.get(f"/api/shows/{made}/merge").status_code == 403
    assert (
        client.post(f"/api/shows/{made}/merge", json={"confirm": "merge"}, headers=h).status_code
        == 403
    )
    h = sign_in(shop["org"], "owner")
    seen = client.get(f"/api/shows/{made}").get_json()
    assert seen["split"] == {"keeper": rec, "shows": [made, rec]}
    preview = client.get(f"/api/shows/{made}/merge").get_json()
    assert preview["keeper"] == rec and preview["folded"] == [made]
    assert preview["sales"] == 1 and preview["orders"] == 1 and preview["blockers"] == []
    assert client.post(f"/api/shows/{made}/merge", json={}, headers=h).status_code == 400
    done = client.post(f"/api/shows/{made}/merge", json={"confirm": "merge"}, headers=h)
    assert done.status_code == 200, done.get_json()
    assert done.get_json()["showId"] == rec
    assert _shows_of(app, live) == [rec]
    assert _ledger(app) == ledger and level(app, shop["red"]) == levels
    with app.app_context():
        assert db.session.get(Show, rec).whatnot_link == "manual"  # a person said so
        line = db.session.scalar(select(AuditLog).where(AuditLog.action == "show.merged"))
        assert line.actor_type == "user"
    again = client.post(f"/api/shows/{rec}/merge", json={"confirm": "merge"}, headers=h)
    assert again.status_code == 409
    assert "already" in again.get_json()["error"]
    assert client.get(f"/api/shows/{rec}").get_json()["split"] is None


def test_a_buyer_with_a_box_being_packed_in_one_half_stops_the_merge_until_it_is_settled(app, shop):
    """The same buyer has an Order in both halves. While one of them is being picked or
    packed, the merge would put unpicked items in that box (or call them picked), so it
    is refused and nothing changes. Back out of picking, the two Orders become one, the
    notes kept."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    made = _made(app, shop, live)
    rec = _laptop(app, shop, live, address=False, lines=[win("kat.c", "Blue bundle", "7.00")])
    _read(app, shop, live)
    run(app, rec)
    with app.app_context():
        both = db.session.scalars(
            select(Order).where(Order.buyer_username == "kat.c").order_by(Order.id)
        ).all()
        assert [o.show_id for o in both] == [made, rec]
        both[0].notes = "gift wrap"
        both[1].status = OrderStatus.picking
        both[1].picked_at = datetime.now(UTC)
        db.session.commit()
    before = _orders(app)
    with app.app_context():
        preview = show_merge.preview(_ws(live))
        assert len(preview["blockers"]) == 1 and "kat.c" in preview["blockers"][0]
    try:
        _merge(app, live)
        raise AssertionError("merged while a box was being picked")
    except show_merge.MergeError as e:
        assert "picked" in str(e)
    assert _orders(app) == before and sorted(_shows_of(app, live)) == sorted([made, rec])

    with app.app_context():  # a person takes it back out of picking
        o = db.session.scalar(select(Order).where(Order.show_id == rec))
        o.status, o.picked_at = OrderStatus.open, None
        db.session.commit()
    _merge(app, live)
    kat = [o for o in _orders(app) if o[0] == "kat.c"]
    assert len(kat) == 1 and kat[0][1] == rec and len(kat[0][3]) == 2
    with app.app_context():
        kept = db.session.scalar(select(Order).where(Order.buyer_username == "kat.c"))
        assert kept.notes == "gift wrap" and kept.status is OrderStatus.open


def test_two_laptop_recordings_stay_two_shows(app, shop):
    """Two laptops that both recorded chat are two recordings by design: only a show with
    no laptop chat is ever folded."""
    _quiet(app, shop)
    live = _live(shop)
    a = _laptop(app, shop, live, address=True, key="laptop-a")
    b = _laptop(app, shop, live, address=True, key="laptop-b")
    _read(app, shop, live)
    assert sorted(_shows_of(app, live)) == sorted([a, b])
    with app.app_context():
        assert show_merge.preview(_ws(live))["folded"] == []
        # Nothing to merge: the show page shows no split card.
        assert show_merge.split_of(db.session.get(Show, a)) is None
    try:
        _merge(app, live)
        raise AssertionError("merged two laptop recordings")
    except show_merge.MergeError as e:
        assert "already" in str(e) or "laptop" in str(e)


# ---------- what reads a show sees one show (A4, A12, A15, A17, A20) ----------


def test_after_the_merge_slips_giveaways_pick_list_and_tiles_are_one_show(app, shop):
    """Lots and the giveaway were read while the made show held the show; after the merge
    the buyer has one slip with every lot and the giveaway, the pick list covers both
    wins, and the show's gifts are counted once."""
    _quiet(app, shop)
    live = _live(shop)
    fake = shop["fake"]
    first = _order(shop, live, "kat.c", "Red bundle", minutes=35)
    gift = _order(shop, live, "kat.c", "Mystery gift", 0, minutes=34, transaction_type="giveaway")
    made = _made(app, shop, live)
    fake.add_shipment(
        recipient="kat.c",
        order_ids=[first["id"], gift["id"]],
        created=shop["now"] - timedelta(minutes=30),
    )
    with app.app_context():
        shipping.read_show(shop["org"], client_for(shop["org"]), _ws(live))
        db.session.commit()
        assert {lot.show_id for lot in db.session.scalars(select(WhatnotLot))} == {made}
    rec = _laptop(app, shop, live, address=True)
    _read(app, shop, live)
    assert _shows_of(app, live) == [rec]
    with app.app_context():
        show = db.session.get(Show, rec)
        assert {lot.show_id for lot in db.session.scalars(select(WhatnotLot))} == {rec}
        doc = order_slips.build(show)
        assert [s["username"] for s in doc["slips"]] == ["kat.c"]
        kinds = [r["kind"] for r in doc["slips"][0]["rows"]]
        assert kinds.count("sale") == 1 and kinds.count("gift") == 1
        assert [gv["buyer"] for gv in giveaway_service.for_show(show)] == ["kat.c"]
        picks = orders.pick_list(show)
        assert [(p["code"], p["quantity"], p["orders"]) for p in picks] == [("RED", 1, 1)]
        (tile,) = dashboard.tiles_for([show])
        assert tile["gifts"] == 1
        # Shipping: the buyer's one Order, and the same buyers as the slips (V9, A15).
        view = shipping.packages(show)
        assert [b["buyer"] for b in view["buyers"]] == ["kat.c"]
        (kat,) = view["buyers"]
        assert kat["orderId"] == doc["slips"][0]["orderId"]
        assert db.session.scalar(select(func.count()).select_from(Order)) == 1
        db.session.rollback()


def test_a_second_laptop_never_prints_a_chat_gift_beside_whatnots_giveaways(app, shop):
    """Two laptops recorded the show; Whatnot's giveaways print on the first recording's
    slips. A host's "won the giveaway" line in the second recording must not print the
    same gift again there, and the second recording's tile does not count it again (A40,
    A17)."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Mystery gift", 0, minutes=15, transaction_type="giveaway")
    a = _laptop(app, shop, live, address=True, key="laptop-a")
    b = _laptop(
        app,
        shop,
        live,
        address=True,
        key="laptop-b",
        lines=[("host_shop", "kat.c won the giveaway!")],
    )
    _read(app, shop, live)
    run(app, b)
    with app.app_context():
        assert sync.recordings_of(_ws(live))[0].id == a
        second = db.session.get(Show, b)
        rows = [r for s in order_slips.build(second)["slips"] for r in s["rows"]]
        assert [r for r in rows if r["kind"] == "gift"] == []
        tiles = {
            t["showId"]: t["gifts"] for t in dashboard.tiles_for([db.session.get(Show, a), second])
        }
        assert tiles == {a: 1, b: 0}


def test_the_shows_selling_way_and_shelves_survive_the_fold(app, shop):
    """The made show is the oldest recording, so its selling way and shelf layout are the
    show's: the recording keeps them after the fold."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    made = _made(app, shop, live)
    with app.app_context():
        db.session.add(ShowSaleMode(show_id=made, mode="listing"))
        db.session.add(ShowShelf(show_id=made, code="A1", sku_id=shop["red"]))
        db.session.commit()
    rec = _laptop(app, shop, live, address=True)
    _read(app, shop, live)
    assert _shows_of(app, live) == [rec]
    with app.app_context():
        show = db.session.get(Show, rec)
        assert sale_mode.of_show(show) == ("listing", True)
        assert [(r.code, r.sku_id) for r in db.session.scalars(select(ShowShelf))] == [
            ("A1", shop["red"])
        ]


def test_two_shows_with_no_laptop_chat_merge_into_the_oldest(app, shop):
    """Both laptops' data deleted (each kept as a show for Whatnot's sales): two shows with
    no chat. The owner's merge keeps the oldest and moves the other's sales and Orders."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    _order(shop, live, "moss.tea", LOT, 700)
    made = _made(app, shop, live)
    ledger = _ledger(app)
    with app.app_context():
        ws = _ws(live)
        other = Show(
            organization_id=shop["org"],
            show_key="kept-for-whatnot",
            started_at=shop["now"] - timedelta(minutes=20),
            show_date=shop["now"].date(),
            whatnot_show_id=ws.id,
            whatnot_link="made",
        )
        db.session.add(other)
        db.session.flush()
        moss = db.session.scalar(select(Sale).where(Sale.buyer_username == "moss.tea"))
        old = moss.order
        moss.show_id, moss.order = other.id, None
        db.session.flush()
        db.session.delete(old)
        db.session.flush()
        orders.attach(moss)
        db.session.commit()
        other_id = other.id
    assert _shows_of(app, live) == [made, other_id]
    out = _merge(app, live)
    assert out["showId"] == made and out["from"] == [other_id]
    assert _shows_of(app, live) == [made]
    assert {o[1] for o in _orders(app)} == {made} and len(_orders(app)) == 2
    assert _ledger(app) == ledger


def test_a_person_matching_the_recording_merges_nothing_the_owner_does(app, client, shop, sign_in):
    """A recording the clock tied, then a manager says by hand which Whatnot show it is.
    Matching never merges (a merge cannot be undone, and is the owner's, with its preview):
    the recording comes first now, the made show stays until the owner merges, and the
    merge is the owner's in the activity log."""
    live, made, rec = _split(app, shop)
    h = sign_in(shop["org"], "manager", email="m@x.test")
    with app.app_context():
        ws_id = _ws(live).id
    r = client.post(f"/api/shows/{rec}/whatnot/link", json={"whatnotShowId": ws_id}, headers=h)
    assert r.status_code == 200, r.get_json()
    _read(app, shop, live)  # nor does the next read of the shows
    assert _shows_of(app, live) == [rec, made]
    with app.app_context():
        assert db.session.get(Show, made) is not None
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "show.merged")) is None
    h = sign_in(shop["org"], "owner")
    done = client.post(f"/api/shows/{rec}/merge", json={"confirm": "merge"}, headers=h)
    assert done.status_code == 200, done.get_json()
    assert _shows_of(app, live) == [rec]
    with app.app_context():
        line = db.session.scalar(select(AuditLog).where(AuditLog.action == "show.merged"))
        assert line.actor_type == "user"


def test_matching_a_show_with_no_chat_keeps_it(app, client, shop, sign_in):
    """A recording whose laptop data was deleted (no chat, kept for Whatnot's sales) is
    matched by hand to a Whatnot show its laptop's recording already holds. The show being
    matched is never folded away under the person, then or at the next read."""
    _quiet(app, shop)
    live = _live(shop)
    _order(shop, live, "kat.c", "Red bundle")
    laptop = _laptop(app, shop, live, address=True, key="laptop-a")
    _read(app, shop, live)
    now = shop["now"]
    with app.app_context():
        empty = Show(
            organization_id=shop["org"],
            show_key="data-deleted",
            started_at=now - timedelta(minutes=30),
            show_date=now.date(),
        )
        db.session.add(empty)
        db.session.commit()
        empty_id, ws_id = empty.id, _ws(live).id
    h = sign_in(shop["org"], "manager", email="m@x.test")
    r = client.post(f"/api/shows/{empty_id}/whatnot/link", json={"whatnotShowId": ws_id}, headers=h)
    assert r.status_code == 200, r.get_json()
    _read(app, shop, live)
    with app.app_context():
        assert db.session.get(Show, empty_id) is not None
    assert sorted(_shows_of(app, live)) == sorted([laptop, empty_id])
    assert client.get(f"/api/shows/{empty_id}", headers=h).status_code == 200


def test_the_merge_waits_for_nobody_while_the_shows_chat_is_being_read(app, client, shop, sign_in):
    """The merge takes the parse lock of every half (as Whatnot's apply does). While the
    parser holds one, the owner is told to try again and nothing changes."""
    live, made, rec = _split(app, shop)
    h = sign_in(shop["org"], "owner")
    before = (_orders(app), _ledger(app))
    with app.app_context():
        other = db.engine.connect()
        held = other.begin()
        other.execute(
            text("SELECT pg_advisory_xact_lock(:c, :s)"), {"c": pipeline.PARSE_LOCK, "s": rec}
        )
    try:
        r = client.post(f"/api/shows/{made}/merge", json={"confirm": "merge"}, headers=h)
        assert r.status_code == 409 and "being read" in r.get_json()["error"]
        assert (_orders(app), _ledger(app)) == before
        assert sorted(_shows_of(app, live)) == sorted([made, rec])
    finally:
        held.rollback()
        other.close()
    r = client.post(f"/api/shows/{made}/merge", json={"confirm": "merge"}, headers=h)
    assert r.status_code == 200 and _shows_of(app, live) == [rec]
