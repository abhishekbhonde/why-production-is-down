"""Fallback: write report to S3 when Slack is unreachable."""

import json
import logging
from datetime import datetime

from src.agent.orchestrator import InvestigationReport
from src.config import settings

logger = logging.getLogger(__name__)


async def upload(report: InvestigationReport) -> str:
    ""
    ""
    ""
    """Uploads the report JSON to S3 and returns the object URL."""
    if settings.mock_mode:
        logger.info("[MOCK] Would upload report to S3 for service %s", report.service)
        return f"s3://{settings.s3_fallback_bucket}/incidents/{report.service}/{datetime.utcnow().isoformat()}.json"

    try:
        import aioboto3  # type: ignore

        key = f"incidents/{report.service}/{datetime.utcnow().strftime('%Y/%m/%d/%H%M%S')}.json"
        body = json.dumps(report.__dict__, default=str, indent=2)

        session = aioboto3.Session()
        async with session.client("s3", region_name=settings.aws_region) as s3:
            await s3.put_object(
                Bucket=settings.s3_fallback_bucket,
                Key=key,
                Body=body.encode(),
                ContentType="application/json",
            )

        return f"https://{settings.s3_fallback_bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"
    except Exception as exc:
        logger.error("S3 upload failed: %s", exc)
        raise
