import io

from openpyxl import load_workbook
from sqlalchemy import select

from app.db import db
from app.models import Organization, Show, User
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
        t["cancelled"],
    ) == (2, 1, 2, 0, 1, 1, 1)
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
    assert revenue[2:5] == (2, 1, 50.0) and revenue[7:9] == (1, 1)
    fulfil = list(wb["Fulfilment"].iter_rows(values_only=True))
    assert fulfil[1][1:6] == ("lani808", "paid", "RED", "Red bundle", 2)
    inv = list(wb["Inventory remaining"].iter_rows(values_only=True))[1]
    assert inv[:5] == ("Red bundle", "RED", 1, 0, 1)
    moves = list(wb["Stock movements"].iter_rows(values_only=True))
    assert [m[2] for m in moves[1:]] == ["restock", "reserve", "reserve", "release", "commit"]


def test_report_routes_and_permissions(client, app, world):
    seed(app, world)
    r = client.get("/api/auth/csrf")
    h = {"X-CSRF-Token": r.get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
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
