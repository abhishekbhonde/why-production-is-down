"""Cross-source event correlation utilities.

Finds the earliest anomaly signal across all adapter results to establish
the true start of an incident (which may predate the alert by several minutes).
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class TimelineEvent:
    source: str
    timestamp: datetime
    description: str
    severity: str = "unknown"


def extract_events(adapter_data: dict, source: str) -> list[TimelineEvent]:
    """Extracts timestamped events from raw adapter data.

    Each adapter has its own data shape. This function normalizes them
    into a common list of TimelineEvents for correlation.
    """
    events: list[TimelineEvent] = []

    if source == "datadog" and adapter_data:
        for point in adapter_data.get("error_rate_series", []):
            if point.get("value", 0) > 1.0:  # >1% error rate = anomaly
                events.append(
                    TimelineEvent(
                        source="datadog",
                        timestamp=datetime.fromisoformat(point["timestamp"]),
                        description=f"Error rate spike: {point['value']:.1f}%",
                        severity="high" if point["value"] > 10 else "medium",
                    )
                )

    if source == "sentry" and adapter_data:
        for group in adapter_data.get("error_groups", []):
            events.append(
                TimelineEvent(
                    source="sentry",
                    timestamp=datetime.fromisoformat(group["firstSeen"]),
                    description=f"Error group: {group.get('title', 'unknown')}",
                    severity="high",
                )
            )

    if source == "github" and adapter_data:
        for deploy in adapter_data.get("deployments", []):
            events.append(
                TimelineEvent(
                    source="github",
                    timestamp=datetime.fromisoformat(deploy["created_at"]),
                    description=f"Deploy #{deploy.get('id')} by {deploy.get('creator', 'unknown')}",
                    severity="info",
                )
            )

    return events


def correlate(all_events: list[TimelineEvent]) -> list[TimelineEvent]:
    """Returns events sorted by timestamp (earliest first)."""
    return sorted(all_events, key=lambda e: e.timestamp)


def find_earliest_failure(events: list[TimelineEvent]) -> TimelineEvent | None:
    """Returns the first non-deploy event (i.e., the first error signal)."""
    for event in events:
        if event.source != "github":
            return event
    return None
