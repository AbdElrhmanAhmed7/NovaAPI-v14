"""In-request event recorder.

Every meaningful gameplay change emits an event. Events are persisted with a
monotonic per-game sequence number so the frontend can subscribe to
``WS /games/{id}/stream`` (or poll ``GET /games/{id}/events?after_seq=N``) and
drive animations/HUD updates without re-reading the whole state.

The dict produced here is *exactly* the row the log will store – same keys, same
timestamp format – because the WebSocket tail pushes it live while a reconnect
replays it from the database. One event, one representation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.utils import iso, utcnow
from app.domain.state import GameState


class EventRecorder:
    def __init__(self, state: GameState) -> None:
        self.state = state
        self.events: list[dict[str, Any]] = []

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        mission_id: str | None = None,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        self.state.event_seq += 1
        event = {
            "seq": self.state.event_seq,
            "type": event_type,
            "mission_id": mission_id,
            "payload": payload or {},
            "created_at": iso(created_at or utcnow()),
        }
        self.events.append(event)
        return event

    def extend(self, events: list[dict[str, Any]]) -> None:
        self.events.extend(events)

    def drain(self) -> list[dict[str, Any]]:
        drained = list(self.events)
        self.events.clear()
        return drained
