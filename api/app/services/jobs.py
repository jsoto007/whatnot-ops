"""Postgres-backed job queue. No Redis: a job is a row, workers claim with SKIP LOCKED.

JOBS_INLINE=1 (tests, local dev without a worker) runs a job synchronously at
enqueue time instead of leaving it for `python -m app.worker`.

A running job carries its claim (locked_by: the worker; attempts: which try) and a
heartbeat: while the handler runs, a thread beside it stamps jobs.heartbeat_at every
BEAT_SECONDS. A job whose worker has said nothing for SILENT_SECONDS is given back by
requeue_stale(), which every worker's poll runs, but only once that worker has itself
reached the database without a failure for SILENT_SECONDS (`reach`): while the database is
away every live worker's beats fail too, and that silence is not death. The claim fences
every later write: a worker that was given up on cannot finish, retry or keep alive a job
another claim holds.
"""

import logging
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select, text, update
from sqlalchemy.exc import InterfaceError, OperationalError

from ..db import db
from ..logsafe import error_line
from ..models import Job, JobStatus

log = logging.getLogger(__name__)
HANDLERS: dict[str, Callable[[dict], object]] = {}
# Claimed ahead of the rest: a live show's parse, and a reset email someone is waiting
# for (both quick), never behind AI checks that each wait on a vendor.
FIRST = ("parse_show", "reset_mail")
# Next: the timer's read of Whatnot and what follows it. A few quick jobs per shop every
# few minutes; behind a hundred AI checks they used to wait until the checks were done, so
# paid orders reached the app far later than the poll interval says.
WHATNOT_READS = ("whatnot_sync", "whatnot_sync_show", "whatnot_apply_show", "whatnot_event")

# A running job's worker says it is alive this often, from a thread beside the handler (the
# worker is one loop, busy inside the handler, so the loop itself cannot say it).
BEAT_SECONDS = 15.0
# After a failed beat (the database away) the next try comes this soon: a live worker's first
# beat after an outage must land before anyone reads its silence as death.
BEAT_RETRY_SECONDS = 2.0
# No beat for this long (six missed): the worker is gone (killed, out of memory, a deploy) and
# its job goes back to the queue. Measured in database time, so workers' clocks do not matter.
SILENT_SECONDS = 90.0
# A running job with no beat at all (claimed before heartbeats existed) keeps the old rule:
# given back fifteen minutes after its claim.
UNBEATEN_MINUTES = 15
# The database went away (restart, failover, network), as opposed to a statement it refused.
DB_DOWN = (OperationalError, InterfaceError)


