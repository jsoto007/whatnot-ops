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
    # The head may be a merge revision (0027): then it has several parents, and a
    # database standing on any one of them is behind.
    parents = head.down_revision if isinstance(head.down_revision, tuple) else (head.down_revision,)
    for behind in parents:
        monkeypatch.setattr(worker, "_db_revision", lambda behind=behind: behind)
        with app.app_context():
            assert worker.schema_ready() is False, behind


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


# ---- architecture review 2026-11-07: #5, #6, #7, #15, #17 ----

import logging  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from app.models import Job, JobStatus  # noqa: E402
from app.services import jobs  # noqa: E402


def _down():
    return OperationalError("SELECT 1", {}, Exception("server closed the connection"))


def _job(kind="parse_show", **kw):
    j = Job(kind=kind, payload={"show_id": 1}, run_at=datetime.now(UTC) - timedelta(seconds=1))
    for k, v in kw.items():
        setattr(j, k, v)
    db.session.add(j)
    db.session.flush()
    return j.id


def test_a_job_the_worker_died_in_every_time_fails_instead_of_looping(app):
    old = datetime.now(UTC) - timedelta(hours=1)
    with app.app_context():
        poison = _job(status=JobStatus.running, attempts=5, max_attempts=5, locked_at=old)
        unlucky = _job(status=JobStatus.running, attempts=2, max_attempts=5, locked_at=old)
        busy = _job(status=JobStatus.running, attempts=5, locked_at=datetime.now(UTC))
        db.session.commit()
        assert jobs.requeue_stale() == 1
        got = {j.id: (j.status, j.finished_at is not None) for j in db.session.scalars(select(Job))}
        assert got[poison] == (JobStatus.failed, True)
        assert got[unlucky] == (JobStatus.queued, False)
        assert got[busy] == (JobStatus.running, False)  # a live job is left alone
        assert "never finished" in db.session.get(Job, poison).error


def test_parsing_is_claimed_before_older_ai_checks(app):
    with app.app_context():
        _job("ai_review_item")
        _job("ai_audit_show")
        parse = _job("parse_show")
        db.session.commit()
        assert jobs.claim("w").id == parse


def _main_until(app, monkeypatch, run_once, sweep=lambda: {}):
    monkeypatch.setattr(worker, "schema_ready", lambda: True)
    monkeypatch.setattr(worker, "create_app", lambda: app)
    monkeypatch.setattr(worker, "_nap", lambda *a: None)
    monkeypatch.setattr(worker.jobs, "requeue_stale", lambda: None)
    monkeypatch.setattr(worker.retention, "sweep", sweep)
    monkeypatch.setattr(worker.jobs, "run_once", run_once)
    assert threading.current_thread() is threading.main_thread()
    try:
        worker.main()
    except SystemExit:
        pass


def test_the_worker_rides_out_a_database_blip(app, monkeypatch, caplog):
    # Alembic's fileConfig (the session's `upgrade()`) disables loggers that already exist.
    monkeypatch.setattr(worker.log, "disabled", False)
    calls = []

    def run_once():
        calls.append(len(calls))
        if len(calls) <= 2:
            raise _down()
        raise SystemExit  # third poll: the database is back; stop the test here

    with caplog.at_level(logging.WARNING, logger="worker"):
        _main_until(app, monkeypatch, run_once)
    assert calls == [0, 1, 2]
    assert sum("database unreachable" in r.getMessage() for r in caplog.records) == 2


def test_an_unreachable_database_is_not_read_as_unmigrated(app, monkeypatch):
    with app.app_context():

        def execute(*a, **kw):
            raise _down()

        monkeypatch.setattr(db.session, "execute", execute)
        try:
            worker._db_revision()
        except OperationalError:
            pass
        else:
            raise AssertionError("an unreachable database must not look unmigrated")


def test_waiting_for_a_migration_keeps_saying_so(app, monkeypatch, caplog):
    states = iter([False, False, False, "down", True])

    def ready():
        s = next(states)
        if s == "down":
            raise _down()
        return s

    monkeypatch.setattr(worker.log, "disabled", False)
    monkeypatch.setattr(worker, "schema_ready", ready)
    monkeypatch.setattr(worker, "SCHEMA_WARN_SECONDS", 0)
    monkeypatch.setattr(worker, "_nap", lambda *a: None)
    with app.app_context(), caplog.at_level(logging.INFO, logger="worker"):
        worker.wait_for_schema(lambda: False)
    said = [r.getMessage() for r in caplog.records]
    assert sum("waiting for the web service to migrate" in m for m in said) == 3
    assert sum("database unreachable" in m for m in said) == 1
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert said[-1] == "database migrated, polling jobs"


def test_log_level_in_any_case(monkeypatch):
    for given, want in [("info", "INFO"), (" Debug ", "DEBUG"), ("verbose", "INFO"), ("", "INFO")]:
        monkeypatch.setenv("LOG_LEVEL", given)
        assert worker._log_level() == want


def test_a_failing_sweep_never_stops_the_queue(app, monkeypatch):
    """A statement timeout in housekeeping is not "database down": jobs still run, and the
    sweep waits for its next round instead of running every poll."""
    sweeps, runs = [], []

    def sweep():
        sweeps.append(1)
        raise OperationalError(
            "DELETE", {}, Exception("canceling statement due to statement timeout")
        )

    def run_once():
        runs.append(1)
        if len(runs) == 3:
            raise SystemExit
        return None

    _main_until(app, monkeypatch, run_once, sweep)
    assert runs == [1, 1, 1] and sweeps == [1]
