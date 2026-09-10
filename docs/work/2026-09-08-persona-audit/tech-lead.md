# Tech Lead — Code Quality, Architectural Drift, Tech Debt

**Date:** 2026-09-08
**Persona:** Tech Lead (code quality / architectural drift / tech debt)
**Scope:** `src/`, `docs/adr/`, `pyproject.toml`, CI config, `frontend/src/` (secondary)
**Mode:** investigate and document only — no code modified.

---

## Ground truth (given, not re-run)

`make check` GREEN. Unit + integration: **1199 passed, 14 skipped, 1 warning, 95.11% coverage**.
Ruff, `ruff format --check`, and mypy (strict) all pass.
From the live log (`make-check.log`), the two jobs that were still running finished clean too:

- **e2e:** `5 passed, 85 warnings in 15.86s`
- **bandit:** `No issues identified` — 12,112 LOC scanned, 0 findings at every severity, `Total potential issues skipped due to specifically being disabled (e.g. #nosec BXXX): 30`
- **pip-audit:** `No known vulnerabilities found` (only skip: `ai-etl` itself, not on PyPI)
- `EXIT=0`

**Headline verdict:** this is defensible code. The LangGraph contract holds without a single exception, there is not one `TODO`/`FIXME`/`HACK` in 15.5k lines of `src/`, every `# type: ignore` carries a reason, and every SQL f-string is an identifier behind a regex allowlist. The findings below are about *the gates being narrower than they appear* and *the decision record having drifted behind the code* — not about the code itself being wrong.

---

## Task 1 — The "1 warning"

**Severity: Low (informational — no action needed in this repo's code)**

**Where:** `make-check.log:1250-1253`

```
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is
  deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa
```

**What:** Third-party, not project code. `starlette 1.3.1` (resolved in `uv.lock`) now wants `httpx2`; the project pins `httpx>=0.27.0` and resolves `httpx 0.28.1`. The warning fires once, at import of `fastapi.testclient`, from any test using `TestClient`.

**Why it matters:** Zero runtime impact today. It is an early signal of the Starlette 1.x → httpx2 migration that will eventually become a hard break. Worth naming so nobody spends time hunting it as a project bug.

**Note for contrast:** the e2e run emits **85** warnings — `celery/backends/redis.py:434 DeprecationWarning: Call to deprecated setex` × 17 per scenario file × 5 scenarios (`make-check.log:1367-1377`). Also third-party, also benign, but it means "1 warning" is a property of the unit/integration suite only, not of the project as a whole.

---

## Task 2 — `# type: ignore` audit

**Severity: PASS — no violations**

13 occurrences in `src/`. **All 13 have an explanatory comment on the same line.** The project rule ("no new `# type: ignore` without a comment explaining why") is fully honoured.

| File:line | Code | Reason given |
|---|---|---|
| `src/ai_etl/agents/analysis/advisor.py:259` | `typeddict-item` | "validated above" |
| `src/ai_etl/agents/pipeline/loader.py:78` | `assignment` | "non-None guaranteed by error short-circuit above" |
| `src/ai_etl/agents/pipeline/quality.py:45` | `assignment` | same |
| `src/ai_etl/api/serialization.py:43` | `no-any-return` | "plotly has no return-type stub here" |
| `src/ai_etl/core/graph.py:63-67` (×5) | `call-overload` | "see comment above" (lines 59-62 give a real 4-line explanation) |
| `src/ai_etl/core/logging_config.py:116` | `attr-defined` | "marker for idempotent re-configuration" |
| `src/ai_etl/services/execution_queue.py:328` | `untyped-decorator` | "celery has no type stubs for @task" |
| `src/ai_etl/services/retention_service.py:203` | `untyped-decorator` | same |
| `src/ai_etl/services/scheduler.py:59` | `untyped-decorator` | same |

**Bare `# type: ignore` with no reason: 0.**

---

## Task 3 — `TODO` / `FIXME` / `XXX` / `HACK` / `# noqa`

### 3a. TODO markers

**Severity: PASS**

`grep -rnE "TODO|FIXME|XXX|HACK" src/` → **zero hits**. Same for `frontend/src` and `frontend/app`. In a 15.5k-line backend with 47 ADRs of accumulated scope, this is genuinely unusual and reflects the "flag it in the ADR, not in a code comment" discipline the ADR set shows.

### 3b. FINDING — 19 `# noqa` directives in `src/` are dead code

**Severity: MEDIUM** — the strongest finding in this audit.

**Evidence** (`uv run ruff check --select RUF100 --output-format concise src/`, run locally during this audit; RUF100 is not part of `make lint`):

```
src/ai_etl/agents/_llm_codegen.py:80:28              RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/agents/analysis/analyst.py:220:35        RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/agents/analysis/planner.py:133:24        RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/agents/analysis/reviewer.py:120:24       RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/agents/analysis/science.py:426:35        RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/core/llm.py:290:31                       RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/core/sandbox.py:209:49                   RUF100 Unused `noqa` (non-enabled: S102)
src/ai_etl/core/sandbox.py:245:31                   RUF100 Unused `noqa` (non-enabled: S102)
src/ai_etl/core/sandbox.py:410:29                   RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/core/tenant_context.py:113:28            RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/core/tenant_context.py:170:24            RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/destinations/mysql_dest.py:54:51         RUF100 Unused `noqa` (non-enabled: PLR2004)
src/ai_etl/destinations/postgres_dest.py:45:51      RUF100 Unused `noqa` (non-enabled: PLR2004)
src/ai_etl/services/pipeline_service.py:168:28      RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/services/retention_service.py:163:39     RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/services/tenant_deletion_service.py:159:31  RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/services/tenant_deletion_service.py:179:39  RUF100 Unused `noqa` (non-enabled: BLE001)
src/ai_etl/sources/rest_source.py:51:34             RUF100 Unused `noqa` (non-enabled: UP024)
src/ai_etl/sources/rest_source.py:78:38             RUF100 Unused `noqa` (non-enabled: UP024)
Found 19 errors. [*] 19 fixable with the `--fix` option.
```

**Root cause** — `pyproject.toml:148`:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "N", "W"]
ignore = ["E501"]
```

Only pycodestyle (E/W), pyflakes (F), isort (I) and pep8-naming (N) are enabled. `BLE` (flake8-blind-except), `S` (flake8-bandit), `PL` (pylint), `UP` (pyupgrade) and `RUF` itself are all **off**, so every one of these 19 suppressions silences a rule that was never going to fire.

**Why it matters** — three distinct problems, in increasing order of seriousness:

1. **Misleading signal.** A reviewer (or a recruiter reading the repo) sees `# noqa: S102` on `sandbox.py:209`'s `exec()` and reasonably concludes a security linter is watching that line and was deliberately overridden. Nothing is watching it. The comments describe a gate that does not exist.
2. **A narrow linter presented as a broad one.** `make lint` = `ruff check src/ tests/`. With `E,F,I,N,W` it is essentially a formatter-and-imports check. It does **not** cover bugbear (`B`), security (`S`), comprehensions (`C4`), simplification (`SIM`), unused-arguments (`ARG`), return consistency (`RET`), or pyupgrade (`UP`). "Lint is green" is therefore much weaker evidence of quality than it reads as — especially in a repo that markets itself on the SR Big Tech standard.
3. **Silent drift.** Because `RUF100` is off, these 19 will accumulate forever with no signal, and the day someone widens `select`, they will get a wall of simultaneous new failures plus 19 suppressions of unknown current validity.

