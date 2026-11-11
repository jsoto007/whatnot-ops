from datetime import UTC, date, datetime

import pytest
from flask_migrate import upgrade
from sqlalchemy import select, text

from app import create_app
from app.config import Config
from app.db import db
from app.models import ChatMessage, MovementKind, Organization, Show
from app.services import catalog, inventory
from app.services.devices import create_device

SHOW = "2026-10-19T20:00:00.000Z"

TABLES = [
    "audit_log",
    "password_resets",
    "invites",
    "users",
    "chat_messages",
    "shows",
    "rate_limits",
    "device_pairings",
    "devices",
    "organizations",
]


@pytest.fixture(scope="session")
def app():
    app = create_app(Config(testing=True))
    # Real migrations on the test database: a broken revision fails the suite.
    # The context is popped again on purpose: a context left pushed for the whole
    # session would make every request share one SQLAlchemy session, which then
    # sits idle-in-transaction and blocks the next test's TRUNCATE.
    with app.app_context():
        upgrade()
    return app


@pytest.fixture(autouse=True)
def clean_tables(app):
    with app.app_context():
        db.session.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
        db.session.commit()
    yield
    with app.app_context():
        db.session.remove()


@pytest.fixture(autouse=True)
def _jobs_as_in_ci(monkeypatch):
    """A developer's .env sets JOBS_INLINE=1 (jobs run the moment they are queued); CI and
    production do not. Every test starts without it, so the suite behaves here as it does
    in CI: a test that needs a job to have run by the next line asks for `inline_jobs`.
    Twenty Whatnot tests once passed on a laptop and failed in CI for want of this."""
    monkeypatch.delenv("JOBS_INLINE", raising=False)


@pytest.fixture
def inline_jobs(monkeypatch, _jobs_as_in_ci):
    """Jobs run synchronously at enqueue time, as in local development and the browser tests."""
    monkeypatch.setenv("JOBS_INLINE", "1")


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def device(app):
    """An organization with one active device; returns (device, plaintext key)."""
    from app.models import Organization
    from app.services.devices import create_device

    with app.app_context():
        org = Organization(name="Test Shop", slug="test-shop")
        db.session.add(org)
        db.session.flush()
        dev, key = create_device(org, "laptop")
        db.session.commit()
        return dev.id, key


@pytest.fixture
def auth(device):
    return {"Authorization": "Bearer " + device[1]}


@pytest.fixture
def world(app):  # shared by the pipeline and AI tests
    """An org with RED (3 on hand), a show, and a helper to add chat lines."""

    with app.app_context():
        org = Organization(name="Shop", slug="shop", host_username="host_shop")
        db.session.add(org)
        db.session.flush()
        red = catalog.create_product(
            org.id, "Red bundle", skus=[{"code": "RED", "priceCents": 2500}]
        ).skus[0]
        inventory.move(red.id, MovementKind.restock, 3, source_type="test", source_id="seed")
        show = Show(
            organization_id=org.id,
            show_key=SHOW,
            started_at=datetime.fromisoformat(SHOW.replace("Z", "+00:00")),
            show_date=datetime.now(UTC).date(),
        )
        db.session.add(show)
        db.session.commit()
        ids = {"org": org.id, "red": red.id, "show": show.id}

    def say(app, lines):
        """Lines are (username, body) chat lines, or (username, body, "player") wins."""
        with app.app_context():
            n = db.session.scalar(select(db.func.count()).select_from(ChatMessage)) or 0
            for i, (u, b, *source) in enumerate(lines, start=n + 1):
                db.session.add(
                    ChatMessage(
                        show_id=ids["show"],
                        client_id=f"{i:016x}",
                        seq=i,
                        username=u,
                        body=b,
                        raw_text=f"{u} {b}".strip(),
                        source=source[0] if source else "chat",
                        captured_at=datetime.now(UTC),
                    )
                )
            db.session.commit()

    ids["say"] = say
    return ids


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


@pytest.fixture
def fake_whatnot(app):
    """An in-memory Whatnot behind the real client. Without this fixture a call to Whatnot
    from a test raises: no test may reach the live shop. Retries do not sleep; the waits
    they asked for are in `fake.sleeps`."""
    from app.services.whatnot.fake import TOKEN, FakeWhatnot

    fake = FakeWhatnot()
    fake.sleeps = []
    app.config.update(
        WHATNOT_TRANSPORT=fake.transport,
        WHATNOT_API_TOKEN=TOKEN,
        WHATNOT_SLEEP=fake.sleeps.append,
        WHATNOT_FETCH=fake.fetch,  # a link Whatnot hands out: a report's file, or a label
    )
    yield fake
    app.config.pop("WHATNOT_TRANSPORT", None)
    app.config.pop("WHATNOT_FETCH", None)
    app.config.pop("WHATNOT_SLEEP", None)
    app.config["WHATNOT_API_TOKEN"] = ""
    app.config["WHATNOT_WEBHOOK_SECRET"] = ""


@pytest.fixture
def sign_in(app, client):
    """sign_in(org_id, role="owner") -> CSRF header for a person signed in to that shop."""
    from app.models import Role, User

    def go(org_id: int, role: str = "owner", email: str = "o@x.test") -> dict:
        h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
        client.post(
            "/api/auth/signup",
            json={
                "organization": "O",
                "name": "Olive",
                "email": email,
                "password": "correct-horse-battery",
            },
            headers=h,
        )
        with app.app_context():
            u = db.session.scalar(select(User).where(User.email == email))
            u.organization_id = org_id
            u.role = Role(role)
            db.session.commit()
        return h

    return go
