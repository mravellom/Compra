import os

import asyncpg
from pgvector.asyncpg import register_vector

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/compraventa")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            init=_init_connection,
        )
    return _pool


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Registra el tipo vector de pgvector en cada conexión."""
    await register_vector(conn)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
