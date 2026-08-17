.PHONY: install test test-e2e lint format format-check type-check security check db-up db-down db-test-up app-db-up app-db-down app-db-test-up db-migrate run-scenario1 run-scenario2 run-scenario3 app api redis-up celery-worker clean unhide-pth

install:
	uv sync --all-extras
	@$(MAKE) unhide-pth

app:
	uv sync --all-extras --quiet
	@$(MAKE) unhide-pth
	uv run streamlit run app.py

# Sprint 6 (ADR-011) — the new HTTP API the Next.js frontend calls. Coexists
# with `app` (Streamlit) until the frontend cutover retires it.
api:
	uv sync --all-extras --quiet
	@$(MAKE) unhide-pth
	uv run uvicorn ai_etl.api.main:app --reload

# macOS + uv workaround: uv marks the .pth files it writes for editable
# installs as "hidden" (UF_HIDDEN). Python >= 3.12.7 skips hidden .pth
# files when building sys.path, which breaks `import ai_etl` silently.
# Clearing the flag after every sync keeps the editable install usable.
unhide-pth:
	@if [ "$$(uname)" = "Darwin" ]; then \
		chflags nohidden .venv/lib/python3.*/site-packages/*.pth 2>/dev/null || true; \
	fi

test:
	uv run pytest tests/unit/ tests/integration/ -v --cov=src/ai_etl --cov-report=term-missing --cov-fail-under=80

test-e2e:
	uv run pytest tests/e2e/ -v --timeout=300

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

format-check:
	uv run ruff format --check src/ tests/

type-check:
	uv run mypy src/

security:
	uv run bandit -c pyproject.toml -r src/ai_etl/
	uv run pip-audit

check: lint format-check type-check test test-e2e security

db-up:
	docker-compose up -d postgres

db-down:
	docker-compose stop postgres

db-test-up:
	docker-compose up -d postgres-test

app-db-up:
	docker-compose up -d app-postgres

app-db-down:
	docker-compose stop app-postgres

app-db-test-up:
	docker-compose up -d app-postgres-test

db-migrate:
	uv run alembic upgrade head

# Sprint 3 (ADR-008) — broker/backend for Celery async execution + rate
# limiting. `app`/`celery-worker` (docker-compose.yml) both need this up.
redis-up:
	docker-compose up -d redis

celery-worker:
	uv run celery -A ai_etl.core.celery_app worker --loglevel=info

run-scenario1:
	uv run python -m ai_etl run \
		--spec "$$(cat case_study/pipelines/scenario1_spec.txt)" \
		--output case_study/results/scenario1/

run-scenario2:
	uv run python -m ai_etl run \
		--spec "$$(cat case_study/pipelines/scenario2_spec.txt)" \
		--output case_study/results/scenario2/

run-scenario3:
	uv run python -m ai_etl run \
		--spec "$$(cat case_study/pipelines/scenario3_spec.txt)" \
		--output case_study/results/scenario3/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	find . -name ".DS_Store" -delete 2>/dev/null; true