**Mitigating context (real, and it lowers this from High to Medium):** the `exec()` line is genuinely covered — by **Semgrep**, not ruff. `.github/workflows/semgrep.yml:44-55` runs `p/security-audit p/secrets p/python p/owasp-top-ten` with `--error` on every PR and push to `main`, and the `# nosemgrep` comments on `sandbox.py:209/245` are live, load-bearing suppressions. So the security posture is real; it is the *ruff* half of the story that is theatre.

**Suggested direction (not applied):** widen `select` to at least `["E","F","I","N","W","B","S","UP","RUF"]`, then let `--fix` clear the genuinely-dead suppressions and keep the ones that start firing. Doing this as its own PR (lint-config change, zero behavioural change) keeps the diff reviewable.

### 3c. Blind-except density

**Severity: LOW (observation, not a defect)**

There are **~34 `# nosec B110`/`# noqa: BLE001` bare-`except Exception: pass` sites** across `src/`. Every single one carries a written justification, and the pattern is consistent and deliberate ("best-effort bookkeeping, never fails the run"; "one bad tenant must not break the tick"). Representative examples:

- `src/ai_etl/services/scheduler.py:109` — `# nosec B110 — one bad pipeline must not break the tick`
- `src/ai_etl/services/retention_service.py:225` — `# nosec B110 — one bad tenant must not break the tick`
- `src/ai_etl/services/pipeline_service.py:980,1022` — best-effort bookkeeping
- `src/ai_etl/audit/db/runs.py:565,607,616` — best-effort artifact reload

The one to watch is `src/ai_etl/services/execution_queue.py:409`, the **only** `# nosec B110` in `src/` with **no reason text** — it reads bare `# nosec B110`. Every sibling in the same file (`:516`, `:548`) has one. Worth a one-line comment for consistency.

The systemic risk of the pattern is that a swallowed exception is invisible in production. Most sites here do log before swallowing; a spot check found `scheduler.py:109` and `retention_service.py:225` swallow inside a per-item loop, which is the correct shape. **Not a defect — flagged so the density is a conscious, reviewed number rather than an accident.**

---

## Task 4 — ADR open items, cross-checked against code

The ADR set (47 documents) is the strongest artifact in this repository. The convention "flag the deferral explicitly, never silently drop it" is applied with real consistency — phrases like *"explicitly out of scope, not silently assumed"* and *"flagged, not silently implied as done"* recur across ~30 of the 47. Cross-checking a sample against the code found **no case of an item claimed done that is not done**. The problems are the reverse: items **done** that the ADR still describes as **open**.

### 4a. FINDING — ADR-003 is factually wrong about the current code, and has been for ~14 sprints

**Severity: HIGH** (as documentation debt on the single most security-sensitive ADR)

**Where:** `docs/adr/ADR-003-exec-sandbox.md` — `**Status:** Accepted (with documented limitations)`, no superseded-by marker.

**What the ADR currently says vs. what the code does:**

| ADR-003 claim | Reality (verified) |
|---|---|
| "`exec()` is called from **three separate sites**, each with its own globals whitelist" (line 24 + table) | **One** site: `src/ai_etl/core/sandbox.py:209` and `:245` (script mode) — same function. `grep -rn "\bexec(" src/` outside `sandbox.py` returns only prose in comments. |
| Table rows point at `src/ai_etl/agents/analyst.py` and `src/ai_etl/agents/science.py` | **Those paths do not exist.** The files are `src/ai_etl/agents/analysis/analyst.py` / `science.py` since the `agents/pipeline` + `agents/analysis` reorg, and neither contains an `exec()`. |
| "`## Follow-up (open as of 2026-08)` — the three independent globals whitelists are a maintenance and audit risk… Not yet decided" | **Closed.** `ADR-007-unified-sandbox-policy.md:120` states verbatim: *"closes ADR-003's 'Follow-up' section — one reviewed base whitelist instead of three drifting copies."* |
| "declared `timeout_seconds` parameters are not enforced — a sandboxed call can run indefinitely" | **Enforced.** `core/sandbox.py:426` takes `timeout_seconds: int = 30`; `:556` computes `deadline = start + timeout_seconds`; `:579` returns `f"Execution exceeded {timeout_seconds}s — simplify the computation"`. Also `:169 scale_timeout_for_rows()`. |
| "Bandit's B102 is suppressed with `# nosec` at each of the three `exec()` call sites" | B102 is suppressed **globally** in `pyproject.toml:161` (`skips = ["B101", "B102"]`), not per-site. |
| "Related" section lists only ADR-001 | ADR-007, ADR-032, ADR-038, ADR-039 and ADR-046 all supersede or extend it; none is linked back from ADR-003. |

