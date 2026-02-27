from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from st_recruitment_svc import scheduler
from st_recruitment_svc import scheduler_jobs
from st_recruitment_svc.scheduler_jobs import JobDefinition


def test_register_duplicate_raises():
    scheduler_jobs.clear_registered_jobs_for_tests()

    def f():
        pass

    jd = JobDefinition(job_id="j1", job_name="j1", seconds=10, func=f)
    scheduler_jobs.register_job(jd)
    with pytest.raises(ValueError):
        scheduler_jobs.register_job(jd)


def test_schedule_registered_jobs_calls_add_job_once():
    scheduler_jobs.clear_registered_jobs_for_tests()

    def f1():
        pass

    def f2():
        pass

    scheduler_jobs.register_job(JobDefinition(job_id="a", job_name="a", seconds=1, func=f1))
    scheduler_jobs.register_job(JobDefinition(job_id="b", job_name="b", seconds=2, func=f2))

    class FakeScheduler:
        def __init__(self):
            self.add_calls = []
            self._jobs = {}

        def get_job(self, id):
            return self._jobs.get(id)

        def add_job(self, func, trigger, seconds=None, id=None, coalesce=True, max_instances=1, misfire_grace_time=None):
            self.add_calls.append({"id": id, "seconds": seconds})
            self._jobs[id] = True

    s = FakeScheduler()
    scheduler_jobs.schedule_registered_jobs(s)
    assert len(s.add_calls) == 2
    ids = {c["id"] for c in s.add_calls}
    assert ids == {"a", "b"}

    # Calling again should not add duplicates because get_job returns existing
    scheduler_jobs.schedule_registered_jobs(s)
    assert len(s.add_calls) == 2


def test_wrapper_acquires_lock_and_runs(monkeypatch):
    scheduler_jobs.clear_registered_jobs_for_tests()
    called = {"cnt": 0}

    def job_func():
        called["cnt"] += 1

    jd = JobDefinition(job_id="runme", job_name="runme", seconds=1, func=job_func)

    # mock db session factory
    mock_session = Mock()
    mock_session.close = Mock()

    def fake_db_factory():
        return mock_session

    # mock advisory_lock to yield True
    class DummyCtxTrue:
        def __init__(self, session, job_name):
            pass

        def __enter__(self):
            return True

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scheduler_jobs, "_db_session_factory", fake_db_factory)
    monkeypatch.setattr("st_recruitment_svc.scheduler_jobs.advisory_lock", lambda db, job_name: DummyCtxTrue(db, job_name))

    wrapper = scheduler_jobs.make_job_wrapper(jd)
    wrapper()
    assert called["cnt"] == 1


def test_wrapper_skips_when_lock_not_acquired(monkeypatch):
    called = {"cnt": 0}

    def job_func():
        called["cnt"] += 1

    jd = JobDefinition(job_id="runme2", job_name="runme2", seconds=1, func=job_func)

    mock_session = Mock()
    mock_session.close = Mock()

    def fake_db_factory():
        return mock_session

    class DummyCtxFalse:
        def __init__(self, session, job_name):
            pass

        def __enter__(self):
            return False

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(scheduler_jobs, "_db_session_factory", fake_db_factory)
    monkeypatch.setattr("st_recruitment_svc.scheduler_jobs.advisory_lock", lambda db, job_name: DummyCtxFalse(db, job_name))

    wrapper = scheduler_jobs.make_job_wrapper(jd)
    wrapper()
    assert called["cnt"] == 0


def test_wrapper_handles_db_factory_failure_and_logs(caplog, monkeypatch):
    caplog.set_level(logging.WARNING)

    def job_func():
        pass

    jd = JobDefinition(job_id="x", job_name="x", seconds=1, func=job_func)

    def bad_db_factory():
        raise Exception("connection failed: postgres://user:pass")

    monkeypatch.setattr(scheduler_jobs, "_db_session_factory", bad_db_factory)

    wrapper = scheduler_jobs.make_job_wrapper(jd)
    wrapper()

    log_text = "\n".join(r.message for r in caplog.records)
    assert "postgres://" not in log_text
    assert "password" not in log_text
    assert any("Failed to obtain DB session" in r.message for r in caplog.records)
