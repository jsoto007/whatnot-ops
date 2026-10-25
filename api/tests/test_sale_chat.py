"""A sale carries its win time and what the winner said in chat around it (sale_chat).

Collected only, never acted on: a "can I get the blue one instead" stays chatter and the
sale is unchanged; a person compares the two on the show's page or in the workbook.
"""

import io
from datetime import UTC, datetime, timedelta

from openpyxl import load_workbook
from sqlalchemy import select

from app.db import db
from app.models import ChatMessage, Organization, Sale, Show, User
from app.services import reports, sale_chat
from tests.test_pipeline import run

T0 = datetime(2026, 10, 20, 1, 30, tzinfo=UTC)  # 8:30 PM Central on Oct 19


def win(buyer, item="Red bundle", price="25.00"):
    return f"{buyer} won the auction for {item} for ${price}"


def say_at(app, show_id, lines):
    """Lines are (minutes after T0, username, body[, source])."""
    with app.app_context():
        n = db.session.scalar(select(db.func.count()).select_from(ChatMessage)) or 0
        for i, (mins, u, b, *source) in enumerate(lines, start=n + 1):
            db.session.add(
                ChatMessage(
                    show_id=show_id,
                    client_id=f"{i:016x}",
                    seq=i,
                    username=u,
                    body=b,
                    raw_text=f"{u} {b}".strip(),
                    source=source[0] if source else "chat",
                    captured_at=T0 + timedelta(minutes=mins),
                )
            )
        db.session.commit()


def chat_of(app, show_id):
    with app.app_context():
        rows = db.session.scalars(
            select(Sale).where(Sale.show_id == show_id).order_by(Sale.id)
        ).all()
        got = sale_chat.around(rows)
        return [
            (s.buyer_username, sale_chat.won_at(s), [(c["body"], c["role"]) for c in got[s.id]])
            for s in rows
        ]


def test_the_winners_lines_around_the_win_are_kept_with_the_sale(app, world):
    say_at(
        app,
        world["show"],
        [
            (-3, "bob", "too early, belongs to nothing"),
            (-1, "bob", "if I win I want the blue one"),
            (0, "", win("bob"), "player"),
            (1, "bob", "can I get the blue one instead?"),
            (2, "host_shop", "ok bob, swapped you to blue for $30"),
            (3, "kat.c", "@bob congrats"),
            (4, "kat.c", "bob is lucky"),  # a viewer naming him bare: not his
            (5, "bobby", "I'm not bob's line"),
            (6, "carol", "unrelated"),
            (11, "bob", "past the window"),
        ],
    )
    run(app, world["show"])
    [(buyer, won, lines)] = chat_of(app, world["show"])
    assert (buyer, won) == ("bob", T0)
    assert lines == [
        ("if I win I want the blue one", "buyer"),
        ("can I get the blue one instead?", "buyer"),
        ("ok bob, swapped you to blue for $30", "host"),
        ("@bob congrats", "other"),
    ]


def test_the_chat_changes_nothing_on_the_sale(app, world):
    say_at(
        app,
        world["show"],
        [(0, "", win("bob"), "player"), (1, "bob", "actually I want it for $10")],
    )
    run(app, world["show"])
    with app.app_context():
        s = db.session.scalar(select(Sale))
        assert (s.buyer_username, s.item_text, s.price_cents) == ("bob", "Red bundle", 2500)


def test_each_line_goes_to_one_win_of_the_same_buyer(app, world):
    say_at(
        app,
        world["show"],
        [
            (0, "", win("bob", "Red bundle"), "player"),
            (1, "bob", "about the first"),
            (5, "", win("bob", "Blue bundle", "12.00"), "player"),
            (6, "bob", "about the second"),
            (4, "bob", "between: goes to the latest win before it"),
        ],
    )
    run(app, world["show"])
    first, second = chat_of(app, world["show"])
    assert [b for b, _ in first[2]] == [
        "about the first",
        "between: goes to the latest win before it",
    ]
    assert [b for b, _ in second[2]] == ["about the second"]
    assert second[1] == T0 + timedelta(minutes=5)


def test_handles_match_whole_and_case_insensitively(app, world):
    say_at(
        app,
        world["show"],
        [
            (0, "", win("a.b-c_9"), "player"),
            (1, "A.B-C_9", "my line, other case"),
            (2, "x", "@a.b-c_9."),
            (3, "x", "@a.b-c_99 is someone else"),
            (4, "x", "mail@a.b-c_9 is not a mention"),
        ],
    )
    run(app, world["show"])
    [(_, _, lines)] = chat_of(app, world["show"])
    assert [b for b, _ in lines] == ["my line, other case", "@a.b-c_9."]


def test_lines_per_sale_are_capped(app, world):
    say_at(
        app,
        world["show"],
        [(0, "", win("bob"), "player")]
        + [(i / 10, "bob", f"line {i}") for i in range(1, sale_chat.MAX_LINES + 5)],
    )
    run(app, world["show"])
    [(_, _, lines)] = chat_of(app, world["show"])
    assert len(lines) == sale_chat.MAX_LINES and lines[0][0] == "line 1"


def _owner_client(app, client, world):
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "organization": "O",
            "name": "O",
            "email": "o@x.test",
            "password": "correct-horse-battery",
        },
        headers=h,
    )
    with app.app_context():
        u = db.session.scalar(select(User))
        u.organization_id = world["org"]
        db.session.commit()


def test_the_show_sales_api_sends_win_time_and_chat(app, client, world):
    say_at(
        app,
        world["show"],
        [(0, "", win("bob"), "player"), (1, "bob", "can I get the blue one instead?")],
    )
    run(app, world["show"])
    _owner_client(app, client, world)
    [s] = client.get(f"/api/shows/{world['show']}/sales").get_json()["sales"]
    assert datetime.fromisoformat(s["wonAt"]) == T0 and s["source"] == "player"
    assert [(c["username"], c["body"], c["role"]) for c in s["chat"]] == [
        ("bob", "can I get the blue one instead?", "buyer")
    ]


def test_the_workbook_has_won_at_in_central_time_and_the_buyer_chat(app, world):
    say_at(
        app,
        world["show"],
        [
            (0, "", win("bob"), "player"),
            (1, "bob", '=HYPERLINK("http://x") blue instead?'),
        ],
    )
    run(app, world["show"])
    with app.app_context():
        org = db.session.get(Organization, world["org"])
        data = reports.build(org, show=db.session.get(Show, world["show"]))
    ws = load_workbook(io.BytesIO(data))["Sales by show"]
    header = [c.value for c in ws[1]]
    assert header[-2:] == ["Won at (Central)", "Buyer chat"]
    row = [c for c in ws[2]]
    assert row[-2].value == "2026-10-19 20:30:00"
    assert row[-1].value == '#2 bob: =HYPERLINK("http://x") blue instead?'
    assert row[-1].data_type == "s"