**The project already knows.** `docs/CURRENT_STATE.md:2122`:

> **`SECURITY.md`/`ADR-003` are now stale** — still describe 3 separate `exec()` sites; ADR-007 supersedes this but the older docs weren't rewritten (only cross-referenced). Low priority, but a reader landing on `SECURITY.md` first would get a wrong picture.

That note was written around Sprint 2 (`CURRENT_STATE.md:1973` records the unification as "Sprint 2, PR #23, ADR-007"). The project is now at Sprint 38+. **A known-stale doc that survives 36 sprints is not "low priority" anymore — it is the oldest open item in the repo, and it sits on the sandbox.**

**Why it matters:** ADR-003 is the file `CLAUDE.md`'s own reading table sends you to for "Execution sandbox". It is the document a TCC examiner, a security reviewer, or a technical interviewer will open first for the project's highest-risk component — and it will tell them the architecture has three diverging security boundaries and no timeout enforcement, when it has one boundary, an enforced timeout, an env-cleared child process, seccomp `execve` denial (ADR-046), and two container backends. **The ADR makes the system look materially worse and sloppier than it is.** That is the rare kind of documentation bug that costs you credibility for free.

### 4b. FINDING — `SECURITY.md` has the same defect, and it is the more public file

**Severity: HIGH**

**Where:** `SECURITY.md:25-41` (the `### Code execution` section).

Verbatim, still in the file today:

```
- `exec()` is used at three call sites, each with its own restricted global namespace…
  - `src/ai_etl/core/sandbox.py` — Transformer agent; `pandas`, `numpy`.
  - `src/ai_etl/agents/analyst.py` — Analyst/Gold agent; …
  - `src/ai_etl/agents/science.py` — Science agent; …
- These three whitelists are maintained independently and are **not** identical…
- Known gap: none of the three sites enforce an actual execution timeout —
  `timeout_seconds` parameters accepted by some callers are not applied.
```

An `**Update (ADR-007):**` paragraph was appended at `SECURITY.md:42` and correctly describes the unified sandbox, the `spawn` child, the enforced timeout and the `os.environ` clearing. But the stale body was left above it, so the file **contradicts itself**, and the wrong version is the one a reader hits first.

**Why it matters more than 4a:** `SECURITY.md` is the file GitHub surfaces in the repo's Security tab, and the repo has been **public since 2026-08-30**. Two of the three file paths it names do not exist. A reviewer who checks one of them concludes the security documentation was never maintained — and stops trusting the parts that are accurate. Cheapest possible fix, highest ratio of credibility recovered per line changed.

### 4c. FINDING — 7 ADRs still say "Proposed" for decisions that are live in production

**Severity: MEDIUM**

`grep`ing the `**Status:**` line of all 47 ADRs:

| ADR | Status line | Shipped? (verified) |
|---|---|---|
| ADR-014 multi-provider LLM | `Proposed` | **Yes.** `langchain-anthropic` / `-google-genai` / `-ollama` are base deps (`pyproject.toml:12-15`); `core/llm.py` (417 lines); extended by ADR-031 (*Accepted*). |
| ADR-016 scheduled pipelines data model | `Proposed (checkpoint — not merged, see PR)` | **Yes.** Migration `0006_saved_pipelines.py`; `core/scheduling.py`; `services/scheduler.py`. |
| ADR-017 run-pipeline linkage | `Proposed — schema-changing PR open, not merged` | **Yes.** Migration `0007_run_pipeline_linkage.py`. |
| ADR-018 drift detection + digest | `Proposed (checkpoint — not merged, see PR)` | **Yes.** Migration `0008_drift_threshold_pct.py`; `core/drift.py`; `services/digest`. |
| ADR-019 tenant budget cap | `Proposed (code complete, migration verified locally, not applied to production)` | **Yes.** Migration `0009_tenant_budget_cap.py`; `api/routers/budget.py`. |
| ADR-020 scheduled pipeline reliability | `Proposed (design decision — implementation follows in this same sprint)` | **Yes.** Migration `0010_scheduled_pipeline_health.py`; `audit/db/health.py`. |
| ADR-022 RBAC / org SSO / tenant secrets | `Proposed (code complete… migration 0011 … not applied to production)` | **Yes.** Migration `0011_tenant_secrets.py`; `api/routers/secrets.py`; `services/secrets_service.py`. |

**Why it matters:** an ADR log is a decision record — "Proposed" means *not yet decided*. Seven records covering the scheduler, the budget cap, RBAC, tenant secrets, drift detection and multi-provider LLM say "not yet decided" about subsystems that are shipped, migrated and running. A tech lead reading this log cannot tell which decisions are actually binding. Worse, ADR-022 has been *carefully* maintained in every other respect — two of its Consequences carry precise `**Superseded 2026-08-31 by ADR-044**` annotations (`ADR-022:186-196`) — while its own Status line was never touched. The maintenance discipline is there; it just never reaches the header.

**Note:** most of these say "not applied to production" as a *checkpoint-time* statement. If the intent is to preserve the historical snapshot, the fix is an `**Update (YYYY-MM-DD):** Accepted — migration NNNN applied` line, not a rewrite.

