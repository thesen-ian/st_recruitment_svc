import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///:memory:")

# JWT configuration: prefer environment variables but provide sensible defaults for tests/dev
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "test-secret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
# Access token lifetime in minutes
try:
    JWT_ACCESS_TOKEN_EXPIRES_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", "30"))
except Exception:
    JWT_ACCESS_TOKEN_EXPIRES_MINUTES = 30

# File storage roots. Can be overridden by environment in production/tests.
# Defaults are placed under /tmp to be writable in CI and tests.
PUBLIC_FILES_DIR = os.getenv("PUBLIC_FILES_DIR", os.path.join(os.getenv("TMPDIR", "/tmp"), "st_recruitment_svc", "public"))
PRIVATE_FILES_DIR = os.getenv("PRIVATE_FILES_DIR", os.path.join(os.getenv("TMPDIR", "/tmp"), "st_recruitment_svc", "private"))
