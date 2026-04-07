"""Tests for rollback button logic in the Slack notifier."""

import pytest

from src.agent.orchestrator import InvestigationReport
from src.notifiers.slack import _format_report, _rollback_value


def _make_report(**kwargs) -> InvestigationReport:
    defaults = dict(
        service="payment-service",
        first_failure_time="2024-01-15T02:47:03",
        alert_time="2024-01-15T02:51:00",
        root_cause="Deploy changed stripe header",
        confidence="HIGH",
        culprit={
            "type": "deploy",
            "detail": "Deploy #892 by @jsmith",
            "diff_url": "https://github.com/acme-corp/payment-service/compare/f1e2d3c...a3f8c21",
        },
        affected_services=[],
        unavailable_sources=[],
        recommended_action="Roll back deploy #892",
        investigation_seconds=42.0,
        input_tokens=8000,
        output_tokens=500,
        estimated_cost_usd=0.05,
    )
    defaults.update(kwargs)
    return InvestigationReport(**defaults)


# ---------------------------------------------------------------------------
# _rollback_value helper
# ---------------------------------------------------------------------------

def test_rollback_value_extracts_sha_and_repo():
    culprit = {
        "type": "deploy",
        "diff_url": "https://github.com/acme-corp/payment-service/compare/f1e2d3c...a3f8c21",
    }
    val = _rollback_value(culprit)
    assert val == "a3f8c21|acme-corp/payment-service"


def test_rollback_value_returns_none_for_non_deploy():
    culprit = {"type": "database", "diff_url": "https://github.com/acme/repo/compare/a...b"}
    assert _rollback_value(culprit) is None


def test_rollback_value_returns_none_when_no_diff_url():
    culprit = {"type": "deploy", "diff_url": None}
    assert _rollback_value(culprit) is None


def test_rollback_value_returns_none_for_non_github_url():
    culprit = {"type": "deploy", "diff_url": "https://gitlab.com/acme/repo/compare/a...b"}
    assert _rollback_value(culprit) is None


# ---------------------------------------------------------------------------
# Rollback button in formatted Slack blocks
# ---------------------------------------------------------------------------

def _action_ids(blocks: list[dict]) -> list[str]:
    for block in blocks:
        if block.get("type") == "actions":
            return [el.get("action_id") for el in block.get("elements", [])]
    return []


def test_rollback_button_present_for_high_confidence_deploy():
    report = _make_report(confidence="HIGH")
    blocks = _format_report(report, investigation_id="inv-123")
    assert "rollback_deploy" in _action_ids(blocks)


def test_rollback_button_absent_for_medium_confidence():
    report = _make_report(confidence="MEDIUM")
    blocks = _format_report(report, investigation_id="inv-123")
    assert "rollback_deploy" not in _action_ids(blocks)


def test_rollback_button_absent_for_non_deploy_culprit():
    report = _make_report(
        confidence="HIGH",
        culprit={"type": "database", "detail": "connection pool exhausted", "diff_url": None},
    )
    blocks = _format_report(report, investigation_id="inv-123")
    assert "rollback_deploy" not in _action_ids(blocks)


def test_rollback_button_absent_without_investigation_id():
    report = _make_report(confidence="HIGH")
    blocks = _format_report(report, investigation_id="")
    assert "rollback_deploy" not in _action_ids(blocks)


def test_rollback_button_value_encodes_sha_and_repo():
    report = _make_report(confidence="HIGH")
    blocks = _format_report(report, investigation_id="inv-123")
    for block in blocks:
        if block.get("type") == "actions":
            for el in block["elements"]:
                if el.get("action_id") == "rollback_deploy":
                    assert el["value"] == "a3f8c21|acme-corp/payment-service"
                    return
    pytest.fail("rollback_deploy button not found")


def test_rollback_button_has_confirmation_dialog():
    report = _make_report(confidence="HIGH")
    blocks = _format_report(report, investigation_id="inv-123")
    for block in blocks:
        if block.get("type") == "actions":
            for el in block["elements"]:
                if el.get("action_id") == "rollback_deploy":
                    assert "confirm" in el
                    return
    pytest.fail("rollback_deploy button not found")


def test_feedback_buttons_always_present_with_investigation_id():
    report = _make_report(confidence="LOW", culprit={"type": "unknown", "detail": ""})
    blocks = _format_report(report, investigation_id="inv-456")
    ids = _action_ids(blocks)
    assert "feedback_correct" in ids
    assert "feedback_incorrect" in ids
