from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from st_recruitment_svc import scheduler_jobs
from st_recruitment_svc.scheduler_jobs import JobDefinition


def test_advisory_lock_contention_only_one_exec(caplog):
    """Simulate two independent instances contending for same advisory lock.

    Use two fake DB sessions with controlled execute() behavior so only the
    first session acquires the lock and the second does not. The wrapped job
    should execute only once and the unlock should be attempted exactly once.
    """
    caplog.set_level(logging.WARNING)

    # Track actual job executions
    called = {"cnt": 0}

    def job_func():
        called["cnt"] += 1

    jd = JobDefinition(job_id="contention_job", job_name="contention_job", seconds=1, func=job_func)

    # Create two session mocks representing two separate app instances
    s1 = Mock()
    s2 = Mock()

    # Ensure close exists (wrapper calls session.close())
    s1.close = Mock()
    s2.close = Mock()

    # Helper to create execute return object with scalar() -> value
    def _res(val):
        m = Mock()
        m.scalar = Mock(return_value=val)
        return m

    # s1 will acquire the lock (True) and release (True)
    def s1_execute(stmt, params=None):
        s = str(stmt)
        if "pg_try_advisory_lock" in s:
            return _res(True)
        if "pg_advisory_unlock" in s:
            return _res(True)
        return _res(None)

    # s2 will fail to acquire the lock (False) and therefore not release
    def s2_execute(stmt, params=None):
        s = str(stmt)
        if "pg_try_advisory_lock" in s:
            return _res(False)
        if "pg_advisory_unlock" in s:
            # should not be called, but safe to return True if invoked
            return _res(True)
        return _res(None)

    s1.execute = Mock(side_effect=s1_execute)
    s2.execute = Mock(side_effect=s2_execute)

    # Database factory yields s1 then s2 for consecutive wrapper invocations
    sessions = [s1, s2]

    def db_factory():
        try:
            return sessions.pop(0)
        except Exception:
            # defensive: return a new dummy session if called unexpectedly
            dummy = Mock()
            dummy.close = Mock()
            dummy.execute = Mock(return_value=_res(False))
            return dummy

    # Create wrapper that uses our factory directly to simulate separate instances
    wrapper = scheduler_jobs.make_job_wrapper(jd, db_session_factory=db_factory)

    # Invoke twice as if two instances (sequential is deterministic)
    wrapper()
    wrapper()

    # Only one execution should have occurred
    assert called["cnt"] == 1

    # s1 should have had both acquire and release calls
    s1_calls = "\n".join(str(c.args[0]) for c in s1.execute.mock_calls)
    assert "pg_try_advisory_lock" in s1_calls
    assert "pg_advisory_unlock" in s1_calls

    # s2 should have had an acquire attempt but no unlock
    s2_calls = "\n".join(str(c.args[0]) for c in s2.execute.mock_calls)
    assert "pg_try_advisory_lock" in s2_calls
    assert not ("pg_advisory_unlock" in s2_calls)

    # Ensure unlock happened exactly once across both sessions
    total_unlocks = 0
    total_unlocks += sum(1 for c in s1.execute.mock_calls if "pg_advisory_unlock" in str(c.args[0]))
    total_unlocks += sum(1 for c in s2.execute.mock_calls if "pg_advisory_unlock" in str(c.args[0]))
    assert total_unlocks == 1

    # No sensitive strings in logs
    log_text = "\n".join(r.message for r in caplog.records)
    assert "postgres://" not in log_text
    assert "password" not in log_text
