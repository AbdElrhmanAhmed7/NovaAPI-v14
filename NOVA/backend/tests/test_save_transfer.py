"""Portable saves (export / import) and mission retries.

These are the two features that keep a campaign *resumable*: a player can move
their save to another server, and a player who wasted a kit can retry an
unfinished mission without being stuck. Both are pure HTTP here – the same
endpoints the React client uses.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import Api, complete_systems_check, complete_training, make_settings


def test_export_is_a_self_contained_bundle(client: TestClient, api: Api) -> None:
    complete_training(api)
    response = api.get("/export")
    assert response.status_code == 200, response.text
    bundle = response.json()

    assert bundle["format"] == "nova-save"
    assert bundle["format_version"] == 1
    assert bundle["game_id"] == api.game_id
    assert bundle["state"]["game_id"] == api.game_id
    assert bundle["state"]["player_name"] == "Test Commander"
    assert bundle["backend_version"]
    assert bundle["exported_at"]
    # Completed missions are normalised rows, not only a blob in the document.
    assert [row["mission_id"] for row in bundle["mission_results"]] == ["training_prep"]


def test_import_issues_a_new_id_and_never_overwrites(client: TestClient, api: Api) -> None:
    complete_training(api)
    bundle = api.get("/export").json()

    response = client.post("/api/v1/games/import", json=bundle)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["game_id"] != api.game_id
    assert body["session_token"]
    assert body["restored"]["mission_results"] == 1
    assert body["state"]["stats"]["missions_completed"] == 1

    # The original save is untouched and still playable.
    original = api.get("").json()
    assert original["game_id"] == api.game_id
    assert original["stats"]["missions_completed"] == 1

    imported = Api(client, body["game_id"], body["session_token"])
    assert imported.get("/reports/training_prep").status_code == 200
    # A restored campaign keeps its unlocks: stage 2 is open, not re-locked.
    catalogue = {m["id"]: m["status"] for m in imported.get("/missions").json()}
    assert catalogue["training_prep"] == "completed"
    assert catalogue["analog_systems_check"] != "locked"


def test_import_can_rename_the_commander(client: TestClient, api: Api) -> None:
    bundle = api.get("/export").json()
    response = client.post("/api/v1/games/import", json={**bundle, "player_name": "Backup Crew"})
    assert response.status_code == 201, response.text
    assert response.json()["state"]["player_name"] == "Backup Crew"


def test_importing_twice_never_collides(client: TestClient, api: Api) -> None:
    complete_training(api)
    bundle = api.get("/export").json()
    first = client.post("/api/v1/games/import", json=bundle).json()
    second = client.post("/api/v1/games/import", json=bundle).json()
    assert first["game_id"] != second["game_id"]
    assert first["restored"] == second["restored"]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"format": "some-other-game"}, "invalid_input"),
        ({"format_version": 99}, "invalid_input"),
        ({"state": {"nonsense": True}}, "invalid_input"),
    ],
)
def test_import_rejects_foreign_or_corrupt_bundles(
    client: TestClient, api: Api, mutation: dict[str, Any], expected: str
) -> None:
    bundle = {**api.get("/export").json(), **mutation}
    response = client.post("/api/v1/games/import", json=bundle)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == expected


def test_import_requires_a_state_document(client: TestClient) -> None:
    response = client.post("/api/v1/games/import", json={"format": "nova-save"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_import_drops_missions_the_server_does_not_know(client: TestClient, api: Api) -> None:
    bundle = api.get("/export").json()
    bundle["state"]["missions"]["mission_from_the_future"] = {
        "mission_id": "mission_from_the_future",
        "status": "available",
    }
    body = client.post("/api/v1/games/import", json=bundle).json()
    assert "mission_from_the_future" not in body["state"]["missions"]


def test_round_trip_preserves_science_samples_and_earth_reports(campaign: Any) -> None:
    """A finished campaign must survive a server move with its science intact."""
    api: Api = campaign.api
    bundle = api.get("/export").json()
    assert bundle["science_samples"], "the campaign should have collected samples"
    assert bundle["earth_reports"], "the campaign should have archived an Earth report"
    assert bundle["simulation_runs"], "the campaign should have simulator runs"

    imported = campaign.client.post("/api/v1/games/import", json=bundle).json()
    new_api = Api(campaign.client, imported["game_id"], imported["session_token"])

    for key in ("mission_results", "science_samples", "simulation_runs", "earth_reports"):
        assert imported["restored"][key] == len(bundle[key]), key

    old_samples = api.get("/science/samples").json()
    new_samples = new_api.get("/science/samples").json()
    assert len(new_samples) == len(old_samples)
    # Ids are re-keyed, so the same bundle can be imported again safely.
    assert {s["sample_id"] for s in new_samples}.isdisjoint({s["sample_id"] for s in old_samples})
    assert {s["rock_type"] for s in new_samples} == {s["rock_type"] for s in old_samples}

    # References *inside* the save follow the new ids – no dangling pointers.
    state = new_api.get("").json()
    sample_ids = {s["sample_id"] for s in new_samples}
    assert set(state["sample_ids"]) <= sample_ids
    reports = new_api.get("/earth/reports").json()
    assert set(state["report_ids"]) == {r["report_id"] for r in reports}
    for sample_id in state["sample_ids"]:
        assert new_api.get(f"/science/samples/{sample_id}").status_code == 200


def test_session_limit_is_enforced_on_create_and_import(tmp_path: Any) -> None:
    settings = make_settings(tmp_path, max_sessions=1)
    with TestClient(create_app(settings)) as client:
        payload = {"player_name": "First", "difficulty": "standard", "seed": 1}
        first = client.post("/api/v1/games", json=payload)
        assert first.status_code == 201
        bundle = client.get(f"/api/v1/games/{first.json()['game_id']}/export").json()

        second = client.post("/api/v1/games", json=payload)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "session_limit"

        third = client.post("/api/v1/games/import", json=bundle)
        assert third.status_code == 409
        assert third.json()["error"]["code"] == "session_limit"


# ---------------------------------------------------------------------------
# Mission retry
# ---------------------------------------------------------------------------
def test_restart_resets_objectives_but_keeps_the_cost(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    assert api.act("training_prep", "review_brief").status_code == 200
    assert api.act("training_prep", "collect_oxygen").status_code == 200
    before = api.mission("training_prep").json()
    assert before["completed_objectives"]

    response = api.post("/missions/training_prep/restart")
    assert response.status_code == 200, response.text
    body = response.json()
    # The counter only ever grows: a retry is possible, never free.
    assert body["attempt"] == before["attempts"] + 1
    assert body["mission"]["completed_objectives"] == []
    assert body["mission"]["status"] == "active"

    events = api.get("/events", params={"types": "mission.restarted"}).json()["events"]
    assert events and events[-1]["payload"]["attempt"] == body["attempt"]
    assert events[-1]["payload"]["objectives_reset"] == len(before["completed_objectives"])


def test_restart_keeps_mistakes_on_the_record(client: TestClient, api: Api) -> None:
    complete_training(api)
    complete_systems_check(api)
    assert api.start("analog_water_leak").status_code == 200
    failed = api.act("analog_water_leak", "repair_pipe")
    assert failed.status_code == 409
    assert api.mission("analog_water_leak").json()["mistakes"] == 1

    body = api.post("/missions/analog_water_leak/restart").json()
    assert body["mission"]["mistakes"] == 1
    assert body["mission"]["completed_objectives"] == []
    # Resources spent on the failed attempt stay spent.
    assert body["resources"]["water"] < 100


def test_a_completed_mission_is_immutable_history(client: TestClient, api: Api) -> None:
    complete_training(api)
    response = api.post("/missions/training_prep/restart")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_completed"


def test_a_locked_mission_cannot_be_restarted(client: TestClient, api: Api) -> None:
    response = api.post("/missions/mars_arrival/restart")
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "mission_locked"
    assert body["details"]["requires"]


def test_restart_of_an_unknown_mission_is_404(client: TestClient, api: Api) -> None:
    response = api.post("/missions/does_not_exist/restart")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "mission_not_found"
