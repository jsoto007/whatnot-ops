"""Winners come from Whatnot's orders; what each winner picks comes from chat.

Two things have to hold for that:
- a win made from an order is placed in time by the EARLIEST evidence of it, so the pick the
  winner typed is not handed to their previous win when the paid order is stamped late (a
  card declined and then paid: 10 of 20 lots on the 2026-11-13 show);
- one show on Whatnot is one chat, however many recordings it was made in (two laptops, or
  a recording started again): a pick typed on another recording still reaches its win.

Every buyer, time and order number here is made up.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import db
from app.models import (
    ChatMessage,
    Organization,
    ParsedEvent,
    Sale,
    Show,
    ShowShelf,
    WhatnotShow,
)
from app.services import catalog, pipeline, sale_chat, shelves
from app.services.devices import create_device
from tests import test_whatnot_orders as wo
from tests.test_pipeline import run
from tests.test_whatnot_orders import apply, order, read

shop = wo.shop  # a connected shop with a show matched to Whatnot

T0 = datetime(2026, 11, 14, 6, 0, tzinfo=UTC)
LOT = "CHOOSE ANY PRODUCT YOU WANT #{}"


def _chat(show_id, seq, user, body, at, received=None, device_id=None):
    db.session.add(
        ChatMessage(
            show_id=show_id,
            client_id=f"{show_id:04x}{seq:012x}",
            seq=seq,
            username=user,
            body=body,
            raw_text=f"{user} {body}".strip(),
            source="chat",
            captured_at=at,
            device_id=device_id,
            **({"received_at": received} if received is not None else {}),
        )
    )


def _laptop(org_id, name="Front laptop") -> int:
    device, _ = create_device(db.session.get(Organization, org_id), name)
    db.session.flush()
    return device.id


def _live(show_id, device_id, *, ahead=0, start=None, minutes=8, first_seq=1000, delay=1):
    """Chat recorded by one laptop and uploaded as the show went: a viewer's line every 8
    seconds, each received `delay` seconds after it was really typed, by a laptop whose
    clock runs `ahead` seconds fast (negative: slow)."""
    start = start or T0 - timedelta(minutes=3)
    for i in range(minutes * 60 // 8):
        true = start + timedelta(seconds=8 * i)
        _chat(
            show_id,
            first_seq + i,
            "other_viewer",
            f"hello {i}",
            true + timedelta(seconds=ahead),
            received=true + timedelta(seconds=delay),
            device_id=device_id,
        )


def _typed(show_id, seq, user, body, true, *, ahead=0, device_id=None):
    """A line really typed at `true`, as a laptop `ahead` seconds fast recorded it."""
    _chat(
        show_id,
        seq,
        user,
        body,
        true + timedelta(seconds=ahead),
        received=true + timedelta(seconds=1),
        device_id=device_id,
    )


def _order_sale(show, buyer, lot, ordered_at, order_id, *, won_at=None):
    return pipeline.create_sale_from_order(
        show,
        buyer=buyer,
        item_text=LOT.format(lot),
        quantity=1,
        price_cents=1700,
        external_order_id=order_id,
        ordered_at=ordered_at,
        won_at=won_at,
        settled=True,
    )


def _second_recording(first: Show, key="2026-10-19T20:05:00.000Z") -> Show:
    """Another laptop's recording of the same show, both matched to one show on Whatnot."""
    if first.whatnot_show_id is None:
        ws = WhatnotShow(organization_id=first.organization_id, whatnot_id="ws-1", status="live")
        db.session.add(ws)
        db.session.flush()
        first.whatnot_show_id = ws.id
    other = Show(
        organization_id=first.organization_id,
        show_key=key,
        started_at=first.started_at,
        show_date=first.show_date,
        whatnot_show_id=first.whatnot_show_id,
    )
    db.session.add(other)
    db.session.flush()
    return other


def _said(lines):
    return [(c["body"], c["after"]) for c in lines]


def _two_products(world, show):
    catalog.create_product(world["org"], "Blue bundle", skus=[{"code": "BLUE", "priceCents": 2500}])
    shelves.save(show, [("B4", "RED"), ("C3", "BLUE")])


# ---------- the win's time is its earliest evidence ----------


