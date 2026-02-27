from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from st_recruitment_svc import advisory_lock


def test_advisory_lock_key_deterministic_and_in_range():
    k1 = advisory_lock.advisory_lock_key("my-job")
    k2 = advisory_lock.advisory_lock_key("my-job")
    assert k1 == k2
    k3 = advisory_lock.advisory_lock_key("other-job")
    # different names usually map differently
    assert isinstance(k1, int)
    assert -2 ** 63 <= k1 < 2 ** 63
    assert k3 != k1 or "my-job" == "other-job"


def _make_db_mock(acquire_result=None, release_result=None, acquire_exc=None, release_exc=None):
    db = Mock()

    def execute_acquire(stmt, params):
        if acquire_exc:
            raise acquire_exc
        # emulate object with scalar() method as SQLAlchemy returns
        m = Mock()
        m.scalar = Mock(return_value=acquire_result)
        return m

    def execute_release(stmt, params):
        if release_exc:
            raise release_exc
        m = Mock()
        m.scalar = Mock(return_value=release_result)
        return m

    # side effects based on SQL string content
    def execute(stmt, params):
        s = str(stmt)
        if "pg_try_advisory_lock" in s:
            return execute_acquire(stmt, params)
        if "pg_advisory_unlock" in s:
            return execute_release(stmt, params)
        # default
        m = Mock()
        m.scalar = Mock(return_value=None)
        return m

    db.execute = Mock(side_effect=execute)
    return db


def test_acquire_success_and_unlock_called(caplog):
    caplog.set_level(logging.WARNING)
    db = _make_db_mock(acquire_result=True, release_result=True)
    job = "scheduled:job1"
    with advisory_lock.advisory_lock(db, job_name=job) as acquired:
        assert acquired is True
    # ensure execute called for acquire and release
    assert any("pg_try_advisory_lock" in str(call.args[0]) for call in db.execute.mock_calls)
    assert any("pg_advisory_unlock" in str(call.args[0]) for call in db.execute.mock_calls)
    # no sensitive strings in logs
    log_text = "\n".join(r.message for r in caplog.records)
    assert "postgres://" not in log_text
    assert "password" not in log_text


def test_acquire_failure_no_unlock(caplog):
    caplog.set_level(logging.WARNING)
    db = _make_db_mock(acquire_result=False, release_result=True)
    job = "scheduled:job2"
    with advisory_lock.advisory_lock(db, job_name=job) as acquired:
        assert acquired is False
    # ensure unlock was not called
    assert not any("pg_advisory_unlock" in str(call.args[0]) for call in db.execute.mock_calls)


def test_db_exception_on_acquire_does_not_raise_and_yields_false(caplog):
    caplog.set_level(logging.WARNING)
    db = _make_db_mock(acquire_exc=Exception("boom"))
    job = "scheduled:job3"
    with advisory_lock.advisory_lock(db, job_name=job) as acquired:
        assert acquired is False
    # ensure no unlock attempted
    assert not any("pg_advisory_unlock" in str(call.args[0]) for call in db.execute.mock_calls)
    # ensure we logged a warning about failure to acquire
    assert any("Failed to acquire advisory lock" in r.message or "Failed to acquire advisory lock" in r.getMessage() for r in caplog.records) or any("Failed to acquire advisory lock" in r.message for r in caplog.records)


def test_db_exception_on_release_is_swallowed_and_logged(caplog):
    caplog.set_level(logging.WARNING)
    db = _make_db_mock(acquire_result=True, release_exc=Exception("unlock fails"))
    job = "scheduled:job4"
    with advisory_lock.advisory_lock(db, job_name=job) as acquired:
        assert acquired is True
    # release was attempted and its exception was swallowed
    assert any("pg_advisory_unlock" in str(call.args[0]) for call in db.execute.mock_calls)
    # ensure warning logged about failed release
    assert any("Failed to release advisory lock" in r.message or "Failed to release advisory lock" in r.getMessage() for r in caplog.records) or any("Failed to release advisory lock" in r.message for r in caplog.records)
