"""FastAPI webhook receiver.

Endpoints:
  POST /webhook/pagerduty   — PagerDuty event webhook
  POST /webhook/datadog     — Datadog monitor alert webhook
  GET  /health              — liveness probe
"""

import asyncio
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime

import redis.asyncio as redis
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from src.agent.orchestrator import Alert, Orchestrator
from src.config import settings
from src.notifiers import email as email_notifier
from src.notifiers import s3 as s3_notifier
from src.notifiers import slack as slack_notifier
from src.notifiers.slack import SlackDeliveryError
from src.utils import dedup
from src.utils import sqs as sqs_util

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None
_orchestrator: Orchestrator | None = None
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
    expected = hmac.new(  # hmac.new is the correct function name
        key=settings.pagerduty_webhook_secret.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"v1={expected}", signature_header)


def _verify_datadog_signature(body: bytes, signature_header: str | None) -> bool:
    # Datadog webhooks don't have a standard HMAC mechanism in basic webhooks;
    # validation is typically via a shared secret in the payload itself.
    # TODO: implement when using Datadog webhook with custom headers.
    return True


# ---------------------------------------------------------------------------
# Background investigation runner
# ---------------------------------------------------------------------------

async def _run_investigation(alert: Alert) -> None:
    assert _orchestrator is not None
    assert _redis_client is not None

    await dedup.mark_in_flight(alert.service, _redis_client)
    try:
        report = await asyncio.wait_for(
            _orchestrator.investigate(alert),
            timeout=settings.investigation_timeout_seconds,
        )

        try:
            await slack_notifier.send(report)
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
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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
