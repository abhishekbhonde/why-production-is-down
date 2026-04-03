"""Timeline correlation tests."""

from datetime import datetime

from src.utils.timeline import TimelineEvent, correlate, extract_events, find_earliest_failure


def test_extract_datadog_events_filters_on_threshold():
    data = {
        "error_rate_series": [
            {"timestamp": "2024-01-15T02:40:00", "value": 0.1},  # below threshold
            {"timestamp": "2024-01-15T02:47:00", "value": 8.4},  # above threshold
        ]
    }
    events = extract_events(data, "datadog")
    assert len(events) == 1
    assert events[0].description.startswith("Error rate spike")


def test_extract_github_events():
    data = {
        "deployments": [
            {"id": 892, "created_at": "2024-01-15T02:43:00", "creator": "jsmith"}
        ]
    }
    events = extract_events(data, "github")
    assert len(events) == 1
    assert events[0].source == "github"


def test_correlate_sorts_by_timestamp():
    events = [
        TimelineEvent(source="datadog", timestamp=datetime(2024, 1, 15, 2, 47), description="error spike"),
        TimelineEvent(source="github", timestamp=datetime(2024, 1, 15, 2, 43), description="deploy"),
    ]
    sorted_events = correlate(events)
    assert sorted_events[0].source == "github"
    assert sorted_events[1].source == "datadog"


def test_find_earliest_failure_skips_deploys():
    events = [
        TimelineEvent(source="github", timestamp=datetime(2024, 1, 15, 2, 43), description="deploy"),
        TimelineEvent(source="datadog", timestamp=datetime(2024, 1, 15, 2, 47), description="error spike"),
    ]
    first_failure = find_earliest_failure(events)
    assert first_failure is not None
    assert first_failure.source == "datadog"
