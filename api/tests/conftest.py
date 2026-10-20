from datetime import UTC, datetime

import pytest
from flask_migrate import upgrade
from sqlalchemy import select, text

from app import create_app
from app.config import Config
from app.db import db
from app.models import ChatMessage, MovementKind, Organization, Show
from app.services import catalog, inventory

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
        with app.app_context():
            n = db.session.scalar(select(db.func.count()).select_from(ChatMessage)) or 0
            for i, (u, b) in enumerate(lines, start=n + 1):
                db.session.add(
                    ChatMessage(
                        show_id=ids["show"],
                        client_id=f"{i:016x}",
                        seq=i,
                        username=u,
                        body=b,
                        raw_text=f"{u} {b}".strip(),
                        captured_at=datetime.now(UTC),
                    )
                )
            db.session.commit()

    ids["say"] = say
    return ids
