import io

from openpyxl import load_workbook
from sqlalchemy import select

from app.db import db
from app.models import Organization, Show, Sku, User
from app.services import dashboard, reconcile, reports
from app.services.inventory import Actor
from tests.test_pipeline import run


def seed(app, world):
    world["say"](
        app,
        [
            ("", "Auction #1: Red bundle"),
            ("", "lani808 won Red bundle x2"),
            ("", "kat.c won Red bundle"),
            ("", "kat.c won the giveaway!"),
            ("", "No bids"),
            ("host_shop", "cancelled for kat.c"),
        ],
    )
    run(app, world["show"])
    with app.app_context():
        show = db.session.get(Show, world["show"])
        u = User(
            organization_id=world["org"],
            email="o@x.test",
            name="O",
            password_hash="x",
            role="owner",
            session_token="t",
        )
        db.session.add(u)
        db.session.flush()
        reconcile.apply(
            show,
            reconcile.plan(
                show,
                reconcile.parse_csv("Buyer,Product,Status,Order ID\nlani808,Red bundle,paid,W1\n"),
            ),
            actor=Actor.user(u),
            import_id="i",
        )
        db.session.commit()


def test_show_tiles_match_the_sheet_and_the_money(app, world):
    seed(app, world)
    with app.app_context():
        t = dashboard.show_tiles(db.session.get(Show, world["show"]))
    assert (
        t["itemsSold"],
        t["buyers"],
        t["red"],
        t["bundles"],
        t["gifts"],
        t["noPicks"],
        t["noWinner"],
        t["cancelled"],
    ) == (2, 1, 2, 0, 1, 0, 1, 1)
    assert t["revenuePaidCents"] == 5000 and t["revenuePendingCents"] == 0
    assert t["toFulfil"] == 1 and t["orders"] == {"paid": 1, "cancelled": 1}
    assert t["live"] is True


def test_overview_and_top_lists(app, world):
    seed(app, world)
    with app.app_context():
        o = dashboard.overview(world["org"])
    assert (
        o["totals"]["shows"] == 1
        and o["totals"]["itemsSold"] == 2
        and o["totals"]["revenuePaidCents"] == 5000
    )
    assert o["topBuyers"][0] == {"buyer": "lani808", "items": 2, "paidCents": 5000, "shows": 1}
    assert (
        o["topSkus"][0]["code"] == "RED"
        and o["topSkus"][0]["sold"] == 2
        and o["topSkus"][0]["available"] == 1
    )
    assert o["topSkus"][0]["sellThrough"] == round(2 / 3, 3)
    assert [s["code"] for s in o["lowStock"]] == []  # low_stock_at defaults to 0 and 1 is available


def test_workbook_sheets_rows_and_currency_cells(app, world):
    seed(app, world)
    with app.app_context():
        org = db.session.get(Organization, world["org"])
        data = reports.build(org, show=db.session.get(Show, world["show"]))
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == [
        "Sales by show",
        "Customers",
        "Products sold",
        "Inventory remaining",
        "Cancellations",
        "Revenue",
        "Walked away",
        "Fulfilment",
        "Stock movements",
    ]
    sales = list(wb["Sales by show"].iter_rows(values_only=True))
    assert sales[0][:5] == ("Show", "Buyer", "Item", "SKU", "Qty")
    assert sales[1][1:5] == ("lani808", "Red bundle", "RED", 2)
    assert (
        sales[1][6] == 50.0 and wb["Sales by show"]["G2"].number_format == '"$"#,##0.00'
    )  # total as dollars
    assert [r[0] for r in list(wb["Cancellations"].iter_rows(values_only=True))[1:]] == [
        sales[1][0]
    ]
    customers = list(wb["Customers"].iter_rows(values_only=True))
    assert customers[1] == ("lani808", 2, 50.0, 1, sales[1][0])
    revenue = list(wb["Revenue"].iter_rows(values_only=True))[1]
    assert revenue[2:5] == (2, 1, 50.0) and revenue[7:10] == (1, 0, 1)
    walked = list(wb["Walked away"].iter_rows(values_only=True))
    assert walked[0] == ("Show", "Buyer", "Outcome", "Item", "Qty", "Line #", "Note")
    assert [w[1:3] for w in walked[1:]] == [("kat.c", "Cancelled")]
    fulfil = list(wb["Fulfilment"].iter_rows(values_only=True))
    assert fulfil[1][1:6] == ("lani808", "ready", "RED", "Red bundle", 2)
    inv_rows = list(wb["Inventory remaining"].iter_rows(values_only=True))
    assert inv_rows[0][-1] == "Weight (oz)"
    inv = inv_rows[1]
    assert inv[:5] == ("Red bundle", "RED", 1, 0, 1) and inv[-1] is None  # not weighed
    moves = list(wb["Stock movements"].iter_rows(values_only=True))
    # every win is paid at once (reserve + commit); kat.c's cancelled win is restocked
    # in words, not ledger codes (reserve / commit / restock)
    assert [m[2] for m in moves[1:]] == [
        "restocked",
        "held for a sale",
        "paid",
        "held for a sale",
        "paid",
        "restocked",
    ]


