"""Small async helpers used by the orchestrator and tests."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, TypeVar

T = TypeVar("T")


def run_sync(coro: Awaitable[T]) -> T:
    """Run a coroutine to completion from synchronous code.

    Creates a fresh event loop so it is safe to call from tests or scripts that
    may already have (or not have) a running loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def gather_results(*coros: Awaitable[Any]) -> list[Any]:
    """Concurrently await several coroutines and return their results."""
    return await asyncio.gather(*coros)


__all__ = ["gather_results", "run_sync"]
