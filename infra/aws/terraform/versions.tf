terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # No backend block on purpose: this is a portability proof (Sprint 10),
  # not the project's committed production state store. Whoever runs this
  # for real should configure a remote backend (S3 + DynamoDB lock table)
  # before the first `apply` -- see README.md "Before you apply".
}
