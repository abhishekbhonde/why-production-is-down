import json
from datetime import datetime
from pathlib import Path

from src.adapters.base import BaseAdapter
from src.config import settings

FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "cloudwatch_logs.json"


class CloudWatchAdapter(BaseAdapter):
    name = "cloudwatch"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return json.loads(FIXTURE_PATH.read_text())

        # TODO: implement live CloudWatch API calls via aioboto3
        # - logs:FilterLogEvents for application logs
        # - cloudwatch:GetMetricData for ECS/RDS metrics
        raise NotImplementedError("Live CloudWatch integration not yet implemented")
