.PHONY: up down test migrate revision createtestdb

up:
	docker compose up -d db

down:
	docker compose down

createtestdb:
	docker compose exec -T db psql -U opngms -d opngms -c "CREATE DATABASE opngms_test;" || true

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

test:
	python -m pytest -v
