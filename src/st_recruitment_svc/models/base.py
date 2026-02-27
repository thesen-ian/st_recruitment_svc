from sqlalchemy import Column, PrimaryKeyConstraint, String
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker, Session

from st_recruitment_svc.config import DATABASE_URL

Base = declarative_base()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def get_db() -> Session:
    session = scoped_session(sessionmaker(bind=engine))
    try:
        yield session
    finally:
        session.close()