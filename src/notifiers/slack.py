"""Primary notification channel: Slack.

Posts a structured incident report to the configured channel.
If delivery fails, raises SlackDeliveryError so the caller can
fall back to S3 + email.
"""

import logging
import re

import httpx

from src.agent.orchestrator import InvestigationReport
from src.config import settings

logger = logging.getLogger(__name__)

CONFIDENCE_EMOJI = {
    "HIGH": ":red_circle:",
    "MEDIUM": ":large_yellow_circle:",
    "LOW": ":white_circle:",
    "UNKNOWN": ":question:",
}


class SlackDeliveryError(Exception):
    pass


# Matches: https://github.com/{org}/{repo}/compare/{base}...{head}
_GITHUB_COMPARE_RE = re.compile(
    r"https://github\.com/([^/]+/[^/]+)/compare/[^.]+\.\.\.([0-9a-f]+)"
)


def _rollback_value(culprit: dict) -> str | None:
    """Returns '{sha}|{repo}' if the culprit is a deploy with a parseable diff URL."""
    if culprit.get("type") != "deploy":
        return None
    m = _GITHUB_COMPARE_RE.match(culprit.get("diff_url") or "")
    if not m:
        return None
    repo, sha = m.group(1), m.group(2)
    return f"{sha}|{repo}"


def _format_report(report: InvestigationReport, investigation_id: str = "") -> list[dict]:
    """Formats the report as Slack Block Kit blocks."""
    emoji = CONFIDENCE_EMOJI.get(report.confidence, ":question:")

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Incident Report: {report.service}"},
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Service*\n{report.service}"},
                {"type": "mrkdwn", "text": f"*Confidence*\n{emoji} {report.confidence}"},
                {"type": "mrkdwn", "text": f"*First Failure*\n{report.first_failure_time}"},
                {"type": "mrkdwn", "text": f"*Investigation Time*\n{report.investigation_seconds}s"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Root Cause*\n{report.root_cause}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Recommended Action*\n{report.recommended_action}",
            },
        },
    ]

    culprit = report.culprit
    if culprit.get("detail"):
        culprit_text = f"*Culprit*\nType: `{culprit['type']}`\nDetail: {culprit['detail']}"
        if culprit.get("diff_url"):
            culprit_text += f"\n<{culprit['diff_url']}|View diff>"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": culprit_text}})

    if report.unavailable_sources:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f":warning: Sources unavailable during investigation: {', '.join(report.unavailable_sources)}",
                    }
                ],
            }
        )

    # Action buttons — only shown when investigation_id is known
    if investigation_id:
        blocks.append({"type": "divider"})

        elements: list[dict] = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": ":thumbsup: Correct"},
                "style": "primary",
                "action_id": "feedback_correct",
                "value": investigation_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": ":thumbsdown: Incorrect"},
                "style": "danger",
                "action_id": "feedback_incorrect",
                "value": investigation_id,
            },
        ]

        # Rollback button — only for HIGH confidence deploy culprits with a diff URL
        if report.confidence == "HIGH":
            rollback_val = _rollback_value(report.culprit or {})
            if rollback_val:
                elements.append(
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":rewind: Roll back deploy"},
                        "style": "danger",
                        "action_id": "rollback_deploy",
                        "value": rollback_val,
                        "confirm": {
                            "title": {"type": "plain_text", "text": "Create revert PR?"},
                            "text": {
                                "type": "mrkdwn",
                                "text": "This will open a *draft* PR on GitHub that reverts the identified deploy. You still need to review and merge it.",
                            },
                            "confirm": {"type": "plain_text", "text": "Yes, create PR"},
                            "deny": {"type": "plain_text", "text": "Cancel"},
                        },
                    }
                )

        blocks.append({"type": "actions", "elements": elements})

    return blocks


async def send(report: InvestigationReport, investigation_id: str = "") -> None:
    if settings.mock_mode:
        logger.info("[MOCK] Slack report:\n%s", _format_report(report, investigation_id))
        return

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            json={
                "channel": settings.slack_channel_id,
                "blocks": _format_report(report, investigation_id),
                "text": f"Incident report for {report.service}: {report.root_cause}",
            },
            timeout=10,
        )

    data = response.json()
    if not data.get("ok"):
        raise SlackDeliveryError(f"Slack API error: {data.get('error', 'unknown')}")


async def post_thread_reply(channel: str, thread_ts: str, text: str) -> None:
    """Posts a reply into an existing Slack message thread."""
    if settings.mock_mode:
        logger.info("[MOCK] Slack thread reply (%s): %s", thread_ts, text)
        return

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            json={"channel": channel, "thread_ts": thread_ts, "text": text},
            timeout=10,
        )

    data = response.json()
    if not data.get("ok"):
        logger.warning("Failed to post Slack thread reply: %s", data.get("error", "unknown"))
