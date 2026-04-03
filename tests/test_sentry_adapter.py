"""Live Sentry adapter tests — no real API calls."""

from datetime import datetime
from unittest.mock import patch

import httpx
import pytest

from src.adapters.sentry import SentryAdapter
from src.config import settings

WINDOW_START = datetime(2024, 1, 15, 2, 20)
WINDOW_END = datetime(2024, 1, 15, 2, 56)


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "sentry_auth_token", "test-token")
    monkeypatch.setattr(settings, "sentry_org", "acme-corp")
    monkeypatch.setattr(settings, "sentry_project", "payment-service")


ISSUES_RESPONSE = [
    {
        "id": "PAYMENT-123",
        "title": "SignatureVerificationError: No signatures found",
        "firstSeen": "2024-01-15T02:47:03Z",
        "lastSeen": "2024-01-15T02:55:00Z",
        "count": "847",
        "level": "error",
        "culprit": "payment_service/webhooks.py in validate_stripe_signature",
        "tags": [
            {"key": "endpoint", "value": "POST /webhooks/stripe"},
            {"key": "http.status_code", "value": "500"},
        ],
    }
]

RELEASES_RESPONSE = [
    {
        "version": "deploy-892",
        "dateCreated": "2024-01-15T02:43:00Z",
        "dateReleased": "2024-01-15T02:43:00Z",
        "firstEvent": "2024-01-15T02:47:03Z",
        "lastEvent": "2024-01-15T02:55:00Z",
        "newGroups": 1,
    }
]


@pytest.mark.asyncio
async def test_live_fetch_returns_merged_data():
    async def mock_request(self, method, url, **kwargs):
        if "issues" in str(url):
            return httpx.Response(200, json=ISSUES_RESPONSE)
        return httpx.Response(200, json=RELEASES_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await SentryAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["error_groups"]) == 1
    assert result.data["error_groups"][0]["count"] == 847
    assert result.data["release"]["version"] == "deploy-892"
    assert result.data["release"]["newGroups"] == 1


@pytest.mark.asyncio
async def test_issues_outside_window_are_filtered():
    issues_outside = [
        {
            **ISSUES_RESPONSE[0],
            "firstSeen": "2024-01-15T01:00:00Z",  # 1hr before window
        }
    ]

    async def mock_request(self, method, url, **kwargs):
        if "issues" in str(url):
            return httpx.Response(200, json=issues_outside)
        return httpx.Response(200, json=[])

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await SentryAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["error_groups"] == []


@pytest.mark.asyncio
async def test_release_outside_window_returns_none():
    releases_outside = [
        {**RELEASES_RESPONSE[0], "dateCreated": "2024-01-14T10:00:00Z"}
    ]

    async def mock_request(self, method, url, **kwargs):
        if "issues" in str(url):
            return httpx.Response(200, json=ISSUES_RESPONSE)
        return httpx.Response(200, json=releases_outside)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await SentryAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["release"] is None


@pytest.mark.asyncio
async def test_handles_issues_api_error():
    async def mock_request(self, method, url, **kwargs):
        if "issues" in str(url):
            return httpx.Response(403, json={"detail": "Forbidden"})
        return httpx.Response(200, json=RELEASES_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await SentryAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["error_groups"] == []
    assert result.data["release"]["version"] == "deploy-892"


@pytest.mark.asyncio
async def test_handles_releases_api_error():
    async def mock_request(self, method, url, **kwargs):
        if "releases" in str(url):
            return httpx.Response(500, json={"detail": "Server Error"})
        return httpx.Response(200, json=ISSUES_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await SentryAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["error_groups"]) == 1
    assert result.data["release"] is None
