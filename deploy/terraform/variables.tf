variable "project_name" {
  description = "Used as a prefix for all resource names"
  type        = string
  default     = "why-production-is-down"
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "VPC ID for Lambda and ElastiCache"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs (Lambda + ElastiCache must be in the same subnets)"
  type        = list(string)
}

variable "lambda_image_uri" {
  description = "ECR image URI for the Lambda function (e.g. 123456789.dkr.ecr.us-east-1.amazonaws.com/why-production-is-down:latest)"
  type        = string
}

# ── Secrets (supply via terraform.tfvars — never commit that file) ────────────

variable "anthropic_api_key" { type = string; sensitive = true }
variable "datadog_api_key"   { type = string; sensitive = true }
variable "datadog_app_key"   { type = string; sensitive = true }
variable "sentry_auth_token" { type = string; sensitive = true }
variable "sentry_org"        { type = string }
variable "sentry_project"    { type = string }
variable "github_token"      { type = string; sensitive = true }
variable "github_org"        { type = string }
variable "pagerduty_token"         { type = string; sensitive = true }
variable "pagerduty_webhook_secret" { type = string; sensitive = true }
variable "slack_bot_token"     { type = string; sensitive = true }
variable "slack_channel_id"    { type = string }
variable "slack_signing_secret" { type = string; sensitive = true }
variable "ses_from_email"      { type = string; default = "" }
variable "launchdarkly_api_key" { type = string; sensitive = true; default = "" }
variable "launchdarkly_env"     { type = string; default = "production" }
