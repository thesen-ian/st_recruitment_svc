from datetime import datetime, timezone, timedelta

from st_recruitment_svc.auth import create_access_token
from st_recruitment_svc.models.base import Notification, User
from sqlalchemy import select


def test_list_and_filter_notifications(client, db_session):
    # setup users
    user_a = User(email="a@example.com", password_hash="x", role="job_seeker")
    user_b = User(email="b@example.com", password_hash="x", role="job_seeker")
    db_session.add_all([user_a, user_b])
    db_session.commit()

    now = datetime.now(tz=timezone.utc)
    # three notifications for A with varying timestamps
    n1 = Notification(user_id=user_a.id, title="t1", body="b1", type="info", created_at=now - timedelta(minutes=3))
    n2 = Notification(user_id=user_a.id, title="t2", body="b2", type="info", created_at=now - timedelta(minutes=2))
    n3 = Notification(user_id=user_a.id, title="t3", body="b3", type="info", created_at=now - timedelta(minutes=1))
    # mark n1 as read
    n1.is_read = True
    n1.read_at = now - timedelta(minutes=2, seconds=30)

    # one notification for B
    nb = Notification(user_id=user_b.id, title="tb", body="bb", type="info", created_at=now)

    db_session.add_all([n1, n2, n3, nb])
    db_session.commit()

    token = create_access_token({"sub": user_a.id, "role": user_a.role})

    # GET default (all)
    resp = client.get("/api/notifications", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    items = data["items"]
    assert len(items) == 3
    # newest-first: n3, n2, n1
    assert items[0]["title"] == "t3"
    assert items[1]["title"] == "t2"
    assert items[2]["title"] == "t1"

    # GET unread filter
    resp = client.get("/api/notifications?filter=unread", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    items = data["items"]
    # only n2 and n3
    titles = {it["title"] for it in items}
    assert titles == {"t2", "t3"}


def test_mark_single_read_and_idempotent(client, db_session):
    user = User(email="c@example.com", password_hash="x", role="job_seeker")
    db_session.add(user)
    db_session.commit()

    now = datetime.now(tz=timezone.utc)
    n = Notification(user_id=user.id, title="t1", body="b1", type="info", created_at=now)
    db_session.add(n)
    db_session.commit()

    token = create_access_token({"sub": user.id, "role": user.role})

    # mark unread -> read
    resp = client.post(f"/api/notifications/{n.id}/read", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_read"] is True
    assert data["read_at"] is not None

    first_read_at = data["read_at"]

    # idempotent: call again
    resp2 = client.post(f"/api/notifications/{n.id}/read", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["is_read"] is True
    # read_at should remain present (we allow equal or later, but ensure not None)
    assert data2["read_at"] is not None


def test_read_all_and_authorization(client, db_session):
    user_a = User(email="d1@example.com", password_hash="x", role="job_seeker")
    user_b = User(email="d2@example.com", password_hash="x", role="job_seeker")
    db_session.add_all([user_a, user_b])
    db_session.commit()

    now = datetime.now(tz=timezone.utc)
    # for A: two unread
    na1 = Notification(user_id=user_a.id, title="a1", body="x", type="info", created_at=now - timedelta(seconds=10))
    na2 = Notification(user_id=user_a.id, title="a2", body="y", type="info", created_at=now - timedelta(seconds=5))
    # for B: one unread
    nb = Notification(user_id=user_b.id, title="b1", body="z", type="info", created_at=now)

    db_session.add_all([na1, na2, nb])
    db_session.commit()

    token_a = create_access_token({"sub": user_a.id, "role": user_a.role})
    token_b = create_access_token({"sub": user_b.id, "role": user_b.role})

    # user A mark all read
    resp = client.post("/api/notifications/read-all", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    data = resp.json()
    # should update 2
    assert data["updated"] == 2

    # unread filter for A should now be empty
    resp = client.get("/api/notifications?filter=unread", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []

    # Authorization: A attempting to mark B's notification returns 404
    resp = client.post(f"/api/notifications/{nb.id}/read", headers={"Authorization": f"Bearer {token_a}"})
    assert resp.status_code == 404

    # B can mark their own
    resp = client.post(f"/api/notifications/{nb.id}/read", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 200