class _Reach:
    """May this process read another worker's silence as death? Only if it has itself reached
    the database, without a failure, for SILENT_SECONDS: while the database is away every live
    worker's beats fail too, so a silence that long can be the outage, not a dead worker.

    The window opens at the first success after the last failure seen anywhere in the process
    (the worker loop, a give-back, a job that lost the database, the heartbeat thread of a job
    whose loop is busy in a handler and never polls) and must stay unbroken. A process that
    has just started has seen nothing yet. Two successes more than two beats apart break it
    too: a call that hung through an outage and then answered raised nothing, and a job whose
    beat stopped said nothing. Monotonic clock, under a lock (the heartbeat thread writes)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """As a process that has just started: nothing proven."""
        with self._lock:
            self._failed_at = self._clock()
            self._since: float | None = None
            self._last_ok = float("-inf")

    def failed(self) -> None:
        with self._lock:
            self._failed_at = self._clock()
            self._since = None

    def ok(self, began: float) -> None:
        """The database answered a call that began at `began` (this clock)."""
        with self._lock:
            if began < self._failed_at:
                return  # it may have answered before the last failure: proves nothing after
            now = self._clock()
            if self._since is None or now - self._last_ok > 2 * BEAT_SECONDS:
                self._since = now
            self._last_ok = now

    def proven(self) -> bool:
        with self._lock:
            now = self._clock()
            return (
                self._since is not None
                and now - self._last_ok <= 2 * BEAT_SECONDS
                and now - self._since >= SILENT_SECONDS
            )

    @contextmanager
    def watch(self, failures: tuple[type[Exception], ...] = (Exception,)) -> Iterator[None]:
        """Count the database calls inside as one success, or (raising `failures`) a failure."""
        began = self._clock()
        try:
            yield
        except failures:
            self.failed()
            raise
        self.ok(began)


reach = _Reach()


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
    behind AI checks that each wait on a vendor. Whatnot's reads come second, ahead of the
    AI checks for the same reason (never ahead of a parse)."""
    now = datetime.now(UTC)
    job = db.session.scalar(
        select(Job)
        .where(Job.status == JobStatus.queued, Job.run_at <= now)
        .order_by(case((Job.kind.in_(FIRST), 0), (Job.kind.in_(WHATNOT_READS), 1), else_=2), Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.session.commit()
        return None
    job.status = JobStatus.running
    job.locked_at = now
    job.locked_by = worker
    job.heartbeat_at = func.now()
    job.attempts += 1
    db.session.commit()
    return job


_BEAT = text(
    "UPDATE jobs SET heartbeat_at = now() "
    "WHERE id = :id AND status = 'running' AND locked_by = :worker AND attempts = :attempt"
)


class _Heartbeat:
    """Says a running job's worker is alive while its handler runs. A daemon thread on its own
    pooled connection, one UPDATE per transaction under short lock and statement timeouts: it
    never shares the handler's session, holds no lock past its own UPDATE, and any failure is
    logged and tried again soon, never raised into the job. The beat names the claim (worker
    and attempt): hostname:pid can repeat after a restart, the attempt number cannot."""

    def __init__(self, job_id: int, worker: str, attempt: int):
        self.job_id, self.worker, self.attempt = job_id, worker, attempt
        self.engine = db.engine  # read here: the thread has no app context
        self.stopped = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name=f"job {job_id} heartbeat", daemon=True
        )

    def beat(self) -> bool:
        """One beat. False: the job is no longer this claim's (given to another worker)."""
        with self.engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '2s'"))
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            done = conn.execute(
                _BEAT, {"id": self.job_id, "worker": self.worker, "attempt": self.attempt}
            )
            return done.rowcount == 1

    def _run(self) -> None:
        wait = BEAT_SECONDS
        while not self.stopped.wait(wait):
            try:
                # A failed beat is this process seeing the database away (`reach`): its loop
                # may be busy in a DB-free handler and never see the outage itself.
                with reach.watch():
                    alive = self.beat()
                if alive:
                    wait = BEAT_SECONDS
                    continue
                if not self.stopped.is_set():
                    log.error(
                        "job %s was given to another worker while %s still ran attempt %s",
                        self.job_id,
                        self.worker,
                        self.attempt,
                    )
                return
            except Exception as e:  # noqa: BLE001 - nothing may end the beat while the job runs
                wait = BEAT_RETRY_SECONDS
                try:
                    log.warning(
                        "job %s: heartbeat failed (%s); trying again in %ss",
                        self.job_id,
                        error_line(e),
                        wait,
                    )
                except Exception:  # noqa: BLE001 - not even a failing log handler
                    pass

    def __enter__(self) -> "_Heartbeat":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stopped.set()
        # A beat in flight finishes (its timeouts bound it) before the job's end is written.
        self.thread.join(timeout=15)


def _settle(job_id: int, worker: str, attempt: int, **values) -> bool:
    """Write a job's end (done, failed, or queued for a retry) if this claim still holds it;
    the rowcount decides. False: it was given to another worker meanwhile, nothing written."""
    n = db.session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.running,
            Job.locked_by == worker,
            Job.attempts == attempt,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    ).rowcount
    if n != 1:
        log.error(
            "job %s ended on %s (attempt %s) after it was given to another worker; "
            "left as that worker has it",
            job_id,
            worker,
            attempt,
        )
    return n == 1


