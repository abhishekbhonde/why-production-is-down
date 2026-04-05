"""SQS alert buffering for alert storm handling.

When an investigation is already in-flight for a service, new alerts are
enqueued to SQS with a 60-second visibility delay. A background consumer
polls the queue and fires investigations once the delay expires.

If SQS is not configured (SQS_QUEUE_URL is empty), buffering is skipped
and the alert is dropped with a warning — same behaviour as Phase 1.
"""

import json
import logging
from datetime import datetime

import aioboto3

from src.config import settings

logger = logging.getLogger(__name__)

_DELAY_SECONDS = 60  # visibility delay before queued alert becomes processable


async def enqueue(service: str, alert_time: datetime, description: str, severity: str, incident_id: str) -> bool:
    """Sends an alert to SQS with a 60-second delay.

    Returns True if enqueued successfully, False if SQS is not configured
    or the send fails (caller should log and continue).
    """
    if not settings.sqs_queue_url:
        logger.warning(
            "SQS not configured — dropping buffered alert for %s (storm guard inactive)",
            service,
        )
        return False

    message = {
        "service": service,
        "alert_time": alert_time.isoformat(),
        "description": description,
        "severity": severity,
        "incident_id": incident_id,
    }

    try:
        session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.aws_region,
        )
        async with session.client("sqs") as sqs:
            await sqs.send_message(
                QueueUrl=settings.sqs_queue_url,
                MessageBody=json.dumps(message),
                DelaySeconds=_DELAY_SECONDS,
            )
        logger.info("Alert for %s buffered to SQS (delay=%ds)", service, _DELAY_SECONDS)
        return True
    except Exception as exc:
        logger.error("Failed to enqueue alert for %s to SQS: %s", service, exc)
        return False


async def drain_one(redis_client, orchestrator, run_investigation_fn) -> bool:
    """Receives and processes one message from the SQS queue.

    Returns True if a message was processed, False if the queue was empty
    or SQS is not configured.

    Designed to be called in a polling loop (e.g. every 10 seconds from a
    background task started in the FastAPI lifespan).
    """
    if not settings.sqs_queue_url:
        return False

    try:
        session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.aws_region,
        )
        async with session.client("sqs") as sqs:
            response = await sqs.receive_message(
                QueueUrl=settings.sqs_queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=1,  # short poll
            )

            messages = response.get("Messages", [])
            if not messages:
                return False

            msg = messages[0]
            receipt_handle = msg["ReceiptHandle"]
            body = json.loads(msg["Body"])

            # Always delete from queue — if investigation fails, the
            # base adapter error handling records it; we don't retry storms.
            await sqs.delete_message(
                QueueUrl=settings.sqs_queue_url,
                ReceiptHandle=receipt_handle,
            )

        from src.agent.orchestrator import Alert
        from src.utils import dedup

        service = body["service"]
        alert_time = datetime.fromisoformat(body["alert_time"])

        # If still in-flight (another storm wave), drop this one
        if await dedup.is_duplicate(service, redis_client):
            logger.info("Queued alert for %s still deduplicated — dropping", service)
            return True

        alert = Alert(
            service=service,
            alert_time=alert_time,
            description=body.get("description", ""),
            severity=body.get("severity", "critical"),
            incident_id=body.get("incident_id", ""),
        )

        await run_investigation_fn(alert)
        return True

    except Exception as exc:
        logger.error("SQS drain_one failed: %s", exc)
        return False
