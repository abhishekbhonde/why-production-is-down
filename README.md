# Why Is Production Down?

An autonomous incident response agent that investigates production outages while you sleep.

## The Problem

It's 3am. An alert fires. Normally you spend 45 minutes jumping between Datadog, Sentry, CloudWatch, and Slack history trying to piece together what happened.

## The Solution

This agent does it automatically the moment an alert fires — it reads your logs, correlates the timeline, identifies the first failing service, traces it back to the specific deploy that caused it, and sends you one message:

> *"The payment service started throwing 500s at 2:47am, 4 minutes after deploy #892 which changed the Stripe webhook handler. Here's the diff."*

You wake up knowing exactly what to fix.

---

## How It Works

```
Alert fires (PagerDuty / Datadog webhook)
          │
          ▼
┌─────────────────────────────────────┐
│         Webhook Receiver            │
│  - Validates webhook signature      │
│  - Checks Redis for duplicate alert │◄── Redis (dedup + state)
│  - Drops if same incident < 5min    │
└────────────────┬────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────┐
│                    Agent Orchestrator                      │
│                                                            │
│  1. Parse alert: service name, severity, timestamp         │
│  2. Set investigation window: [T-30min → T+5min]          │
│     (expands to T-2hr if no signal found in initial pass) │
│  3. Fan out parallel data fetching:                        │
│     ├── Metrics & APM        → Datadog                     │
│     ├── Error groups         → Sentry                      │
│     ├── Application logs     → CloudWatch / Datadog Logs   │
│     ├── Recent deploys       → GitHub / CI pipeline        │
│     └── DB health            → RDS / CloudWatch metrics    │
│  4. Correlate timeline — find the earliest anomaly signal  │
│  5. Test hypotheses in priority order:                     │
│     a. Recent deploy within 30min of first failure?        │
│     b. Upstream service degraded at same time?             │
│     c. Database connection pool exhausted?                 │
│     d. Traffic spike / resource saturation?                │
│     e. Infrastructure event (AZ, spot termination)?        │
│  6. Fetch diff if deploy found; fetch dependency graph     │
│     if cascade failure suspected                           │
│  7. Produce structured root cause report                   │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│          Notification Hub           │
│  → Slack DM + channel alert         │
│  → PagerDuty incident note          │
│  → (Phase 3+) Jira ticket creation  │
│                                     │
│  Fallback: if Slack is unreachable, │
│  writes report to S3 + sends email  │
└─────────────────────────────────────┘
```

---

## Example Output

```
INCIDENT SUMMARY
─────────────────────────────────────────────────────────────
Service:        payment-service
First failure:  2024-01-15 02:47:03 UTC
Alert fired:    2024-01-15 02:51:00 UTC  (4 min detection lag)
Investigation:  completed in 58 seconds

Cause: HTTP 500 error rate spiked from 0.1% → 34% on POST /webhooks/stripe

Likely culprit: Deploy #892 by @jsmith at 02:43 UTC
  Commit: fix(stripe): update webhook signature validation
  Repo:   acme-corp/payment-service @ a3f8c21

Relevant diff:
  - const sig = req.headers['stripe-signature']
  + const sig = req.headers['x-stripe-signature']   ← wrong header name

Other services affected: checkout-service, order-service (downstream cascade)
DB health: normal  |  Upstream APIs: normal  |  Infrastructure: normal

Confidence: HIGH — deploy timing + diff explain the failure directly

Recommended action: Roll back deploy #892 or revert the header name change.
─────────────────────────────────────────────────────────────
```

---

## Edge Cases Handled

### No recent deploy found
If no deploy occurred within 30 minutes of the first failure, the agent shifts to checking:
- Upstream service health (did a dependency degrade first?)
- Database connection pool exhaustion or slow query explosion
- Infrastructure events (AZ outage, spot instance termination, autoscaling failure)
- Traffic pattern anomalies (sudden spike, DDoS-like pattern)
- Feature flag rollout timing (LaunchDarkly / custom flag systems)

The report will reflect low confidence and list the best available hypothesis.

### Cascade failure (multiple services failing simultaneously)
The agent identifies the **earliest failing service** by comparing first-error timestamps across all affected services. The root cause investigation focuses on that service, and downstream impact is listed separately.

