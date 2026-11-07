"""Background worker: `python -m app.worker`. Polls the jobs table; stops on SIGTERM."""

import logging
import os
import signal
import time
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from . import create_app
from .db import db
from .services import jobs, retention

log = logging.getLogger("worker")

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
SCHEMA_WAIT_SECONDS = 5


def _scripts() -> ScriptDirectory:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return ScriptDirectory.from_config(cfg)


def _db_revision() -> str | None:
    """The revision `flask db upgrade` last applied; None on a database it never touched."""
    try:
        return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except DBAPIError:  # no alembic_version table yet
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


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app = create_app()
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    poll = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
    last_sweep = float("-inf")  # sweep at startup, however long the machine has been up
    with app.app_context():
        log.info("worker started")
        waited = False
        while not stop and not schema_ready():
            if not waited:
                log.info("waiting for the web service to migrate the database")
                waited = True
            time.sleep(SCHEMA_WAIT_SECONDS)
        if waited and not stop:
            log.info("database migrated, polling jobs")
        while not stop:
            if time.monotonic() - last_sweep > 300:
                jobs.requeue_stale()
                try:
                    pruned = {k: n for k, n in retention.sweep().items() if n}
                    if pruned:
                        log.info("pruned %s", pruned)
                except Exception:  # noqa: BLE001 - housekeeping must never stop the queue
                    db.session.rollback()
                    log.exception("retention sweep failed")
                last_sweep = time.monotonic()
            job = jobs.run_once()
            if job is None:
                time.sleep(poll)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
