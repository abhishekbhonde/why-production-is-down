"""Live CloudWatch adapter tests using aioboto3 mocking — no real AWS calls."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.adapters.cloudwatch import CloudWatchAdapter
from src.config import settings

WINDOW_START = datetime(2024, 1, 15, 2, 20)
WINDOW_END = datetime(2024, 1, 15, 2, 56)


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    monkeypatch.setattr(settings, "max_log_lines", 200)


def _make_boto_client(filter_response=None, raises=None):
    """Returns a mock aioboto3 logs client context manager."""
    client = AsyncMock()

    if raises:
        client.filter_log_events = AsyncMock(side_effect=raises)
    else:
        client.filter_log_events = AsyncMock(return_value=filter_response or {"events": []})

    # Simulate ResourceNotFoundException class attribute
    not_found = type("ResourceNotFoundException", (Exception,), {})
    client.exceptions = MagicMock()
    client.exceptions.ResourceNotFoundException = not_found

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


FILTER_RESPONSE = {
    "events": [
        {
            "timestamp": 1705283223000,
            "message": "ERROR SignatureVerificationError: No signatures found\n",
            "logStreamName": "payment-service/app/abc123",
        },
        {
            "timestamp": 1705283224000,
            "message": "ERROR 500 POST /webhooks/stripe - 12ms\n",
            "logStreamName": "payment-service/app/abc123",
        },
    ],
    "searchedLogStreams": [],
}


@pytest.mark.asyncio
async def test_live_fetch_returns_log_events():
    client_cm = _make_boto_client(FILTER_RESPONSE)

    with patch("aioboto3.Session") as mock_session:
        mock_session.return_value.client.return_value = client_cm
        result = await CloudWatchAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["log_events"]) == 2
    assert result.data["log_events"][0]["message"] == "ERROR SignatureVerificationError: No signatures found"
    assert "logGroup" in result.data["log_events"][0]
    assert "logStream" in result.data["log_events"][0]


@pytest.mark.asyncio
async def test_tries_fallback_log_group_patterns():
    """First log group returns empty; second pattern should be tried."""
    call_count = 0
    not_found = type("ResourceNotFoundException", (Exception,), {})

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise not_found()
        return FILTER_RESPONSE

    client = AsyncMock()
    client.filter_log_events = AsyncMock(side_effect=side_effect)
    client.exceptions = MagicMock()
    client.exceptions.ResourceNotFoundException = not_found

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("aioboto3.Session") as mock_session:
        mock_session.return_value.client.return_value = cm
        result = await CloudWatchAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["log_events"]) == 2
    assert call_count == 2  # first pattern failed, second succeeded


@pytest.mark.asyncio
async def test_all_log_group_patterns_missing_returns_empty():
    not_found = type("ResourceNotFoundException", (Exception,), {})

    client = AsyncMock()
    client.filter_log_events = AsyncMock(side_effect=not_found())
    client.exceptions = MagicMock()
    client.exceptions.ResourceNotFoundException = not_found

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("aioboto3.Session") as mock_session:
        mock_session.return_value.client.return_value = cm
        result = await CloudWatchAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["log_events"] == []
    assert result.data["total_events"] == 0


@pytest.mark.asyncio
async def test_log_message_newlines_stripped():
    response = {
        "events": [
            {"timestamp": 1705283223000, "message": "ERROR something\n\n", "logStreamName": "s1"},
        ],
        "searchedLogStreams": [],
    }
    client_cm = _make_boto_client(response)

    with patch("aioboto3.Session") as mock_session:
        mock_session.return_value.client.return_value = client_cm
        result = await CloudWatchAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.data["log_events"][0]["message"] == "ERROR something"
