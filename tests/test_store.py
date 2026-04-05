"""Tests for DB persistence, feedback recording, and weekly accuracy report."""

import pytest

from src.agent.orchestrator import InvestigationReport
from src.store import db as store


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB to a temp file for each test."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(store, "_DB_PATH", db_file)


def _make_report(**kwargs) -> InvestigationReport:
    defaults = dict(
        service="payment-service",
        first_failure_time="2024-01-15T02:47:03",
        alert_time="2024-01-15T02:51:00",
        root_cause="Deploy #892 changed stripe header",
        confidence="HIGH",
        culprit={"type": "deploy", "detail": "Deploy #892", "diff_url": "https://github.com/x"},
        affected_services=["checkout-service"],
        unavailable_sources=[],
        recommended_action="Roll back deploy #892",
        investigation_seconds=58.2,
        input_tokens=8000,
        output_tokens=500,
        estimated_cost_usd=0.0575,
    )
    defaults.update(kwargs)
    return InvestigationReport(**defaults)


@pytest.mark.asyncio
async def test_save_and_retrieve_investigation():
    await store.init()
    report = _make_report()
    inv_id = await store.save_investigation(report)

    assert inv_id == "payment-service:2024-01-15T02:51:00"

    # Verify via weekly_accuracy total count
    stats = await store.weekly_accuracy()
    assert stats["total_investigations"] == 1


@pytest.mark.asyncio
async def test_record_correct_feedback():
    await store.init()
    report = _make_report()
    inv_id = await store.save_investigation(report)

    await store.record_feedback(inv_id, "correct")

    stats = await store.weekly_accuracy()
    assert stats["correct"] == 1
    assert stats["incorrect"] == 0
    assert stats["accuracy_pct"] == 100.0


@pytest.mark.asyncio
async def test_record_incorrect_feedback():
    await store.init()
    report = _make_report()
    inv_id = await store.save_investigation(report)

    await store.record_feedback(inv_id, "incorrect")

    stats = await store.weekly_accuracy()
    assert stats["correct"] == 0
    assert stats["incorrect"] == 1
    assert stats["accuracy_pct"] == 0.0


@pytest.mark.asyncio
async def test_accuracy_with_mixed_feedback():
    await store.init()

    for i in range(3):
        r = _make_report(alert_time=f"2024-01-15T0{i}:00:00", confidence="HIGH")
        inv_id = await store.save_investigation(r)
        await store.record_feedback(inv_id, "correct")

    r = _make_report(alert_time="2024-01-15T04:00:00", confidence="LOW")
    inv_id = await store.save_investigation(r)
    await store.record_feedback(inv_id, "incorrect")

    stats = await store.weekly_accuracy()
    assert stats["total_investigations"] == 4
    assert stats["with_feedback"] == 4
    assert stats["correct"] == 3
    assert stats["incorrect"] == 1
    assert stats["accuracy_pct"] == 75.0
    assert "HIGH" in stats["by_confidence"]


@pytest.mark.asyncio
async def test_cost_aggregation():
    await store.init()
    await store.save_investigation(_make_report(
        alert_time="2024-01-15T01:00:00", estimated_cost_usd=0.05, input_tokens=5000, output_tokens=300
    ))
    await store.save_investigation(_make_report(
        alert_time="2024-01-15T02:00:00", estimated_cost_usd=0.08, input_tokens=7000, output_tokens=500
    ))

    stats = await store.weekly_accuracy()
    assert abs(stats["total_cost_usd"] - 0.13) < 0.001
    assert stats["total_input_tokens"] == 12000
    assert stats["total_output_tokens"] == 800


@pytest.mark.asyncio
async def test_invalid_verdict_raises():
    await store.init()
    with pytest.raises(ValueError, match="verdict must be"):
        await store.record_feedback("some-id", "maybe")


@pytest.mark.asyncio
async def test_no_feedback_accuracy_is_none():
    await store.init()
    await store.save_investigation(_make_report())

    stats = await store.weekly_accuracy()
    assert stats["total_investigations"] == 1
    assert stats["with_feedback"] == 0
    assert stats["accuracy_pct"] is None
