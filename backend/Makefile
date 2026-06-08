.PHONY: up down test migrate revision createtestdb

ADMIN_DATABASE_URL ?= postgresql+asyncpg://opngms:opngms@localhost:5432/opngms

up:
	docker compose up -d db

down:
	docker compose down

createtestdb:
	docker compose exec -T db psql -U opngms -d opngms -c "CREATE DATABASE opngms_test;" || true

migrate:
	ALEMBIC_DATABASE_URL=$(ADMIN_DATABASE_URL) alembic upgrade head

revision:
	ALEMBIC_DATABASE_URL=$(ADMIN_DATABASE_URL) alembic revision --autogenerate -m "$(m)"

test:
	python -m pytest -v
