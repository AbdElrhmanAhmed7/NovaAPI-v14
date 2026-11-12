"""Simulator HTTP contract: start / tick / status / reset / scene / history.

The domain tests in ``test_sim_*`` integrate the models directly. This suite
drives the *endpoints* a 3D client calls at ~10 Hz, including the guards that
keep physics server-authoritative.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.simulations.lunar_lander import ATTITUDE_RATE, MAX_ATTITUDE
from conftest import Api

SIM_MISSIONS = {
    "lunar_landing": "lunar_lander",
    "freefall_fire": "microgravity_fire",
    "crew_health": "crew_vitals",
    "mars_rover_traverse": "rover_traverse",
}


def sim_url(mission_id: str, tail: str = "") -> str:
    return f"/missions/{mission_id}/simulation{tail}"


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------
def test_descriptor_catalogue_matches_the_missions(client: TestClient) -> None:
    descriptors = client.get("/api/v1/simulations").json()
    assert {entry["kind"] for entry in descriptors} == set(SIM_MISSIONS.values())
    for entry in descriptors:
        assert entry["max_duration_seconds"] > 0
        assert entry["telemetry_fields"], entry["kind"]
        assert entry["controls"], entry["kind"]


def test_every_sim_mission_advertises_its_simulator(client: TestClient) -> None:
    blueprints = client.get("/api/v1/content/missions").json()
    advertised = {m["id"]: m["simulation"] for m in blueprints if m["simulation"]}
    assert advertised == SIM_MISSIONS


# ---------------------------------------------------------------------------
# Guards on a fresh campaign
# ---------------------------------------------------------------------------
def test_locked_mission_cannot_start_a_run(api: Api) -> None:
    response = api.post(sim_url("lunar_landing", "/start"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "simulation_state"


def test_a_mission_without_physics_has_no_simulator(api: Api) -> None:
    response = api.post(sim_url("training_prep", "/start"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert "no simulation" in response.json()["error"]["message"]


def test_unknown_mission_is_404(api: Api) -> None:
    assert api.post(sim_url("warp_drive", "/start")).status_code == 404


def test_ticking_before_starting_is_404(api: Api) -> None:
    response = api.post(sim_url("crew_health", "/tick"), {"dt_seconds": 0.5, "command": {}})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "simulation_not_found"


def test_status_and_scene_before_starting_are_404(api: Api) -> None:
    assert api.get(sim_url("lunar_landing")).status_code == 404
    assert api.get(sim_url("lunar_landing", "/scene")).status_code == 404


def test_reset_without_a_run_is_404(api: Api) -> None:
    assert api.post(sim_url("lunar_landing", "/reset")).status_code == 404


def test_tick_rejects_a_dt_above_one_second(api: Api) -> None:
    response = api.post(sim_url("crew_health", "/tick"), {"dt_seconds": 5})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_tick_rejects_unknown_fields(api: Api) -> None:
    response = api.post(sim_url("crew_health", "/tick"), {"altitude": 0, "score_pct": 100})
    assert response.status_code == 422


def test_start_rejects_unknown_fields(api: Api) -> None:
    assert api.post(sim_url("crew_health", "/start"), {"altitude": 0}).status_code == 422


def test_run_history_is_empty_for_a_new_campaign(api: Api) -> None:
    assert api.get("/simulation-runs").json() == []


# ---------------------------------------------------------------------------
# A real run, over HTTP, on an unlocked campaign
# ---------------------------------------------------------------------------
def test_start_returns_authoritative_telemetry(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    response = api.post(sim_url("lunar_landing", "/reset"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"].startswith("run_")
    telemetry = body["telemetry"]
    assert telemetry["status"] == "running"
    assert telemetry["altitude_agl"] > 0
    assert telemetry["fuel"] > 0
    assert telemetry["vertical_speed"] <= 0


def test_status_matches_the_mission_view(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    api.post(sim_url("lunar_landing", "/reset"))
    status = api.get(sim_url("lunar_landing")).json()["telemetry"]
    view = api.mission("lunar_landing").json()
    assert view["simulation"] == "lunar_lander"
    assert view["simulation_state"]["kind"] == "lunar_lander"
    assert status["elapsed"] == view["simulation_state"]["elapsed"]


def test_scene_geometry_is_available_for_the_3d_client(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    api.post(sim_url("lunar_landing", "/reset"))
    lander = api.get(sim_url("lunar_landing", "/scene")).json()
    assert lander["kind"] == "lunar_lander"
    assert len(lander["terrain"]) >= 2

    api.post(sim_url("mars_rover_traverse", "/reset"))
    rover = api.get(sim_url("mars_rover_traverse", "/scene")).json()
    assert rover["kind"] == "rover_traverse"
    assert rover["waypoints"]
    assert isinstance(rover["hazards"], list)


def test_a_second_start_while_running_is_a_conflict(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    api.post(sim_url("crew_health", "/reset"))
    response = api.post(sim_url("crew_health", "/start"))
    assert response.status_code == 409
    assert "already active" in response.json()["error"]["message"]


def test_client_intent_is_clamped_and_never_writes_state(campaign: SimpleNamespace) -> None:
    """A hacked client cannot teleport the lander or award itself a score."""
    api: Api = campaign.api
    api.post(sim_url("lunar_landing", "/reset"))
    before = api.get(sim_url("lunar_landing")).json()["telemetry"]
    after = api.post(
        sim_url("lunar_landing", "/tick"),
        {
            "dt_seconds": 0.5,
            "command": {
                "throttle": 99,
                "attitude": -50,
                "altitude_agl": 0,
                "fuel": 9999,
                "score_pct": 100,
                "status": "succeeded",
            },
        },
    ).json()["telemetry"]
    assert after["status"] == "running"
    assert after["fuel"] <= before["fuel"]
    assert after["altitude_agl"] < before["altitude_agl"]
    # `attitude` is reported in degrees and slewed at a fixed rate: the injected
    # -50 must not snap the vehicle to an arbitrary angle.
    assert abs(after["attitude"]) <= MAX_ATTITUDE
    assert abs(after["attitude"] - before["attitude"]) <= ATTITUDE_RATE * 0.5 + 1e-6
    assert after.get("score_pct", 0) < 100


def test_the_run_history_records_every_attempt(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    runs = api.get("/simulation-runs").json()
    assert runs, "the fixture campaign should have produced runs"
    assert {run["mission_id"] for run in runs} >= set(SIM_MISSIONS)
    for run in runs:
        assert run["kind"] in SIM_MISSIONS.values()
        assert 0 <= run["score_pct"] <= 100
    filtered = api.get("/simulation-runs?mission_id=lunar_landing").json()
    assert filtered
    assert {run["mission_id"] for run in filtered} == {"lunar_landing"}


def test_a_finished_run_refuses_more_ticks_until_reset(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    api.post(sim_url("crew_health", "/reset"))
    for _ in range(400):
        body = api.post(
            sim_url("crew_health", "/tick"),
            {
                "dt_seconds": 1.0,
                "command": {
                    "monitored": True,
                    "oxygen_support": True,
                    "resting": True,
                    "cooling_applied": True,
                    "salts_administered": True,
                },
            },
        ).json()
        if body["finished"]:
            break
    assert body["finished"], "supportive care should finish the vitals run"
    assert body["result"]["score_pct"] > 0
    refused = api.post(sim_url("crew_health", "/tick"), {"dt_seconds": 0.5, "command": {}})
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["status"] in {"succeeded", "failed"}
    assert api.post(sim_url("crew_health", "/reset")).status_code == 200


def test_starting_a_run_draws_habitat_power(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    before = api.get("/resources").json()["resources"]["power"]
    api.post(sim_url("lunar_landing", "/reset"))
    after = api.get("/resources").json()["resources"]["power"]
    assert after <= before


def test_simulation_endpoints_require_a_token_when_auth_is_on(auth_client: TestClient) -> None:
    created = auth_client.post("/api/v1/games", json={"player_name": "Auth"}).json()
    game_id = created["game_id"]
    url = f"/api/v1/games/{game_id}/missions/lunar_landing/simulation/start"
    assert auth_client.post(url, json={}).status_code == 401
    assert (
        auth_client.post(
            url, json={}, headers={"X-Session-Token": created["session_token"]}
        ).status_code
        == 409  # token accepted, mission still locked
    )
