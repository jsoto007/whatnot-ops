"""The worker waits for the web service's migrations before touching the jobs table."""

import threading

import pytest
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
    # No give-back window here (jobs.reach): this test is about the schema wait.
    monkeypatch.setattr(worker.jobs, "SILENT_SECONDS", 0)
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
    kw.setdefault("payload", {"show_id": 1})
    j = Job(kind=kind, run_at=datetime.now(UTC) - timedelta(seconds=1))
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


def _main_until(app, monkeypatch, run_once, sweep=lambda: {}, requeue=lambda: None):
    monkeypatch.setattr(worker, "schema_ready", lambda: True)
    monkeypatch.setattr(worker, "create_app", lambda: app)
    monkeypatch.setattr(worker, "_nap", lambda *a: None)
    monkeypatch.setattr(worker.jobs, "requeue_stale", requeue)
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


def test_a_request_whatnot_refused_fails_the_job_at_once(app, monkeypatch):
    """The 2026-11-13 show: Whatnot answered 422 to the fee report and the job asked five
    times in three minutes. A request error cannot work on a retry; a timer asks again later."""
    from app.services import jobs
    from app.services.whatnot.client import Invalid, Response, Unavailable, WhatnotClient

    def refused(_payload):
        raise Invalid(status=422)

    def down(_payload):
        raise Unavailable(status=503)

    monkeypatch.setitem(jobs.HANDLERS, "test_refused", refused)
    monkeypatch.setitem(jobs.HANDLERS, "test_down", down)
    with app.app_context():
        a, b = jobs.enqueue("test_refused", {}), jobs.enqueue("test_down", {})
        db.session.commit()
        assert jobs.run_job(a.id).status.value == "failed"
        assert jobs.run_job(b.id).status.value == "queued"  # Whatnot down: try again
    # 408 and 425 mean "later", like a 5xx: not a request error.
    for status in (408, 425):
        err = WhatnotClient._error(Response(status, {}, {}), "GET", "/x", "r")
        assert isinstance(err, Unavailable) and not err.final


def test_a_failing_retention_sweep_does_not_stop_the_whatnot_timer(app, world, monkeypatch, caplog):
    """The sweep failing (a timeout on a big DELETE, a lock) used to skip the line that queues
    each shop's Whatnot read, every five minutes, with nothing said but a log line. The failed
    statement also leaves the session needing a rollback: the next step must still work."""
    from app.models import WhatnotConnection

    monkeypatch.setattr(worker.log, "disabled", False)
    with app.app_context():
        db.session.add(WhatnotConnection(organization_id=world["org"], seller_id="1234567"))
        db.session.commit()

    def sweep():
        db.session.execute(text("DELETE FROM no_such_table"))  # fails, session now aborted
        return {}

    stopped = []

    def run_once():
        stopped.append(1)
        raise SystemExit

    with caplog.at_level(logging.WARNING, logger="worker"):
        _main_until(app, monkeypatch, run_once, sweep)
    with app.app_context():
        queued = db.session.scalars(select(Job).where(Job.kind == "whatnot_sync")).all()
        assert len(queued) == 1 and queued[0].status is JobStatus.queued
    assert any("housekeeping" in r.getMessage() for r in caplog.records)


def test_a_failing_requeue_does_not_stop_the_sweep_or_the_timer(app, monkeypatch):
    """Giving back a dead worker's job runs on its own short timer now (every poll here),
    apart from the five-minute sweep: failing every time, it stops neither the queue nor
    the sweep nor the Whatnot timer."""
    from app.services.whatnot import sync as whatnot_sync

    seen, runs = [], []

    def requeue():
        seen.append("requeue")
        raise OperationalError("UPDATE", {}, Exception("lock timeout"))

    def run_once():
        runs.append(1)
        if len(runs) == 3:
            raise SystemExit
        return None

    monkeypatch.setattr(worker, "REQUEUE_SECONDS", 0)
    # No give-back window (jobs.reach): each failed give-back closes it, and the next good
    # poll reopens it at once.
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 0)
    monkeypatch.setattr(whatnot_sync, "schedule_all", lambda: seen.append("whatnot") or 0)
    _main_until(app, monkeypatch, run_once, lambda: seen.append("sweep") or {}, requeue)
    assert runs == [1, 1, 1]
    assert seen == ["sweep", "whatnot", "requeue", "requeue", "requeue"]


