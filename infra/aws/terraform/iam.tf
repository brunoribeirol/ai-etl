# --- Task execution role -------------------------------------------------
# Used by the ECS agent itself: pull the image from ECR, ship logs to
# CloudWatch, resolve Secrets Manager values into container env vars at
# task start. Not the application's own runtime identity -- see the task
# role below for that.

resource "aws_iam_role" "ecs_task_execution" {
  name = "${var.project_name}-${var.environment}-task-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "read-app-secrets"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [for s in aws_secretsmanager_secret.app : s.arn]
    }]
  })
}

# --- Task (application) role ----------------------------------------------
# What the running container is allowed to do at runtime. Scoped to the one
# S3 bucket ADR-009's StorageBackend needs -- and nothing else. This is a
# real, positive difference from the Railway deployment: Railway has no IAM
# concept, so it authenticates to S3 with static AWS_ACCESS_KEY_ID /
# AWS_SECRET_ACCESS_KEY env vars (see .env.example). On ECS, boto3 picks up
# temporary credentials from this task role automatically -- no static keys
# to provision, rotate, or leak. Documented in docs/deploy-diff-railway-vs-aws.md.

resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-${var.environment}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name = "runs-bucket-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = ["arn:aws:s3:::${var.s3_bucket_name}"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = ["arn:aws:s3:::${var.s3_bucket_name}/*"]
      }
    ]
  })
}
