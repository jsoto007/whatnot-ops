import pytest
from sqlalchemy import text

from app import create_app
from app.config import Config
from app.db import db


@pytest.fixture(scope="session")
def app():
    app = create_app(Config(testing=True))
    with app.app_context():
        db.session.execute(text("SELECT 1"))
    yield app


@pytest.fixture
def client(app):
    return app.test_client()