def test_whatnot_reads_are_not_stuck_behind_ai_checks_but_parsing_still_goes_first(app):
    with app.app_context():
        _job("ai_review_item")
        _job("ai_audit_show")
        sync = _job("whatnot_sync", payload={"org_id": 1})
        apply_ = _job("whatnot_apply_show", payload={"org_id": 1})
        parse = _job("parse_show")
        db.session.commit()
        assert jobs.claim("w").id == parse
        assert jobs.claim("w").id == sync
        assert jobs.claim("w").id == apply_


# ---- A56: a dead worker's job comes back in about a minute and a half; a live one never ----

import time  # noqa: E402


def _ago(seconds: float) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


def _running(kind="parse_show", worker="gone:1", attempts=1, locked=60, beat=None, **kw):
    """A job `worker` claimed `locked` seconds ago and last said it was alive `beat` seconds
    ago (None: no beat at all, as a job claimed before heartbeats existed)."""
    return _job(
        kind,
        status=JobStatus.running,
        locked_by=worker,
        attempts=attempts,
        locked_at=_ago(locked),
        heartbeat_at=None if beat is None else _ago(beat),
        **kw,
    )


def _beat_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("job ")]


def _in_thread(app, name, fn, out):
    def body():
        with app.app_context():
            try:
                out[name] = fn()
            except BaseException as e:  # noqa: BLE001 - the test reads it
                out[name] = e
            finally:
                db.session.remove()

    return threading.Thread(target=body, name=name, daemon=True)


def test_a_worker_that_stopped_beating_gives_its_job_back_in_seconds_not_minutes(app):
    with app.app_context():
        dead = _running(locked=120, beat=120)  # claimed 2 min ago, silent since
        alive = _running(worker="busy:2", locked=3600, beat=5)  # an hour-long job, alive
        legacy_fresh = _running(worker="old:3", locked=600)  # no beat data, 10 min
        legacy_old = _running(worker="old:4", locked=1200)  # no beat data, 20 min
        db.session.commit()
        assert jobs.requeue_stale() == 2
        got = {
            j.id: (j.status, j.locked_by, j.heartbeat_at) for j in db.session.scalars(select(Job))
        }
        assert got[dead] == (JobStatus.queued, None, None)
        assert got[alive][:2] == (JobStatus.running, "busy:2")
        assert got[legacy_fresh][:2] == (JobStatus.running, "old:3")  # the 15-minute rule
        assert got[legacy_old] == (JobStatus.queued, None, None)


def test_a_last_attempt_whose_worker_stopped_beating_fails_instead_of_looping(app):
    with app.app_context():
        poison = _running(attempts=5, max_attempts=5, locked=120, beat=120)
        db.session.commit()
        assert jobs.requeue_stale() == 0
        job = db.session.get(Job, poison)
        assert (job.status, job.locked_by, job.heartbeat_at) == (JobStatus.failed, None, None)
        assert job.finished_at is not None and "never finished" in job.error


def test_claim_stamps_the_heartbeat_and_it_is_cleared_when_the_job_leaves_running(app, monkeypatch):
    def flaky(_payload):
        raise RuntimeError("try again")

    monkeypatch.setitem(jobs.HANDLERS, "test_ok", lambda _p: None)
    monkeypatch.setitem(jobs.HANDLERS, "test_flaky", flaky)
    with app.app_context():
        _job("test_ok")
        db.session.commit()
        claimed = jobs.claim("w")
        assert claimed.heartbeat_at is not None
        assert abs(claimed.heartbeat_at - datetime.now(UTC)) < timedelta(seconds=30)
        done = jobs.run_job(claimed.id, "w")
        assert (done.status, done.heartbeat_at) == (JobStatus.done, None)
        _job("test_flaky")
        db.session.commit()
        retried = jobs.run_once("w")
        assert (retried.status, retried.heartbeat_at) == (JobStatus.queued, None)


