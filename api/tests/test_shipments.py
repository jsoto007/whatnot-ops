"""Whatnot's Shipments table, read by the scraper after a show: stored as evidence and compared
with the show's order slips. It never changes a sale, an order or stock."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import Organization, Sale, Show, WhatnotShipment
from app.services import order_slips, shipments
from app.services.devices import create_device
from tests.test_pipeline import run


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Oct 31, 2026", (date(2026, 10, 31), False)),
        ("Oct. 31, 2026", (date(2026, 10, 31), False)),
        ("10/31/2026", (date(2026, 10, 31), False)),
        ("Oct 31, 2026, 11:42 PM", (date(2026, 10, 31), True)),
        # after midnight, before the 5 AM rollover: still the night's show
        ("Nov 1, 2026 1:05 AM", (date(2026, 10, 31), True)),
        ("11/01/2026 02:10", (date(2026, 10, 31), True)),
        ("Nov 1, 2026 9:00 AM", (date(2026, 11, 1), True)),
        ("yesterday", (None, False)),
    ],
)
def test_order_dates_map_to_show_dates(text, expected):
    assert shipments.ordered_on(text) == expected


@pytest.mark.parametrize(
    "text,lines,expected",
    [
        ("3", None, 3),
        ("3 items", None, 3),
        ("1 Item", None, 1),
        ("", ["a", "b"], 2),
        ("", None, None),
    ],
)
def test_items_cell_reads_as_a_count(text, lines, expected):
    assert shipments.item_count(text, lines) == expected


@pytest.fixture
def shop(app, world):
    """The world's shop (a show today) with a paired laptop; returns ids and the key header."""
    with app.app_context():
        org = db.session.get(Organization, world["org"])
        show = db.session.get(Show, world["show"])
        show.show_date = date(2026, 10, 31)
        _, key = create_device(org, "packing laptop")
        db.session.commit()
    return {**world, "auth": {"Authorization": "Bearer " + key}}


def row(recipient, items="1", order_date="Oct 31, 2026", value="$14.89", status="Unfulfilled"):
    return {
        "recipient": recipient,
        "orderDate": order_date,
        "items": items,
        "value": value,
        "status": status,
        "tracking": "",
    }


def test_shipments_are_matched_to_the_show_and_compared_with_the_slips(client, app, shop):
    shop["say"](
        app,
        [
            ("", "jess88 won the auction for Quick Hit 2 for $14.89", "player"),
            ("", "jess88 won the auction for Quick Hit 5 for $13.17", "player"),
            ("", "Amy won the auction for Quick Hit 3 for $8.00", "player"),
        ],
    )
    run(app, shop["show"])
    with app.app_context():
        before = db.session.execute(select(Sale.id, Sale.status, Sale.sku_id)).all()

    r = client.post(
        "/api/ingest/shipments",
        json={
            "rows": [
                row("jess88", items="2 items", value="$28.06"),
                row("@nolan_k", order_date="Nov 1, 2026 1:05 AM"),  # a win the app never saw
                row("someone", order_date="Sep 2, 2026"),  # no show that day
                {"recipient": "Jane Doe\n12 Main St", "items": "1"},  # not a username: dropped
                row("jess88", items="2 items", value="$28.06"),  # the same row twice
            ]
        },
        headers=shop["auth"],
    )
    assert r.status_code == 200
    assert r.get_json() == {
        "stored": 3,
        "skipped": 1,
        "unmatched": 1,
        "shows": {str(shop["show"]): 2},
    }

    # Evidence only: no sale moved.
    with app.app_context():
        assert db.session.execute(select(Sale.id, Sale.status, Sale.sku_id)).all() == before
        doc = order_slips.build(db.session.get(Show, shop["show"]))
    assert doc["whatnot"]["shipments"] == 2
    assert doc["whatnot"]["missingHere"] == ["nolan_k"]
    assert doc["whatnot"]["notOnWhatnot"] == ["Amy"]
    jess = next(b for b in doc["slips"] if b["username"] == "jess88")
    assert jess["whatnot"] == {"items": 2, "statuses": ["Unfulfilled"]}

    # Reading the page again after shipping updates the row instead of adding one.
    client.post(
        "/api/ingest/shipments",
        json={"rows": [row("jess88", items="2 items", value="$28.06", status="Shipped")]},
        headers=shop["auth"],
    )
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotShipment)) == 3
        doc = order_slips.build(db.session.get(Show, shop["show"]))
    assert next(b for b in doc["slips"] if b["username"] == "jess88")["whatnot"]["statuses"] == [
        "Shipped"
    ]


def test_shipments_need_a_device_key_and_valid_rows(client, shop):
    assert client.post("/api/ingest/shipments", json={"rows": []}).status_code == 401
    bad = client.post("/api/ingest/shipments", json={"rows": "x"}, headers=shop["auth"])
    assert bad.status_code == 400
    many = [row(f"u{i}") for i in range(shipments.MAX_ROWS + 1)]
    assert (
        client.post("/api/ingest/shipments", json={"rows": many}, headers=shop["auth"]).status_code
        == 400
    )


def test_a_slip_without_a_shipments_read_says_nothing(app, world):
    with app.app_context():
        doc = order_slips.build(db.session.get(Show, world["show"]))
    assert doc["whatnot"] is None
