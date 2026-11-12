"""Mission lifecycle: locking, starting, acting, completing and reporting."""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import Api, complete_systems_check, complete_training


def test_catalogue_tracks_progress(client: TestClient, api: Api) -> None:
    before = {m["id"]: m for m in api.get("/missions").json()}
    assert before["training_prep"]["objectives_done"] == 0
    complete_training(api)
    after = {m["id"]: m for m in api.get("/missions").json()}
    assert after["training_prep"]["status"] == "completed"
    assert after["training_prep"]["objectives_done"] == after["training_prep"]["objectives_total"]
    assert after["analog_systems_check"]["status"] == "available"


def test_locked_mission_cannot_be_started(client: TestClient, api: Api) -> None:
    response = api.start("mars_arrival")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "mission_locked"
    assert error["details"]["mission_id"] == "mars_arrival"


def test_acting_on_a_locked_mission_is_refused(client: TestClient, api: Api) -> None:
    response = api.act("mars_arrival", "exit_lander")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_locked"


def test_unknown_mission_is_404(client: TestClient, api: Api) -> None:
    assert api.mission("no_such_mission").status_code == 404
    assert api.start("no_such_mission").status_code == 404


def test_start_returns_the_mission_view(client: TestClient, api: Api) -> None:
    body = api.start("training_prep").json()
    assert body["mission"]["status"] == "active"
    assert body["mission"]["id"] == "training_prep"
    objectives = body["mission"]["objectives"]
    assert objectives[0]["available"] is True
    assert objectives[-1]["available"] is False
    assert objectives[-1]["unmet"], "confirm_readiness must explain what is missing"


def test_action_response_carries_animation_and_deltas(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    body = api.act("training_prep", "review_brief").json()
    assert body["success"] is True
    assert body["animation"]
    assert body["duration_ms"] > 0
    assert body["message"]
    assert "changes" in body
    assert body["mission"]["status"] == "active"
    assert body["completed"] is False
    assert "resources" in body and "inventory" in body


def test_unknown_action_lists_allowed_actions(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    response = api.act("training_prep", "teleport_to_mars")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "unknown_action"
    assert "review_brief" in error["details"]["allowed"]


def test_repeating_a_completed_objective_is_a_conflict(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    api.act("training_prep", "review_brief")
    response = api.act("training_prep", "review_brief")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "already_completed"


def test_acting_without_start_auto_starts(client: TestClient, api: Api) -> None:
    response = api.act("training_prep", "review_brief")
    assert response.status_code == 200
    assert api.mission("training_prep").json()["status"] == "active"


def test_completing_a_mission_produces_a_report(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    for action, payload in (
        ("review_brief", {}),
        ("collect_oxygen", {}),
        ("take_repair_tools", {}),
        ("take_scanner", {}),
        ("inspect_rover", {"check_mode": "full"}),
        ("suit_check", {}),
    ):
        assert api.act("training_prep", action, payload).status_code == 200
    final = api.act("training_prep", "confirm_readiness").json()
    assert final["completed"] is True
    report = final["report"]
    assert report["mission_id"] == "training_prep"
    assert report["score_pct"] > 0
    assert report["grade"] in {"S", "A", "B", "C", "D"}
    assert report["passed"] is True
    assert report["unlocked_missions"]
    assert report["readiness"] >= 50

    fetched = api.get("/missions/training_prep/report").json()
    assert fetched["mission_id"] == "training_prep"
    assert api.get("/reports/training_prep").json()["mission_id"] == "training_prep"


def test_report_before_completion_is_404(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    response = api.get("/missions/training_prep/report")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_not_found"


def test_completed_mission_rejects_further_actions(client: TestClient, api: Api) -> None:
    complete_training(api)
    response = api.act("training_prep", "review_brief")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mission_completed"


def test_starting_a_completed_mission_is_a_conflict(client: TestClient, api: Api) -> None:
    complete_training(api)
    assert api.start("training_prep").status_code == 409


def test_campaign_report_aggregates(client: TestClient, api: Api) -> None:
    complete_training(api)
    complete_systems_check(api)
    body = api.get("/reports").json()
    assert body["missions_completed"] == 2
    assert body["missions_total"] == 15
    assert body["average_score_pct"] > 0
    assert len(body["reports"]) == 2
    assert body["stats"]["missions_completed"] == 2


def test_interaction_map_gives_positions_and_prompts(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    body = api.get("/missions/training_prep/interactions").json()
    assert body["mission_id"] == "training_prep"
    assert body["objects"]
    for obj in body["objects"]:
        assert len(obj["position"]) == 3
        assert obj["label"]["en"]
        assert obj["label"]["ar"] == obj["label"]["en"]
        for interaction in obj["interactions"]:
            assert "available" in interaction
            assert "animation" in interaction
            if not interaction["available"]:
                assert interaction["unmet"]


def test_unmet_conditions_are_english_and_explainable(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    view = api.mission("training_prep").json()
    readiness = next(o for o in view["objectives"] if o["id"] == "confirm_readiness")
    assert readiness["available"] is False
    for unmet in readiness["unmet"]:
        assert unmet["type"]
        assert unmet["message"]


def test_payload_choices_are_validated(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    api.act("training_prep", "review_brief")
    bad = api.act("training_prep", "inspect_rover", {"check_mode": "instant"})
    assert bad.status_code == 422
    good = api.act("training_prep", "inspect_rover", {"check_mode": "full"})
    assert good.status_code == 200


def test_mission_chain_unlocks_stage_by_stage(client: TestClient, api: Api) -> None:
    """Training → Analog → Mars: no stage skipping."""
    assert api.start("mars_arrival").status_code == 409
    complete_training(api)
    assert api.start("mars_arrival").status_code == 409
    complete_systems_check(api)
    catalogue = {m["id"]: m for m in api.get("/missions").json()}
    assert catalogue["analog_water_leak"]["status"] == "available"
    assert catalogue["mars_arrival"]["status"] == "locked"


def test_events_record_success_and_completion(client: TestClient, api: Api) -> None:
    complete_training(api)
    types = [event["type"] for event in api.get("/events", params={"limit": 200}).json()["events"]]
    assert "mission.started" in types
    assert "action.succeeded" in types
    assert "mission.completed" in types


def test_mission_view_exposes_simulation_state_for_sim_missions(
    client: TestClient, api: Api
) -> None:
    view = api.mission("lunar_landing").json()
    assert view["simulation"] == "lunar_lander"
    assert view["simulation_state"] is None
