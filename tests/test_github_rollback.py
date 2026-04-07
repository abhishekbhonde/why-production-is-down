"""Tests for create_revert_pr — no real API calls."""

from unittest.mock import patch

import httpx
import pytest

from src.adapters.github import create_revert_pr
from src.config import settings

REPO = "acme-corp/payment-service"
SHA = "a3f8c21b9e4f2c8d1a5e7b3f9c2d4e6a8b0c1d2e"
PARENT_SHA = "f1e2d3c4b5a6978869504132212345678901234a"

COMMIT_RESPONSE = {
    "sha": SHA,
    "parents": [{"sha": PARENT_SHA}],
    "commit": {"message": "fix(stripe): update webhook signature validation\n\nLong body"},
}

COMPARE_RESPONSE_NO_GAP = {"ahead_by": 0}
COMPARE_RESPONSE_WITH_GAP = {"ahead_by": 3}

REF_RESPONSE = {"ref": f"refs/heads/revert/{SHA[:7]}", "object": {"sha": PARENT_SHA}}

PR_RESPONSE = {
    "number": 101,
    "html_url": f"https://github.com/{REPO}/pull/101",
    "draft": True,
}


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "ghp_test")
    monkeypatch.setattr(settings, "github_org", "acme-corp")


def _resp(status: int, json: dict, method: str, url) -> httpx.Response:
    """Builds an httpx.Response with the request attached (required for raise_for_status)."""
    req = httpx.Request(method, url)
    return httpx.Response(status, json=json, request=req)


def _make_router(ahead_by: int = 0):
    """Returns a mock httpx request handler for the rollback flow."""
    async def mock_request(self, method, url, **kwargs):
        url_str = str(url)
        if method == "GET" and f"/commits/{SHA}" in url_str:
            return _resp(200, COMMIT_RESPONSE, method, url)
        if method == "GET" and "/compare/" in url_str:
            return _resp(200, {"ahead_by": ahead_by}, method, url)
        if method == "POST" and "/git/refs" in url_str:
            return _resp(201, REF_RESPONSE, method, url)
        if method == "POST" and "/pulls" in url_str:
            return _resp(201, PR_RESPONSE, method, url)
        return _resp(404, {"message": "Not found"}, method, url)
    return mock_request


@pytest.mark.asyncio
async def test_creates_pr_and_returns_url():
    with patch.object(httpx.AsyncClient, "request", _make_router(ahead_by=0)):
        pr_url = await create_revert_pr(REPO, SHA)

    assert pr_url == f"https://github.com/{REPO}/pull/101"


@pytest.mark.asyncio
async def test_pr_body_has_no_warning_when_no_gap():
    captured_body = {}

    async def mock_request(self, method, url, **kwargs):
        if method == "POST" and "/pulls" in str(url):
            captured_body.update(kwargs.get("json", {}))
            return _resp(201, PR_RESPONSE, method, url)
        return await _make_router(0)(self, method, url, **kwargs)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        await create_revert_pr(REPO, SHA)

    assert "Warning" not in captured_body.get("body", "")
    assert captured_body.get("draft") is True


@pytest.mark.asyncio
async def test_pr_body_warns_when_newer_commits_exist():
    captured_body = {}

    async def mock_request(self, method, url, **kwargs):
        if method == "POST" and "/pulls" in str(url):
            captured_body.update(kwargs.get("json", {}))
            return _resp(201, PR_RESPONSE, method, url)
        return await _make_router(3)(self, method, url, **kwargs)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        await create_revert_pr(REPO, SHA)

    assert "3 commit(s)" in captured_body.get("body", "")
    assert "Warning" in captured_body.get("body", "")


@pytest.mark.asyncio
async def test_pr_title_uses_first_line_of_commit_message():
    captured = {}

    async def mock_request(self, method, url, **kwargs):
        if method == "POST" and "/pulls" in str(url):
            captured.update(kwargs.get("json", {}))
            return _resp(201, PR_RESPONSE, method, url)
        return await _make_router()(self, method, url, **kwargs)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        await create_revert_pr(REPO, SHA)

    assert captured.get("title") == "revert: fix(stripe): update webhook signature validation"


@pytest.mark.asyncio
async def test_raises_when_commit_has_no_parents():
    async def mock_request(self, method, url, **kwargs):
        if method == "GET" and f"/commits/{SHA}" in str(url):
            return _resp(200, {**COMMIT_RESPONSE, "parents": []}, method, url)
        return _resp(200, {}, method, url)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        with pytest.raises(ValueError, match="no parents"):
            await create_revert_pr(REPO, SHA)


@pytest.mark.asyncio
async def test_raises_on_github_api_error():
    async def mock_request(self, method, url, **kwargs):
        return _resp(403, {"message": "Forbidden"}, method, url)

    with patch.object(httpx.AsyncClient, "request", mock_request):
        with pytest.raises(httpx.HTTPStatusError):
            await create_revert_pr(REPO, SHA)
