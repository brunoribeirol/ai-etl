# AWS Secrets Manager. Railway's equivalent is its dashboard's plain
# (encrypted-at-rest, but not access-audited the same way) service
# variables -- see docs/deploy-diff-railway-vs-aws.md for the full
# comparison. Values are supplied via Terraform variables (sensitive,
# no defaults, never committed) and written here; they are not baked into
# the container image or the task definition's plaintext environment block.

locals {
  app_secrets = {
    openai-api-key        = var.openai_api_key
    app-database-url      = var.app_database_url
    clerk-secret-key      = var.clerk_secret_key
    clerk-publishable-key = var.clerk_publishable_key
  }
}

resource "aws_secretsmanager_secret" "app" {
  for_each = local.app_secrets

  name = "${var.project_name}-${var.environment}-${each.key}"
}

resource "aws_secretsmanager_secret_version" "app" {
  for_each = local.app_secrets

  secret_id     = aws_secretsmanager_secret.app[each.key].id
  secret_string = each.value
}
