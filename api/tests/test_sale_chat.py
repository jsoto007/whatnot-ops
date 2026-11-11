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
            (45, "bob", "sorry, was away: the green one"),  # a slow pick is still his
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
        ("sorry, was away: the green one", "buyer"),
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
            "acceptLicence": True,
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


# ---- naming many buyers at once (architecture review 2026-11-07, finding #1) ----


def test_named_agrees_with_names_on_tricky_handles():
    """`_Named` answers what `_names` answers, buyer by buyer, for every text: handles with
    "." and "-", a trailing dot, "@" glued to a word, case, non-ASCII neighbours. Only
    look-alike letters that re.I folds into ASCII differ (stricter), as asserted last."""
    import random

    rnd = random.Random(7)
    alphabet = "bobBOkat.-_@ é1!,:"
    buyers = ["bob", "bob.", "bob-x", "kat.c", "k", "b_o", "ok", "bob.b", "é", "bob smith", ""]
    named = sale_chat._Named(buyers)
    for _ in range(4000):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 14)))
        for bare in (True, False):
            want = {b for b in buyers if sale_chat._names(text, b, bare=bare)}
            assert named(text, bare=bare) == want, (text, bare)
    # The one deliberate difference: re.I folds look-alikes ("ſ" -> "s"); the lookup does not.
    assert sale_chat._names("@boſs", "boss", bare=True)
    assert sale_chat._Named(["boss"])("@boſs", bare=True) == set()


def _reference_around(sales):
    """The pre-2026-11-07 algorithm (one regex per line x buyer), kept to check the new one."""
    from collections import defaultdict

    from app.services.sale_chat import BEFORE, _handle, _line, _lines, _names, _owners, lot_of

    out = {s.id: [] for s in sales}
    sales = [s for s in sales if sale_chat.won_at(s) is not None and s.buyer_username]
    show = sales[0].show
    host = _handle(show.organization.host_username or "")
    wins = defaultdict(list)
    for s in sales:
        wins[_handle(s.buyer_username)].append((sale_chat.won_at(s), s))
    for w in wins.values():
        w.sort(key=lambda x: (x[0], x[1].id))
    lots = {s.id: lot_of(s) for s in sales}
    own = {s.event.message_id for s in sales}
    waiting = {s.id for s in sales if s.sku_id is None}
    since = min(sale_chat.won_at(s) for s in sales) - BEFORE
    for m in _lines(show, list(wins), host, since):
        if m.id in own:
            continue
        named = sale_chat.rules.lot_refs(m.body)
        for buyer, w in wins.items():
            user = _handle(m.username)
            is_host = bool(host) and user == host
            if user == buyer:
                role = "buyer"
            elif _names(m.raw_text, buyer, bare=not user or is_host):
                role = "platform" if not user else "host" if is_host else "other"
            else:
                continue
            for sale, after, by_lot in _owners(w, m.captured_at, named, lots, waiting):
                if role == "buyer":
                    waiting.discard(sale.id)
                out[sale.id].append(_line(m, role, after=after, names_lot=by_lot))
    return out


def test_around_matches_the_reference_on_a_busy_show(app, world):
    """40 buyers, back-to-back wins, @-mentions, host and platform lines, lot numbers."""
    import random

    rnd = random.Random(11)
    buyers = [f"buyer{i}" for i in range(40)] + ["kat.c", "bob-x", "Mixed.Case"]
    lines, minute = [], 0.0
    for lot in range(1, 121):
        minute += rnd.uniform(0.2, 2.0)
        b = rnd.choice(buyers)
        lines.append((minute, "", win(b, f"CHOOSE ANY PRODUCT YOU WANT #{lot}", "5.00"), "player"))
        for _ in range(rnd.randint(0, 6)):
            minute += rnd.uniform(0.01, 0.5)
            who = rnd.choice([*buyers, "host_shop", "viewer1", "", "bob-x"])
            other = rnd.choice(buyers)
            body = rnd.choice(
                [
                    f"the red one for #{lot}",
                    f"@{other} congrats",
                    f"{other} you got it",
                    f"#{rnd.randint(1, lot)} blue please",
                    "gl everyone",
                    f"@{other}. nice",
                    f"{other.upper()} pick one",
                ]
            )
            lines.append((minute, who, body))
    say_at(app, world["show"], lines)
    run(app, world["show"])
    with app.app_context():
        rows = db.session.scalars(
            select(Sale).where(Sale.show_id == world["show"]).order_by(Sale.id)
        ).all()
        assert len(rows) > 100
        got = sale_chat.around(rows, limit=None)
        assert got == _reference_around(rows)
        assert sum(map(len, got.values())) > 200


def test_naming_scales_past_the_regex_cache():
    """600 buyers x 5,000 lines: one pass per line, not a regex per buyer (the old way spent
    ~30 us per check above 256 buyers, minutes for this)."""
    import time

    buyers = [f"buyer{i:04d}" for i in range(600)]
    named = sale_chat._Named(buyers)
    texts = [f"@buyer{i % 700:04d} what do you want? buyer{(i * 7) % 600:04d}" for i in range(5000)]
    t = time.perf_counter()
    hits = sum(len(named(x, bare=True)) for x in texts)
    assert time.perf_counter() - t < 2.0
    every = set(buyers)
    assert hits == sum(
        len({f"buyer{i % 700:04d}", f"buyer{(i * 7) % 600:04d}"} & every) for i in range(5000)
    )
