"""One request budget for Whatnot, shared by every process.

Whatnot allows 10 requests a second per seller (per app) and answers 429 past it. The two
web processes and the worker all call it, so each counts its attempts in the same Postgres
row (the `rate_limits` table the app's own limits use) and waits for the next second when
the shop's budget for this one is spent. We stay at 8 to leave room for clock skew.

Unlike the app's own limits this is never switched off: it protects the shop's standing
with Whatnot, in development too.
"""

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from ...db import db
from ...models import RateLimit
from .client import Busy

PER_SECOND = 8
MAX_WAIT_SECONDS = 30.0


def _count(key: str) -> int:
    now = datetime.now(UTC).replace(microsecond=0)
    stmt = (
        insert(RateLimit)
        .values(key=key, window_start=now, count=1)
        .on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window_start],
            set_={"count": RateLimit.count + 1},
        )
        .returning(RateLimit.count)
    )
    # Its own connection and transaction: the caller's transaction (which may hold row
    # locks for a while) neither delays the count nor rolls it back.
    with db.engine.begin() as conn:
        return conn.execute(stmt).scalar_one()


def acquire(
    org_id: int,
    *,
    per_second: int = PER_SECOND,
    sleep: Callable[[float], None] = time.sleep,
    max_wait: float = MAX_WAIT_SECONDS,
) -> None:
    """Return when this process may send one request for the shop; wait if it must."""
    key = f"whatnot-out:org:{org_id}"
    waited = 0.0
    while _count(key) > per_second:
        if waited >= max_wait:
            raise Busy("the shop's Whatnot request budget stayed full")
        # To the start of the next second, plus a little so waiters do not all wake at once.
        delay = 1.0 - (time.time() % 1.0) + random.uniform(0.01, 0.08)
        sleep(delay)
        waited += delay
