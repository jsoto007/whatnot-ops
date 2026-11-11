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
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError

from . import create_app
from .db import db
from .services import jobs, retention

log = logging.getLogger("worker")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA_WAIT_SECONDS = 5
# While waiting for the web service's migration, say so again this often: a deploy whose
# migration failed leaves the worker waiting, and one log line at boot is easy to miss.
SCHEMA_WARN_SECONDS = 60
SWEEP_SECONDS = 300
DB_RETRY_MAX_SECONDS = 60
# The database went away (restart, failover, network): wait and try again, never exit.
DB_DOWN = (OperationalError, InterfaceError)


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
            if schema_ready():
                break
            why = "waiting for the web service to migrate the database"
            detail = (
                f"(database at {_db_revision()}, this code needs {_scripts().get_current_head()})"
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


def _housekeeping() -> None:
    """Give back jobs of a dead worker and prune old bookkeeping. Never raises: it must not
    stop the queue, and whether the database is down is for the next poll to find out."""
    try:
        jobs.requeue_stale()
        pruned = {k: n for k, n in retention.sweep().items() if n}
        if pruned:
            log.info("pruned %s", pruned)
        # The timer's read of Whatnot for each connected shop (one queued job per shop).
        from .services.whatnot import sync as whatnot_sync

        whatnot_sync.schedule_all()
    except Exception:  # noqa: BLE001 - housekeeping must never stop the queue
        _forget_connection()
        log.exception("housekeeping failed; next try in %ss", SWEEP_SECONDS)


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
    poll = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
    last_sweep = float("-inf")  # sweep at startup, however long the machine has been up
    down = 0  # consecutive polls that found the database unreachable
    with app.app_context():
        log.info("worker started")
        wait_for_schema(stopped)
        while not stop:
            if time.monotonic() - last_sweep > SWEEP_SECONDS:
                # Stamped first: a sweep that fails (a statement timeout, the database
                # going away) waits for the next round instead of running every poll.
                last_sweep = time.monotonic()
                _housekeeping()
            try:
                job = jobs.run_once()
            except DB_DOWN:
                # A job cut off mid-run stays 'running' until requeue_stale gives it back.
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
                log.info("database reachable again after %s failed polls", down)
                down = 0
            if job is None:
                _nap(poll, stopped)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
