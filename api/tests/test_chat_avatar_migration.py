"""Migration 0028: chat lines saved under Whatnot's avatar letter get their writer back."""

import importlib.util
from pathlib import Path

from sqlalchemy import select, text

from app.db import db
from app.models import ChatMessage
from tests.test_pipeline import run

_path = Path(__file__).parents[1] / "migrations/versions/0028_chat_avatar_letter.py"
_spec = importlib.util.spec_from_file_location("m0028", _path)
m0028 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m0028)

WINNERS = ("lani808", "kat.c", "4x4_deals", "buyer77")


def test_lines_saved_under_the_avatar_letter_get_their_writer_back(app, world):
    win = "{} won the auction for CHOOSE ANY PRODUCT YOU WANT #{} for $17.00"
    world["say"](app, [("", win.format(b, n), "player") for n, b in enumerate(WINNERS, 1)])
    run(app, world["show"])
    world["say"](
        app,
        [
            ("L", "lani808 B4"),
            ("k", "Kat.c New Buyer A3"),
            ("4", "4x4_deals Do a buyers giveaway only."),
            ("J", "buyer77 joined"),  # not that name's initial: a real one-letter name
            ("B", "buyer77"),  # nothing after the name: left alone
            ("lani808", "L sold"),  # a name of its own
            ("s", "stranger7 B4"),  # not a winner: maybe a real one-letter name, left alone
            ("L", "l lol x"),  # a one-letter word is never a name
        ],
    )
    with app.app_context():
        for _ in range(2):  # a second run changes nothing
            db.session.execute(text(m0028.SQL))
            db.session.commit()
            got = db.session.execute(
                select(ChatMessage.username, ChatMessage.body)
                .where(ChatMessage.source == "chat")
                .order_by(ChatMessage.seq)
            ).all()
            assert [tuple(r) for r in got] == [
                ("lani808", "B4"),
                ("Kat.c", "New Buyer A3"),
                ("4x4_deals", "Do a buyers giveaway only."),
                ("J", "buyer77 joined"),
                ("B", "buyer77"),
                ("lani808", "L sold"),
                ("s", "stranger7 B4"),
                ("L", "l lol x"),
            ]
        raw = db.session.scalar(
            select(ChatMessage.raw_text)
            .where(ChatMessage.source == "chat")
            .order_by(ChatMessage.seq)
        )
        assert raw == "L lani808 B4"  # as captured