### 4d. Genuinely-open ADR items (correctly documented — no action, listed for completeness)

Cross-checked against code; these are real and honestly flagged:

| ADR | Open item | Verified state |
|---|---|---|
| **ADR-039** §Follow-up 1-2 | Vercel Sandbox never live-verified; not the production default | True. `core/sandbox.py:446` — default is `"process"`. All **7** `tests/integration/test_sandbox_vercel.py` tests self-skip for missing `VERCEL_TOKEN`/`VERCEL_TEAM_ID`/`VERCEL_PROJECT_ID` (`make-check.log:1218-1241`). Blocked by the Hobby-plan billing wall, not by engineering. |
| **ADR-038** §Follow-up 1-4 | Docker backend not production-reachable (no Docker-in-Docker on Railway) | True, `sandbox_docker.py:113` errors out explicitly if opted into without a daemon. |
| **ADR-022** / **ADR-044** / **ADR-045** | REST source's `_build_auth` tenant-secret wiring | ADR-045 exists and is *Accepted*; ADR-022's own note says "REST's `_build_auth` remains unwired, per ADR-044's own scope note" — worth a cross-check that ADR-045 closed it, since ADR-022 was not updated to say so. |
| **ADR-014** §4 / **ADR-031** §5 | Automatic cross-provider LLM fallback | Still out of scope; ADR-041 shipped a circuit breaker instead, which is the adjacent-but-different mechanism. Correctly distinguished in both ADRs. |
| **ADR-042** | The 3-item PITR follow-up | **Closed 2026-09-07** and documented in exemplary detail (`ADR-042:244-275`): PITR unavailable on Supabase Free, manual `pg_dump`→S3 stopgap built, **6 real failed attempts each named as a distinct root cause**, real `pg_restore` rehearsal performed. This is the single best-written section in the ADR set. |
| **ADR-032** Decision 4 | `exec()` introspection bypass accepted as unmitigated | Genuinely superseded by ADR-038/ADR-039/ADR-046 (seccomp `execve` denial, *"live-verified against a real Linux"*). ADR-032 has explicit revisit triggers. |

---

## Task 5 — CLAUDE.md non-negotiable rules

### 5a. In-place `PipelineState` mutation — **PASS**

`grep -rnE '^\s*state\[[^]]+\]\s*=' src/` returns exactly 2 hits, **neither of which is a `PipelineState` in a node**:

- `src/ai_etl/audit/db/runs.py:564` — `state` here is a local `dict[str, Any]` built at `:559` by `json.loads(storage.read_bytes(json_key))`. Mutating a freshly deserialized local dict.
- `src/ai_etl/api/serialization.py:62` — `state = dict(result.get("state") or {})` at `:59`. A defensive copy, mutated before return.

Also checked `state.update(...)` / `.setdefault(...)` / `.pop(...)`:

- `src/ai_etl/services/pipeline_service.py:206` — `final_state.update(partial)`, where `final_state: dict[str, Any] = dict(state)` at `:185`. Local accumulator over `graph.stream()` chunks. Correct.
- `src/ai_etl/core/llm.py:390,407,414` — `_circuit_state.setdefault(...)`, an unrelated module-level circuit-breaker dict (ADR-041). Different `state`.

**Zero violations.**

### 5b. `exec()` outside `core/sandbox.py` — **PASS**

Only `src/ai_etl/core/sandbox.py:209` (function mode) and `:245` (script mode). Every other grep hit is prose inside a comment or docstring (`sandbox_docker.py:5,248`, `sandbox_vercel.py:266`, `quality.py:16`, `audit/db/runs.py:324`).

`quality.py:16` deserves a positive callout — the custom-quality-rules feature explicitly documents *"Deliberately NOT an `eval()`/`exec()` of an arbitrary expression"*. The boundary was defended at exactly the point where it would have been easiest to break.

### 5c. SQL interpolation — **PASS (with one enforcement caveat, see 5f)**

7 f-string SQL sites, all identifier-only, all validated:

- `src/ai_etl/destinations/postgres_dest.py:35,70` — `text(f"SELECT COUNT(*) FROM {table}")`, preceded by `validate_table_name(table, allow_dots=True)` at `:31`.
- `src/ai_etl/destinations/mysql_dest.py:44,78` — same shape.
- `src/ai_etl/sources/postgres_source.py:35`, `sqlite_source.py:39`, `mysql_source.py:51` — `sql = query or f"SELECT * FROM {table}"`, table validated.

The defense is `src/ai_etl/core/sql_safety.py`, and it is a genuinely good piece of work:

- `validate_table_name()` uses `re.fullmatch`, **not** `re.match` with `$` — with a docstring (`:39-43`) explaining that `$` matches before a trailing `\n`, so `"users\n"` would slip a `re.match("^...$")` check. Correct, and correctly explained.
- `allow_dots` defaults to `False` (the restrictive option) *specifically* so a future call site that forgets the flag fails safe (`:20-22`).
- `validate_select_only_query()` (`:95-119`) blocks statement stacking, `--` and `/*` comment markers, and an 18-keyword denylist — added after a **real exploit**: `{"type": "sqlite", "query": "DROP TABLE users; --"}` was passed straight to `pd.read_sql` *and actually dropped the table* (`:57-65`).

No parameterizable value is ever interpolated. **Zero violations.**

### 5d. `sqlite3.connect()` without `contextlib.closing` — **PASS (rule is now vacuous)**

`grep -rn "sqlite3.connect(" src/` → **zero hits**. The SQLite audit store (ADR-004) has been fully migrated to SQLAlchemy. The `contextlib.closing` rule in `CLAUDE.md` now guards a pattern that no longer exists in the codebase — worth noting so a future reader does not assume raw `sqlite3` is still in play.

### 5e. `print()` in production code — **1 borderline site**

