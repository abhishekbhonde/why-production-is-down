"""Tool definitions passed to the Claude API for structured data fetching.

In Phase 1 these are used for documentation purposes only — the orchestrator
calls adapters directly. In Phase 2+ these will be wired to live API calls
via the Anthropic tool use API.
"""

TOOLS = [
    {
        "name": "get_error_rate",
        "description": "Get the HTTP error rate for a service over a time window from Datadog.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name"},
                "start": {"type": "string", "description": "ISO 8601 start time"},
                "end": {"type": "string", "description": "ISO 8601 end time"},
            },
            "required": ["service", "start", "end"],
        },
    },
    {
        "name": "get_error_groups",
        "description": "Get Sentry error groups for a service sorted by first seen time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["service", "start", "end"],
        },
    },
    {
        "name": "get_logs",
        "description": "Get application logs from CloudWatch for a service.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "filter_pattern": {
                    "type": "string",
                    "description": "CloudWatch filter pattern, e.g. ERROR or ?500",
                },
            },
            "required": ["service", "start", "end"],
        },
    },
    {
        "name": "get_recent_deploys",
        "description": "Get recent GitHub deployments for a service within the time window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["service", "start", "end"],
        },
    },
    {
        "name": "get_commit_diff",
        "description": "Get the diff for a specific commit or deployment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "org/repo"},
                "base": {"type": "string", "description": "base SHA or tag"},
                "head": {"type": "string", "description": "head SHA or tag"},
            },
            "required": ["repo", "base", "head"],
        },
    },
    {
        "name": "get_db_health",
        "description": "Get RDS database health metrics (connections, CPU, latency).",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
            },
            "required": ["service", "start", "end"],
        },
    },
]
