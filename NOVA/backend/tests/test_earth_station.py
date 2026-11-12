"""Earth Trends – the Earth Observation Station (stage 8).

Covers the whole loop the player walks through: pick a region, enable a layer,
compare two periods, read the change, archive the report. Live NASA POWER data
is exercised with a mocked transport so the suite stays offline.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.integrations.nasa_client import NasaClient
from app.main import create_app
from conftest import Api, make_settings

COMPARE = {
    "region_id": "nile_delta",
    "layer_id": "ndvi",
    "period_before": "2001-2005",
    "period_after": "2021-2025",
}


def power_payload(
    parameter: str, start: int, end: int, value: float, *, missing: tuple[int, ...] = ()
) -> dict[str, Any]:
    """A NASA POWER monthly point response (the real shape, trimmed).

    ``missing`` months carry POWER's -999 fill value, exactly as the API sends it.
    """
    series = {
        f"{year}{month:02d}": (-999.0 if month in missing else value + (year - start) * 0.1)
        for year in range(start, end + 1)
        for month in range(1, 13)
    }
    return {"properties": {"parameter": {parameter: series}}}


def test_catalogue_lists_regions_layers_and_periods(client: TestClient) -> None:
    body = client.get("/api/v1/earth/catalogue").json()
    assert {"nile_delta", "amazon_basin"} <= {r["id"] for r in body["regions"]}
    assert {"ndvi", "lst", "precipitation"} <= {layer["id"] for layer in body["layers"]}
    assert body["periods"] == sorted(body["periods"])
    assert len(body["default_comparison"]) == 2
    # Only layers backed by a POWER parameter may claim live capability.
    assert set(body["live_capable_layers"]) == {"lst", "precipitation"}
    for layer in body["layers"]:
        assert layer["nasa_product"] and layer["nasa_url"].startswith("https://")
        assert layer["name"]["en"]
        assert layer["name"]["ar"] == layer["name"]["en"]
    assert body["note"]["en"]
    assert body["note"]["ar"] == body["note"]["en"]


def test_compare_returns_grids_statistics_and_a_narrative(client: TestClient) -> None:
    response = client.post("/api/v1/earth/compare", json={**COMPARE, "grid_size": 6})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["region"] == "nile_delta"
    assert body["layer"] == "ndvi"
    assert body["unit"] == "index"
    assert len(body["grid_before"]) == len(body["grid_after"]) == 36
    assert body["direction"] in {"increase", "decrease", "stable"}
    assert body["verdict"] in {"improving", "degrading", "stable", "mixed"}
    assert body["cells_changed"] <= 36
    assert body["narrative"]["en"]
    assert body["narrative"]["ar"] == body["narrative"]["en"]
    assert body["statistics"]["before"]["mean"] != body["statistics"]["after"]["mean"]
    for cell in body["grid_before"]:
        assert {"row", "col", "lat", "lon", "value"} <= set(cell)
    for spot in body["hotspots"]:
        assert spot["delta"] == pytest.approx(spot["after"] - spot["before"], abs=1e-6)


def test_compare_is_a_preview_and_saves_nothing(client: TestClient, api: Api) -> None:
    assert client.post("/api/v1/earth/compare", json=COMPARE).status_code == 200
    assert api.get("/earth/reports").json() == []


def test_comparison_is_deterministic_for_the_same_inputs(client: TestClient) -> None:
    first = client.post("/api/v1/earth/compare", json=COMPARE).json()
    second = client.post("/api/v1/earth/compare", json=COMPARE).json()
    assert first["mean_delta"] == second["mean_delta"]
    assert first["grid_after"] == second["grid_after"]


@pytest.mark.parametrize(
    "payload",
    [
        {**COMPARE, "period_after": COMPARE["period_before"]},
        {**COMPARE, "region_id": "atlantis"},
        {**COMPARE, "layer_id": "vibes"},
        {**COMPARE, "period_before": "1999-2000"},
        {**COMPARE, "period_before": "not-a-period"},
        {**COMPARE, "grid_size": 2},
        {**COMPARE, "grid_size": 64},
        {**COMPARE, "surprise": True},
    ],
)
def test_bad_comparisons_are_rejected(client: TestClient, payload: dict[str, Any]) -> None:
    response = client.post("/api/v1/earth/compare", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in {"invalid_input", "validation_error"}


def test_unknown_region_lists_the_available_ones(client: TestClient) -> None:
    body = client.post("/api/v1/earth/compare", json={**COMPARE, "region_id": "atlantis"}).json()
    assert "nile_delta" in body["error"]["details"]["available"]


def test_requesting_live_data_offline_is_labelled_not_hidden(client: TestClient) -> None:
    body = client.post("/api/v1/earth/compare", json={**COMPARE, "live": True}).json()
    assert body["requested_live"] is True
    assert body["live_supported"] is False  # NDVI has no POWER parameter
    assert body["provenance"] == "simulated"
    assert body["live_fallback_reason"]


def test_live_capable_layer_falls_back_with_a_reason(client: TestClient) -> None:
    body = client.post(
        "/api/v1/earth/compare", json={**COMPARE, "layer_id": "lst", "live": True}
    ).json()
    assert body["live_supported"] is True
    assert body["provenance"] == "simulated"
    assert "POWER" in body["live_fallback_reason"]


def test_live_power_data_is_used_and_labelled(tmp_path: Any) -> None:
    """With NASA POWER reachable, the grid must come from the real product."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=power_payload("T2M", 2001, 2025, 21.5))

    settings = make_settings(tmp_path, nasa_enabled=True, nasa_max_retries=0)
    nasa = NasaClient(settings, fallback={}, transport=httpx.MockTransport(handler))
    with TestClient(create_app(settings, nasa=nasa)) as client:
        body = client.post(
            "/api/v1/earth/compare",
            json={**COMPARE, "layer_id": "lst", "live": True, "grid_size": 4},
        ).json()

    assert len(calls) == 2, "one POWER call per period"
    assert "power.larc.nasa.gov" in str(calls[0].url)
    assert body["provenance"] in {"live", "cache", "mixed"}
    assert body["live_fallback_reason"] is None
    assert "NASA POWER" in body["sources"][0]
    assert body["statistics"]["after"]["mean"] > body["statistics"]["before"]["mean"]


