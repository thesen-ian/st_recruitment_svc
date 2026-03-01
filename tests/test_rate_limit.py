from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from fastapi import HTTPException
from st_recruitment_svc.auth import rate_limit
from st_recruitment_svc.models.auth import AuthRateLimitCounter


def test_allows_and_blocks_at_threshold(db_session):
    identifier = "user@example.com"
    action = rate_limit.AUTH_LOGIN

    # Initially allowed
    rate_limit.check_rate_limit(db_session, identifier=identifier, action=action)

    # Increment 4 times, should still be allowed
    for _ in range(4):
        rate_limit.increment_rate_limit(db_session, identifier=identifier, action=action)
        rate_limit.check_rate_limit(db_session, identifier=identifier, action=action)

    # 5th increment -> reaches threshold
    rate_limit.increment_rate_limit(db_session, identifier=identifier, action=action)

    # Now check_rate_limit should raise 429
    with pytest.raises(HTTPException) as exc:
        rate_limit.check_rate_limit(db_session, identifier=identifier, action=action)
    assert getattr(exc.value, 'status_code', None) == 429


def test_window_resets_after_expiry(db_session):
    identifier = "other@example.com"
    action = rate_limit.AUTH_LOGIN

    # Create an old bucket that is beyond the window
    past = datetime.now(timezone.utc) - timedelta(seconds=3600)
    row = AuthRateLimitCounter(identifier=identifier, action=action, bucket_start=past, count=10)
    db_session.add(row)
    db_session.commit()

    # check_rate_limit should allow because bucket is expired
    rate_limit.check_rate_limit(db_session, identifier=identifier, action=action)

    # increment_rate_limit should create a new bucket (reset) and set count to 1
    rate_limit.increment_rate_limit(db_session, identifier=identifier, action=action)

    # Fetch latest active bucket
    now_threshold = datetime.now(timezone.utc) - timedelta(seconds=900)
    stmt = (
        select(AuthRateLimitCounter)
        .where(
            AuthRateLimitCounter.identifier == identifier,
            AuthRateLimitCounter.action == action,
            AuthRateLimitCounter.bucket_start >= now_threshold,
        )
        .order_by(AuthRateLimitCounter.bucket_start.desc())
    )
    active = db_session.execute(stmt).scalars().first()

    assert active is not None
    assert active.count == 1


def test_actions_and_identifiers_separate(db_session):
    id_a = "a@example.com"
    id_b = "b@example.com"
    action_login = rate_limit.AUTH_LOGIN
    action_reset = rate_limit.AUTH_PASSWORD_RESET_REQUEST

    # Increment for id_a login
    rate_limit.increment_rate_limit(db_session, identifier=id_a, action=action_login)
    # Increment for id_a password reset
    rate_limit.increment_rate_limit(db_session, identifier=id_a, action=action_reset)
    # Increment for id_b login
    rate_limit.increment_rate_limit(db_session, identifier=id_b, action=action_login)

    # Ensure counts are separate
    stmt = select(AuthRateLimitCounter)
    rows = db_session.execute(stmt).scalars().all()
    key_counts = {(r.identifier, r.action): r.count for r in rows}
    assert key_counts.get((id_a, action_login)) == 1
    assert key_counts.get((id_a, action_reset)) == 1
    assert key_counts.get((id_b, action_login)) == 1
