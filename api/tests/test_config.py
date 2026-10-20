import pytest

from app.config import Config, ConfigError, db_host


def test_missing_database_url_fails_loudly(monkeypatch):
    # CI runs with APP_ENV=test, which makes Config() read TEST_DATABASE_URL instead.
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError):
        Config()


def test_postgres_scheme_is_normalised_to_psycopg(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example.com:5432/x")
    assert Config().SQLALCHEMY_DATABASE_URI == "postgresql+psycopg://u:p@db.example.com:5432/x"


def test_db_host_never_includes_credentials():
    url = "postgresql+psycopg://user:secret@db.example.com:6543/x"
    assert db_host(url) == "db.example.com:6543/x"


def test_app_env_test_selects_the_test_database(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/dev")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/testdb")
    assert Config().SQLALCHEMY_DATABASE_URI.endswith("/testdb")
