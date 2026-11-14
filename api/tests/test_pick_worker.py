"""The pick worker (stage 3): ten minutes after a winner's-pick show ends, the shelf spot each
winner typed names the product, with no person, when the shop switched that on.

A pick is applied only when all of this holds; otherwise a person decides, and the review
item says what was found:
- the line is the win's own buyer's, after the win and before the show ended;
- it names exactly one spot, laid out with a product in use;
- Whatnot holds the win's order as paid;
- every laptop that recorded the chat is known to be in step with the server;
- nothing about the sale is in doubt.

Chat is typed by anyone watching: the last tests are what a viewer can try.

Every buyer, lot and order here is made up; Whatnot is the in-memory fake.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db import db
from app.models import (
    AuditLog,
    ChatMessage,
    InventoryMovement,
    Job,
    Organization,
    ReviewItem,
    ReviewStatus,
    Sale,
    Show,
    Sku,
    WhatnotShow,
)
from app.services import activity, catalog, picks, pipeline, sale_mode, shelves
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import apply, order, read, set_mode
from tests.test_winners_from_whatnot import _laptop, _live, _typed

shop = wo.shop  # a connected shop; its show is matched to Whatnot by the first read

LOT = "CHOOSE ANY PRODUCT YOU WANT #{}"


def iso(at):
    return at.isoformat().replace("+00:00", "Z")


@pytest.fixture
def stage(app, shop):
    """A winner's-pick show that ended twenty minutes ago: two products on the shelves (B4
    red, C3 blue), one laptop whose clock is in step, the switch on. `stage["win"]` records
    a win from Whatnot's paid order; `stage["say"]` is a chat line typed at a given minute
    of the show."""
    end = datetime.now(UTC) - timedelta(minutes=20)
    start = end - timedelta(minutes=40)
    shop["fake"].shows[shop["wshow"]].update(status="ended", ended_at=iso(end))
    read(app, shop)
    with app.app_context():
        org = db.session.get(Organization, shop["org"])
        org.auto_pick = True
        show = db.session.get(Show, shop["show"])
        show.started_at = start - timedelta(minutes=5)
        catalog.create_product(org.id, "Blue bundle", skus=[{"code": "BLUE", "priceCents": 2500}])
        shelves.save(show, [("B4", "RED"), ("C3", "BLUE")])
        device = _laptop(org.id)
        _live(show.id, device, start=start, minutes=8)
        # The fixture's own welcome line was recorded by this laptop too (every real line
        # has a laptop; one without would count as a second clock nobody can judge).
        db.session.execute(
            update(ChatMessage).where(ChatMessage.device_id.is_(None)).values(device_id=device)
        )
        db.session.commit()
        blue = db.session.scalar(select(Sku.id).where(Sku.code == "BLUE"))
    seq = iter(range(5000, 6000))

    def won(buyer, lot, minute, *, status="pending_fulfillment"):
        """Whatnot's order for a win at that minute of the show, read and applied."""
        o = order(shop, buyer, LOT.format(lot), 1100, created=start + timedelta(minutes=minute))
        if status != "pending_fulfillment":
            shop["fake"].set_order_status(o["id"], status)
        read(app, shop)
        apply(app, shop)
        return o

    def say(user, body, minute, *, ahead=0, laptop=device):
        with app.app_context():
            at = start + timedelta(minutes=minute)
            _typed(shop["show"], next(seq), user, body, at, ahead=ahead, device_id=laptop)
            db.session.commit()

    def go():
        with app.app_context():
            summary = picks.run(db.session.get(Show, shop["show"]))
            db.session.commit()
            return summary

    return {**shop, "win": won, "say": say, "go": go, "end": end, "start": start,
            "blue": blue, "device": device}  # fmt: skip


def sales(app):
    with app.app_context():
        return [
            (s.buyer_username, s.status.value, s.sku.code if s.sku else None)
            for s in db.session.scalars(select(Sale).order_by(Sale.id))
        ]


def found(app):
    """What the worker left on each open decision, oldest first."""
    with app.app_context():
        return [
            (i.proposal or {}).get("pick")
            for i in db.session.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.status == ReviewStatus.open, ReviewItem.reason == "unmatched_item"
                )
                .order_by(ReviewItem.id)
            )
        ]


