"""The flagship rule: *move → interact → consequence*, in the right order.

The design brief is explicit: repairing a leaking pipe before closing the
isolation valve must FAIL, cost a repair kit and lose water. Doing it in the
right order stops the leak and refills the water reserve.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import Api, complete_systems_check, complete_training

MISSION = "analog_water_leak"


def _ready(api: Api) -> None:
    complete_training(api)
    complete_systems_check(api)
    assert api.start(MISSION).status_code == 200


def test_repairing_before_closing_the_valve_fails(client: TestClient, api: Api) -> None:
    _ready(api)
    api.act(MISSION, "scan_leak")

    before = api.get("/resources").json()["resources"]
    kits_before = api.get("/inventory").json()["inventory"].get("repair_kit", 0)

    response = api.act(MISSION, "repair_pipe")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "precondition_failed"
    assert "valve" in error["message"].lower()
    assert error["details"]["hazard_applied"] is True
    assert error["details"]["mistakes"] == 1

    after = api.get("/resources").json()["resources"]
    assert after["water"] < before["water"], "a live pipe must cost water"
    kits_after = api.get("/inventory").json()["inventory"].get("repair_kit", 0)
    assert kits_after < kits_before, "the wasted repair kit is consumed"

    view = api.mission(MISSION).json()
    assert "repair_pipe" not in view["completed_objectives"]
    assert view["status"] == "active"


def test_scanning_is_required_before_closing_the_valve(client: TestClient, api: Api) -> None:
    _ready(api)
    response = api.act(MISSION, "close_valve")
    assert response.status_code == 409
    assert any(u["type"] == "objective" for u in response.json()["error"]["details"]["unmet"])


def test_the_correct_sequence_stops_the_leak(client: TestClient, api: Api) -> None:
    _ready(api)
    assert api.get("").json()["world"]["leak_active"] is True

    assert api.act(MISSION, "scan_leak").status_code == 200
    assert api.get("").json()["flags"]["leak.detected"] is True

    assert api.act(MISSION, "close_valve").status_code == 200
    assert api.get("").json()["world"]["leak"]["valve_closed"] is True

    water_before = api.get("/resources").json()["resources"]["water"]
    repair = api.act(MISSION, "repair_pipe").json()
    assert repair["success"] is True
    assert repair["resources"]["water"] > water_before, "fixing the loop recovers water"

    world = api.get("").json()["world"]
    assert world["leak_active"] is False
    assert world["leak"]["repaired"] is True

    final = api.act(MISSION, "verify_pressure").json()
    assert final["completed"] is True
    assert final["report"]["mistakes"] == 0
    assert final["report"]["grade"] in {"S", "A"}
    assert api.get("").json()["flags"]["water_loop_verified"] is True


def test_a_mistake_lowers_the_score(client: TestClient, api: Api) -> None:
    _ready(api)
    api.act(MISSION, "scan_leak")
    api.act(MISSION, "repair_pipe")  # wrong order → hazard
    for action in ("close_valve", "repair_pipe", "verify_pressure"):
        assert api.act(MISSION, action).status_code == 200
    report = api.get(f"/missions/{MISSION}/report").json()
    assert report["mistakes"] == 1
    assert report["mistake_penalty"] > 0
    assert report["score_pct"] < 100


def test_missing_scanner_blocks_the_scan(client: TestClient, api: Api) -> None:
    """No gear, no mission – the Training Facility loadout matters."""
    api.start("training_prep")
    for action, payload in (
        ("review_brief", {}),
        ("collect_oxygen", {}),
        ("take_repair_tools", {}),
        ("inspect_rover", {"check_mode": "quick"}),
        ("suit_check", {}),
    ):
        api.act("training_prep", action, payload)
    # confirm_readiness needs the scanner, so the campaign cannot advance yet.
    blocked = api.act("training_prep", "confirm_readiness")
    assert blocked.status_code == 409
    assert any(u.get("id") == "prep.scanner" for u in blocked.json()["error"]["details"]["unmet"])
    assert api.start(MISSION).status_code == 409


def test_failure_emits_an_event_with_the_hazard(client: TestClient, api: Api) -> None:
    _ready(api)
    api.act(MISSION, "scan_leak")
    api.act(MISSION, "repair_pipe")
    events = api.get("/events", params={"types": ["action.failed"], "limit": 10}).json()["events"]
    assert events
    payload = events[-1]["payload"]
    assert payload["action"] == "repair_pipe"
    assert payload["hazard"] is not None
    assert payload["animation"] == "fail"
    assert payload["changes"], "the HUD must be able to show what the mistake cost"


def test_interaction_map_shows_the_valve_before_the_repair(client: TestClient, api: Api) -> None:
    _ready(api)
    objects = {o["id"]: o for o in api.get(f"/missions/{MISSION}/interactions").json()["objects"]}
    repair_object = next(
        o for o in objects.values() if any(i["action"] == "repair_pipe" for i in o["interactions"])
    )
    repair = next(i for i in repair_object["interactions"] if i["action"] == "repair_pipe")
    assert repair["available"] is False

    api.act(MISSION, "scan_leak")
    api.act(MISSION, "close_valve")
    objects = {o["id"]: o for o in api.get(f"/missions/{MISSION}/interactions").json()["objects"]}
    repair_object = next(
        o for o in objects.values() if any(i["action"] == "repair_pipe" for i in o["interactions"])
    )
    repair = next(i for i in repair_object["interactions"] if i["action"] == "repair_pipe")
    assert repair["available"] is True


def test_legacy_prototype_client_still_plays_the_same_mission(client: TestClient) -> None:
    """The old unversioned frontend must keep working on the new engine."""
    created = client.post("/games", json={"player_name": "Prototype"}).json()
    game_id = created["game_id"]
    # The legacy session starts at the leak with the gear already issued.
    mission = client.get(f"/games/{game_id}/missions/water-leak").json()
    assert mission["id"] == "water_leak"
    assert mission["leak_active"] is True

    bad = client.post(
        f"/games/{game_id}/missions/water-leak/actions", json={"action": "repair_pipe"}
    )
    assert bad.status_code == 409
    assert "detail" in bad.json(), "prototype clients parse `detail`, not the v1 envelope"

    for action in ("scan_leak", "close_valve", "repair_pipe", "verify_pressure"):
        response = client.post(
            f"/games/{game_id}/missions/water-leak/actions", json={"action": action}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
    assert body["mission_status"] == "completed"
    assert body["mission"]["leak_active"] is False
