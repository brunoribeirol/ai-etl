# ADR-038: Docker-Isolated Sandbox Backend — Supersedes ADR-032 Decision 4

**Status:** Accepted — code complete and dev/local-verified; **production
(Railway) rollout explicitly deferred**, see "Railway feasibility" below.
**Date:** 2026-08-26
**Deciders:** Bruno Ribeiro (project owner decision; this ADR documents and
executes it, it does not re-litigate whether to do it)

## Context

ADR-032 Decision 4 (2026-08-22) accepted, as a conscious and re-confirmed
risk, that `core/sandbox.py`'s `exec()`-with-restricted-globals boundary is
bypassable via introspection
(`().__class__.__mro__[1].__subclasses__()` reaching an already-imported
module's `__globals__`, e.g. `os`) — arbitrary code execution *inside* the
already-isolated `multiprocessing.Process` child ADR-007 introduced, with no
OS-level containment (no network restriction, no filesystem restriction
beyond the child's own OS-user permissions, no enforced CPU/memory ceiling
beyond wall-clock timeout). That ADR's stated trigger for revisiting it was
"opening the product to self-serve, unvetted external signups." **The
project owner has now decided to migrate to real Docker isolation ahead of
that trigger**, independent of it — this ADR formalizes and executes that
decision.

**What does not change:** `execute_in_sandbox()`'s public contract. Every
existing caller (`agents/pipeline/transformer.py`, `agents/analysis/analyst.py`,
`agents/analysis/science.py`) keeps calling it exactly as today, with the
same `SandboxResult` return shape (`values`, `error`, `timed_out`,
`duration_seconds`). No caller needed to change for this migration.

## Railway feasibility — investigated first, before writing implementation code

This project deploys to Railway (`railway.json`, `DOCKERFILE` builder,
top-level `Dockerfile` — one image serves both the FastAPI web service and
the Celery worker). Running a real Docker sandbox in production means
`docker run` (or the Docker SDK, which itself talks to a daemon) from
*inside* that already-containerized Railway service — i.e. Docker-in-Docker.

**Finding: Railway does not support this.** Railway's own documentation
states it directly, in the Docker-in-Docker context specifically:

> "Because Railway containers are non-privileged, GitHub Workflows that
> build-and-then-mount containers on the same host (i.e. Docker-in-Docker)
> will fail."
> — <https://docs.railway.com/guides/github-actions-runners#best-practices>

Corroborated by two independent Railway docs pages describing the same
underlying constraint in different contexts: "Railway containers don't allow
privilege escalation" (`guides/bridge-railway-to-rds-with-tailscale`) and the
project-lockdown guide's framing of Railway's role model, which has no
concept of a privileged-container escape hatch at all
(`guides/lock-down-production-project`). Nothing in Railway's docs describes
an opt-in privileged mode, a Docker-socket-mount pattern, or a supported
DinD path for application services (as opposed to their own CI runners
product, which is the one surface the quoted line is about, and which
*also* fails for the same reason). This is a platform-level constraint, not
a configuration gap this project could work around with a Railway setting.

**Consequence for scope:** `docker run` (or equivalent) cannot run inside
the Railway-deployed API/worker container today. Shipping this migration as
"flip a flag in production" would silently not work — the sandboxed code
path would hit a missing Docker daemon on every call. This ADR does **not**
do that. Instead:

- The Docker backend is implemented, tested, and **dev/local-verified**
  (`make sandbox-image` + a local Docker daemon — confirmed working via
  `docker version`/`docker info` in this project's own dev environment).
- The `"process"` backend (`multiprocessing.Process`, unchanged) **remains
  the default in every deployed environment**, including Railway, until a
  separate follow-up lands.
- **Explicit follow-up, not silently deferred:** production Docker isolation
  on Railway requires one of — (a) a **separate, dedicated execution
  service** (its own Railway service, or a different provider entirely, that
  *does* support running Docker containers on demand — e.g. a small
  Fly.io/AWS Fargate/GCP Cloud Run job, or a bare-metal/VM-backed worker with
  a real Docker daemon — called over a narrow internal API from the existing
  FastAPI/Celery services instead of shelling out to a local `docker run`);
  or (b) evaluating whether Railway's roadmap adds privileged-container or
  Firecracker/gVisor-style support later. Neither is scoped or implemented
  in this PR — sizing that follow-up is real infrastructure work in its own
  right (a new deployable, its own auth/network boundary to the existing
  services, its own cost), matching this ADR's own instruction not to rush
  an untested production path into this security boundary.

This mirrors ADR-032 Decision 4's own alternatives analysis, which already
flagged "Railway's current setup was not verified to support this without
infrastructure changes" — this ADR is the verification, and the answer is:
it does not, without a separate service.

## Decision

Add a second, opt-in isolation backend to `core/sandbox.py`'s
`execute_in_sandbox()`, selected via the `backend` argument or the
`AI_ETL_SANDBOX_BACKEND` environment variable (`"process"` default,
`"docker"` opt-in):

- **`"process"`** (default, unchanged): the existing
  `multiprocessing.Process` (`spawn` context) implementation from ADR-007,
  with the same restricted `SAFE_BUILTINS`/`SAFE_GLOBALS`, the same
  `os.environ.clear()` discipline, and the same poll-while-joining timeout
  enforcement (ADR-007's documented pipe-buffer-deadlock avoidance).
- **`"docker"`** (new, opt-in — `core/sandbox_docker.py`): runs the same
  code through the same restricted-globals dispatch (`_execute_code()`,
  extracted out of `_sandbox_worker()` so both backends share exactly one
  implementation of that logic — no duplicated security-relevant code),
  inside a `docker run` container instead of a process:
  - `--network none` — no network reachable from sandboxed code at all.
  - `--read-only` root filesystem, `--tmpfs /tmp:rw,noexec,nosuid,size=128m`
    for the only writable (and non-executable) location.
  - `--cap-drop ALL`, `--security-opt no-new-privileges`, `--user
    65534:65534` (non-root, matches the image's own `USER`).
  - `--memory`/`--memory-swap`/`--cpus`/`--pids-limit` — hard resource
    ceilings the "process" backend never had (only a wall-clock timeout).
  - No `-e`/`--env-file` at all — stronger than `os.environ.clear()`: there
    is no host environment passed into the container in the first place.
  - Payload transport: the request (`code`, `dfs`, `mode`, `entry_point`,
    `result_vars`, `extra_globals`, `extra_modules`, `extra_builtins`) and
    the response (`values`, `error`) are pickled over the container's
    stdin/stdout — the same values that already cross the `spawn` process
    boundary today via pickling, just piped to a container instead of a
    child process. No volume mount carries the payload, so no host path is
    ever exposed inside the container.
  - Timeout: `subprocess.Popen(...).communicate(timeout=...)`, and on
    expiry, `docker kill <container>` (not merely killing the local `docker`
    CLI handle — the container is the thing actually doing work, the same
    "must stop the thing itself, not just stop waiting for it" principle
    ADR-007 established for the process backend's SIGTERM/SIGKILL
    escalation).
  - `DockerSandboxUnavailableError` — raised, not silently downgraded to
    `"process"`, when Docker isn't reachable or the image isn't built. An
    explicit opt-in into stronger isolation that quietly falls back to
    weaker isolation is worse than a loud failure.

### Image

`docker/sandbox/Dockerfile` builds a minimal image (`python:3.12-slim` +
`uv sync --no-dev --no-editable`, the real `ai_etl` package — no `api`
extra, since sandboxed code never imports fastapi/uvicorn) so
`docker/sandbox/run_sandboxed.py` (the container's `ENTRYPOINT`) can import
`ai_etl.core.sandbox._execute_code` directly rather than re-implementing the
restricted-globals/mode dispatch a second time. `plotly`/`scikit-learn`/
`statsmodels` are already base dependencies (`pyproject.toml`), so no
separate extra is needed for Analyst/Science's `extra_globals`. Built via
`make sandbox-image`; not part of the top-level `Dockerfile`/Railway build.

### Alternatives considered

- **gVisor / Firecracker microVMs** — stronger isolation than plain Docker
  (userspace-kernel syscall interception, or a full lightweight VM
  boundary). Not implemented this round: same Railway-hosting constraint
  applies (neither runs inside a non-privileged Railway container either),
  and evaluating them meaningfully requires the same "separate execution
  service" infrastructure decision the Railway follow-up above already
  needs — better to make that decision once, for whichever container/VM
  technology the follow-up service actually runs, than to evaluate gVisor
  in the abstract here. Flagged as a real option for that follow-up, not
  rejected.
- **`RestrictedPython` or similar AST-level sandboxing** — ADR-032 flagged
  this as unexplored, not rejected. Still not evaluated: it would reduce
  reliance on kernel-level isolation but is a fundamentally different
  approach (restrict what code *can be written*, vs. contain what already-
  running code *can reach*) and doesn't remove the need for a real isolation
  boundary once external, unvetted tenants are in scope — the actual
  trigger ADR-032 named.
- **Ship the Docker backend as the production default immediately, accept
  that it needs a Docker daemon on Railway** — rejected outright once the
  Railway finding above was confirmed: this would silently break every
  sandboxed pipeline/analysis run in production (`DockerSandboxUnavailableError`
  on every call, since Railway has no Docker daemon to reach), the exact
  "ship something that silently doesn't work in production" outcome this
  task was explicitly scoped to avoid.
- **Do nothing until the ADR-032 trigger (self-serve external signups)
  actually fires** — rejected: this is the project owner's explicit,
  already-made decision to migrate now, ahead of that trigger, not a
  question this ADR reopens.

## Consequences

- **Positive:** the introspection bypass ADR-032 Decision 4 accepted as
  unmitigated is now demonstrably contained in the `"docker"` backend —
  proven, not just asserted, by `tests/integration/test_sandbox_docker.py`,
  which runs the exact `().__class__.__mro__[1].__subclasses__()` gadget
  named in ADR-003/ADR-007/ADR-032 and shows it can still reach a real `os`/
  `socket` module reference inside the container (Python-level restriction
  does not stop it, matching ADR-032's own finding) but cannot write outside
  `/tmp` (`--read-only`), cannot reach the network (`--network none`), and
  finds no host secrets in its environment (no `-e`/`--env-file` passed at
  all).
- **Positive:** `execute_in_sandbox()`'s signature and `SandboxResult` shape
  are unchanged for every existing caller — this migration is additive, not
  breaking, and can be adopted per-environment via one env var.
- **Positive:** the restricted-globals/mode-dispatch logic
  (`_execute_code()`) is now shared by both backends from one place in
  `core/sandbox.py` — a future fix to that logic (e.g. tightening
  `SAFE_BUILTINS` further) automatically applies to both, rather than
  needing to be kept in sync across two implementations.
- **Negative — accepted, explicit:** the `"docker"` backend is **not yet
  usable in production** on this project's current Railway deployment
  (non-privileged containers, no Docker-in-Docker). `"process"` remains the
  default and the only backend that actually runs in the deployed API/
  worker today. This is the single most important thing to not
  misrepresent about this migration's status.
- **Negative — accepted, explicit:** operational surface grows for whoever
  *does* run the `"docker"` backend (local dev today; a future separate
  execution service in production): Docker daemon availability becomes a
  new dependency, image build/pull adds latency the `"process"` backend
  never had (mitigated today by a pre-built local image, not a build-per-call),
  and container resource limits (`--memory`/`--cpus`) need tuning against
  real workload sizes the way ADR-012's row-count-based timeout scaling
  already does for wall-clock time — not yet re-derived for the container
  path.
- **Negative — deferred, tracked:** the actual production rollout (a
  separate sandboxed execution service reachable from Railway, per the
  "Railway feasibility" section) is real, unscoped follow-up work — not
  implemented, not estimated in detail, in this PR.

## Follow-up work (explicit, not silently dropped)

1. Design and stand up a separate execution service that *does* support
   on-demand container isolation (own Railway service pointed at a
   Docker-capable host, or a different provider entirely), reachable from
   the existing FastAPI/Celery services over a narrow internal API —
   sizing this is its own ADR-worthy decision (auth boundary, network
   exposure, cost, latency budget).
2. Once that service exists, flip `AI_ETL_SANDBOX_BACKEND=docker` (or
   redirect the `"docker"` backend to call that service instead of a local
   `docker run`) in the Railway production environment, and re-verify the
   introspection-bypass containment tests against that real deployment
   target, not just local Docker Desktop.
3. Re-derive resource limits (`--memory`/`--cpus`/timeout) against real
   Analyst/Science workload sizes on whatever infrastructure the follow-up
   service runs on, the same way ADR-012 did for the `"process"` backend's
   wall-clock timeout.
4. Revisit gVisor/Firecracker as the follow-up service's actual container
   runtime, now that "which host can run containers at all" is resolved as
   a separate question from "which container technology."

## Related

- [ADR-003](ADR-003-exec-sandbox.md) — original `exec()` sandbox decision,
  Docker considered and rejected then for latency/infra-readiness reasons
  that no longer hold given this ADR's own investigation and dev-verified
  implementation.
- [ADR-007](ADR-007-unified-sandbox-policy.md) — the `"process"` backend
  this ADR adds an alternative to; `_execute_code()`'s extraction keeps its
  timeout/pickling design intent (poll-while-joining, kill-the-thing-not-
  just-stop-waiting) mirrored in the Docker backend's own timeout handling.
- [ADR-032](ADR-032-security-posture-admin-role-sast.md) — Decision 4, the
  risk acceptance this ADR supersedes (see that document's own updated
  note).
- `core/sandbox.py`, `core/sandbox_docker.py`, `docker/sandbox/Dockerfile`,
  `docker/sandbox/run_sandboxed.py` — the implementation.
- `tests/unit/test_sandbox.py`, `tests/unit/test_sandbox_docker_dispatch.py`,
  `tests/integration/test_sandbox_docker.py` — contract-parity and
  containment tests.
