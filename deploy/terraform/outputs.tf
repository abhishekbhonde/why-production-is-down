output "webhook_base_url" {
  description = "Base URL for all webhooks — configure this in PagerDuty, Datadog, and Slack"
  value       = "${aws_apigatewayv2_stage.prod.invoke_url}"
}

output "pagerduty_webhook_url" {
  description = "POST this URL in PagerDuty → Extensions → Generic Webhook"
  value       = "${aws_apigatewayv2_stage.prod.invoke_url}/webhook/pagerduty"
}

output "datadog_webhook_url" {
  description = "POST this URL in Datadog → Integrations → Webhooks"
  value       = "${aws_apigatewayv2_stage.prod.invoke_url}/webhook/datadog"
}

output "slack_interactive_url" {
  description = "POST this URL in Slack App → Interactivity & Shortcuts → Request URL"
  value       = "${aws_apigatewayv2_stage.prod.invoke_url}/webhook/slack/interactive"
}

output "weekly_report_url" {
  description = "GET to retrieve the 7-day accuracy report"
  value       = "${aws_apigatewayv2_stage.prod.invoke_url}/report/weekly"
}

output "sqs_queue_url" {
  description = "SQS queue URL (also set in Lambda env automatically)"
  value       = aws_sqs_queue.alerts.url
}

output "s3_fallback_bucket" {
  description = "S3 bucket name for fallback report storage"
  value       = aws_s3_bucket.fallback.bucket
}

output "redis_endpoint" {
  description = "ElastiCache primary endpoint (TLS)"
  value       = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379"
  sensitive   = true
}
