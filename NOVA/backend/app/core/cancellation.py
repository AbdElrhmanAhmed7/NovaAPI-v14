"""Cancellation-safe database calls.

Why this module exists
----------------------
A WebSocket handler is cancelled at an arbitrary await point when the peer goes
away. If that point is *inside* a SQLAlchemy greenlet call, the driver
connection is invalidated and the pool terminates it: SQLAlchemy shields a
graceful ``close()`` while force-stopping the driver thread, which can leave an
orphaned task that no longer answers cancellation. Under an anyio cancel scope
(TestClient, Starlette task groups) that orphan wedges event-loop shutdown –
the socket closes, but the process never exits.

The fix is to never let a cancellation land inside the driver: run the call in
its own task and shield it. The caller still gets cancelled immediately, while
the short database call finishes on its own and returns its connection to the
pool cleanly.

Use it for the reads a long-lived, cancellable handler performs (the event
stream's backfill and reconcile). Ordinary request handlers do not need it –
they are not cancelled mid-flight by the server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

from app.core.logging import get_logger

logger = get_logger("nova.db.guard")

T = TypeVar("T")

#: Strong references so a shielded call is never garbage collected mid-flight.
_in_flight: set[asyncio.Task[Any]] = set()


def _settle(task: asyncio.Task[Any]) -> None:
    _in_flight.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        # Nobody is waiting for this result any more; keep it out of the
        # "Task exception was never retrieved" noise but leave a trace.
        logger.debug("db.guard.late_failure", exc_info=error)


async def shield_db_call(awaitable: Awaitable[T]) -> T:
    """Await a database call without ever interrupting the driver.

    On cancellation the caller is re-cancelled at once, but the underlying call
    runs to completion so its connection is checked back in normally.
    """
    task: asyncio.Task[T] = asyncio.ensure_future(awaitable)
    _in_flight.add(task)
    task.add_done_callback(_settle)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        logger.debug("db.guard.detached", extra={"task": task.get_name()})
        raise


async def drain_in_flight(timeout: float = 2.0) -> None:  # noqa: ASYNC109 - bounded shutdown wait
    """Wait for detached calls to finish (used on shutdown, never fatal).

    The budget is deliberate: shutdown must not hang on a wedged driver, and
    the caller is a lifespan hook rather than a task with its own cancel scope.
    """
    pending = [task for task in _in_flight if not task.done()]
    if not pending:
        return
    await asyncio.wait(pending, timeout=timeout)


def in_flight_count() -> int:
    """Number of shielded calls still running (exposed by readiness)."""
    return sum(1 for task in _in_flight if not task.done())


__all__ = ["drain_in_flight", "in_flight_count", "shield_db_call"]
