# ADR-039: Vercel Sandbox as the Production Sandbox Isolation Backend — Resolves ADR-038's Deferred Production Rollout

**Status:** Accepted — code complete; **not yet dev/live-verified against a
real Vercel Sandbox** (no `VERCEL_TOKEN`/`VERCEL_TEAM_ID`/`VERCEL_PROJECT_ID`
or `vercel` Python package available in the environment this ADR was
written in — see "Verification status" below). Not yet the production
default (`"process"` still is).
**Date:** 2026-08-27
**Deciders:** Bruno Ribeiro (project owner decision; this ADR documents and
executes it, it does not re-litigate whether to do it)

## Context

ADR-038 added a real, kernel-container-isolated sandbox backend
(`core/sandbox_docker.py`) that closes the introspection-bypass gap ADR-032
Decision 4 accepted as unmitigated risk. It explicitly could not ship that
backend to production: this project's Railway deployment runs in a
non-privileged container, and Docker-in-Docker (running `docker run` from
inside an already-containerized service) does not work there. ADR-038's own
"Follow-up work" item 1 named the fix: "a separate execution service that
*does* support on-demand container isolation ... reachable from the
existing FastAPI/Celery services over a narrow internal API."

**The project owner has now picked that service: Vercel Sandbox**
(Firecracker microVMs, reached over HTTPS via the `vercel` Python package),
in preference to standing up a self-hosted Docker-capable VPS. Rationale:

1. **Zero new vendor to manage.** The frontend already deploys to Vercel.
   Adding a VPS (or Fly.io/Fargate/Cloud Run, per ADR-038's own alternatives
   list) would be a *new* infrastructure provider this solo-maintained,
   pre-launch project would have to operate, monitor, and pay for — a real,
   explicit cost this session weighed alongside the technical trade-offs,
   not an afterthought.
2. **Billing model fits the actual workload.** Vercel Sandbox bills
   Active-CPU-hours only — time the sandboxed code is actually computing,
   excluding time spent waiting on I/O/network. This project's sandbox
   calls are short LLM-code-execution bursts (Transformer ~30s budget,
   Analyst/Science ~15–20s budget per sub-task — see "Cost estimate"
   below), not long-running compute. A VPS bills flat monthly regardless of
   utilization; E2B/Modal-style sandboxes bill wall-clock time, which would
   include any I/O wait inside the sandboxed script (there normally is
   none, since sandboxed code doesn't do network I/O by design, but the
   *sandbox creation/file-transfer* overhead around each call is real dead
   time that Active-CPU billing does not charge for and wall-clock billing
   would).
3. **The Hobby (free) plan's 5 included Active-CPU-hours/month likely
   covers this project's current pre-launch traffic for a long time at
   zero cost** — see "Cost estimate" below.

## Decision

Add a third, opt-in isolation backend to `core/sandbox.py`'s
`execute_in_sandbox()`, selected via `backend="vercel"` or
`AI_ETL_SANDBOX_BACKEND=vercel` (`"process"` remains the default;
`"docker"` is unchanged, dev/local-only per ADR-038):

- **`core/sandbox_vercel.py`** — `execute_in_vercel_sandbox()`, using the
  **synchronous** `vercel.sandbox.sync` API (`from vercel.sandbox import
  sync as sandbox`). Every existing caller of `execute_in_sandbox()`
  (Transformer, Analyst, Science) is synchronous code called from Celery
  tasks, not an async FastAPI request handler — the Python SDK reference
  explicitly warns against calling its sync API from inside an active async
  event loop, so this backend deliberately does not introduce async/await
  into this call path.
- One sandbox per call, created and torn down via
  `sandbox.create_sandbox(...)` as a context manager (`with ... as box:`) —
  auto-stops and destroys on exit, matching the "process"/"docker"
  backends' one-shot-per-call lifecycle. `persistent=False` — this is an
  ephemeral execution, not a dev environment; no snapshotting.
- **`network_policy=NetworkPolicy.deny_all()`** — no outbound network
  reachable from sandboxed code at all, the same guarantee as the Docker
  backend's `--network none`.
- **No host env vars forwarded.** `create_sandbox()`'s `env=` kwarg is
  simply never passed — the runner script needs nothing from it (the
  payload arrives via a file, not an env var), so there is no allowlist to
  maintain and no host secret (`OPENAI_API_KEY`, `POSTGRES_URL`,
  `AI_ETL_SECRETS_ENCRYPTION_KEY`, ...) that could leak this way even in
  principle. Stronger than the "process" backend's `os.environ.clear()`
  discipline (nothing to clear here — nothing was ever passed in), matching
  the Docker backend's equivalent guarantee (no `-e`/`--env-file`).
