import json
import logging
from datetime import datetime
from pathlib import Path

import aioboto3

from src.adapters.base import BaseAdapter
from src.config import settings

logger = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "cloudwatch_logs.json"

# Conventional log group paths — tried in order until one returns events
_LOG_GROUP_PATTERNS = [
    "/ecs/{service}",
    "/aws/ecs/{service}",
    "/app/{service}",
    "/{service}",
]


class CloudWatchAdapter(BaseAdapter):
    name = "cloudwatch"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.aws_region,
        )

        async with session.client("logs") as logs_client:
            log_events, total_events, scanned_bytes = await self._fetch_log_events(
                logs_client, service, start, end
            )

        return {
            "log_events": log_events,
            "total_events": total_events,
            "scanned_bytes": scanned_bytes,
        }

    async def _fetch_log_events(
        self,
        client,
        service: str,
        start: datetime,
        end: datetime,
    ) -> tuple[list[dict], int, int]:
        """Searches CloudWatch Logs for ERROR-level events using FilterLogEvents.

        Tries conventional log group naming patterns until one succeeds.
        Returns (events, total_count, scanned_bytes).
        """
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        for pattern in _LOG_GROUP_PATTERNS:
            log_group = pattern.format(service=service)
            events, total, scanned = await self._query_log_group(
                client, log_group, start_ms, end_ms
            )
            if events:
                return events, total, scanned

        logger.warning("No CloudWatch log groups found for service: %s", service)
        return [], 0, 0

    async def _query_log_group(
        self,
        client,
        log_group: str,
        start_ms: int,
        end_ms: int,
    ) -> tuple[list[dict], int, int]:
        """Runs FilterLogEvents against a specific log group.

        Filters for ERROR-level messages and caps results at max_log_lines.
        Returns empty tuple if the log group doesn't exist.
        """
        try:
            response = await client.filter_log_events(
                logGroupName=log_group,
                startTime=start_ms,
                endTime=end_ms,
                filterPattern='"ERROR" OR "CRITICAL" OR "Exception" OR "error"',
                limit=settings.max_log_lines,
            )
        except client.exceptions.ResourceNotFoundException:
            return [], 0, 0
        except Exception as exc:
            logger.warning("CloudWatch FilterLogEvents failed for %s: %s", log_group, exc)
            return [], 0, 0

        raw_events = response.get("events", [])
        searched = response.get("searchedLogStreams", [])

        events = [
            {
                "timestamp": datetime.fromtimestamp(e["timestamp"] / 1000).isoformat(),
                "message": e["message"].rstrip("\n"),
                "logGroup": log_group,
                "logStream": e.get("logStreamName", ""),
            }
            for e in raw_events
        ]

        total = len(raw_events)
        scanned_bytes = sum(s.get("searchedCompletely", 0) for s in searched)

        return events, total, scanned_bytes
