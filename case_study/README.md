# Case study

Two kinds of input live here. Only the first is the case study reported in the technical report.

## The case study of the report: public data

| File | What |
|---|---|
| `public_data_manifest.json` | URLs and SHA-256 of the public inputs: NYC TLC yellow-taxi trips (January 2025, a fixed 20,000-row sample), the TLC taxi-zone table, the Open-Meteo historical archive |
| `data/fetch_public_data.py` | Downloads the inputs and checks them against the manifest (`make case-study-data`) |
| `data/seed_public_postgres.py` | Loads the taxi-zone table into PostgreSQL (`make case-study-seed`) |
| `pipelines/public_scenario1_spec.txt` to `public_scenario3_spec.txt` | The specifications of scenarios 1 to 3 (CSV to CSV; CSV and PostgreSQL to PostgreSQL; plus the REST source) |
| `pipelines/public_scenario4_question.txt` | The business question of scenario 4 (the analysis layer over scenario 1) |
| `scripts/run_case_study.py` | Runs N independent runs per scenario and model, records each run and checks the output against a reference computed with plain pandas (`scripts/output_checks.py`) |
| `scripts/connector_check.py` | One run per connector the scenarios do not reach (MySQL, MongoDB, SQLite, Parquet in S3) |

Run it through `.github/workflows/case-study.yml` (inputs `models`, `runs`, `scenarios`, `job`).
Scenario 4 runs only on Linux. Results and tables: the vault note
`tcc/artefact/case-study-evidence-2026-10-12.md`.

## Other inputs: tests, load and regression

| File | Used by |
|---|---|
| `pipelines/scenario1_spec.txt` to `scenario3_spec.txt`, `data/generate_sales.py`, `data/generate_orders.py`, `data/seed_postgres.py` | the end-to-end tests (`tests/e2e/`) and `make run-scenario1` to `3`: synthetic data written by the project, with errors inserted on purpose. Replaced as the report's case study by the public data, because data made by the author invites a bias objection |
| `data/generate_benchmark.py`, `data/profile_scale.py`, `scripts/load_test.py`, `results/sprint34/` | scale profile and load test (ADR-013, ADR-033) |
| `scenarios/*.json`, `scripts/regression_harness.py`, `results/sprint28/` | prompt-regression harness (ADR-029, `.github/workflows/prompt-regression.yml`) |
| `scripts/model_comparison.py`, `results/sprint8/` | the first model comparison (August), on synthetic data |
| `baselines/*.ipynb` | hand-written pandas baselines of the three synthetic scenarios |
| `results/runs_log.csv` | append-only log of `run_case_study.py` runs |