def test_a_retried_cards_pick_stays_on_its_own_win(app, world):
    with app.app_context():
        show = db.session.get(Show, world["show"])
        _two_products(world, show)
        # Lot 1: won at T0, ordered at once; the winner picks B4.
        first = _order_sale(show, "buyer_a", 1, T0 + timedelta(seconds=2), "2000000001")
        _chat(show.id, 1, "buyer_a", "B4", T0 + timedelta(seconds=6))
        # Lot 5: won four minutes later. The card is declined (an order at the hammer), the
        # winner types C3 five seconds after the win, and the retry pays 40 seconds on.
        hammer = T0 + timedelta(minutes=4)
        _chat(show.id, 2, "buyer_a", "C3", hammer + timedelta(seconds=5))
        second = _order_sale(
            show,
            "buyer_a",
            5,
            hammer + timedelta(seconds=40),
            "2000000002",
            won_at=hammer + timedelta(seconds=2),
        )
        db.session.commit()
        sales = list(db.session.scalars(select(Sale).where(Sale.show_id == show.id)))
        lines = sale_chat.around(sales, limit=None)
        # Each pick is on its own win. Before, C3 went to lot 1 and lot 5 had nothing.
        assert _said(lines[first.id]) == [("B4", True)]
        assert _said(lines[second.id]) == [("C3", True)]
        picks = shelves.suggest(sales)
        assert (picks[first.id]["spot"], picks[second.id]["spot"]) == ("B4", "C3")
        # The win is when the first order was placed, not when the retry went through.
        assert second.ordered_at == hammer + timedelta(seconds=2)


def test_a_retried_card_is_won_at_the_first_order_and_paid_at_the_one_that_went_through(app, world):
    with app.app_context():
        show = db.session.get(Show, world["show"])
        sale = pipeline.create_sale_from_order(
            show,
            buyer="buyer_a",
            item_text="Red bundle #7",  # names a product, so the sale is paid at once
            quantity=1,
            price_cents=2500,
            external_order_id="2000000009",
            ordered_at=T0 + timedelta(seconds=40),
            won_at=T0 + timedelta(seconds=2),
            settled=True,
        )
        db.session.commit()
        assert sale.status.value == "paid"
        assert sale.ordered_at == T0 + timedelta(seconds=2)
        assert sale.paid_at == T0 + timedelta(seconds=40)


def test_whatnots_declined_then_paid_orders_place_the_win_at_the_first_one(app, shop):
    """The same, through Whatnot's own orders: a failed order, then a paid one, one win."""
    hammer = datetime.now(UTC) - timedelta(minutes=10)
    failed = order(shop, "buyer_a", LOT.format(5), 1700, created=hammer)
    shop["fake"].set_order_status(failed["id"], "failed")
    paid = order(shop, "buyer_a", LOT.format(5), 1700, created=hammer + timedelta(seconds=45))
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1, "skip": 1}
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale.external_order_id == paid["id"]
        assert abs((sale.ordered_at - hammer).total_seconds()) < 1  # the declined attempt
        # A pick typed 5 s after the hammer, 40 s before the paid order's stamp, is the win's.
        _chat(shop["show"], 1, "buyer_a", "C3", hammer + timedelta(seconds=5))
        db.session.commit()
        assert _said(sale_chat.around([sale], limit=None)[sale.id]) == [("C3", True)]
    # Reading the orders again settles on the same time: it never moves later.
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        assert abs((db.session.scalar(select(Sale)).ordered_at - hammer).total_seconds()) < 1


def test_with_no_win_line_a_pick_typed_just_before_the_order_time_still_counts(app, world):
    with app.app_context():
        show = db.session.get(Show, world["show"])
        sale = _order_sale(show, "buyer_a", 1, T0, "2000000001")
        lag = sale_chat.ORDER_LAG
        _chat(show.id, 1, "buyer_a", "too early", T0 - lag - timedelta(seconds=20))
        _chat(show.id, 2, "buyer_a", "B4", T0 - lag + timedelta(seconds=3))
        _chat(show.id, 3, "buyer_a", "thanks", T0 + timedelta(seconds=30))
        db.session.commit()
        # The order trails the hammer by a moment: "B4" is after the win. A line well before
        # is shown as said before it, and is not a pick.
        assert _said(sale_chat.around([sale], limit=None)[sale.id]) == [
            ("too early", False),
            ("B4", True),
            ("thanks", True),
        ]
        assert sale_chat.won_at(sale) == T0  # what is shown as the win time is not shifted


