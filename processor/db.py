"""
Database connection pools for the processor.

Pools:
  - write_pool: High-throughput listing UPSERTs. Uses synchronous_commit=off
                for ~3x faster commits (data is still crash-safe after ~600ms).
  - copy_pool:  Dedicated to COPY protocol for price_history inserts.
                Separate pool prevents COPY streams from blocking UPSERT connections.
  - read_pool:  Product index refresh, anomaly cache warmup.
"""
import os

import asyncpg
from pgvector.asyncpg import register_vector

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/compraventa")

# Write pool: listing UPSERTs + product creation
WRITE_POOL_MIN = int(os.getenv("WRITE_POOL_MIN", "10"))
WRITE_POOL_MAX = int(os.getenv("WRITE_POOL_MAX", "30"))

# COPY pool: price_history bulk inserts via COPY protocol
COPY_POOL_MIN = int(os.getenv("COPY_POOL_MIN", "2"))
COPY_POOL_MAX = int(os.getenv("COPY_POOL_MAX", "5"))

# Read pool: index refresh, anomaly cache warmup
READ_POOL_MIN = int(os.getenv("READ_POOL_MIN", "2"))
READ_POOL_MAX = int(os.getenv("READ_POOL_MAX", "5"))

HNSW_EF_SEARCH = int(os.getenv("HNSW_EF_SEARCH", "100"))

# WAL optimization: off = don't wait for WAL flush on commit (~3x faster).
# Data survives process crashes but not OS crashes within the ~600ms window.
# Acceptable tradeoff for price_history (append-only, reconstructible).
SYNC_COMMIT = os.getenv("DB_SYNC_COMMIT", "off")

_write_pool: asyncpg.Pool | None = None
_copy_pool: asyncpg.Pool | None = None
_read_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)
    await conn.execute(f"SET hnsw.ef_search = {HNSW_EF_SEARCH}")


async def _init_write_connection(conn: asyncpg.Connection) -> None:
    """Write connections: pgvector + WAL optimization."""
    await register_vector(conn)
    await conn.execute(f"SET hnsw.ef_search = {HNSW_EF_SEARCH}")
    await conn.execute(f"SET synchronous_commit = {SYNC_COMMIT}")


async def _init_copy_connection(conn: asyncpg.Connection) -> None:
    """COPY connections: WAL optimization only (no pgvector needed)."""
    await conn.execute(f"SET synchronous_commit = {SYNC_COMMIT}")


async def get_pool() -> asyncpg.Pool:
    """Get the write pool (listing UPSERTs + product creation)."""
    global _write_pool
    if _write_pool is None:
        _write_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=WRITE_POOL_MIN,
            max_size=WRITE_POOL_MAX,
            init=_init_write_connection,
        )
    return _write_pool


async def get_copy_pool() -> asyncpg.Pool:
    """Get the COPY pool (price_history bulk inserts)."""
    global _copy_pool
    if _copy_pool is None:
        _copy_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=COPY_POOL_MIN,
            max_size=COPY_POOL_MAX,
            init=_init_copy_connection,
        )
    return _copy_pool


async def get_read_pool() -> asyncpg.Pool:
    """Get the read pool (index refresh, bulk reads)."""
    global _read_pool
    if _read_pool is None:
        _read_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=READ_POOL_MIN,
            max_size=READ_POOL_MAX,
            init=_init_connection,
        )
    return _read_pool


async def close_pool() -> None:
    global _write_pool, _copy_pool, _read_pool
    for pool in (_write_pool, _copy_pool, _read_pool):
        if pool:
            await pool.close()
    _write_pool = None
    _copy_pool = None
    _read_pool = None
