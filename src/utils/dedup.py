"""Redis-based alert deduplication.

Same service + same alert within DEDUP_TTL_SECONDS = one investigation.
If Redis is unavailable the investigation runs anyway (fail open).
"""

import hashlib
import logging

import redis.asyncio as redis

from src.config import settings

logger = logging.getLogger(__name__)

DEDUP_TTL_SECONDS = 300  # 5 minutes


def _alert_key(service: str) -> str:
    token = f"incident:{service}"
    return f"wpid:dedup:{hashlib.sha256(token.encode()).hexdigest()[:16]}"


async def is_duplicate(service: str, redis_client: redis.Redis) -> bool:
    """Returns True if an investigation for this service is already in flight."""
    try:
        key = _alert_key(service)
        result = await redis_client.get(key)
        return result is not None
    except Exception as exc:
        logger.warning("Redis unavailable during dedup check, proceeding without dedup: %s", exc)
        return False


async def mark_in_flight(service: str, redis_client: redis.Redis) -> None:
    """Marks an investigation as in-flight with a TTL."""
    try:
        key = _alert_key(service)
        await redis_client.setex(key, DEDUP_TTL_SECONDS, "1")
    except Exception as exc:
        logger.warning("Redis unavailable, could not mark incident in-flight: %s", exc)


async def clear(service: str, redis_client: redis.Redis) -> None:
    """Clears the dedup key once investigation completes."""
    try:
        key = _alert_key(service)
        await redis_client.delete(key)
    except Exception as exc:
        logger.warning("Redis unavailable, could not clear dedup key: %s", exc)
