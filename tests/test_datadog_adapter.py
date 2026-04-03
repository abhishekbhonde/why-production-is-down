"""Live Datadog adapter tests using httpx mocking — no real API calls."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.adapters.datadog import DatadogAdapter
from src.config import settings

WINDOW_START = datetime(2024, 1, 15, 2, 20)
WINDOW_END = datetime(2024, 1, 15, 2, 56)


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "datadog_api_key", "test-api-key")
    monkeypatch.setattr(settings, "datadog_app_key", "test-app-key")
    monkeypatch.setattr(settings, "datadog_site", "datadoghq.com")


METRICS_RESPONSE = {
    "series": [
        {
            "pointlist": [
                [1705283220000, 0.1],
                [1705283400000, 0.2],
                [1705283820000, 8.4],
                [1705284000000, 34.7],
            ]
        }
    ]
}

LOGS_RESPONSE = {
    "data": [
        {
            "attributes": {
                "timestamp": "2024-01-15T02:47:03Z",
                "message": "ERROR SignatureVerificationError: No signatures found",
                "status": "error",
                "service": "payment-service",
            }
        }
    ]
}


@pytest.mark.asyncio
async def test_live_fetch_returns_merged_data():
    import httpx

    metrics_resp = httpx.Response(200, json=METRICS_RESPONSE)
    logs_resp = httpx.Response(200, json=LOGS_RESPONSE)

    call_count = 0

    async def mock_request(self, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "v1/query" in str(url):
            return metrics_resp
        return logs_resp

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await DatadogAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert "error_rate_series" in result.data
    assert "log_events" in result.data
    assert len(result.data["error_rate_series"]) == 4
    assert result.data["error_rate_series"][2]["value"] == 8.4
    assert len(result.data["log_events"]) == 1
    assert call_count == 2  # one metrics call + one logs call


@pytest.mark.asyncio
async def test_live_fetch_handles_metrics_api_error():
    import httpx

    async def mock_request(self, method, url, **kwargs):
        if "v1/query" in str(url):
            return httpx.Response(403, json={"errors": ["Forbidden"]})
        return httpx.Response(200, json=LOGS_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await DatadogAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok  # adapter-level errors don't fail the result
    assert result.data["error_rate_series"] == []  # gracefully empty


@pytest.mark.asyncio
async def test_live_fetch_handles_logs_api_error():
    import httpx

    async def mock_request(self, method, url, **kwargs):
        if "v2/logs" in str(url):
            return httpx.Response(429, json={"errors": ["Rate limited"]})
        return httpx.Response(200, json=METRICS_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await DatadogAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["error_rate_series"]) == 4
    assert result.data["log_events"] == []


@pytest.mark.asyncio
async def test_live_fetch_flattens_pointlist_correctly():
    import httpx

    response_with_nulls = {
        "series": [
            {
                "pointlist": [
                    [1705283220000, None],  # null value — should be filtered out
                    [1705283400000, 5.5],
                ]
            }
        ]
    }

    async def mock_request(self, method, url, **kwargs):
        if "v1/query" in str(url):
            return httpx.Response(200, json=response_with_nulls)
        return httpx.Response(200, json={"data": []})

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await DatadogAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    series = result.data["error_rate_series"]
    assert len(series) == 1  # null filtered out
    assert series[0]["value"] == 5.5
