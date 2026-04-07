"""LaunchDarkly adapter — fetches feature flag changes from the audit log.

Uses the LaunchDarkly REST API v2 audit log endpoint to find flag changes
that occurred within the investigation window. Flag rollouts are a common
cause of production incidents and are often missed when only checking deploys.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.adapters.base import BaseAdapter
from src.config import settings

logger = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "launchdarkly_flags.json"

_BASE = "https://app.launchdarkly.com"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": settings.launchdarkly_api_key}


class LaunchDarklyAdapter(BaseAdapter):
    name = "launchdarkly"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        if not settings.launchdarkly_api_key:
            logger.debug("LaunchDarkly API key not configured, skipping")
            return {"flag_changes": []}

        # LaunchDarkly audit log timestamps are Unix milliseconds
        after_ms = int(start.timestamp() * 1000)
        before_ms = int(end.timestamp() * 1000)

        async with httpx.AsyncClient(timeout=settings.adapter_timeout_seconds) as client:
            flag_changes = await self._fetch_flag_changes(client, service, after_ms, before_ms)

        return {"flag_changes": flag_changes}

    async def _fetch_flag_changes(
        self,
        client: httpx.AsyncClient,
        service: str,
        after_ms: int,
        before_ms: int,
    ) -> list[dict]:
        """Queries the audit log for flag changes in the investigation window.

        Filters to entries of kind 'flag' and optionally narrows by a service
        search term so the LLM isn't flooded with unrelated flag changes.
        """
        params: dict = {
            "after": after_ms,
            "before": before_ms,
            "limit": 50,
        }
        # Use the service name as a search hint — LD will match against flag
        # keys and descriptions, surfacing the most relevant flags first.
        if settings.launchdarkly_env:
            # Scope to the production environment resource
            params["spec"] = f"proj/*:env/{settings.launchdarkly_env}:flag/*"

        response = await client.get(
            f"{_BASE}/api/v2/auditlog",
            headers=_auth_headers(),
            params=params,
        )

        if response.status_code != 200:
            logger.warning(
                "LaunchDarkly audit log returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return []

        items = response.json().get("items", [])
        changes = []
        for item in items:
            if item.get("kind") != "flag":
                continue
            # Timestamp comes back in milliseconds
            ts_ms = item.get("date", 0)
            changes.append(
                {
                    "flag_key": item.get("name", ""),
                    "description": item.get("description", ""),
                    "changed_at": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
                    "changed_by": item.get("member", {}).get("email", "unknown"),
                    "resources": item.get("target", {}).get("resources", []),
                }
            )

        # Chronological order so the LLM sees the timeline naturally
        changes.sort(key=lambda x: x["changed_at"])
        return changes
