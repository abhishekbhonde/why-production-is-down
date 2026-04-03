"""Deduplication tests using fakeredis — no real Redis required."""

import pytest
import fakeredis.aioredis as fakeredis

from src.utils.dedup import clear, is_duplicate, mark_in_flight


@pytest.fixture
async def redis_client():
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_first_alert_is_not_duplicate(redis_client):
    assert not await is_duplicate("payment-service", redis_client)


@pytest.mark.asyncio
async def test_second_alert_within_window_is_duplicate(redis_client):
    await mark_in_flight("payment-service", redis_client)
    assert await is_duplicate("payment-service", redis_client)


@pytest.mark.asyncio
async def test_different_service_is_not_duplicate(redis_client):
    await mark_in_flight("payment-service", redis_client)
    assert not await is_duplicate("checkout-service", redis_client)


@pytest.mark.asyncio
async def test_clear_removes_dedup_key(redis_client):
    await mark_in_flight("payment-service", redis_client)
    await clear("payment-service", redis_client)
    assert not await is_duplicate("payment-service", redis_client)
