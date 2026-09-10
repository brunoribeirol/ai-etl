# Sub-processor Register

**Date:** 2026-09-06
**Purpose:** SOC 2 CC9 / LGPD Art. 39 / GDPR Art. 28 — disclose every
third-party vendor that processes tenant or personal data on this
application's behalf, and this project's own assessment of each vendor's
compliance posture. Sourced from `.env.example`, `core/llm.py`'s provider
list, and `docs/compliance/lgpd-gdpr-data-processing.md` — not invented.

**How to read this:** "Data exposed" is what actually flows to that vendor
from this application, not a generic description of the vendor's product.
"Vendor's own compliance posture" links to the vendor's published report or
security page; this project has not independently audited any of them —
that's the nature of relying on a sub-processor rather than self-hosting.

---

| Vendor | Role | Data exposed | Vendor's own compliance posture |
|---|---|---|---|
| **Clerk** | Identity provider (auth) | Email, name, auth metadata for every user; this app never stores these fields itself (`docs/compliance/lgpd-gdpr-data-processing.md` §1) | [SOC 2 Type II](https://clerk.com/legal/security) |
| **Supabase** | Application Postgres hosting | Every row this app writes — `users`, `runs`, `analysis_runs`, `saved_pipelines`, `tenant_secrets` (ciphertext only), audit/admin logs | [SOC 2 Type II](https://supabase.com/security) |
| **Railway** | API + Celery worker hosting | Application logs (redacted — `audit/logger.py` auto-redacts key/token/secret fields), env vars (credentials), request bodies transiting the API at runtime | [Railway Trust & Security](https://railway.com/legal/security) — no published SOC 2 report as of this writing; revisit if/when a customer's due-diligence process requires one |
| **Vercel** | Frontend hosting | Static frontend assets, browser-side requests proxied to the API; no tenant data at rest | [SOC 2 Type II](https://vercel.com/docs/security/soc2) |
| **AWS S3** | Run artifact storage (production) | Silver/Gold/Science CSVs and figures — the actual dataset content a tenant uploads or connects, tenant-scoped key prefix (ADR-009) | [AWS SOC 2 / SOC 3](https://aws.amazon.com/compliance/soc-faqs/) |
| **OpenAI** | LLM provider (opt-in per tenant/pipeline, ADR-031) | Pipeline spec, extracted schema/sample data, business questions — whatever a Transformer/Analyst/Science/Advisor prompt includes | [OpenAI enterprise privacy / SOC 2](https://openai.com/enterprise-privacy/) |
| **Anthropic** | LLM provider (opt-in per tenant/pipeline, ADR-031) | Same category of data as OpenAI above, only when a tenant selects this provider | [Anthropic Trust Center](https://trust.anthropic.com/) |
| **Google (Gemini)** | LLM provider (opt-in per tenant/pipeline, ADR-031) | Same category of data as OpenAI above, only when a tenant selects this provider | [Google Cloud compliance](https://cloud.google.com/security/compliance) |
| **GitHub (Actions)** | Backup execution runner (ADR-042) | The nightly `.github/workflows/db-backup.yml` runs `pg_dump --schema=public` of the production database — `users`, `runs`, `analysis_runs`, `saved_pipelines`, `tenant_secrets` (ciphertext) — on a GitHub-hosted Actions runner, then uploads it to AWS S3. The dump transits the runner's ephemeral filesystem. | [GitHub SOC 2 / Trust Center](https://github.com/security) |

**Not a sub-processor:** Ollama, when used, runs entirely on the operator's
own machine for local model comparison (`case_study/scripts/model_comparison.py`)
— no tenant data plane ever reaches it in production; it has never been, and
is not planned to be, part of the client-facing model selector (see
`docs/CURRENT_STATE.md`'s Decisions on this).

## Known gap, explicitly not closed by this document

**No verification that LLM sub-processors delete data already sent to them
as part of a completed request** — this is outside this application's
control layer once a request leaves it (`docs/CURRENT_STATE.md`,
`docs/compliance/lgpd-gdpr-data-processing.md` §5). Each provider's own
enterprise/API data-retention terms govern this, not this application.

## Review cadence

Reviewed whenever a new vendor is integrated, or at minimum once before any
paid customer's due-diligence review. Last reviewed: 2026-09-09 (added GitHub
Actions as the backup-execution sub-processor — audit finding M1/CTO-6; the
2026-09-07 backup workflow had introduced it without a register update).
