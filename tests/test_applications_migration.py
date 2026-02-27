import os
import tempfile
from datetime import datetime, timezone, timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _run_alembic_upgrade(database_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


def test_applications_constraints_and_cascade(tmp_path):
    db_file = tmp_path / "test_applications.db"
    database_url = f"sqlite:///{db_file}"

    if db_file.exists():
        db_file.unlink()

    _run_alembic_upgrade(database_url)

    engine = create_engine(database_url)
    conn = engine.connect()
    try:
        # create company user and job
        conn.execute(
            text("INSERT INTO users (id, email, password_hash, role, email_verified, status, failed_login_count, created_at) VALUES (:id, :email, :pwd, :role, :ev, :status, :flc, CURRENT_TIMESTAMP)"),
            {
                "id": "c1111111-1111-1111-1111-111111111111",
                "email": "comp@example.com",
                "pwd": "hashed",
                "role": "company",
                "ev": False,
                "status": "active",
                "flc": 0,
            },
        )
        conn.execute(
            text("INSERT INTO jobs (id, company_id, title, status, created_at) VALUES (:id, :company_id, :title, :status, CURRENT_TIMESTAMP)"),
            {"id": "job-1111-1111-1111-111111111111", "company_id": "c1111111-1111-1111-1111-111111111111", "title": "Engineer", "status": "draft"},
        )

        # create job seeker user and required file_object + resume
        conn.execute(
            text("INSERT INTO users (id, email, password_hash, role, email_verified, status, failed_login_count, created_at) VALUES (:id, :email, :pwd, :role, :ev, :status, :flc, CURRENT_TIMESTAMP)"),
            {
                "id": "s2222222-2222-2222-2222-222222222222",
                "email": "seeker@example.com",
                "pwd": "hashed",
                "role": "job_seeker",
                "ev": False,
                "status": "active",
                "flc": 0,
            },
        )

        conn.execute(
            text("INSERT INTO file_objects (id, owner_user_id, purpose, original_filename, content_type, size_bytes, storage_path, created_at) VALUES (:id, :owner, :purpose, :orig, :ctype, :size, :path, CURRENT_TIMESTAMP)"),
            {
                "id": "f3333333-3333-3333-3333-333333333333",
                "owner": "s2222222-2222-2222-2222-222222222222",
                "purpose": "resume",
                "orig": "resume.pdf",
                "ctype": "application/pdf",
                "size": 100,
                "path": "private/resume/f3333.pdf",
            },
        )

        conn.execute(
            text("INSERT INTO resumes (id, user_id, label, file_object_id, created_at) VALUES (:id, :user_id, :label, :file_object_id, CURRENT_TIMESTAMP)"),
            {
                "id": "r4444444-4444-4444-4444-444444444444",
                "user_id": "s2222222-2222-2222-2222-222222222222",
                "label": "Default",
                "file_object_id": "f3333333-3333-3333-3333-333333333333",
            },
        )

        # Insert application - should succeed
        conn.execute(
            text("INSERT INTO applications (id, job_id, job_seeker_user_id, selected_resume_id, cover_letter, status, applied_at, updated_at) VALUES (:id, :job_id, :jsu, :resume, :cover, :status, :applied_at, :updated_at)"),
            {
                "id": "a5555555-5555-5555-5555-555555555555",
                "job_id": "job-1111-1111-1111-111111111111",
                "jsu": "s2222222-2222-2222-2222-222222222222",
                "resume": "r4444444-4444-4444-4444-444444444444",
                "cover": None,
                "status": "Applied",
                "applied_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )

        # Duplicate application for same job and user should fail
        try:
            conn.execute(
                text("INSERT INTO applications (id, job_id, job_seeker_user_id, selected_resume_id, cover_letter, status, applied_at, updated_at) VALUES (:id, :job_id, :jsu, :resume, :cover, :status, :applied_at, :updated_at)"),
                {
                    "id": "a6666666-6666-6666-6666-666666666666",
                    "job_id": "job-1111-1111-1111-111111111111",
                    "jsu": "s2222222-2222-2222-2222-222222222222",
                    "resume": "r4444444-4444-4444-4444-444444444444",
                    "cover": None,
                    "status": "Applied",
                    "applied_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            raise AssertionError("Duplicate application insertion should have failed")
        except IntegrityError:
            pass

        # Prepare job_posting and question/option for answers
        conn.execute(
            text("INSERT INTO job_postings (id, company_id, state, title, description, created_at) VALUES (:id, :company_id, :state, :title, :desc, CURRENT_TIMESTAMP)"),
            {"id": "jp7777777-7777-7777-7777-777777777777", "company_id": "c1111111-1111-1111-1111-111111111111", "state": "draft", "title": "Posting", "desc": "x"},
        )
        conn.execute(
            text("INSERT INTO job_posting_questions (id, job_posting_id, type, prompt, is_required, position, created_at) VALUES (:id, :jp, :type, :prompt, :is_required, :position, CURRENT_TIMESTAMP)"),
            {"id": "q8888888-8888-8888-8888-888888888888", "jp": "jp7777777-7777-7777-7777-777777777777", "type": "text", "prompt": "Q?", "is_required": False, "position": 0},
        )

        # Valid answer insert should succeed (answer_text populated)
        conn.execute(
            text("INSERT INTO application_answers (id, application_id, question_id, answer_text) VALUES (:id, :app, :q, :text)"),
            {"id": "ans999999-9999-9999-9999-999999999999", "app": "a5555555-5555-5555-5555-555555555555", "q": "q8888888-8888-8888-8888-888888888888", "text": "My answer"},
        )

        # Duplicate application_id+question_id should fail
        try:
            conn.execute(
                text("INSERT INTO application_answers (id, application_id, question_id, answer_text) VALUES (:id, :app, :q, :text)"),
                {"id": "ansaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "app": "a5555555-5555-5555-5555-555555555555", "q": "q8888888-8888-8888-8888-888888888888", "text": "Other"},
            )
            raise AssertionError("Duplicate application_answers insertion should have failed")
        except IntegrityError:
            pass

        # Check cascade: deleting application should remove answers
        conn.execute(text("DELETE FROM applications WHERE id = :id"), {"id": "a5555555-5555-5555-5555-555555555555"})
        res = conn.execute(text("SELECT count(*) as c FROM application_answers WHERE application_id = :id"), {"id": "a5555555-5555-5555-5555-555555555555"}).fetchone()
        assert res[0] == 0

        # Check the CHECK constraint: inserting zero populated answer fields
        # First, create a fresh application to reference
        conn.execute(
            text("INSERT INTO applications (id, job_id, job_seeker_user_id, selected_resume_id, cover_letter, status, applied_at, updated_at) VALUES (:id, :job_id, :jsu, :resume, :cover, :status, :applied_at, :updated_at)"),
            {
                "id": "abbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "job_id": "job-1111-1111-1111-111111111111",
                "jsu": "s2222222-2222-2222-2222-222222222222",
                "resume": "r4444444-4444-4444-4444-444444444444",
                "cover": None,
                "status": "Applied",
                "applied_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        )

        # Zero fields populated should fail
        try:
            conn.execute(
                text("INSERT INTO application_answers (id, application_id, question_id) VALUES (:id, :app, :q)"),
                {"id": "ansbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "app": "abbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "q": "q8888888-8888-8888-8888-888888888888"},
            )
            raise AssertionError("Insert with zero answer fields should have failed")
        except IntegrityError:
            pass

    finally:
        conn.close()
