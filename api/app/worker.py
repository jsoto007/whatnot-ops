"""Background worker: `python -m app.worker`. Polls the jobs table; stops on SIGTERM."""

import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from . import create_app
from .db import db
from .logsafe import error_line
from .services import jobs, retention

log = logging.getLogger("worker")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA_WAIT_SECONDS = 5
# While waiting for the web service's migration, say so again this often: a deploy whose
# migration failed leaves the worker waiting, and one log line at boot is easy to miss.
SCHEMA_WARN_SECONDS = 60
SWEEP_SECONDS = 300
# Giving back a dead worker's job (jobs.requeue_stale) is one small locking read on the
# running rows: done this often between jobs, not only with the five-minute sweep, so a job
# comes back about jobs.SILENT_SECONDS after its worker's last heartbeat. Only once this
# worker has itself reached the database for jobs.SILENT_SECONDS (jobs.reach): after it
# starts, and after any database failure it saw, a live worker's silence proves nothing.
REQUEUE_SECONDS = 15
DB_RETRY_MAX_SECONDS = 60
# The database went away (restart, failover, network): wait and try again, never exit.
DB_DOWN = jobs.DB_DOWN


def _scripts() -> ScriptDirectory:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return ScriptDirectory.from_config(cfg)


def _db_revision() -> str | None:
    """The revision `flask db upgrade` last applied; None on a database it never touched.
    A database that cannot be reached raises (DB_DOWN): that is not "not migrated"."""
    try:
        return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except ProgrammingError:  # no alembic_version table yet
        return None
    finally:
        db.session.rollback()  # never sit idle in a transaction


def schema_ready() -> bool:
    """True once the database has this code's migrations. The web service runs them
    (`flask db upgrade` in its start command) and the worker boots on its own, often
    first: on a new database it would otherwise crash on the missing `jobs` table.
    A revision this code does not know is newer (the web deployed ahead): ready."""
    current = _db_revision()
    if current is None:
        return False
    scripts = _scripts()
    if current == scripts.get_current_head():
        return True
    try:
        return scripts.get_revision(current) is None
    except CommandError:
        return True


def _log_level() -> str:
    """LOG_LEVEL in any case ("info" works); an unknown one falls back to INFO."""
    level = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    return level if level in logging.getLevelNamesMapping() else "INFO"


def _nap(seconds: float, stopped: Callable[[], bool]) -> None:
    """Sleep, waking each second to honour SIGTERM (Render kills 30 s after it)."""
    end = time.monotonic() + seconds
    while not stopped() and (left := end - time.monotonic()) > 0:
        time.sleep(min(1.0, left))


def _forget_connection() -> None:
    try:
        db.session.rollback()
    except Exception:  # noqa: BLE001 - the connection is already gone
        pass
    db.session.remove()


def wait_for_schema(stopped: Callable[[], bool]) -> None:
    """Block until the database carries this code's migrations (or SIGTERM)."""
    waited = False
    last_warning = float("-inf")
    while not stopped():
        try:
            with jobs.reach.watch(DB_DOWN):
                if schema_ready():
                    break
                why = "waiting for the web service to migrate the database"
                detail = (
                    f"(database at {_db_revision()}, "
                    f"this code needs {_scripts().get_current_head()})"
                )
        except DB_DOWN:
            _forget_connection()
            why, detail = "database unreachable", "(retrying)"
        waited = True
        if time.monotonic() - last_warning >= SCHEMA_WARN_SECONDS:
            # The first word at INFO (a normal deploy waits ~20 s); after that, a warning.
            (log.warning if last_warning > float("-inf") else log.info)(
                "%s %s; no jobs run until then", why, detail
            )
            last_warning = time.monotonic()
        _nap(SCHEMA_WAIT_SECONDS, stopped)
    if waited and not stopped():
        log.info("database migrated, polling jobs")


