"""The worker waits for the web service's migrations before touching the jobs table."""

import threading

from sqlalchemy import text

from app import worker
from app.db import db


def test_schema_ready_on_a_migrated_database(app):
    with app.app_context():
        assert worker._db_revision() == worker._scripts().get_current_head()
        assert worker.schema_ready() is True


def test_not_ready_before_any_migration(app, monkeypatch):
    # A new Render database: the worker booted before `flask db upgrade` ran.
    monkeypatch.setattr(worker, "_db_revision", lambda: None)
    with app.app_context():
        assert worker.schema_ready() is False


def test_not_ready_while_the_database_is_behind(app, monkeypatch):
    scripts = worker._scripts()
    head = scripts.get_revision(scripts.get_current_head())
    monkeypatch.setattr(worker, "_db_revision", lambda: head.down_revision)
    with app.app_context():
        assert worker.schema_ready() is False


def test_ready_when_the_database_is_ahead_of_this_code(app, monkeypatch):
    monkeypatch.setattr(worker, "_db_revision", lambda: "abcdef123456")
    with app.app_context():
        assert worker.schema_ready() is True


def test_missing_version_table_reads_as_none_and_leaves_no_transaction(app, monkeypatch):
    real_execute = db.session.execute

    def execute(stmt, *a, **kw):
        if "alembic_version" in str(stmt):
            stmt = text(str(stmt).replace("alembic_version", "alembic_version_missing"))
        return real_execute(stmt, *a, **kw)

    with app.app_context():
        monkeypatch.setattr(db.session, "execute", execute)
        assert worker._db_revision() is None
        monkeypatch.undo()
        assert not db.session().in_transaction()
        assert worker.schema_ready() is True  # the session still works afterwards


def test_main_waits_then_polls(app, monkeypatch):
    """main() does not touch the jobs table until the schema is ready."""
    ready = iter([False, False, True])
    monkeypatch.setattr(worker, "schema_ready", lambda: next(ready))
    monkeypatch.setattr(worker, "SCHEMA_WAIT_SECONDS", 0)
    monkeypatch.setattr(worker, "create_app", lambda: app)
    calls = []

    def run_once():
        calls.append("run")
        raise SystemExit  # one poll is enough

    monkeypatch.setattr(worker.jobs, "requeue_stale", lambda: calls.append("requeue"))
    monkeypatch.setattr(worker.retention, "sweep", lambda: {})
    monkeypatch.setattr(worker.jobs, "run_once", run_once)
    # signal.signal only works on the main thread; this test runs there.
    assert threading.current_thread() is threading.main_thread()
    try:
        worker.main()
    except SystemExit:
        pass
    assert calls == ["requeue", "run"]
