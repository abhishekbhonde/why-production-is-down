"""FastAPI webhook receiver.

Endpoints:
  POST /webhook/pagerduty         — PagerDuty event webhook
  POST /webhook/datadog           — Datadog monitor alert webhook
  POST /webhook/slack/interactive — Slack button callbacks (thumbs up/down)
  GET  /report/weekly             — Weekly accuracy report
  GET  /health                    — liveness probe
"""

import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import parse_qs

import redis.asyncio as redis
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from src.adapters.github import create_revert_pr
from src.adapters.pagerduty import PagerDutyAdapter
from src.agent.orchestrator import Alert, Orchestrator
from src.config import settings
from src.notifiers import email as email_notifier
from src.notifiers import s3 as s3_notifier
from src.notifiers import slack as slack_notifier
from src.notifiers.slack import SlackDeliveryError
from src.store import db as store
from src.utils import dedup
from src.utils import sqs as sqs_util

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None
_orchestrator: Orchestrator | None = None
_pagerduty = PagerDutyAdapter()
_drain_task: asyncio.Task | None = None


async def _sqs_drain_loop() -> None:
    """Background loop that drains buffered SQS alerts every 10 seconds."""
    while True:
        try:
            await sqs_util.drain_one(_redis_client, _orchestrator, _run_investigation)
        except Exception:
            logger.exception("Unexpected error in SQS drain loop")
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis_client, _orchestrator, _drain_task
    _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    _orchestrator = Orchestrator()
    await store.init()
    if settings.sqs_queue_url:
        _drain_task = asyncio.create_task(_sqs_drain_loop())
        logger.info("SQS drain loop started")
    yield
    if _drain_task:
        _drain_task.cancel()
    if _redis_client:
        await _redis_client.aclose()


