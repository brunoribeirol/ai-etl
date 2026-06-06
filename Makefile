.PHONY: install test test-e2e lint type-check check db-up db-down run-scenario1 run-scenario2 run-scenario3 clean

install:
	uv sync --all-extras

test:
	uv run pytest tests/unit/ tests/integration/ -v

test-e2e:
	uv run pytest tests/e2e/ -v --timeout=300

lint:
	uv run ruff check src/ tests/

type-check:
	uv run mypy src/

check: lint type-check test

db-up:
	docker-compose up -d postgres

db-down:
	docker-compose stop postgres

db-test-up:
	docker-compose up -d postgres-test

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