def test_a_live_slow_job_is_never_handed_to_a_second_worker(app, monkeypatch):
    """Two workers for real. A's job runs several cutoffs long; B gives back stale jobs and
    claims all the while. A's heartbeat keeps the job A's: it runs once, and is done once."""
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 0.1)
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 2.0)
    runs, started = [], threading.Event()

    def slow(_payload):
        runs.append(threading.current_thread().name)
        started.set()
        time.sleep(5.0)

    monkeypatch.setitem(jobs.HANDLERS, "test_slow", slow)
    with app.app_context():
        jid = _job("test_slow")
        db.session.commit()

    def a():
        job = jobs.run_once("A")
        return job.id, job.status

    def b():
        started.wait(5)
        seen, end = [], time.monotonic() + 5.0
        while time.monotonic() < end:
            gave_back = jobs.requeue_stale()
            got = jobs.run_once("B")
            seen.append((gave_back, got and got.id))
            time.sleep(0.05)
        return seen

    out = {}
    threads = [_in_thread(app, "A", a, out), _in_thread(app, "B", b, out)]
    try:
        for t in threads:
            t.start()
    finally:
        for t in threads:
            t.join(20)
    assert not any(t.is_alive() for t in threads)
    assert out["A"] == (jid, JobStatus.done), out
    assert len(out["B"]) > 30  # B really polled through the whole run
    assert all(seen == (0, None) for seen in out["B"]), out["B"]
    assert runs == ["A"]
    with app.app_context():
        job = db.session.get(Job, jid)
        assert (job.status, job.attempts, job.locked_by) == (JobStatus.done, 1, "A")
    assert _beat_threads() == []


def test_without_a_heartbeat_the_job_would_run_twice_and_the_late_finish_is_fenced(
    app, monkeypatch, caplog
):
    """The control for the test above: with no beat inside the cutoff, B takes A's job and
    runs it again (the double run the heartbeat prevents). A's late finish then changes
    nothing on B's claim."""
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 60)
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 1.0)
    monkeypatch.setattr(jobs.log, "disabled", False)
    runs, started = [], threading.Event()

    def slow(_payload):
        runs.append(threading.current_thread().name)
        started.set()
        time.sleep(4.0)

    monkeypatch.setitem(jobs.HANDLERS, "test_slow", slow)
    with app.app_context():
        jid = _job("test_slow")
        db.session.commit()

    def a():
        job = jobs.run_once("A")
        return job.status, job.locked_by

    def b():
        started.wait(5)
        end = time.monotonic() + 8
        while time.monotonic() < end:
            jobs.requeue_stale()
            if (got := jobs.run_once("B")) is not None:
                return got.id
            time.sleep(0.05)
        return None

    out = {}
    threads = [_in_thread(app, "A", a, out), _in_thread(app, "B", b, out)]
    with caplog.at_level(logging.ERROR, logger=jobs.log.name):
        try:
            for t in threads:
                t.start()
        finally:
            for t in threads:
                t.join(20)
    assert not any(t.is_alive() for t in threads)
    assert runs == ["A", "B"]
    assert out["B"] == jid
    assert out["A"] == (JobStatus.running, "B")  # A's finish left B's claim alone
    with app.app_context():
        job = db.session.get(Job, jid)
        assert (job.status, job.attempts, job.locked_by) == (JobStatus.done, 2, "B")
    assert any("another worker" in r.getMessage() for r in caplog.records)


