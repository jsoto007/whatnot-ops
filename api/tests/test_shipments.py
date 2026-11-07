"""Whatnot's Shipments table, read by the scraper after a show: stored as evidence and, with
its lots, made into the show's pick list. It never changes a sale, an order or stock.

Lot markup and labels are as seen on the shop's Seller Hub on 2026-11-05; buyers, order
numbers and prices here are made up."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import Sale, Show, WhatnotLot, WhatnotShipment
from app.services import order_slips, shipments
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


TODAY = date(2026, 11, 5)


@pytest.mark.parametrize(
    "text,expected",
    [
        # The real page: "Oct 22", no year and no time. The year is the latest not ahead.
        ("Oct 22", (date(2026, 10, 22), False)),
        ("Nov 6", (date(2026, 11, 6), False)),  # a day ahead: a zone edge, still this year
        ("Dec 25", (date(2025, 12, 25), False)),  # ahead: last year's
        ("Feb 30", (None, False)),
    ],
)
def test_order_dates_without_a_year(text, expected):
    assert shipments.ordered_on(text, TODAY) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        # 1:44 AM in New York is 12:44 AM in the shop's zone: before 5 AM, the night before.
        (
            "$1 START ON EVERYTHING + $10,000 On Amazon Gift Cards Oct 22•1:44 AM EDT",
            date(2026, 10, 21),
        ),
        ("Sunday Funday Oct 26•7:00 PM CDT", date(2026, 10, 26)),
        # 1:30 AM CDT on the 27th, before the rollover: still the 26th's show
        ("Sunday Funday Oct 26 • 11:30 PM PDT", date(2026, 10, 26)),
        ("Morning Oct 27 • 4:00 AM PDT", date(2026, 10, 27)),  # 6 AM CDT: that day's show
        ("Decks 25 Mega Nov 3•8:00 PM CST", date(2026, 11, 3)),  # "Decks 25" is no date
        ("Mystery Oct 31, 2025•9:00 PM EDT", date(2025, 10, 31)),
        ("All Shows & Marketplace", None),
        ("Show Oct 22", None),  # no start time: not trusted
        ("", None),
    ],
)
def test_the_show_filter_label_names_the_show_date(label, expected):
    assert shipments.show_on(label, TODAY) == expected


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
        "lots": 0,
        "skipped": 1,
        "unmatched": 1,
        "shows": {str(shop["show"]): 2},
        "show": None,
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


def lot(title, order, price, qty="1", sale_type="Auction", shipping="$0.00"):
    return {
        "title": title,
        "orderId": order,
        "quantity": qty,
        "price": price,
        "shipping": shipping,
        "saleType": sale_type,
    }


LABEL = "$1 START ON EVERYTHING Oct 31•8:00 PM CDT"


def test_lots_read_under_the_show_filter_make_the_pick_list(client, app, shop, monkeypatch):
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    shop["say"](
        app,
        [
            ("", "jess88 won the auction for CHOOSE ANY PRODUCT YOU WANT #73 for $17.00", "player"),
            ("jess88", "can I get the Fitness Ball please"),
            ("", "Amy won the auction for Quick Hit 3 for $8.00", "player"),
        ],
    )
    run(app, shop["show"])
    with app.app_context():
        before = db.session.execute(select(Sale.id, Sale.status, Sale.sku_id)).all()

    read = {
        "show": LABEL,
        "rows": [
            # The real page says only "Oct 22": the filter, not the row, names the show.
            row("jess88", items="2", order_date="Oct 22", value="$32.00")
            | {
                "lots": [
                    lot(
                        "CHOOSE ANY PRODUCT YOU WANT #73", "1462657228", "$17.00", shipping="$5.25"
                    ),
                    lot("CHOOSE ANY PRODUCT YOU WANT #79", "1462660717", "$15.00"),
                ]
            },
            row("nolan_k", items="2", order_date="Oct 22", value="$20.00")
            | {
                "lots": [
                    lot("Ultra 9", "1462661450", "$20.00", qty="2"),
                    lot("Giveaway #4", "1462661999", "$0.00", sale_type="Giveaway"),
                    lot("no order number", "", "$1.00"),  # dropped: nothing to key it by
                ]
            },
        ],
    }
    r = client.post("/api/ingest/shipments", json=read, headers=shop["auth"])
    assert r.status_code == 200
    assert r.get_json() == {
        "stored": 2,
        "lots": 4,
        "skipped": 0,
        "unmatched": 0,
        "shows": {str(shop["show"]): 2},
        "show": {"id": shop["show"], "date": "2026-10-31"},
    }
    # Reading again (after shipping) refreshes the same lots.
    assert client.post("/api/ingest/shipments", json=read, headers=shop["auth"]).status_code == 200

    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(WhatnotLot)) == 4
        assert db.session.scalar(select(func.count()).select_from(WhatnotShipment)) == 2
        assert db.session.scalar(select(WhatnotShipment.show_label).limit(1)) == LABEL
        # Read-only: matching lots to sales changed no sale.
        assert db.session.execute(select(Sale.id, Sale.status, Sale.sku_id)).all() == before
        amy, jess = (
            db.session.scalar(select(Sale.id).where(Sale.buyer_username == n))
            for n in ("Amy", "jess88")
        )
        doc = order_slips.build(db.session.get(Show, shop["show"]))

    rows = {
        b["username"]: [
            (
                x["auction"],
                x["product"],
                x["priceCents"],
                x["whatnotOrder"],
                x["saleId"],
                x["onWhatnot"],
                x["said"],
            )
            for x in b["rows"]
        ]
        for b in doc["slips"]
    }
    assert rows["jess88"] == [
        # The win in chat, joined by buyer + lot number + price; the pick is what they said.
        (
            "Listing 73",
            "No Pick",
            1700,
            "1462657228",
            jess,
            True,
            ["can I get the Fitness Ball please"],
        ),
        ("Listing 79", "No Pick", 1500, "1462660717", None, True, []),  # chat never showed it
    ]
    assert rows["nolan_k"] == [
        ("Ultra 9", "No Pick", 2000, "1462661450", None, True, []),
        ("Ultra 9", "No Pick", None, "1462661450", None, True, []),  # qty 2: two rows to tick
        ("Gift", "Gift", 0, "1462661999", None, True, []),
    ]
    # A sale Whatnot does not list stays on the slip, flagged.
    assert rows["Amy"] == [("Quick Hit 3", "No Pick", 800, None, amy, False, [])]
    w = doc["whatnot"]
    assert (w["lots"], w["lotsNotInApp"], w["salesNotOnWhatnot"]) == (4, 3, 1)
    assert doc["totals"] == {"buyers": 3, "items": 6, "gifts": 0}


def test_a_lot_matches_its_sale_by_whatnot_order_number_first(client, app, shop, monkeypatch):
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    shop["say"](app, [("", "jess88 won the auction for Red bundle for $25.00", "player")])
    run(app, shop["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        sale.external_order_id = "555"  # from the payment file
        db.session.commit()
        sale_id = sale.id
    client.post(
        "/api/ingest/shipments",
        json={"show": LABEL, "rows": [row("jess88") | {"lots": [lot("Red bundle", "555", "$25")]}]},
        headers=shop["auth"],
    )
    with app.app_context():
        doc = order_slips.build(db.session.get(Show, shop["show"]))
    [r] = doc["slips"][0]["rows"]
    assert (r["saleId"], r["product"], r["matched"]) == (sale_id, "Red bundle", True)


def test_a_filter_naming_no_show_here_files_nothing(client, app, shop, monkeypatch):
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    r = client.post(
        "/api/ingest/shipments",
        json={
            "show": "Another show Sep 2•8:00 PM CDT",
            # The row's own date would match the show: the filter wins, so it is not filed.
            "rows": [row("jess88") | {"lots": [lot("Quick Hit 1", "777", "$5")]}],
        },
        headers=shop["auth"],
    )
    assert r.get_json()["show"] is None and r.get_json()["unmatched"] == 1
    with app.app_context():
        assert db.session.scalar(select(WhatnotLot.show_id)) is None


@pytest.mark.parametrize(
    "text,today,expected",
    [
        # Year end (architecture review 2026-11-07, #9): a year-less date just after New
        # Year read on Dec 31 is next year's; one just before it read on Jan 1 is last year's.
        ("Jan 2", date(2026, 12, 31), (date(2027, 1, 2), False)),
        ("Jan 1", date(2026, 12, 31), (date(2027, 1, 1), False)),
        ("Dec 30", date(2027, 1, 1), (date(2026, 12, 30), False)),
        ("Mar 3", date(2026, 12, 31), (date(2026, 3, 3), False)),
        ("Feb 29", date(2026, 11, 5), (None, False)),
    ],
)
def test_order_dates_without_a_year_cross_new_year(text, today, expected):
    assert shipments.ordered_on(text, today) == expected


def test_a_late_new_years_eve_show_label_read_on_dec_31_names_that_show():
    # 12:30 AM Eastern on Jan 1 is 11:30 PM Central on Dec 31: that night's show.
    assert shipments.show_on("Countdown Jan 1•12:30 AM EST", date(2026, 12, 31)) == date(
        2026, 12, 31
    )


def test_a_read_again_that_cannot_place_its_rows_keeps_where_they_were_filed(
    client, app, shop, monkeypatch
):
    """Reading Shipments again after "Start fresh" made a second show that day must not
    un-file what the first read filed (architecture review 2026-11-07, #3)."""
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    read = {
        "show": LABEL,
        "rows": [
            row("jess88", order_date="Oct 31") | {"lots": [lot("Quick Hit 1", "9001", "$5")]},
            row("amy", order_date="Oct 31") | {"lots": [lot("Quick Hit 2", "9002", "$6")]},
        ],
    }
    first = client.post("/api/ingest/shipments", json=read, headers=shop["auth"]).get_json()
    assert first["unmatched"] == 0 and first["show"]["id"] == shop["show"]
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        db.session.add(
            Show(
                organization_id=show.organization_id,
                show_key="started-fresh",
                started_at=show.started_at,
                show_date=show.show_date,
            )
        )
        db.session.commit()
    again = client.post("/api/ingest/shipments", json=read, headers=shop["auth"]).get_json()
    assert again["unmatched"] == 0 and again["shows"] == {str(shop["show"]): 2}
    with app.app_context():
        assert set(db.session.scalars(select(WhatnotLot.show_id))) == {shop["show"]}
        assert set(db.session.scalars(select(WhatnotShipment.show_id))) == {shop["show"]}
        assert order_slips.build(db.session.get(Show, shop["show"]))["whatnot"] is not None


def test_a_misread_quantity_prints_a_bounded_slip_and_asks_a_person(client, app, shop, monkeypatch):
    """Qty "999" is a misread column: at most MAX_UNIT_ROWS lines, and the buyer is flagged
    (architecture review 2026-11-07, #16)."""
    monkeypatch.setattr(shipments, "_today", lambda: TODAY)
    read = {
        "show": LABEL,
        "rows": [row("jess88") | {"lots": [lot("Quick Hit 1", "9101", "$5", qty="999")]}],
    }
    assert client.post("/api/ingest/shipments", json=read, headers=shop["auth"]).status_code == 200
    with app.app_context():
        doc = order_slips.build(db.session.get(Show, shop["show"]))
    [slip] = [b for b in doc["slips"] if b["username"] == "jess88"]
    assert len(slip["rows"]) == order_slips.MAX_UNIT_ROWS and slip["needsReview"]
