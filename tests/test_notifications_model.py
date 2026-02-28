from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from st_recruitment_svc.models import Notification, NotificationPreferences, User, UserRole


def test_notification_preferences_and_unread_query(db_session):
    # create a user
    user = User(email="notify@example.com", password_hash="hashed", role=UserRole.job_seeker)
    db_session.add(user)
    db_session.flush()

    # insert a NotificationPreferences row for the user
    prefs = NotificationPreferences(user_id=user.id)
    db_session.add(prefs)
    db_session.flush()

    # create notifications with controlled timestamps
    now = datetime.now(timezone.utc)
    n1 = Notification(user_id=user.id, type="info", title="Old", body="old body", is_read=False, created_at=now - timedelta(minutes=5))
    n2 = Notification(user_id=user.id, type="info", title="New", body="new body", is_read=False, created_at=now - timedelta(minutes=1))
    n3 = Notification(user_id=user.id, type="info", title="Read", body="read body", is_read=True, read_at=now - timedelta(minutes=2), created_at=now - timedelta(minutes=2))

    db_session.add_all([n1, n2, n3])
    db_session.flush()

    # query unread notifications ordered newest-first
    stmt = select(Notification).where(Notification.user_id == user.id, Notification.is_read == False).order_by(Notification.created_at.desc())
    rows = db_session.execute(stmt).scalars().all()

    assert len(rows) == 2
    # newest unread should be n2
    assert rows[0].title == "New"
    assert rows[1].title == "Old"