def test_a_job_taken_over_mid_run_is_neither_finished_nor_retried_by_the_first_worker(
    app, monkeypatch
):
    from app.models import Organization

    taken = {}

    def handler(payload):
        # Meanwhile another worker gave the job back and claimed it.
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE jobs SET locked_by = 'B', attempts = attempts + 1, "
                    "heartbeat_at = now() WHERE id = :id"
                ),
                {"id": taken["id"]},
            )
        db.session.add(Organization(name="Late tail", slug=f"late-tail-{taken['id']}"))
        if payload.get("fail"):
            raise RuntimeError("boom")

    monkeypatch.setitem(jobs.HANDLERS, "test_taken", handler)
    for fail in (False, True):
        with app.app_context():
            taken["id"] = _job("test_taken", payload={"fail": fail})
            db.session.commit()
            jobs.run_once("A")
            job = db.session.get(Job, taken["id"])
            assert (job.status, job.locked_by, job.attempts) == (JobStatus.running, "B", 2)
            assert job.finished_at is None and job.error is None
            # What A's handler left uncommitted is not written for it either.
            assert (
                db.session.scalar(
                    select(db.func.count())
                    .select_from(Organization)
                    .where(Organization.slug == f"late-tail-{taken['id']}")
                )
                == 0
            )
            job.status = JobStatus.done  # out of the way of the next round's claim
            db.session.commit()


def test_a_job_running_on_another_worker_is_not_run_again_here(app, monkeypatch):
    calls = []
    monkeypatch.setitem(jobs.HANDLERS, "test_ok", lambda p: calls.append(p))
    with app.app_context():
        jid = _running("test_ok", worker="B:1", locked=1, beat=1)
        db.session.commit()
        job = jobs.run_job(jid, "A:1")
        assert calls == []
        assert (job.status, job.locked_by, job.attempts) == (JobStatus.running, "B:1", 1)


def test_a_finished_job_run_again_by_hand_runs_as_a_new_attempt(app, monkeypatch):
    calls = []
    monkeypatch.setitem(jobs.HANDLERS, "test_ok", lambda p: calls.append(p))
    with app.app_context():
        jid = _job(
            "test_ok",
            status=JobStatus.failed,
            attempts=1,
            max_attempts=1,
            finished_at=datetime.now(UTC),
            error="first try",
        )
        db.session.commit()
        job = jobs.run_job(jid)
        assert len(calls) == 1
        assert (job.status, job.attempts, job.error) == (JobStatus.done, 2, None)


def test_the_heartbeat_beats_during_a_long_job_and_stops_with_it(app, monkeypatch):
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 0.05)
    stamps, ids = [], {}

    def slow(_payload):
        for _ in range(2):
            time.sleep(0.3)
            with db.engine.connect() as conn:
                stamps.append(
                    conn.execute(
                        text("SELECT heartbeat_at FROM jobs WHERE id = :id"), {"id": ids["job"]}
                    ).scalar()
                )
        assert _beat_threads() == [f"job {ids['job']} heartbeat"]

    monkeypatch.setitem(jobs.HANDLERS, "test_slow", slow)
    with app.app_context():
        ids["job"] = _job("test_slow")
        db.session.commit()
        claimed_at = jobs.claim("w").heartbeat_at
        assert jobs.run_job(ids["job"], "w").status is JobStatus.done
    assert len(stamps) == 2 and claimed_at < stamps[0] < stamps[1]
    assert _beat_threads() == []