**Severity: LOW**

`src/ai_etl/__main__.py:29,35,38` — three `print()` calls in the `python -m ai_etl run` CLI entry point:

```python
print(f"[ai-etl] Starting pipeline run {run_id}")
print(f"[ai-etl] Pipeline {status}. Audit log: {json_path}")
print(f"[ai-etl] Error: {final_state['error']}")
```

**Judgement: acceptable, but it should be an explicit exemption rather than an accident.** `print()` to stdout *is* the correct interface for a CLI's user-facing output; routing it through `log_action()` (which requires a `PipelineState`) or the JSON logger would be wrong. The pipeline's own actions are already fully audited by the nodes underneath.

The reason to flag it: `pyproject.toml:171` sets `omit = ["*/__init__.py", "src/ai_etl/__main__.py"]` for coverage — so the CLI entry point is **excluded from the 95.11% figure and has no tests at all**. It is the one module in `src/` that is simultaneously unlinted-for-print, untested, and uncovered. It is 43 lines and low-risk, but the headline coverage number should be read as "95.11% of everything except the CLI".

Suggested: one line in `CLAUDE.md` exempting `__main__.py` from the no-`print()` rule, so the next audit does not re-litigate it.

### 5f. FINDING — bandit's B102 (`exec_used`) is disabled repo-wide

**Severity: MEDIUM**

**Where:** `pyproject.toml:161`

```toml
[tool.bandit]
skips = ["B101", "B102"]
exclude_dirs = ["tests", ".venv"]
```

**What:** B102 is bandit's `exec_used` check. It is skipped **globally**, not per-site. Combined with finding 3b (ruff's `S102` is not enabled either), the project's own #1 non-negotiable rule — *"`exec()` outside `src/ai_etl/core/sandbox.py`"* — has **no enforcement in either of the two linters that `make check` runs.** If someone adds an `exec()` to a new agent tomorrow, `make check` stays green.

This also explains the log line `Total potential issues skipped due to specifically being disabled: 30` — bandit reports "No issues identified" over 12,112 lines partly because 30 potential findings were pre-suppressed.

**Mitigating context (again real, and it caps this at Medium):** **Semgrep does catch it.** `.github/workflows/semgrep.yml` runs `p/python` + `p/security-audit` with `--error` on every PR and push to `main`, and `python.lang.security.audit.exec-detected` is precisely why `sandbox.py:209/245` carry `# nosemgrep` comments. So a stray `exec()` fails CI — just not `make check`, and not locally via `pre-commit`.

**Why it still matters:**
1. The **local** gate (`make check`, the pre-commit hooks) and the **CI** gate now enforce different rules. `CLAUDE.md` says "run `make check` before opening any PR" as if it were the complete gate; it is not.
2. `.github/workflows/codeql.yml` is **disabled** (triggers commented out at `:20-30`) because code scanning needed GitHub Advanced Security on what was then a private repo. **The repo went public on 2026-08-30** (per project memory) — CodeQL is free on public repos. This workflow can very likely be re-enabled with an uncomment, and nobody has revisited it since the repo's visibility changed. That is a free, high-value win sitting in a commented-out block.
3. B101 (`assert_used`) is a defensible skip. B102 is the one that specifically guards this project's single largest risk surface. Narrowing the skip to `["B101"]` and relying on the existing per-site `# nosec B102` comments would restore the local gate at zero cost.

---

## Task 6 — LangGraph architectural drift

**Severity: PASS — the contract is clean, with one documented-by-design exception**

### 6a. Node signatures — 5/5 correct

| Node | Signature | Line |
|---|---|---|
| `orchestrator_node` | `(state: PipelineState) -> PipelineState` | `agents/pipeline/orchestrator.py:59` |
| `extractor_node` | `(state: PipelineState) -> PipelineState` | `agents/pipeline/extractor.py:29` |
| `transformer_node` | `(state: PipelineState) -> PipelineState` | `agents/pipeline/transformer.py:153` |
| `quality_node` | `(state: PipelineState) -> PipelineState` | `agents/pipeline/quality.py:33` |
| `loader_node` | `(state: PipelineState) -> PipelineState` | `agents/pipeline/loader.py:59` |

### 6b. Immutable returns — every state-returning path uses `{**state, ...}`

Verified every `return` in `agents/pipeline/*.py`. Each one that returns a `PipelineState` spreads `**state`:
`extractor.py:89,116`; `loader.py:86,93,119,122`; `orchestrator.py:117,123`; `quality.py:84,130`; `transformer.py:216,236`.

The remaining `return {` statements are helper functions returning plain dicts — confirmed by reading: `quality.py:163` is `_check_duplicates() -> dict[str, Any]`, `extractor.py:135` is the schema-summary builder. Not state returns.

### 6c. Short-circuit on `state.get("error")` — 4/5, and the 5th is correct

| Node | Short-circuit |
|---|---|
| `extractor.py:35` | `if state.get("error"): return state` |
| `transformer.py:159` | ✅ |
| `quality.py:42` | ✅ |
| `loader.py:74` | ✅ |
| `orchestrator.py` | **absent** |

**Judgement: not a defect.** `graph.py:69` sets `orchestrator` as `set_entry_point`, and the topology is strictly linear with no back-edge into it (`graph.py:70-78`: orchestrator → extractor → transformer → quality → {loader, END}; loader → END). Nothing can hand the orchestrator a state that already carries an error. `pipeline_service.py`'s resume paths (`resume_pending_load:939`, `reject_pending_load:992`) re-enter at the loader stage, not through the graph's entry point.

**Worth one line of documentation, though.** `CLAUDE.md` states the rule as *"Short-circuit `if state.get("error"): return state` in every node"* with no exception, so every future reviewer (human or agent) will re-flag this and re-derive the same reasoning. A comment in `orchestrator_node`'s docstring — "entry node; no upstream error is reachable, see `graph.py:69`" — closes it permanently.

