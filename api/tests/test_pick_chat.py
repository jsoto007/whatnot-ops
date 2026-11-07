"""The winner picks the product in chat after the win, and the order slip (the final pick
list) prints what they said: the right lines on the right lot, a changed mind included,
however long they took (review 2026-11-07, findings 1-4).

Chat lines here are made up; lot markup is as on the shop's Seller Hub (2026-11-05)."""

from sqlalchemy import select

from app.db import db
from app.models import Sale, Show
from app.services import order_slips, rules, sale_chat, shipments
from tests.test_pipeline import run
from tests.test_sale_chat import say_at
from tests.test_shipments import LABEL, TODAY, lot, row

CHOOSE = "CHOOSE ANY PRODUCT YOU WANT #{}"


def won(buyer, n, price="17.00"):
    """A pick-your-own win as the extension writes it from Whatnot's video player."""
    return ("", f"{buyer} won the auction for {CHOOSE.format(n)} for ${price}", "player")


def lines_of(app, show_id):
    """Lot number → [(body, after, namesLot)] per sale."""
    with app.app_context():
        sales = db.session.scalars(select(Sale).where(Sale.show_id == show_id)).all()
        got = sale_chat.around(list(sales), limit=None)
        return {
            sale_chat.lot_of(s): [(c["body"], c["after"], c["namesLot"]) for c in got[s.id]]
            for s in sales
        }


def read_lots(client, shop, lots_by_buyer):
    read = {
        "show": LABEL,
        "rows": [
            row(b, items=str(len(ls)), order_date="Oct 31") | {"lots": ls}
            for b, ls in lots_by_buyer.items()
        ],
    }
    assert client.post("/api/ingest/shipments", json=read, headers=shop["auth"]).status_code == 200


def slip_rows(app, show_id):
    with app.app_context():
        doc = order_slips.build(db.session.get(Show, show_id))
    return {
        b["username"]: [(r["auction"], r["product"], r["said"], r["saidMore"]) for r in b["rows"]]
        for b in doc["slips"]
    }


def test_lines_name_lots_by_number_or_lot_word():
    assert rules.lot_refs("red one for #72") == {72}
    assert rules.lot_refs("#72 red, # 73 blue") == {72, 73}
    assert rules.lot_refs("quick hit 2 please, and Ultra #19") == {2, 19}
    assert rules.lot_refs("I'll take 2 for $72") == set()  # a bare number is a qty or price


# ---------- which win a line goes to ----------


def test_a_line_naming_a_lot_goes_to_that_win(app, world):
    say_at(
        app,
        world["show"],
        [
            (-10, "bob", "I want #72 so bad"),  # bidding, long before: not a pick
            (0, *won("bob", 72)),
            (1, *won("bob", 73, "15.00")),
            (1.5, "bob", "red one for #72"),
            (2, "bob", "blue one for #73"),
            (3, "bob", "#72 make it green actually"),
        ],
    )
    run(app, world["show"])
    assert lines_of(app, world["show"]) == {
        72: [("red one for #72", True, True), ("#72 make it green actually", True, True)],
        73: [("blue one for #73", True, True)],
    }


def test_a_pick_after_two_quick_wins_shows_on_both_until_the_first_has_one(app, world):
    say_at(
        app,
        world["show"],
        [
            (0, *won("bob", 72)),
            (1, *won("bob", 73, "15.00")),
            (1.5, "bob", "red one"),
            (2, "bob", "and the blue one"),
        ],
    )
    run(app, world["show"])
    assert lines_of(app, world["show"]) == {
        72: [("red one", True, False)],
        73: [("red one", True, False), ("and the blue one", True, False)],
    }


def test_a_win_with_a_product_or_long_ago_does_not_take_a_later_line(app, world):
    say_at(
        app,
        world["show"],
        [
            (0, "", "bob won the auction for Red bundle for $25.00", "player"),  # has a SKU
            (1, *won("bob", 5)),
            (30, *won("bob", 90)),  # lot 5 was 29 minutes before: not "together"
            (31, "bob", "red please"),
        ],
    )
    run(app, world["show"])
    got = lines_of(app, world["show"])
    assert got[90] == [("red please", True, False)]
    assert got[5] == [] and got[None] == []  # the Red bundle win has no lot number


