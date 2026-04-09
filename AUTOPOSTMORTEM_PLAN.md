# AutoPostmortem — Implementation Plan

## What We're Building

AutoPostmortem generates a complete, structured incident postmortem the moment an alert fires — not after the incident is resolved. Four Claude agents watch silently, collect all signals, and produce a report nobody had to manually write. By the time engineers resolve the incident, the postmortem is ready.

This integrates directly into the existing `why-production-is-down` codebase: same Python FastAPI backend, same adapters, same Anthropic client. We add a Next.js frontend that serves as a real-time incident dashboard.

---

## Architecture Overview

```
PagerDuty / Datadog
       │ webhook
       ▼
FastAPI (webhook.py)
       │
       ├─── Orchestrator.investigate()   ← existing investigation
       │         │
       │         └── InvestigationReport (+ investigation_context)
       │                   │
       │                   ├── save to SQLite
       │                   ├── post to Slack
       │                   └── trigger PostmortemOrchestrator (background task)
       │                                 │
       │                          asyncio.gather()
       │                     ┌────┴────┬────────┬────────┐
       │               Timeline   RootCause   Impact   (parallel)
       │                     └────┬────┴────────┘
       │                          │
       │                     ActionItems Agent (uses phase-1 results)
       │                          │
       │                     save postmortem to SQLite
       │                          │
       │                     broadcast via WebSocket
       │
       └─── REST API (/api/incidents/*)  ← new endpoints
       └─── WebSocket (/ws/incidents/*)  ← new real-time endpoint

Next.js Frontend (port 3000)
       ├── / (Dashboard — incident list)
       └── /incidents/[id] (Incident detail + PostmortemPanel)
                │
                ├── REST API call → investigation data
                └── WebSocket → live postmortem updates
```

---

## Backend File Changes

### New Files

| File | Purpose |
|------|---------|
| `src/agent/postmortem_agents.py` | PostmortemOrchestrator + 4 Claude agents |
| `src/utils/ws_manager.py` | WebSocket connection manager (per-incident subscriber map) |
| `src/server/api.py` | New FastAPI router: REST + WebSocket endpoints |

### Modified Files

| File | Change |
|------|--------|
| `src/agent/prompts.py` | Add 4 postmortem agent system prompts |
| `src/agent/orchestrator.py` | Add `investigation_context` field to `InvestigationReport`; populate with formatted prompt |
| `src/store/db.py` | Add `postmortems` table; add `save_postmortem()`, `get_postmortem()`, `list_investigations()`, `get_investigation()` |
| `src/server/webhook.py` | Add CORS middleware; mount `api_router`; trigger postmortem after investigation |
| `src/config.py` | Add `cors_origins: list[str]` setting |

---

## The Four Postmortem Agents

All agents receive the same `investigation_context` — the full formatted investigation prompt (alert details, adapter data, correlated timeline, LLM report). This gives them complete situational awareness.

### Agent 1 — Timeline Agent (parallel)
**Input:** investigation context  
**Output:**
```json
{
  "events": [
    {
      "time": "T-15m",
      "source": "datadog | sentry | github | pagerduty | slack",
      "event": "one-sentence description of what happened",
      "significance": "high | medium | low"
    }
  ]
}
```
Extracts a chronological sequence of key events relative to the alert time. T-15m = 15 minutes before alert.

### Agent 2 — Root Cause Analysis Agent (parallel)
**Input:** investigation context  
**Output:**
```json
{
  "summary": "one-sentence root cause",
  "confidence": "HIGH | MEDIUM | LOW",
  "culprit": { "type": "deploy|feature_flag|...", "detail": "...", "diff_url": "..." },
  "contributing_factors": ["..."],
  "trigger_chain": ["step1 → step2 → step3 → alert"]
}
```
Expands on the existing investigation report with deeper causal analysis and trigger chain.

### Agent 3 — Impact Agent (parallel)
**Input:** investigation context  
**Output:**
```json
{
  "duration_minutes": 18,
  "users_affected": "~2,400 users during checkout",
  "error_rate_peak": "8.3%",
  "services_impacted": ["payment-service", "order-service"],
  "severity": "P0 | P1 | P2 | P3",
  "estimated_revenue_impact": "$14,200 in failed transactions",
  "customer_facing": true
}
```

### Agent 4 — Action Items Agent (sequential, uses phases 1-3)
**Input:** investigation context + output from Timeline, RootCause, Impact agents  
**Output:**
```json
{
  "immediate": ["action within the hour"],
  "short_term": ["action within the sprint"],
  "long_term": ["action within the quarter"],
  "preventability": "preventable | partially_preventable | not_preventable",
  "prevention_summary": "two-sentence summary of how to prevent recurrence"
}
```

---

## Database Schema

### New Table: `postmortems`
```sql
CREATE TABLE IF NOT EXISTS postmortems (
    id              TEXT PRIMARY KEY,   -- same as investigation_id
    status          TEXT NOT NULL DEFAULT 'generating',
    timeline        TEXT,               -- JSON blob
    root_cause      TEXT,               -- JSON blob
    impact          TEXT,               -- JSON blob
    action_items    TEXT,               -- JSON blob
    generated_at    TEXT,
    created_at      TEXT NOT NULL
)
```