def test_report_routes_and_permissions(client, app, world):
    seed(app, world)
    r = client.get("/api/auth/csrf")
    h = {"X-CSRF-Token": r.get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "acceptLicence": True,
            "organization": "O",
            "name": "O",
            "email": "o2@x.test",
            "password": "correct-horse-battery",
        },
        headers=h,
    )
    with app.app_context():
        u = db.session.scalar(select(User).where(User.email == "o2@x.test"))
        u.organization_id = world["org"]
        db.session.commit()
    r = client.get(f"/api/reports/show/{world['show']}.xlsx")
    assert (
        r.status_code == 200
        and r.data[:2] == b"PK"
        and "attachment" in r.headers["Content-Disposition"]
    )
    r = client.get("/api/reports/overview.xlsx?from=2020-01-01")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    assert client.get("/api/reports/overview.xlsx?from=nope").status_code == 400
    d = client.get("/api/dashboard").get_json()
    assert d["totals"]["itemsSold"] == 2
    assert client.get(f"/api/dashboard/shows/{world['show']}").get_json()["tiles"]["buyers"] == 1
    # fulfilment role cannot see reports
    token = (
        client.post(
            "/api/team/invites", json={"email": "p@x.test", "role": "fulfillment"}, headers=h
        )
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    pat = app.test_client()
    hp = {"X-CSRF-Token": pat.get("/api/auth/csrf").get_json()["token"]}
    pat.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "P", "password": "correct-horse-battery"},
        headers=hp,
    )
    assert pat.get(f"/api/reports/show/{world['show']}.xlsx").status_code == 403
    assert pat.get(f"/api/dashboard/shows/{world['show']}").status_code == 200


def test_overview_attention_lists_what_needs_a_person(app, world):
    seed(app, world)
    with app.app_context():
        a = dashboard.overview(world["org"])["attention"]
    assert a["openReviews"] == 0 and a["toFulfil"] == 1 and a["lowStock"] == 0  # every line matched
    assert a["live"] is not None and a["live"]["showId"] == world["show"]
    assert a["unreconciledShows"] == []  # the live show is excluded; nothing else pending


def test_attention_details_and_live_chat_pace(app, world):
    seed(app, world)
    with app.app_context():
        a = dashboard.overview(world["org"])["attention"]
        c = dashboard.counts(world["org"])
    assert a["fulfilItems"] == 2 and a["fulfilOldestShow"] == a["live"]["date"]
    assert a["reviewReasons"] == {}
    pace = a["live"]["pace"]
    assert (
        len(pace) == 30 and sum(pace) == 6 and pace[-1] == 6
    )  # all six lines landed in the last minute
    assert c == {"openReviews": 0, "toFulfil": 1, "live": True, "whatnotProblem": False}


def test_low_stock_carries_threshold_and_sold(app, world):
    seed(app, world)
    with app.app_context():
        sku = db.session.get(Sku, world["red"])
        sku.low_stock_at = 1
        db.session.commit()
        low = dashboard.overview(world["org"])["lowStock"]
    assert low == [
        {
            "skuId": world["red"],
            "code": "RED",
            "product": "Red bundle",
            "available": 1,
            "short": 0,
            "lowAt": 1,
            "sold": 2,
        }
    ]


def test_oversold_sku_reports_how_many_to_buy(app, world):
    seed(app, world)
    with app.app_context():
        from app.services import inventory

        inventory.reserve(world["red"], 4, source_type="sale", source_id="oversell")
        db.session.commit()
        d = dashboard.overview(world["org"])
        sku = db.session.get(Sku, world["red"])
        assert d["lowStock"][0]["code"] == "RED"
        assert d["lowStock"][0]["available"] == sku.available < 0
        assert d["lowStock"][0]["short"] == -sku.available
        assert [s["code"] for s in d["attention"]["oversold"]] == ["RED"]
        top = next(t for t in dashboard.top_skus(world["org"]) if t["code"] == "RED")
        assert top["sellThrough"] == 1.0


