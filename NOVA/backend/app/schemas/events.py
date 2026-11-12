"""Event log / websocket contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GameEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    seq: int
    type: str
    mission_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: Any = None


class EventPage(BaseModel):
    game_id: str
    after_seq: int
    events: list[GameEvent] = Field(default_factory=list)
    next_seq: int = 0
    has_more: bool = False


class StreamStats(BaseModel):
    """Live fan-out counters, as reported by the process-local event bus.

    Process-local by design: with several workers each one reports its own
    sockets, which is exactly what an operator debugging a sticky-session
    problem needs to see.
    """

    games_watched: int = Field(default=0, description="Saves with at least one open socket.")
    subscribers: int = Field(default=0, description="Open sockets across every save.")
    published_events: int = Field(default=0, description="Events handed to subscribers.")
    published_batches: int = Field(default=0, description="Fan-out calls that had a subscriber.")


class LeaderboardEntry(BaseModel):
    rank: int
    game_id: str
    player_name: str
    missions_completed: int
    readiness: float
    average_score_pct: float