def test_a_heartbeat_that_cannot_reach_the_database_never_fails_the_job(app, monkeypatch, caplog):
    """A database blip (or anything else) inside the beat is logged and tried again soon;
    the beat thread never dies of it, and the job never notices."""
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "BEAT_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(jobs.log, "disabled", False)
    real, tries, ids = jobs._Heartbeat.beat, [], {}

    def flaky(self):
        tries.append(1)
        if len(tries) == 1:
            raise _down()
        if len(tries) == 2:
            raise RuntimeError("not a database error either")
        return real(self)

    def slow(_payload):
        time.sleep(0.6)
        with db.engine.connect() as conn:
            ids["stamp"] = conn.execute(
                text("SELECT heartbeat_at FROM jobs WHERE id = :id"), {"id": ids["job"]}
            ).scalar()

    monkeypatch.setattr(jobs._Heartbeat, "beat", flaky)
    monkeypatch.setitem(jobs.HANDLERS, "test_slow", slow)
    with app.app_context(), caplog.at_level(logging.WARNING, logger=jobs.log.name):
        ids["job"] = _job("test_slow")
        db.session.commit()
        claimed_at = jobs.claim("w").heartbeat_at
        job = jobs.run_job(ids["job"], "w")
        assert job.status is JobStatus.done
    assert len(tries) >= 4  # failed twice, then kept beating
    assert sum("heartbeat" in r.getMessage() for r in caplog.records) >= 2
    assert ids["stamp"] > claimed_at  # a beat after the failures landed
    assert _beat_threads() == []


def test_a_beat_never_stamps_a_job_another_claim_now_holds(app):
    """hostname:pid can repeat (a restarted container): the claim's attempt number is part
    of the fence, so an old claim's beat cannot keep a newer claim of the same name alive."""
    with app.app_context():
        jid = _running(worker="w", attempts=2, locked=600, beat=500)
        db.session.commit()
        before = db.session.get(Job, jid).heartbeat_at
        assert jobs._Heartbeat(jid, "w", 1).beat() is False
        assert jobs._Heartbeat(jid, "other", 2).beat() is False
        db.session.expire_all()
        assert db.session.get(Job, jid).heartbeat_at == before
        assert jobs._Heartbeat(jid, "w", 2).beat() is True
        db.session.expire_all()
        assert db.session.get(Job, jid).heartbeat_at > before


def test_giving_back_skips_a_row_someone_holds_and_never_waits_for_it(app):
    with app.app_context():
        jid = _running(locked=120, beat=120)
        db.session.commit()
        holder = db.engine.connect()
        tx = holder.begin()
        try:
            holder.execute(text("SELECT 1 FROM jobs WHERE id = :id FOR UPDATE"), {"id": jid})
            # Waiting would end in an error here instead of hanging the suite.
            db.session.execute(text("SET LOCAL lock_timeout = '3s'"))
            started = time.monotonic()
            assert jobs.requeue_stale() == 0
            assert time.monotonic() - started < 2
        finally:
            tx.rollback()
            holder.close()
        assert jobs.requeue_stale() == 1


def test_the_worker_gives_back_stale_jobs_between_jobs_not_only_every_five_minutes(
    app, monkeypatch
):
    sweeps, requeues, runs = [], [], []

    def run_once():
        runs.append(1)
        if len(runs) == 3:
            raise SystemExit
        return None

    monkeypatch.setattr(worker, "REQUEUE_SECONDS", 0)
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 0)  # no give-back window (jobs.reach) here
    _main_until(
        app, monkeypatch, run_once, lambda: sweeps.append(1) or {}, lambda: requeues.append(1)
    )
    assert (len(sweeps), len(requeues), len(runs)) == (1, 3, 3)


