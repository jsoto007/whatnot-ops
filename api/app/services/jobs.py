"""Postgres-backed job queue. No Redis: a job is a row, workers claim with SKIP LOCKED.

JOBS_INLINE=1 (tests, local dev without a worker) runs a job synchronously at
enqueue time instead of leaving it for `python -m app.worker`.
"""

import logging
import os
import socket
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, select, text

from ..db import db
from ..models import Job, JobStatus

log = logging.getLogger(__name__)
HANDLERS: dict[str, Callable[[dict], object]] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def inline() -> bool:
    return os.environ.get("JOBS_INLINE") == "1"


def enqueue(
    kind: str,
    payload: dict,
    *,
    org_id: int | None = None,
    dedupe_key: str | None = None,
    delay_s: int = 0,
) -> Job:
    """Queue a job. With a dedupe_key, an already-queued job with that key is returned instead."""
    if dedupe_key:
        existing = db.session.scalar(
            select(Job)
            .where(Job.dedupe_key == dedupe_key, Job.status == JobStatus.queued)
            .with_for_update(skip_locked=True)
        )
        if existing is not None:
            return existing
    job = Job(
        organization_id=org_id,
        kind=kind,
        payload=payload,
        dedupe_key=dedupe_key,
        run_at=datetime.now(UTC) + timedelta(seconds=delay_s),
    )
    db.session.add(job)
    db.session.flush()
    if inline():
        db.session.commit()
        run_job(job.id)
    return job


def claim(worker: str) -> Job | None:
    """Take the next due job, or None. Runs in its own short transaction. Parsing goes
    first: it keeps a live show's numbers current, and one lane must not leave it queued
    behind AI checks that each wait on a vendor."""
    now = datetime.now(UTC)
    job = db.session.scalar(
        select(Job)
        .where(Job.status == JobStatus.queued, Job.run_at <= now)
        .order_by(case((Job.kind == "parse_show", 0), else_=1), Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.session.commit()
        return None
    job.status = JobStatus.running
    job.locked_at = now
    job.locked_by = worker
    job.attempts += 1
    db.session.commit()
    return job


def run_job(job_id: int, worker: str = "inline") -> Job:
    job = db.session.get(Job, job_id)
    assert job is not None
    if job.status is JobStatus.queued:
        job.status = JobStatus.running
        job.locked_at = datetime.now(UTC)
        job.locked_by = worker
        job.attempts += 1
        db.session.commit()
    fn = HANDLERS.get(job.kind)
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job kind {job.kind!r}")
        fn(job.payload)
        db.session.commit()
        job = db.session.get(Job, job_id)
        job.status = JobStatus.done
        job.error = None
        job.finished_at = datetime.now(UTC)
        db.session.commit()
    except Exception as e:  # noqa: BLE001 - any failure is recorded on the job
        db.session.rollback()
        job = db.session.get(Job, job_id)
        job.error = "".join(traceback.format_exception_only(type(e), e)).strip()[:2000]
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.failed
            job.finished_at = datetime.now(UTC)
            log.error("job %s %s failed for good: %s", job.id, job.kind, job.error)
        else:
            job.status = JobStatus.queued
            job.run_at = datetime.now(UTC) + timedelta(
                seconds=min(300, 10 * 2 ** (job.attempts - 1))
            )
            log.warning(
                "job %s %s failed (attempt %s): %s", job.id, job.kind, job.attempts, job.error
            )
        db.session.commit()
    return job


def run_once(worker: str | None = None) -> Job | None:
    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    job = claim(worker)
    if job is None:
        return None
    return run_job(job.id, worker)


def requeue_stale(older_than_minutes: int = 15) -> int:
    """A worker that died mid-job leaves it 'running'; give it back after a while. A job
    whose last allowed attempt never finished is failed instead: one that kills its worker
    (out of memory, say) would otherwise crash it every 15 minutes forever."""
    cutoff = datetime.now(UTC) - timedelta(minutes=older_than_minutes)
    gave_up = db.session.execute(
        text(
            "UPDATE jobs SET status = 'failed', locked_by = NULL, finished_at = now(), "
            "error = :why WHERE status = 'running' AND locked_at < :cutoff "
            "AND attempts >= max_attempts RETURNING id, kind, attempts, max_attempts"
        ),
        {
            "cutoff": cutoff,
            "why": "gave up after its last attempt never finished: the worker stopped "
            "during it (a crash, out of memory, or a deploy); not retried again",
        },
    ).all()
    for job_id, kind, attempts, most in gave_up:
        log.error("job %s %s failed for good: worker stopped during attempt %s of %s",
                  job_id, kind, attempts, most)  # fmt: skip
    r = db.session.execute(
        text(
            "UPDATE jobs SET status = 'queued', locked_by = NULL "
            "WHERE status = 'running' AND locked_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    db.session.commit()
    return r.rowcount
