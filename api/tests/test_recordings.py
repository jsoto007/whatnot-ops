"""Deleting what a laptop recorded for a show (services/recordings.py).

Every buyer name here is made up. RED starts with 3 on the shelf (the `world` fixture).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.db import db
from app.models import (
    AuditLog,
    ChatMessage,
    DeletedRecording,
    Device,
    Order,
    OrderStatus,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    Sku,
    WhatnotOrder,
    WhatnotOrderItem,
)
from app.services import activity, orders, pipeline, recordings
from tests import test_whatnot_orders as wo
from tests.test_ingest import batch, row
from tests.test_pipeline import _owner

shop = wo.shop  # the world's shop connected to Whatnot, its show matched to one there


def parse(app, show_id):
    with app.app_context():
        pipeline.process_show(show_id)
        db.session.commit()


def quiet(app, show_id, ago=timedelta(hours=1)):
    """The laptop stopped recording `ago`."""
    with app.app_context():
        db.session.execute(
            update(Show).where(Show.id == show_id).values(updated_at=datetime.now(UTC) - ago)
        )
        db.session.commit()


def delete(app, org_id, show_id):
    with app.app_context():
        out = recordings.delete_laptop_data(org_id, show_id, user=_owner(org_id))
        db.session.commit()
        return out


def level(app, sku_id):
    with app.app_context():
        s = db.session.get(Sku, sku_id)
        return s.on_hand, s.reserved


def count(app, model, *where):
    with app.app_context():
        return db.session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def test_the_laptops_chat_sales_orders_and_questions_go_and_the_stock_comes_back(app, world):
    """mika_r won 1 RED and jo.b 2 (paid at the win: the shelf is down to 0); cardguy's
    "mystery box" waits in Review. Deleting puts all 3 RED back and leaves nothing of the
    recording but the key that stops a late upload."""
    world["say"](
        app,
        [
            ("", "mika_r won Red bundle for $25"),
            ("", "jo.b won Red bundle x2"),
            ("", "cardguy bought mystery box for $10"),
        ],
    )
    parse(app, world["show"])
    assert level(app, world["red"]) == (0, 0)
    quiet(app, world["show"])
    with app.app_context():
        show = db.session.get(Show, world["show"])
        before = recordings.preview(show)
        db.session.rollback()
    assert before == {
        "messages": 3,
        "sales": 3,
        "standing": 3,
        "inPackage": 0,
        "moved": 0,
        "orders": 3,
        "reviews": 1,
        "keptFromWhatnot": 0,
        "blockers": [],
    }
    out = delete(app, world["org"], world["show"])
    assert out == {
        "showId": None,
        "date": out["date"],
        "messages": 3,
        "sales": 3,
        "cancelled": 3,
        "inPackage": 0,
        "moved": 0,
        "orders": 3,
        "reviews": 1,
        "kept": False,
    }
    assert level(app, world["red"]) == (3, 0)
    for model in (Show, ChatMessage, ParsedEvent, Sale, Order, ReviewItem):
        assert count(app, model) == 0, model
    with app.app_context():
        assert recordings.was_deleted(
            world["org"], db.session.scalar(select(DeletedRecording.show_key))
        )
        line = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "show.laptop_data_deleted")
        )
        words = activity.describe(line.action, line.details)
        assert words.startswith("Deleted the laptop data of the ")
        assert words.endswith(": 3 chat lines, 3 sales, 3 orders, 1 question")


def test_whatnots_own_data_stays_and_the_show_stays_with_no_recording(app, world, shop):
    """The laptop recorded mika_r's and sam_t's wins. Whatnot has mika_r's order and one
    nobody recorded (jo.b's), which applying made a sale of; sam_t's win has no order.
    Deleting the laptop data closes only sam_t's sale (its RED back) and keeps everything
    Whatnot vouches for: both orders, mika_r's and jo.b's sales and their stock, and the
    show, now one with no laptop recording."""
    world["say"](app, [("", "mika_r won Red bundle for $25"), ("", "sam_t won Red bundle for $25")])
    parse(app, world["show"])
    wo.order(shop, "mika_r", "Red bundle", 2500)
    wo.order(shop, "jo.b", "Red bundle", 2500)
    wo.read(app, shop)
    wo.apply(app, shop)
    with app.app_context():
        vouched = {
            s.buyer_username: s.id
            for s in db.session.scalars(select(Sale).where(Sale.external_order_id.is_not(None)))
        }
        assert set(vouched) == {"mika_r", "jo.b"}
    assert level(app, world["red"]) == (0, 0)
    quiet(app, world["show"])
    out = delete(app, world["org"], world["show"])
    assert (out["showId"], out["kept"], out["sales"], out["cancelled"]) == (
        world["show"],
        True,
        1,
        1,
    )
    assert level(app, world["red"]) == (1, 0)  # sam_t's back; the vouched two still sold
    with app.app_context():
        show = db.session.get(Show, world["show"])
        assert show.whatnot_link == "made" and show.whatnot_show_id is not None
        kept = {s.buyer_username: s for s in db.session.scalars(select(Sale))}
        assert set(kept) == {"mika_r", "jo.b"}
        assert all(s.event_id is None for s in kept.values())
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrder)) == 2
        links = {
            o.buyer_username: [i.sale_id for i in o.items]
            for o in db.session.scalars(select(WhatnotOrder))
        }
        assert links == {b: [vouched[b]] for b in ("mika_r", "jo.b")}
        assert db.session.scalar(select(func.count()).select_from(ChatMessage)) == 0
        assert db.session.scalar(select(func.count()).select_from(WhatnotOrderItem)) == 2


def test_a_sale_already_packed_is_not_counted_back(app, world):
    """mika_r's order was being picked: whether that RED is still on the shelf is a
    person's call, as for any cancel after picking. jo.b's, still on the shelf, comes back."""
    world["say"](app, [("", "mika_r won Red bundle for $25"), ("", "jo.b won Red bundle")])
    parse(app, world["show"])
    with app.app_context():
        order = db.session.scalar(select(Order).where(Order.buyer_username == "mika_r"))
        orders.advance(order, OrderStatus.picking, actor_label="t")
        db.session.commit()
    assert level(app, world["red"]) == (1, 0)
    quiet(app, world["show"])
    out = delete(app, world["org"], world["show"])
    assert (out["cancelled"], out["inPackage"]) == (2, 1)
    assert level(app, world["red"]) == (2, 0)


def test_refused_while_the_laptop_may_still_be_recording(app, world):
    world["say"](app, [("", "mika_r won Red bundle for $25")])
    parse(app, world["show"])
    quiet(app, world["show"], ago=timedelta(minutes=3))
    with app.app_context():
        with pytest.raises(recordings.RecordingError, match="may still be recording"):
            recordings.delete_laptop_data(world["org"], world["show"], user=_owner(world["org"]))
        db.session.rollback()
    assert count(app, ChatMessage) == 1 and level(app, world["red"]) == (2, 0)


def test_refused_while_another_recording_holds_a_copy_of_one_of_these_wins(app, world):
    """The other laptop's copy of mika_r's win waits in its Review naming this recording's
    sale: deleting would lose a real win, so a person decides it first."""
    world["say"](app, [("", "mika_r won Red bundle for $25")])
    parse(app, world["show"])
    quiet(app, world["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        other = Show(
            organization_id=world["org"],
            show_key="2026-10-19T20:05:00.000Z",
            started_at=datetime.now(UTC) - timedelta(hours=2),
            show_date=sale.show.show_date,
        )
        db.session.add(other)
        db.session.flush()
        msg = ChatMessage(
            show_id=other.id,
            client_id="00000000000000aa",
            seq=1,
            username="",
            body="mika_r won Red bundle for $25",
            raw_text="mika_r won Red bundle for $25",
            captured_at=datetime.now(UTC) - timedelta(hours=2),
        )
        db.session.add(msg)
        db.session.flush()
        ev = ParsedEvent(
            organization_id=world["org"],
            show_id=other.id,
            message_id=msg.id,
            kind=sale.event.kind,
            buyer_username="mika_r",
            item_text="Red bundle",
            price_cents=2500,
            confidence=0.5,
            rule="test",
        )
        db.session.add(ev)
        db.session.flush()
        pipeline.open_review(
            world["org"],
            "possible_duplicate",
            f"mika_r won Red bundle for $25.00 — the same win is already sale #{sale.id} in "
            "another recording of this show",
            show_id=other.id,
            message_id=msg.id,
            event_id=ev.id,
        )
        db.session.commit()
        show = db.session.get(Show, world["show"])
        assert "record it as a sale there" in recordings.preview(show)["blockers"][0]
        with pytest.raises(recordings.RecordingError, match="may also be in another laptop"):
            recordings.delete_laptop_data(world["org"], world["show"], user=_owner(world["org"]))
        db.session.rollback()
        # Once a person has decided it, the delete goes ahead.
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.show_id == other.id))
        item.status = ReviewStatus.dismissed
        db.session.commit()
    delete(app, world["org"], world["show"])
    assert count(app, Sale) == 0 and level(app, world["red"]) == (3, 0)
    assert count(app, ChatMessage) == 1  # the other recording's line stays


def test_a_late_upload_of_a_deleted_recording_is_dropped(app, client, device, auth):
    r = client.post("/api/ingest", json=batch([row(1), row(2)]), headers=auth)
    assert r.status_code == 200
    with app.app_context():
        show = db.session.scalar(select(Show))
        org_id, show_id = show.organization_id, show.id
    quiet(app, show_id)
    delete(app, org_id, show_id)
    long_ago = datetime.now(UTC) - timedelta(days=1)
    with app.app_context():
        db.session.execute(update(Device).values(last_seen_at=long_ago))
        db.session.commit()
    # The laptop's outbox sends it again: received, stored nowhere, no show made again.
    r = client.post("/api/ingest", json=batch([row(1), row(3)]), headers=auth)
    assert r.status_code == 200 and r.get_json() == {
        "accepted": 0,
        "duplicates": 2,
        "deleted": True,
        "show": None,
    }
    assert count(app, Show) == 0 and count(app, ChatMessage) == 0
    with app.app_context():  # the laptop was still seen
        assert db.session.scalar(select(Device.last_seen_at)) > long_ago
    # A new recording (another key) is recorded as usual.
    later = "2026-10-20T20:00:00.000Z"
    r = client.post("/api/ingest", json=batch([row(1, show=later)], show=later), headers=auth)
    assert r.get_json()["accepted"] == 1 and count(app, Show) == 1


def test_only_the_owner_may_delete_and_must_say_so(app, client, world, sign_in):
    world["say"](app, [("", "mika_r won Red bundle for $25")])
    parse(app, world["show"])
    quiet(app, world["show"])
    url = f"/api/shows/{world['show']}/laptop-data"
    h = sign_in(world["org"], role="manager", email="m@x.test")
    assert client.get(url).status_code == 403
    assert client.delete(url, json={"confirm": "delete"}, headers=h).status_code == 403
    client.post("/api/auth/logout", headers=h)
    h = sign_in(world["org"], role="owner")
    got = client.get(url).get_json()
    assert (got["messages"], got["sales"], got["blockers"]) == (1, 1, [])
    assert client.delete(url, json={"confirm": "delete"}).status_code == 403  # no CSRF token
    r = client.delete(url, json={}, headers=h)
    assert r.status_code == 400 and r.get_json()["error"] == 'Type "delete" to confirm.'
    r = client.delete(url, json={"confirm": "delete"}, headers=h)
    assert r.status_code == 200 and r.get_json()["sales"] == 1
    assert client.get(url).status_code == 404
    assert level(app, world["red"]) == (3, 0)


def test_a_show_made_from_whatnot_has_nothing_to_delete(app, world):
    with app.app_context():
        show = db.session.get(Show, world["show"])
        assert recordings.preview(show)["blockers"] == ["Nothing on this show came from a laptop."]
        db.session.rollback()
    with app.app_context():
        with pytest.raises(recordings.RecordingError, match="Nothing on this show"):
            recordings.delete_laptop_data(world["org"], world["show"], user=_owner(world["org"]))


def test_a_sale_whatnots_order_made_stays_even_when_a_laptop_line_claimed_it(app, world, shop):
    """Whatnot's order for jo.b's win was applied first (a sale with no line); the laptop's
    win line then claimed it. It is Whatnot's record: it stays, with its stock, and loses
    only the line."""
    wo.order(shop, "jo.b", "Red bundle", 2500)
    wo.read(app, shop)
    wo.apply(app, shop)
    world["say"](app, [("", "jo.b won Red bundle for $25")])
    parse(app, world["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale.event_id is not None and sale.external_order_id is not None
        sale_id = sale.id
    assert level(app, world["red"]) == (2, 0)
    quiet(app, world["show"])
    out = delete(app, world["org"], world["show"])
    assert (out["sales"], out["cancelled"], out["kept"]) == (0, 0, True)
    assert level(app, world["red"]) == (2, 0)
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale is not None and sale.event_id is None and sale.status is SaleStatus.paid
        assert db.session.get(Show, world["show"]).whatnot_link == "made"
        assert db.session.scalar(select(func.count()).select_from(ChatMessage)) == 0


def test_a_line_here_that_claimed_a_sale_on_another_show_is_unlinked_not_a_crash(app, world):
    """One laptop recorded through two shows: a line in this recording claimed the order
    sale that lives on the other show. Deleting this recording keeps that sale (Whatnot's)
    and takes only the line away from it."""
    world["say"](app, [("", "jo.b won Red bundle for $25")])
    parse(app, world["show"])
    with app.app_context():
        first = db.session.scalar(select(Sale))
        other = Show(
            organization_id=world["org"],
            show_key="2026-10-19T23:00:00.000Z",
            started_at=datetime.now(UTC) - timedelta(hours=2),
            show_date=first.show.show_date,
        )
        db.session.add(other)
        db.session.flush()
        msg = ChatMessage(
            show_id=other.id,
            client_id="00000000000000bb",
            seq=1,
            username="",
            body="jo.b won Red bundle for $25",
            raw_text="jo.b won Red bundle for $25",
            captured_at=datetime.now(UTC) - timedelta(hours=2),
        )
        db.session.add(msg)
        db.session.flush()
        ev = ParsedEvent(
            organization_id=world["org"],
            show_id=other.id,
            message_id=msg.id,
            kind=first.event.kind,
            buyer_username="jo.b",
            item_text="Red bundle",
            price_cents=2500,
            rule="t",
        )
        db.session.add(ev)
        db.session.flush()
        # The sale on this show is Whatnot's (an order made it); its line is the other's.
        first.event_id, first.external_order_id = ev.id, "5000000001"
        other.updated_at = datetime.now(UTC) - timedelta(hours=2)
        db.session.commit()
        other_id, sale_id = other.id, first.id
    out = delete(app, world["org"], other_id)
    assert (out["sales"], out["kept"], out["showId"]) == (0, False, None)
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale is not None and sale.event_id is None and sale.show_id == world["show"]


def test_the_take_it_out_of_the_package_note_outlives_the_sale(app, world):
    """mika_r's order was being picked: the sale goes, but the note telling a person a unit
    may be in a box stays in Review, in its own words, without its links."""
    world["say"](app, [("", "mika_r won Red bundle for $25")])
    parse(app, world["show"])
    with app.app_context():
        orders.advance(db.session.scalar(select(Order)), OrderStatus.picking, actor_label="t")
        db.session.commit()
    quiet(app, world["show"])
    out = delete(app, world["org"], world["show"])
    assert (out["inPackage"], out["kept"]) == (1, False)
    with app.app_context():
        notes = db.session.scalars(select(ReviewItem)).all()
        assert [(n.reason, n.status, n.sale_id, n.show_id) for n in notes] == [
            ("closed_after_picking", ReviewStatus.open, None, None)
        ]
        assert "mika_r" in notes[0].summary and "Take it out of the package" in notes[0].summary


def test_a_win_the_other_laptop_also_recorded_moves_there_and_nothing_moves_in_stock(app, world):
    """Two laptops on one stream: this recording made mika_r's sale; the other read the same
    win from the video player and, the win being held here, made no sale of its own.
    Deleting this recording moves the sale to the other one: same sale, same product, same
    stock, the other laptop's line as its evidence, on mika_r's order there."""
    world["say"](app, [("", "mika_r won Red bundle for $25")])
    parse(app, world["show"])
    assert level(app, world["red"]) == (2, 0)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        won = pipeline.won_when(sale)
        other = Show(
            organization_id=world["org"],
            show_key="2026-10-19T20:01:00.000Z",
            started_at=won - timedelta(minutes=30),
            show_date=sale.show.show_date,
        )
        db.session.add(other)
        db.session.flush()
        msg = ChatMessage(
            show_id=other.id,
            client_id="00000000000000cc",
            seq=1,
            username="",
            body="mika_r won Red bundle for $25",
            raw_text="mika_r won Red bundle for $25",
            source="player",
            captured_at=won + timedelta(seconds=4),
        )
        db.session.add(msg)
        db.session.flush()
        ev = ParsedEvent(
            organization_id=world["org"],
            show_id=other.id,
            message_id=msg.id,
            kind=sale.event.kind,
            buyer_username="mika_r",
            item_text="Red bundle",
            price_cents=2500,
            rule="player_win",
        )
        db.session.add(ev)
        # A question still open about the sale itself (a declined card seen on the stream).
        pipeline.open_review(
            world["org"],
            "decline_seen",
            "mika_r's card may have been declined",
            show_id=world["show"],
            message_id=sale.event.message_id,
            event_id=sale.event_id,
            sale_id=sale.id,
        )
        db.session.commit()
        sale_id, ev_id, other_id = sale.id, ev.id, other.id
    quiet(app, world["show"])
    with app.app_context():
        assert recordings.preview(db.session.get(Show, world["show"]))["moved"] == 1
        db.session.rollback()
    out = delete(app, world["org"], world["show"])
    assert (out["moved"], out["sales"], out["cancelled"], out["kept"]) == (1, 0, 0, False)
    assert level(app, world["red"]) == (2, 0)
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert (sale.show_id, sale.event_id, sale.status) == (other_id, ev_id, SaleStatus.paid)
        assert sale.order is not None and sale.order.show_id == other_id
        # Its question went with it, still open, onto the other laptop's line.
        q = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "decline_seen"))
        assert (q.status, q.show_id, q.event_id, q.sale_id) == (
            ReviewStatus.open,
            other_id,
            ev_id,
            sale_id,
        )
        assert db.session.get(Show, world["show"]) is None
        assert db.session.scalar(select(func.count()).select_from(Order)) == 1


def test_a_lock_collision_says_try_again_and_changes_nothing(
    app, client, world, sign_in, monkeypatch
):
    """Two deletes, or a delete and the parser, can wait on each other's buyer locks; Postgres
    then fails one of them. The owner is told to try again, not shown an error page."""
    from sqlalchemy.exc import OperationalError

    def deadlock(*a, **k):
        raise OperationalError("SELECT pg_advisory_xact_lock", {}, Exception("deadlock detected"))

    monkeypatch.setattr(recordings, "delete_laptop_data", deadlock)
    h = sign_in(world["org"], role="owner")
    r = client.delete(
        f"/api/shows/{world['show']}/laptop-data", json={"confirm": "delete"}, headers=h
    )
    assert r.status_code == 409 and r.get_json()["error"].endswith("Try again in a minute.")


def _copy_elsewhere(app, world, client_id="00000000000000dd"):
    """Another laptop's recording of the same stream read the same win of mika_r's from the
    video player and, the win being held here, made no sale of its own."""
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        won = pipeline.won_when(sale)
        other = Show(
            organization_id=world["org"],
            show_key="2026-10-19T20:02:00.000Z",
            started_at=won - timedelta(minutes=30),
            show_date=sale.show.show_date,
        )
        db.session.add(other)
        db.session.flush()
        msg = ChatMessage(
            show_id=other.id,
            client_id=client_id,
            seq=1,
            username="",
            body="mika_r won Red bundle for $25",
            raw_text="mika_r won Red bundle for $25",
            source="player",
            captured_at=won + timedelta(seconds=3),
        )
        db.session.add(msg)
        db.session.flush()
        db.session.add(
            ParsedEvent(
                organization_id=world["org"],
                show_id=other.id,
                message_id=msg.id,
                kind=sale.event.kind,
                buyer_username="mika_r",
                item_text="Red bundle",
                price_cents=2500,
                rule="player_win",
            )
        )
        db.session.commit()
        return other.id


def _advance(app, *stages):
    with app.app_context():
        order = db.session.scalar(select(Order))
        for stage in stages:
            orders.advance(order, stage, actor_label="t")
        db.session.commit()


def test_a_win_being_packed_that_the_other_laptop_also_saw_waits_for_the_packing(app, world):
    """mika_r's box is being picked here and the other laptop has the same win: moving it
    would make a second, unpacked order there. A person finishes the packing first."""
    world["say"](app, [("", "mika_r won Red bundle for $25")])
    parse(app, world["show"])
    _copy_elsewhere(app, world)
    _advance(app, OrderStatus.picking)
    quiet(app, world["show"])
    with app.app_context():
        got = recordings.preview(db.session.get(Show, world["show"]))
        assert (got["moved"], got["inPackage"]) == (0, 1)
        assert "being picked or packed" in got["blockers"][0]
        with pytest.raises(recordings.RecordingError, match="Finish packing"):
            recordings.delete_laptop_data(world["org"], world["show"], user=_owner(world["org"]))
        db.session.rollback()
    assert count(app, ChatMessage) == 2 and count(app, Sale) == 1


def test_a_shipped_win_the_other_laptop_also_saw_closes_here_and_does_not_move(app, world):
    """mika_r's box has left: the unit is gone. The laptop-only sale closes here like any
    other (nothing counted back, the package note stays); nothing moves to the other
    laptop's recording, so nothing can be packed twice."""
    world["say"](app, [("", "mika_r won Red bundle for $25")])
    parse(app, world["show"])
    other_id = _copy_elsewhere(app, world)
    _advance(app, OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped)
    assert level(app, world["red"]) == (2, 0)
    quiet(app, world["show"])
    out = delete(app, world["org"], world["show"])
    assert (out["moved"], out["cancelled"], out["inPackage"], out["kept"]) == (0, 1, 1, False)
    assert level(app, world["red"]) == (2, 0)
    assert count(app, Sale) == 0
    assert count(app, Order, Order.show_id == other_id) == 0  # no second order to pack
    with app.app_context():
        note = db.session.scalar(select(ReviewItem))
        assert (note.reason, note.status) == ("closed_after_picking", ReviewStatus.open)
