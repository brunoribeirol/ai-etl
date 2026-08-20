# Reuses the account's default VPC/subnets rather than provisioning a
# dedicated network. Deliberate scope cut for a portability *proof*: this
# stack exists to show the same Docker image runs on AWS, not to design
# production network topology (private subnets + NAT gateway would be the
# real next step -- see README.md "Known simplifications").

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_caller_identity" "current" {}