### Flapping alerts (alert fires, clears, fires again)
Redis deduplication treats all alerts for the same service within a 5-minute window as one incident. A flapping alert does not trigger multiple parallel investigations. The agent notes the flap pattern in its report.

### Diff too large to be useful
When a deploy contains thousands of changed lines (e.g., a dependency upgrade or bulk rename), the agent flags this and focuses on files most likely related to the failure path based on the error stack trace.

### Investigation window too narrow
If the initial 30-minute window yields no signal, the window automatically expands to 2 hours. If still no signal, the report explicitly states "no correlating event found in 2-hour window" rather than guessing.

### Source unavailable during investigation
Each adapter has a 10-second timeout and fails independently. If Sentry is down, the investigation continues without it. The final report lists which sources were unreachable so the reader knows what data is missing.

### Slack unreachable during an incident
If Slack delivery fails, the report is written to an S3 bucket and a fallback email is sent via SES. The S3 object URL is included in the PagerDuty incident note.

### Redis unavailable
If Redis is unreachable at alert time, the deduplication step is skipped and the investigation runs anyway. A warning is added to the report. Duplicate investigations may occur during Redis outages.

### Alert storm (10+ alerts firing in under 60 seconds)
The agent processes the first alert immediately and queues the rest via SQS with a 60-second delay. If the incident is resolved before queued alerts are processed, they are dropped.

---

## Tech Stack

| Layer | Technology | Reason |
|-------|-----------|--------|
| Language | Python 3.12 | Async-first, rich API client ecosystem |
| Web framework | FastAPI | Async, low overhead, webhook signature validation |
| Task queue | SQS | Alert storm buffering, retry on failure |
| Dedup / state | Redis | Sub-millisecond dedup, TTL-based expiry |
| Metrics source | Datadog | APM, metrics, log search in one API |
| Error tracking | Sentry | Error groups, releases, stack traces |
| Log source | AWS CloudWatch | Lambda/ECS logs, RDS metrics |
| Deploy tracking | GitHub API | Commit diffs, tags, PR metadata |
| Alerting input | PagerDuty | Webhook trigger, incident annotation |
| Notification output | Slack | Incident report delivery |
| Fallback storage | AWS S3 | Report persistence when Slack is down |
| Deployment | AWS Lambda / Fly.io | Event-driven, scales to zero |

---

## Integrations

- **Datadog** — error rate metrics, APM traces, log search
- **Sentry** — error groups, stack traces, release and deploy tracking
- **AWS CloudWatch** — Lambda/ECS/RDS logs, alarms, and metric streams
- **AWS RDS** — database health, connection count, slow query metrics
- **GitHub** — commit diffs, deploy tags, pull request history
- **PagerDuty** — alert ingestion, incident annotations, on-call routing
- **Slack** — incident report delivery with thread replies for updates
- **AWS SQS** — alert buffering and retry
- **AWS S3** — fallback report storage
- **LaunchDarkly** *(Phase 2+)* — feature flag change history correlation

---

## Project Structure

