terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── Data sources ─────────────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

# ── S3 fallback bucket ───────────────────────────────────────────────────────

resource "aws_s3_bucket" "fallback" {
  bucket = "${var.project_name}-fallback-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_lifecycle_configuration" "fallback" {
  bucket = aws_s3_bucket.fallback.id
  rule {
    id     = "expire-old-reports"
    status = "Enabled"
    expiration { days = 90 }
  }
}

resource "aws_s3_bucket_public_access_block" "fallback" {
  bucket                  = aws_s3_bucket.fallback.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── SQS alert storm buffer ───────────────────────────────────────────────────

resource "aws_sqs_queue" "alerts" {
  name                       = "${var.project_name}-alerts"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 3600   # 1 hour — storm alerts older than this are irrelevant
  receive_wait_time_seconds  = 5

  tags = { Project = var.project_name }
}

# ── ElastiCache Redis (dedup + in-flight state) ───────────────────────────────

resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.project_name}-redis-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "redis" {
  name        = "${var.project_name}-redis"
  description = "Allow Lambda to reach Redis"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.lambda.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.project_name}-redis"
  description          = "Dedup and in-flight state for incident agent"
  node_type            = "cache.t4g.micro"
  num_cache_clusters   = 1
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  tags = { Project = var.project_name }
}

# ── IAM role for Lambda ───────────────────────────────────────────────────────

resource "aws_iam_role" "lambda" {
  name = "${var.project_name}-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.alerts.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.fallback.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricData", "logs:FilterLogEvents", "logs:DescribeLogGroups"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["rds:DescribeDBInstances"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface",
        ]
        Resource = "*"
      },
    ]
  })
}

# ── Lambda security group ─────────────────────────────────────────────────────

resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-lambda"
  description = "Outbound access for incident agent Lambda"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── Lambda function ───────────────────────────────────────────────────────────

resource "aws_lambda_function" "agent" {
  function_name = var.project_name
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = var.lambda_image_uri
  timeout       = 120   # investigation_timeout(90) + buffer
  memory_size   = 512

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      MOCK_MODE                   = "false"
      REDIS_URL                   = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379"
      SQS_QUEUE_URL               = aws_sqs_queue.alerts.url
      S3_FALLBACK_BUCKET          = aws_s3_bucket.fallback.bucket
      AWS_REGION                  = var.aws_region
      INVESTIGATION_TIMEOUT_SECONDS = "90"
      DB_PATH                     = "/tmp/incidents.db"

      # Secrets injected at deploy time via terraform.tfvars (not committed)
      ANTHROPIC_API_KEY           = var.anthropic_api_key
      DATADOG_API_KEY             = var.datadog_api_key
      DATADOG_APP_KEY             = var.datadog_app_key
      SENTRY_AUTH_TOKEN           = var.sentry_auth_token
      SENTRY_ORG                  = var.sentry_org
      SENTRY_PROJECT              = var.sentry_project
      GITHUB_TOKEN                = var.github_token
      GITHUB_ORG                  = var.github_org
      PAGERDUTY_TOKEN             = var.pagerduty_token
      PAGERDUTY_WEBHOOK_SECRET    = var.pagerduty_webhook_secret
      SLACK_BOT_TOKEN             = var.slack_bot_token
      SLACK_CHANNEL_ID            = var.slack_channel_id
      SLACK_SIGNING_SECRET        = var.slack_signing_secret
      SES_FROM_EMAIL              = var.ses_from_email
      LAUNCHDARKLY_API_KEY        = var.launchdarkly_api_key
      LAUNCHDARKLY_ENV            = var.launchdarkly_env
    }
  }

  tags = { Project = var.project_name }
}

# ── API Gateway (HTTP API) ────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "webhooks" {
  name          = "${var.project_name}-webhooks"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.webhooks.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.agent.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "pagerduty" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /webhook/pagerduty"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "datadog" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /webhook/datadog"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "slack_interactive" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "POST /webhook/slack/interactive"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "weekly_report" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "GET /report/weekly"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "health" {
  api_id    = aws_apigatewayv2_api.webhooks.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "prod" {
  api_id      = aws_apigatewayv2_api.webhooks.id
  name        = "prod"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.webhooks.execution_arn}/*/*"
}
