"""Game session lifecycle: create, snapshot, resume, delete, tick, events."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from conftest import Api, complete_training


def test_create_game_returns_id_token_and_state(client: TestClient) -> None:
    response = client.post(
        "/api/v1/games", json={"player_name": "Nova Commander", "difficulty": "cadet", "seed": 11}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["game_id"].startswith("nova_")
    assert body["session_token"]
    state = body["state"]
    assert state["player_name"] == "Nova Commander"
    assert state["difficulty"] == "cadet"
    assert state["environment"] == "training_facility"
    assert state["resources"]["oxygen"] > 0
    assert state["hud"]["readiness"] == 50.0
    assert state["missions_catalogue"], "the client needs the mission list at boot"


def test_first_mission_available_and_the_rest_locked(
    client: TestClient, game: dict[str, Any]
) -> None:
    catalogue = {m["id"]: m for m in game["state"]["missions_catalogue"]}
    assert catalogue["training_prep"]["status"] == "available"
    assert catalogue["mars_arrival"]["status"] == "locked"
    assert catalogue["earth_trends"]["status"] == "locked"


def test_player_name_is_validated(client: TestClient) -> None:
    assert client.post("/api/v1/games", json={"player_name": ""}).status_code == 422
    assert client.post("/api/v1/games", json={"player_name": "   "}).status_code == 422
    assert client.post("/api/v1/games", json={"player_name": "x" * 51}).status_code == 422
    assert client.post("/api/v1/games", json={}).status_code == 422


def test_unknown_field_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/games", json={"player_name": "Ok", "cheat": True})
    assert response.status_code == 422


def test_difficulty_and_seed_are_bounded(client: TestClient) -> None:
    assert (
        client.post("/api/v1/games", json={"player_name": "A", "difficulty": "god"}).status_code
        == 422
    )
    assert client.post("/api/v1/games", json={"player_name": "A", "seed": -1}).status_code == 422


def test_snapshot_is_the_hud_bootstrap(client: TestClient, api: Api) -> None:
    body = api.get("").json()
    for key in (
        "game_id",
        "resources",
        "resource_caps",
        "inventory",
        "suit",
        "equipment",
        "missions",
        "missions_catalogue",
        "continuity",
        "stats",
        "hud",
        "recent_events",
    ):
        assert key in body, key
    assert body["hud"]["active_mission"] is None


def test_snapshot_event_limit_is_respected(client: TestClient, api: Api) -> None:
    complete_training(api)
    body = api.get("", params={"event_limit": 3}).json()
    assert len(body["recent_events"]) <= 3
    assert api.get("", params={"event_limit": 999}).status_code == 422


def test_missing_game_is_404_everywhere(client: TestClient) -> None:
    for path in ("", "/resources", "/inventory", "/missions", "/continuity", "/reports", "/suit"):
        response = client.get(f"/api/v1/games/nova_missing{path}")
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "session_not_found"


def test_list_games_filters_and_paginates(client: TestClient) -> None:
    for name in ("Alpha", "Alpha", "Beta"):
        assert client.post("/api/v1/games", json={"player_name": name}).status_code == 201
    everyone = client.get("/api/v1/games").json()
    assert len(everyone) == 3
    alphas = client.get("/api/v1/games", params={"player_name": "Alpha"}).json()
    assert len(alphas) == 2
    page = client.get("/api/v1/games", params={"limit": 1, "offset": 1}).json()
    assert len(page) == 1
    assert client.get("/api/v1/games", params={"limit": 0}).status_code == 422


def test_resume_reissues_a_token_and_keeps_progress(client: TestClient, api: Api) -> None:
    complete_training(api)
    response = api.post("/resume")
    assert response.status_code == 200
    body = response.json()
    assert body["game_id"] == api.game_id
    assert body["session_token"]
    assert body["state"]["missions"]["training_prep"]["status"] == "completed"


def test_delete_removes_the_save_and_children(client: TestClient, api: Api) -> None:
    complete_training(api)
    assert api.client.delete(f"/api/v1/games/{api.game_id}").status_code == 200
    assert api.get("").status_code == 404
    assert api.get("/events").status_code == 404
    assert api.get("/reports").status_code == 404


def test_tick_drains_oxygen_and_reports_telemetry(client: TestClient, api: Api) -> None:
    before = api.get("/resources").json()["resources"]["oxygen"]
    response = api.post("/tick", {"dt_seconds": 5, "activity": "run"})
    assert response.status_code == 200
    body = response.json()
    telemetry = body["telemetry"]
    assert telemetry["activity"] == "run"
    assert telemetry["rate_per_second"] > 0
    assert telemetry["oxygen_after"] <= before
    assert body["resources"]["oxygen"] == telemetry["oxygen_after"]


def test_tick_clamps_a_huge_dt(client: TestClient, api: Api) -> None:
    """A backgrounded tab must not fast-forward life support."""
    body = api.post("/tick", {"dt_seconds": 60, "activity": "eva"}).json()
    assert body["telemetry"]["dt_seconds"] == 5.0


def test_tick_rejects_invalid_activity_and_dt(client: TestClient, api: Api) -> None:
    assert api.post("/tick", {"dt_seconds": 1, "activity": "fly"}).status_code == 422
    assert api.post("/tick", {"dt_seconds": 0}).status_code == 422
    assert api.post("/tick", {"dt_seconds": -3}).status_code == 422
    assert api.post("/tick", {"dt_seconds": 61}).status_code == 422


def test_tick_accumulates_playtime(client: TestClient, api: Api) -> None:
    for _ in range(3):
        api.post("/tick", {"dt_seconds": 2, "activity": "walk"})
    stats = api.get("").json()["stats"]
    assert stats["playtime_seconds"] >= 6


def test_events_are_ordered_and_cursor_paginated(client: TestClient, api: Api) -> None:
    complete_training(api)
    page = api.get("/events", params={"limit": 5}).json()
    seqs = [event["seq"] for event in page["events"]]
    assert seqs == sorted(seqs)
    assert page["has_more"] is True
    nxt = api.get("/events", params={"after_seq": page["next_seq"], "limit": 5}).json()
    assert all(event["seq"] > page["next_seq"] for event in nxt["events"])


def test_events_can_be_filtered_by_type(client: TestClient, api: Api) -> None:
    complete_training(api)
    page = api.get("/events", params={"types": ["mission.completed"], "limit": 50}).json()
    assert page["events"]
    assert {event["type"] for event in page["events"]} == {"mission.completed"}


def test_leaderboard_ranks_campaigns(client: TestClient, api: Api) -> None:
    complete_training(api)
    board = client.get("/api/v1/leaderboard").json()
    assert board
    assert board[0]["rank"] == 1
    assert any(entry["game_id"] == api.game_id for entry in board)
    assert client.get("/api/v1/leaderboard", params={"limit": 101}).status_code == 422


def test_two_games_are_isolated(client: TestClient) -> None:
    a = client.post("/api/v1/games", json={"player_name": "A"}).json()
    b = client.post("/api/v1/games", json={"player_name": "B"}).json()
    api_a, api_b = Api(client, a["game_id"]), Api(client, b["game_id"])
    complete_training(api_a)
    assert api_a.get("").json()["missions"]["training_prep"]["status"] == "completed"
    assert api_b.get("").json()["missions"]["training_prep"]["status"] == "available"


def test_same_seed_reproduces_the_same_start(client: TestClient) -> None:
    payloads = [
        client.post("/api/v1/games", json={"player_name": "Seed", "seed": 99}).json()["state"]
        for _ in range(2)
    ]
    assert payloads[0]["resources"] == payloads[1]["resources"]
    assert payloads[0]["world"] == payloads[1]["world"]
