# How to Use "Why Is Production Down?" — Complete Guide

This guide teaches you everything you need to know to set up, run, and get value out of this agent in a real production environment. No prior knowledge of the codebase is assumed.

---

## Table of Contents

1. [What Does This Agent Do?](#1-what-does-this-agent-do)
2. [How It Works — The Full Picture](#2-how-it-works--the-full-picture)
3. [What You Need Before Starting](#3-what-you-need-before-starting)
4. [Step 1 — Install the Project](#step-1--install-the-project)
5. [Step 2 — Configure Your Environment](#step-2--configure-your-environment)
6. [Step 3 — Run in Mock Mode First](#step-3--run-in-mock-mode-first)
7. [Step 4 — Connect Real Integrations](#step-4--connect-real-integrations)
8. [Step 5 — Deploy to Production](#step-5--deploy-to-production)
9. [Step 6 — Wire Up Your Alert Sources](#step-6--wire-up-your-alert-sources)
10. [What Happens During an Incident](#what-happens-during-an-incident)
11. [Reading the Slack Report](#reading-the-slack-report)
12. [The Rollback Button](#the-rollback-button)
13. [Giving Feedback to Improve Accuracy](#giving-feedback-to-improve-accuracy)
14. [Checking the Weekly Accuracy Report](#checking-the-weekly-accuracy-report)
15. [Troubleshooting Common Problems](#troubleshooting-common-problems)

---

## 1. What Does This Agent Do?

At 3am, when your monitoring fires an alert, the normal workflow looks like this:

1. You get paged
2. You spend 20–45 minutes jumping between Datadog, Sentry, CloudWatch, GitHub, and Slack trying to piece together what happened
3. You eventually find the cause and fix it

**This agent replaces steps 1–3 with a single Slack message.**

The moment an alert fires, the agent automatically:

- Reads your Datadog metrics and APM data
- Reads your Sentry error groups and stack traces
- Reads your CloudWatch and application logs
- Looks at every deploy in the last 30 minutes on GitHub
- Checks your database health (RDS)
- Checks if any feature flags changed (LaunchDarkly)
- Correlates all of this into a single timeline
- Identifies the most likely root cause
- Posts a structured report to Slack with a confidence level, the specific culprit, and a recommended action

You wake up knowing exactly what to fix. If the culprit was a deploy, you can roll it back in one click from Slack without touching GitHub.

### What it does NOT do

- It does **not** fix anything automatically. Humans still review and approve every action.
- It does **not** page you in addition to your existing alerting — it attaches to your existing PagerDuty or Datadog alerts.
- It does **not** require you to change your existing monitoring setup. It reads from the tools you already use.

---

## 2. How It Works — The Full Picture

Here is the exact sequence of events from alert to Slack message:

```
1. Alert fires in PagerDuty or Datadog
         │
         ▼
2. Webhook arrives at this server (POST /webhook/pagerduty or /webhook/datadog)
         │
         ├─ Signature validated (HMAC check)
         ├─ Duplicate check via Redis — same service within 5 min = skip
         └─ If an alert storm is happening (>1 alert/min), buffer via SQS
         │
         ▼
3. Agent fans out in parallel to all data sources:
   ├── Datadog  — error rate metrics, APM latency, log search
   ├── Sentry   — error groups, first-seen timestamps, stack traces
   ├── CloudWatch — application logs (ECS/Lambda), alarms
   ├── GitHub   — recent deploys, commit diffs, PR metadata
   ├── RDS      — DB connection count, CPU, slow queries
   └── LaunchDarkly — feature flag changes in the window
         │
         ▼
4. Timeline is correlated across all sources (earliest event first)
         │
         ▼
5. Past mistakes are loaded from the database
   (e.g. "deploy was incorrectly blamed 4 times — be cautious")
         │
         ▼
6. Everything is passed to Claude (claude-opus-4-6) with:
   - The correlated timeline
   - All raw data from adapters
   - Hypothesis priority order to follow
   - Past mistakes to avoid
         │
         ▼
7. Claude produces a structured JSON report:
   root_cause, confidence, culprit (type + detail + diff URL),
   affected services, recommended action
         │
         ▼
8. Report is saved to SQLite database
         │
         ▼
9. PagerDuty incident is annotated with the root cause summary
         │
         ▼
10. Slack report is posted to your channel with:
    - Root cause and confidence level
    - Culprit details and diff link
    - Thumbs up / thumbs down feedback buttons
    - "Roll back deploy" button (only if HIGH confidence deploy culprit)
```

If Slack is unreachable, the report is written to S3 and an email is sent via AWS SES.

---

## 3. What You Need Before Starting

Before you run a single command, make sure you have:

| Requirement | Why you need it |
|-------------|----------------|
| Python 3.12+ | The runtime |
| Redis 7.0+ | Duplicate alert prevention and state tracking |
| Anthropic API key | Powers the AI reasoning (Claude) |
| Datadog API + App key | Reads metrics and logs |
| Sentry auth token | Reads error groups and releases |
| GitHub personal access token (repo read scope) | Reads deploys and diffs |
| PagerDuty API token + webhook secret | Receives alerts and annotates incidents |
| Slack bot token + signing secret | Posts reports and receives button clicks |
| AWS account (optional for full production setup) | CloudWatch, SQS, S3, SES |
| LaunchDarkly API key (optional) | Feature flag change correlation |

You can start with just the Anthropic key and one or two integrations. Each adapter fails independently — missing keys simply mean that source is skipped.

---

## Step 1 — Install the Project

```bash
# Clone the repository
git clone https://github.com/abhishekbhonde/why-production-is-down.git
cd why-production-is-down

# Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate        # On Windows: .venv\Scripts\activate

# Install the project and all dependencies
pip install -e .

# Install development tools (for running tests)
pip install -e ".[dev]"
```

Verify the install worked:

```bash
python -c "from src.agent.orchestrator import Orchestrator; print('OK')"
```

---

## Step 2 — Configure Your Environment

Copy the example config file:

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in the values you have. Here is what each variable does:

### Anthropic (required)

```env
ANTHROPIC_API_KEY=sk-ant-...
```

Get this from https://console.anthropic.com → API Keys. This is the only truly required key — without it, the agent cannot reason about the data it collects.

### Datadog

```env
DATADOG_API_KEY=...          # From Datadog → Organization Settings → API Keys
DATADOG_APP_KEY=...          # From Datadog → Organization Settings → Application Keys
DATADOG_SITE=datadoghq.com   # Use datadoghq.eu if you're on the EU region
```

The agent uses these to query error rate metrics, APM latency, and search logs for the service that triggered the alert.

### Sentry

```env
SENTRY_AUTH_TOKEN=...        # From Sentry → Settings → Auth Tokens
SENTRY_ORG=your-org-slug     # The slug shown in your Sentry URL: sentry.io/organizations/YOUR-SLUG
SENTRY_PROJECT=your-project  # The project slug to query
```

### GitHub

```env
GITHUB_TOKEN=ghp_...         # Personal access token with repo:read scope
GITHUB_ORG=your-org-name     # Your GitHub organization name
```

The agent uses this to list recent deployments and fetch commit diffs to find what changed.

### PagerDuty

```env
PAGERDUTY_TOKEN=...                  # From PagerDuty → Integrations → API Access Keys
PAGERDUTY_WEBHOOK_SECRET=...         # You set this when creating the webhook (see Step 6)
```

### Slack

```env
SLACK_BOT_TOKEN=xoxb-...             # From your Slack app → OAuth & Permissions
SLACK_CHANNEL_ID=C0123456789         # The channel ID where reports should be posted
SLACK_SIGNING_SECRET=...             # From your Slack app → Basic Information
```

To find a channel ID: right-click the channel in Slack → View channel details → scroll to the bottom.

### AWS (needed for CloudWatch, SQS buffering, S3 fallback, email fallback)

```env
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/123456789/your-queue
S3_FALLBACK_BUCKET=your-bucket-name
SES_FROM_EMAIL=alerts@yourdomain.com
```

If you skip these, the agent works fine — it just won't read CloudWatch logs, won't buffer alert storms via SQS, and if Slack is down it will log an error instead of falling back to S3/email.

### Redis

```env
REDIS_URL=redis://localhost:6379
```

Start Redis locally:
```bash
# macOS
brew install redis && brew services start redis

# Linux
sudo apt install redis-server && sudo systemctl start redis
```

### Agent behaviour (optional tuning)

```env
MOCK_MODE=false                      # Set to true to use fixture data (no real API calls)
INVESTIGATION_WINDOW_MINUTES=30      # How far back to look from the alert time
MAX_LOG_LINES=200                    # Max log lines sent to Claude per query
MAX_DIFF_LINES=300                   # Max diff lines sent per commit
ADAPTER_TIMEOUT_SECONDS=10           # Per-source timeout before skipping
INVESTIGATION_TIMEOUT_SECONDS=90     # Hard limit on the full investigation
```

---

## Step 3 — Run in Mock Mode First

Before connecting real APIs, run the agent with fake fixture data to verify everything is wired up correctly.

```bash
# Set mock mode in your .env
echo "MOCK_MODE=true" >> .env

# Start the server
uvicorn src.server.webhook:app --reload
```

You should see:

```
INFO:     Started server process
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

Now send a fake PagerDuty alert:

```bash
curl -X POST http://localhost:8000/webhook/pagerduty \
  -H "Content-Type: application/json" \
  -d '{
    "event": {
      "event_type": "incident.triggered",
      "data": {
        "id": "INC001",
        "title": "High error rate on payment-service",
        "service": {"name": "payment-service"},
        "created_at": "2024-01-15T02:51:00Z"
      }
    }
  }'
```

Expected response:
```json
{"status": "accepted", "service": "payment-service"}
```

In the server logs you will see the agent run through all the adapters, make the Claude API call, and print the Slack report it would have sent. This confirms the full flow works end-to-end.

Check the health endpoint to confirm the server is running:

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Step 4 — Connect Real Integrations

Once mock mode works, fill in your real API keys in `.env` and set `MOCK_MODE=false`.

Restart the server:

```bash
uvicorn src.server.webhook:app --reload
```

Test each integration one at a time by sending a test alert and watching the logs. The logs tell you which adapters succeeded and which timed out or errored:

```
INFO  Datadog adapter fetched 47 data points for payment-service
INFO  Sentry adapter fetched 3 error groups for payment-service
WARN  CloudWatch adapter timed out after 10s — skipping
INFO  GitHub adapter fetched 2 deployments for payment-service
INFO  RDS adapter fetched DB health metrics
INFO  Investigation complete — tokens: 9243 in / 612 out, cost: $0.0323, elapsed: 24.1s
```

Each adapter that fails is listed in the Slack report under "Sources unavailable" so the reader knows what data was missing.

---

## Step 5 — Deploy to Production

You have two options: Docker (recommended for most teams) or AWS Lambda.

### Option A — Docker (Fly.io, ECS, any container host)

```bash
# Build the image
docker build -f deploy/Dockerfile -t why-production-is-down .

# Run it (pass your .env file)
docker run -p 8000:8000 --env-file .env why-production-is-down
```

The server listens on port 8000. Put it behind a load balancer or reverse proxy with HTTPS — PagerDuty and Slack require HTTPS for webhooks.

**Deploy to Fly.io:**

```bash
fly launch --name why-production-is-down --dockerfile deploy/Dockerfile
fly secrets import < .env
fly deploy
```

Your webhook URL will be: `https://why-production-is-down.fly.dev`

### Option B — AWS Lambda

```bash
# Build the Lambda container image
docker build -f deploy/Dockerfile.lambda -t why-production-is-down-lambda .

# Push to ECR and deploy via Terraform
cd deploy/terraform
terraform init
terraform apply
```

The Lambda is invoked via API Gateway. The Terraform config creates the Lambda, API Gateway, SQS queue, and the required IAM roles.

---

## Step 6 — Wire Up Your Alert Sources

This is the step that makes it automatic. You need to point your alerting tools at the server's webhook endpoints.

### PagerDuty

1. Go to **PagerDuty → Services → Your Service → Integrations**
2. Add a **Generic Webhook (V3)** integration
3. Set the URL to: `https://your-server.com/webhook/pagerduty`
4. Copy the **webhook secret** and put it in your `.env` as `PAGERDUTY_WEBHOOK_SECRET`
5. Set the events to trigger on: `incident.triggered` and `incident.acknowledged`

From now on, every time an incident triggers in PagerDuty, the agent runs automatically.

### Datadog

1. Go to **Datadog → Integrations → Webhooks**
2. Create a new webhook
3. Set the URL to: `https://your-server.com/webhook/datadog`
4. In your monitor settings, add the webhook as a notification channel: `@webhook-your-webhook-name`

### Slack App Setup (required for the feedback buttons and rollback to work)

The agent needs an interactive Slack app — not just a bot token — so that button clicks are sent back to the server.

1. Go to https://api.slack.com/apps and create a new app
2. Under **OAuth & Permissions**, add these bot token scopes:
   - `chat:write` — post messages
   - `chat:write.public` — post to public channels
3. Under **Interactivity & Shortcuts**, enable interactivity and set the **Request URL** to:
   `https://your-server.com/webhook/slack/interactive`
4. Install the app to your workspace
5. Copy the **Bot User OAuth Token** → `SLACK_BOT_TOKEN`
6. Copy the **Signing Secret** (under Basic Information) → `SLACK_SIGNING_SECRET`

Without the interactive URL configured, the thumbs up/down buttons and the rollback button will appear in Slack but clicks won't reach the server.

---

## What Happens During an Incident

Here is the exact experience once everything is set up:

1. Your service starts throwing errors
2. Your existing monitoring (Datadog/PagerDuty) fires an alert as it normally would
3. **Within seconds**, the agent receives the webhook, starts the investigation in the background, and acknowledges the webhook
4. **Within 90 seconds**, a Slack message appears in your channel
5. You open Slack and see the full analysis — no terminal, no dashboards

You are now in the loop before you even have time to open your laptop.

---

## Reading the Slack Report

The Slack report looks like this:

```
┌─────────────────────────────────────────────────┐
│  Incident Report: payment-service               │
├─────────────────────────────────────────────────┤
│  Service          │  Confidence                 │
│  payment-service  │  🔴 HIGH                    │
│                   │                             │
│  First Failure    │  Investigation Time         │
│  02:47:03 UTC     │  31.4s                      │
├─────────────────────────────────────────────────┤
│  Root Cause                                     │
│  HTTP 500 error rate spiked from 0.1% → 34%    │
│  on POST /webhooks/stripe, starting 4 minutes   │
│  after deploy #892.                             │
├─────────────────────────────────────────────────┤
│  Recommended Action                             │
│  Roll back deploy #892 or revert the Stripe     │
│  webhook header name change.                    │
├─────────────────────────────────────────────────┤
│  Culprit                                        │
│  Type: deploy                                   │
│  Detail: Deploy #892 by @jsmith at 02:43 UTC   │
│  View diff →                                    │
├─────────────────────────────────────────────────┤
│  [👍 Correct]  [👎 Incorrect]  [⏪ Roll back]  │
└─────────────────────────────────────────────────┘
```

### What each field means

| Field | What to look at |
|-------|----------------|
| **Confidence** | RED = HIGH (strong evidence), YELLOW = MEDIUM (likely but not certain), WHITE = LOW (best guess), GREY = UNKNOWN (no signal found) |
| **Root Cause** | One sentence describing what broke and how it manifested |
| **First Failure** | The timestamp of the *earliest* anomaly signal — this may be earlier than when the alert fired |
| **Culprit** | The specific thing that caused the failure: a deploy SHA, a feature flag key, a database metric, etc. |
| **View diff** | A direct link to the GitHub diff for the culprit deploy |
| **Investigation Time** | How long the agent took to run the full analysis |

### Understanding confidence levels

- **HIGH** — Matching timing AND a clear causal mechanism (e.g. deploy touched the exact code path that's failing). Act on this immediately.
- **MEDIUM** — Timing matches but mechanism is inferred, not proven (e.g. a deploy happened but the diff doesn't obviously explain the error). Investigate further before rolling back.
- **LOW** — Some correlation but weak. Use as a starting point for manual investigation.
- **UNKNOWN** — No correlating event found in the 2-hour window. The agent could not find a cause. This needs a human.

### When sources are unavailable

If a section says "⚠️ Sources unavailable: cloudwatch, rds" it means those adapters timed out. The report is based on the data that was available. The confidence level accounts for missing sources.

---

## The Rollback Button

The "Roll back deploy" button appears only when:
- Confidence is **HIGH**
- The culprit type is **deploy**
- The culprit deploy has a parseable GitHub diff URL

When you click it:

1. Slack shows a **confirmation dialog**: "This will open a draft PR on GitHub that reverts the identified deploy. You still need to review and merge it."
2. You click **"Yes, create PR"**
3. The agent creates a branch at the parent commit (the last good state), opens a **draft PR** titled `revert: <original commit message>`, and posts the PR URL back into the Slack thread as a reply
4. The PagerDuty incident is annotated with the PR URL
5. Your engineers review the PR and merge it to complete the rollback

**Nothing is merged automatically.** The agent creates the PR; a human merges it.

If newer commits have landed on the branch since the bad deploy, the PR body will warn you: "Note: 3 commits have landed since this deploy. Review carefully before merging." This prevents accidentally rolling back unrelated work.

---

## Giving Feedback to Improve Accuracy

After each investigation, you'll see **👍 Correct** and **👎 Incorrect** buttons in the Slack report.

- Click **👍 Correct** if the agent correctly identified the root cause
- Click **👎 Incorrect** if the agent was wrong

This feedback is stored in the local SQLite database. The agent reads it before every future investigation. If a certain type of culprit (e.g. "deploy") has been incorrectly blamed 3 or more times in the last 30 days, the agent injects a warning into its reasoning:

> "deploy was incorrectly identified 4 time(s) — be extra cautious before blaming this type."

Over time this makes the agent progressively more accurate for your specific services and failure patterns.

---

## Checking the Weekly Accuracy Report

The agent tracks how often it got the root cause right. Check the weekly stats:

```bash
curl http://localhost:8000/report/weekly | python -m json.tool
```

Example output:

```json
{
  "period": "last_7_days",
  "total_investigations": 12,
  "with_feedback": 9,
  "correct": 7,
  "incorrect": 2,
  "accuracy_pct": 77.8,
  "by_confidence": {
    "HIGH":   {"correct": 5, "incorrect": 0},
    "MEDIUM": {"correct": 2, "incorrect": 1},
    "LOW":    {"correct": 0, "incorrect": 1}
  },
  "total_cost_usd": 0.3821,
  "total_input_tokens": 87420,
  "total_output_tokens": 5940
}
```

This tells you:
- **accuracy_pct** — how often the agent was right (across investigations where you gave feedback)
- **by_confidence** — HIGH confidence is most reliable; LOW confidence should always be verified manually
- **total_cost_usd** — what the AI API calls cost this week (typically $2–5/month at 50 incidents)

---

## Troubleshooting Common Problems

### The server starts but investigating returns an error

Check that `ANTHROPIC_API_KEY` is set and valid. The AI call is the last step — if everything else works but you see an error in the logs mentioning `anthropic`, the API key is the issue.

### Slack messages are not appearing

1. Confirm `SLACK_BOT_TOKEN` starts with `xoxb-`
2. Confirm `SLACK_CHANNEL_ID` is the channel **ID** (starts with `C`), not the channel name
3. Make sure the bot has been invited to the channel: in Slack, type `/invite @your-bot-name` in the channel

### Button clicks in Slack do nothing

The Slack app's **Interactivity Request URL** must point to your server's `/webhook/slack/interactive` endpoint. This URL must be publicly reachable (not `localhost`). Use a tool like `ngrok` during local development:

```bash
ngrok http 8000
# Copy the https URL and set it in your Slack app's Interactivity settings
```

### PagerDuty webhooks return 401

The `PAGERDUTY_WEBHOOK_SECRET` in your `.env` must match the secret you set when creating the PagerDuty webhook. They are case-sensitive. If you're not sure, leave `PAGERDUTY_WEBHOOK_SECRET` empty to skip signature validation (only safe in development).

### Investigations always time out

The default timeout is 90 seconds. If your adapters are slow (common with CloudWatch), you can raise the timeout:

```env
INVESTIGATION_TIMEOUT_SECONDS=120
ADAPTER_TIMEOUT_SECONDS=15
```

### Redis connection refused

Start Redis: `redis-server` (or `brew services start redis` on macOS). If Redis is unavailable, duplicate detection is skipped and the investigation runs anyway — a warning is added to the report.

### "No signal found" — investigation window too narrow

If the incident started slowly (e.g. a memory leak that took an hour to trigger), the 30-minute window may miss the root cause. You can widen the default window:

```env
INVESTIGATION_WINDOW_MINUTES=60
```

The agent also automatically expands to 2 hours if the 30-minute window returns no data at all.

### Running tests

```bash
# Run all tests
pytest

# Run a specific test file
pytest tests/test_orchestrator.py -v

# Run with output for debugging
pytest -s
```

All tests run against fixtures — no real API keys needed for the test suite.

---

## Quick Reference

| Action | Command / URL |
|--------|--------------|
| Start server locally | `uvicorn src.server.webhook:app --reload` |
| Send a test alert | `curl -X POST http://localhost:8000/webhook/pagerduty -H "Content-Type: application/json" -d '{"event":{"event_type":"incident.triggered","data":{"id":"INC001","title":"High error rate","service":{"name":"payment-service"},"created_at":"2024-01-15T02:51:00Z"}}}'` |
| Check server health | `curl http://localhost:8000/health` |
| Get weekly accuracy | `curl http://localhost:8000/report/weekly` |
| Run tests | `pytest` |
| Build Docker image | `docker build -f deploy/Dockerfile -t why-production-is-down .` |
| Run Docker container | `docker run -p 8000:8000 --env-file .env why-production-is-down` |
