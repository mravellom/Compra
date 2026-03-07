"""
Load Test — 100 listings simultáneos a Redis Streams

Simula el flujo real: publicar → consumer group → ACK.
Requiere Redis corriendo en localhost:6379.

Ejecutar:
    pytest tests/test_load_redis.py -v -s -m load
"""
import asyncio
import os
import time

import pytest
import redis.asyncio as aioredis

from scraper.schemas import RawListing

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_KEY = "test_load_raw_listings"
GROUP_NAME = "test_load_group"
NUM_LISTINGS = 100


def make_listing(index: int) -> RawListing:
    """Genera un listing sintético con datos únicos por índice."""
    return RawListing(
        title=f"Test Product {index:04d} - Sony WH-1000XM4 Headphones",
        price=round(99.99 + index * 0.37, 2),
        currency="USD",
        url=f"https://www.ebay.com/itm/{100000 + index}",
        marketplace_id="ebay",
        image_url=f"https://i.ebayimg.com/images/test_{index}.jpg",
        raw_html_snippet=f"<div>test item {index}</div>",
    )


@pytest.fixture
def redis_available():
    """Skip test si Redis no está disponible."""
    import redis as sync_redis

    try:
        r = sync_redis.from_url(REDIS_URL)
        r.ping()
        r.close()
        return True
    except Exception:
        pytest.skip("Redis not available at " + REDIS_URL)


@pytest.mark.load
class TestRedisLoad:

    @pytest.mark.asyncio
    async def test_publish_100_concurrent(self, redis_available):
        """Publica 100 listings en paralelo con asyncio.gather."""
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.delete(STREAM_KEY)

        listings = [make_listing(i) for i in range(NUM_LISTINGS)]

        start = time.perf_counter()

        async def publish(listing: RawListing):
            await r.xadd(STREAM_KEY, listing.to_stream_dict())

        await asyncio.gather(*[publish(l) for l in listings])
        elapsed = time.perf_counter() - start

        # Verificar que los 100 llegaron
        info = await r.xinfo_stream(STREAM_KEY)
        assert info["length"] == NUM_LISTINGS

        print(f"\n  PUBLISH: {NUM_LISTINGS} msgs in {elapsed:.3f}s "
              f"({NUM_LISTINGS / elapsed:.0f} msgs/sec)")

        assert elapsed < 5.0, f"Publish too slow: {elapsed:.2f}s"

        await r.delete(STREAM_KEY)
        await r.aclose()

    @pytest.mark.asyncio
    async def test_read_100_verify_integrity(self, redis_available):
        """Publica 100, lee con XRANGE y verifica datos del primero/último."""
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.delete(STREAM_KEY)

        for i in range(NUM_LISTINGS):
            await r.xadd(STREAM_KEY, make_listing(i).to_stream_dict())

        start = time.perf_counter()
        messages = await r.xrange(STREAM_KEY, count=NUM_LISTINGS)
        elapsed = time.perf_counter() - start

        assert len(messages) == NUM_LISTINGS

        # Integridad: primer y último mensaje
        first = messages[0][1]
        assert "Test Product 0000" in first["title"]
        assert first["marketplace_id"] == "ebay"

        last = messages[-1][1]
        assert "Test Product 0099" in last["title"]

        # Precios únicos (no hay duplicados)
        prices = {m[1]["price"] for m in messages}
        assert len(prices) == NUM_LISTINGS

        print(f"\n  READ: {NUM_LISTINGS} msgs in {elapsed:.3f}s "
              f"({NUM_LISTINGS / elapsed:.0f} msgs/sec)")

        await r.delete(STREAM_KEY)
        await r.aclose()

    @pytest.mark.asyncio
    async def test_consumer_group_processes_all_under_5s(self, redis_available):
        """
        Flujo completo:
          1. Publica 100 listings
          2. Consumer group los lee en batches de 10
          3. ACK individual por mensaje
          4. Verifica 0 pending y < 5 segundos
        """
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.delete(STREAM_KEY)

        # Publicar 100 listings
        for i in range(NUM_LISTINGS):
            await r.xadd(STREAM_KEY, make_listing(i).to_stream_dict())

        # Crear consumer group
        await r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)

        # Consumir en batches (simula processor/consumer.py)
        processed = 0
        titles_seen: list[str] = []
        start = time.perf_counter()

        while processed < NUM_LISTINGS:
            messages = await r.xreadgroup(
                groupname=GROUP_NAME,
                consumername="load-test-consumer",
                streams={STREAM_KEY: ">"},
                count=10,
                block=2000,
            )
            if not messages:
                break

            for _stream, entries in messages:
                for msg_id, data in entries:
                    # Simular procesamiento mínimo
                    titles_seen.append(data["title"])
                    await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                    processed += 1

        elapsed = time.perf_counter() - start

        # Aserciones
        assert processed == NUM_LISTINGS, f"Processed {processed}, expected {NUM_LISTINGS}"
        assert len(titles_seen) == NUM_LISTINGS

        # No quedan mensajes pending
        pending = await r.xpending(STREAM_KEY, GROUP_NAME)
        assert pending["pending"] == 0

        # Performance: debe completar en < 5 segundos
        assert elapsed < 5.0, f"Consumer too slow: {elapsed:.2f}s"

        print(f"\n  CONSUMER GROUP: {NUM_LISTINGS} msgs in {elapsed:.3f}s "
              f"({NUM_LISTINGS / elapsed:.0f} msgs/sec) | 0 pending")

        await r.delete(STREAM_KEY)
        await r.aclose()
