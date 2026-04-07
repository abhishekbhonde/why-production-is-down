"""Adapter tests — run entirely against fixture data, no real API calls."""

import pytest
from datetime import datetime

from src.adapters.datadog import DatadogAdapter
from src.adapters.sentry import SentryAdapter
from src.adapters.cloudwatch import CloudWatchAdapter
from src.adapters.github import GitHubAdapter
from src.adapters.rds import RDSAdapter
from src.config import settings


@pytest.fixture(autouse=True)
def force_mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", True)


WINDOW_START = datetime(2024, 1, 15, 2, 20)
WINDOW_END = datetime(2024, 1, 15, 2, 56)


@pytest.mark.asyncio
async def test_datadog_adapter_returns_fixture():
    result = await DatadogAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)
    assert result.ok
    assert "error_rate_series" in result.data
    assert len(result.data["error_rate_series"]) > 0


@pytest.mark.asyncio
async def test_sentry_adapter_returns_fixture():
    result = await SentryAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)
    assert result.ok
    assert "error_groups" in result.data
    assert result.data["error_groups"][0]["count"] > 0


@pytest.mark.asyncio
async def test_cloudwatch_adapter_returns_fixture():
    result = await CloudWatchAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)
    assert result.ok
    assert "log_events" in result.data


@pytest.mark.asyncio
async def test_github_adapter_returns_fixture():
    result = await GitHubAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)
    assert result.ok
    assert "deployments" in result.data
    assert result.data["deployments"][0]["creator"] == "jsmith"


@pytest.mark.asyncio
async def test_rds_adapter_returns_healthy_metrics():
    result = await RDSAdapter().fetch("payment-service", WINDOW_START, WINDOW_END)
    assert result.ok
    assert result.data["status"] == "healthy"


@pytest.mark.asyncio
async def test_adapter_timeout():
    import asyncio
    from src.adapters.base import BaseAdapter

    class SlowAdapter(BaseAdapter):
        name = "slow"

        async def _fetch(self, service, start, end):
            await asyncio.sleep(999)

    settings.adapter_timeout_seconds = 0  # immediate timeout
    result = await SlowAdapter().fetch("svc", WINDOW_START, WINDOW_END)
    assert not result.ok
    assert result.timed_out
    settings.adapter_timeout_seconds = 10  # restore