app = FastAPI(title="Why Is Production Down?", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Signature validation helpers
# ---------------------------------------------------------------------------

def _verify_pagerduty_signature(body: bytes, signature_header: str | None) -> bool:
    if not settings.pagerduty_webhook_secret:
        return True  # skip validation if secret not configured (dev mode)
    if not signature_header:
        return False
    expected = hmac.new(
        key=settings.pagerduty_webhook_secret.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"v1={expected}", signature_header)


def _verify_datadog_signature(body: bytes, signature_header: str | None) -> bool:
    # Datadog webhooks don't have a standard HMAC mechanism in basic webhooks;
    # validation is typically via a shared secret in the payload itself.
    return True


def _verify_slack_signature(body: bytes, signature_header: str | None, timestamp: str | None) -> bool:
    """Verifies Slack request signature using the signing secret."""
    slack_signing_secret = getattr(settings, "slack_signing_secret", "")
    if not slack_signing_secret:
        return True  # skip in dev mode
    if not signature_header or not timestamp:
        return False
    base = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        key=slack_signing_secret.encode(),
        msg=base.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Background investigation runner
# ---------------------------------------------------------------------------

def _build_pagerduty_note(report) -> str:
    lines = [
        f"Root cause: {report.root_cause}",
        f"Confidence: {report.confidence}",
    ]
    culprit = report.culprit or {}
    if culprit.get("detail"):
        lines.append(f"Culprit: {culprit['detail']}")
    if culprit.get("diff_url"):
        lines.append(f"Diff: {culprit['diff_url']}")
    lines.append(f"Recommended action: {report.recommended_action}")
    lines.append(f"Investigation took {report.investigation_seconds}s")
    return "\n".join(lines)


async def _run_investigation(alert: Alert) -> None:
    assert _orchestrator is not None
    assert _redis_client is not None

    await dedup.mark_in_flight(alert.service, _redis_client)
    try:
        report = await asyncio.wait_for(
            _orchestrator.investigate(alert),
            timeout=settings.investigation_timeout_seconds,
        )

        # Persist to DB
        investigation_id = await store.save_investigation(report)

        # Annotate PagerDuty incident
        if alert.incident_id:
            await _pagerduty.annotate_incident(
                alert.incident_id, _build_pagerduty_note(report)
            )

        # Notify via Slack (with feedback buttons) or fall back to S3 + email
        try:
            await slack_notifier.send(report, investigation_id)
        except SlackDeliveryError as exc:
            logger.warning("Slack delivery failed (%s), falling back to S3 + email", exc)
            s3_url = await s3_notifier.upload(report)
            if settings.ses_from_email:
                await email_notifier.send(report, s3_url, settings.ses_from_email)

    except asyncio.TimeoutError:
        logger.error(
            "Investigation for %s timed out after %ds",
            alert.service,
            settings.investigation_timeout_seconds,
        )
    except Exception:
        logger.exception("Unhandled error during investigation for %s", alert.service)
    finally:
        await dedup.clear(alert.service, _redis_client)


# ---------------------------------------------------------------------------
# Rollback helper
# ---------------------------------------------------------------------------

async def _do_rollback(sha: str, repo: str, channel_id: str, thread_ts: str) -> None:
    """Creates a GitHub revert PR and posts the result back to the Slack thread."""
    try:
        pr_url = await create_revert_pr(repo, sha)
        await slack_notifier.post_thread_reply(
            channel_id,
            thread_ts,
            f":white_check_mark: Revert PR created: {pr_url}\nReview and merge to roll back the deploy.",
        )
    except Exception:
        logger.exception("Failed to create revert PR for %s@%s", repo, sha)
        await slack_notifier.post_thread_reply(
            channel_id,
            thread_ts,
            ":x: Failed to create revert PR automatically. Please create one manually.",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/report/weekly")
async def weekly_report() -> dict:
    """Returns the accuracy report for the past 7 days."""
    return await store.weekly_accuracy()


@app.post("/webhook/pagerduty")
async def pagerduty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_pagerduty_signature: str | None = Header(default=None),
) -> dict:
    body = await request.body()

    if not _verify_pagerduty_signature(body, x_pagerduty_signature):
        raise HTTPException(status_code=401, detail="Invalid PagerDuty webhook signature")

    payload = await request.json()

    # PagerDuty v3 webhook envelope
    event_type = payload.get("event", {}).get("event_type", "")
    if event_type not in ("incident.triggered", "incident.acknowledged"):
        return {"status": "ignored", "reason": f"event_type={event_type}"}

    incident = payload.get("event", {}).get("data", {})
    service_name = incident.get("service", {}).get("name", "unknown")
    created_at_raw = incident.get("created_at", datetime.utcnow().isoformat())
    description = incident.get("title", "No description")
    incident_id = incident.get("id", "")

    try:
        alert_time = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except ValueError:
        alert_time = datetime.utcnow()

    assert _redis_client is not None
    if await dedup.is_duplicate(service_name, _redis_client):
        enqueued = await sqs_util.enqueue(
            service=service_name,
            alert_time=alert_time,
            description=description,
            severity="critical",
            incident_id=incident_id,
        )
        return {
            "status": "buffered" if enqueued else "deduplicated",
            "service": service_name,
        }

    alert = Alert(
        service=service_name,
        alert_time=alert_time,
        description=description,
        severity="critical",
        incident_id=incident_id,
    )

    background_tasks.add_task(_run_investigation, alert)
    return {"status": "accepted", "service": service_name}


@app.post("/webhook/datadog")
async def datadog_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    body = await request.body()

    if not _verify_datadog_signature(body, None):
        raise HTTPException(status_code=401, detail="Invalid Datadog webhook signature")

    payload = await request.json()

    # Datadog monitor alert payload
    alert_type = payload.get("alert_type", "")
    if alert_type not in ("error", "warning"):
        return {"status": "ignored", "reason": f"alert_type={alert_type}"}

    service_name = payload.get("tags", {}).get("service", payload.get("title", "unknown"))
    description = payload.get("body", payload.get("title", "No description"))
    date_happened = payload.get("date_happened")

    try:
        alert_time = datetime.utcfromtimestamp(int(date_happened)) if date_happened else datetime.utcnow()
    except (TypeError, ValueError):
        alert_time = datetime.utcnow()

    assert _redis_client is not None
    if await dedup.is_duplicate(service_name, _redis_client):
        enqueued = await sqs_util.enqueue(
            service=service_name,
            alert_time=alert_time,
            description=description,
            severity="critical",
            incident_id="",
        )
        return {
            "status": "buffered" if enqueued else "deduplicated",
            "service": service_name,
        }

    alert = Alert(
        service=service_name,
        alert_time=alert_time,
        description=description,
        severity="critical",
    )

    background_tasks.add_task(_run_investigation, alert)
    return {"status": "accepted", "service": service_name}


@app.post("/webhook/slack/interactive")
async def slack_interactive(
    request: Request,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> JSONResponse:
    """Handles Slack interactive button callbacks (thumbs up / thumbs down).

    Slack sends these as application/x-www-form-urlencoded with a 'payload'
    field containing JSON.
    """
    body = await request.body()

    if not _verify_slack_signature(body, x_slack_signature, x_slack_request_timestamp):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    # Decode form-encoded payload
    form = parse_qs(body.decode())
    payload_raw = form.get("payload", ["{}"])[0]
    payload = json.loads(payload_raw)

    actions = payload.get("actions", [])
    if not actions:
        return JSONResponse(content={"status": "no_actions"})

    action = actions[0]
    action_id = action.get("action_id", "")
    value = action.get("value", "")

    if action_id in ("feedback_correct", "feedback_incorrect"):
        verdict = "correct" if action_id == "feedback_correct" else "incorrect"
        if value:
            await store.record_feedback(value, verdict)
            logger.info("Feedback recorded via Slack: %s → %s", value, verdict)
        ack_text = (
            ":white_check_mark: Thanks for the feedback!"
            if verdict == "correct"
            else ":notepad_spiral: Got it — marked as incorrect."
        )
        return JSONResponse(content={"text": ack_text})

    if action_id == "rollback_deploy":
        # value format: "{sha}|{repo}"
        sha, _, repo = value.partition("|")
        channel_id = payload.get("container", {}).get("channel_id", "")
        thread_ts = payload.get("container", {}).get("message_ts", "")
        asyncio.create_task(_do_rollback(sha, repo, channel_id, thread_ts))
        return JSONResponse(content={"text": ":hourglass_flowing_sand: Creating revert PR, hang tight..."})

    return JSONResponse(content={"status": "unknown_action"})