# ---------- the pick names the product ----------


def test_the_shelf_spot_the_winner_typed_names_the_product_after_the_show(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4 please", 10.5)
    assert sales(app) == [("ann_made_up", "detected", None)]  # waiting for the pick
    assert stage["go"]() == {"assigned": 1}
    assert sales(app) == [("ann_made_up", "paid", "RED")]
    assert level(app, stage["red"]) == (2, 0)  # the unit left the shelf, once
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "unmatched_item"))
        assert item.status is ReviewStatus.resolved
        assert item.resolution["by"] == "pick" and item.resolution["spot"] == "B4"
        assert item.resolved_by_id is None
        sale = db.session.scalar(select(Sale))
        moves = db.session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.source_id == str(sale.id))
            .order_by(InventoryMovement.id)
        ).all()
        # Held and marked sold exactly as when a person names it: one reserve, one commit.
        assert [m.kind.value for m in moves] == ["reserve", "commit"]
    assert stage["go"]() == {}  # reading again finds nothing waiting
    assert level(app, stage["red"]) == (2, 0)


def test_the_latest_word_before_the_show_ends_is_the_pick(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["say"]("ann_made_up", "actually C3", 25)  # a change of mind, still on air
    assert stage["go"]() == {"assigned": 1}
    assert sales(app) == [("ann_made_up", "paid", "BLUE")]
    assert level(app, stage["red"]) == (3, 0)


def test_a_spot_typed_after_the_show_ended_is_not_a_pick(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["say"]("ann_made_up", "C3", 41)  # the show ended at minute 40
    assert stage["go"]() == {"assigned": 1}
    assert sales(app) == [("ann_made_up", "paid", "RED")]


def test_each_of_a_buyers_wins_gets_its_own_pick(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["win"]("ann_made_up", 2, minute=20)
    stage["say"]("ann_made_up", "C3", 20.5)
    stage["win"]("bo_made_up", 3, minute=22)
    stage["say"]("bo_made_up", "b04", 22.5)
    assert stage["go"]() == {"assigned": 3}
    assert sales(app) == [
        ("ann_made_up", "paid", "RED"),
        ("ann_made_up", "paid", "BLUE"),
        ("bo_made_up", "paid", "RED"),
    ]
    assert level(app, stage["red"]) == (1, 0)


def test_picks_are_not_read_before_they_close(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow))
        ws.ended_at = datetime.now(UTC) - timedelta(minutes=9)  # the show has just ended
        db.session.commit()
        assert picks.run(db.session.get(Show, stage["show"])) is None
        ws.status, ws.ended_at = "live", None
        db.session.commit()
        assert picks.run(db.session.get(Show, stage["show"])) is None
    assert sales(app) == [("ann_made_up", "detected", None)] and found(app) == [None]


# ---------- the switch ----------


def test_with_the_switch_off_the_pick_is_recorded_for_a_person_to_confirm(app, stage):
    with app.app_context():
        db.session.get(Organization, stage["org"]).auto_pick = False
        db.session.commit()
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    assert stage["go"]() == {"suggested": 1, "changed": 1}
    assert sales(app) == [("ann_made_up", "detected", None)]
    assert level(app, stage["red"]) == (3, 0)
    assert found(app) == [
        {"state": "product", "spot": "B4", "skuId": stage["red"], "name": "Red bundle",
         "held": ["off"]}
    ]  # fmt: skip
    assert stage["go"]() == {"suggested": 1}  # nothing new to say the second time
    with app.app_context():  # one press applies what the winner picked
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.status == ReviewStatus.open))
        assert item.proposal["action"] == "assign_sku" and item.proposal["skuId"] == stage["red"]
        pipeline.resolve(item, "apply_proposal", {}, user=None, actor=pipeline.RULE_ACTOR)
        db.session.commit()
    assert sales(app) == [("ann_made_up", "paid", "RED")]