```
why-production-is-down/
├── src/
│   ├── agent/
│   │   ├── orchestrator.py      # Core agent loop, hypothesis testing
│   │   ├── tools.py             # Tool definitions (one per data source)
│   │   └── prompts.py           # Investigation system prompt
│   ├── adapters/
│   │   ├── base.py              # Abstract adapter interface + timeout wrapper
│   │   ├── datadog.py           # Metrics, APM, logs
│   │   ├── sentry.py            # Error groups, releases
│   │   ├── cloudwatch.py        # AWS logs and alarms
│   │   ├── rds.py               # Database health metrics
│   │   ├── github.py            # Deploys, diffs, PR metadata
│   │   └── pagerduty.py         # Alert parsing, incident annotation
│   ├── notifiers/
│   │   ├── slack.py             # Primary: Slack report delivery
│   │   ├── email.py             # Fallback: SES email
│   │   └── s3.py                # Fallback: S3 report persistence
│   ├── server/
│   │   └── webhook.py           # FastAPI: PagerDuty + Datadog receivers
│   └── utils/
│       ├── dedup.py             # Redis deduplication logic
│       ├── timeline.py          # Cross-source event correlation
│       ├── truncate.py          # Log/diff size limiting before LLM call
│       └── rate_limit.py        # Per-adapter rate limit tracking
├── tests/
│   ├── fixtures/
│   │   ├── datadog_metrics.json
│   │   ├── sentry_errors.json
│   │   ├── github_deploys.json
│   │   └── cloudwatch_logs.json
│   ├── test_orchestrator.py
│   ├── test_adapters.py
│   ├── test_dedup.py
│   └── test_timeline.py
├── deploy/
│   ├── lambda_handler.py        # AWS Lambda entrypoint
│   ├── Dockerfile               # Fly.io / ECS container
│   └── terraform/               # Infrastructure as code (Lambda + SQS + Redis)
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Implementation Phases

### Phase 1 — Skeleton
- [x] FastAPI webhook receiver with PagerDuty and Datadog signature validation
- [x] Redis deduplication (same alert within 5 minutes = one investigation)
- [x] Agent loop with stubbed adapters returning fixture data
- [x] Hypothesis testing logic with prioritized root cause search
- [x] Slack output with structured report format

**Goal:** End-to-end flow works with fixture data. No real API calls.

### Phase 2 — Real Integrations
- [x] Live Datadog adapter: error rate metrics + log search
- [x] Live Sentry adapter: error groups + release tracking
- [x] Live GitHub adapter: deploy tags + commit diffs
- [x] Live CloudWatch adapter: ECS/Lambda logs + RDS metrics
- [x] Rate limit handling for all adapters (Datadog: 300 req/hr, GitHub: 5000 req/hr)
- [ ] Validated against 3 real past incidents in staging *(QA activity — requires live environment)*

**Goal:** Correctly identify root cause for real historical incidents.

### Phase 3 — Reliability
- [x] 90-second hard timeout on full investigation
- [x] Per-adapter 10-second timeout with graceful skip
- [x] Alert storm handling via SQS queue with 60-second delay
- [x] S3 + email fallback if Slack delivery fails
- [x] Investigation window auto-expansion (30min → 2hr) on no-signal
- [x] Cost tracking: log token count and estimated cost per investigation

**Goal:** Production-ready. Handles all edge cases without human intervention.

### Phase 4 — Learning
- [x] Persist `(investigation, outcome)` pairs to a database
- [x] "Was this correct?" Slack button (thumbs up / thumbs down)
- [x] Weekly accuracy report: % of incidents where root cause was correct
- [x] Tune investigation prompts based on systematic misses
- [x] Auto-annotate PagerDuty incidents that were resolved by rolling back the identified deploy

### Phase 5 — One-Click Rollback
- [x] "Roll back deploy" button in Slack (HIGH confidence deploy culprits only)
- [x] Confirmation dialog before any action is taken
- [x] Creates a draft revert PR on GitHub; warns if newer commits landed after the bad deploy
- [x] Posts PR URL back into the Slack thread
- [x] Annotates the PagerDuty incident with the rollback PR URL

---

## Key Design Decisions

**Parallel fetching, sequential reasoning**
All API calls run concurrently via `asyncio.gather`. Only the reasoning step is sequential. Keeps data collection under 15 seconds even with 5+ sources.

**Dynamic investigation window**
Starts at `[alert_time - 30min, alert_time + 5min]`. Expands to `[alert_time - 2hr, alert_time + 5min]` if no signal is found. Prevents runaway API costs while not missing slow-burn incidents.

**Hypotheses tested in priority order**
Recent deploy → upstream failure → database → traffic spike → infrastructure. The most common causes are tested first. The agent stops as soon as it finds a high-confidence root cause.

**Hard token budget per investigation**
Logs are capped at 200 lines, diffs at 300 lines, metrics at 100 data points before being sent for analysis. Prevents runaway costs on log-heavy services. Cap limits are configurable per adapter.

**No autonomous remediation in v1**
The agent recommends action but does not execute it. Rollbacks and restarts require a human to approve. Trust is built through demonstrated accuracy before automation is added.

**Adapter isolation**
Each adapter fails independently. A Sentry outage does not abort the investigation — it just removes one data source. The final report lists any sources that were unavailable.

---

## Rate Limits Reference

| Source | Limit | Agent behavior |
|--------|-------|---------------|
| Datadog Metrics API | 300 requests/hour | Batch metric queries, cache results within investigation |
| Datadog Logs API | 300 requests/hour | Single query per investigation, truncate to 200 lines |
| Sentry API | 100 requests/second | No issue in normal operation |
| GitHub REST API | 5,000 requests/hour | No issue in normal operation |
| GitHub GraphQL API | 5,000 points/hour | Prefer REST for diffs |
| PagerDuty API | 900 requests/minute | No issue in normal operation |

---

## Cost Estimate

Per investigation (typical incident):

| Item | Estimate |
|------|---------|
| LLM input tokens | ~8,000–15,000 tokens |
| LLM output tokens | ~500–1,000 tokens |
| API calls (all sources) | ~15–25 calls |
| Total LLM cost | ~$0.04–$0.10 per investigation |
| AWS Lambda runtime | ~$0.0001 per investigation |

At 50 incidents/month: approximately $2–5/month in LLM costs.

---

## Prerequisites

Before running this project you need:

- Python 3.12+
- Redis 7.0+ (local or managed, e.g. Redis Cloud, Elasticache)
- Active accounts and API keys for: Datadog, Sentry, GitHub, PagerDuty, Slack
- AWS account (for CloudWatch, SQS, S3, Lambda deployment)
- A GitHub repo with deploy tags or a CI system that creates them

---

## Getting Started

```bash
# Clone and install
git clone https://github.com/abhishekbhonde/why-production-is-down.git
cd why-production-is-down

