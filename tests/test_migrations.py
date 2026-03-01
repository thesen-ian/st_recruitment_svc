import os
import sqlite3
from datetime import datetime
import pathlib
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy import Table, MetaData, select

import alembic.config as alembic_config
import alembic.command as alembic_command


def _alembic_cfg(db_url: str, tmp_path: pathlib.Path) -> alembic_config.Config:
    # set DATABASE_URL so migrations/env.py picks it up
    os.environ['DATABASE_URL'] = db_url
    cfg = alembic_config.Config('alembic.ini')
    return cfg


def test_alembic_upgrade_and_constraints(tmp_path: pathlib.Path) -> None:
    db_file = tmp_path / 'test.db'
    db_url = f'sqlite:///{db_file}'

    cfg = _alembic_cfg(db_url, tmp_path)

    # run upgrade to head
    alembic_command.upgrade(cfg, 'head')

    # connect and inspect
    engine = create_engine(db_url)
    conn = engine.connect()
    meta = MetaData()
    meta.reflect(bind=engine)

    # Verify tables exist
    expected_tables = {'user', 'email_verification_token', 'password_reset_token', 'auth_rate_limit_counter'}
    assert expected_tables.issubset(set(meta.tables.keys()))

    users = Table('user', meta)

    # test unique constraint on email
    ins = users.insert().values(email='a@example.com', password_hash='h', role='job_seeker', status='active')
    conn.execute(ins)
    conn.commit()

    with pytest.raises(IntegrityError):
        conn.execute(ins)
        conn.commit()
    # ensure transaction state is clean for further operations
    conn.rollback()

    # test token tables accept used_at NULL and non-NULL
    evt = Table('email_verification_token', meta)
    prt = Table('password_reset_token', meta)

    now = datetime.utcnow()
    # used_at NULL
    conn.execute(evt.insert().values(user_id=1, token_hash='hash1', expires_at=now))
    conn.execute(prt.insert().values(user_id=1, token_hash='hash2', expires_at=now, used_at=None))
    conn.commit()

    # used_at non-NULL
    conn.execute(evt.insert().values(user_id=1, token_hash='hash3', expires_at=now, used_at=now))
    conn.commit()

    # test rate limit unique constraint
    rl = Table('auth_rate_limit_counter', meta)
    bucket_start = datetime.utcnow().replace(second=0, microsecond=0)
    conn.execute(rl.insert().values(identifier='id1', action='login', bucket_start=bucket_start, count=1))
    conn.commit()

    with pytest.raises(IntegrityError):
        conn.execute(rl.insert().values(identifier='id1', action='login', bucket_start=bucket_start, count=1))
        conn.commit()
    # cleanup failed transaction
    conn.rollback()

    conn.close()

    # downgrade to base (remove all tables)
    alembic_command.downgrade(cfg, 'base')

    # Reconnect and reflect should find none of the tables
    engine2 = create_engine(db_url)
    meta2 = MetaData()
    meta2.reflect(bind=engine2)
    for tbl in expected_tables:
        assert tbl not in meta2.tables
