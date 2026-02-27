import os
import tempfile

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _run_alembic_upgrade(database_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


def test_alembic_upgrade_and_tables_created(tmp_path):
    db_file = tmp_path / "test_migration.db"
    database_url = f"sqlite:///{db_file}"

    # ensure file does not exist initially
    if db_file.exists():
        db_file.unlink()

    _run_alembic_upgrade(database_url)

    engine = create_engine(database_url)
    inspector = inspect(engine)

    expected_tables = {
        "users",
        "tokens",
        "file_objects",
        "notifications",
        "notification_preferences",
    }

    existing = set(inspector.get_table_names())
    assert expected_tables.issubset(existing)


def test_users_email_unique_and_token_hash_only(tmp_path):
    db_file = tmp_path / "test_migration2.db"
    database_url = f"sqlite:///{db_file}"

    if db_file.exists():
        db_file.unlink()

    _run_alembic_upgrade(database_url)

    engine = create_engine(database_url)
    conn = engine.connect()
    try:
        # insert first user
        conn.execute(
            text("INSERT INTO users (id, email, password_hash, role, email_verified, status, failed_login_count, created_at) VALUES (:id, :email, :pwd, :role, :ev, :status, :flc, CURRENT_TIMESTAMP)"),
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "email": "user@example.com",
                "pwd": "hashed",
                "role": "job_seeker",
                "ev": False,
                "status": "active",
                "flc": 0,
            },
        )

        # inserting second user with same email should fail due to unique constraint
        try:
            conn.execute(
                text("INSERT INTO users (id, email, password_hash, role, email_verified, status, failed_login_count, created_at) VALUES (:id, :email, :pwd, :role, :ev, :status, :flc, CURRENT_TIMESTAMP)"),
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "email": "user@example.com",
                    "pwd": "hashed2",
                    "role": "job_seeker",
                    "ev": False,
                    "status": "active",
                    "flc": 0,
                },
            )
            # On SQLite, unique constraint violation raises an IntegrityError
            raise AssertionError("Unique constraint on users.email not enforced")
        except IntegrityError:
            # expected
            pass

        # Verify tokens table has token_hash column and no plaintext token column
        inspector = inspect(conn)
        cols = {c['name'] for c in inspector.get_columns('tokens')}
        assert 'token_hash' in cols
        assert 'token' not in cols
    finally:
        conn.close()
