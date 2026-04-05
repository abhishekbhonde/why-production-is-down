import logging
from datetime import datetime

import httpx

from src.adapters.base import BaseAdapter
from src.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.pagerduty.com"


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Token token={settings.pagerduty_token}",
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json",
    }


class PagerDutyAdapter(BaseAdapter):
    """Used for annotating incidents after root cause is found, not for data fetching."""

    name = "pagerduty"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        # PagerDuty is an input source (webhook), not a data source for investigation.
        # This adapter is used to push notes back to the incident.
        return {}

    async def annotate_incident(self, incident_id: str, note: str) -> bool:
        """Posts a note to a PagerDuty incident.

        Returns True on success, False on any failure (non-fatal — annotation
        failures should never abort the investigation flow).
        """
        if not incident_id:
            logger.debug("No incident_id provided, skipping PagerDuty annotation")
            return False

        if settings.mock_mode:
            logger.info("[MOCK] PagerDuty annotation for %s: %s", incident_id, note[:80])
            return True

        if not settings.pagerduty_token:
            logger.warning("PAGERDUTY_TOKEN not configured, skipping annotation")
            return False

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{_BASE}/incidents/{incident_id}/notes",
                    headers=_auth_headers(),
                    json={"note": {"content": note}},
                )

            if response.status_code in (200, 201):
                logger.info("PagerDuty incident %s annotated", incident_id)
                return True

            logger.warning(
                "PagerDuty annotation returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return False

        except Exception as exc:
            logger.warning("PagerDuty annotation failed for %s: %s", incident_id, exc)
            return False