def test_a_slow_pick_stays_with_the_win_until_the_next_one(app, world):
    say_at(
        app,
        world["show"],
        [
            (0, *won("bob", 73)),
            (25, "bob", "sorry, was away: the green one"),
            (40, *won("bob", 80)),
            (41, "bob", "the lamp"),
        ],
    )
    run(app, world["show"])
    assert lines_of(app, world["show"]) == {
        73: [("sorry, was away: the green one", True, False)],
        80: [("the lamp", True, False)],
    }


# ---------- what the order slip prints ----------


def test_the_slip_prints_the_final_choice_not_the_chat_before_the_win(
    client, app, shop, monkeypatch
):
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    say_at(
        app,
        shop["show"],
        [
            (-1, "bob", "gl everyone"),
            (-0.5, "bob", "that last one was fire"),
            (0, *won("bob", 73)),
            (1, "bob", "red one please"),
            (2, "bob", "actually make it the blue one"),
            (4, "bob", "no wait, final answer: green"),
        ],
    )
    run(app, shop["show"])
    read_lots(client, shop, {"bob": [lot(CHOOSE.format(73), "1000000001", "$17.00")]})
    assert slip_rows(app, shop["show"])["bob"] == [
        (
            "Listing 73",
            "No Pick",
            ["red one please", "actually make it the blue one", "no wait, final answer: green"],
            0,
        )
    ]


def test_a_long_chat_prints_the_first_and_last_lines_and_how_many_are_left_out(
    client,
    app,
    shop,
    monkeypatch,
):
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    say_at(
        app,
        shop["show"],
        [
            (0, *won("bob", 73)),
            (0.5, "host_shop", "congrats bob! what do you want?"),
            (1, "bob", "yay thank you!!"),
            (1.5, "bob", "hmm let me look"),
            (2, "bob", "the Fitness Ball please"),
            (3, "bob", "lol"),
            (4, "bob", "gl all"),
        ],
    )
    run(app, shop["show"])
    read_lots(client, shop, {"bob": [lot(CHOOSE.format(73), "1000000001", "$17.00")]})
    [(_, _, said, more)] = slip_rows(app, shop["show"])["bob"]
    assert said == ["congrats bob! what do you want?", "the Fitness Ball please", "lol", "gl all"]
    assert more == 2


def test_back_to_back_wins_print_each_pick_on_its_own_lot(client, app, shop, monkeypatch):
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    say_at(
        app,
        shop["show"],
        [
            (0, *won("bob", 72)),
            (1, *won("bob", 73, "15.00")),
            (1.5, "bob", "red one for #72"),
            (2, "bob", "blue one for #73"),
        ],
    )
    run(app, shop["show"])
    read_lots(
        client,
        shop,
        {
            "bob": [
                lot(CHOOSE.format(72), "1000000001", "$17.00"),
                lot(CHOOSE.format(73), "1000000002", "$15.00"),
            ]
        },
    )
    assert slip_rows(app, shop["show"])["bob"] == [
        ("Listing 72", "No Pick", ["red one for #72"], 0),
        ("Listing 73", "No Pick", ["blue one for #73"], 0),
    ]


def test_rows_without_a_matched_win_still_print_the_chat(client, app, shop, monkeypatch):
    """A lot the app has no win for gets the lines naming its number; a win Whatnot does not
    list gets its own chat like any other."""
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    say_at(
        app,
        shop["show"],
        [
            (0, "jess88", "#79 the blue lamp please"),  # the win itself was never read
            (1, "jess88", "thanks!"),
            (2, "", "Amy won the auction for Quick Hit 3 for $8.00", "player"),
            (3, "Amy", "red please"),
        ],
    )
    run(app, shop["show"])
    read_lots(client, shop, {"jess88": [lot(CHOOSE.format(79), "1000000001", "$15.00")]})
    rows = slip_rows(app, shop["show"])
    assert rows["jess88"] == [("Listing 79", "No Pick", ["#79 the blue lamp please"], 0)]
    assert rows["Amy"] == [("Quick Hit 3", "No Pick", ["red please"], 0)]  # not on Whatnot


def test_slips_before_the_shipments_read_print_the_chat_too(app, world):
    say_at(app, world["show"], [(0, *won("bob", 73)), (1, "bob", "green one")])
    run(app, world["show"])
    assert slip_rows(app, world["show"])["bob"] == [("Listing 73", "No Pick", ["green one"], 0)]
