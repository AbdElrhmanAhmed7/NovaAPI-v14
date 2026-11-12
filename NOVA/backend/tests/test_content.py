"""Static content endpoints – the contract the React client boots from."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.missions.registry import GameContent

ROOT = Path(__file__).resolve().parent.parent


def test_catalogue_has_items_resources_and_controls(client: TestClient) -> None:
    body = client.get("/api/v1/content/catalogue").json()
    assert body["items"] and body["resources"]
    assert body["languages"] == ["en"]
    assert "training_facility" in body["environments"]
    assert "mars_surface" in body["environments"]
    keys = {binding["action"] for binding in body["controls"]}
    assert {
        "move",
        "look",
        "sprint",
        "jump",
        "interact",
        "use_tool",
        "toggle_camera",
        "inventory",
    } <= keys


def test_control_bindings_match_the_agreed_scheme(client: TestClient) -> None:
    controls = client.get("/api/v1/content/controls").json()["controls"]
    by_action = {c["action"]: c["keys"] for c in controls}
    assert by_action["move"] == ["W", "A", "S", "D"]
    assert by_action["sprint"] == ["Shift"]
    assert by_action["jump"] == ["Space"]
    assert by_action["interact"] == ["E"]
    assert by_action["use_tool"] == ["F"]
    assert by_action["toggle_camera"] == ["V"]
    assert by_action["inventory"] == ["Tab"]


def test_item_catalogue_is_english_and_typed(client: TestClient) -> None:
    items = client.get("/api/v1/content/items").json()
    scanner = next(item for item in items if item["id"] == "scanner")
    assert scanner["name"]["en"]
    assert scanner["name"]["ar"] == scanner["name"]["en"]
    assert scanner["category"] in {
        "tool",
        "consumable",
        "protection",
        "component",
        "sample",
        "data",
    }
    assert scanner["durable"] is True


def test_resource_definitions_expose_units_and_caps(client: TestClient) -> None:
    resources = {r["id"]: r for r in client.get("/api/v1/content/resources").json()}
    assert {"oxygen", "water", "power", "fuel"} <= set(resources)
    for definition in resources.values():
        assert definition["unit"]
        assert definition["cap"] > 0
        assert definition["start"] >= 0
        assert definition["critical_below"] >= 0


def test_mission_list_covers_eight_stages(client: TestClient) -> None:
    missions = client.get("/api/v1/content/missions").json()
    assert len(missions) == 15
    assert {m["stage"] for m in missions} == set(range(1, 9))
    for mission in missions:
        assert mission["title"]["en"]
        assert mission["title"]["ar"] == mission["title"]["en"]
        assert mission["objectives"], mission["id"]


def test_mission_filter_by_stage_and_environment(client: TestClient) -> None:
    stage2 = client.get("/api/v1/content/missions", params={"stage": 2}).json()
    assert len(stage2) == 7
    assert all(m["environment"] == "earth_analog" for m in stage2)

    mars = client.get("/api/v1/content/missions", params={"environment": "mars_surface"}).json()
    assert {m["id"] for m in mars} == {"mars_arrival", "mars_rover_traverse"}

    assert client.get("/api/v1/content/missions", params={"stage": 9}).status_code == 422


def test_blueprint_exposes_objectives_and_world_objects(client: TestClient) -> None:
    blueprint = client.get("/api/v1/content/missions/analog_water_leak").json()
    assert blueprint["id"] == "analog_water_leak"
    objectives = [o["id"] for o in blueprint["objectives"]]
    assert objectives == ["scan_leak", "close_valve", "repair_pipe", "verify_pressure"]
    assert blueprint["world_objects"], "the 3D client needs interactable positions"
    for obj in blueprint["world_objects"]:
        assert "position" in obj and "id" in obj
    assert blueprint["hazards"], "repairing a live pipe must be authored as a hazard"


def test_unknown_blueprint_returns_404_with_available_ids(client: TestClient) -> None:
    response = client.get("/api/v1/content/missions/does_not_exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "mission_not_found"
    assert "analog_water_leak" in error["details"]["available"]


def test_validation_report_is_clean(client: TestClient) -> None:
    body = client.get("/api/v1/content/validation").json()
    assert body["ok"] is True
    assert body["errors"] == 0
    assert body["warnings"] == 0
    assert body["issues"] == []
    assert body["missions_checked"] == 15


def test_validation_matches_the_library(content: GameContent) -> None:
    from app.missions.validation import validate_content

    report = validate_content(content)
    assert report.ok
    assert report.actions_checked > 90


def test_every_mission_declares_a_disclaimer_or_science_notes(content: GameContent) -> None:
    """Scientific honesty: simulations must be labelled as simplifications."""
    for definition in content.missions.ordered():
        assert definition.disclaimer or definition.science_notes, definition.id


def test_simulation_catalogue_lists_control_contracts(client: TestClient) -> None:
    sims = client.get("/api/v1/simulations").json()
    kinds = {s["kind"] for s in sims}
    assert {"lunar_lander", "microgravity_fire", "crew_vitals", "rover_traverse"} <= kinds
    lander = next(s for s in sims if s["kind"] == "lunar_lander")
    assert any("throttle" in c for c in lander["controls"])


def test_science_reference_uses_the_canonical_habitability_scale(content: GameContent) -> None:
    """The science panel maps four fixed levels; content must not invent more."""
    from app.domain.enums import Habitability

    dataset = content.read_json("science/mars_minerals.json")
    allowed = set(Habitability)
    for mineral in dataset["minerals"]:
        assert mineral["habitability"] in allowed, mineral["id"]


def test_validator_catches_a_drifted_habitability_vocabulary(tmp_path: Path) -> None:
    """Regression guard: 'very_high' once shipped and broke the science UI."""
    import json
    import shutil

    from app.missions.registry import GameContent
    from app.missions.validation import validate_content

    pack = tmp_path / "content"
    shutil.copytree(ROOT / "content", pack)
    science = pack / "science" / "mars_minerals.json"
    dataset = json.loads(science.read_text("utf-8"))
    dataset["minerals"][0]["habitability"] = "very_high"
    science.write_text(json.dumps(dataset, ensure_ascii=False), "utf-8")

    report = validate_content(GameContent.load(pack))
    codes = {issue.code for issue in report.errors}
    assert "science_habitability_vocabulary" in codes