def test_power_fill_values_are_not_treated_as_measurements(tmp_path: Any) -> None:
    """-999 is POWER's missing-data marker; it must never cool a heatmap."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = power_payload("T2M", 2001, 2025, 0.0, missing=tuple(range(1, 13)))
        return httpx.Response(200, json=payload)

    settings = make_settings(tmp_path, nasa_enabled=True, nasa_max_retries=0)
    nasa = NasaClient(settings, fallback={}, transport=httpx.MockTransport(handler))
    with TestClient(create_app(settings, nasa=nasa)) as client:
        body = client.post(
            "/api/v1/earth/compare", json={**COMPARE, "layer_id": "lst", "live": True}
        ).json()

    assert body["provenance"] == "simulated"
    assert body["live_fallback_reason"]
    assert body["statistics"]["before"]["mean"] > -100


def test_missing_months_are_interpolated_not_averaged_in(tmp_path: Any) -> None:
    """Half a year of fill values must not drag the climatology down."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=power_payload("T2M", 2001, 2025, 20.0, missing=(1, 2, 3, 4, 5, 6))
        )

    settings = make_settings(tmp_path, nasa_enabled=True, nasa_max_retries=0)
    nasa = NasaClient(settings, fallback={}, transport=httpx.MockTransport(handler))
    with TestClient(create_app(settings, nasa=nasa)) as client:
        body = client.post(
            "/api/v1/earth/compare", json={**COMPARE, "layer_id": "lst", "live": True}
        ).json()

    assert body["provenance"] in {"live", "cache", "mixed"}
    assert body["statistics"]["before"]["mean"] >= 20.0


# ---------------------------------------------------------------------------
# Archiving the mission deliverable
# ---------------------------------------------------------------------------
def test_report_is_archived_listed_and_reopened(client: TestClient, api: Api) -> None:
    response = api.post("/earth/reports", {**COMPARE, "title": "Nile delta greenness"})
    assert response.status_code == 200, response.text
    report = response.json()
    report_id = report["report_id"]
    assert report["game_id"] == api.game_id
    assert report["region_id"] == "nile_delta"
    assert report["verdict"] == report["analysis"]["verdict"]
    assert report["analysis"]["title"] == "Nile delta greenness"

    listed = api.get("/earth/reports").json()
    assert [r["report_id"] for r in listed] == [report_id]
    # Regression guard: created_at is a string in the contract, not a datetime.
    assert isinstance(listed[0]["created_at"], str)
    assert listed[0]["created_at"].endswith("Z")

    fetched = api.get(f"/earth/reports/{report_id}").json()
    assert fetched["report_id"] == report_id
    assert isinstance(fetched["created_at"], str)
    assert fetched["analysis"]["grid_after"], "a stored report keeps its grids"

    # The save document and the event log both reference the deliverable.
    state = api.get("").json()
    assert report_id in state["report_ids"]
    events = api.get("/events", params={"types": "earth.report_saved"}).json()["events"]
    assert events[-1]["payload"]["report_id"] == report_id


def test_reports_are_scoped_to_their_campaign(client: TestClient, api: Api) -> None:
    report_id = api.post("/earth/reports", COMPARE).json()["report_id"]
    other = client.post(
        "/api/v1/games", json={"player_name": "Other Crew", "difficulty": "cadet", "seed": 3}
    ).json()
    other_api = Api(client, other["game_id"], other["session_token"])

    assert other_api.get("/earth/reports").json() == []
    assert other_api.get(f"/earth/reports/{report_id}").status_code == 404
    assert api.get("/earth/reports/earth_missing").status_code == 404


def test_archiving_validates_the_session_before_analysing(client: TestClient) -> None:
    response = client.post("/api/v1/games/nova_nope/earth/reports", json=COMPARE)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


def test_archiving_rejects_an_unknown_region(client: TestClient, api: Api) -> None:
    response = api.post("/earth/reports", {**COMPARE, "region_id": "atlantis"})
    assert response.status_code == 422
    assert api.get("/earth/reports").json() == []


def test_archiving_requires_the_session_token(auth_client: TestClient) -> None:
    created = auth_client.post(
        "/api/v1/games", json={"player_name": "Guarded", "difficulty": "standard", "seed": 5}
    ).json()
    url = f"/api/v1/games/{created['game_id']}/earth/reports"

    assert auth_client.post(url, json=COMPARE).status_code == 401
    authorised = auth_client.post(
        url, json=COMPARE, headers={"X-Session-Token": created["session_token"]}
    )
    assert authorised.status_code == 200, authorised.text


def test_earth_trends_mission_accepts_the_archived_report(campaign: Any) -> None:
    """The stage-8 mission is completed by the report, not by a quiz answer."""
    api: Api = campaign.api
    report = api.get("/reports/earth_trends").json()
    assert report["status"] == "completed"
    assert report["score_pct"] > 0

    progress = api.mission("earth_trends").json()
    assert "save_report" in progress["completed_objectives"]
    science = api.get("/earth/reports").json()
    assert science and science[0]["provenance"] in {"simulated", "bundled", "live", "cache"}
    assert json.dumps(science)  # fully JSON-serialisable
