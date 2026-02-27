import pytest

from st_recruitment_svc import scheduler


def test_start_shutdown_idempotent_calls():
    # Ensure clean state
    scheduler.shutdown_scheduler()
    assert not scheduler.is_running()

    # Calling shutdown when not started should not raise and should remain stopped
    scheduler.shutdown_scheduler()
    assert not scheduler.is_running()

    # Record current start count so tests are robust when other tests started the scheduler
    before = scheduler.get_start_count()

    # Start twice should be idempotent
    scheduler.start_scheduler()
    scheduler.start_scheduler()
    assert scheduler.is_running()

    after = scheduler.get_start_count()
    # start_count should increase by at most one (idempotent) and never decrease
    assert 0 <= (after - before) <= 1

    # Shutdown should stop and be idempotent
    scheduler.shutdown_scheduler()
    assert not scheduler.is_running()
    scheduler.shutdown_scheduler()
    assert not scheduler.is_running()


def test_scheduler_started_on_app_start_and_stopped_on_shutdown(client):
    # TestClient fixture starts the app lifespan which should start scheduler
    assert scheduler.is_running() is True
    # start_count should be at least 1 overall
    assert scheduler.get_start_count() >= 1

    # Explicit shutdown call should be safe and stop scheduler
    scheduler.shutdown_scheduler()
    assert not scheduler.is_running()
