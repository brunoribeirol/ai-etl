# AI-ETL — AWS deploy (Sprint 10 portability proof)

> Companion to `docs/adr/ADR-012-multi-cloud-deploy-aws-portability.md` and
> `docs/deploy-diff-railway-vs-aws.md`.

## STOP — before you run `apply`

**Nothing in this directory has been provisioned.** This IaC was written and
validated (`terraform validate`, clean — see below) but deliberately never
applied, per the checkpoint in ADR-012's PR. Running `terraform apply`
creates real, billable AWS resources (ECS Fargate tasks, an ALB,
ElastiCache, ECR, Secrets Manager entries). **Get Bruno's explicit
go-ahead before running `apply`** — this file existing is not that
go-ahead.

## What this proves

That the same Docker image and application code that runs on Railway also
runs on AWS, with a deploy topology that mirrors Railway's two-service
shape (public web + unexposed worker) closely enough that the diff is
mostly *where config lives*, not *what the app needs*. See
`docs/deploy-diff-railway-vs-aws.md` for the full comparison.

## What gets created (if applied)

- ECR repository (image registry)
- ECS cluster (Fargate) with two services: `web` (public, behind an ALB)
  and `worker` (Celery, no public route)
- Application Load Balancer + target group + HTTP listener
- ElastiCache for Redis (single node) — Celery broker/result backend
- IAM: a task execution role (pull image, ship logs, read secrets) and a
  task role scoped to one S3 bucket (no static AWS keys needed by the app
  at runtime — see the deploy-diff doc)
- Secrets Manager entries for `OPENAI_API_KEY`, `APP_DATABASE_URL`,
  `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`
- CloudWatch log groups for both services

It reuses the account's **default VPC** (no dedicated network is
provisioned) and an **existing** S3 bucket you create by hand first (same
manual-step pattern ADR-009 already established for the Railway/S3
integration) — this stack does not create or touch the production bucket
(`ai-etl-artifacts-brlla`).

## Prerequisites

1. An AWS account with credentials configured locally (`aws configure` or
   equivalent) — **never commit these, never let an agent hold them.**
2. Terraform >= 1.6 (`terraform version` — this was validated with 1.15.8).
3. Docker, to build the image.
4. An S3 bucket already created (separate from the production one), e.g.
   `ai-etl-artifacts-aws-poc`, in the same region as `aws_region`.
5. A Clerk application's keys (reuse the existing one, or a test instance)
   and the existing Supabase `APP_DATABASE_URL`.

## Before you apply (for real, later)

- **Configure a remote state backend.** `versions.tf` deliberately has no
  `backend` block — local state is fine for `plan`-only review, never for
  a real `apply`. Add an S3 backend + DynamoDB lock table first.
- **Review `terraform.tfvars.example`** and copy it to a git-ignored
  `terraform.tfvars` with real values.

## Build and push the image

```bash
# From the repo root, not this directory.
aws ecr get-login-password --region <region> \
  | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com

docker build -t ai-etl-aws-poc .
docker tag ai-etl-aws-poc:latest <account-id>.dkr.ecr.<region>.amazonaws.com/ai-etl-aws-poc:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/ai-etl-aws-poc:latest
```

The ECR repository itself is created by this Terraform stack
(`ecr.tf`) — so the intended order on a real run is: `apply` once to
create the (empty) ECR repo and everything else that doesn't depend on the
image, push the image, then `apply` again (or from the start, with a
placeholder tag) to roll the services onto the pushed image. This
two-step chicken-and-egg is normal for Fargate + ECR and not a bug in this
stack.

## Dry-run commands (safe, no side effects)

```bash
cd infra/aws/terraform
terraform init -backend=false   # no remote state configured yet — see above
terraform validate              # confirmed clean, no AWS credentials needed
terraform plan                  # NEEDS real AWS credentials to read current
                                 # state via data sources (default VPC, caller
                                 # identity) -- read-only, creates nothing, but
                                 # was not run in this session (no credentials
                                 # were used, per the task's constraints)
```

`terraform validate` was run in this session and is clean. `terraform
plan` was intentionally **not** run here — it requires live AWS API calls
(even read-only ones, e.g. resolving the default VPC) via real credentials,
which this task is not authorized to use. Run `plan` yourself before
requesting an `apply` decision.

## Known simplifications

See `docs/deploy-diff-railway-vs-aws.md`'s "Known simplifications" section
— default VPC/public subnets, HTTP-only ALB, no autoscaling, no remote
state backend. All intentional scope cuts for a portability *proof*, not
gaps to silently carry into a real production cutover.

## Tearing down

If this is ever applied for a live demo, tear it down promptly
(`terraform destroy`) once verification is done — ECS/ALB/ElastiCache all
bill continuously while running, unlike Railway's stopped-service billing
model.
