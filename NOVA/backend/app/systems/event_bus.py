"""In-process fan-out of the authoritative event log.

The persisted log (``events`` table) stays the single source of truth. This bus
only *notifies* connected sockets that something new was committed, so the
WebSocket tail never has to poll the database in a loop:

* ``GameService`` publishes **after** the transaction commits, so a subscriber
  can never see an event that was rolled back.
* Subscribers are per game session; a busy campaign fans out to every HUD,
  mission panel and spectator socket that is watching that save.
* Publishing is synchronous, non-blocking and failure-proof: a broken or slow
  subscriber can never break a gameplay request.

Backpressure is the subscriber's business: a sink that cannot keep up raises
:class:`SubscriptionOverflow`, the bus marks the subscription as overflowed and
the socket re-syncs from the log using its cursor. Nothing is lost – the log is
authoritative, the bus is only a doorbell.

Multi-worker deployments still work: the bus is process-local, so the stream
also reconciles against the database on a slow timer (see ``api/v1/stream.py``).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.core.utils import utcnow

logger = get_logger("nova.events.bus")

#: A sink receives the committed events for one game, in log order.
Sink = Callable[[list[dict[str, Any]]], None]


class SubscriptionOverflow(Exception):
    """Raised by a sink whose buffer is full – the socket must re-sync."""


@dataclass(slots=True)
class Subscription:
    """One socket's registration on the bus."""

    id: int
    game_id: str
    sink: Sink
    created_at: str = field(default_factory=lambda: utcnow().isoformat())
    delivered: int = 0
    dropped: int = 0
    overflowed: bool = False

    def acknowledge(self) -> None:
        """Called by the socket after it re-synced from the log."""
        self.overflowed = False


class EventBus:
    """Process-local publish/subscribe for gameplay events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, dict[int, Subscription]] = {}
        self._ids = itertools.count(1)
        self.published_events = 0
        self.published_batches = 0

    # -- registration -------------------------------------------------------
    def subscribe(self, game_id: str, sink: Sink) -> Subscription:
        subscription = Subscription(id=next(self._ids), game_id=game_id, sink=sink)
        self._subscribers.setdefault(game_id, {})[subscription.id] = subscription
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        bucket = self._subscribers.get(subscription.game_id)
        if bucket is None:
            return
        bucket.pop(subscription.id, None)
        if not bucket:
            self._subscribers.pop(subscription.game_id, None)

    @contextmanager
    def subscription(self, game_id: str, sink: Sink) -> Iterator[Subscription]:
        """Scoped registration – always unregistered, even on cancellation."""
        subscription = self.subscribe(game_id, sink)
        try:
            yield subscription
        finally:
            self.unsubscribe(subscription)

    # -- publishing ---------------------------------------------------------
    def publish(self, game_id: str, events: Sequence[dict[str, Any]]) -> int:
        """Notify every subscriber of ``game_id``. Never raises."""
        if not events:
            return 0
        bucket = self._subscribers.get(game_id)
        if not bucket:
            return 0

        batch = list(events)
        self.published_events += len(batch)
        self.published_batches += 1
        notified = 0
        for subscription in list(bucket.values()):
            try:
                subscription.sink(batch)
            except SubscriptionOverflow:
                subscription.overflowed = True
                subscription.dropped += len(batch)
                logger.warning(
                    "events.subscriber_overflow",
                    extra={"game_id": game_id, "subscription": subscription.id},
                )
            except Exception:  # pragma: no cover - a broken socket must not break gameplay
                logger.exception(
                    "events.subscriber_failed",
                    extra={"game_id": game_id, "subscription": subscription.id},
                )
                self.unsubscribe(subscription)
            else:
                subscription.delivered += len(batch)
                notified += 1
        return notified

    # -- introspection ------------------------------------------------------
    def subscriber_count(self, game_id: str | None = None) -> int:
        if game_id is not None:
            return len(self._subscribers.get(game_id, ()))
        return sum(len(bucket) for bucket in self._subscribers.values())

    def stats(self) -> dict[str, Any]:
        return {
            "games_watched": len(self._subscribers),
            "subscribers": self.subscriber_count(),
            "published_events": self.published_events,
            "published_batches": self.published_batches,
        }

    def clear(self) -> None:
        """Drop every registration (shutdown)."""
        self._subscribers.clear()


__all__ = ["EventBus", "Sink", "Subscription", "SubscriptionOverflow"]
