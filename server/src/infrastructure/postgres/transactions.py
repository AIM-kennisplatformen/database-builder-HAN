"""Shared cancellable transaction scope for Postgres adapters."""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

import anyio
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool


@asynccontextmanager
async def staging_transaction(
    pool: AsyncConnectionPool,
) -> AsyncGenerator[AsyncConnection[tuple[object, ...]]]:
    """Keep metadata staging SQL cancellable and shield transaction teardown."""
    stack = AsyncExitStack()
    try:
        connection = await stack.enter_async_context(pool.connection())
        _ = await stack.enter_async_context(connection.transaction())
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise

    try:
        yield connection
        await anyio.lowlevel.checkpoint()
    except BaseException as exc:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        with anyio.CancelScope(shield=True):
            _ = await stack.__aexit__(None, None, None)
