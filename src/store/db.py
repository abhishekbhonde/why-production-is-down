"""SQLite persistence for investigation outcomes and feedback.

Schema:
  investigations — one row per completed investigation
  feedback       — one row per thumbs up/down from Slack

The DB file lives at ./data/incidents.db by default, configurable via
DB_PATH in the environment.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from src.config import settings

logger = logging.getLogger(__name__)

_DB_PATH = Path(getattr(settings, "db_path", "data/incidents.db"))

_CREATE_INVESTIGATIONS = """
CREATE TABLE IF NOT EXISTS investigations (
    id              TEXT PRIMARY KEY,
    service         TEXT NOT NULL,
    alert_time      TEXT NOT NULL,
    first_failure   TEXT,
    root_cause      TEXT,
    confidence      TEXT,
    culprit_type    TEXT,
    culprit_detail  TEXT,
    diff_url        TEXT,
    affected        TEXT,      -- JSON list
    unavailable     TEXT,      -- JSON list
    recommended     TEXT,
    investigation_s REAL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost_usd        REAL,
    created_at      TEXT NOT NULL
)
"""

_CREATE_FEEDBACK = """
CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    investigation_id TEXT NOT NULL,
    verdict         TEXT NOT NULL,   -- 'correct' | 'incorrect'
    recorded_at     TEXT NOT NULL,
    FOREIGN KEY (investigation_id) REFERENCES investigations(id)
)
"""


async def init() -> None:
    """Creates the DB file and tables if they don't exist."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(_CREATE_INVESTIGATIONS)
        await db.execute(_CREATE_FEEDBACK)
        await db.commit()
    logger.info("DB initialised at %s", _DB_PATH)


async def save_investigation(report) -> str:
    """Persists an InvestigationReport. Returns the investigation ID."""
    inv_id = f"{report.service}:{report.alert_time}"
    culprit = report.culprit or {}

    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO investigations
            (id, service, alert_time, first_failure, root_cause, confidence,
             culprit_type, culprit_detail, diff_url, affected, unavailable,
             recommended, investigation_s, input_tokens, output_tokens, cost_usd, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                inv_id,
                report.service,
                report.alert_time,
                report.first_failure_time,
                report.root_cause,
                report.confidence,
                culprit.get("type", ""),
                culprit.get("detail", ""),
                culprit.get("diff_url", ""),
                json.dumps(report.affected_services),
                json.dumps(report.unavailable_sources),
                report.recommended_action,
                report.investigation_seconds,
                report.input_tokens,
                report.output_tokens,
                report.estimated_cost_usd,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    logger.info("Saved investigation %s", inv_id)
    return inv_id


async def record_feedback(investigation_id: str, verdict: str) -> None:
    """Records a thumbs up ('correct') or thumbs down ('incorrect')."""
    if verdict not in ("correct", "incorrect"):
        raise ValueError(f"verdict must be 'correct' or 'incorrect', got {verdict!r}")

    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT INTO feedback (investigation_id, verdict, recorded_at) VALUES (?,?,?)",
            (investigation_id, verdict, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    logger.info("Feedback recorded: %s → %s", investigation_id, verdict)


async def weekly_accuracy() -> dict:
    """Returns accuracy stats for the past 7 days.

    Returns a dict with:
      total_investigations, with_feedback, correct, incorrect, accuracy_pct
      and a breakdown by confidence level.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Total investigations in the last 7 days
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM investigations "
            "WHERE created_at >= datetime('now', '-7 days')"
        )
        row = await cur.fetchone()
        total = row["n"]

        # Feedback counts
        cur = await db.execute(
            """
            SELECT f.verdict, COUNT(*) AS n
            FROM feedback f
            JOIN investigations i ON i.id = f.investigation_id
            WHERE i.created_at >= datetime('now', '-7 days')
            GROUP BY f.verdict
            """
        )
        rows = await cur.fetchall()
        counts = {r["verdict"]: r["n"] for r in rows}
        correct = counts.get("correct", 0)
        incorrect = counts.get("incorrect", 0)
        with_feedback = correct + incorrect
        accuracy = round(correct / with_feedback * 100, 1) if with_feedback else None

        # Breakdown by confidence
        cur = await db.execute(
            """
            SELECT i.confidence, f.verdict, COUNT(*) AS n
            FROM feedback f
            JOIN investigations i ON i.id = f.investigation_id
            WHERE i.created_at >= datetime('now', '-7 days')
            GROUP BY i.confidence, f.verdict
            """
        )
        rows = await cur.fetchall()
        by_confidence: dict[str, dict] = {}
        for r in rows:
            conf = r["confidence"]
            if conf not in by_confidence:
                by_confidence[conf] = {"correct": 0, "incorrect": 0}
            by_confidence[conf][r["verdict"]] = r["n"]

        # Cost summary
        cur = await db.execute(
            "SELECT SUM(cost_usd) AS total, SUM(input_tokens) AS inp, SUM(output_tokens) AS out "
            "FROM investigations WHERE created_at >= datetime('now', '-7 days')"
        )
        cost_row = await cur.fetchone()

    return {
        "period": "last_7_days",
        "total_investigations": total,
        "with_feedback": with_feedback,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy_pct": accuracy,
        "by_confidence": by_confidence,
        "total_cost_usd": round(cost_row["total"] or 0, 4),
        "total_input_tokens": cost_row["inp"] or 0,
        "total_output_tokens": cost_row["out"] or 0,
    }