def test_winners_without_purchase_lists_every_way_a_win_went_nowhere(app, world):
    world["say"](
        app,
        [
            ("", "dave-77 won the auction!"),  # no listing yet: won, nothing picked
            ("", "Auction #1: Red bundle"),
            ("", "lani808 won Red bundle"),
            ("", "kat.c won Red bundle"),
            ("host_shop", "cancelled for kat.c"),
            ("", "pokemom_22 won Red bundle"),
            ("host_shop", "payment failed for pokemom_22"),
            ("cardguy", "won the auction!"),  # the split shape: waits in Review
            ("newbie", "won last week too"),  # chatter, not a walk-away
        ],
    )
    run(app, world["show"])
    with app.app_context():
        show = db.session.get(Show, world["show"])
        reconcile.apply(
            show,
            reconcile.plan(
                show, reconcile.parse_csv("Buyer,Product,Status\nnobody,Red bundle,paid\n")
            ),
            actor=Actor("user", None, "t"),
            import_id="i2",
        )
        db.session.commit()
        rows = dashboard.winners_without_purchase(show)
        tiles = dashboard.show_tiles(show)
    assert [(r["buyer"], r["outcome"]) for r in rows] == [
        ("dave-77", "no_item"),
        ("kat.c", "cancelled"),
        ("pokemom_22", "payment_failed"),
        ("lani808", "unpaid"),
        ("cardguy", "unconfirmed"),
    ]
    assert rows[0]["seq"] == 1 and rows[-1]["note"] == "cardguy won the auction!"
    assert tiles["noPicks"] == 1 and tiles["noWinner"] == 0


def test_quick_hit_is_tallied_like_the_other_auction_types(app, world):
    world["say"](app, [("", "lani808 won Quick Hit 188 for $6"), ("", "kat.c won Red bundle")])
    run(app, world["show"])
    with app.app_context():
        t = dashboard.show_tiles(db.session.get(Show, world["show"]))
    assert (t["quickHit"], t["red"], t["itemsSold"]) == (1, 1, 2)
    assert "quickHit" in dashboard.totals([t])


def test_overview_report_defaults_to_recent_shows_and_refuses_a_range_too_wide(
    client, app, world, monkeypatch
):
    """No dates = the last 30 days; an explicit range past the cap is refused in words (the
    workbook is built in memory inside the request: all history once took 518 MB)."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models import AuditLog, Show, User
    from app.routes import reports as reports_routes
    from app.services import showdate

    monkeypatch.setattr(reports_routes, "MAX_OVERVIEW_SHOWS", 2)
    client.get("/api/auth/me")
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "acceptLicence": True,
            "email": "rng@x.test",
            "name": "O",
            "organization": "Rng",
            "password": "correct-horse-battery",
        },
        headers=h,
    )
    today = showdate.show_date(datetime.now(UTC))
    with app.app_context():
        u = db.session.scalar(select(User).where(User.email == "rng@x.test"))
        u.organization_id = world["org"]
        db.session.get(Show, world["show"]).show_date = today
        for i, back in enumerate((3, 10, 400)):
            db.session.add(
                Show(
                    organization_id=world["org"],
                    show_key=f"old-{i}",
                    started_at=datetime.now(UTC) - timedelta(days=back),
                    show_date=today - timedelta(days=back),
                )
            )
        db.session.commit()

    def last_range():
        with app.app_context():
            row = db.session.scalar(
                select(AuditLog)
                .where(AuditLog.action == "report.downloaded")
                .order_by(AuditLog.id.desc())
            )
            return row.details

    # No dates: three shows fall in the last 30 days, the cap is two, so the newest two.
    r = client.get("/api/reports/overview.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    assert last_range() == {"from": str(today - timedelta(days=3)), "to": "None"}
    # Asked for everything: refused, with a count and what to do, and no setting named.
    r = client.get("/api/reports/overview.xlsx?from=2020-01-01")
    assert r.status_code == 400
    assert "4 shows" in r.get_json()["error"] and "shorter range" in r.get_json()["error"]
    # A range that fits is built as asked.
    r = client.get(f"/api/reports/overview.xlsx?from={today - timedelta(days=5)}")
    assert r.status_code == 200 and last_range()["from"] == str(today - timedelta(days=5))