### 6d. FINDING — `log_action()` coverage is asymmetric: 4 of 9 agents write nothing to the audit trail

**Severity: MEDIUM**

**Evidence** (`grep -c log_action` per agent file):

| Agent | Layer | `log_action` calls |
|---|---|---|
| orchestrator | Silver / graph | 4 |
| extractor | Silver / graph | 4 |
| transformer | Silver / graph | 4 |
| quality | Silver / graph | 3 |
| loader | Silver / graph | 5 |
| planner | Agentic BI | 1 |
| **analyst** | Agentic BI | **0** |
| **science** | Agentic BI | **0** |
| **advisor** | Agentic BI | **0** |
| **reviewer** | Agentic BI | **0** |

**Root cause, and it is documented, not accidental** — `services/pipeline_service.py:269-289`, `_log_llm_override_if_used()`:

> "…for the agentic BI layer that runs outside the LangGraph/`PipelineState` (see this module's docstring) and therefore **can't call `audit/logger.py::log_action`** the way `agents/pipeline/*.py` nodes do. Standard logging is the established substitute this module already uses for that layer's other agent-level visibility."

`log_action(state, agent, action, payload)` takes a `PipelineState` as its first argument. The Agentic BI agents run outside the graph and have no state to thread, so they emit `logger.info` / `logger.warning` instead. `_record_stage_call()` (`:245-267`) captures their timings into a separate `stage_log`, and their *results* are persisted (`audit/db/runs.py` handles gold/science/advisor artifacts).

**Why it still matters:** `CLAUDE.md`'s non-negotiable rule reads *"Use `log_action()` … for every relevant action of **every agent**"*, and the project's thesis claim is that the agents *"generate auditable Python code end to end."* In practice the machine-readable `audit_log` array — the artifact that makes the audit claim demonstrable — covers **Silver only**. Gold, Science, Advisor and Reviewer activity lands in Python stdlib logs and separate artifacts instead.

The engineering is defensible; the **claim** is what is over-broad. For a TCC where auditability is a stated contribution, an examiner asking *"show me the audit trail for the Advisor's recommendation"* gets a materially weaker answer than for the Extractor. Two honest options, in order of cost:

- **Cheap:** amend `CLAUDE.md` and the architecture doc to state that `audit_log` is the Silver-layer trail and name what covers the BI layer (`stage_log` + stdlib logging + persisted artifacts). Removes the over-claim; costs one paragraph.
- **Real:** give the BI layer a state-free `log_analysis_action()` writing into the same audit store, so `audit_log` genuinely spans all 9 agents. Clean extension point; a sprint-sized piece of work, not a refactor.

---

## Task 7 — Dependency health

### 7a. FINDING — declared floors are 2-3 major versions behind what actually resolves

**Severity: MEDIUM**

| Declared in `pyproject.toml` | Actually resolved in `uv.lock` | Gap |
|---|---|---|
| `langchain>=0.3.0` | **1.3.10** | major 0.x → 1.x |
| `langgraph>=0.2.0` | **1.2.6** | major 0.x → 1.x |
| `langchain-openai>=0.3.0` | **1.3.2** | major |
| `langchain-anthropic>=0.3.0` | **1.6.0** | major |
| `openai>=1.50.0` | **2.43.0** | major |
| `numpy>=1.26.0` | **2.4.6 / 2.5.0** | major |
| `scikit-learn>=1.4.0` | 1.9.0 | minor |
| `cryptography>=44.0.0` | 50.0.0 | 6 majors |
| `boto3>=1.34.0` | 1.43.72 | minor |
| `redis>=5.0.0` | 8.1.0 | 3 majors |
| `celery>=5.4.0` | 5.6.3 | minor |
| `fastapi>=0.115.0` | 0.141.1 | minor |
| `sqlalchemy>=2.0.0` | 2.0.51 | patch |
| `pandas>=2.0.0,<4.0.0` | 2.3.3 | **the only upper bound in the file** |

**Why it matters:**

1. **The floors no longer describe the project.** `pandas` is the *only* dependency with an upper bound. Every LLM dependency — the ones with the fastest and most breaking release cadence in this stack — is an unbounded `>=` on a floor from the 0.x era. A fresh `uv lock --upgrade` (or any environment resolving without the lockfile, e.g. a `pip install ai-etl` consumer) can jump majors again with nothing to stop it. `uv.lock` protects the repo's own CI and deploys; it does not protect a rebuild-from-scratch or a downstream install.
2. **`CLAUDE.md:176` repeats the stale floors as the project's stack statement:**
   ```
   Python 3.11+ | langgraph>=0.2 | langchain>=0.3 | langchain-openai>=0.3 | openai>=1.50
   ```
   Anyone reading `CLAUDE.md` — human or agent — concludes the project runs LangGraph 0.2-era APIs. It runs **LangGraph 1.2.6 and LangChain 1.3.10**. For a portfolio piece, "we're on the 1.x line of LangGraph" is a *better* story than what the doc currently tells, and it is the true one. This is unforced.
3. **No vulnerability exposure today.** `pip-audit`: `No known vulnerabilities found`. This is a maintainability/reproducibility finding, not a security one.

**Suggested direction (not applied):** raise the floors to match what has actually been tested (`langchain>=1.3`, `langgraph>=1.2`, `openai>=2.0`, `numpy>=2.0`), add `<next-major` caps on at least the LLM stack, and sync `CLAUDE.md:176`.

### 7b. `# type: ignore` for missing stubs — 4 of 13 are potentially resolvable

**Severity: LOW**

