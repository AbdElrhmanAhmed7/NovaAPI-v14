"""The event bus and the socket inbox – the machinery behind the live tail.

Two invariants matter more than anything else here:

1. **the doorbell never rings before the commit** – a subscriber may only see
   events that are already durable in the log;
2. **a broken or slow subscriber can never break a gameplay request** – it is
   marked, dropped or re-synced, but the player's action still succeeds.

The queue mechanics are unit-tested directly: overflow is a race in a real
socket, and a suite that "waits and hopes" is worse than no suite at all.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.stream import MAX_BATCH, _dedupe, _Inbox, _matches, _parse_message
from app.main import create_app
from app.systems.event_bus import EventBus, SubscriptionOverflow
from conftest import Api, complete_training, make_settings, stream_url, sync


def event(seq: int, kind: str = "action.succeeded") -> dict[str, Any]:
    return {"seq": seq, "type": kind, "mission_id": None, "payload": {}, "created_at": "now"}


class Recorder:
    """A sink that remembers every batch it was handed."""

    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []

    def __call__(self, events: list[dict[str, Any]]) -> None:
        self.batches.append(events)

    @property
    def events(self) -> list[dict[str, Any]]:
        return [item for batch in self.batches for item in batch]


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------
def test_publishing_without_subscribers_is_free() -> None:
    bus = EventBus()
    assert bus.publish("nova_x", [event(1)]) == 0
    assert bus.stats() == {
        "games_watched": 0,
        "subscribers": 0,
        "published_events": 0,
        "published_batches": 0,
    }


def test_every_subscriber_of_the_game_gets_the_batch() -> None:
    bus = EventBus()
    hud, panel, other = Recorder(), Recorder(), Recorder()
    bus.subscribe("nova_a", hud)
    bus.subscribe("nova_a", panel)
    bus.subscribe("nova_b", other)

    batch = [event(1), event(2)]
    assert bus.publish("nova_a", batch) == 2
    assert hud.events == panel.events == batch
    assert other.batches == []
    assert bus.subscriber_count("nova_a") == 2
    assert bus.subscriber_count() == 3
    assert bus.stats()["published_events"] == 2
    assert bus.stats()["published_batches"] == 1


def test_the_batch_is_copied_so_a_later_mutation_cannot_rewrite_history() -> None:
    bus = EventBus()
    sink = Recorder()
    bus.subscribe("nova_a", sink)
    source = [event(1)]
    bus.publish("nova_a", source)
    source.append(event(2))
    assert sink.events == [event(1)]


def test_an_empty_batch_is_not_published() -> None:
    bus = EventBus()
    sink = Recorder()
    bus.subscribe("nova_a", sink)
    assert bus.publish("nova_a", []) == 0
    assert sink.batches == []


def test_unsubscribing_stops_delivery() -> None:
    bus = EventBus()
    sink = Recorder()
    subscription = bus.subscribe("nova_a", sink)
    bus.publish("nova_a", [event(1)])
    bus.unsubscribe(subscription)
    bus.publish("nova_a", [event(2)])
    assert [e["seq"] for e in sink.events] == [1]
    assert bus.subscriber_count("nova_a") == 0
    bus.unsubscribe(subscription)  # idempotent


def test_a_scoped_subscription_is_removed_even_when_the_body_raises() -> None:
    bus = EventBus()
    sink = Recorder()
    with pytest.raises(RuntimeError), bus.subscription("nova_a", sink):
        assert bus.subscriber_count("nova_a") == 1
        raise RuntimeError("socket blew up")
    assert bus.subscriber_count() == 0


def test_clear_drops_every_registration() -> None:
    bus = EventBus()
    bus.subscribe("nova_a", Recorder())
    bus.subscribe("nova_b", Recorder())
    bus.clear()
    assert bus.subscriber_count() == 0
    assert bus.publish("nova_a", [event(1)]) == 0


# ---------------------------------------------------------------------------
# Backpressure and failure isolation
# ---------------------------------------------------------------------------
def test_an_overflowing_sink_is_marked_and_can_acknowledge() -> None:
    bus = EventBus()

    def slow(_: list[dict[str, Any]]) -> None:
        raise SubscriptionOverflow

    subscription = bus.subscribe("nova_a", slow)
    healthy = Recorder()
    bus.subscribe("nova_a", healthy)

    assert bus.publish("nova_a", [event(1), event(2)]) == 1  # only the healthy one
    assert subscription.overflowed is True
    assert subscription.dropped == 2
    assert subscription.delivered == 0
    assert healthy.events == [event(1), event(2)]

    subscription.acknowledge()
    assert subscription.overflowed is False


def test_a_broken_sink_is_removed_and_never_breaks_the_publisher() -> None:
    bus = EventBus()

    def broken(_: list[dict[str, Any]]) -> None:
        raise ValueError("socket is gone")

    bus.subscribe("nova_a", broken)
    healthy = Recorder()
    bus.subscribe("nova_a", healthy)

    assert bus.publish("nova_a", [event(1)]) == 1
    assert bus.subscriber_count("nova_a") == 1
    assert healthy.events == [event(1)]


# ---------------------------------------------------------------------------
# The socket inbox
# ---------------------------------------------------------------------------
def test_the_inbox_merges_events_and_control_frames_in_order() -> None:
    inbox = _Inbox(8)
    inbox.offer_events([event(1)])
    inbox.offer("client", "ping")
    inbox.offer_events([event(2)])
    inbox.offer("closed")

    kinds = [kind for kind, _ in inbox.drain()]
    assert kinds == ["events", "client", "events", "closed"]
    assert inbox.drain() == []


def test_a_full_inbox_raises_subscription_overflow_for_events() -> None:
    inbox = _Inbox(1)
    inbox.offer_events([event(1)])
    with pytest.raises(SubscriptionOverflow):
        inbox.offer_events([event(2)])
    assert inbox.overflowed is True
    assert len(inbox.drain()) == 1


def test_control_frames_are_never_dropped_when_the_buffer_is_full() -> None:
    inbox = _Inbox(1)
    inbox.offer_events([event(1)])
    inbox.offer("client", "ping")  # evicts the event batch to make room
    assert inbox.overflowed is True
    assert inbox.drain() == [("client", "ping")]


def test_the_disconnect_notice_survives_a_full_buffer() -> None:
    inbox = _Inbox(2)
    inbox.offer_events([event(1)])
    inbox.offer_events([event(2)])
    inbox.offer("client", "ping")
    inbox.offer("closed")
    kinds = [kind for kind, _ in inbox.drain()]
    assert kinds[-1] == "closed", "a dropped disconnect would keep a dead socket alive"


def test_draining_is_bounded_so_one_wake_cannot_starve_the_timers() -> None:
    inbox = _Inbox(MAX_BATCH + 10)
    for seq in range(MAX_BATCH + 5):
        inbox.offer("client", str(seq))
    assert len(inbox.drain()) == MAX_BATCH
    assert len(inbox.drain()) == 5


def test_drain_can_take_the_item_the_loop_already_waited_for() -> None:
    inbox = _Inbox(4)
    inbox.offer("client", "second")
    assert [payload for _, payload in inbox.drain(("client", "first"))] == ["first", "second"]


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ping", "ping"),
        ("  PING  ", "ping"),
        ('{"type": "ping"}', "ping"),
        ('{"type": "resume", "after_seq": 12}', 12),
        ('{"type": "resume", "after_seq": "12"}', 12),
        ('{"type": "resume"}', 0),
        ('{"type": "resume", "after_seq": -1}', None),
        ('{"type": "resume", "after_seq": "tomorrow"}', None),
        ('{"type": "teleport"}', None),
        ("[1, 2, 3]", None),
        ("nonsense", None),
        ("", None),
    ],
)
def test_client_messages_are_parsed_defensively(raw: str, expected: str | int | None) -> None:
    assert _parse_message(raw) == expected


def test_type_filters_support_prefix_wildcards() -> None:
    wanted = {"mission.*", "oxygen.low"}
    assert _matches({"type": "mission.started"}, wanted)
    assert _matches({"type": "oxygen.low"}, wanted)
    assert not _matches({"type": "oxygen.refilled"}, wanted)
    assert _matches({"type": "anything"}, set())


def test_dedupe_keeps_log_order_and_drops_what_the_cursor_covered() -> None:
    batch = [event(5), event(3), event(5), event(2)]
    assert [e["seq"] for e in _dedupe(batch, cursor=2)] == [3, 5, 5]
    assert _dedupe(batch, cursor=9) == []


# ---------------------------------------------------------------------------
# Wiring: the app, the service and the log
# ---------------------------------------------------------------------------
def test_the_application_owns_one_bus_shared_with_the_game_service(
    client: TestClient, api: Api
) -> None:
    bus: EventBus = client.app.state.event_bus
    assert client.app.state.games.bus is bus

    sink = Recorder()
    with bus.subscription(api.game_id, sink):
        complete_training(api)

    stored = api.get("/events", params={"after_seq": 0, "limit": 500}).json()["events"]
    assert sink.events == stored[1:]  # game.created happened before we subscribed
    assert [e["seq"] for e in sink.events] == list(range(2, stored[-1]["seq"] + 1))
    assert bus.subscriber_count(api.game_id) == 0


def test_the_doorbell_only_rings_for_events_that_are_already_committed(
    client: TestClient, api: Api
) -> None:
    """A subscriber that reads the log immediately must find every event."""
    games = client.app.state.games
    bus: EventBus = client.app.state.event_bus
    checks: list[tuple[int, int]] = []

    async def verify(expected_seq: int) -> None:
        page = await games.events(api.game_id, after_seq=0, limit=500)
        checks.append((expected_seq, max(e["seq"] for e in page["events"])))

    def sink(events: list[dict[str, Any]]) -> None:
        asyncio.get_running_loop().create_task(verify(events[-1]["seq"]))

    with bus.subscription(api.game_id, sink):
        api.start("training_prep")
        api.act("training_prep", "review_brief")
        client.portal.call(asyncio.sleep, 0.05)

    assert checks
    for expected, visible in checks:
        assert visible >= expected, "published before the commit was durable"


def test_a_refused_action_still_publishes_its_consequences(client: TestClient, api: Api) -> None:
    """Gameplay failures are outcomes: the HUD must see them too."""
    bus: EventBus = client.app.state.event_bus
    sink = Recorder()
    api.start("training_prep")

    with bus.subscription(api.game_id, sink):
        refused = api.act("training_prep", "confirm_readiness")
        assert refused.status_code == 409

    assert any(e["type"] == "action.failed" for e in sink.events)
    tail = api.get("/events", params={"after_seq": 0, "limit": 500}).json()["events"]
    assert sink.events == tail[-len(sink.events) :]


def test_readiness_reports_the_stream_fan_out(client: TestClient, api: Api) -> None:
    """``/ready`` is where an operator sees the live tail without opening a socket."""
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        sync(socket)
        api.start("training_prep")
        sync(socket)
        body = client.get("/api/v1/ready").json()
    assert body["stream"]["subscribers"] == 1
    assert body["stream"]["games_watched"] == 1
    assert body["stream"]["published_events"] > 0
    assert body["db_calls_in_flight"] >= 0
    assert client.get("/api/v1/ready").json()["stream"]["subscribers"] == 0


def test_importing_a_save_announces_the_new_session(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/games", json={"player_name": "Mover", "difficulty": "standard", "seed": 3}
        ).json()
        game_id = created["game_id"]
        headers = {"X-Session-Token": created["session_token"]}
        bundle = client.get(f"/api/v1/games/{game_id}/export", headers=headers).json()

        bus: EventBus = client.app.state.event_bus
        published: list[tuple[str, list[dict[str, Any]]]] = []
        original = bus.publish

        def spy(target: str, events: Any) -> int:
            published.append((target, list(events)))
            return original(target, events)

        bus.publish = spy  # type: ignore[method-assign]
        try:
            imported = client.post("/api/v1/games/import", json=bundle).json()
        finally:
            bus.publish = original  # type: ignore[method-assign]

        new_id = imported["game_id"]
        assert new_id != game_id
        assert [target for target, _ in published] == [new_id]
        assert published[0][1][0]["type"] == "game.imported"


def test_a_private_bus_is_created_when_none_is_injected(content: Any, tmp_path: Path) -> None:
    """Scripts and unit tests build a GameService without an application."""
    from app.services.game_service import GameService

    service = GameService(None, content, make_settings(tmp_path))  # type: ignore[arg-type]
    assert isinstance(service.bus, EventBus)
    assert service.bus.publish("nova_x", [event(1)]) == 0
