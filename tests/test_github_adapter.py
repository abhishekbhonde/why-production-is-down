"""Live GitHub adapter tests using httpx mocking — no real API calls."""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.adapters.github import GitHubAdapter
from src.config import settings

WINDOW_START = datetime(2024, 1, 15, 2, 20)
WINDOW_END = datetime(2024, 1, 15, 2, 56)


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", False)
    monkeypatch.setattr(settings, "github_token", "ghp_test_token")
    monkeypatch.setattr(settings, "github_org", "acme-corp")
    monkeypatch.setattr(settings, "max_diff_lines", 300)


DEPLOYMENTS_RESPONSE = [
    {
        "id": 892,
        "ref": "a3f8c21",
        "sha": "a3f8c21b9e4f2c8d1a5e7b3f9c2d4e6a8b0c1d2e",
        "environment": "production",
        "creator": {"login": "jsmith"},
        "created_at": "2024-01-15T02:43:00Z",
        "description": "fix(stripe): update webhook signature validation",
        "statuses_url": "https://api.github.com/repos/acme-corp/payment-service/deployments/892/statuses",
    }
]

COMPARE_RESPONSE = {
    "html_url": "https://github.com/acme-corp/payment-service/compare/f1e2d3c...a3f8c21",
    "base_commit": {"sha": "f1e2d3c"},
    "merge_base_commit": {"sha": "a3f8c21"},
    "files": [
        {
            "filename": "payment_service/webhooks.py",
            "additions": 3,
            "deletions": 3,
            "patch": "@@ -42,7 +42,7 @@\n-    sig = req.headers.get('stripe-signature')\n+    sig = req.headers.get('x-stripe-signature')",
        }
    ],
}


@pytest.mark.asyncio
async def test_live_fetch_returns_deployments_and_diff():
    import httpx

    call_count = 0

    async def mock_request(self, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "deployments" in str(url):
            return httpx.Response(200, json=DEPLOYMENTS_RESPONSE)
        return httpx.Response(200, json=COMPARE_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await GitHubAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["deployments"]) == 1
    deploy = result.data["deployments"][0]
    assert deploy["id"] == 892
    assert deploy["creator"] == "jsmith"

    diff = result.data["diff"]
    assert diff is not None
    assert len(diff["files_changed"]) == 1
    assert diff["files_changed"][0]["filename"] == "payment_service/webhooks.py"
    assert call_count == 2  # one deployments + one compare


@pytest.mark.asyncio
async def test_deployment_outside_window_is_excluded():
    import httpx

    outside_window = [
        {
            **DEPLOYMENTS_RESPONSE[0],
            "created_at": "2024-01-15T01:00:00Z",  # before window start
        }
    ]

    async def mock_request(self, method, url, **kwargs):
        if "deployments" in str(url):
            return httpx.Response(200, json=outside_window)
        return httpx.Response(200, json=COMPARE_RESPONSE)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await GitHubAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["deployments"] == []
    assert result.data["diff"] is None


@pytest.mark.asyncio
async def test_diff_patch_truncated_to_max_lines(monkeypatch):
    import httpx

    monkeypatch.setattr(settings, "max_diff_lines", 3)

    long_patch = "\n".join(f"line {i}" for i in range(20))
    compare_with_big_diff = {
        **COMPARE_RESPONSE,
        "files": [
            {
                "filename": "big_file.py",
                "additions": 20,
                "deletions": 0,
                "patch": long_patch,
            }
        ],
    }

    async def mock_request(self, method, url, **kwargs):
        if "deployments" in str(url):
            return httpx.Response(200, json=DEPLOYMENTS_RESPONSE)
        return httpx.Response(200, json=compare_with_big_diff)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await GitHubAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    patch_text = result.data["diff"]["files_changed"][0]["patch"]
    assert "truncated" in patch_text
    assert len(patch_text.splitlines()) == 4  # 3 kept lines + truncation note


@pytest.mark.asyncio
async def test_handles_deployments_api_error():
    import httpx

    async def mock_request(self, method, url, **kwargs):
        return httpx.Response(403, json={"message": "Forbidden"})

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await GitHubAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert result.data["deployments"] == []
    assert result.data["diff"] is None


@pytest.mark.asyncio
async def test_handles_compare_api_error():
    import httpx

    async def mock_request(self, method, url, **kwargs):
        if "deployments" in str(url):
            return httpx.Response(200, json=DEPLOYMENTS_RESPONSE)
        return httpx.Response(404, json={"message": "Not Found"})

    with patch.object(httpx.AsyncClient, "request", mock_request):
        result = await GitHubAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)

    assert result.ok
    assert len(result.data["deployments"]) == 1
    assert result.data["diff"] is None
