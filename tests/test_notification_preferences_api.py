from datetime import datetime, timezone

from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc.models.base import NotificationPreferences, User
from sqlalchemy import select


def test_get_creates_defaults(client, db_session):
    # create a user
    user = User(email="u1@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role})
    resp = client.get("/api/notification-preferences/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == user.id
    # defaults per model server_default are true
    assert data["in_app_enabled"] is True
    assert data["notify_application_submitted"] is True

    # Ensure row created in DB
    stmt = select(NotificationPreferences).where(NotificationPreferences.user_id == user.id)
    np = db_session.execute(stmt).scalars().first()
    assert np is not None


def test_put_updates_existing(client, db_session):
    user = User(email="u2@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    # create an existing pref row with defaults
    pref = NotificationPreferences(user_id=user.id)
    db_session.add(pref)
    db_session.commit()
    db_session.refresh(pref)

    token = create_access_token({"sub": user.id, "role": user.role})
    # toggle a field
    resp = client.put(
        "/api/notification-preferences/me",
        json={"in_app_enabled": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["in_app_enabled"] is False

    # Ensure DB persisted
    db_session.refresh(pref)
    assert pref.in_app_enabled is False


def test_put_creates_if_missing(client, db_session):
    user = User(email="u3@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role})
    resp = client.put(
        "/api/notification-preferences/me",
        json={"notify_interview_updates": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == user.id
    assert data["notify_interview_updates"] is False

    stmt = select(NotificationPreferences).where(NotificationPreferences.user_id == user.id)
    np = db_session.execute(stmt).scalars().first()
    assert np is not None
    assert np.notify_interview_updates is False


def test_auth_required(client):
    # unauthenticated should return 401
    resp = client.get("/api/notification-preferences/me")
    assert resp.status_code == 401
    resp = client.put("/api/notification-preferences/me", json={"in_app_enabled": False})
    assert resp.status_code == 401
