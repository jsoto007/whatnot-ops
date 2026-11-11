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


def test_whatnot_secrets_come_from_the_environment_and_never_into_a_test_run(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/dev")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/testdb")
    monkeypatch.setenv("WHATNOT_API_TOKEN", " wn_pat_tk_live ")
    monkeypatch.setenv("WHATNOT_WEBHOOK_SECRET", "wn_wh_abc")
    monkeypatch.setenv("APP_ENV", "development")
    c = Config()
    assert (c.WHATNOT_API_TOKEN, c.WHATNOT_WEBHOOK_SECRET) == ("wn_pat_tk_live", "wn_wh_abc")
    # There is no sandbox: a test process never holds the live shop's token.
    monkeypatch.setenv("APP_ENV", "test")
    c = Config()
    assert (c.WHATNOT_API_TOKEN, c.WHATNOT_WEBHOOK_SECRET) == ("", "")
    assert Config(testing=True).WHATNOT_API_TOKEN == ""


def test_the_fake_whatnot_is_refused_in_production(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/dev")
    monkeypatch.setenv("SECRET_KEY", "x" * 32)
    monkeypatch.setenv("WHATNOT_API", "fake")
    monkeypatch.delenv("JOBS_INLINE", raising=False)  # refused in production on its own
    monkeypatch.setenv("APP_ENV", "development")
    assert Config().WHATNOT_FAKE is True
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ConfigError, match="WHATNOT_API=fake"):
        Config()
    monkeypatch.delenv("APP_ENV")  # a missing APP_ENV is production too
    with pytest.raises(ConfigError, match="WHATNOT_API=fake"):
        Config()
