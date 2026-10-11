.PHONY: install test test-e2e lint format format-check type-check security check db-up db-down db-test-up app-db-up app-db-down app-db-test-up db-migrate run-scenario1 run-scenario2 run-scenario3 case-study-data case-study-seed case-study api redis-up celery-worker clean unhide-pth mysql-test-up mysql-test-down mongodb-test-up mongodb-test-down sandbox-image sandbox-vcr-image

install:
	uv sync --all-extras
	@$(MAKE) unhide-pth

# Sprint 6 (ADR-011) — the HTTP API the Next.js frontend calls. Replaced
# the old Streamlit `app` target in the PR 6 cutover.
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
	uv run pip install --upgrade pip
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

# Sprint 11 (ADR-012) — real MySQL/MongoDB for the mysql_source.py/
# mongodb_source.py integration tests (tests/integration/test_mysql_source_real.py,
# test_mongodb_source_real.py). Both self-skip if unreachable, same
# convention as postgres-test/db-test-up.
mysql-test-up:
	docker-compose up -d mysql-test

mysql-test-down:
	docker-compose stop mysql-test

mongodb-test-up:
	docker-compose up -d mongodb-test

mongodb-test-down:
	docker-compose stop mongodb-test

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

# Case study over public data (NYC TLC trip records + zone lookup, Open-Meteo).
# `case-study-data` downloads and pins the extract in
# case_study/public_data_manifest.json; `case-study-seed` loads the zone table
# (needs POSTGRES_URL — `make db-up`); `case-study` calls real models and costs
# real money (needs OPENAI_API_KEY and/or ANTHROPIC_API_KEY). Scenario 4 hangs on
# macOS (plotly in the sandbox) — run it through the `Case study` workflow instead.
case-study-data:
	uv run python case_study/data/fetch_public_data.py

case-study-seed:
	uv run python case_study/data/seed_public_postgres.py

case-study:
	uv run python case_study/scripts/run_case_study.py

# ADR-038 — builds the image core/sandbox_docker.py's execute_in_sandbox()
# runs when AI_ETL_SANDBOX_BACKEND=docker. Local/dev-verified only today;
# not part of the Railway deploy build (top-level Dockerfile is unchanged).
sandbox-image:
	docker build -f docker/sandbox/Dockerfile -t ai-etl-sandbox:latest .

# ADR-039 — pushes the same image (docker/sandbox/Dockerfile; both
# run_sandboxed.py and run_sandboxed_vercel.py ship in it) to this project's
# Vercel Container Registry, for core/sandbox_vercel.py's
# AI_ETL_SANDBOX_BACKEND=vercel production backend. Requires the Vercel CLI
# authenticated and this repo linked to a Vercel project (`vercel login` +
# `vercel link`) — not automated in CI (no live Vercel project available
# there), a manual/deploy-time step. Re-run after any change to
# src/ai_etl/** that sandboxed code paths depend on, or to
# docker/sandbox/run_sandboxed*.py.
sandbox-vcr-image:
	vercel vcr build docker . ai-etl-sandbox:latest --push

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	find . -name ".DS_Store" -delete 2>/dev/null; true
