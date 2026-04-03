import json
from datetime import datetime
from pathlib import Path

from src.adapters.base import BaseAdapter
from src.config import settings

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "datadog_metrics.json"


class DatadogAdapter(BaseAdapter):
    name = "datadog"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        # TODO: implement live Datadog API calls
        # - GET /api/v1/query for error rate metric
        # - POST /api/v2/logs/events/search for log search
        raise NotImplementedError("Live Datadog integration not yet implemented")
