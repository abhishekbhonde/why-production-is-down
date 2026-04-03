"""Fallback: send report via AWS SES when Slack is unreachable."""

import logging

from src.agent.orchestrator import InvestigationReport
from src.config import settings

logger = logging.getLogger(__name__)


async def send(report: InvestigationReport, s3_url: str, recipient: str) -> None:
    if settings.mock_mode:
        logger.info(
            "[MOCK] Would email incident report for %s to %s (S3: %s)",
            report.service,
            recipient,
            s3_url,
        )
        return

    try:
        import aioboto3  # type: ignore

        subject = f"[INCIDENT] {report.service}: {report.root_cause[:80]}"
        body = (
            f"Incident report for {report.service}\n\n"
            f"Root cause: {report.root_cause}\n"
            f"Confidence: {report.confidence}\n"
            f"Recommended action: {report.recommended_action}\n\n"
            f"Full report: {s3_url}\n"
        )

        session = aioboto3.Session()
        async with session.client("ses", region_name=settings.aws_region) as ses:
            await ses.send_email(
                Source=settings.ses_from_email,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": body}},
                },
            )
    except Exception as exc:
        logger.error("SES email failed: %s", exc)
        raise
