"""Housekeeping: delete rows that only ever grow and that nobody reads once old.

Run by the worker's sweep (`app/worker.py`, every few minutes) and by `flask prune`.
Shop records are never touched here: chat, sales, orders, inventory movements,
the audit log and AI call logs are kept. Only queue bookkeeping, finished rate-limit
windows and long-dead one-time tokens go.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update

from ..db import db
from ..models import DevicePairing, Job, JobStatus, PasswordReset, WhatnotEvent
from . import ratelimit

DONE_JOBS = timedelta(days=7)
FAILED_JOBS = timedelta(days=90)  # long enough to read why a parse kept failing
DEAD_TOKENS = timedelta(days=30)  # expired pairing codes and reset links (hashes only)
# Webhook deliveries already acted on. Whatnot retries one for about 2.5 days, and the
# unique webhook id has to outlive that; a month also leaves time to read what happened.
WHATNOT_EVENTS = timedelta(days=30)
STUCK_EVENTS = timedelta(days=3)  # Whatnot itself stops retrying after about 2.5 days


def sweep() -> dict[str, int]:
    """Delete what is past its keep-by date; returns rows deleted per table."""
    now = datetime.now(UTC)
    out = {
        "jobs_done": db.session.execute(
            delete(Job).where(Job.status == JobStatus.done, Job.finished_at < now - DONE_JOBS)
        ).rowcount,
        "jobs_failed": db.session.execute(
            delete(Job).where(Job.status == JobStatus.failed, Job.finished_at < now - FAILED_JOBS)
        ).rowcount,
        "device_pairings": db.session.execute(
            delete(DevicePairing).where(DevicePairing.expires_at < now - DEAD_TOKENS)
        ).rowcount,
        "password_resets": db.session.execute(
            delete(PasswordReset).where(PasswordReset.expires_at < now - DEAD_TOKENS)
        ).rowcount,
        # A delivery whose job gave up for good (five tries) is settled as failed, so it
        # is not "waiting to be read" forever. Reading the show again covers what it said.
        "whatnot_events_stuck": db.session.execute(
            update(WhatnotEvent)
            .where(WhatnotEvent.status == "queued", WhatnotEvent.received_at < now - STUCK_EVENTS)
            .values(status="failed", error="never handled", processed_at=now)
        ).rowcount,
        "whatnot_events": db.session.execute(
            delete(WhatnotEvent).where(
                WhatnotEvent.status != "queued", WhatnotEvent.received_at < now - WHATNOT_EVENTS
            )
        ).rowcount,
        "rate_limits": ratelimit.prune(),
    }
    db.session.commit()
    return out
