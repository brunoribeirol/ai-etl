# Deploy diff — Railway (current, live) vs. AWS (Sprint 10 portability proof)

> Companion to `docs/adr/ADR-012-multi-cloud-deploy-aws-portability.md`. This
> document is descriptive (what would change if this stack were applied),
> not a record of anything actually provisioned — **nothing in the AWS
> column below has been created.** See `infra/aws/terraform/README.md` for
> the human-approval gate before any `apply`.

## Topology

| | Railway (live) | AWS (`infra/aws/terraform/`) |
|---|---|---|
| Web process | `ai-etl` service, Dockerfile `ENTRYPOINT`, public domain auto-generated (`*.up.railway.app`) | ECS Fargate service (`web`), behind an Application Load Balancer, public DNS from the ALB |
| Worker process | Second Railway service, same image, Custom Start Command overrides `ENTRYPOINT`, no public domain | ECS Fargate service (`worker`), same ECR image, task definition overrides the container command, no load balancer / no public IP route |
| Build | Railway builds directly from the repo's Dockerfile on every push (Railpack/Dockerfile builder, `railway.json`) | No "build from git" equivalent for Fargate — image is built and pushed to ECR manually/via CI (`docker build && docker push`), referenced by tag in `terraform.tfvars`' `container_image` |
| Redis | Railway-managed Redis addon, referenced via `${{Redis.REDIS_URL}}` | ElastiCache for Redis, single node (`cache.t3.micro` by default), `REDIS_URL` derived from the cluster's endpoint by Terraform |
| Networking | Fully managed by Railway — no VPC/subnet/security-group concepts exposed | Account's **default VPC** + its default (public) subnets — see "Known simplifications" in the Terraform README; a real production cutover would add private subnets + NAT |
| Load balancing/TLS | Railway terminates TLS automatically on the generated domain | ALB, **HTTP only** in this proof (no ACM cert/domain owned for this exercise) — a real cutover adds an HTTPS listener |

## Environment variables

Same application-level variables either way (`.env.example` is the source of truth for what the app reads); what changes is *where* they live and *how* they're injected.

| Variable | Railway | AWS |
|---|---|---|
| `OPENAI_API_KEY`, `APP_DATABASE_URL`, `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY` | Railway dashboard "Variables" tab (encrypted at rest, not individually access-audited) | AWS Secrets Manager, injected into the container via the ECS task definition's `secrets` block (not plaintext `environment`) — `iam.tf` scopes exactly which role can read them |
| `CLERK_JWKS_URL`, `CLERK_ISSUER`, `AI_ETL_LLM_MODEL`, `AI_ETL_LOG_DIR`, `AI_ETL_MAX_RETRIES`, `AI_ETL_SANDBOX_TIMEOUT`, `AI_ETL_RATE_LIMIT_*` | Railway dashboard, plaintext | ECS task definition, plaintext `environment` block (non-secret, matches Railway's own treatment) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | **Required** on Railway — boto3 has no other way to get AWS credentials from a non-AWS host | **Absent by design on AWS.** boto3 (`audit/storage.py`'s `S3StorageBackend`) picks up temporary credentials automatically from the ECS task's IAM role (`aws_iam_role.ecs_task`, `iam.tf`). No static long-lived key to provision, rotate, or ever leak in a log. This is the one genuine security *improvement* this ADR's stack has over the current Railway setup — noted in ADR-012, not backported to Railway (no equivalent mechanism exists there). |
| `AI_ETL_S3_BUCKET` | `ai-etl-artifacts-brlla` (production bucket) | A **separate** bucket (e.g. `ai-etl-artifacts-aws-poc`), created manually before any `apply` — this proof must never point at production data |
| `AI_ETL_ENV` | `prod` | `aws-poc` (or whatever `environment` var is set to) — ADR-009's existing key-prefix isolation means this alone prevents any collision with Railway's `prod/` prefix even if the buckets were ever shared (they aren't here) |
| `PORT` | Injected by Railway at runtime, read via `$PORT` in the Dockerfile's `sh -c` entrypoint | Fixed at `8000` via Terraform (`container_port` variable), passed as a normal env var — same `$PORT`-reading `ENTRYPOINT` works unmodified |
| `API_ALLOWED_ORIGINS` | Single production Vercel origin | Set per-`apply` to whatever frontend origin is being verified against (a Vercel preview, or omitted entirely for a `curl`-only smoke test) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` Railway variable reference | Computed by Terraform from the ElastiCache cluster's endpoint, wired into both task definitions automatically |

## What did **not** change

- Application code: zero changes. Same Docker image, same `ENTRYPOINT`, same FastAPI app, same Celery worker command.
- `APP_DATABASE_URL` still points at the existing Supabase Postgres instance in both cases — this proof is about compute/network/storage portability, not about also re-homing the application database.
- `StorageBackend`'s S3 implementation (ADR-009) — used as-is, no code path is AWS-vs-Railway-specific.
- Celery `--pool=threads` requirement (ADR-007's sandbox needs real child processes, incompatible with Celery's default daemonic `prefork` pool) — identical constraint, identical fix, on both platforms.

## Known simplifications in the AWS proof (not production-ready as-is)

- Default VPC / public subnets, not a dedicated private-subnet + NAT topology.
- ALB is HTTP-only — no ACM certificate/HTTPS listener (no owned domain for this exercise).
- No autoscaling policies — fixed `desired_count` (1 each for web/worker by default).
- No remote Terraform state backend configured (see `infra/aws/terraform/README.md`) — deliberate for a proof that's never applied; required before any real use.

See `infra/aws/terraform/README.md` for how to (eventually, with explicit human approval) build the image, run `plan`, and what `apply` would actually create.
