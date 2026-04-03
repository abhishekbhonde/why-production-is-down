"""Orchestrator tests — stub the LLM call to test the full flow with fixture data."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.orchestrator import Alert, Orchestrator
from src.config import settings


@pytest.fixture(autouse=True)
def force_mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "mock_mode", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")


MOCK_LLM_REPORT = {
    "service": "payment-service",
    "first_failure_time": "2024-01-15T02:47:03",
    "alert_time": "2024-01-15T02:51:00",
    "root_cause": "Deploy #892 changed the Stripe webhook signature header from 'stripe-signature' to 'x-stripe-signature', causing all webhook validation to fail.",
    "confidence": "HIGH",
    "culprit": {
        "type": "deploy",
        "detail": "Deploy #892 by @jsmith — commit a3f8c21 in payment_service/webhooks.py",
        "diff_url": "https://github.com/acme-corp/payment-service/compare/f1e2d3c...a3f8c21",
    },
    "affected_services": ["payment-service", "checkout-service"],
    "unavailable_sources": [],
    "recommended_action": "Roll back deploy #892 or revert the header name change in webhooks.py",
    "investigation_seconds": 12.4,
}


@pytest.mark.asyncio
async def test_orchestrator_produces_report():
    alert = Alert(
        service="payment-service",
        alert_time=datetime(2024, 1, 15, 2, 51, 0),
        description="High error rate on payment-service",
    )

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=json.dumps(MOCK_LLM_REPORT))]

    with patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        orchestrator = Orchestrator()
        report = await orchestrator.investigate(alert)

    assert report.service == "payment-service"
    assert report.confidence == "HIGH"
    assert report.culprit["type"] == "deploy"
    assert "Roll back" in report.recommended_action


@pytest.mark.asyncio
async def test_orchestrator_handles_malformed_llm_response():
    alert = Alert(
        service="payment-service",
        alert_time=datetime(2024, 1, 15, 2, 51, 0),
        description="High error rate",
    )

    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="Sorry, I cannot determine the root cause.")]

    with patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=mock_message)

        orchestrator = Orchestrator()
        report = await orchestrator.investigate(alert)

    assert report.confidence == "UNKNOWN"
    assert report.service == "payment-service"
