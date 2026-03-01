from .base import Base, get_db

# Import auth models so they are registered on Base.metadata for migrations
from . import auth  # noqa: F401