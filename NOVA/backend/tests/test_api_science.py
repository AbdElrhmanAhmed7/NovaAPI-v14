"""Martian Guide HTTP contract: reference data, samples, spectrometer, database.

The science pipeline is only reachable after the mission chain unlocks, so most
tests run against the shared ``campaign`` fixture (a real playthrough).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.services.science_service import OXIDES
from conftest import Api


# ---------------------------------------------------------------------------
# Reference content (public, no session)
# ---------------------------------------------------------------------------
def test_reference_is_english_and_sourced(client: TestClient) -> None:
    body = client.get("/api/v1/science/reference").json()
    assert body["minerals"] and body["sites"]
    for mineral in body["minerals"]:
        assert mineral["name"]["en"]
        assert mineral["name"]["ar"] == mineral["name"]["en"]
        assert mineral["signature"]
        assert mineral["interpretation"]["en"]
    for site in body["sites"]:
        assert len(site["coordinates"]) == 2
        assert site["weights"]
    assert "simulat" in body["disclaimer"].lower()


def test_reference_oxides_match_the_spectrometer(client: TestClient) -> None:
    minerals = client.get("/api/v1/science/reference").json()["minerals"]
    for mineral in minerals:
        assert set(mineral["signature"]) <= set(OXIDES)


# ---------------------------------------------------------------------------
# Empty and unknown
# ---------------------------------------------------------------------------
def test_a_new_campaign_has_an_empty_science_database(api: Api) -> None:
    body = api.get("/science").json()
    assert body["samples_total"] == 0
    assert body["samples_analysed"] == 0
    assert body["samples"] == []
    assert body["mineral_reference"], "the reference is always available"
    assert api.get("/science/samples").json() == []


def test_unknown_sample_is_404(api: Api) -> None:
    response = api.get("/science/samples/smp_does_not_exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "sample_not_found"
    assert api.post("/science/samples/smp_does_not_exist/analyse").status_code == 404
    assert api.post("/science/samples/smp_does_not_exist/log").status_code == 404


# ---------------------------------------------------------------------------
# After a real Martian Guide run
# ---------------------------------------------------------------------------
def test_the_database_aggregates_the_campaign(campaign: SimpleNamespace) -> None:
    body = campaign.api.get("/science").json()
    assert body["samples_total"] >= 1
    assert body["samples_analysed"] >= 1
    assert sum(body["by_rock_type"].values()) == body["samples_analysed"]
    assert body["disclaimer"]
    for sample in body["samples"]:
        assert sample["game_id"] == campaign.game_id
        assert sample["mission_id"] == "martian_guide"
        assert set(sample["composition"]) == set(OXIDES)
        assert 0 < sum(sample["composition"].values()) <= 100.5


def test_sample_list_and_detail_agree(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    samples = api.get("/science/samples").json()
    assert isinstance(samples, list) and samples
    sample_id = samples[0]["sample_id"]
    detail = api.get(f"/science/samples/{sample_id}").json()
    assert detail["sample_id"] == sample_id
    assert detail["composition"] == samples[0]["composition"]


def test_analysis_is_labelled_simulated_and_explainable(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    sample_id = api.get("/science/samples").json()[0]["sample_id"]
    analysis = api.get(f"/science/samples/{sample_id}").json()["analysis"]
    assert analysis["provenance"] == "simulated"
    assert 0 <= analysis["confidence_pct"] <= 100
    assert analysis["best_match"]["mineral_id"]
    assert analysis["interpretation"]["en"]
    assert analysis["interpretation"]["ar"] == analysis["interpretation"]["en"]
    assert analysis["habitability"] in {"low", "moderate", "high", "none"}


def test_rerunning_the_spectrometer_is_deterministic(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    sample_id = api.get("/science/samples").json()[0]["sample_id"]
    first = api.post(f"/science/samples/{sample_id}/analyse").json()
    second = api.post(f"/science/samples/{sample_id}/analyse").json()
    assert first["rock_type"] == second["rock_type"]
    assert first["confidence_pct"] == second["confidence_pct"]


def test_findings_are_recorded_with_a_note(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    sample_id = api.get("/science/samples").json()[0]["sample_id"]
    entry = api.post(
        f"/science/samples/{sample_id}/log", {"note": "delta front, fine grained"}
    ).json()
    assert entry["logged"] is True
    assert entry["sample_id"] == sample_id
    assert entry["note"] == "delta front, fine grained"
    assert entry["rock_type"]


def test_a_note_is_length_validated(campaign: SimpleNamespace) -> None:
    api: Api = campaign.api
    sample_id = api.get("/science/samples").json()[0]["sample_id"]
    response = api.post(f"/science/samples/{sample_id}/log", {"note": "x" * 501})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert api.post(f"/science/samples/{sample_id}/log", {"unknown": 1}).status_code == 422


def test_samples_are_scoped_to_their_campaign(campaign: SimpleNamespace, api: Api) -> None:
    sample_id = campaign.api.get("/science/samples").json()[0]["sample_id"]
    assert api.get(f"/science/samples/{sample_id}").status_code == 404
    assert api.get("/science").json()["samples_total"] == 0


def test_the_mission_records_the_last_sample_in_the_world_state(
    campaign: SimpleNamespace,
) -> None:
    snapshot = campaign.api.get("").json()
    geology = snapshot["world"]["geology"]
    assert geology["last_sample_id"].startswith("smp_")
    assert geology["last_rock_type"]
    assert geology["logged_sample_id"] == geology["last_sample_id"]


def test_science_writes_require_a_token_when_auth_is_on(auth_client: TestClient) -> None:
    created = auth_client.post("/api/v1/games", json={"player_name": "Auth"}).json()
    url = f"/api/v1/games/{created['game_id']}/science/samples/smp_x/analyse"
    assert auth_client.post(url, json={}).status_code == 401


def test_the_science_pipeline_emits_events(campaign: SimpleNamespace) -> None:
    events: list[dict[str, Any]] = campaign.api.get("/events?limit=200").json()["events"]
    kinds = {event["type"] for event in events}
    assert {
        "science.sample_collected",
        "science.sample_analysed",
        "science.finding_logged",
    } <= kinds
