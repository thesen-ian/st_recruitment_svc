import pytest
from sqlalchemy import select

from st_recruitment_svc.models.base import (
    User,
    UserRole,
    CompanyProfile,
    ensure_company_profile,
    create_user,
)


def test_auto_create_company_profile(db_session):
    # Use the explicit helper to create a company user and profile in one transaction
    create_user(db_session, email="c@example.com", password_hash="x", role=UserRole.company)
    db_session.commit()

    stmt = select(CompanyProfile).where(CompanyProfile.company_id == db_session.execute(select(User).where(User.email == "c@example.com")).scalars().first().id)
    profiles = db_session.execute(select(CompanyProfile).where(CompanyProfile.company_id == db_session.execute(select(User).where(User.email == "c@example.com")).scalars().first().id)).scalars().all()
    assert len(profiles) == 1


def test_idempotent_ensure_company_profile(db_session):
    user = User(email="c2@example.com", password_hash="x", role=UserRole.company)
    db_session.add(user)
    db_session.flush()  # assign defaults

    # Call ensure twice; should only create one profile
    p1 = ensure_company_profile(db_session, user)
    p2 = ensure_company_profile(db_session, user)
    assert p1 is p2 or p1.id == p2.id

    db_session.commit()

    stmt = select(CompanyProfile).where(CompanyProfile.company_id == user.id)
    profiles = db_session.execute(stmt).scalars().all()
    assert len(profiles) == 1


def test_rollback_when_profile_creation_fails(db_session, monkeypatch):
    # Monkeypatch ensure_company_profile to raise to simulate failure during profile creation
    def _raise(session, user):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr('st_recruitment_svc.models.base.ensure_company_profile', _raise)

    # Use the explicit helper which will call ensure_company_profile and raise
    with pytest.raises(RuntimeError):
        create_user(db_session, email="c3@example.com", password_hash="x", role=UserRole.company)
    # The exception may have left the session in a failed state; reset before assertions
    db_session.rollback()

    # Ensure user was not persisted due to rollback
    stmt = select(User).where(User.email == "c3@example.com")
    users = db_session.execute(stmt).scalars().all()
    assert len(users) == 0