- **`untyped-decorator` on `@celery_app.task` × 3** (`execution_queue.py:328`, `retention_service.py:203`, `scheduler.py:59`) — "celery has no type stubs for `@task`". A `celery-types` stub package exists on PyPI; the project already accepts stub packages (`pandas-stubs>=2.0.0,<4.0.0` is in `dev`). Worth a spike to see whether it removes all three cleanly on `celery 5.6.3`. If it does not, the ignores stay and the comment becomes verifiably true rather than assumed.
- **`no-any-return` on `fig.to_plotly_json()`** (`api/serialization.py:43`) — "plotly has no return-type stub here". Same evaluation applies to `plotly-stubs`.

The 5 `call-overload` ignores in `graph.py:63-67` are **not** fixable this way — they are a genuine mypy TypeVar-resolution artifact of the `NodeFn` wrapper alias, correctly diagnosed in the 4-line comment above them.

### 7c. `ruff` targets py311, `mypy` targets py312

**Severity: LOW**

`pyproject.toml`: `[tool.mypy] python_version = "3.12"` vs `[tool.ruff] target-version = "py311"` vs `[project] requires-python = ">=3.11"`.

mypy type-checks against 3.12 stdlib semantics while 3.11 is a supported runtime, so a 3.12-only stdlib API would pass `make type-check` and fail at runtime on 3.11. The project has already been bitten by this class of gap in the other direction — `pyproject.toml:53-58` documents `typing_extensions` being added because *"`typing.TypedDict` fails Pydantic's schema generation on Python < 3.12 … CI's Python 3.11 job caught this for real."* The CI matrix is the real safety net here, which is why this is Low. Setting `python_version = "3.11"` would move the catch left, to the local gate.

### 7d. Alembic revision `0013` is absent — chain verified intact

**Severity: LOW (informational)**

`alembic/versions/` runs `0001…0012`, then `0014…0022`. **Not a lost migration:** `0014_tenant_deletion_log.py:28` has `down_revision = "0012"`, and `0012_pipeline_quality_rules.py:20` has `down_revision = "0011"` — the chain is continuous. `docs/CURRENT_STATE.md:1311` explains it (*"Sprints 16/18 hold `0012`/`0013`, not yet merged"*) — `0013` was reserved by a branch that never landed, and the number was burned.

Flagged only because "a missing migration number" is exactly the kind of thing that costs a new engineer half an hour of alarm. A one-line comment in `0014`'s header would pre-empt it.

---

## Secondary: frontend tech debt

### 8a. FINDING — the same `eslint-disable` repeated across 8 components is a missing abstraction

**Severity: MEDIUM (low urgency, clear direction)**

10 `eslint-disable` directives in `frontend/src`, zero `@ts-ignore`, zero `@ts-nocheck`, zero TODOs:

