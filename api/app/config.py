"""Configuration comes from the environment only.

DATABASE_URL is required. There is deliberately no default: a missing value
must fail loudly rather than quietly point at some other database.
"""

import os
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


class ConfigError(RuntimeError):
    pass


def db_host(url: str) -> str:
    """Host:port/dbname of a database URL, with no credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname or '?'}:{parts.port or 5432}{parts.path}"


class Config:
    def __init__(self, *, testing: bool = False) -> None:
        self.APP_ENV = os.environ.get("APP_ENV", "development")
        testing = testing or self.APP_ENV == "test"
        self.TESTING = testing
        self.SECRET_KEY = os.environ.get("SECRET_KEY", "")
        url = os.environ.get("TEST_DATABASE_URL" if testing else "DATABASE_URL", "")
        if not url:
            which = "TEST_DATABASE_URL" if testing else "DATABASE_URL"
            raise ConfigError(f"{which} is not set; refusing to start without a database")
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        self.SQLALCHEMY_DATABASE_URI = url
        self.SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
        if self.APP_ENV == "production" and not self.SECRET_KEY:
            raise ConfigError("SECRET_KEY must be set in production")
        self.SESSION_COOKIE_SECURE = self.APP_ENV == "production"
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