# Create a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies (once pyproject.toml is in place)
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start Redis locally (if not already running)
redis-server

# Run in mock mode (uses fixture data, no real API calls)
MOCK_MODE=true uvicorn src.server.webhook:app --reload

# Run with real integrations
uvicorn src.server.webhook:app --reload
```

### Sending a test alert locally

```bash
curl -X POST http://localhost:8000/webhook/pagerduty \
  -H "Content-Type: application/json" \
  -d '{
    "event": "incident.trigger",
    "incident": {
      "service": {"name": "payment-service"},
      "created_at": "2024-01-15T02:51:00Z",
      "title": "High error rate on payment-service"
    }
  }'
```

---

## Configuration

```env
# .env.example

# Datadog
DATADOG_API_KEY=
DATADOG_APP_KEY=
DATADOG_SITE=datadoghq.com        # or datadoghq.eu for EU customers

# Sentry
SENTRY_AUTH_TOKEN=
SENTRY_ORG=                        # your Sentry organization slug
SENTRY_PROJECT=                    # project slug to query

# GitHub
GITHUB_TOKEN=                      # needs repo:read scope
GITHUB_ORG=                        # your GitHub organization name

# PagerDuty
PAGERDUTY_TOKEN=
PAGERDUTY_WEBHOOK_SECRET=          # for validating incoming webhook signatures

# Slack
SLACK_BOT_TOKEN=
SLACK_CHANNEL_ID=                  # channel to post incident reports

# AWS (CloudWatch, SQS, S3, SES)
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
SQS_QUEUE_URL=
S3_FALLBACK_BUCKET=
SES_FROM_EMAIL=                    # fallback email sender

# Redis
REDIS_URL=redis://localhost:6379

# Agent behavior
MOCK_MODE=false                    # set true to use fixture data (no real API calls)
INVESTIGATION_WINDOW_MINUTES=30    # initial lookback window
MAX_LOG_LINES=200                  # log lines sent to the agent per query
MAX_DIFF_LINES=300                 # diff lines sent per commit
ADAPTER_TIMEOUT_SECONDS=10         # per-source timeout before skipping
INVESTIGATION_TIMEOUT_SECONDS=90   # hard limit on full investigation
```

---

## Contributing

1. Fork the repository
2. Add fixture data to `tests/fixtures/` for any new adapter
3. Write an adapter test that runs entirely against fixtures (no real API calls)
4. Open a pull request

Each adapter must implement the `BaseAdapter` interface defined in `src/adapters/base.py` and handle its own timeout and error suppression.

---

## License

MIT
