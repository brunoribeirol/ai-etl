# Documentation map

| Where | What | Start with |
|---|---|---|
| [`CURRENT_STATE.md`](CURRENT_STATE.md) | What is true today: where the system runs, what is verified, what is open, the limits kept by decision | read this first |
| [`history/state-log.md`](history/state-log.md) | Every dated entry since August 2026: what changed, what was verified and why | the newest entry, at the top |
| [`adr/`](adr/) | 47 architecture decision records (`ADR-001` to `ADR-047`), one decision each | ADR-003 (sandbox), ADR-040 (tenant isolation) |
| [`architecture/`](architecture/) | Storage roles (database, object store, files) and what changes when moving from Railway to AWS | `architecture-storage.md` |
| [`runbooks/`](runbooks/) | Operational procedures: credential rotation, the Anthropic key expiry reminder | `credential-rotation.md` |
| [`compliance/`](compliance/) | SOC 2 self-assessment, LGPD/GDPR data processing, risk register, incident response, sub-processors | `soc2-readiness-assessment.md` |
| [`archive/`](archive/) | Superseded plans and audits (`archive/work/`: sprint plans and audits of August and September; old phase documents), kept for the history the ADRs point to | only when an ADR links there |

Other places in the repository:

- `CLAUDE.md`: architecture in one page, commands, the non-negotiable rules.
- `case_study/README.md`: the case study of the report and the other inputs in that folder.
- `frontend/README.md`: the web interface.
- `infra/aws/terraform/README.md`: the AWS deployment kept as a portability option.
