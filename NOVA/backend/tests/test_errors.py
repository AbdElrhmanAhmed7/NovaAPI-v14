"""The error envelope, guards and HTTP edge cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import Api, make_settings


def test_error_envelope_shape(client: TestClient) -> None:
    response = client.get("/api/v1/games/nova_nope")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error", "request_id"}
    assert body["error"]["code"] == "session_not_found"
    assert body["error"]["message"]
    assert body["request_id"]


def test_validation_error_envelope(client: TestClient) -> None:
    response = client.post("/api/v1/games", json={"player_name": ""})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["errors"]


def test_unknown_route_uses_the_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


def test_method_not_allowed_uses_the_envelope(client: TestClient) -> None:
    response = client.put("/api/v1/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "http_405"


def test_rate_limit_returns_429_with_retry_after(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, rate_limit_enabled=True, rate_limit_requests=3)
    with TestClient(create_app(settings)) as client:
        codes = [client.get("/api/v1/version").status_code for _ in range(6)]
        assert codes[:3] == [200, 200, 200]
        assert 429 in codes
        blocked = next(
            r for r in (client.get("/api/v1/version") for _ in range(2)) if r.status_code == 429
        )
        assert blocked.json()["error"]["code"] == "rate_limited"
        assert int(blocked.headers["Retry-After"]) >= 1
        assert blocked.headers["X-RateLimit-Remaining"] == "0"


def test_rate_limit_headers_on_success(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, rate_limit_enabled=True, rate_limit_requests=50)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/version")
        assert response.headers["X-RateLimit-Limit"] == "50"
        assert int(response.headers["X-RateLimit-Remaining"]) > 0


def test_oversized_body_is_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_request_bytes=512)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/games", json={"player_name": "x" * 2000})
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"


def test_idempotency_key_replays_the_first_result(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    assert api.act("training_prep", "review_brief").status_code == 200
    headers = {"Idempotency-Key": "collect-once"}
    first = api.act("training_prep", "collect_oxygen", headers=headers)
    assert first.status_code == 200
    oxygen_after_first = first.json()["resources"]["oxygen"]

    replay = api.act("training_prep", "collect_oxygen", headers=headers)
    assert replay.status_code == 200
    assert replay.json()["resources"]["oxygen"] == oxygen_after_first
    assert replay.json()["action"] == "collect_oxygen"

    # Without the key the action is rejected as already completed.
    again = api.act("training_prep", "collect_oxygen")
    assert again.status_code == 409


def test_idempotency_keys_are_scoped_per_game(client: TestClient, api: Api) -> None:
    other = client.post("/api/v1/games", json={"player_name": "Other"}).json()
    api.start("training_prep")
    api.act("training_prep", "review_brief")
    api.act("training_prep", "collect_oxygen", headers={"Idempotency-Key": "shared-key"})
    fresh = Api(client, other["game_id"])
    fresh.start("training_prep")
    fresh.act("training_prep", "review_brief")
    response = fresh.act(
        "training_prep", "collect_oxygen", headers={"Idempotency-Key": "shared-key"}
    )
    assert response.status_code == 200


def test_long_idempotency_key_is_rejected(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    api.act("training_prep", "review_brief")
    response = api.act("training_prep", "collect_oxygen", headers={"Idempotency-Key": "k" * 65})
    assert response.status_code == 422


def test_game_id_path_is_length_validated(client: TestClient) -> None:
    assert client.get("/api/v1/games/abc").status_code == 422
    assert client.get("/api/v1/games/" + "x" * 65).status_code == 422


def test_unhandled_errors_do_not_leak_stack_traces(tmp_path: Path, monkeypatch: Any) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:

        async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("secret internal detail")

        monkeypatch.setattr(app.state.games, "snapshot", boom)
        game = client.post("/api/v1/games", json={"player_name": "Boom"}).json()
        response = client.get(f"/api/v1/games/{game['game_id']}")
        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "internal_error"
        assert "secret internal detail" not in str(body)
