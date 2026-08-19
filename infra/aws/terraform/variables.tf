# AI-ETL — AWS portability proof (Sprint 10, ADR-012)
#
# Variable defaults intentionally mirror the values already in use on
# Railway (see .env.example / docs/CURRENT_STATE.md's "Deploy" section) so
# the diff between the two deployments is in *where* config lives, not what
# it says.

variable "aws_region" {
  description = "AWS region to deploy into. sa-east-1 matches the existing S3 bucket from ADR-009 (ai-etl-artifacts-brlla), avoiding cross-region S3 data-transfer cost/latency."
  type        = string
  default     = "sa-east-1"
}

variable "environment" {
  description = "Deployment environment, scopes resource names and the S3 key prefix (AI_ETL_ENV, same convention as ADR-009). Kept distinct from Railway's prod so this portability proof can never collide with the live Railway deployment's data."
  type        = string
  default     = "aws-poc"
}

variable "project_name" {
  description = "Short name used as a prefix for every resource this stack creates."
  type        = string
  default     = "ai-etl"
}

variable "container_image" {
  description = "Full ECR image URI (repository:tag) to deploy. Built and pushed separately -- see README.md \"Build and push the image\". No default: must be supplied explicitly so a stale/wrong image can never be deployed by accident."
  type        = string
}

variable "container_port" {
  description = "Port the FastAPI container listens on inside the task. Matches docker-compose.yml's local dev parity setup (PORT=8000) -- the Dockerfile's ENTRYPOINT reads $PORT at runtime either way."
  type        = number
  default     = 8000
}

variable "web_desired_count" {
  description = "Number of Fargate tasks for the web (FastAPI) service."
  type        = number
  default     = 1
}

variable "worker_desired_count" {
  description = "Number of Fargate tasks for the Celery worker service."
  type        = number
  default     = 1
}

variable "web_cpu" {
  description = "Fargate task CPU units for the web service (1024 = 1 vCPU)."
  type        = number
  default     = 512
}

variable "web_memory" {
  description = "Fargate task memory (MiB) for the web service."
  type        = number
  default     = 1024
}

variable "worker_cpu" {
  description = "Fargate task CPU units for the Celery worker service."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Fargate task memory (MiB) for the Celery worker service."
  type        = number
  default     = 1024
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type -- the AWS-managed replacement for Railway's managed Redis addon (ADR-008)."
  type        = string
  default     = "cache.t3.micro"
}

variable "s3_bucket_name" {
  description = "Existing S3 bucket for tenant-scoped run artifacts (ADR-009). Not created by this stack -- it already exists (ai-etl-artifacts-brlla on the live Railway deployment); a separate bucket name is expected for this portability proof to avoid touching production data."
  type        = string
}

# --- Application secrets -----------------------------------------------
# All marked sensitive; none have defaults. Passed via -var or a
# git-ignored *.auto.tfvars at apply time -- never committed. Stored in AWS
# Secrets Manager by this stack (see secrets.tf), not as plaintext
# environment variables on the ECS task definitions.

variable "openai_api_key" {
  description = "OPENAI_API_KEY -- same variable the Railway deployment uses."
  type        = string
  sensitive   = true
}

variable "app_database_url" {
  description = "APP_DATABASE_URL -- the existing Supabase Postgres connection string (Session pooler, IPv4-compatible -- see docs/CURRENT_STATE.md's Railway note on why Direct connection doesn't work). Deliberately reused as-is: this proof is about compute/network/storage portability, not about also standing up a second managed Postgres."
  type        = string
  sensitive   = true
}

variable "clerk_secret_key" {
  description = "CLERK_SECRET_KEY, same Clerk application as Railway/Vercel."
  type        = string
  sensitive   = true
}

variable "clerk_publishable_key" {
  description = "CLERK_PUBLISHABLE_KEY."
  type        = string
  sensitive   = true
}

variable "clerk_jwks_url" {
  description = "CLERK_JWKS_URL."
  type        = string
}

variable "clerk_issuer" {
  description = "CLERK_ISSUER."
  type        = string
}

variable "api_allowed_origins" {
  description = "API_ALLOWED_ORIGINS -- comma-separated CORS allowlist. Point this at a Vercel preview URL (or a temporary frontend deploy) if verifying end-to-end; never a wildcard."
  type        = string
}
