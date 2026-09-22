# Runbook — Credential Rotation

> Scope: rotating a long-lived credential used by the deployed application. Written after the
> `ANTHROPIC_API_KEY` rotation on 2026-09-21, which failed on the first attempt and produced a
> short window where the key in production was invalid. The procedure below is shaped by that
> failure — read "Lessons from 2026-09-21" before rotating anything.

## Credential inventory

Where each externally-issued credential actually lives. Verified 2026-09-21 by inspecting the
Railway variable listing, the GitHub secret listing, and the source tree — not from memory.

| Credential | Railway `ai-etl` | Railway `tranquil-appreciation` | Railway `celery-beat` | GitHub secrets | Vercel | Local |
|---|---|---|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | yes | no | no | no | no |
| `OPENAI_API_KEY` | yes | yes | no | `OPEN_API_KEY` (unused by workflows) | no | no |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | yes | yes | no | `BACKUP_*` (separate pair) | no | no |
| `CLERK_SECRET_KEY` | yes | no | no | no | publishable key only | no |
| `AI_ETL_SECRETS_ENCRYPTION_KEY` | yes | yes | no | no | no | no |
| `RESEND_API_KEY` | no | yes | no | no | no | no |

Notes that save time later:

- **`celery-beat` holds no application credentials** — only `REDIS_URL` and the Railway-injected
  variables. It never needs touching during a rotation.
- **There is no `.env` at the repo root.** Only `.env.example`. If one is ever created (e.g. to
  run `case_study/scripts/model_comparison.py`), it must use a **separate, short-lived key**: the
  project directory is inside an iCloud-synced folder, which is a materially higher exposure than
  a Railway environment variable.
- **No provider key is stored in the database.** `services/secrets_service.py` holds tenant-scoped
  *data source* credentials (connection strings), encrypted with Fernet. LLM provider keys are
  process-level environment variables only — there is no BYOK path for them.
- **The frontend never reads a provider key.** `frontend/src/lib/model-reference-data.ts` contains
  provider *names* as static data; the browser always calls the Railway API.

## Procedure

Use the **Railway dashboard**, not the CLI. See "Lessons" below for why.

1. **Create the new credential** at the provider. Do **not** revoke the old one yet — both can be
   valid at the same time, which is what makes a zero-downtime rotation possible.
   - Anthropic shows an API key exactly once, at creation. Save it before leaving the page.
2. **Update every location** in the inventory table above. For `ANTHROPIC_API_KEY` that is two
   Railway services: `ai-etl` and `tranquil-appreciation`.
   - Railway → service → Variables → edit → Save. Saving triggers a redeploy automatically.
3. **Confirm a redeploy actually fired.** Each service should show a deployment dated today. If
   the deployment list is unchanged, the variable was not written — see Lessons.
4. **Verify with a real API call** (next section). "The command returned no error" is not
   verification.
5. **Only after a 200: revoke the old credential.**
6. **Record it**: add the rotation date and the new expiry date to `docs/CURRENT_STATE.md`, and
   import the `.ics` reminder for the next rotation.

## Verification

The authoritative check runs the credential **as stored in Railway** against the provider, and
prints only the HTTP status — the value is never displayed:

```bash
railway run -s ai-etl -e production -- sh -c \
  'curl -s -o /dev/null -w "ai-etl -> HTTP %{http_code}\n" \
   https://api.anthropic.com/v1/models \
   -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01"'
```

Repeat with `-s tranquil-appreciation`. `200` = valid. `401` = invalid or revoked.

`railway run` pulls variables from the Railway API, so this tests the **stored** value
independently of whether the running container has picked it up yet. That separation is useful:
a 200 here with a stale container means "the value is right, the deploy hasn't landed".

There is also a product-level check — `POST /llm/test-connectivity` (`api/routers/llm.py`), exposed
in the UI on each model card in the model picker. It makes one real LLM call and reports
`ok`/`latency_ms`. Use it to confirm the *application* path, after the curl confirms the credential.

## Lessons from 2026-09-21

