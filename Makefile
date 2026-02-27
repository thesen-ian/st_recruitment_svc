# DO NOT modify, rename, or remove these targets. They are used by the system.
install:
	poetry install

setup:
	poetry run alembic upgrade head

unittest:
	poetry run pytest tests

run:
	poetry run st_recruitment_svc