def test_a_show_that_sells_one_product_per_listing_is_left_alone(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    with app.app_context():
        sale_mode.set_show(db.session.get(Show, stage["show"]), sale_mode.LISTING)
        db.session.commit()
    assert stage["go"]() == {}
    assert sales(app) == [("ann_made_up", "detected", None)] and found(app) == [None]


# ---------- what is left for a person, and why ----------


def test_a_winner_who_typed_no_spot_did_not_pick(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "love it, thank you!", 10.5)
    assert stage["go"]() == {"none": 1, "changed": 1}
    assert found(app) == [{"state": "none"}]
    assert sales(app) == [("ann_made_up", "detected", None)]


def test_a_spot_with_no_product_and_two_spots_in_one_line_are_never_guessed(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "D7", 10.5)
    stage["win"]("bo_made_up", 2, minute=12)
    stage["say"]("bo_made_up", "B4 or C3?", 12.5)
    assert stage["go"]() == {"unknown_code": 1, "several": 1, "changed": 2}
    assert found(app) == [
        {"state": "unknown_code", "spot": "D7"},
        {"state": "several", "spots": ["B4", "C3"]},
    ]
    assert level(app, stage["red"]) == (3, 0)


def test_a_win_whatnot_has_not_confirmed_as_paid_is_not_assigned(app, stage):
    # Seen on the stream only: no order of Whatnot's is tied to it.
    stage["say"]("", f"ann_made_up won the auction for {LOT.format(1)} for $11.00", 10)
    run(app, stage["show"])
    stage["say"]("ann_made_up", "B4", 10.5)
    # An order Whatnot is still charging.
    stage["win"]("bo_made_up", 2, minute=12, status="pending")
    stage["say"]("bo_made_up", "C3", 12.5)
    assert stage["go"]() == {"suggested": 2, "changed": 2}
    assert [p["held"] for p in found(app)] == [["unconfirmed"], ["unconfirmed"]]
    assert level(app, stage["red"]) == (3, 0)


def test_a_laptop_whose_clock_cannot_be_told_leaves_the_pick_to_a_person(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    with app.app_context():  # another laptop recorded the pick, with too little chat to judge
        other = _laptop(stage["org"], "Back laptop")
        db.session.commit()
    stage["say"]("ann_made_up", "B4", 10.5, laptop=other)
    with app.app_context():
        # One clock in step and one that cannot be told: the chat is not read as one.
        assert picks.run(db.session.get(Show, stage["show"])) == {"unread": 1, "changed": 1}
        db.session.commit()
    assert found(app) == [{"state": "unread"}]
    assert sales(app) == [("ann_made_up", "detected", None)]


def test_one_laptop_with_too_little_chat_to_judge_its_clock_is_not_trusted(app, shop):
    """The clock is not known to be out, so the pick is still read and suggested; it is not
    known to be in step either, so it is never assigned by itself."""
    end = datetime.now(UTC) - timedelta(minutes=20)
    shop["fake"].shows[shop["wshow"]].update(status="ended", ended_at=iso(end))
    read(app, shop)
    with app.app_context():
        db.session.get(Organization, shop["org"]).auto_pick = True
        shelves.save(db.session.get(Show, shop["show"]), [("B4", "RED")])
        db.session.commit()
    order(shop, "ann_made_up", LOT.format(1), 1100, created=end - timedelta(minutes=30))
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        _typed(shop["show"], 9001, "ann_made_up", "B4", end - timedelta(minutes=29))
        db.session.commit()
        assert picks.run(db.session.get(Show, shop["show"])) == {"suggested": 1, "changed": 1}
        db.session.commit()
    assert [p["held"] for p in found(app)] == [["clock"]]
    assert level(app, shop["red"]) == (3, 0)


def test_a_sale_in_doubt_is_not_assigned(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        pipeline.open_review(
            sale.organization_id,
            pipeline.DECLINE_SEEN,
            "the stream showed the card declined",
            show_id=sale.show_id,
            sale_id=sale.id,
        )
        db.session.commit()
    assert stage["go"]() == {"suggested": 1, "changed": 1}
    assert [p["held"] for p in found(app)] == [["doubt"]]


def test_a_person_who_decided_first_is_not_overruled(app, stage, monkeypatch):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)

    def taken(*a, **kw):
        raise pipeline.ReviewError("already decided", 409)

    monkeypatch.setattr(pipeline, "resolve", taken)  # a person pressed a moment earlier
    assert stage["go"]() == {}
    assert sales(app) == [("ann_made_up", "detected", None)]
    assert level(app, stage["red"]) == (3, 0)


# ---------- which win a spot is for ----------


def test_one_line_fills_one_win_the_oldest_still_waiting(app, stage):
    """Found by the review: one "B4" used to name the product of every waiting win."""
    stage["win"]("ann_made_up", 1, minute=10)
    stage["win"]("ann_made_up", 2, minute=10.2)
    stage["win"]("ann_made_up", 3, minute=14)
    stage["say"]("ann_made_up", "B4", 14.5)
    assert stage["go"]() == {"assigned": 1, "none": 2, "changed": 2}
    assert sales(app) == [
        ("ann_made_up", "paid", "RED"),
        ("ann_made_up", "detected", None),
        ("ann_made_up", "detected", None),
    ]
    assert level(app, stage["red"]) == (2, 0)  # one unit, for the one pick


def test_a_spot_typed_as_the_next_win_is_recorded_is_not_guessed(app, stage):
    """Whatnot stamps an order some seconds after the hammer, and a laptop may run a few
    seconds slow: a spot typed right at the second win looks as if it came before it. It
    used to replace the first win's pick."""
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.2)
    stage["win"]("ann_made_up", 2, minute=11)
    stage["say"]("ann_made_up", "C3", 11 - 5 / 60)  # five seconds before the order's stamp
    summary = stage["go"]()
    assert "assigned" not in summary
    picks_found = found(app)
    assert picks_found[0]["spot"] == "B4" and picks_found[0]["held"] == ["unclear"]
    assert picks_found[1] == {"state": "none"}
    assert level(app, stage["red"]) == (3, 0)


def test_a_spot_typed_just_before_a_win_was_recorded_is_held_for_that_win(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10 - 6 / 60)  # six seconds before the order's stamp
    assert stage["go"]() == {"suggested": 1, "changed": 1}
    assert [(p["spot"], p["held"]) for p in found(app)] == [("B4", ["near"])]
    assert level(app, stage["red"]) == (3, 0)


def test_a_change_of_mind_with_several_wins_is_never_guessed(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["win"]("ann_made_up", 2, minute=20)
    stage["say"]("ann_made_up", "C3", 20.5)
    stage["say"]("ann_made_up", "wait, B4 instead", 30)  # for which of the two?
    assert stage["go"]() == {"suggested": 2, "changed": 2}
    assert [(p["spot"], p["held"]) for p in found(app)] == [
        ("B4", ["unclear"]),
        ("C3", ["unclear"]),
    ]
    assert level(app, stage["red"]) == (3, 0)


def test_a_line_that_names_the_lot_is_for_that_lot(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["win"]("ann_made_up", 2, minute=12)
    stage["say"]("ann_made_up", "#2 C3", 13)
    stage["say"]("ann_made_up", "B4", 14)
    stage["say"]("ann_made_up", "#2 B4 after all", 30)
    assert stage["go"]() == {"assigned": 2}
    assert sales(app) == [("ann_made_up", "paid", "RED"), ("ann_made_up", "paid", "RED")]


@pytest.mark.parametrize(
    "line,is_pick",
    [
        ("B4", True),
        ("b4 please", True),
        ("actually C3", True),
        ("I'll take b4", True),
        ("wait, B4 instead", True),
        ("#2 B4 after all", True),
        ("can I have C3 please", True),
        ("B4 thanks hun", True),
        ("can u ship it b4 friday", False),
        ("omg been waiting for this b4 the show even started", False),
        ("is the i7 any good", False),
        ("is B4 still there?", False),
        ("B4 for the first one", False),
        ("got my b12 vitamins today lol", False),
    ],
)
def test_a_pick_line_is_the_spot_with_little_else(line, is_pick):
    assert picks._reads_as_pick(line, shelves.typed(line)) is is_pick


def test_a_second_reading_never_moves_a_line_on_to_the_next_win(app, stage):
    """Found by the second review: after the first reading named win 1 from "B4", a later
    reading (late orders queue one) gave the same line to win 2."""
    stage["win"]("ann_made_up", 1, minute=10)
    stage["win"]("ann_made_up", 2, minute=10.2)
    stage["say"]("ann_made_up", "B4", 10.5)
    assert stage["go"]() == {"assigned": 1, "none": 1, "changed": 1}
    assert stage["go"]() == {"none": 1}
    assert sales(app) == [("ann_made_up", "paid", "RED"), ("ann_made_up", "detected", None)]
    assert level(app, stage["red"]) == (2, 0)


def test_a_win_a_person_named_used_up_its_line(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["win"]("ann_made_up", 2, minute=10.2)
    stage["say"]("ann_made_up", "B4", 10.5)
    with app.app_context():  # a person names the first win during the show
        item = db.session.scalars(select(ReviewItem).order_by(ReviewItem.id)).first()
        pipeline.resolve(
            item, "assign_sku", {"skuId": stage["red"]}, user=None, actor=pipeline.RULE_ACTOR
        )
        db.session.commit()
    assert stage["go"]() == {"none": 1, "changed": 1}
    assert sales(app) == [("ann_made_up", "paid", "RED"), ("ann_made_up", "detected", None)]
    assert level(app, stage["red"]) == (2, 0)


def test_chatter_that_holds_a_spot_like_word_changes_no_pick(app, stage):
    """Found by the second review: "b4" is also how people write "before"."""
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "C3", 10.5)
    stage["say"]("ann_made_up", "can u ship it b4 friday", 12)
    stage["win"]("bo_made_up", 2, minute=14)
    stage["say"]("bo_made_up", "omg been waiting for this b4 the show even started", 14.5)
    assert stage["go"]() == {"suggested": 1, "none": 1, "changed": 2}
    assert [(p.get("spot"), p.get("held")) for p in found(app)] == [
        ("C3", ["unclear"]),
        (None, None),
    ]
    assert level(app, stage["red"]) == (3, 0)


def test_words_that_point_at_a_win_are_not_read(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["win"]("ann_made_up", 2, minute=20)
    stage["say"]("ann_made_up", "C3", 20.5)  # for the lot just won, as she means it
    stage["say"]("ann_made_up", "oh and B4 for the first one", 21)
    summary = stage["go"]()
    assert "assigned" not in summary
    assert level(app, stage["red"]) == (3, 0)


def test_a_lot_named_before_it_was_won_is_not_a_pick_for_it(app, stage):
    stage["win"]("ann_made_up", 1, minute=3)
    stage["say"]("ann_made_up", "C3", 3.5)
    stage["say"]("ann_made_up", "#2 B4", 5)  # seven minutes before she won lot 2
    stage["win"]("ann_made_up", 2, minute=12)
    summary = stage["go"]()
    assert "assigned" not in summary
    assert sales(app)[1] == ("ann_made_up", "detected", None)


def test_the_same_spot_twice_for_two_wins_is_not_guessed(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["win"]("ann_made_up", 2, minute=10.2)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["say"]("ann_made_up", "B4", 10.7)  # again because the host did not hear, or one each?
    assert stage["go"]() == {"suggested": 2, "changed": 2}
    assert [p["held"] for p in found(app)] == [["unclear"], ["unclear"]]
    assert level(app, stage["red"]) == (3, 0)


def test_a_line_naming_two_lots_is_not_guessed(app, stage):
    stage["win"]("ann_made_up", 2, minute=10)
    stage["say"]("ann_made_up", "B4 for #2 and #5", 10.5)
    summary = stage["go"]()
    assert "assigned" not in summary
    assert level(app, stage["red"]) == (3, 0)


def test_a_show_with_no_shelf_layout_says_so(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    with app.app_context():
        shelves.save(db.session.get(Show, stage["show"]), [])
        db.session.commit()
    assert stage["go"]() == {"no_layout": 1, "changed": 1}
    assert found(app) == [{"state": "no_layout"}]


# ---------- how it is started, and what the log says ----------


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_applying_the_orders_of_a_show_that_is_over_reads_its_picks(app, client, stage):
    app.config["WHATNOT_WEBHOOK_SECRET"] = wo.SECRET
    set_mode(app, stage, "auto")
    o = order(
        stage, "ann_made_up", LOT.format(1), 1100, created=stage["start"] + timedelta(minutes=10)
    )
    stage["say"]("ann_made_up", "B4", 10.5)
    wo.deliver(client, "orders.created", {"id": o["id"], "show_id": stage["wshow"]})
    assert sales(app) == [("ann_made_up", "paid", "RED")]
    with app.app_context():
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "picks.read"))
        assert activity.who(entry.action, entry.actor_type, entry.actor_label) == (
            "The winner's pick"
        )
        assert activity.describe(entry.action, entry.details) == (
            "Read the winners' picks after a show: 1 assigned from the winner's shelf spot"
        )


def test_a_person_applying_the_orders_queues_the_reading_of_the_picks(app, client, stage, sign_in):
    """Found by the review: the job was queued after the commit and never saved."""
    h = sign_in(stage["org"])
    order(stage, "ann_made_up", LOT.format(1), 1100, created=stage["start"] + timedelta(minutes=10))
    read(app, stage)
    assert client.post(f"/api/shows/{stage['show']}/whatnot/apply", headers=h).status_code == 200
    with app.app_context():
        kinds = [j.kind for j in db.session.scalars(select(Job))]
        assert "pick_show" in kinds


def test_the_owner_switches_it_on_in_settings_and_the_log_says_so(app, client, world, sign_in):
    h = sign_in(world["org"])
    assert client.get("/api/auth/me").get_json()["organization"]["autoPick"] is False
    r = client.patch("/api/team/organization", json={"autoPick": "yes"}, headers=h)
    assert r.status_code == 400
    r = client.patch("/api/team/organization", json={"autoPick": True}, headers=h)
    assert r.status_code == 200 and r.get_json()["organization"]["autoPick"] is True
    with app.app_context():
        assert db.session.get(Organization, world["org"]).auto_pick is True
        entry = db.session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "organization.updated")
            .order_by(AuditLog.id.desc())
        ).first()
        assert activity.describe(entry.action, entry.details) == (
            "Switched on assigning winners' picks after each show"
        )


def test_the_orders_page_is_told_what_the_worker_found(app, client, stage, sign_in):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["go"]()
    sign_in(stage["org"])
    rows = client.get("/api/orders?view=all").get_json()["orders"]
    assert [s["review"]["pick"] for o in rows for s in o["sales"]] == ["none"]


# ---------- what a viewer can try (chat is attacker-controlled) ----------


def test_a_look_alike_name_is_not_the_winner(app, stage):
    """Found by the review: "K" (the Kelvin sign) lowers to "k", so a viewer with that
    name could speak for the winner."""
    stage["win"]("kate_made_up", 1, minute=10)
    stage["say"]("\u212aate_made_up", "B4", 10.5)
    assert stage["go"]() == {"none": 1, "changed": 1}
    assert sales(app) == [("kate_made_up", "detected", None)]
    with app.app_context():  # nor is it suggested to a person as hers
        assert shelves.suggest([db.session.scalar(select(Sale))]) == {}


def test_only_the_wins_own_buyer_can_pick_for_it(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("viewer9", "B4", 10.2)  # somebody else
    stage["say"]("viewer9", "@ann_made_up B4", 10.3)  # somebody else, naming the winner
    stage["say"]("ann_made_up_", "B4", 10.4)  # a look-alike name
    stage["say"]("ANN_MADE_UP2", "B4", 10.5)
    assert stage["go"]() == {"none": 1, "changed": 1}
    assert sales(app) == [("ann_made_up", "detected", None)]
    assert level(app, stage["red"]) == (3, 0)


def test_a_spot_typed_before_the_win_is_not_a_pick_for_it(app, stage):
    stage["say"]("ann_made_up", "B4", 3)  # long before she won anything
    stage["win"]("ann_made_up", 1, minute=10)
    assert stage["go"]() == {"none": 1, "changed": 1}
    assert sales(app) == [("ann_made_up", "detected", None)]


def test_a_flood_of_spots_assigns_nothing(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", " ".join(f"B{i}" for i in range(1, 30)), 10.5)
    assert stage["go"]() == {"several": 1, "changed": 1}
    assert level(app, stage["red"]) == (3, 0)


def test_another_buyers_spot_never_names_this_buyers_win(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["win"]("bo_made_up", 2, minute=10.2)
    stage["say"]("bo_made_up", "C3", 10.5)
    assert stage["go"]() == {"assigned": 1, "none": 1, "changed": 1}
    assert sales(app) == [("ann_made_up", "detected", None), ("bo_made_up", "paid", "BLUE")]
