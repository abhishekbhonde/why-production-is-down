import json
from datetime import datetime
from pathlib import Path

from src.adapters.base import BaseAdapter
from src.config import settings


class PagerDutyAdapter(BaseAdapter):
    """Used for annotating incidents after root cause is found, not for data fetching."""

    name = "pagerduty"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        # PagerDuty is an input source (webhook), not a data source for investigation.
        # This adapter is used to push notes back to the incident.
        return {}

    async def annotate_incident(self, incident_id: str, note: str) -> bool:
        if settings.mock_mode:
            return True

        # TODO: POST /incidents/{id}/notes with root cause summary
        raise NotImplementedError("Live PagerDuty annotation not yet implemented")