def test_after_a_database_outage_the_worker_waits_before_giving_any_job_back(app, monkeypatch):
    """When the database comes back, every live worker's beats failed during the outage too:
    reading that silence as death would hand a live job to a second worker. The worker gives
    nothing back after it starts, nor while the database is away, until it has reached the
    database for one cutoff (counted from its first poll that reached it again). A clock of
    its own: each poll takes ten seconds of it."""

    def scenario(quiet):
        clock, requeued_at = [0.0], []
        polls = iter([None, None, None, "down", "down", None, None, None, None, "exit"])

        def run_once():
            step = next(polls)
            clock[0] += 10
            if step == "down":
                raise _down()
            if step == "exit":
                raise SystemExit
            return None

        monkeypatch.setattr(jobs, "reach", jobs._Reach(clock=lambda: clock[0]))
        monkeypatch.setattr(worker, "REQUEUE_SECONDS", 0)
        monkeypatch.setattr(jobs, "SILENT_SECONDS", quiet)
        _main_until(app, monkeypatch, run_once, requeue=lambda: requeued_at.append(clock[0]))
        return requeued_at

    # Started at 0: proven at 30. The polls at 30 and 40 fail; the one from 50 reaches the
    # database again (at 60), so the next give-back is at 90, one cutoff later.
    assert scenario(30) == [30, 90]
    # With no window it gives back at every poll, except straight after a failed one.
    assert scenario(0) == [0, 10, 20, 30, 60, 70, 80, 90]


@pytest.mark.usefixtures("inline_jobs")
def test_inline_jobs_still_run_at_once(app, monkeypatch):
    calls = []
    monkeypatch.setitem(jobs.HANDLERS, "test_ok", lambda p: calls.append(p))
    with app.app_context():
        job = jobs.enqueue("test_ok", {"n": 1})
        db.session.commit()
        job = db.session.get(Job, job.id)
        assert calls == [{"n": 1}]
        assert (job.status, job.locked_by, job.heartbeat_at) == (JobStatus.done, "inline", None)
    assert _beat_threads() == []


# ---- A56 follow-up: only a worker that has itself reached the database for a whole cutoff
# reads another worker's silence as death ----


def test_a_restarted_worker_gives_back_its_predecessors_job_after_one_cutoff_not_at_once(
    app, monkeypatch
):
    """A worker that has just started has seen nothing: a live worker's beats may have been
    failing until a moment ago (the database was away), so its silence proves nothing yet.
    The predecessor's job still comes back, one cutoff after the new worker reached the
    database."""
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 0.5)
    monkeypatch.setattr(worker, "REQUEUE_SECONDS", 0)
    real_requeue = jobs.requeue_stale
    with app.app_context():
        jid = _running(locked=300, beat=120)
        db.session.commit()
    gave, t0 = [], time.monotonic()

    def requeue():
        if real_requeue():
            gave.append(time.monotonic() - t0)

    def run_once():
        if gave or time.monotonic() - t0 > 5:
            raise SystemExit
        time.sleep(0.01)
        return None

    _main_until(app, monkeypatch, run_once, requeue=requeue)
    assert len(gave) == 1 and gave[0] >= 0.5, gave
    with app.app_context():
        job = db.session.get(Job, jid)
        assert (job.status, job.locked_by, job.heartbeat_at) == (JobStatus.queued, None, None)