### New DB Queries
- `save_postmortem(result: PostmortemResult)` — upsert all fields
- `get_postmortem(investigation_id: str)` — returns dict or None
- `list_investigations(limit=50)` — returns investigations joined with postmortem status
- `get_investigation(id: str)` — returns single investigation dict

---

## API Endpoints

### `GET /api/incidents`
Returns paginated investigation list with postmortem status.

**Response:**
```json
{
  "incidents": [
    {
      "id": "payment-service:2024-01-15T10:30:00+00:00",
      "service": "payment-service",
      "alert_time": "2024-01-15T10:30:00+00:00",
      "root_cause": "Deploy v2.4.1 introduced null-safety bug",
      "confidence": "HIGH",
      "culprit_type": "deploy",
      "investigation_seconds": 45.2,
      "created_at": "2024-01-15T10:31:00+00:00",
      "postmortem_status": "done"
    }
  ],
  "total": 15
}
```

### `GET /api/incidents/{incident_id}`
Returns full investigation + postmortem.

**Response:**
```json
{
  "id": "...",
  "service": "...",
  "root_cause": "...",
  "confidence": "...",
  "culprit": {},
  "affected_services": [],
  "recommended_action": "...",
  "investigation_seconds": 45.2,
  "created_at": "...",
  "postmortem": {
    "status": "done",
    "timeline": { "events": [...] },
    "root_cause_analysis": { "summary": "...", ... },
    "impact": { "duration_minutes": 18, ... },
    "action_items": { "immediate": [...], ... },
    "generated_at": "..."
  }
}
```

### `WS /ws/incidents/{incident_id}`
Real-time WebSocket feed for postmortem generation progress.

**Server → Client events:**
```json
{ "event": "section_ready", "section": "timeline", "data": {...} }
{ "event": "section_ready", "section": "root_cause_analysis", "data": {...} }
{ "event": "section_ready", "section": "impact", "data": {...} }
{ "event": "section_ready", "section": "action_items", "data": {...} }
{ "event": "postmortem_done", "generated_at": "2024-01-15T10:31:45+00:00" }
{ "event": "error", "message": "Investigation not found" }
```

If the postmortem is already complete when the client connects, all sections are sent immediately.

---

## Frontend Structure

```
frontend/
├── package.json              (Next.js 14, Tailwind, Radix UI, Recharts, Zustand)
├── tsconfig.json
├── next.config.ts
├── tailwind.config.ts
├── postcss.config.mjs
└── src/
    ├── app/
    │   ├── layout.tsx         (root layout, dark theme)
    │   ├── page.tsx           (Dashboard — incident list)
    │   ├── globals.css
    │   └── incidents/
    │       └── [id]/
    │           └── page.tsx   (Incident detail)
    ├── components/
    │   ├── ui/               (Radix UI + Tailwind primitives)
    │   │   ├── badge.tsx
    │   │   ├── card.tsx
    │   │   ├── tabs.tsx
    │   │   ├── skeleton.tsx
    │   │   └── progress.tsx
    │   ├── IncidentCard.tsx
    │   ├── StatsRow.tsx
    │   ├── InvestigationReport.tsx
    │   ├── PostmortemPanel.tsx
    │   └── postmortem/
    │       ├── TimelineTab.tsx
    │       ├── RootCauseTab.tsx
    │       ├── ImpactTab.tsx
    │       └── ActionItemsTab.tsx
    ├── hooks/
    │   └── usePostmortemWs.ts (WebSocket hook — auto-reconnect, state machine)
    ├── lib/
    │   ├── api.ts             (typed API client)
    │   └── utils.ts           (cn(), formatters)
    └── store/
        └── useIncidentStore.ts (Zustand — incident list + active postmortem state)
```

---

## Frontend State Shape (Zustand)

```ts
interface IncidentStore {
  // Dashboard
  incidents: Incident[]
  total: number
  loading: boolean
  fetchIncidents: () => Promise<void>

  // Active incident detail
  activeIncident: IncidentDetail | null
  fetchIncident: (id: string) => Promise<void>

  // Postmortem (real-time)
  postmortem: {
    status: 'idle' | 'generating' | 'done' | 'error'
    timeline: TimelineData | null
    root_cause_analysis: RootCauseData | null
    impact: ImpactData | null
    action_items: ActionItemsData | null
  }
  updatePostmortemSection: (section: string, data: unknown) => void
  setPostmortemDone: () => void
}
```

---

## Real-Time UX Flow

1. User opens `/incidents/[id]`
2. App fetches investigation data (REST) — shows report immediately
3. If postmortem status is `generating` or `idle`:
   - Show "Analysing incident..." with pulsing bar
   - Open WebSocket connection
   - As each section arrives, fade it in
4. If postmortem status is `done`:
   - WebSocket sends all sections immediately on connect
   - Full postmortem renders in one shot

---

## Mock Mode

When `MOCK_MODE=true` (default), all 4 agents return realistic pre-built mock data instead of calling Claude. The 1-second sleep per agent simulates realistic latency so the real-time feel is preserved.

---

## Environment Variables

### Backend (`.env`)
```
ANTHROPIC_API_KEY=sk-...
CORS_ORIGINS=["http://localhost:3000"]
```

### Frontend (`.env.local`)
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

---

## Run Instructions

```bash
# Backend
pip install -e ".[dev]"
uvicorn src.server.webhook:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev   # runs on :3000
```
