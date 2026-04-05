import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import anthropic

logger = logging.getLogger(__name__)

from src.adapters.base import AdapterResult
from src.adapters.cloudwatch import CloudWatchAdapter
from src.adapters.datadog import DatadogAdapter
from src.adapters.github import GitHubAdapter
from src.adapters.rds import RDSAdapter
from src.adapters.sentry import SentryAdapter
from src.agent.prompts import INVESTIGATION_PROMPT_TEMPLATE, SYSTEM_PROMPT
from src.config import settings
from src.utils.truncate import truncate_for_llm


@dataclass
class Alert:
    service: str
    alert_time: datetime
    description: str
    severity: str = "critical"
    incident_id: str = ""


@dataclass
class InvestigationReport:
    service: str
    first_failure_time: str
    alert_time: str
    root_cause: str
    confidence: str
    culprit: dict
    affected_services: list[str]
    unavailable_sources: list[str]
    recommended_action: str
    investigation_seconds: float
    raw_llm_response: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0


class Orchestrator:
    def __init__(self) -> None:
        self._datadog = DatadogAdapter()
        self._sentry = SentryAdapter()
        self._cloudwatch = CloudWatchAdapter()
        self._github = GitHubAdapter()
        self._rds = RDSAdapter()
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def investigate(self, alert: Alert) -> InvestigationReport:
        started_at = time.monotonic()

        window_start = alert.alert_time - timedelta(minutes=settings.investigation_window_minutes)
        window_end = alert.alert_time + timedelta(minutes=5)

        # Fan out all adapters in parallel
        results: list[AdapterResult] = await asyncio.gather(
            self._datadog.fetch(alert.service, window_start, window_end),
            self._sentry.fetch(alert.service, window_start, window_end),
            self._cloudwatch.fetch(alert.service, window_start, window_end),
            self._github.fetch(alert.service, window_start, window_end),
            self._rds.fetch(alert.service, window_start, window_end),
        )

        datadog_result, sentry_result, cloudwatch_result, github_result, rds_result = results

        unavailable = [r.source for r in results if not r.ok]

        # If no signal in the initial window, expand to 2 hours and retry
        if self._no_signal(results) and settings.investigation_window_minutes < 120:
            expanded_start = alert.alert_time - timedelta(minutes=120)
            results = await asyncio.gather(
                self._datadog.fetch(alert.service, expanded_start, window_end),
                self._sentry.fetch(alert.service, expanded_start, window_end),
                self._cloudwatch.fetch(alert.service, expanded_start, window_end),
                self._github.fetch(alert.service, expanded_start, window_end),
                self._rds.fetch(alert.service, expanded_start, window_end),
            )
            datadog_result, sentry_result, cloudwatch_result, github_result, rds_result = results

        prompt = INVESTIGATION_PROMPT_TEMPLATE.format(
            service=alert.service,
            alert_time=alert.alert_time.isoformat(),
            description=alert.description,
            severity=alert.severity,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
            datadog_data=truncate_for_llm(datadog_result.data, max_lines=settings.max_log_lines),
            sentry_data=truncate_for_llm(sentry_result.data, max_lines=settings.max_log_lines),
            cloudwatch_data=truncate_for_llm(cloudwatch_result.data, max_lines=settings.max_log_lines),
            github_data=truncate_for_llm(github_result.data, max_lines=settings.max_diff_lines),
            rds_data=truncate_for_llm(rds_result.data, max_lines=100),
            unavailable_sources=", ".join(unavailable) if unavailable else "none",
        )

        message = await self._client.messages.create(
            model="claude-opus-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_response = message.content[0].text
        elapsed = time.monotonic() - started_at

        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        # claude-opus-4-6 pricing: $15/M input, $75/M output
        estimated_cost = (input_tokens * 15 + output_tokens * 75) / 1_000_000

        logger.info(
            "Investigation for %s complete — tokens: %d in / %d out, cost: $%.4f, elapsed: %.1fs",
            alert.service,
            input_tokens,
            output_tokens,
            estimated_cost,
            elapsed,
        )

        report_data = self._parse_report(raw_response, alert, unavailable, elapsed, input_tokens, output_tokens, estimated_cost)
        return report_data

    def _no_signal(self, results: list[AdapterResult]) -> bool:
        """Returns True if all adapters returned empty or errored data."""
        for r in results:
            if r.ok and r.data:
                return False
        return True

    def _parse_report(
        self,
        raw: str,
        alert: Alert,
        unavailable: list[str],
        elapsed: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
    ) -> InvestigationReport:
        try:
            # Extract JSON block from LLM response
            start = raw.find("{")
            end = raw.rfind("}") + 1
            data = json.loads(raw[start:end])
        except (json.JSONDecodeError, ValueError):
            data = {
                "service": alert.service,
                "first_failure_time": alert.alert_time.isoformat(),
                "alert_time": alert.alert_time.isoformat(),
                "root_cause": "Failed to parse LLM response",
                "confidence": "UNKNOWN",
                "culprit": {"type": "unknown", "detail": "", "diff_url": None},
                "affected_services": [],
                "recommended_action": "Manual investigation required",
            }

        return InvestigationReport(
            service=data.get("service", alert.service),
            first_failure_time=data.get("first_failure_time", alert.alert_time.isoformat()),
            alert_time=data.get("alert_time", alert.alert_time.isoformat()),
            root_cause=data.get("root_cause", "Unknown"),
            confidence=data.get("confidence", "UNKNOWN"),
            culprit=data.get("culprit", {"type": "unknown", "detail": "", "diff_url": None}),
            affected_services=data.get("affected_services", []),
            unavailable_sources=data.get("unavailable_sources", unavailable),
            recommended_action=data.get("recommended_action", ""),
            investigation_seconds=round(elapsed, 2),
            raw_llm_response=raw,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=round(estimated_cost, 6),
        )
