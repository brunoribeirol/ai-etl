# Skill: run-pipeline

Run and verify a pipeline scenario end-to-end.

## Steps

1. Check that `.env` exists with `OPENAI_API_KEY` set (read `.env.example` for reference).
2. Run: `uv run python -m ai_etl run --spec "<spec>"` or `make run-scenario1` (or 2/3).
3. Check the output:
   - Audit JSON: `case_study/results/scenario<N>/` — verify `status == "completed"` and `audit_log` has 5 entries (one per agent).
   - Output file or rows_loaded in the JSON.
4. Report: spec used, status, rows_loaded, any errors from audit_log, path to output.

## Quick verification checklist

- [ ] `status == "completed"` in audit JSON
- [ ] `audit_log` has exactly 5 entries (orchestrator, extractor, transformer, quality, loader)
- [ ] `quality_report.severity` is not "error"
- [ ] Output file exists at the expected destination
- [ ] No `error` field in the state

## Run all 3 scenarios (case study protocol)

Each scenario must be run 5 times for the case study metrics:

```bash
for i in 1 2 3 4 5; do make run-scenario1; done
for i in 1 2 3 4 5; do make run-scenario2; done
for i in 1 2 3 4 5; do make run-scenario3; done
```

After running, summarize: success rate, average rows_loaded, common quality issues detected.
