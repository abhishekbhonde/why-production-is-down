import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from src.adapters.base import BaseAdapter
from src.config import settings

logger = logging.getLogger(__name__)

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "sentry_errors.json"

_BASE = "https://sentry.io/api/0"


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.sentry_auth_token}"}


class SentryAdapter(BaseAdapter):
    name = "sentry"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        async with httpx.AsyncClient(timeout=settings.adapter_timeout_seconds) as client:
            error_groups = await self._fetch_issues(client, start, end)
            release = await self._fetch_latest_release(client, start, end)

        return {"error_groups": error_groups, "release": release}

    async def _fetch_issues(
        self,
        client: httpx.AsyncClient,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Fetches unresolved error groups from Sentry sorted by first seen.

        Uses GET /api/0/projects/{org}/{project}/issues/ with date filters.
        """
        params = {
            "query": "is:unresolved level:error",
            "firstSeen": f">{start.strftime('%Y-%m-%dT%H:%M:%S')}",
            "sort": "date",
            "limit": 25,
        }

        response = await client.get(
            f"{_BASE}/projects/{settings.sentry_org}/{settings.sentry_project}/issues/",
            headers=_auth_headers(),
            params=params,
        )

        if response.status_code != 200:
            logger.warning(
                "Sentry issues API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return []

        issues = response.json()
        return [
            {
                "id": issue.get("id"),
                "title": issue.get("title"),
                "firstSeen": issue.get("firstSeen"),
                "lastSeen": issue.get("lastSeen"),
                "count": int(issue.get("count", 0)),
                "level": issue.get("level"),
                "culprit": issue.get("culprit"),
                "tags": {t["key"]: t["value"] for t in issue.get("tags", [])},
            }
            for issue in issues
            # keep only issues whose firstSeen falls within the window
            if self._in_window(issue.get("firstSeen", ""), start, end)
        ]

    async def _fetch_latest_release(
        self,
        client: httpx.AsyncClient,
        start: datetime,
        end: datetime,
    ) -> dict | None:
        """Fetches the most recent Sentry release deployed within the window.

        Uses GET /api/0/projects/{org}/{project}/releases/
        Sentry releases correlate with deploys and carry firstEvent timestamps,
        which lets us pin exactly when a release started producing errors.
        """
        response = await client.get(
            f"{_BASE}/projects/{settings.sentry_org}/{settings.sentry_project}/releases/",
            headers=_auth_headers(),
            params={"per_page": 10, "sort": "date"},
        )

        if response.status_code != 200:
            logger.warning(
                "Sentry releases API returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return None

        releases = response.json()
        for rel in releases:
            deployed_at = rel.get("dateCreated") or rel.get("dateReleased")
            if deployed_at and self._in_window(deployed_at, start, end):
                return {
                    "version": rel.get("version"),
                    "dateCreated": deployed_at,
                    "deployedAt": rel.get("dateReleased") or deployed_at,
                    "firstEvent": rel.get("firstEvent"),
                    "lastEvent": rel.get("lastEvent"),
                    "newGroups": rel.get("newGroups", 0),
                }

        return None

    @staticmethod
    def _in_window(timestamp_str: str, start: datetime, end: datetime) -> bool:
        if not timestamp_str:
            return False
        try:
            # Sentry timestamps include timezone info (Z or +00:00)
            ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            ts_naive = ts.replace(tzinfo=None)
            return start <= ts_naive <= end
        except ValueError:
            return False
