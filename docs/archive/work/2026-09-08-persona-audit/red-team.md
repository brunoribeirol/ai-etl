# Red Team — Exploitable Vulnerabilities

**Date:** 2026-09-09 (audit opened 2026-09-08)
**Mode:** authorized, throwaway local instance. Real PoCs run; no app code modified.

## Method
Ran real payloads via `.venv` against `ai_etl.core.sandbox.execute_in_sandbox` on the
**default `process` backend** (macOS → seccomp is a no-op, mirroring the pre-ADR-046
production posture and any non-Linux/non-seccomp path). Tested the SQL allowlist
(`core/sql_safety.py`) directly against injection payloads. Ground-truth escape gadget
taken from the project's own `tests/unit/test_sandbox.py::_SUBPROCESS_GADGET_CODE`.

## Findings

### RT-1 — Sandbox escape → RCE + arbitrary file read (HIGH; documented accepted risk, but revisit trigger crossed)
Live PoC (script-mode payload, no `import` anywhere):
```
CMD_EXEC : RCE_OK:brunoribeiro        # subprocess.run(["whoami"]) via CompletedProcess.__init__.__globals__["run"]
FILE_READ: ##                          # first line of /etc/passwd via os.open/os.read reached through a subclass' globals
ENV_LEAK : <EMPTY>                      # os.environ cleared — secrets NOT reachable via env
```
- **Command execution** and **arbitrary host file read** both succeed on the `process`
  backend when seccomp is inactive. This is the exact risk ADR-032 Decision 4 accepts and
  ADR-046 partially closes.
- **Nuance the ADRs under-state:** the ADR-046 seccomp filter denies `execve`/`execveat`
  only. `os.open`/`os.read` use no `execve`, so **arbitrary file read of the container
  filesystem remains open even on seccomp'd Linux production** — and the container runs as
  **root** (`Dockerfile` has no `USER`, confirmed). On Linux the parent process's
  `/proc/<ppid>/environ` (untestable on macOS, no `/proc`) is a plausible route to the
  app's real secrets even though the child's own `os.environ` is scrubbed.
- **Env-scrubbing holds** (ENV_LEAK empty) — the ADR-007 mitigation works.
- **Revisit trigger:** ADR-032 Decision 4 says real isolation "must land *before*"
  self-serve unvetted signups. Per SECURITY.md the product was opened to self-serve
  signups (~2026-09-06); the primary mitigation (Vercel Sandbox, ADR-039) is disabled by
  the owner's Hobby-plan billing decision; the Docker backend can't run on Railway. So the
  trigger is crossed while the mitigation is inactive.

### RT-2 — SQL injection: PASS (prior real exploit properly closed)
`validate_table_name` (uses `re.fullmatch`) and `validate_select_only_query` reject every
payload tried; legit identifiers/queries accepted:
```
'products; DROP TABLE users --' -> rejected     'orders' -> ACCEPTED
'products WHERE 1=1'            -> rejected     'public.orders' -> ACCEPTED
'users\n'                       -> rejected
'SELECT * FROM t; DROP TABLE users' -> rejected  'SELECT * FROM t WHERE a=1' -> ACCEPTED
'SELECT load_file(...)'         -> rejected
```
The 2026-08-24 `DROP TABLE users; --` exploit is closed. No new SQLi finding.

## Verdict
The only live-exploitable class is the documented sandbox escape. It is honestly recorded
in ADR-032/ADR-046/SECURITY.md, but (a) file-read is open even with seccomp, (b) the
container is root, and (c) the ADR's own "fix before public signups" precondition was not
met when signups opened. This is the sharpest security finding of the audit.
