.PHONY: install dev lint typecheck test test-cov build docker-build docker-run clean migrate

# ---------------------------------------------------------------------------
# Development setup
# ---------------------------------------------------------------------------

install:
	pip install -e ".[dev]"

dev:
	uvicorn aumos_vendor_intelligence.main:app --reload --host 0.0.0.0 --port 8000

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

lint-fix:
	ruff check --fix src/ tests/
	ruff format src/ tests/

typecheck:
	mypy src/

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=aumos_vendor_intelligence --cov-report=html --cov-report=term-missing

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------

migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(MSG)"

migrate-down:
	alembic downgrade -1

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

docker-build:
	docker build -t aumos-vendor-intelligence:latest .

docker-run:
	docker-compose -f docker-compose.dev.yml up

docker-down:
	docker-compose -f docker-compose.dev.yml down

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
