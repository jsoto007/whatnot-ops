import pytest

from app.config import Config, ConfigError, db_host


def test_missing_database_url_fails_loudly(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError):
        Config()


def test_postgres_scheme_is_normalised_to_psycopg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example.com:5432/x")
    assert Config().SQLALCHEMY_DATABASE_URI == "postgresql+psycopg://u:p@db.example.com:5432/x"


def test_db_host_never_includes_credentials():
    url = "postgresql+psycopg://user:secret@db.example.com:6543/x"
    assert db_host(url) == "db.example.com:6543/x"
