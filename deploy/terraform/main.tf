/**
 * Agentix Platform — Terraform Module
 *
 * Provisions:
 *   - EKS cluster (or GKE / AKS via provider variable)
 *   - RDS PostgreSQL (standard/enterprise tier)
 *   - ElastiCache Redis (standard/enterprise tier)
 *   - ECR repository
 *   - IAM roles + policies
 *   - Secrets Manager entries
 *
 * Usage:
 *   terraform init
 *   terraform apply -var-file=environments/prod.tfvars
 */

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.0"
    }
  }
  backend "s3" {
    bucket = "agentix-terraform-state"
    key    = "agentix/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "aws_region"      { default = "us-east-1" }
variable "environment"     { default = "production" }
variable "infra_tier"      { default = "standard" }   # lite | standard | enterprise
variable "cluster_name"    { default = "agentix-cluster" }
variable "db_instance_class" { default = "db.t3.medium" }
variable "redis_node_type"   { default = "cache.t3.micro" }
variable "agentix_version"   { default = "4.0.0" }

# ---------------------------------------------------------------------------
# ECR Repository
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "agentix" {
  name                 = "agentix"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  tags = { Environment = var.environment }
}

# ---------------------------------------------------------------------------
# Secrets Manager
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name = "agentix/${var.environment}/anthropic-api-key"
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name = "agentix/${var.environment}/jwt-secret"
}

resource "aws_secretsmanager_secret" "audit_hmac_secret" {
  name = "agentix/${var.environment}/audit-hmac-secret"
}

# ---------------------------------------------------------------------------
# VPC (simplified — use your existing VPC module in practice)
# ---------------------------------------------------------------------------

resource "aws_vpc" "agentix" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = { Name = "agentix-${var.environment}" }
}

# ---------------------------------------------------------------------------
# RDS PostgreSQL (standard/enterprise tier)
# ---------------------------------------------------------------------------

resource "aws_db_instance" "agentix" {
  count                = var.infra_tier != "lite" ? 1 : 0
  identifier           = "agentix-${var.environment}"
  engine               = "postgres"
  engine_version       = "15"
  instance_class       = var.db_instance_class
  db_name              = "agentix"
  username             = "agentix"
  manage_master_user_password = true
  skip_final_snapshot  = var.environment != "production"
  deletion_protection  = var.environment == "production"
  storage_encrypted    = true
  backup_retention_period = 7

  tags = { Environment = var.environment }
}

# ---------------------------------------------------------------------------
# ElastiCache Redis (standard/enterprise tier)
# ---------------------------------------------------------------------------

resource "aws_elasticache_cluster" "agentix" {
  count                = var.infra_tier != "lite" ? 1 : 0
  cluster_id           = "agentix-${var.environment}"
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.0"
  port                 = 6379

  tags = { Environment = var.environment }
}

# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

output "ecr_repository_url" {
  value = aws_ecr_repository.agentix.repository_url
}

output "db_endpoint" {
  value     = var.infra_tier != "lite" ? aws_db_instance.agentix[0].endpoint : null
  sensitive = true
}

output "redis_endpoint" {
  value     = var.infra_tier != "lite" ? aws_elasticache_cluster.agentix[0].cache_nodes[0].address : null
  sensitive = true
}