The first attempt used a shell one-liner:

```bash
read -rs "?Nova ANTHROPIC_API_KEY: " NEW_KEY && echo
printf '%s' "$NEW_KEY" | railway variable set ANTHROPIC_API_KEY --stdin -s ai-etl -e production
```

It silently wrote nothing. The old key had already been revoked, so both services were left
returning `401`. Three things went wrong, each worth avoiding:

1. **The old credential was revoked before verifying the new one.** This is the only mistake that
   actually caused an outage. Revocation is the *last* step, always.
2. **A multi-line paste into an interactive `read`** can let the following line be consumed as the
   value. The failure is silent — no error, wrong value stored.
3. **"No error" was treated as success.** The signal that exposed the truth was structural: the
   most recent deployment on both services was still eight days old. A variable write always
   triggers a redeploy unless `--skip-deploys` is passed, so *no new deployment means no write*.
   Check the deployment timestamp before trusting a variable change.

Impact was contained by an unrelated fact: `AI_ETL_LLM_PROVIDER` is not set in Railway, so the
default provider is `openai` (`core/llm.py:85`) and the default execution path was never affected.
Only pipelines with an explicit `llm_provider=anthropic` override would have failed.

## Alternatives evaluated

### Workload Identity Federation — rejected, platform-blocked

The Anthropic API supports WIF (GA, no beta header). The SDK auto-detects it when
`ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`, `ANTHROPIC_SERVICE_ACCOUNT_ID` and
`ANTHROPIC_IDENTITY_TOKEN_FILE` are all present, exchanges the JWT at `/v1/oauth/token`, and
refreshes automatically. No long-lived credential.

**It does not apply here because Railway does not issue workload OIDC identity tokens.** Railway's
OIDC surface is SAML SSO (enterprise, human login) and "Login with Railway" (user OAuth); nothing
injects an identity token into a running container. Without that token there is no identity to
federate, and standing up a custom issuer would require a long-lived credential to authenticate
*to the issuer* — circular.

Secondary blockers, relevant only if the platform one is ever removed:

- `core/llm.py:147` calls `_require_env("ANTHROPIC_API_KEY", "anthropic")`, which fails the request
  before the SDK's credential resolution runs.
- The project pins `anthropic` 0.125.0 via `langchain-anthropic` 1.7.1; WIF support in that version
  is unconfirmed and would likely require the SDK's 0.x → 1.x major upgrade.
- OpenAI and Google would keep static keys, breaking the uniform `_require_env` treatment that
  `core/llm.py` currently gives all three providers.

Bedrock IAM and Vertex ADC were rejected for the same root reason: from Railway, both still
require a static credential (AWS access keys, or a service account key file) to bootstrap.

**Revisit if** the backend moves to a platform that issues workload identity tokens — ECS/EKS,
Cloud Run/GKE, or any Kubernetes with a projected service account token.

### Related, and actually available: GitHub Actions OIDC → AWS

`.github/workflows/db-backup.yml` authenticates with permanent AWS access keys held as repository
secrets. GitHub Actions **does** issue OIDC tokens, so that one is genuinely federatable. It is
tracked as deferred work in `docs/work/2026-09-13-repository-hardening.md`.

## Expiry tracking

| Credential | Rotated | Expires | Reminder |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | 2026-09-21 | **2027-09-21** (12 months) | 2027-08-22 — `anthropic-key-expiry-2027-09-21.ics` |

Twelve months was chosen deliberately over the more conventional 90 days. A 90-day key created on
2026-09-21 would have expired on 2026-12-20 — inside the TCC defense window (~18-20 Dec 2026).
Six months (2027-03-20) was the acceptable stricter alternative. The security value of a shorter
window is small here: the key lives only in two Railway environment variables, never in git, CI, or
a local file, so the realistic leak surface is narrow.

Nothing in the system tracks this date. There is deliberately **no code** for it — an expiry monitor
for a single credential is not worth the maintenance. The calendar event is the mechanism; import
the `.ics` and the reminder survives any laptop, repo, or context loss.
