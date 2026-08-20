output "alb_dns_name" {
  description = "Public DNS name of the load balancer -- the AWS equivalent of Railway's *.up.railway.app domain. http://<this>/health should return 200 once the service is stable."
  value       = aws_lb.web.dns_name
}

output "ecr_repository_url" {
  description = "Push images here before the first apply / after each code change -- see README.md."
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "redis_endpoint" {
  description = "ElastiCache Redis endpoint (informational -- already wired into both task definitions as REDIS_URL)."
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}