def _give_back() -> None:
    """Give back the jobs of a worker that stopped beating (jobs.requeue_stale). Never
    raises: like the sweep, it must not stop the queue. A failure here, whatever it was,
    closes this worker's window (jobs.reach) like any other."""
    try:
        with jobs.reach.watch():
            jobs.requeue_stale()
    except Exception as e:  # noqa: BLE001 - a lock or statement timeout, the database away
        _forget_connection()  # the failed statement leaves the session needing a rollback
        log.warning(
            "could not give back stale jobs (%s); next try in %ss", error_line(e), REQUEUE_SECONDS
        )


def _housekeeping() -> None:
    """Prune old bookkeeping and queue each shop's read of Whatnot. Never raises: it must
    not stop the queue, and whether the database is down is for the next poll to find out.
    Each step is its own try: a sweep that fails (a timeout on a big DELETE, a lock) used to
    skip the line that queues the Whatnot read, so orders stopped arriving with only a log
    line to say so. Giving back a dead worker's job is not here: it runs far more often
    (`_give_back`, every REQUEUE_SECONDS)."""
    from .services.whatnot import sync as whatnot_sync

    def pruned_log() -> None:
        pruned = {k: n for k, n in retention.sweep().items() if n}
        if pruned:
            log.info("pruned %s", pruned)

    steps = (
        ("prune old rows", pruned_log),
        # The timer's read of Whatnot for each connected shop (one queued job per shop).
        ("queue the Whatnot read", whatnot_sync.schedule_all),
    )
    for what, step in steps:
        try:
            # The database going away here (or a statement timeout: the same exception)
            # closes the give-back window (jobs.reach); a bug in a step does not.
            with jobs.reach.watch(DB_DOWN):
                step()
        except Exception:  # noqa: BLE001 - housekeeping must never stop the queue
            _forget_connection()  # the failed statement leaves the session needing a rollback
            log.exception(
                "housekeeping failed (could not %s); next try in %ss", what, SWEEP_SECONDS
            )


def main() -> None:
    logging.basicConfig(level=_log_level())
    app = create_app()
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    def stopped() -> bool:
        return stop

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    # Idle, the loop still reaches the database at least once a beat: two successes further
    # apart than that break this worker's give-back window (jobs.reach), and it would never
    # give anything back.
    poll = min(float(os.environ.get("WORKER_POLL_SECONDS", "2")), jobs.BEAT_SECONDS)
    last_sweep = float("-inf")  # sweep at startup, however long the machine has been up
    last_give_back = float("-inf")
    down = 0  # consecutive polls that found the database unreachable
    # A worker that has just started has proved nothing: a live worker's beats may have
    # failed until a moment ago (the database away). It gives nothing back until it has
    # reached the database, with no failure anywhere in this process, for one cutoff
    # (jobs.SILENT_SECONDS); a dead predecessor's job comes back then.
    jobs.reach.reset()
    with app.app_context():
        log.info("worker started")
        wait_for_schema(stopped)
        while not stop:
            if time.monotonic() - last_sweep > SWEEP_SECONDS:
                # Stamped first: a sweep that fails (a statement timeout, the database
                # going away) waits for the next round instead of running every poll.
                last_sweep = time.monotonic()
                _housekeeping()
            if jobs.reach.proven() and time.monotonic() - last_give_back >= REQUEUE_SECONDS:
                last_give_back = time.monotonic()
                _give_back()
            try:
                with jobs.reach.watch(DB_DOWN):
                    job = jobs.run_once()
            except DB_DOWN:
                # A job cut off mid-run stays 'running' until its heartbeat goes stale and
                # a worker's poll gives it back.
                down += 1
                _forget_connection()
                delay = min(DB_RETRY_MAX_SECONDS, 2 ** min(down, 6))
                log.warning(
                    "database unreachable (%s in a row); retrying in %ss",
                    down,
                    delay,
                    exc_info=down == 1,
                )
                _nap(delay, stopped)
                continue
            if down:
                # Every live worker's heartbeat failed while the database was away too: the
                # failures closed this worker's window (jobs.reach), so it gives them one
                # cutoff to beat again before anyone's silence is read as death.
                log.info("database reachable again after %s failed polls", down)
                down = 0
            if job is None:
                _nap(poll, stopped)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
