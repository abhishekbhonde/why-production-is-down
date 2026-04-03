"""Per-adapter rate limit tracking.

Tracks API call counts per adapter per hour. Adapters check this before
making calls. In Phase 1 this is a simple in-process counter; in Phase 2+
it should be moved to Redis for multi-instance deployments.
"""

from collections import defaultdict
from datetime import datetime, timedelta

# Limits from the README
RATE_LIMITS: dict[str, int] = {
    "datadog_metrics": 300,
    "datadog_logs": 300,
    "sentry": 6000,        # 100/sec * 60 = generous per-minute, tracking per hour
    "github_rest": 5000,
    "github_graphql": 5000,
    "pagerduty": 54000,    # 900/min
}

_counts: dict[str, list[datetime]] = defaultdict(list)


def check_and_record(adapter: str) -> bool:
    """Returns True if the call is allowed, False if rate limited.

    Records the call timestamp if allowed.
    """
    limit = RATE_LIMITS.get(adapter)
    if limit is None:
        return True

    now = datetime.utcnow()
    window_start = now - timedelta(hours=1)

    # Prune old entries
    _counts[adapter] = [ts for ts in _counts[adapter] if ts > window_start]

    if len(_counts[adapter]) >= limit:
        return False

    _counts[adapter].append(now)
    return True


def current_usage(adapter: str) -> int:
    now = datetime.utcnow()
    window_start = now - timedelta(hours=1)
    _counts[adapter] = [ts for ts in _counts[adapter] if ts > window_start]
    return len(_counts[adapter])