def test_a_worker_busy_through_an_outage_never_hands_a_live_job_to_itself(app, monkeypatch):
    """The verifier's A56 probe (3 runs of 3): worker X sits in a job that never touches the
    database while the database is away for longer than the cutoff, so X's loop never sees
    the outage; only X's heartbeat thread does. Worker Z is alive, in a long job, and its
    beats failed through the outage too. X's job ends just after the database returns, before
    Z's next beat lands: X must not read Z's silence as death (that ran Z's job twice). It
    gives nothing back until X itself has reached the database, without a failure, for one
    whole cutoff after its last failed beat; then a really dead worker's job does come back."""
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 0.2)
    monkeypatch.setattr(jobs, "BEAT_RETRY_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 1.0)
    monkeypatch.setattr(worker, "REQUEUE_SECONDS", 0)
    real_requeue, real_run_once, real_beat = jobs.requeue_stale, jobs.run_once, jobs._Heartbeat.beat
    outage, back, stop_z = threading.Event(), threading.Event(), threading.Event()
    failed_beats, ran, gave, ids, at = [], [], [], {}, {}

    def beat(self):  # X's heartbeat thread: the database is away during the outage
        if outage.is_set():
            failed_beats.append(time.monotonic())
            raise _down()
        return real_beat(self)

    def busy(_payload):  # never touches the database itself
        outage.set()
        time.sleep(1.3)  # longer than the cutoff
        outage.clear()
        back.set()
        time.sleep(0.1)  # the job ends just after the database is back

    monkeypatch.setattr(jobs._Heartbeat, "beat", beat)
    monkeypatch.setitem(jobs.HANDLERS, "test_busy", busy)
    monkeypatch.setitem(jobs.HANDLERS, "test_noop", lambda p: ran.append(p["name"]))
    monkeypatch.setitem(jobs.HANDLERS, "test_z", lambda p: ran.append("Z's job"))
    with app.app_context():
        ids["dead1"] = _running("test_noop", payload={"name": "dead1"}, locked=300, beat=120)
        db.session.commit()
        engine = db.engine

    def z_beats():
        """Z is alive: its beats failed through the outage, and its first one after it lands
        a moment after X's job ended (in the probe: about a second later)."""
        back.wait(10)
        time.sleep(0.3)
        while not stop_z.is_set():
            with engine.begin() as conn:
                conn.execute(text("UPDATE jobs SET heartbeat_at = now() WHERE id = :id"),
                             {"id": ids["z"]})  # fmt: skip
            stop_z.wait(0.1)

    z = threading.Thread(target=z_beats, name="Z beats", daemon=True)
    t0 = time.monotonic()

    def requeue():
        gave.append((time.monotonic(), real_requeue()))

    def run_once():
        if "z" not in ids and ran == ["dead1"]:
            # X gave back the dead job and ran it: X has proved itself. Z takes its job now.
            ids["z"] = _running("test_z", worker="Z:1", locked=1, beat=0)
            _job("test_busy", payload={})
            db.session.commit()
            z.start()
        job = real_run_once("X")
        if job is not None and job.kind == "test_busy":
            at["end"] = time.monotonic()
            ids["dead2"] = _running("test_noop", payload={"name": "dead2"}, locked=300, beat=120)
            db.session.commit()
        if time.monotonic() - at.get("end", float("inf")) > 2.5 or time.monotonic() - t0 > 20:
            raise SystemExit
        time.sleep(0.01)
        return job

    try:
        _main_until(app, monkeypatch, run_once, requeue=requeue)
    finally:
        stop_z.set()
        if z.is_alive():
            z.join(5)
    assert "end" in at, "the busy job never ran"
    assert len(failed_beats) >= 2  # X's heartbeat thread really saw the outage
    # Z's job stays Z's: never given back, never run here.
    assert "Z's job" not in ran, ran
    with app.app_context():
        zjob = db.session.get(Job, ids["z"])
        assert (zjob.status, zjob.locked_by, zjob.attempts) == (JobStatus.running, "Z:1", 1)
        dead2 = db.session.get(Job, ids["dead2"])
        assert (dead2.status, dead2.attempts) == (JobStatus.done, 2)
    # The dead job after the outage did come back, but only one cutoff after X's last failed
    # beat (X's loop never saw the outage itself).
    after = [t for t, n in gave if t > at["end"] and n]
    assert after, gave
    assert after[0] - failed_beats[-1] >= jobs.SILENT_SECONDS
    assert ran == ["dead1", "dead2"]


def test_the_give_back_window_needs_a_whole_unbroken_cutoff_of_successes(monkeypatch):
    """jobs.reach on a clock of its own: what opens, closes and restarts the window."""
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 90.0)
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 15.0)
    now = [0.0]
    reach = jobs._Reach(clock=lambda: now[0])

    def ok_every_10s(start, end):
        for t in range(start, end + 1, 10):
            now[0] = t
            reach.ok(began=t)

    now[0] = 1000
    assert not reach.proven()  # a process that has just started has seen nothing
    ok_every_10s(1000, 1080)
    assert not reach.proven()
    ok_every_10s(1090, 1090)
    assert reach.proven()  # 90 s of successes
    now[0] = 1095
    reach.failed()  # any thread: the heartbeat thread of a busy job, say
    assert not reach.proven()
    now[0] = 1096
    reach.ok(began=1094)  # began before the failure: it may have answered before it
    now[0] = 1180
    assert not reach.proven()
    ok_every_10s(1180, 1260)
    assert not reach.proven()  # opened at 1180, the first success after the failure
    ok_every_10s(1270, 1270)
    assert reach.proven()
    # A success more than two beats after the one before restarts the window: a call that
    # hung through an outage and then answered raised nothing.
    ok_every_10s(1310, 1390)
    assert not reach.proven()
    ok_every_10s(1400, 1400)
    assert reach.proven()
    now[0] = 1431  # and with no success for over two beats, it is not proven either
    assert not reach.proven()
    reach.reset()
    ok_every_10s(1440, 1520)
    assert not reach.proven()


