# ElastiCache Redis -- the AWS-managed replacement for Railway's managed
# Redis addon (ADR-008: Celery broker/result-backend + the per-tenant
# fixed-window rate-limit counters in services/execution_queue.py). Single
# node, no replication: matches Railway's own setup, which is also a
# single unclustered instance -- not a downgrade, a parity choice.

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.project_name}-${var.environment}-redis"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.project_name}-${var.environment}-redis"
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]
}