def test_a_win_line_typed_late_cannot_put_the_win_after_its_own_order(app, world):
    """A host types "sold to…" a minute and a half after the hammer; the winner had already
    picked. On a laptop known to be in step, the order's time bounds the win."""
    world["say"](app, [("", "buyer_a won Red bundle")])
    run(app, world["show"])
    with app.app_context():
        laptop = _laptop(world["org"])
        sale = db.session.scalar(select(Sale))
        line = sale.event.message
        line.captured_at, line.received_at = T0 + timedelta(seconds=90), T0 + timedelta(seconds=91)
        line.device_id = laptop
        sale.ordered_at = T0 + timedelta(seconds=2)
        _typed(sale.show_id, 9, "buyer_a", "B4", T0 + timedelta(seconds=10), device_id=laptop)
        db.session.commit()
        # Too little chat to tell this laptop's clock: the win line's time stands on its own,
        # as before, and a line before it is shown as said before the win.
        assert sale_chat.clocks([sale.show_id]) == {("laptop", laptop): sale_chat.UNKNOWN}
        assert _said(sale_chat.around([sale], limit=None)[sale.id]) == [("B4", False)]

        _live(sale.show_id, laptop)
        db.session.commit()
        assert sale_chat.clocks([sale.show_id])[("laptop", laptop)].ok is True
        assert _said(sale_chat.around([sale], limit=None)[sale.id]) == [("B4", True)]
        # What is shown as the time of the win is still the line's own.
        assert sale_chat.won_at(sale) == T0 + timedelta(seconds=90)


def test_a_win_read_from_the_player_is_never_moved_by_its_order(app, world):
    """Back-to-back wins by one buyer, 15 seconds apart, read from the video player: each
    win's lines start at its own "won!", on the laptop's clock, whatever the orders say."""
    world["say"](
        app,
        [
            ("", f"buyer_a won the auction for {LOT.format(1)} for $17.00", "player"),
            ("", f"buyer_a won the auction for {LOT.format(2)} for $17.00", "player"),
        ],
    )
    run(app, world["show"])
    with app.app_context():
        laptop = _laptop(world["org"])
        first, second = db.session.scalars(select(Sale).order_by(Sale.id)).all()
        for sale, hammer in ((first, T0), (second, T0 + timedelta(seconds=15))):
            sale.event.message.captured_at = hammer
            sale.event.message.received_at = hammer + timedelta(seconds=1)
            sale.event.message.device_id = laptop
            sale.ordered_at = hammer + timedelta(seconds=2)
        _live(first.show_id, laptop)  # the clock is known to be in step
        show = first.show_id
        _typed(show, 1, "buyer_a", "B4", T0 + timedelta(seconds=5), device_id=laptop)
        _typed(show, 2, "buyer_a", "actually C9", T0 + timedelta(seconds=9), device_id=laptop)
        _typed(show, 3, "buyer_a", "A1", T0 + timedelta(seconds=23), device_id=laptop)
        db.session.commit()
        lines = sale_chat.around([first, second], limit=None)
        # "actually C9", 9 s after the first hammer and 6 s before the second, is the first's.
        assert _said(lines[first.id]) == [("B4", True), ("actually C9", True)]
        assert _said(lines[second.id]) == [("A1", True)]


# ---------- a laptop's clock is not Whatnot's ----------


def _states(show_ids):
    return {key: (c.ok, c.ahead) for key, c in sale_chat.clocks(show_ids).items()}


