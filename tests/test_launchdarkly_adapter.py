"""LaunchDarkly adapter tests — no real API calls."""

from datetime import datetime
from unittest.mock import patch

import httpx
import pytest

from src.adapters.launchdarkly import LaunchDarklyAdapter
from src.config import settings

WINDOW_START = datetime(2024, 1, 15, 2, 20)
WINDOW_END = datetime(2024, 1, 15, 2, 56)

AUDIT_LOG_RESPONSE = {
    "items": [
        {
            "kind": "flag",
            "name": "payment-service-new-checkout-flow",
            "description": "Turned on flag 'payment-service-new-checkout-flow' for 100% of users",
            "date": 1705283580000,  # 2024-01-15T02:13:00 UTC in ms — inside window
            "member": {"email": "jsmith@acme.com"},
            "target": {
                "resources": ["proj/acme:env/production:flag/payment-service-new-checkout-flow"]
            },
        },
        {
            "kind": "flag",
            "name": "payment-service-stripe-v2",
            "description": "Enabled flag 'payment-service-stripe-v2' for internal users",
            "date": 1705283670000,
            "member": {"email": "alice@acme.com"},
            "target": {
                "resources": ["proj/acme:env/production:flag/payment-service-stripe-v2"]
            },
        },
        {
            # Non-flag audit entry — should be filtered out
            "kind": "project",
            "name": "acme",
            "description": "Updated project settings",
            "date": 1705283600000,
            "member": {"email": "bob@acme.com"},
            "target": {"resources": []},
        },
    ]
}


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "launchdarkly_api_key", "api-test-key")
    monkeypatch.setattr(settings, "launchdarkly_env", "production")


@pytest.mark.asyncio
async def test_returns_flag_changes():
    async def mock_request(self, method, url, **kwargs):
        return httpx.Response(200, json=AUDIT_LOG_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await LaunchDarklyAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    changes = result.data["flag_changes"]
    assert len(changes) == 2  # project entry filtered out
    assert changes[0]["flag_key"] == "payment-service-new-checkout-flow"
    assert changes[0]["changed_by"] == "jsmith@acme.com"
    assert changes[1]["flag_key"] == "payment-service-stripe-v2"


@pytest.mark.asyncio
async def test_non_flag_entries_are_filtered():
    async def mock_request(self, method, url, **kwargs):
        return httpx.Response(200, json=AUDIT_LOG_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await LaunchDarklyAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    flag_keys = [c["flag_key"] for c in result.data["flag_changes"]]
    assert "acme" not in flag_keys  # the project-kind entry must not appear


@pytest.mark.asyncio
async def test_results_sorted_chronologically():
    # Reverse the order in the API response to confirm sorting
    reversed_response = {
        "items": list(reversed(AUDIT_LOG_RESPONSE["items"]))
    }

    async def mock_request(self, method, url, **kwargs):
        return httpx.Response(200, json=reversed_response)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await LaunchDarklyAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    times = [c["changed_at"] for c in result.data["flag_changes"]]
    assert times == sorted(times)


@pytest.mark.asyncio
async def test_handles_api_error_gracefully():
    async def mock_request(self, method, url, **kwargs):
        return httpx.Response(403, json={"message": "Forbidden"})

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await LaunchDarklyAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["flag_changes"] == []


@pytest.mark.asyncio
async def test_skips_fetch_when_api_key_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "launchdarkly_api_key", "")

    call_count = 0

    async def mock_request(self, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"items": []})

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await LaunchDarklyAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["flag_changes"] == []
    assert call_count == 0  # no HTTP call made


@pytest.mark.asyncio
async def test_mock_mode_returns_fixture(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", True)

    result = await LaunchDarklyAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["flag_changes"]) == 2
    assert result.data["flag_changes"][0]["flag_key"] == "payment-service-new-checkout-flow"
