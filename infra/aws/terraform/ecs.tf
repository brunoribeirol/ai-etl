resource "aws_ecs_cluster" "this" {
  name = "${var.project_name}-${var.environment}"
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${var.project_name}-${var.environment}/web"
  retention_in_days = 14
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.project_name}-${var.environment}/worker"
  retention_in_days = 14
}

# --- Shared, non-secret application config --------------------------------
# Mirrors .env.example / docs/CURRENT_STATE.md's Railway env var list.
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are deliberately absent here --
# boto3 (audit/storage.py's S3StorageBackend) picks up credentials
# automatically from the task role (iam.tf) instead. See
# docs/deploy-diff-railway-vs-aws.md.

locals {
  redis_url = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.cache_nodes[0].port}/0"

  common_env = [
    { name = "AI_ETL_LLM_MODEL", value = "gpt-4o-mini" },
    { name = "AI_ETL_LOG_DIR", value = "./runs" },
    { name = "AI_ETL_MAX_RETRIES", value = "3" },
    { name = "AI_ETL_SANDBOX_TIMEOUT", value = "30" },
    { name = "STORAGE_BACKEND", value = "s3" },
    { name = "AI_ETL_S3_BUCKET", value = var.s3_bucket_name },
    { name = "AI_ETL_ENV", value = var.environment },
    { name = "AWS_DEFAULT_REGION", value = var.aws_region },
    { name = "REDIS_URL", value = local.redis_url },
    { name = "AI_ETL_RATE_LIMIT_MAX_RUNS", value = "10" },
    { name = "AI_ETL_RATE_LIMIT_WINDOW_SECONDS", value = "3600" },
    { name = "CLERK_JWKS_URL", value = var.clerk_jwks_url },
    { name = "CLERK_ISSUER", value = var.clerk_issuer },
  ]

  common_secrets = [
    { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.app["openai-api-key"].arn },
    { name = "APP_DATABASE_URL", valueFrom = aws_secretsmanager_secret.app["app-database-url"].arn },
    { name = "CLERK_SECRET_KEY", valueFrom = aws_secretsmanager_secret.app["clerk-secret-key"].arn },
    { name = "CLERK_PUBLISHABLE_KEY", valueFrom = aws_secretsmanager_secret.app["clerk-publishable-key"].arn },
  ]
}

# --- Web (FastAPI) service -------------------------------------------------

resource "aws_ecs_task_definition" "web" {
  family                   = "${var.project_name}-${var.environment}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.web_cpu
  memory                   = var.web_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name      = "web"
      image     = var.container_image
      essential = true
      portMappings = [
        { containerPort = var.container_port, protocol = "tcp" }
      ]
      # Same ENTRYPOINT as the Dockerfile ("sh -c ... --port $PORT") --
      # only the PORT value differs from Railway's injected one.
      environment = concat(local.common_env, [
        { name = "PORT", value = tostring(var.container_port) },
        { name = "API_ALLOWED_ORIGINS", value = var.api_allowed_origins },
      ])
      secrets = local.common_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.web.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "web"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "web" {
  name            = "${var.project_name}-${var.environment}-web"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = true # default VPC's subnets are public -- see README.md "Known simplifications"
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.web_http]
}

# --- Celery worker service --------------------------------------------------
# Same image as web, different container command -- the ECS equivalent of
# Railway's second service with a Custom Start Command overriding the
# Dockerfile ENTRYPOINT (see docs/CURRENT_STATE.md's "Second Railway
# service" note). --pool=threads preserved for the same reason: ADR-007's
# sandbox needs to spawn a real multiprocessing.Process per execution, and
# Celery's prefork pool runs daemonic workers that can't have children.

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project_name}-${var.environment}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name       = "worker"
      image      = var.container_image
      essential  = true
      entryPoint = ["sh", "-c"]
      command = [
        "celery -A ai_etl.core.celery_app:celery_app worker --loglevel=info --pool=threads --concurrency=2"
      ]
      environment = local.common_env
      secrets     = local.common_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "worker" {
  name            = "${var.project_name}-${var.environment}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true # no inbound SG rule regardless -- see security_groups.tf
  }
}
