"""The WebSocket event tail – the HUD's live feed.

The contract the frontend relies on:

* the stream is a **read model over the persisted log** – a pushed event and a
  replayed event are the same document, and a reconnect with ``after_seq``
  replays exactly what was missed;
* ``ping`` is a **barrier**: everything committed before it is delivered before
  the ``pong`` (that is what makes this suite deterministic – no sleeps, no
  "read exactly N frames");
* a slow consumer is told to re-sync instead of silently losing events, a buggy
  client cannot kill its own feed, and an unauthorised socket gets a specific
  close code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.systems.event_bus import EventBus
from conftest import (
    Api,
    complete_training,
    events_in,
    make_settings,
    read_until,
    stream_url,
    sync,
)


def _new_game(client: TestClient, name: str = "Stream Commander", seed: int = 7) -> Api:
    created = client.post(
        "/api/v1/games", json={"player_name": name, "difficulty": "standard", "seed": seed}
    ).json()
    return Api(client, created["game_id"], created["session_token"])


def _log(api: Api, after_seq: int = 0) -> list[dict[str, Any]]:
    page = api.get("/events", params={"after_seq": after_seq, "limit": 500}).json()
    return list(page["events"])


# ---------------------------------------------------------------------------
# Handshake and replay
# ---------------------------------------------------------------------------
def test_stream_announces_itself_then_replays_the_log(client: TestClient, api: Api) -> None:
    complete_training(api)
    snapshot = api.get("").json()

    with client.websocket_connect(stream_url(api.game_id)) as socket:
        ready = socket.receive_json()
        assert ready["type"] == "stream.ready"
        assert ready["protocol"] == "nova.stream/1"
        assert ready["game_id"] == api.game_id
        assert ready["player_name"] == "Test Commander"
        assert ready["after_seq"] == 0
        assert ready["event_seq"] == snapshot["event_seq"]
        assert ready["resumed"] is False
        assert ready["heartbeat_seconds"] > 0

        page = socket.receive_json()
        assert page["type"] == "events"
        assert page["next_seq"] == ready["event_seq"]
        types = [event["type"] for event in page["events"]]
        assert types[0] == "game.created"
        assert "mission.completed" in types
        assert page["events"] == sorted(page["events"], key=lambda e: e["seq"])


def test_the_replay_is_paged_and_never_skips_a_sequence(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, stream_batch_size=2)
    with TestClient(create_app(settings)) as client:
        api = _new_game(client)
        complete_training(api)
        stored = _log(api)

        with client.websocket_connect(stream_url(api.game_id)) as socket:
            frames = sync(socket)

        pages = [frame for frame in frames if frame["type"] == "events"]
        assert len(pages) > 1, "a small page size must produce several frames"
        assert all(len(page["events"]) <= 2 for page in pages)
        assert [page["next_seq"] for page in pages] == sorted({page["next_seq"] for page in pages})
        assert events_in(frames) == stored
        assert frames[-1]["cursor"] == stored[-1]["seq"]


def test_a_reconnect_only_receives_what_it_missed(client: TestClient, api: Api) -> None:
    complete_training(api)
    cursor = api.get("").json()["event_seq"]

    api.start("analog_systems_check")
    api.act("analog_systems_check", "check_ventilation")
    missed = _log(api, after_seq=cursor)
    assert missed

    with client.websocket_connect(stream_url(api.game_id, after_seq=cursor)) as socket:
        ready = socket.receive_json()
        assert ready["after_seq"] == cursor
        frames = sync(socket)
        assert events_in(frames) == missed
        assert all(event["seq"] > cursor for event in events_in(frames))


def test_a_cursor_ahead_of_the_log_waits_quietly(client: TestClient, api: Api) -> None:
    head = api.get("").json()["event_seq"]
    with client.websocket_connect(stream_url(api.game_id, after_seq=head + 10)) as socket:
        frames = sync(socket)
    assert [frame["type"] for frame in frames] == ["stream.ready", "pong"]
    assert frames[-1]["cursor"] == head + 10


# ---------------------------------------------------------------------------
# Live tail
# ---------------------------------------------------------------------------
def test_events_emitted_while_connected_are_pushed(client: TestClient, api: Api) -> None:
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        sync(socket)  # ready + initial replay, drained to the pong

        api.start("training_prep")
        pushed = events_in(sync(socket))

        assert any(event["type"] == "mission.started" for event in pushed)
        assert [event["seq"] for event in pushed] == sorted({e["seq"] for e in pushed})


def test_a_pushed_event_is_the_same_document_the_log_stores(client: TestClient, api: Api) -> None:
    """The bus is a doorbell, not a second source of truth."""
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        cursor = sync(socket)[-1]["cursor"]
        complete_training(api)
        pushed = events_in(sync(socket))

    stored = _log(api, after_seq=cursor)
    assert pushed == stored, "live push and replay must be byte-identical"
    assert pushed[0]["created_at"].endswith("Z")


def test_a_ping_flushes_everything_committed_before_it(client: TestClient, api: Api) -> None:
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        first = sync(socket)
        cursor = first[-1]["cursor"]

        complete_training(api)  # eight requests, many events
        frames = sync(socket)

        pushed = events_in(frames)
        head = api.get("").json()["event_seq"]
        assert [event["seq"] for event in pushed] == list(range(cursor + 1, head + 1))
        assert frames[-1] == {"type": "pong", "cursor": head, "event_seq": head}


def test_binary_client_frames_are_understood(client: TestClient, api: Api) -> None:
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        sync(socket)
        socket.send_bytes(b"ping")
        frames = read_until(socket, lambda frame: frame["type"] == "pong")
        assert frames[-1]["cursor"] >= 0


def test_a_type_filter_delivers_matches_but_still_advances_the_cursor(
    client: TestClient, api: Api
) -> None:
    with client.websocket_connect(stream_url(api.game_id, types="mission.*")) as socket:
        ready = socket.receive_json()
        assert ready["types"] == ["mission.*"]
        sync(socket)

        complete_training(api)
        frames = sync(socket)

        pushed = events_in(frames)
        assert pushed, "the training chain emits mission events"
        assert all(event["type"].startswith("mission.") for event in pushed)
        # Filtered events are still *seen*: the cursor tracks the whole log.
        assert frames[-1]["cursor"] == api.get("").json()["event_seq"]


def test_several_exact_types_can_be_combined(client: TestClient, api: Api) -> None:
    url = stream_url(api.game_id, types="game.created,mission.started")
    with client.websocket_connect(url) as socket:
        assert socket.receive_json()["types"] == ["game.created", "mission.started"]
        sync(socket)
        api.start("training_prep")
        pushed = events_in(sync(socket))
    assert {event["type"] for event in pushed} == {"mission.started"}


# ---------------------------------------------------------------------------
# Client messages
# ---------------------------------------------------------------------------
def test_unknown_messages_answer_stream_error_and_keep_the_feed_alive(
    client: TestClient, api: Api
) -> None:
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        sync(socket)
        garbage = [
            "nonsense",
            "",
            '{"type": "teleport"}',
            "[1, 2, 3]",
            '{"type": "resume", "after_seq": -5}',
            '{"type": "resume", "after_seq": "tomorrow"}',
        ]
        for message in garbage:
            socket.send_text(message)
        frames = sync(socket)

        errors = [frame for frame in frames if frame["type"] == "stream.error"]
        assert len(errors) == len(garbage)
        assert {error["code"] for error in errors} == {"unknown_message"}
        assert frames[-1]["type"] == "pong", "the socket must survive its own client"

        api.start("training_prep")
        assert any(event["type"] == "mission.started" for event in events_in(sync(socket)))


def test_resume_replays_from_the_requested_cursor(client: TestClient, api: Api) -> None:
    complete_training(api)
    stored = _log(api)
    split = stored[len(stored) // 2]["seq"]

    with client.websocket_connect(stream_url(api.game_id)) as socket:
        sync(socket)
        socket.send_json({"type": "resume", "after_seq": split})
        frames = sync(socket)

        ready = frames[0]
        assert ready["type"] == "stream.ready"
        assert ready["resumed"] is True
        assert ready["after_seq"] == split
        assert events_in(frames) == [event for event in stored if event["seq"] > split]


def test_json_ping_is_equivalent_to_the_text_ping(client: TestClient, api: Api) -> None:
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        sync(socket)
        socket.send_json({"type": "ping"})
        assert read_until(socket, lambda frame: frame["type"] == "pong")[-1]["cursor"] >= 0


# ---------------------------------------------------------------------------
# Timers: heartbeat and multi-worker reconcile
# ---------------------------------------------------------------------------
def test_an_idle_socket_gets_heartbeats(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, stream_heartbeat_seconds=0.2)
    with TestClient(create_app(settings)) as client:
        api = _new_game(client)
        with client.websocket_connect(stream_url(api.game_id)) as socket:
            assert socket.receive_json()["heartbeat_seconds"] == 0.2
            frames = read_until(socket, lambda frame: frame["type"] == "heartbeat")
            beat = frames[-1]
            assert beat["cursor"] >= 0
            assert beat["server_time"].endswith("Z")
            # …and it keeps beating.
            assert read_until(socket, lambda frame: frame["type"] == "heartbeat")[-1]["cursor"] >= 0


def test_commits_from_another_worker_arrive_through_the_reconcile_timer(tmp_path: Path) -> None:
    """The bus is process-local; the log is not. Two apps, one database."""
    settings = make_settings(tmp_path, stream_reconcile_seconds=0.2, stream_heartbeat_seconds=30)
    with (
        TestClient(create_app(settings)) as worker_a,
        TestClient(create_app(settings)) as worker_b,
    ):
        api_a = _new_game(worker_a, name="Two Workers")
        api_b = Api(worker_b, api_a.game_id, api_a.headers.get("X-Session-Token", ""))

        with worker_a.websocket_connect(stream_url(api_a.game_id)) as socket:
            cursor = sync(socket)[-1]["cursor"]  # ready + initial replay drained
            assert worker_a.app.state.event_bus.subscriber_count(api_a.game_id) == 1
            assert worker_b.app.state.event_bus.subscriber_count(api_a.game_id) == 0

            api_b.start("training_prep")  # committed by the *other* process
            frames = read_until(socket, lambda frame: frame["type"] == "events")
            pushed = events_in(frames)
            assert any(event["type"] == "mission.started" for event in pushed)
            assert all(event["seq"] > cursor for event in pushed)


# ---------------------------------------------------------------------------
# Backpressure
# ---------------------------------------------------------------------------
def test_a_slow_consumer_is_told_to_resync_and_keeps_working(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, stream_queue_size=1)
    with TestClient(create_app(settings)) as client:
        api = _new_game(client)
        complete_training(api)
        stored = _log(api)
        head = stored[-1]["seq"]
        bus: EventBus = client.app.state.event_bus

        with client.websocket_connect(stream_url(api.game_id)) as socket:
            assert sync(socket)[-1]["cursor"] == head

            async def flood() -> None:
                # Two publishes with no await between them: the first fills the
                # one-slot buffer, the second overflows it. No timing involved.
                bus.publish(api.game_id, stored)
                bus.publish(api.game_id, stored)

            client.portal.call(flood)

            frames = read_until(socket, lambda frame: frame["type"] == "stream.resync")
            resync = frames[-1]
            assert resync["reason"] == "slow_consumer"
            assert resync["cursor"] == head
            # The duplicated batches were already covered by the cursor.
            assert events_in(frames) == []

            api.start("analog_systems_check")
            pushed = events_in(sync(socket))
            assert any(event["type"] == "mission.started" for event in pushed)
            assert all(event["seq"] > head for event in pushed)


def test_a_resync_refills_the_events_the_buffer_dropped(tmp_path: Path) -> None:
    """Overflow must cost latency, never an event.

    Worker B commits while worker A's socket is connected: A's bus never hears
    about it (process-local), so the *only* way those events can reach the
    socket is the log refill that follows ``stream.resync``.
    """
    settings = make_settings(
        tmp_path,
        stream_queue_size=1,
        stream_reconcile_seconds=30,
        stream_heartbeat_seconds=30,
    )
    with (
        TestClient(create_app(settings)) as worker_a,
        TestClient(create_app(settings)) as worker_b,
    ):
        api_a = _new_game(worker_a, name="Refill")
        api_b = Api(worker_b, api_a.game_id)
        bus: EventBus = worker_a.app.state.event_bus
        old = _log(api_a)

        with worker_a.websocket_connect(stream_url(api_a.game_id)) as socket:
            cursor = sync(socket)[-1]["cursor"]

            api_b.start("training_prep")
            fresh = _log(api_a, after_seq=cursor)
            assert fresh, "the other worker must have committed new events"

            async def flood() -> None:
                # Already-seen events: they fill the one-slot buffer and then
                # overflow it, without delivering anything new.
                bus.publish(api_a.game_id, old)
                bus.publish(api_a.game_id, old)

            worker_a.portal.call(flood)

            frames = read_until(socket, lambda frame: frame["type"] == "stream.resync")
            frames += sync(socket)
            delivered = events_in(frames)
            assert [event["seq"] for event in delivered] == [event["seq"] for event in fresh]
            assert delivered == fresh


# ---------------------------------------------------------------------------
# Limits, auth and lifecycle
# ---------------------------------------------------------------------------
def test_too_many_sockets_for_one_save_is_4429(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, stream_max_connections_per_game=1)
    with TestClient(create_app(settings)) as client:
        api = _new_game(client)
        with client.websocket_connect(stream_url(api.game_id)) as first:
            assert first.receive_json()["type"] == "stream.ready"
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect(stream_url(api.game_id)) as second:
                    second.receive_json()
            assert exc.value.code == 4429


def test_a_closed_socket_releases_its_subscription(client: TestClient, api: Api) -> None:
    bus: EventBus = client.app.state.event_bus
    with client.websocket_connect(stream_url(api.game_id)) as socket:
        socket.receive_json()
        assert bus.subscriber_count(api.game_id) == 1
    assert bus.subscriber_count(api.game_id) == 0
    assert bus.subscriber_count() == 0


def test_two_sockets_on_one_save_both_receive_the_event(client: TestClient, api: Api) -> None:
    with (
        client.websocket_connect(stream_url(api.game_id)) as hud,
        client.websocket_connect(stream_url(api.game_id)) as panel,
    ):
        sync(hud)
        sync(panel)
        api.start("training_prep")
        for socket in (hud, panel):
            assert any(event["type"] == "mission.started" for event in events_in(sync(socket)))


def test_unknown_game_is_closed_with_4404(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(stream_url("nova_does_not_exist")) as socket:
            socket.receive_json()
    assert exc.value.code == 4404


def test_negative_cursor_is_rejected(client: TestClient, api: Api) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(stream_url(api.game_id, after_seq=-1)) as socket:
            socket.receive_json()
    assert exc.value.code == 4400


def test_auth_enabled_requires_a_token_on_the_socket(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auth_enabled=True)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/games", json={"player_name": "Guarded", "difficulty": "standard", "seed": 4}
        ).json()
        game_id = created["game_id"]

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(stream_url(game_id)) as socket:
                socket.receive_json()
        assert exc.value.code == 4401

        with client.websocket_connect(
            stream_url(game_id, token=created["session_token"])
        ) as socket:
            assert socket.receive_json()["type"] == "stream.ready"


def test_a_token_for_another_game_is_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auth_enabled=True)
    with TestClient(create_app(settings)) as client:
        payload = {"player_name": "Two crews", "difficulty": "standard", "seed": 4}
        first = client.post("/api/v1/games", json=payload).json()
        second = client.post("/api/v1/games", json=payload).json()

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                stream_url(second["game_id"], token=first["session_token"])
            ) as socket:
                socket.receive_json()
        assert exc.value.code == 4401


def test_the_trusted_api_key_opens_the_socket(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auth_enabled=True, api_key="server-to-server")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/games", json={"player_name": "Ops", "difficulty": "standard", "seed": 4}
        ).json()
        url = stream_url(created["game_id"], api_key="server-to-server")
        with client.websocket_connect(url) as socket:
            assert socket.receive_json()["type"] == "stream.ready"


def test_the_session_token_header_also_works(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auth_enabled=True)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/v1/games", json={"player_name": "Headers", "difficulty": "standard", "seed": 4}
        ).json()
        url = stream_url(created["game_id"])
        with client.websocket_connect(
            url, headers={"x-session-token": created["session_token"]}
        ) as socket:
            assert socket.receive_json()["type"] == "stream.ready"
