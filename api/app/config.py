"""Configuration comes from the environment only.

DATABASE_URL is required. There is deliberately no default: a missing value
must fail loudly rather than quietly point at some other database.
"""

import os
from datetime import timedelta
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


class ConfigError(RuntimeError):
    pass


APP_ENVS = ("development", "test", "production")
#: Environments where development conveniences (dev-only secret, open sign-up,
#: invite / reset links returned in API responses) are allowed. Anything else,
#: including a missing APP_ENV, behaves as production: fail closed.
DEV_ENVS = ("development", "test")
#: Largest request body accepted anywhere (ingest batches of 500 rows × 4000
#: chars fit with room to spare; CSV imports are far smaller).
MAX_BODY_BYTES = 8 * 1024 * 1024


def is_dev(app_env: str | None) -> bool:
    return app_env in DEV_ENVS


def db_host(url: str) -> str:
    """Host:port/dbname of a database URL, with no credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname or '?'}:{parts.port or 5432}{parts.path}"


class Config:
    def __init__(self, *, testing: bool = False) -> None:
        app_env = (os.environ.get("APP_ENV") or "production").strip().lower()
        if app_env not in APP_ENVS:
            raise ConfigError(f"APP_ENV must be one of {', '.join(APP_ENVS)} (got {app_env!r})")
        self.APP_ENV = app_env
        testing = testing or self.APP_ENV == "test"
        self.TESTING = testing
        self.SECRET_KEY = os.environ.get("SECRET_KEY", "") or (
            "dev-only-secret" if is_dev(self.APP_ENV) else ""
        )
        self.MAX_CONTENT_LENGTH = MAX_BODY_BYTES
        url = os.environ.get("TEST_DATABASE_URL" if testing else "DATABASE_URL", "")
        if not url:
            which = "TEST_DATABASE_URL" if testing else "DATABASE_URL"
            raise ConfigError(f"{which} is not set; refusing to start without a database")
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        self.SQLALCHEMY_DATABASE_URI = url
        # Explicit and small: two gunicorn processes plus the worker stay well under a
        # small Render Postgres's connection limit, and rate-limit counters (which take a
        # second connection while the request holds one) cannot exhaust a process's pool.
        self.SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 5,
            "pool_timeout": 10,
            "pool_recycle": 1800,
            # A logged database error must not carry row values (password hashes, session
            # tokens, buyer names): production logs show the SQL without its parameters.
            "hide_parameters": self.APP_ENV == "production",
        }
        # Trusted proxies in front of the app (Cloudflare, then Render's router): see
        # create_app. Wrong in either direction is a security bug (too few: all visitors
        # share a rate limit; too many: a client picks its own address), so it is checked.
        hops = os.environ.get("PROXY_HOPS", "2").strip()
        if not hops.isdigit() or not 1 <= int(hops) <= 4:
            raise ConfigError(f"PROXY_HOPS must be a number from 1 to 4 (got {hops!r})")
        self.PROXY_HOPS = int(hops)
        if self.APP_ENV == "production" and not self.SECRET_KEY:
            raise ConfigError("SECRET_KEY must be set in production")
        if self.APP_ENV == "production" and os.environ.get("JOBS_INLINE") == "1":
            # Inline jobs would run every parse (and any AI call) inside a web request.
            raise ConfigError("JOBS_INLINE=1 is for development and tests, not production")
        # `__Host-` in production: the browser only accepts it from this exact host over
        # HTTPS with Path=/ and no Domain, so no sibling subdomain can plant a session (and
        # with it a CSRF token) for this one. Plain http://localhost cannot use the prefix.
        self.SESSION_COOKIE_NAME = (
            "__Host-wn_session" if self.APP_ENV == "production" else "wn_session"
        )
        self.SESSION_COOKIE_SECURE = self.APP_ENV == "production"
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.PERMANENT_SESSION_LIFETIME = timedelta(days=14)
