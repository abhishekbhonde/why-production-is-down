import logging
from datetime import datetime

import aioboto3

from src.adapters.base import BaseAdapter
from src.config import settings

logger = logging.getLogger(__name__)

# RDS CloudWatch metrics to fetch
_METRICS = [
    ("DatabaseConnections", "Count", "Maximum"),
    ("DatabaseConnections", "Count", "Average"),
    ("CPUUtilization", "Percent", "Maximum"),
    ("CPUUtilization", "Percent", "Average"),
    ("FreeableMemory", "Bytes", "Minimum"),
    ("ReadLatency", "Seconds", "Maximum"),
    ("ReadLatency", "Seconds", "Average"),
    ("WriteLatency", "Seconds", "Maximum"),
    ("WriteLatency", "Seconds", "Average"),
]

# Threshold for flagging unhealthy metrics
_UNHEALTHY_THRESHOLDS = {
    "DatabaseConnections_max": 900,   # typical RDS max_connections ~1000
    "CPUUtilization_max": 90.0,       # percent
    "FreeableMemory_min": 104857600,  # 100 MB in bytes
    "ReadLatency_max": 0.1,           # 100ms
    "WriteLatency_max": 0.1,
}


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

        # Derive DB instance identifier from service name convention
        # e.g. "payment-service" -> "payment-service-db" or just "payment-service"
        db_instance_id = f"{service}-db"

        session = aioboto3.Session(
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
            region_name=settings.aws_region,
        )

        async with session.client("cloudwatch") as cw:
            raw = await self._get_metric_data(cw, db_instance_id, start, end)

        return self._summarise(raw)

    async def _get_metric_data(
        self,
        client,
        db_instance_id: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[float]]:
        """Calls GetMetricData for all RDS metrics in one batched request."""
        queries = []
        for i, (metric_name, unit, stat) in enumerate(_METRICS):
            queries.append(
                {
                    "Id": f"m{i}",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/RDS",
                            "MetricName": metric_name,
                            "Dimensions": [
                                {"Name": "DBInstanceIdentifier", "Value": db_instance_id}
                            ],
                        },
                        "Period": 60,
                        "Stat": stat,
                        "Unit": unit,
                    },
                    "ReturnData": True,
                }
            )

        try:
            response = await client.get_metric_data(
                MetricDataQueries=queries,
                StartTime=start,
                EndTime=end,
            )
        except Exception as exc:
            logger.warning("CloudWatch GetMetricData failed for %s: %s", db_instance_id, exc)
            return {}

        results: dict[str, list[float]] = {}
        for result in response.get("MetricDataResults", []):
            idx = int(result["Id"][1:])
            metric_name, _, stat = _METRICS[idx]
            key = f"{metric_name}_{stat.lower()}"
            results[key] = [v for v in result.get("Values", []) if v is not None]

        return results

    def _summarise(self, raw: dict[str, list[float]]) -> dict:
        """Collapses per-minute series into scalar summary values.

        Also derives a top-level 'status' field based on threshold checks.
        """
        def _max(vals: list[float]) -> float | None:
            return round(max(vals), 6) if vals else None

        def _avg(vals: list[float]) -> float | None:
            return round(sum(vals) / len(vals), 6) if vals else None

        def _min(vals: list[float]) -> float | None:
            return round(min(vals), 6) if vals else None

        summary: dict = {}

        conn_max = _max(raw.get("DatabaseConnections_maximum", []))
        conn_avg = _avg(raw.get("DatabaseConnections_average", []))
        summary["DatabaseConnections"] = {"max": conn_max, "avg": conn_avg, "unit": "Count"}

        cpu_max = _max(raw.get("CPUUtilization_maximum", []))
        cpu_avg = _avg(raw.get("CPUUtilization_average", []))
        summary["CPUUtilization"] = {"max": cpu_max, "avg": cpu_avg, "unit": "Percent"}

        mem_min = _min(raw.get("FreeableMemory_minimum", []))
        summary["FreeableMemory"] = {"min": mem_min, "unit": "Bytes"}

        rl_max = _max(raw.get("ReadLatency_maximum", []))
        rl_avg = _avg(raw.get("ReadLatency_average", []))
        summary["ReadLatency"] = {"max": rl_max, "avg": rl_avg, "unit": "Seconds"}

        wl_max = _max(raw.get("WriteLatency_maximum", []))
        wl_avg = _avg(raw.get("WriteLatency_average", []))
        summary["WriteLatency"] = {"max": wl_max, "avg": wl_avg, "unit": "Seconds"}

        # Derive health status from thresholds
        unhealthy_reasons = []
        checks = {
            "DatabaseConnections_max": conn_max,
            "CPUUtilization_max": cpu_max,
            "FreeableMemory_min": mem_min,
            "ReadLatency_max": rl_max,
            "WriteLatency_max": wl_max,
        }
        for key, value in checks.items():
            threshold = _UNHEALTHY_THRESHOLDS.get(key)
            if value is None or threshold is None:
                continue
            if "min" in key and value < threshold:
                unhealthy_reasons.append(f"{key}={value} below threshold {threshold}")
            elif "min" not in key and value > threshold:
                unhealthy_reasons.append(f"{key}={value} above threshold {threshold}")

        summary["status"] = "unhealthy" if unhealthy_reasons else "healthy"
        if unhealthy_reasons:
            summary["unhealthy_reasons"] = unhealthy_reasons

        return summary