def test_the_app_tells_whether_a_laptops_clock_is_in_step(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        org = world["org"]
        good, fast, slow, late, short = (
            _laptop(org, n) for n in ("good", "fast", "slow", "late", "short")
        )
        _live(a.id, good)
        _live(a.id, fast, ahead=60, first_seq=2000)
        _live(a.id, slow, ahead=-45, first_seq=3000)
        # Uploaded in one go after the show: it says nothing about the clock.
        for i in range(60):
            at = T0 + timedelta(seconds=8 * i)
            _chat(a.id, 4000 + i, "other_viewer", f"hi {i}", at, T0 + timedelta(hours=3), late)
        # Too short to tell.
        _typed(a.id, 5000, "other_viewer", "hello", T0, ahead=300, device_id=short)
        db.session.commit()
        state = _states([a.id])
        assert state[("laptop", good)][0] is True
        assert state[("laptop", fast)][0] is False
        assert 58 <= state[("laptop", fast)][1].total_seconds() <= 60
        assert state[("laptop", slow)][0] is False
        assert -47 <= state[("laptop", slow)][1].total_seconds() <= -45
        assert state[("laptop", late)] == (None, None) == state[("laptop", short)]
        problems = sale_chat.clock_problems(a)
        assert [(p["laptop"], p["seconds"] > 0) for p in problems] == [
            ("fast", True),
            ("slow", False),
        ]


def test_a_wifi_drop_on_a_laptop_with_the_right_clock_is_not_taken_for_a_wrong_clock(app, world):
    """Seven minutes live, then the connection drops and the rest of an hour arrives as one
    backlog. The clock is right, and the app says so: nothing is shifted and picks are still
    suggested. (A "correction" worked out from the backlog moved every pick by minutes.)"""
    with app.app_context():
        show = db.session.get(Show, world["show"])
        _two_products(world, show)
        laptop = _laptop(world["org"])
        start = T0 - timedelta(minutes=3)
        _live(show.id, laptop, start=start, minutes=7)
        flush = start + timedelta(minutes=75)
        for i in range(53 * 60 // 8):  # the other 53 minutes, all received together
            true = start + timedelta(minutes=7, seconds=8 * i)
            _chat(show.id, 2000 + i, "other_viewer", f"later {i}", true, flush, laptop)
        first = _order_sale(show, "buyer_a", 1, T0, "2000000001")
        second = _order_sale(show, "buyer_a", 2, T0 + timedelta(minutes=20), "2000000002")
        _typed(show.id, 1, "buyer_a", "B4", T0 + timedelta(seconds=8), device_id=laptop)
        late = T0 + timedelta(minutes=20, seconds=8)
        _chat(show.id, 2, "buyer_a", "C3", late, flush, laptop)  # typed offline, sent later
        db.session.commit()
        assert sale_chat.clocks([show.id])[("laptop", laptop)].ok is True
        assert sale_chat.clock_problems(show) == []
        lines = sale_chat.around([first, second], limit=None)
        assert (_said(lines[first.id]), _said(lines[second.id])) == (
            [("B4", True)],
            [("C3", True)],
        )
        picks = shelves.suggest([first, second])
        assert (picks[first.id]["spot"], picks[second.id]["spot"]) == ("B4", "C3")


def test_a_show_with_a_laptop_a_minute_fast_gets_no_suggested_picks_and_says_why(app, world):
    with app.app_context():
        show = db.session.get(Show, world["show"])
        _two_products(world, show)
        laptop = _laptop(world["org"])
        _live(show.id, laptop, ahead=60)
        first = _order_sale(show, "buyer_a", 1, T0, "2000000001")
        second = _order_sale(show, "buyer_a", 2, T0 + timedelta(seconds=60), "2000000002")
        # Typed 30 s after the first win; the laptop stamps it 90 s after, past the second.
        _typed(show.id, 1, "buyer_a", "B4", T0 + timedelta(seconds=30), ahead=60, device_id=laptop)
        db.session.commit()
        # The line is shown where its stamp puts it (the times are not rewritten)…
        lines = sale_chat.around([first, second], limit=None)
        assert _said(lines[second.id]) == [("B4", True)]
        # …so nothing is suggested for the show, and the show page names the laptop.
        assert shelves.suggest([first, second]) == {}
        (problem,) = sale_chat.clock_problems(show)
        assert problem["laptop"] == "Front laptop" and 58 <= problem["seconds"] <= 60


def test_a_clock_set_right_in_the_middle_of_a_show_is_not_steady(app, world):
    with app.app_context():
        show = db.session.get(Show, world["show"])
        _two_products(world, show)
        laptop = _laptop(world["org"])
        start = T0 - timedelta(minutes=6)
        _live(show.id, laptop, ahead=90, start=start, minutes=6)  # fast…
        _live(show.id, laptop, start=start + timedelta(minutes=6), minutes=6, first_seq=3000)
        sale = _order_sale(show, "buyer_a", 1, T0 + timedelta(minutes=2), "2000000001")
        at = T0 + timedelta(minutes=2, seconds=8)
        _typed(show.id, 1, "buyer_a", "B4", at, device_id=laptop)
        db.session.commit()
        assert _states([show.id])[("laptop", laptop)] == (False, None)
        assert shelves.suggest([sale]) == {}
        assert sale_chat.clock_problems(show) == [{"laptop": "Front laptop", "seconds": None}]


def test_two_laptops_a_minute_apart_get_no_suggested_picks(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = _second_recording(a)
        _two_products(world, a)
        one, two = _laptop(world["org"], "one"), _laptop(world["org"], "two")
        _live(a.id, one)
        _live(b.id, two, ahead=60, first_seq=5000)
        first = _order_sale(a, "buyer_a", 1, T0, "2000000001")
        second = _order_sale(a, "buyer_a", 2, T0 + timedelta(seconds=60), "2000000002")
        said = T0 + timedelta(seconds=20)
        _typed(a.id, 1, "buyer_a", "B4", said, device_id=one)
        _typed(b.id, 1, "buyer_a", "B4", said, ahead=60, device_id=two)
        db.session.commit()
        # Both recordings of the show are held back, whichever one the sale is in.
        assert sale_chat.unsafe([a, b]) == {a.id, b.id}
        assert shelves.suggest([first, second]) == {}
        assert [p["laptop"] for p in sale_chat.clock_problems(a)] == ["two"]


def test_a_laptop_started_again_keeps_what_is_known_about_its_clock(app, world):
    """A second, short recording by the same laptop: too little chat of its own to judge,
    but it is the same clock."""
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = _second_recording(a)
        laptop = _laptop(world["org"])
        _live(a.id, laptop, ahead=60)
        _typed(b.id, 1, "other_viewer", "back again", T0 + timedelta(minutes=9), device_id=laptop)
        db.session.commit()
        assert _states([b.id]) == {("laptop", laptop): (None, None)}  # asked alone: no telling
        assert sale_chat.unsafe([b]) == {b.id}  # with the show's other recording: it is out


# ---------- one show on Whatnot is one chat ----------


def test_a_pick_typed_on_another_recording_reaches_its_win(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = _second_recording(a)
        _two_products(world, a)
        sale = _order_sale(a, "buyer_b", 9, T0, "2000000003")
        _chat(a.id, 1, "other_viewer", "hi all", T0 + timedelta(seconds=4))
        _chat(b.id, 1, "buyer_b", "B4 please", T0 + timedelta(seconds=10))
        db.session.commit()
        assert _said(sale_chat.around([sale], limit=None)[sale.id]) == [("B4 please", True)]
        # Two laptops whose clocks cannot be told are not known to be one chat: the line is
        # shown, and no pick is suggested from it.
        assert shelves.suggest([sale]) == {}

        one, two = _laptop(world["org"], "one"), _laptop(world["org"], "two")
        _live(a.id, one)
        _live(b.id, two, first_seq=5000)
        for m in db.session.scalars(select(ChatMessage).where(ChatMessage.device_id.is_(None))):
            m.device_id = one if m.show_id == a.id else two
        db.session.commit()
        assert sale_chat.unsafe([a]) == set()
        assert shelves.suggest([sale])[sale.id]["spot"] == "B4"


def test_two_recordings_that_are_not_one_whatnot_show_stay_apart(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = Show(
            organization_id=a.organization_id,
            show_key="2026-10-19T20:05:00.000Z",
            started_at=a.started_at,
            show_date=a.show_date,
        )
        db.session.add(b)
        db.session.flush()
        sale = _order_sale(a, "buyer_b", 9, T0, "2000000003")
        _chat(b.id, 1, "buyer_b", "B4 please", T0 + timedelta(seconds=10))
        db.session.commit()
        assert sale_chat.around([sale], limit=None)[sale.id] == []


def test_a_line_both_laptops_recorded_counts_once(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = _second_recording(a)
        sale = _order_sale(a, "buyer_a", 1, T0, "2000000001")
        at = T0 + timedelta(seconds=20)
        _chat(a.id, 1, "buyer_a", "B4", at)
        _chat(b.id, 1, "buyer_a", "B4", at + timedelta(seconds=2))  # the other laptop's copy
        # Said again a little later: twice on each laptop is still twice, not four times.
        _chat(a.id, 2, "buyer_a", "B4", at + timedelta(seconds=12))
        _chat(b.id, 2, "buyer_a", "B4", at + timedelta(seconds=13))
        _chat(b.id, 3, "buyer_a", "and a C3", at + timedelta(seconds=40))  # only laptop two saw it
        db.session.commit()
        assert [c["body"] for c in sale_chat.around([sale], limit=None)[sale.id]] == [
            "B4",
            "B4",
            "and a C3",
        ]


def test_the_other_laptops_copy_of_a_win_line_is_not_something_the_winner_said(app, world):
    world["say"](app, [("", "buyer_a won Red bundle")])
    run(app, world["show"])
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = _second_recording(a)
        sale = db.session.scalar(select(Sale))
        won = sale.event.message.captured_at
        _chat(b.id, 1, "", "buyer_a won Red bundle", won + timedelta(seconds=1))
        _chat(b.id, 2, "buyer_a", "thank you", won + timedelta(seconds=9))
        db.session.commit()
        assert [c["body"] for c in sale_chat.around([sale], limit=None)[sale.id]] == ["thank you"]


def test_a_buyers_wins_in_two_recordings_decide_together_where_a_line_goes(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = _second_recording(a)
        first = _order_sale(a, "buyer_a", 1, T0, "2000000001")
        _chat(a.id, 1, "buyer_a", "B4", T0 + timedelta(seconds=6))
        # The second win is a sale in the other recording; its pick was typed on the first.
        later = T0 + timedelta(minutes=4)
        second = _order_sale(b, "buyer_a", 5, later, "2000000002")
        _chat(a.id, 2, "buyer_a", "C3", later + timedelta(seconds=5))
        db.session.commit()
        # Asked about the first recording's sale alone, the app still knows of the later win:
        # "C3" is not handed to lot 1.
        assert _said(sale_chat.around([first], limit=None)[first.id]) == [("B4", True)]
        assert _said(sale_chat.around([second], limit=None)[second.id]) == [("C3", True)]


def test_the_shelf_layout_is_the_shows_whichever_recording_it_was_typed_on(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = _second_recording(a)
        catalog.create_product(world["org"], "Blue bundle", skus=[{"code": "BLUE"}])
        shelves.save(b, [("B4", "RED")])
        db.session.commit()
        assert (
            [r["spot"] for r in shelves.read(a)] == ["B4"] == [r["spot"] for r in shelves.read(b)]
        )
        # A recording that starts later (the laptop reloaded) uses the same layout at once.
        c = _second_recording(a, key="2026-10-19T20:40:00.000Z")
        db.session.commit()
        assert [(r["spot"], r["code"]) for r in shelves.read(c)] == [("B4", "RED")]
        laptop = _laptop(world["org"])  # one laptop made all three recordings
        _live(a.id, laptop)
        sale = _order_sale(c, "buyer_a", 1, T0, "2000000001")
        _typed(a.id, 1, "buyer_a", "b4", T0 + timedelta(seconds=5), device_id=laptop)
        db.session.commit()
        assert shelves.suggest([sale])[sale.id]["spot"] == "B4"
        # Laid out again from any recording: every one has the new layout, none the old.
        shelves.save(c, [("C3", "BLUE")])
        db.session.commit()
        assert {(r.show_id, r.code) for r in db.session.scalars(select(ShowShelf))} == {
            (a.id, "C3"),
            (b.id, "C3"),
            (c.id, "C3"),
        }


def test_a_show_on_its_own_reads_exactly_as_before(app, world):
    """One recording, win lines from the player: nothing about it changes."""
    world["say"](
        app,
        [
            ("", f"buyer_a won the auction for {LOT.format(1)} for $17.00", "player"),
            ("buyer_a", "B4 please"),
            ("buyer_a", "B4 please"),
            ("other_viewer", "nice"),
        ],
    )
    run(app, world["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert db.session.scalar(select(ParsedEvent.id).where(ParsedEvent.id == sale.event_id))
        assert [c["body"] for c in sale_chat.around([sale], limit=None)[sale.id]] == [
            "B4 please",
            "B4 please",
        ]


# ---------- the order time of a retried card ----------


def _sale_times(app):
    with app.app_context():
        return {
            s.item_text: (s.external_order_id, s.ordered_at)
            for s in db.session.scalars(select(Sale).order_by(Sale.id))
        }


def test_a_declined_attempt_belongs_to_the_payment_that_follows_it_not_the_latest(app, shop):
    """One buyer wins the same listing twice at the same price; the first card was retried."""
    t = datetime.now(UTC) - timedelta(minutes=20)
    failed = order(shop, "buyer_a", "Red bundle", created=t)
    shop["fake"].set_order_status(failed["id"], "failed")
    retry = order(shop, "buyer_a", "Red bundle", created=t + timedelta(minutes=3))
    again = order(shop, "buyer_a", "Red bundle", created=t + timedelta(minutes=5))
    read(app, shop)
    assert apply(app, shop) == {"recorded": 2, "skip": 1}
    with app.app_context():
        when = {s.external_order_id: s.ordered_at for s in db.session.scalars(select(Sale))}
    # The first win is placed at its declined attempt; the second keeps its own time.
    assert abs((when[retry["id"]] - t).total_seconds()) < 1
    assert abs((when[again["id"]] - (t + timedelta(minutes=5))).total_seconds()) < 1


def test_with_several_declined_attempts_the_first_one_is_the_win(app, shop):
    t = datetime.now(UTC) - timedelta(minutes=20)
    for seconds in (0, 20, 40):
        o = order(shop, "buyer_a", LOT.format(3), 1700, created=t + timedelta(seconds=seconds))
        shop["fake"].set_order_status(o["id"], "failed")
    order(shop, "buyer_a", LOT.format(3), 1700, created=t + timedelta(seconds=70))
    read(app, shop)
    assert apply(app, shop) == {"recorded": 1, "skip": 3}
    ((_, at),) = _sale_times(app).values()
    assert abs((at - t).total_seconds()) < 1


def test_a_sale_that_held_the_payments_time_moves_to_the_declined_attempts_when_read_again(
    app, shop
):
    """Sales made before this change carry the paid order's time."""
    t = datetime.now(UTC) - timedelta(minutes=20)
    failed = order(shop, "buyer_a", LOT.format(4), 1700, created=t)
    shop["fake"].set_order_status(failed["id"], "failed")
    paid = order(shop, "buyer_a", LOT.format(4), 1700, created=t + timedelta(seconds=50))
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        sale.ordered_at = t + timedelta(seconds=50)  # as it was stored before
        db.session.commit()
    read(app, shop)
    apply(app, shop)
    ((order_id, at),) = _sale_times(app).values()
    assert order_id == paid["id"] and abs((at - t).total_seconds()) < 1


def test_two_recordings_laid_out_before_they_were_matched_use_one_layout(app, world):
    with app.app_context():
        a = db.session.get(Show, world["show"])
        b = Show(
            organization_id=a.organization_id,
            show_key="2026-10-19T20:05:00.000Z",
            started_at=a.started_at,
            show_date=a.show_date,
        )
        db.session.add(b)
        db.session.flush()
        catalog.create_product(world["org"], "Blue bundle", skus=[{"code": "BLUE"}])
        shelves.save(a, [("B4", "RED")])
        shelves.save(b, [("B4", "BLUE")])  # the same spot, another product
        ws = WhatnotShow(organization_id=a.organization_id, whatnot_id="ws-9", status="live")
        db.session.add(ws)
        db.session.flush()
        a.whatnot_show_id = b.whatnot_show_id = ws.id
        db.session.commit()
        # One show, one answer: the older recording's layout, on both.
        assert [(r["spot"], r["code"]) for r in shelves.read(a)] == [("B4", "RED")]
        assert [(r["spot"], r["code"]) for r in shelves.read(b)] == [("B4", "RED")]
        laptop = _laptop(world["org"])
        _live(a.id, laptop)
        sale = _order_sale(b, "buyer_a", 1, T0, "2000000001")
        _typed(b.id, 1, "buyer_a", "B4", T0 + timedelta(seconds=5), device_id=laptop)
        db.session.commit()
        assert shelves.suggest([sale])[sale.id]["name"].startswith("Red bundle")


def test_the_show_page_is_told_which_laptops_clock_is_out(app, client, world, sign_in):
    sign_in(world["org"])
    assert client.get(f"/api/shows/{world['show']}").get_json()["clockProblems"] == []
    with app.app_context():
        laptop = _laptop(world["org"])
        _live(world["show"], laptop, ahead=-70)
        db.session.commit()
    (problem,) = client.get(f"/api/shows/{world['show']}").get_json()["clockProblems"]
    assert problem["laptop"] == "Front laptop" and -72 <= problem["seconds"] <= -70
