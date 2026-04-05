from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Datadog
    datadog_api_key: str = ""
    datadog_app_key: str = ""
    datadog_site: str = "datadoghq.com"

    # Sentry
    sentry_auth_token: str = ""
    sentry_org: str = ""
    sentry_project: str = ""

    # GitHub
    github_token: str = ""
    github_org: str = ""

    # PagerDuty
    pagerduty_token: str = ""
    pagerduty_webhook_secret: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_channel_id: str = ""
    slack_signing_secret: str = ""

    # AWS
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    sqs_queue_url: str = ""
    s3_fallback_bucket: str = ""
    ses_from_email: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Agent behavior
    mock_mode: bool = True
    investigation_window_minutes: int = 30
    max_log_lines: int = 200
    max_diff_lines: int = 300
    adapter_timeout_seconds: int = 10
    investigation_timeout_seconds: int = 90

    # Anthropic
    anthropic_api_key: str = ""

    # Persistence
    db_path: str = "data/incidents.db"


settings = Settings()
