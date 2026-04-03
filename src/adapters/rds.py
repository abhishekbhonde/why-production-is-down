from datetime import datetime

from src.adapters.base import BaseAdapter
from src.config import settings


class RDSAdapter(BaseAdapter):
    """Fetches database health metrics from CloudWatch RDS namespace."""

    name = "rds"

    async def _fetch(self, service: str, start: datetime, end: datetime) -> dict:
        if settings.mock_mode:
            return {
                "DatabaseConnections": {"max": 45, "avg": 12, "unit": "Count"},
                "CPUUtilization": {"max": 38.2, "avg": 22.1, "unit": "Percent"},
                "FreeableMemory": {"min": 2147483648, "unit": "Bytes"},
                "ReadLatency": {"max": 0.003, "avg": 0.001, "unit": "Seconds"},
                "WriteLatency": {"max": 0.005, "avg": 0.002, "unit": "Seconds"},
                "SlowQueries": 0,
                "status": "healthy",
            }

        # TODO: implement via aioboto3 cloudwatch get_metric_data
        # Namespace: AWS/RDS, dimensions: DBInstanceIdentifier
        raise NotImplementedError("Live RDS integration not yet implemented")
