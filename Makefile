.PHONY: setup db-up db-down index test lint

setup:
	uv sync

db-up:
	docker compose up -d
	@echo "Waiting for postgres to be healthy..."
	@until docker compose ps postgres | grep -q healthy; do sleep 1; done
	uv run alembic upgrade head

db-down:
	docker compose down

index:
	uv run codecart index $(REPO)

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy src
