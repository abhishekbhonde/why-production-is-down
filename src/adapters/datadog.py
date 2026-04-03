import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from src.adapters.base import BaseAdapter
from src.config import settings
from src.utils.rate_limit import check_and_record

logger = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "datadog_metrics.json"

# Datadog API base URLs vary by site (datadoghq.com vs datadoghq.eu)
_BASE = "https://api.{site}"


def _base(path: str) -> str:
    return _BASE.format(site=settings.datadog_site) + path


def _auth_headers() -> dict[str, str]:
    return {
        "DD-API-KEY": settings.datadog_api_key,
        "DD-APPLICATION-KEY": settings.datadog_app_key,
    }


class DatadogAdapter(BaseAdapter):
    name = "datadog"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        async with httpx.AsyncClient(timeout=settings.adapter_timeout_seconds) as client:
            metrics = await self._fetch_error_rate(client, service, start, end)
            logs = await self._fetch_logs(client, service, start, end)

        return {
            "error_rate_series": metrics,
            "log_events": logs,
        }

    async def _fetch_error_rate(
        self,
        client: httpx.AsyncClient,
        service: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Queries the Datadog Metrics API for HTTP 5xx error rate.

        Uses the standard APM metric: trace.http.request.errors.
        Falls back to an empty list on any non-200 response.
        """
        if not check_and_record("datadog_metrics"):
            logger.warning("Datadog metrics rate limit reached, skipping")
            return []

        query = f"sum:trace.http.request.errors{{service:{service}}}.as_rate()"
        params = {
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
            "query": query,
        }

        response = await client.get(
            _base("/api/v1/query"),
            headers=_auth_headers(),
            params=params,
        )

        if response.status_code != 200:
            logger.warning(
                "Datadog metrics API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return []

        payload = response.json()
        series = payload.get("series", [])
        if not series:
            return []

        # Flatten the first matching series into [{timestamp, value}, ...]
        pointlist = series[0].get("pointlist", [])
        return [
            {"timestamp": datetime.fromtimestamp(ts / 1000, tz=None).isoformat(), "value": round(val, 4)}
            for ts, val in pointlist
            if val is not None
        ]

    async def _fetch_logs(
        self,
        client: httpx.AsyncClient,
        service: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Searches Datadog Logs for ERROR-level events on this service.

        Uses the v2 Logs Events Search API (POST /api/v2/logs/events/search).
        Results are capped at settings.max_log_lines.
        """
        if not check_and_record("datadog_logs"):
            logger.warning("Datadog logs rate limit reached, skipping")
            return []

        body = {
            "filter": {
                "query": f"service:{service} status:error",
                "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "sort": "timestamp",
            "page": {"limit": settings.max_log_lines},
        }

        response = await client.post(
            _base("/api/v2/logs/events/search"),
            headers={**_auth_headers(), "Content-Type": "application/json"},
            json=body,
        )

        if response.status_code != 200:
            logger.warning(
                "Datadog logs API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return []

        payload = response.json()
        events = payload.get("data", [])
        return [
            {
                "timestamp": e.get("attributes", {}).get("timestamp", ""),
                "message": e.get("attributes", {}).get("message", ""),
                "status": e.get("attributes", {}).get("status", ""),
                "service": e.get("attributes", {}).get("service", service),
            }
            for e in events
        ]