def test_the_window_counts_what_the_watched_call_did(monkeypatch):
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 0.0)
    reach = jobs._Reach()
    with reach.watch(jobs.DB_DOWN):
        pass
    assert reach.proven()
    with pytest.raises(ValueError), reach.watch(jobs.DB_DOWN):
        raise ValueError("a bug, not the database")
    assert reach.proven()  # not one of the failures it watches for
    with pytest.raises(OperationalError), reach.watch(jobs.DB_DOWN):
        raise _down()
    assert not reach.proven()
    with reach.watch():
        pass
    assert reach.proven()
    with pytest.raises(RuntimeError), reach.watch():
        raise RuntimeError("the give-back failed, whatever the reason")
    assert not reach.proven()


def test_a_job_that_lost_the_database_closes_the_window_even_when_its_end_is_written(
    app, monkeypatch
):
    """The handler died of the database going away and the database was back for the end
    write: run_once returns normally, so the loop alone would never know."""
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 0.0)
    monkeypatch.setattr(jobs, "reach", jobs._Reach())

    def lost(_payload):
        raise _down()

    monkeypatch.setitem(jobs.HANDLERS, "test_lost", lost)
    with app.app_context():
        jobs.reach.ok(began=time.monotonic())
        assert jobs.reach.proven()
        jid = _job("test_lost")
        db.session.commit()
        with jobs.reach.watch(jobs.DB_DOWN):
            job = jobs.run_once("w")
        assert (job.id, job.status) == (jid, JobStatus.queued)  # retried later, as before
        assert not jobs.reach.proven()


def test_a_failed_beat_alone_closes_the_window_while_the_loop_is_busy(app, monkeypatch):
    """The loop is inside a handler that never touches the database: only the job's heartbeat
    thread can see the database go away. Its failed beat closes the window, though the job is
    shorter than two beats (the gap rule cannot explain it) and its end was written."""
    monkeypatch.setattr(jobs, "SILENT_SECONDS", 0.0)
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 1.0)  # two beats: longer than this whole job
    monkeypatch.setattr(jobs, "reach", jobs._Reach())
    beat_failed = threading.Event()

    def beat(self):
        beat_failed.set()
        raise _down()

    def busy(_payload):  # never touches the database
        assert beat_failed.wait(5)
        time.sleep(0.05)

    monkeypatch.setattr(jobs._Heartbeat, "beat", beat)
    monkeypatch.setitem(jobs.HANDLERS, "test_busy", busy)
    with app.app_context():
        jid = _job("test_busy", payload={})
        db.session.commit()
        jobs.reach.ok(began=time.monotonic())
        assert jobs.reach.proven()
        started = time.monotonic()
        with jobs.reach.watch(jobs.DB_DOWN):  # as the worker loop runs it
            job = jobs.run_once("X")
        assert (job.id, job.status) == (jid, JobStatus.done)
        assert time.monotonic() - started < 2 * jobs.BEAT_SECONDS
        assert not jobs.reach.proven()
    assert _beat_threads() == []
