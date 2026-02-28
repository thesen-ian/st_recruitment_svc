import os
import tempfile
from datetime import datetime, timezone, timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def _run_alembic_upgrade(database_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


def test_notifications_migration_and_basic_queries(tmp_path):
    db_file = tmp_path / "test_notifications.db"
    database_url = f"sqlite:///{db_file}"

    if db_file.exists():
        db_file.unlink()

    _run_alembic_upgrade(database_url)

    engine = create_engine(database_url)
    conn = engine.connect()
    try:
        # create a user to own notifications/preferences
        conn.execute(
            text("INSERT INTO users (id, email, password_hash, role, email_verified, status, failed_login_count, created_at) VALUES (:id, :email, :pwd, :role, :ev, :status, :flc, CURRENT_TIMESTAMP)"),
            {
                "id": "u1111111-1111-1111-1111-111111111111",
                "email": "user@example.com",
                "pwd": "hashed",
                "role": "job_seeker",
                "ev": False,
                "status": "active",
                "flc": 0,
            },
        )

        # Insert preference row
        conn.execute(
            text("INSERT INTO notification_preferences (id, user_id, in_app_enabled, notify_application_submitted, notify_application_status_changed, notify_application_withdrawn, notify_interview_updates, notify_admin_report_updates, created_at, updated_at) VALUES (:id, :user_id, :ia, :nas, :nasc, :naw, :niu, :nar, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
            {
                "id": "np2222222-2222-2222-2222-222222222222",
                "user_id": "u1111111-1111-1111-1111-111111111111",
                "ia": True,
                "nas": True,
                "nasc": True,
                "naw": True,
                "niu": True,
                "nar": True,
            },
        )

        # Insert notifications with different created_at timestamps
        now = datetime.now(timezone.utc)
        older = now - timedelta(hours=1)
        newer = now + timedelta(minutes=1)

        conn.execute(
            text("INSERT INTO notifications (id, user_id, title, body, type, created_at, updated_at) VALUES (:id, :user_id, :title, :body, :type, :created_at, :updated_at)"),
            {
                "id": "n1-11111111-1111-1111-111111111111",
                "user_id": "u1111111-1111-1111-1111-111111111111",
                "title": "Old",
                "body": "Old body",
                "type": "application_submitted",
                "created_at": older,
                "updated_at": older,
            },
        )
        conn.execute(
            text("INSERT INTO notifications (id, user_id, title, body, type, created_at, updated_at) VALUES (:id, :user_id, :title, :body, :type, :created_at, :updated_at)"),
            {
                "id": "n2-22222222-2222-2222-222222222222",
                "user_id": "u1111111-1111-1111-1111-111111111111",
                "title": "New",
                "body": "New body",
                "type": "status_changed",
                "created_at": newer,
                "updated_at": newer,
            },
        )

        # Unread filter should return both unless we mark one read
        res_all = conn.execute(text("SELECT id FROM notifications WHERE user_id = :uid ORDER BY created_at DESC"), {"uid": "u1111111-1111-1111-1111-111111111111"}).fetchall()
        ids_ordered = [r[0] for r in res_all]
        assert ids_ordered[0].startswith('n2') and ids_ordered[1].startswith('n1')

        # Mark one as read
        conn.execute(text("UPDATE notifications SET is_read = 1, read_at = CURRENT_TIMESTAMP WHERE id = :id"), {"id": "n1-11111111-1111-1111-111111111111"})

        # Query unread only
        res_unread = conn.execute(text("SELECT count(*) FROM notifications WHERE user_id = :uid AND is_read = 0"), {"uid": "u1111111-1111-1111-1111-111111111111"}).fetchone()
        assert res_unread[0] == 1

    finally:
        conn.close()