| File:line | Rule |
|---|---|
| `frontend/src/components/pipeline-history.tsx:76` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/secrets-manager.tsx:69` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/approval-queue.tsx:73` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/pipelines-manager.tsx:122` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/admin-manager.tsx:77` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/budget-manager.tsx:66` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/data-export-manager.tsx:82` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/executive-summary.tsx:145` | `react-hooks/set-state-in-effect` |
| `frontend/src/components/admin-manager.tsx:79` | `react-hooks/exhaustive-deps` |
| `frontend/src/components/admin-manager.tsx:108` | `react-hooks/exhaustive-deps` |

**Every one carries a justification**, and the comments diagnose their own root cause. `pipeline-history.tsx:71-76`:

> "Intentional synchronous loading/error reset before a cancellable fetch, so a `pipelineId` change immediately shows the loading state instead of stale history for one frame; same established pattern as `executive-summary.tsx` and `pipelines-manager.tsx`. **No data-fetching library (SWR/React Query) in this codebase to delegate this to.**"

**Why it matters:** eight components hand-rolling `setLoading(true)` → `authedFetch` → `cancelled` flag → `setState` is ~8 copies of the same 20-line effect, each an independent opportunity for a race or a missed cleanup. The two `exhaustive-deps` disables in `admin-manager.tsx` are the sharper edge — that rule exists to catch stale closures, and suppressing it on a mount-only effect is conventional but load-bearing on the assumption that `loadAuditLog` never needs to change.

**Direction:** one `useAsyncData(fetcher, deps)` hook (or adopting SWR / TanStack Query, which the comment itself names) collapses all 8 into one reviewed implementation and deletes 8 suppressions. This is textbook "the third copy is where you extract" — the codebase is at eight.

### 8b. Largest modules — watch, don't act

| File | LOC |
|---|---|
| `src/ai_etl/services/pipeline_service.py` | **1031** |
| `frontend/src/components/pipelines-manager.tsx` | **689** |
| `src/ai_etl/audit/db/runs.py` | 660 |
| `src/ai_etl/services/execution_queue.py` | 651 |
| `src/ai_etl/core/sandbox.py` | 611 |
| `src/ai_etl/audit/db/pipelines.py` | 611 |

**Severity: LOW**

`pipeline_service.py` is the orchestration seam between the LangGraph Silver pipeline and the out-of-graph Agentic BI layer, and it holds 17 top-level functions covering silver / gold / science / advisor / repair / approval-resume. It is cohesive rather than a grab-bag, and the split is legible (`run_silver_pipeline`, `run_gold_analysis`, `run_science_analysis`, `_run_with_repair`, `run_analysis_tasks`, `run_advisor_analysis`, `run_full_analysis`, `resume_pending_load`, `reject_pending_load`). **No action recommended now.** The natural seam if it keeps growing is `_run_with_repair` + `run_gold_with_repair` + `run_science_with_repair` + `run_analysis_tasks` → an `analysis_runner` module, leaving `pipeline_service` as the top-level composition.

`pipelines-manager.tsx` at 689 lines is the one to watch on the frontend — nearly double the next-largest component.

---

## Summary — findings by severity

| # | Severity | Finding | Evidence |
|---|---|---|---|
| 4b | **HIGH** | `SECURITY.md:25-41` describes 3 `exec()` sites and 2 file paths that do not exist; contradicted by its own `:42` update note. Public repo since 2026-08-30. | `SECURITY.md:25-41` vs `core/sandbox.py:209` |
| 4a | **HIGH** | `ADR-003` is factually wrong on 6 counts (3 sites, dead paths, "Follow-up open" closed by ADR-007, "timeout not enforced" enforced at `sandbox.py:556`); known stale since Sprint 2, still open at Sprint 38+. | `ADR-003` vs `ADR-007:120`, `CURRENT_STATE.md:2122` |
| 3b | **MEDIUM** | 19 dead `# noqa` in `src/` — `BLE001`/`S102`/`PLR2004`/`UP024` are not in ruff's `select`. `make lint` is E/F/I/N/W only. | `ruff --select RUF100 src/`; `pyproject.toml:148` |
| 5f | **MEDIUM** | bandit `B102` (`exec_used`) skipped repo-wide → the "`exec()` only in sandbox" rule has zero enforcement in `make check`. Semgrep covers it in CI; CodeQL is disabled and the repo is now public. | `pyproject.toml:161`; `semgrep.yml:44`; `codeql.yml:20-30` |
| 4c | **MEDIUM** | 7 ADRs (014, 016, 017, 018, 019, 020, 022) still say `Proposed` for subsystems shipped with migrations `0006`-`0011`. | ADR headers vs `alembic/versions/` |
| 6d | **MEDIUM** | Analyst / Science / Advisor / Reviewer emit **0** `log_action()` calls — the `audit_log` covers Silver only, while docs claim end-to-end auditability. | `grep -c log_action`; `pipeline_service.py:269-289` |
| 7a | **MEDIUM** | Declared floors 2-3 majors behind reality (`langgraph>=0.2` → 1.2.6; `openai>=1.50` → 2.43.0); only `pandas` has an upper bound; `CLAUDE.md:176` repeats the stale floors. | `pyproject.toml` vs `uv.lock` |
| 8a | **MEDIUM** | Same `react-hooks/set-state-in-effect` disable in 8 components; the comments name the missing abstraction themselves. | 8 files listed in §8a |
| 1 | LOW | The "1 warning" is third-party: Starlette deprecating `httpx` in favour of `httpx2`. | `make-check.log:1252` |
| 3c | LOW | ~34 justified blind-excepts; `execution_queue.py:409` is the only one with no reason text. | `execution_queue.py:409` |
| 5e | LOW | 3 `print()` in `__main__.py` — defensible for a CLI, but the module is also excluded from coverage (`pyproject.toml:171`) and untested. | `__main__.py:29,35,38` |
| 7b | LOW | 4 of 13 `# type: ignore` may be removable via `celery-types` / `plotly-stubs`; `pandas-stubs` precedent exists. | `pyproject.toml` `dev` extra |
| 7c | LOW | mypy targets py312, ruff targets py311, runtime supports 3.11 — CI matrix is the only guard. | `pyproject.toml` |
| 7d | LOW | Alembic `0013` absent; chain verified intact via `0014.down_revision = "0012"`. | `alembic/versions/0014:28` |
| 6c | LOW | `orchestrator_node` has no `state.get("error")` short-circuit — correct (entry node, linear graph) but contradicts `CLAUDE.md`'s "every node". | `orchestrator.py:59`; `graph.py:69-78` |
| 5d | LOW | The `sqlite3.connect()` + `contextlib.closing` rule now guards a pattern with zero occurrences in `src/`. | `grep sqlite3.connect src/` → 0 |
| 8b | LOW | `pipeline_service.py` at 1031 LOC and `pipelines-manager.tsx` at 689 LOC — cohesive, watch only. | `wc -l` |

**PASS (verified, no action):** LangGraph node signatures 5/5 · `{**state, ...}` immutability 100% · error short-circuits 4/4 where reachable · `exec()` confined to `core/sandbox.py` · SQL identifier allowlist via `core/sql_safety.py` · zero `TODO`/`FIXME`/`HACK` in `src/` and `frontend/src/` · 13/13 `# type: ignore` justified · zero in-place `PipelineState` mutation · pip-audit clean · bandit clean.

---

## Closing assessment

**Would I defend this in front of a tech lead? Yes — with two doc fixes made first.**

The code itself is stronger than most production codebases I would expect at this stage: an architectural contract with zero exceptions across 5 nodes, a SQL-safety module whose docstrings explain *why* `re.fullmatch` over `re.match` and cite the real exploit that motivated it, 95.11% coverage, 47 ADRs with a genuine "flag every deferral" convention, and a DR section (`ADR-042:244-275`) that documents six consecutive failed backup attempts by root cause instead of quietly reporting the seventh success.

The gap is not quality — it is that **the documentation understates the code**. `SECURITY.md` and `ADR-003` describe a three-headed sandbox with no timeout enforcement that was unified 36 sprints ago; `CLAUDE.md` advertises a LangGraph 0.2-era stack that is actually on 1.2.6; seven ADRs mark shipped subsystems as undecided. Every one of these makes the project look *less* rigorous than it is, and every one is a text edit.

The one finding with real engineering substance behind it is **3b/5f combined**: `make lint` and `make check` are narrower gates than the repo's own conventions imply, and 19 `# noqa` comments actively advertise protection that is not there. CI (Semgrep) covers the security-critical case, so this is a credibility and drift problem rather than an exposure — but "the local gate and the CI gate enforce different rules, and `CLAUDE.md` says to trust the local one" is exactly the kind of thing a tech lead notices in the first hour.

Fix the two docs and widen `select`, and there is nothing here I would hold a merge over.
