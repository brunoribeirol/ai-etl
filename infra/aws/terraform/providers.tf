provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ai-etl"
      Environment = var.environment
      ManagedBy   = "terraform"
      Sprint      = "10-multi-cloud-portability"
    }
  }
}
