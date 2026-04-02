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
┌─────────────────────┐
│   Webhook Receiver  │  Validates signature, deduplicates alerts
└────────┬────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────┐
│                    Agent Orchestrator                    │
│                                                          │
│  1. Extract alert context (service, time, severity)      │
│  2. Determine investigation window (T-30min to now)      │
│  3. Fan out parallel data fetching:                      │
│     ├── Metrics & APM        → Datadog                   │
│     ├── Error groups         → Sentry                    │
│     ├── Logs                 → CloudWatch / Datadog      │
│     └── Recent deploys       → GitHub / CI pipeline      │
│  4. Correlate timeline across all sources                │
│  5. Identify root cause — deploy SHA, diff, author       │
└────────┬─────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────┐
│   Notification Hub  │  → Slack message with full report
│                     │  → PagerDuty incident note
└─────────────────────┘
```

---

## Example Output

```
INCIDENT SUMMARY
─────────────────────────────────────────
Service:        payment-service
First failure:  2024-01-15 02:47:03 UTC
Alert fired:    2024-01-15 02:51:00 UTC (4 min lag)

Cause: 500 error rate spiked from 0.1% → 34% on POST /webhooks/stripe

Likely culprit: Deploy #892 by @jsmith at 02:43 UTC
  Commit: fix(stripe): update webhook signature validation
  Repo:   acme-corp/payment-service @ a3f8c21

Relevant diff:
  - const sig = req.headers['stripe-signature']
  + const sig = req.headers['x-stripe-signature']  ← header name incorrect

Recommended action: Roll back deploy #892 or revert the header name change.
─────────────────────────────────────────
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Web framework | FastAPI |
| Queue / dedup | Redis |
| Metrics source | Datadog |
| Error tracking | Sentry |
| Log source | AWS CloudWatch |
| Deploy tracking | GitHub API |
| Alerting input | PagerDuty |
| Notification output | Slack |
| Deployment | AWS Lambda / Fly.io |

---

## Integrations

- **Datadog** — metrics, APM traces, log search
- **Sentry** — error groups, stack traces, release tracking
- **AWS CloudWatch** — Lambda/ECS logs and alarms
- **GitHub** — commit diffs, deploy tags, PR history
- **PagerDuty** — alert ingestion and incident annotations
- **Slack** — incident report delivery

---

## Project Structure

```
why-production-is-down/
├── src/
│   ├── agent/
│   │   ├── orchestrator.py      # Core agent loop
│   │   ├── tools.py             # Tool definitions
│   │   └── prompts.py           # Investigation prompts
│   ├── adapters/
│   │   ├── datadog.py
│   │   ├── sentry.py
│   │   ├── cloudwatch.py
│   │   ├── github.py
│   │   └── pagerduty.py
│   ├── notifiers/
│   │   └── slack.py
│   ├── server/
│   │   └── webhook.py           # FastAPI webhook receiver
│   └── utils/
│       ├── dedup.py             # Redis deduplication
│       └── timeline.py          # Time correlation helpers
├── tests/
│   ├── fixtures/                # Recorded API responses for testing
│   └── test_agent.py
├── deploy/
│   ├── lambda_handler.py        # AWS Lambda entrypoint
│   └── Dockerfile
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Implementation Phases

### Phase 1 — Skeleton
- Webhook receiver with signature validation
- Redis deduplication
- Agent loop with mock data adapters
- End-to-end Slack output

### Phase 2 — Real Integrations
- Live Datadog, Sentry, GitHub, CloudWatch adapters
- Tested against real incidents in staging

### Phase 3 — Reliability
- 90-second investigation timeout budget
- Graceful degradation when sources are unavailable
- Token/cost tracking per investigation

### Phase 4 — Learning
- Store investigation → outcome pairs
- Slack feedback buttons (was the root cause correct?)
- Prompt tuning based on misses

---

## Key Design Decisions

**Parallel fetching, sequential reasoning** — all API calls run concurrently. Only the reasoning step is sequential. Keeps data collection under 15 seconds.

**Fixed investigation window** — always `[alert_time - 30min, alert_time + 5min]`. Prevents runaway API costs.

**Deploy correlation first** — 80% of incidents trace to a recent deploy. This hypothesis is checked first.

**No autonomous remediation in v1** — the agent recommends action, humans approve it. Trust is built incrementally.

---

## Getting Started

```bash
# Clone and install
git clone https://github.com/abhishekbhonde/why-production-is-down.git
cd why-production-is-down
pip install -e .

# Configure
cp .env.example .env
# Fill in your API keys

# Run locally
uvicorn src.server.webhook:app --reload
```

---

## Configuration

```env
# .env.example
DATADOG_API_KEY=
DATADOG_APP_KEY=
SENTRY_AUTH_TOKEN=
SENTRY_ORG=
GITHUB_TOKEN=
PAGERDUTY_TOKEN=
SLACK_BOT_TOKEN=
SLACK_CHANNEL_ID=
REDIS_URL=redis://localhost:6379
```

---

## License

MIT
