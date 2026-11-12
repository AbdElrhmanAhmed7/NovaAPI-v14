"""Meta endpoints: liveness, readiness, version and the OpenAPI contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app
from conftest import make_settings


def test_health_is_cheap_and_public(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "test"
    assert body["uptime_seconds"] >= 0


def test_unversioned_health_alias(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_root_serves_the_mission_control_console(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    # The dev console is a static, dependency-free client (see tests/test_console.py).
    assert "NOVA" in response.text
    assert "Mission Control" in response.text


def test_root_json_card_when_the_console_is_absent(tmp_path: Path) -> None:
    """Without the static bundle the root still advertises the API as JSON."""
    import app.main as main_module

    original = main_module.STATIC_DIR
    main_module.STATIC_DIR = tmp_path / "missing-static"
    try:
        with TestClient(create_app(make_settings(tmp_path))) as bare:
            body = bare.get("/").json()
        assert body["docs"] == "/docs"
        assert body["api"] == "/api/v1"
        assert body["health"] == "/api/v1/health"
    finally:
        main_module.STATIC_DIR = original


def test_ready_checks_database_and_content(client: TestClient) -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] is True
    assert body["content"] is True
    assert body["missions_loaded"] == 15
    assert body["items_loaded"] > 0
    assert "nasa_cache" in body
    # No socket is open in this test: the fan-out counters must read zero, not
    # "missing" – the console renders them before any stream is connected.
    assert body["stream"] == {
        "games_watched": 0,
        "subscribers": 0,
        "published_events": 0,
        "published_batches": 0,
    }
    assert body["db_calls_in_flight"] == 0


def test_version_reports_schema_and_simulators(client: TestClient) -> None:
    body = client.get("/api/v1/version").json()
    assert body["api_version"] == "v1"
    assert body["state_schema_version"] == 1
    assert body["missions"] == 15
    assert body["stages"] == 8
    assert set(body["simulations"]) == {
        "lunar_lander",
        "microgravity_fire",
        "crew_vitals",
        "rover_traverse",
        "earth_trends",
    }


def test_openapi_documents_every_tag(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"].startswith("NOVA")
    paths = schema["paths"]
    # The contract the 3D client depends on.
    for required in (
        "/api/v1/games",
        "/api/v1/games/{game_id}",
        "/api/v1/games/{game_id}/tick",
        "/api/v1/games/{game_id}/missions/{mission_id}/actions",
        "/api/v1/games/{game_id}/missions/{mission_id}/simulation/tick",
        "/api/v1/games/{game_id}/inventory/use",
        "/api/v1/games/{game_id}/earth/reports",
        "/api/v1/games/{game_id}/science/samples/{sample_id}/analyse",
        "/api/v1/content/missions",
    ):
        assert required in paths, required
    tags = {tag["name"] for tag in schema["tags"]}
    assert {"Games", "Missions", "Simulations", "Loadout", "Earth Trends", "Science"} <= tags


def test_request_id_and_timing_headers(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "req-from-test"})
    assert response.headers["X-Request-ID"] == "req-from-test"
    assert float(response.headers["X-Process-Time-ms"]) >= 0
    assert response.headers["X-NOVA-Backend"] == "nova-gameplay"


def test_request_id_is_generated_when_absent(client: TestClient) -> None:
    response = client.get("/api/v1/version")
    assert response.headers["X-Request-ID"].startswith("req_")


def test_cors_allows_the_vite_dev_server(client: TestClient) -> None:
    response = client.options(
        "/api/v1/games",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-session-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_gzip_compresses_large_payloads(client: TestClient) -> None:
    response = client.get("/api/v1/content/missions", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert len(response.content) > 1024


def test_docs_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_health_is_exempt_from_rate_limiting(tmp_path: Any) -> None:
    from app.main import create_app
    from conftest import make_settings

    settings = make_settings(tmp_path, rate_limit_enabled=True, rate_limit_requests=2)
    with TestClient(create_app(settings)) as limited:
        for _ in range(6):
            assert limited.get("/api/v1/health").status_code == 200
