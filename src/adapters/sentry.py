import json
from datetime import datetime
from pathlib import Path

from src.adapters.base import BaseAdapter
from src.config import settings

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "sentry_errors.json"


class SentryAdapter(BaseAdapter):
    name = "sentry"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        # TODO: implement live Sentry API calls
        # - GET /api/0/projects/{org}/{project}/issues/ filtered by date
        # - GET /api/0/projects/{org}/{project}/releases/ for deploy tracking
        raise NotImplementedError("Live Sentry integration not yet implemented")
