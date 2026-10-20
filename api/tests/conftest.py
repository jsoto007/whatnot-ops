import pytest
from flask_migrate import upgrade
from sqlalchemy import text

from app import create_app
from app.config import Config
from app.db import db

TABLES = ["chat_messages", "shows", "devices", "organizations"]


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
