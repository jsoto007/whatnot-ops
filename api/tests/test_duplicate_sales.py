"""One sale line or one win captured twice is one sale (audit 2026-10-27, findings 1 and 2).

The extension records a chat row again when Whatnot re-renders it (new DOM node, new id),
and two laptops on one stream each make their own show and may read a win's title
differently. Both reach the parser as separate lines; the parser must turn the copy into a
"possible duplicate" Review item, never a second sale or a second reserve.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import db
from app.models import ChatMessage, ReviewItem, SaleStatus
from app.services import pipeline
from tests.test_pipeline import _owner, level, open_reviews, run, sales
from tests.test_player_wins import _add, _second_show, reasons, sale_rows

LINE = "lani808 won Red bundle"  # the platform's line: no sender, a sale at full confidence


def chat(app, show_id, seq, body, *, at, user=""):
    with app.app_context():
        db.session.add(
            ChatMessage(
                show_id=show_id,
                client_id=f"c{show_id:03x}{seq:012x}",
                seq=seq,
                username=user,
                body=body,
                raw_text=f"{user} {body}".strip(),
                source="chat",
                captured_at=at,
            )
        )
        db.session.commit()


def test_a_sale_line_recorded_twice_is_one_sale_and_one_question(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, LINE, at=now)
    chat(app, world["show"], 2, "nice pull", at=now + timedelta(seconds=3), user="amy")
    chat(app, world["show"], 3, LINE, at=now + timedelta(seconds=40))  # re-rendered copy
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 1)]
    assert level(app, world["red"]) == (2, 0)
    assert reasons(app) == ["possible_duplicate"]
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert "the line that made sale #1 (Red bundle, $25.00, paid)" in item.summary
        assert item.sale_id is None
    # Parsing again changes nothing.
    run(app, world["show"], reparse=True)
    assert len(sales(app, world["show"])) == 1


def test_a_person_can_still_record_the_copy_as_a_separate_sale(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, LINE, at=now)
    chat(app, world["show"], 2, LINE, at=now + timedelta(minutes=2))
    run(app, world["show"])
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        pipeline.resolve(item, "confirm_sale", {}, user=_owner(world["org"]))
        db.session.commit()
    assert sales(app, world["show"]) == [
        ("lani808", "paid", "RED", 1),
        ("lani808", "paid", "RED", 1),
    ]
    assert level(app, world["red"]) == (1, 0)


def test_the_same_line_after_the_window_is_a_new_sale(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, LINE, at=now - timedelta(minutes=11))
    chat(app, world["show"], 2, LINE, at=now)
    run(app, world["show"])
    assert len(sales(app, world["show"])) == 2
    assert reasons(app) == []


def test_a_different_buyer_or_wording_is_not_the_same_line(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, LINE, at=now)
    chat(app, world["show"], 2, "kat.c won Red bundle", at=now + timedelta(seconds=5))
    chat(app, world["show"], 3, "lani808 won Red bundle x2", at=now + timedelta(seconds=9))
    run(app, world["show"])
    assert [(b, q) for b, _, _, q in sales(app, world["show"])] == [
        ("lani808", 1),
        ("kat.c", 1),
        ("lani808", 2),
    ]
    assert reasons(app) == []


def test_a_re_rendered_line_does_not_bring_a_cancelled_sale_back(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, LINE, at=now)
    chat(
        app,
        world["show"],
        2,
        "cancelled for lani808",
        at=now + timedelta(seconds=20),
        user="host_shop",
    )
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "cancelled", "RED", 1)]
    chat(app, world["show"], 3, LINE, at=now + timedelta(seconds=60))
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "cancelled", "RED", 1)]
    assert level(app, world["red"]) == (3, 0)
    assert reasons(app) == ["possible_duplicate"]


def test_two_laptops_on_a_chat_only_show_make_one_sale(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, LINE, at=now)
    other = _second_show(app, world)
    chat(app, other, 1, LINE, at=now + timedelta(seconds=2))
    run(app, world["show"])
    run(app, other)
    assert len(sales(app, world["show"]) + sales(app, other)) == 1
    assert reasons(app) == ["possible_duplicate"]
    assert level(app, world["red"]) == (2, 0)


def test_a_viewer_typing_the_line_first_does_not_hide_the_real_sale(app, world):
    # The viewer's row reads the same ("lani808" + "won Red bundle") but made no sale, so
    # the platform line after it is not a copy of anything.
    now = datetime.now(UTC)
    chat(app, world["show"], 1, "won Red bundle", at=now, user="lani808")
    chat(app, world["show"], 2, LINE, at=now + timedelta(seconds=4))
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 1)]
    assert [r for r, _ in open_reviews(app)] == []


# ---------- two laptops, one win read differently ----------

WIN_752 = "chonwhite828 won the auction for Red bundle #752 for $195.00"
WIN_753 = "chonwhite828 won the auction for Red bundle #753 for $195.00"


def test_two_laptops_that_read_a_different_title_make_one_sale(app, world):
    now = datetime.now(UTC)
    _add(app, world["show"], 1, WIN_752, at=now)
    other = _second_show(app, world)
    _add(app, other, 1, WIN_753, at=now + timedelta(seconds=1))
    run(app, world["show"])
    run(app, other)
    assert sale_rows(app, world["show"]) == [("chonwhite828", "Red bundle #752", 19500, 1, "paid")]
    assert sale_rows(app, other) == []
    assert reasons(app) == ["possible_duplicate"]
    assert level(app, world["red"]) == (2, 0)
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert 'read there as "Red bundle #752"' in item.summary


def test_the_exact_match_wins_over_a_nearer_loose_one(app, world):
    # Laptop A sold #752 and then #753 to the same buyer at the same price, 40 s apart.
    # Laptop B read both right; each copy must point at its own sale.
    now = datetime.now(UTC)
    _add(app, world["show"], 1, WIN_752, at=now)
    _add(app, world["show"], 2, WIN_753, at=now + timedelta(seconds=40))
    other = _second_show(app, world)
    _add(app, other, 1, WIN_752, at=now + timedelta(seconds=39))
    run(app, world["show"])
    run(app, other)
    assert len(sale_rows(app, world["show"])) == 2 and sale_rows(app, other) == []
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert "already sale #1 in another" in item.summary  # #752, not the nearer #753


def test_a_different_price_or_a_later_win_on_the_other_laptop_is_a_new_sale(app, world):
    now = datetime.now(UTC)
    _add(app, world["show"], 1, WIN_752, at=now)
    other = _second_show(app, world)
    # Same buyer, another lot at another price, seconds later: a second auction.
    _add(
        app,
        other,
        1,
        "chonwhite828 won the auction for Red bundle #753 for $20.00",
        at=now + timedelta(seconds=30),
    )
    # Same buyer and price, another title, three minutes later: past the near-match window.
    _add(app, other, 2, WIN_753.replace("#753", "#760"), at=now + timedelta(minutes=3))
    run(app, world["show"])
    run(app, other)
    assert len(sale_rows(app, world["show"])) == 1
    assert [(i, c) for _, i, c, _, _ in sale_rows(app, other)] == [
        ("Red bundle #753", 2000),
        ("Red bundle #760", 19500),
    ]
    assert reasons(app) == []


def test_two_lots_to_one_buyer_at_one_price_on_one_laptop_are_two_sales(app, world):
    # The near match only looks at other recordings; within one show quick repeat wins
    # by the same buyer at the same price are ordinary ($1 auctions).
    now = datetime.now(UTC)
    _add(app, world["show"], 1, WIN_752, at=now)
    _add(app, world["show"], 2, WIN_753, at=now + timedelta(seconds=20))
    run(app, world["show"])
    assert len(sale_rows(app, world["show"])) == 2
    assert reasons(app) == []


def test_a_cancelled_sale_on_one_laptop_is_still_the_same_win_on_the_other(app, world):
    # The cross-laptop check counts a sale in any status: a cancel on laptop A must not
    # turn laptop B's copy of the same win into a new sale.
    now = datetime.now(UTC)
    _add(app, world["show"], 1, WIN_752, at=now)
    run(app, world["show"])
    with app.app_context():
        from app.models import Sale

        s = db.session.scalar(select(Sale))
        pipeline.set_sale_status(s, SaleStatus.cancelled, reason="test", actor=pipeline.RULE_ACTOR)
        db.session.commit()
    other = _second_show(app, world)
    _add(app, other, 1, WIN_753, at=now + timedelta(seconds=2))
    run(app, other)
    assert sale_rows(app, other) == []
    assert reasons(app) == ["possible_duplicate"]


# ---------- review round: genuine second wins must not be held ----------


def _blue(app, world):
    from app.models import MovementKind
    from app.services import catalog, inventory

    with app.app_context():
        blue = catalog.create_product(
            world["org"], "Blue pack", skus=[{"code": "BLUE", "priceCents": 1000}]
        ).skus[0]
        inventory.move(blue.id, MovementKind.restock, 5, source_type="test", source_id="seed")
        db.session.commit()
        return blue.id


def test_the_same_winner_line_under_another_listing_is_asked_about_with_both_items(app, world):
    # "lani808 won the auction!" names no item: the listing on screen does. Two wins under two
    # listings and one line re-rendered under the next listing look the same, so a person
    # decides, with both items in front of them; recording it makes the Blue sale.
    blue = _blue(app, world)
    now = datetime.now(UTC)
    chat(app, world["show"], 1, "Now selling - Red bundle", at=now, user="host_shop")
    chat(app, world["show"], 2, "lani808 won the auction!", at=now + timedelta(seconds=30))
    chat(app, world["show"], 3, "Now selling - Blue pack", at=now + timedelta(seconds=60))
    chat(app, world["show"], 4, "lani808 won the auction!", at=now + timedelta(seconds=90))
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 1)]
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert item.reason == "possible_duplicate"
        assert "(Blue pack, $?)" in item.summary and "(Red bundle, $25.00, paid)" in item.summary
        assert "another listing" in item.summary and "second win by the same buyer" in item.summary
        pipeline.resolve(item, "confirm_sale", {}, user=_owner(world["org"]))
        db.session.commit()
    assert [s[2] for s in sales(app, world["show"])] == ["RED", "BLUE"]
    assert level(app, blue) == (4, 0)


def test_a_copy_re_rendered_under_the_next_listing_is_not_sold_again(app, world):
    blue = _blue(app, world)
    now = datetime.now(UTC)
    chat(app, world["show"], 1, "Now selling - Red bundle", at=now, user="host_shop")
    chat(app, world["show"], 2, "lani808 won the auction!", at=now + timedelta(seconds=30))
    chat(app, world["show"], 3, "Now selling - Blue pack", at=now + timedelta(seconds=40))
    chat(app, world["show"], 4, "lani808 won the auction!", at=now + timedelta(seconds=45))
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 1)]
    assert level(app, blue) == (5, 0)
    assert reasons(app) == ["possible_duplicate"]


def test_a_copy_with_no_listing_in_its_pass_is_still_asked_about_and_says_why(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, "Now selling - Red bundle", at=now, user="host_shop")
    chat(app, world["show"], 2, "lani808 won the auction!", at=now + timedelta(seconds=30))
    run(app, world["show"])
    # Next upload: the re-rendered copy, with no listing line before it in this pass.
    chat(app, world["show"], 3, "lani808 won the auction!", at=now + timedelta(seconds=75))
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 1)]
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert item.reason == "possible_duplicate"
        assert "(no item named, $?)" in item.summary
        assert "sale #1 (Red bundle, $25.00, paid) 45 s earlier" in item.summary


def test_the_same_line_at_another_price_is_a_second_sale(app, world):
    now = datetime.now(UTC)
    chat(app, world["show"], 1, "sold to lani808 $25", at=now, user="host_shop")
    chat(
        app,
        world["show"],
        2,
        "sold to lani808 $30",
        at=now + timedelta(seconds=40),
        user="host_shop",
    )
    run(app, world["show"])
    assert len(sales(app, world["show"])) == 2
    assert "possible_duplicate" not in reasons(app)  # each asks only which product


def test_a_copy_of_a_line_a_person_recorded_is_not_another_not_sure(app, world):
    # The split winner line is low confidence; a person records it. Its re-rendered copy must
    # point at that sale, not ask "is this the platform?" again (a click would sell twice).
    now = datetime.now(UTC)
    split = "won the auction for Red bundle for $25.00"
    chat(app, world["show"], 1, split, at=now, user="lani808")
    run(app, world["show"])
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert item.reason == "low_confidence"
        pipeline.resolve(item, "confirm_sale", {}, user=_owner(world["org"]))
        db.session.commit()
    chat(app, world["show"], 2, split, at=now + timedelta(seconds=30), user="lani808")
    run(app, world["show"])
    assert len(sales(app, world["show"])) == 1
    assert reasons(app) == ["possible_duplicate"]


def test_a_lot_the_other_laptop_missed_is_a_sale_not_a_copy(app, world):
    # Laptop A recorded #752 only; laptop B recorded #752 and, 40 s later, #753 to the same
    # buyer at the same price. B's #752 is A's sale; B's #753 is a second win.
    now = datetime.now(UTC)
    _add(app, world["show"], 1, WIN_752, at=now)
    other = _second_show(app, world)
    _add(app, other, 1, WIN_752, at=now + timedelta(seconds=1))
    _add(app, other, 2, WIN_753, at=now + timedelta(seconds=40))
    run(app, world["show"])
    run(app, other)
    assert [i for _, i, _, _, _ in sale_rows(app, other)] == ["Red bundle #753"]
    assert reasons(app) == ["possible_duplicate"]
    assert level(app, world["red"]) == (1, 0)


def test_a_restart_on_the_same_laptop_does_not_near_match(app, world):
    # One laptop reads one "won!" display one way; a restarted recording never re-records the
    # win on screen. So another title at the same price from the same laptop is another lot.
    from app.models import Organization
    from app.services.devices import create_device

    with app.app_context():
        dev, _ = create_device(db.session.get(Organization, world["org"]), "laptop")
        db.session.commit()
        dev_id = dev.id
    now = datetime.now(UTC)
    _add(app, world["show"], 1, WIN_752, at=now)
    other = _second_show(app, world)
    _add(app, other, 1, WIN_753, at=now + timedelta(seconds=75))
    with app.app_context():
        for msg in db.session.scalars(select(ChatMessage)):
            msg.device_id = dev_id
        db.session.commit()
    run(app, world["show"])
    run(app, other)
    assert len(sale_rows(app, world["show"]) + sale_rows(app, other)) == 2
    assert reasons(app) == []


def test_an_old_twin_does_not_hide_a_new_copy(app, world):
    # 40 minutes ago both laptops recorded kat.c winning "Red bundle" for $1 (no lot number).
    # Now laptop A records another such win and laptop B reads its title as "Blue pack": B's
    # old event is not the twin of A's new sale, so B's line is still a copy.
    now = datetime.now(UTC)
    old_win = "kat.c won the auction for Red bundle for $1.00"
    _add(app, world["show"], 1, old_win, at=now - timedelta(minutes=40))
    other = _second_show(app, world)
    _add(app, other, 1, old_win, at=now - timedelta(minutes=40) + timedelta(seconds=1))
    _add(app, world["show"], 2, old_win, at=now)
    _add(
        app,
        other,
        2,
        "kat.c won the auction for Blue pack for $1.00",
        at=now + timedelta(seconds=1),
    )
    run(app, world["show"])
    run(app, other)
    assert len(sale_rows(app, world["show"])) == 2
    assert sale_rows(app, other) == []
    assert reasons(app) == ["possible_duplicate", "possible_duplicate"]
