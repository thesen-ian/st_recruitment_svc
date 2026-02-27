import os
import tempfile
from datetime import datetime, timedelta, timezone

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


def test_file_objects_constraints_and_token_uniqueness(tmp_path):
    db_file = tmp_path / "test_migration3.db"
    database_url = f"sqlite:///{db_file}"

    if db_file.exists():
        db_file.unlink()

    _run_alembic_upgrade(database_url)

    engine = create_engine(database_url)
    conn = engine.connect()
    try:
        # create a user to own file objects and tokens
        conn.execute(
            text("INSERT INTO users (id, email, password_hash, role, email_verified, status, failed_login_count, created_at) VALUES (:id, :email, :pwd, :role, :ev, :status, :flc, CURRENT_TIMESTAMP)"),
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "email": "owner@example.com",
                "pwd": "hashed",
                "role": "company",
                "ev": False,
                "status": "active",
                "flc": 0,
            },
        )

        # Attempt to insert a file_object with null required fields should fail
        try:
            conn.execute(
                text("INSERT INTO file_objects (id, owner_user_id, purpose, original_filename, content_type, size_bytes, storage_path, created_at) VALUES (:id, NULL, :purpose, :orig, :ctype, :size, :path, CURRENT_TIMESTAMP)"),
                {
                    "id": "44444444-4444-4444-4444-444444444444",
                    "purpose": "company_logo",
                    "orig": "logo.png",
                    "ctype": "image/png",
                    "size": 1234,
                    "path": "public/company/4444/logo.png",
                },
            )
            raise AssertionError("Inserted file_object with null owner_user_id")
        except IntegrityError:
            # expected
            pass

        # Insert a valid file_object
        conn.execute(
            text("INSERT INTO file_objects (id, owner_user_id, purpose, original_filename, content_type, size_bytes, storage_path, created_at) VALUES (:id, :owner, :purpose, :orig, :ctype, :size, :path, CURRENT_TIMESTAMP)"),
            {
                "id": "55555555-5555-5555-5555-555555555555",
                "owner": "33333333-3333-3333-3333-333333333333",
                "purpose": "company_logo",
                "orig": "logo.png",
                "ctype": "image/png",
                "size": 1234,
                "path": "public/company/5555/logo.png",
            },
        )

        # Inserting negative size_bytes should fail due to CHECK constraint
        try:
            conn.execute(
                text("INSERT INTO file_objects (id, owner_user_id, purpose, original_filename, content_type, size_bytes, storage_path, created_at) VALUES (:id, :owner, :purpose, :orig, :ctype, :size, :path, CURRENT_TIMESTAMP)"),
                {
                    "id": "66666666-6666-6666-6666-666666666666",
                    "owner": "33333333-3333-3333-3333-333333333333",
                    "purpose": "company_cover",
                    "orig": "cover.png",
                    "ctype": "image/png",
                    "size": -10,
                    "path": "public/company/6666/cover.png",
                },
            )
            raise AssertionError("Inserted file_object with negative size_bytes")
        except IntegrityError:
            # expected on SQLite enforcing CHECK
            pass

        # Duplicate storage_path should fail due to unique constraint
        try:
            conn.execute(
                text("INSERT INTO file_objects (id, owner_user_id, purpose, original_filename, content_type, size_bytes, storage_path, created_at) VALUES (:id, :owner, :purpose, :orig, :ctype, :size, :path, CURRENT_TIMESTAMP)"),
                {
                    "id": "77777777-7777-7777-7777-777777777777",
                    "owner": "33333333-3333-3333-3333-333333333333",
                    "purpose": "resume",
                    "orig": "file.pdf",
                    "ctype": "application/pdf",
                    "size": 100,
                    "path": "public/company/5555/logo.png",  # same as valid inserted earlier
                },
            )
            raise AssertionError("Inserted duplicate storage_path")
        except IntegrityError:
            # expected
            pass

        # Token uniqueness: insert token and duplicate token_hash
        expires = datetime.now(timezone.utc) + timedelta(hours=1)
        created = datetime.now(timezone.utc)
        conn.execute(
            text("INSERT INTO tokens (id, user_id, token_hash, type, expires_at, created_at) VALUES (:id, :user, :hash, :type, :expires_at, :created_at)"),
            {
                "id": "88888888-8888-8888-8888-888888888888",
                "user": "33333333-3333-3333-3333-333333333333",
                "hash": "hash1",
                "type": "download",
                "expires_at": expires,
                "created_at": created,
            },
        )

        try:
            conn.execute(
                text("INSERT INTO tokens (id, user_id, token_hash, type, expires_at, created_at) VALUES (:id, :user, :hash, :type, :expires_at, :created_at)"),
                {
                    "id": "99999999-9999-9999-9999-999999999999",
                    "user": "33333333-3333-3333-3333-333333333333",
                    "hash": "hash1",
                    "type": "download",
                    "expires_at": expires,
                    "created_at": created,
                },
            )
            raise AssertionError("Inserted duplicate token_hash")
        except IntegrityError:
            # expected
            pass

    finally:
        conn.close()