- **Payload transport via files, not stdin.** The Vercel Sandbox Python SDK
  does not support piping data to a process's stdin ("Process standard
  input isn't supported," per the SDK reference) — unlike the Docker
  backend, which pipes the pickled request over the container's stdin.
  Instead: the request is pickled and written to a file inside the sandbox
  (`box.fs.write_bytes(input_path, payload)`), the driver script is run with
  that path as an argument, and the pickled result is read back from a
  second file (`box.fs.read_bytes(output_path)`).
- **`docker/sandbox/run_sandboxed_vercel.py`** (new) — the file-based
  counterpart to the Docker backend's `run_sandboxed.py`. Reads the request
  pickle from `sys.argv[1]`, calls `ai_etl.core.sandbox._execute_code()` —
  **the same shared function** the "process" backend's
  `_sandbox_worker()` and the Docker backend's `run_sandboxed.py` both call
  — and writes the response pickle to `sys.argv[2]`. No new copy of the
  restricted-globals/mode-dispatch logic exists anywhere in this project;
  this ADR adds a third caller of the one function that logic lives in.
- **Timeout enforcement:** `execution_time_limit` on the sandbox itself
  (`timeout_seconds` plus a fixed slack, so sandbox creation/file-transfer
  overhead is never counted against the caller's budget) bounds the whole
  session; `kill_after=timeout_seconds` on `run_process()` is the
  server-side SIGKILL deadline for the actual execution — the tighter,
  caller-facing bound, mirroring how the Docker backend uses both
  `subprocess.Popen(...).communicate(timeout=...)` *and* `docker kill` (the
  thing actually doing the work must be stopped, not just the local caller
  giving up on waiting for it).
- **`VercelSandboxUnavailableError`** — raised, never silently downgraded to
  a weaker backend, in two cases: (1) the `vercel` package isn't installed
  (it's an opt-in extra, `pyproject.toml`'s new `vercel-sandbox` group, not
  a base dependency — every other backend must keep working without it);
  (2) `VERCEL_TOKEN`/`VERCEL_TEAM_ID`/`VERCEL_PROJECT_ID` aren't all set —
  this project runs on Railway, never on a Vercel deployment, so the SDK's
  automatic OIDC auth path never applies here, and there is no fallback
  auth to try. A `SandboxApiError`/`SandboxCredentialsError`/
  `SandboxTerminalStateError`/`SandboxTimeoutError`/`SandboxError` raised by
  the SDK itself (bad token, image not found/not ready, quota, transient
  API outage) is also re-raised as `VercelSandboxUnavailableError` — a
  backend-availability failure, not a sandboxed-code error, so it must not
  be silently absorbed into an ordinary `SandboxResult` the way a script's
  own `SyntaxError`/runtime exception is.

## Image strategy — investigated before deciding

Checked `https://vercel.com/docs/sandbox/concepts/images` directly (not
assumed): the default `vercel/sandbox/universal:latest` managed image ships
Node.js LTS and **Python 3.14**, plus general coding-agent tooling — but
**not** pandas/numpy/plotly/scikit-learn/statsmodels. Every real
Transformer/Analyst/Science call needs at least pandas/numpy, and
Analyst/Science additionally need plotly/scikit-learn/statsmodels
unconditionally (`pyproject.toml` already lists them as base dependencies
for exactly this reason, per ADR-038's own image notes).

Two options considered:

1. **`pip install` the dependencies on every ephemeral sandbox before
   running the actual payload.** Rejected: installing pandas + numpy +
   plotly + scikit-learn + statsmodels fresh (no wheel cache across
   ephemeral, non-persistent sandboxes) would add tens of seconds of
   latency to *every single* Transformer/Analyst/Science call — for a
   30-second Transformer budget or a 15–20 second Analyst/Science budget,
   that overhead alone could exceed the entire budget before the actual
   generated code even starts running. This also directly fights the
   Active-CPU billing model this ADR was chosen for: `pip install`'s CPU
   time compiling/unpacking wheels is itself billed compute, spent on
   every call instead of once at image-build time.
2. **A custom image via Vercel Container Registry (VCR)** — chosen. VCR
   images are pulled by `Sandbox.create(image=...)` the same way
   `docker/sandbox/Dockerfile` already builds an image with the real
   `ai_etl` package (and therefore pandas/numpy/plotly/scikit-learn/
   statsmodels, all base dependencies) installed for the Docker backend.
   **The same Dockerfile now serves both backends** — this PR adds
   `run_sandboxed_vercel.py` alongside the existing `run_sandboxed.py` in
   the same image (see the Dockerfile's updated header comment), so one
   `docker build` produces an image usable both as `docker run`'s
   container (Docker backend, ENTRYPOINT = `run_sandboxed.py`) and as a
   VCR-pushed Vercel Sandbox image (Vercel backend, `run_sandboxed_vercel.py`
   invoked explicitly via `run_process()` — **Vercel Sandbox does not run a
   custom image's Docker `ENTRYPOINT`/`CMD` at all**, confirmed from the
   docs, which is exactly why the Vercel path needs its own driver script
   invoked by name rather than reusing the Docker backend's ENTRYPOINT).

Pushing the image (`vercel vcr build docker . ai-etl-sandbox:latest
--push`) requires the Vercel CLI authenticated and this repo linked to a
Vercel project (`vercel login` + `vercel link`) — **not automated in CI**,
since no live Vercel project exists in that environment; a `make
sandbox-vcr-image` target documents the manual/deploy-time command (see
Makefile). This mirrors ADR-038's own `make sandbox-image` local-build
precedent and its explicit acknowledgment that CI does not build the
Docker sandbox image either.

## Required environment variables (new)

Documented in `.env.example`, all three required together for this backend
(no partial-credential fallback):

- `VERCEL_TOKEN` — a personal or team Vercel access token.
- `VERCEL_TEAM_ID` — the team owning the sandbox image/project.
- `VERCEL_PROJECT_ID` — the project the pushed `ai-etl-sandbox:latest` VCR
  image lives under.

## Cost estimate (order-of-magnitude, explicitly an estimate)

Vercel Sandbox (Pro/Hobby) bills **Active-CPU-hours** — compute time only,
excluding I/O/network wait. Sandboxed code here does no network I/O by
design (`NetworkPolicy.deny_all()`), so a sandbox call's Active-CPU time is
close to its full wall-clock duration once the process actually starts
(the only excluded time is sandbox creation/file-transfer latency around
it, not billed as this project's compute).

**Per-call budgets** (this project's own configured timeouts, not
measured Vercel invoice data — no real Vercel Sandbox calls have been made
yet):

- Transformer: 30s default budget, ×2 (`scale_timeout_for_rows()`,
  `LARGE_DATASET_TIMEOUT_MULTIPLIER`) for inputs over the 50k-row
  threshold → worst case 60s.
- Analyst/Science: 15–20s default budget per sub-analysis, same ×2 scaling
  → worst case 30–40s per sub-task. A Planner-decomposed question
  typically yields a handful of sub-analyses (observed range in this
  project's case study runs: roughly 3–6), not just one.

**A single realistic case-study pipeline run** (Silver ETL + one
Planner-driven analysis, per `run_full_analysis`): 1 Transformer call
(~30s) + ~4 Analyst/Science sub-analysis calls (~20s each, generously
assuming some hit the ×2 large-dataset multiplier) ≈ 30s + 4×30s = **150s
≈ 0.042 Active-CPU-hours** at 1 vCPU. Real code rarely runs the entire
budget (these are *timeout ceilings*, not typical durations) — actual
compute per call is very likely well under half the budget in the common
case, so treat this as a conservative upper bound, not a typical-case
number.

**Scaling to a month of pre-launch usage:** even at a generous 200
full pipeline+analysis runs in a month (this project has no real external
users yet — this is a TCC capstone project pre-launch, so real traffic is
the owner's own testing plus, at most, a handful of case-study
evaluation runs), that's roughly 200 × 0.042 ≈ **8.4 Active-CPU-hours**,
which is *above* the Hobby free tier's 5 hours/month — but each of those
"runs" bundles multiple sandbox calls already counted at their full
timeout ceiling, a deliberately pessimistic assumption. At half that
per-call utilization (a more realistic assumption for code that isn't
pathologically slow), the same 200 runs land at **~4.2 Active-CPU-hours**,
comfortably inside the free tier. **Conclusion, labeled as an estimate:**
the Hobby plan's 5 free Active-CPU-hours/month is very likely sufficient
for this project's actual current pre-launch traffic (the owner's own
testing, not hundreds of independent-user runs), with a plausible but not
certain chance of needing the Pro plan's included credit if usage is
heavier than assumed here — worth checking actual Vercel usage dashboards
after this backend sees real traffic rather than trusting this estimate
indefinitely.

## Verification status — read before trusting the security claims below

**No real Vercel Sandbox calls have been made against this implementation.**
This development environment has no `VERCEL_TOKEN`/`VERCEL_TEAM_ID`/
`VERCEL_PROJECT_ID` set and does not have the `vercel` Python package
installed (`pip show vercel` reports not found). Per this task's own
instructions, credentials were checked for, not requested from the user.

Consequently:

- `tests/unit/test_sandbox_vercel_dispatch.py` — run and passing. Covers
  backend-selection dispatch and fail-closed behavior via monkeypatching;
  no real Vercel calls.
- `tests/integration/test_sandbox_vercel.py` — written, mirroring
  `test_sandbox_docker.py`'s contract-parity and introspection-bypass
  containment tests (including the same `().__class__.__mro__[1]
  .__subclasses__()` gadget), but **self-skips** (`pytest.mark.skipif`)
  because the `vercel` package/credentials/pushed image aren't available
  here. **These tests have never actually run against a real Vercel
  Sandbox** — the containment claims in this ADR (no network reachable, no
  host secrets leaked) are the same design intent as ADR-038's verified
  Docker backend, extrapolated to Vercel's documented `NetworkPolicy.deny_all()`
  and "no env forwarded" behavior, but are **asserted from the SDK's
  documentation, not independently demonstrated** the way ADR-038's Docker
  backend was. Running these tests for real (after `make sandbox-vcr-image`
  and setting the three credential env vars) is the single most important
  remaining step before treating this backend as production-ready, not
  just production-capable.
- `make check` (lint/format-check/type-check/test/security) — run without
  live credentials; the skipped integration test does not affect this.

## Consequences

- **Positive:** resolves ADR-038's explicitly deferred production-rollout
  follow-up item without adding a new infrastructure vendor beyond Vercel,
  which this project already depends on for its frontend.
- **Positive:** `execute_in_sandbox()`'s signature and `SandboxResult` shape
  are unchanged for every existing caller — purely additive, opt-in via one
  env var, same as ADR-038's `"docker"` backend.
- **Positive:** the restricted-globals/mode-dispatch logic (`_execute_code()`)
  now has a third caller, still defined in exactly one place.
- **Negative — accepted, explicit:** not yet verified against a real Vercel
  Sandbox (see "Verification status" above) — this is a real gap, not a
  formality, and is the top follow-up item.
- **Negative — accepted, explicit:** the cost estimate above is a reasoned
  order-of-magnitude estimate, not measured invoice data — real Vercel usage
  should be checked after this backend sees production traffic.
- **Negative — accepted, explicit:** `make sandbox-vcr-image` cannot run in
  CI (no live Vercel project there) — pushing a new image after a
  `core/sandbox.py`/`_execute_code()` change is a manual step someone must
  remember to run before flipping `AI_ETL_SANDBOX_BACKEND=vercel` in
  production, the same operational burden ADR-038 already accepted for
  `make sandbox-image`.

## Follow-up work (explicit, not silently dropped)

1. **Run the skipped integration tests for real** — get `VERCEL_TOKEN`/
   `VERCEL_TEAM_ID`/`VERCEL_PROJECT_ID`, run `make sandbox-vcr-image`, and
   confirm `tests/integration/test_sandbox_vercel.py` actually passes,
   including the introspection-bypass containment tests — this is what
   turns this ADR's security claims from "extrapolated from documentation"
   into "demonstrated," matching ADR-038's own bar for the Docker backend.
2. Flip `AI_ETL_SANDBOX_BACKEND=vercel` in Railway's production environment
   once (1) is done, and monitor real Active-CPU-hour usage against the
   estimate above.
3. Consider a Vercel Sandbox **snapshot** (not to be confused with a
   sandbox filesystem snapshot's persistence use case — a build-time
   pre-warmed image) if cold-start latency from the custom VCR image proves
   material once real timings are available; not evaluated here since no
   real calls have been timed yet.
4. Re-derive the `_SESSION_LIMIT_SLACK_SECONDS`/`kill_after` split against
   real observed sandbox-creation latency once real credentials are
   available, the same way ADR-012 re-derived the "process" backend's
   timeouts against real profiling.

## Related

- [ADR-038](ADR-038-docker-sandbox-migration.md) — the Docker-isolated
  backend this ADR's production rollout resolves; `_execute_code()`'s
  extraction (from that ADR) is what this ADR's Vercel driver script reuses
  rather than forking a third copy.
- [ADR-032](ADR-032-security-posture-admin-role-sast.md) — Decision 4, the
  original risk acceptance ADR-038 superseded and this ADR extends to a
  production-reachable backend.
- `core/sandbox.py`, `core/sandbox_vercel.py`,
  `docker/sandbox/Dockerfile`, `docker/sandbox/run_sandboxed_vercel.py` —
  the implementation.
- `tests/unit/test_sandbox_vercel_dispatch.py`,
  `tests/integration/test_sandbox_vercel.py` — dispatch and (currently
  self-skipping) containment tests.
