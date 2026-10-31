"""Attempt limits for unauthenticated endpoints (login, sign-up, password reset,
invite acceptance, laptop pairing).

Counters live in Postgres so both gunicorn workers share them. Each hit is one
atomic `INSERT … ON CONFLICT DO UPDATE SET count = count + 1 RETURNING count`
on its own connection and transaction: a request that later fails and rolls
back still counts. The client address comes from `request.remote_addr`, which
ProxyFix sets from Render's X-Forwarded-For in production.
"""

from datetime import UTC, datetime, timedelta

from flask import request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db import db
from ..models import RateLimit


class RateLimited(Exception):
    status = 429

    def __init__(
        self,
        message: str = "too many attempts; try again in a few minutes",
        *,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        #: Seconds until the window resets, sent as Retry-After.
        self.retry_after = retry_after


def client_ip() -> str:
    return (request.remote_addr or "unknown")[:64]


def _window(seconds: int) -> datetime:
    now = datetime.now(UTC).timestamp()
    return datetime.fromtimestamp(now - now % seconds, UTC)


def seconds_left(window_s: int) -> int:
    """Seconds until the current fixed window of `window_s` ends."""
    now = datetime.now(UTC).timestamp()
    return max(1, int(window_s - now % window_s))


def enabled() -> bool:
    """RATE_LIMITS=off is honoured only in development/test (the browser e2e suite signs
    up many shops from 127.0.0.1); production always limits."""
    import os

    from flask import current_app

    from ..config import is_dev

    return not (
        os.environ.get("RATE_LIMITS") == "off" and is_dev(current_app.config.get("APP_ENV"))
    )


def hit(key: str, limit: int, window_s: int) -> int:
    """Count one attempt for `key`; raise RateLimited past `limit` in the window."""
    if not enabled():
        return 0
    stmt = (
        insert(RateLimit)
        .values(key=key[:200], window_start=_window(window_s), count=1)
        .on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window_start],
            set_={"count": RateLimit.count + 1},
        )
        .returning(RateLimit.count)
    )
    with db.engine.begin() as conn:
        n = conn.execute(stmt).scalar_one()
    if n > limit:
        raise RateLimited(retry_after=seconds_left(window_s))
    return n


def prune(older_than: timedelta = timedelta(days=1)) -> int:
    """Drop finished windows (the worker's housekeeping sweep calls this)."""
    r = db.session.execute(
        delete(RateLimit).where(RateLimit.window_start < datetime.now(UTC) - older_than)
    )
    return r.rowcount


def peek(key: str, window_s: int) -> int:
    """Attempts so far in the current window, without counting one."""
    if not enabled():
        return 0
    with db.engine.connect() as conn:
        return (
            conn.execute(
                select(RateLimit.count).where(
                    RateLimit.key == key[:200], RateLimit.window_start == _window(window_s)
                )
            ).scalar_one_or_none()
            or 0
        )


def per_ip(scope: str, limit: int, window_s: int) -> None:
    hit(f"{scope}:ip:{client_ip()}", limit, window_s)
