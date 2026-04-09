# Setup Guide

## Prerequisites

- Python 3.12+
- Docker
- AWS CLI (for production deployment)
- Terraform >= 1.5 (for production deployment)

---

## Local Development

### 1. Install dependencies

```bash
pip install -e ".[dev]"
```

### 2. Set environment variables

Create a `.env` file or export these in your shell:

```bash
# Required
ANTHROPIC_API_KEY=sk-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_CHANNEL_ID=C...
PAGERDUTY_TOKEN=...
PAGERDUTY_WEBHOOK_SECRET=...
GITHUB_TOKEN=...
GITHUB_ORG=your-org

# Optional integrations
DATADOG_API_KEY=...
DATADOG_APP_KEY=...
SENTRY_AUTH_TOKEN=...
SENTRY_ORG=your-org
SENTRY_PROJECT=your-project
LAUNCHDARKLY_API_KEY=...
LAUNCHDARKLY_ENV=production

# Skip real API calls during development
MOCK_MODE=true
```

### 3. Run the server

```bash
uvicorn src.server.webhook:app --reload --port 8000
```

Server starts at `http://localhost:8000`. Available webhook endpoints:

- `POST /webhook/pagerduty`
- `POST /webhook/datadog`
- `POST /webhook/slack/interactive`
- `GET  /health`
- `GET  /report/weekly`

### 4. Run tests

```bash
pytest tests/ -q
```

CI runs with `MOCK_MODE=true` so no real API keys are needed for tests.

---

## Docker (local)

```bash
# Build
docker build -f deploy/Dockerfile -t why-production-is-down .

# Run
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-... \
  -e SLACK_BOT_TOKEN=xoxb-... \
  -e SLACK_SIGNING_SECRET=... \
  -e SLACK_CHANNEL_ID=C... \
  -e PAGERDUTY_TOKEN=... \
  -e PAGERDUTY_WEBHOOK_SECRET=... \
  -e GITHUB_TOKEN=... \
  -e GITHUB_ORG=your-org \
  why-production-is-down
```

---

## Production Deployment (AWS Lambda)

The production stack runs on **AWS Lambda + API Gateway**, with **ElastiCache Redis** for dedup state and **SQS** for alert buffering. Terraform manages all infrastructure; GitHub Actions handles CI/CD.

### AWS prerequisites

Before running Terraform you need:

- An **ECR repository** to store the Docker image
- A **VPC** with private subnets (Lambda and ElastiCache share these)
- **SES** sender email verified in your AWS account (for `ses_from_email`)

### Step 1 — Build and push the Lambda image

```bash
# Authenticate to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <ECR_REGISTRY>

# Build the Lambda image
docker build -f deploy/Dockerfile.lambda \
  -t <ECR_REGISTRY>/why-production-is-down:latest .

# Push
docker push <ECR_REGISTRY>/why-production-is-down:latest
```

### Step 2 — Create terraform.tfvars

```bash
cd deploy/terraform
```

Create `terraform.tfvars` — **never commit this file**:

```hcl
# Infrastructure
vpc_id             = "vpc-..."
private_subnet_ids = ["subnet-...", "subnet-..."]
lambda_image_uri   = "<ECR_REGISTRY>/why-production-is-down:latest"

# AWS
aws_region    = "us-east-1"
ses_from_email = "alerts@yourdomain.com"

# Anthropic
anthropic_api_key = "sk-..."

# Slack
slack_bot_token      = "xoxb-..."
slack_signing_secret = "..."
slack_channel_id     = "C..."

# PagerDuty
pagerduty_token          = "..."
pagerduty_webhook_secret = "..."

# GitHub
github_token = "ghp_..."
github_org   = "your-org"

# Datadog
datadog_api_key = "..."
datadog_app_key = "..."

# Sentry
sentry_auth_token = "..."
sentry_org        = "your-org"
sentry_project    = "your-project"

# LaunchDarkly (optional)
launchdarkly_api_key = ""
launchdarkly_env     = "production"
```

### Step 3 — Apply Terraform

```bash
terraform init
terraform plan
terraform apply
```

This provisions:

| Resource | Details |
|---|---|
| Lambda | 512 MB memory, 120s timeout, container image |
| API Gateway | HTTP API, `prod` stage, auto-deploy |
| ElastiCache Redis | `cache.t4g.micro`, TLS enabled, private subnet |
| SQS queue | 1hr retention, alert storm buffer |
| S3 bucket | Fallback report storage, 90-day lifecycle |
| IAM role | Least-privilege policy for Lambda |

### Step 4 — Configure webhook URLs

After `terraform apply` completes, run:

```bash
terraform output
```

Use the printed URLs to configure your integrations:

| Service | Setting | URL |
|---|---|---|
| PagerDuty | Extensions → Generic Webhook | `webhook_base_url/webhook/pagerduty` |
| Datadog | Integrations → Webhooks | `webhook_base_url/webhook/datadog` |
| Slack | Interactivity → Request URL | `webhook_base_url/webhook/slack/interactive` |

### Step 5 — CI/CD via GitHub Actions

Every push to `main` that passes CI automatically builds and deploys the Lambda image.

Add these secrets to your GitHub repo under **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key |
| `AWS_REGION` | e.g. `us-east-1` |
| `ECR_REPOSITORY` | ECR repo name (not the full URI) |
| `LAMBDA_FUNCTION_NAME` | `why-production-is-down` |

CI (lint + tests) runs on every PR and push to `main`. Deployment only triggers when CI passes on `main`.

---

## Notes

- **SQLite on Lambda** — the Lambda uses `/tmp/incidents.db` which is ephemeral and not shared across invocations. This is fine for the webhook handler but means the REST API endpoints (`/api/incidents/*`) need a persistent store for production use.
- **Redis TLS** — the Terraform config enables transit encryption on ElastiCache. The Lambda connects via `rediss://` (note the double `s`).
- **Rollback** — the deploy workflow also tags each image as `latest` in ECR, making it easy to revert by updating the Lambda image URI to a prior SHA tag.