def run_job(job_id: int, worker: str = "inline") -> Job:
    """Run one job as `worker`: one `claim(worker)` took, a queued one (inline), or a finished
    one run again by hand (as a new attempt). A job running under another worker is left
    alone: running it here too is the double run the claim and heartbeat exist to prevent."""
    job = db.session.get(Job, job_id)
    assert job is not None
    if job.status is JobStatus.running and job.locked_by != worker:
        log.warning(
            "job %s %s is running on %s; not run again on %s",
            job.id,
            job.kind,
            job.locked_by,
            worker,
        )
        return job
    if job.status is not JobStatus.running:
        job.status = JobStatus.running
        job.locked_at = datetime.now(UTC)
        job.locked_by = worker
        job.heartbeat_at = func.now()
        job.attempts += 1
        db.session.commit()
    attempt, most, kind, payload = job.attempts, job.max_attempts, job.kind, job.payload
    fn = HANDLERS.get(kind)
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job kind {kind!r}")
        with _Heartbeat(job_id, worker, attempt):
            fn(payload)
        # The end goes in the handler's own last transaction, and only while this claim
        # still holds the job: a worker given up on, and what it left uncommitted, change
        # nothing on the claim that replaced it.
        if _settle(
            job_id,
            worker,
            attempt,
            status=JobStatus.done,
            error=None,
            heartbeat_at=None,
            finished_at=datetime.now(UTC),
        ):
            db.session.commit()
        else:
            db.session.rollback()
    except Exception as e:  # noqa: BLE001 - any failure is recorded on the job
        if isinstance(e, DB_DOWN):
            reach.failed()  # the handler lost the database: so may every worker's beat
        db.session.rollback()
        why = error_line(e)[:2000]  # no row values from a database error's DETAIL
        if attempt >= most or getattr(e, "final", False):
            if _settle(
                job_id,
                worker,
                attempt,
                status=JobStatus.failed,
                error=why,
                heartbeat_at=None,
                finished_at=datetime.now(UTC),
            ):
                log.error("job %s %s failed for good: %s", job_id, kind, why)
        else:
            delay = min(300, 10 * 2 ** (attempt - 1))
            if _settle(
                job_id,
                worker,
                attempt,
                status=JobStatus.queued,
                error=why,
                heartbeat_at=None,
                run_at=datetime.now(UTC) + timedelta(seconds=delay),
            ):
                log.warning("job %s %s failed (attempt %s): %s", job_id, kind, attempt, why)
        db.session.commit()
    return db.session.get(Job, job_id)


def run_once(worker: str | None = None) -> Job | None:
    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    job = claim(worker)
    if job is None:
        return None
    return run_job(job.id, worker)


def requeue_stale() -> int:
    """Give back the jobs of workers that stopped: running with no heartbeat for
    SILENT_SECONDS, or (no heartbeat at all) claimed UNBEATEN_MINUTES ago. A job whose last
    allowed attempt never finished is failed instead: one that kills its worker (out of
    memory, say) would otherwise crash it again and again forever. A row someone holds a lock
    on is skipped, never waited for: the lock is a live transaction. Returns how many went
    back to the queue."""
    stale = db.session.execute(
        text(
            "SELECT id, kind, attempts, max_attempts, locked_by FROM jobs "
            "WHERE status = 'running' AND ("
            "heartbeat_at < now() - make_interval(secs => :silent) "
            "OR (heartbeat_at IS NULL AND locked_at < now() - make_interval(mins => :minutes))"
            ") ORDER BY id FOR UPDATE SKIP LOCKED"
        ),
        {"silent": float(SILENT_SECONDS), "minutes": int(UNBEATEN_MINUTES)},
    ).all()
    gave_up = [r for r in stale if r.attempts >= r.max_attempts]
    back = [r for r in stale if r.attempts < r.max_attempts]
    stopped = {"locked_by": None, "heartbeat_at": None}
    if gave_up:
        db.session.execute(
            update(Job)
            .where(Job.id.in_([r.id for r in gave_up]))
            .values(
                status=JobStatus.failed,
                finished_at=func.now(),
                error="gave up after its last attempt never finished: the worker stopped "
                "during it (a crash, out of memory, or a deploy); not retried again",
                **stopped,
            )
            .execution_options(synchronize_session=False)
        )
    if back:
        db.session.execute(
            update(Job)
            .where(Job.id.in_([r.id for r in back]))
            .values(status=JobStatus.queued, **stopped)
            .execution_options(synchronize_session=False)
        )
    db.session.commit()
    for r in gave_up:
        log.error("job %s %s failed for good: worker %s stopped during attempt %s of %s",
                  r.id, r.kind, r.locked_by, r.attempts, r.max_attempts)  # fmt: skip
    for r in back:
        log.warning("job %s %s given back: worker %s stopped during attempt %s",
                    r.id, r.kind, r.locked_by, r.attempts)  # fmt: skip
    return len(back)
