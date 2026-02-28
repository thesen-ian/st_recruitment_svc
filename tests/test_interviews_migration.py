import tempfile
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _run_alembic_upgrade(database_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")


def test_interviews_migration_creates_tables(tmp_path):
    db_file = tmp_path / "test_interviews.db"
    database_url = f"sqlite:///{db_file}"

    if db_file.exists():
        db_file.unlink()

    _run_alembic_upgrade(database_url)

    engine = create_engine(database_url)
    insp = inspect(engine)

    tables = insp.get_table_names()
    assert 'interviews' in tables
    assert 'interview_reschedule_requests' in tables

    # Check foreign keys
    fks = insp.get_foreign_keys('interviews')
    # ensure one of the foreign keys references applications
    assert any(fk.get('referred_table') == 'applications' for fk in fks)

    fks_req = insp.get_foreign_keys('interview_reschedule_requests')
    assert any(fk.get('referred_table') == 'interviews' for fk in fks_req)
