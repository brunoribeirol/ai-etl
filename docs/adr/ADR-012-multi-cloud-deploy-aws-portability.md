# ADR-012 — Multi-cloud deploy: AWS as the portability proof (Terraform, not applied)

**Status:** Accepted
**Date:** 2026-08-19
**Deciders:** Bruno Ribeiro

---

## Context

Sprint 10 of the unified roadmap (Vault `artefact/sprint-roadmap.md`) asks for evidence that AI-ETL's Docker-based deployment isn't tied to Railway specifically — the goal is a portability *proof*, not a production migration. Railway already proved "the app runs from one Dockerfile" (Sprints 1–7); this sprint proves "and it also runs on a real hyperscaler," which matters for the TCC's architecture chapter (a containerized, cloud-agnostic design is a claim that needs evidence, not just an assertion) and, separately, de-risks a future real migration if Railway ever stops being the right fit commercially.

Two constraints shaped this decision before any IaC was written:

1. **No real infrastructure gets provisioned by this work.** The orchestrating session has no authorization to run `terraform apply`, hold real cloud credentials, or create billable resources — see the checkpoint note in this ADR's PR. Everything here is designed, `validate`d, and reviewable, but deliberately never applied.
2. **AWS vs. GCP vs. both** was an open call the roadmap flagged, noting the S3 storage backend (ADR-009) already gives AWS a head start.

## Decision

**Target AWS only for this sprint**, via ECS Fargate + Terraform. GCP is not attempted now.

### Why AWS over GCP (or both)

| Factor | AWS | GCP |
|---|---|---|
| Storage | `S3StorageBackend` already exists and is live in production (ADR-009, `audit/storage.py`) — zero new application code | Would need a new `GCSStorageBackend` implementation, a second `StorageBackend` variant to test and maintain |
| Credentials/IAM | AWS account already exists and is already in active use (the live S3 bucket) | New account, new IAM model, new billing setup from scratch |
| Marginal proof value | Reuses a real, already-integrated dependency — closer to "does our existing cloud-agnostic design hold" | A second cloud adds breadth but the app doesn't have any GCP-specific code path to prove out yet |
| Effort vs. sprint goal | One cloud, done properly (real Terraform, real `plan`-level validation) | Two clouds, each necessarily shallower, for a sprint whose deliverable is "prove portability," not "run in production on N clouds" |

**"Both" was considered and rejected for this sprint specifically**: splitting the same time budget across two providers would mean either a shallower AWS stack or a GCP stack built without any of the storage-layer work already done for AWS, and the roadmap's ask is proof of portability, not breadth. If the TCC or product roadmap later wants a second provider, `audit/storage.py`'s `StorageBackend` protocol (ADR-009) is already the extension point — a `GCSStorageBackend` is additive, not a redesign, whenever that's actually needed. Recorded as a candidate for a later sprint, not committed to one now.

### Why ECS Fargate over the alternatives considered

| Option | Verdict |
|---|---|
| **AWS App Runner** | Closest analogue to Railway's simplicity (build from an image, auto HTTPS, no cluster to manage) — but it always provisions a public HTTPS endpoint per service, which is a poor fit for the Celery worker (Railway's worker service is deliberately unexposed, no public domain; matching that shape mattered for the diff to be honest about what actually changes). |
| **ECS Fargate** (chosen) | Serverless containers (no EC2 fleet to patch), one cluster hosting both a public web service (behind an ALB) and a private, un-exposed worker service — the closest real match to the existing two-Railway-service topology (`docs/CURRENT_STATE.md`'s "Second Railway service" section). Same container image for both, differing only in the task definition's command — mirrors Railway's Custom Start Command override exactly. |
| **EKS (Kubernetes)** | Correct for a real production migration at scale, but a large step up in operational surface (control plane, node groups, cluster add-ons) for what this sprint needs to prove. Rejected as disproportionate to a portability *proof*. |
| **EC2 + Docker Compose** | Closest 1:1 to local dev, but reintroduces exactly the patching/scaling ops burden Railway (and Fargate) exist to remove — would prove "Docker runs on a VM," which was never in question. |

### Why Terraform (not CloudFormation/CDK/Pulumi)

The roadmap already suggested Terraform, and it holds up on its own merits here: provider-agnostic HCL (the same tool would express a future GCP stack, unlike CloudFormation), a mature `hashicorp/aws` provider, and `terraform plan`/`validate` give exactly the pre-apply dry-run this sprint's constraints require without inventing custom tooling. No justification needed to deviate — Terraform is used as suggested.

### Scope

- `infra/aws/terraform/` — the full IaC: ECS Fargate cluster running two services (`web`, `worker`) from one ECR image, an ALB in front of `web` only, ElastiCache Redis (replaces Railway's managed Redis addon), IAM roles (task execution + a scoped task role for S3 access), Secrets Manager for the same secrets Railway holds in its dashboard. Reuses the account's **default VPC** rather than provisioning a dedicated network — a deliberate scope cut for a proof, documented as a known simplification in `infra/aws/terraform/README.md` (a real production cutover would want private subnets + NAT gateway).
- `docs/deploy-diff-railway-vs-aws.md` — exactly what changes moving from the Railway config to this AWS one: env vars, networking model, secrets handling, credential model (a real, positive difference: ECS uses an IAM task role for S3 instead of Railway's static `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`).
- **Not in scope**: a second managed Postgres. `APP_DATABASE_URL` keeps pointing at the existing Supabase instance either way — this sprint is about compute/network/storage portability, not about also re-homing the application database, which is already cloud-agnostic (a plain Postgres connection string) and out of scope for both Railway's and this ADR's decision.

## Consequences

- **Positive**: a real, `terraform validate`-clean, human-reviewable IaC artifact exists proving the Docker image is not Railway-specific — directly usable evidence for the TCC's architecture chapter.
- **Positive**: the IAM-task-role-over-static-keys difference is a genuine security improvement path, worth calling out even though it's not being adopted on Railway (Railway has no equivalent mechanism).
- **Positive**: `StorageBackend`'s existing abstraction (ADR-009) needed zero code changes to support this — validates that ADR's design.
- **Negative**: two deploy targets to reason about if this were ever kept running long-term (not the intent here — this is a proof, torn down after review, never applied) — real ongoing multi-cloud operation would need a decision about which is primary and how config drift between them is prevented.
- **Negative**: the default-VPC simplification means this stack is not directly production-ready as-is; anyone promoting it later needs to add private networking first.
- **Neutral**: no application code changed. This ADR is infrastructure-only.

## Related

- ADR-009 — `StorageBackend` abstraction this decision reuses without modification.
- ADR-008 — Celery/Redis architecture ElastiCache replaces the managed-Redis half of.
- `docs/CURRENT_STATE.md` — "Deploy" section, the Railway configuration this ADR is diffed against.
- `docs/deploy-diff-railway-vs-aws.md` — the detailed diff.
- `infra/aws/terraform/README.md` — how to run `plan`/`apply` (human-gated), known simplifications.
