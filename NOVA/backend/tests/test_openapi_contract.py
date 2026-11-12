"""The published contract: route discovery, security and typed responses.

Regression suite for the FastAPI >= 0.160 layout, where ``include_router``
keeps a nested ``_IncludedRouter`` instead of flattening routes into
``app.routes``. A hand-rolled walk saw 9 of 75 routes, so ``core.openapi``
found zero guarded operations and shipped a document with no ``security``
block and no ``401`` responses – while the handlers *were* protected.

These tests fail if that blind spot ever comes back, and they gate the two
mistakes the route audit exists for: an unguarded game write and an untyped
response model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.routing import (
    HTTP_METHODS,
    dependency_calls,
    http_routes,
    iter_app_routes,
    iter_routes,
    websocket_routes,
)
from app.dependencies import require_game_write
from app.main import create_app
from app.schemas.common import MessageResponse
from conftest import make_settings

ROOT = Path(__file__).resolve().parent.parent
VERBS = HTTP_METHODS - {"head", "options", "trace"}


def _operations(schema: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): operation
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
        if method in VERBS
    }


@pytest.fixture(scope="module")
def contract(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """The app and its schema, built once (no lifespan, no database)."""
    settings = make_settings(tmp_path_factory.mktemp("nova-contract"))
    app = create_app(settings)
    return {"app": app, "schema": app.openapi()}


# ---------------------------------------------------------------------------
# Route discovery
# ---------------------------------------------------------------------------
def test_walker_sees_every_published_operation(contract: dict[str, Any]) -> None:
    """The blind spot: OpenAPI knows 67 operations, a naive walk knew 9."""
    app, schema = contract["app"], contract["schema"]
    walked = {
        (route.path, method)
        for route in http_routes(app, schema_only=True)
        for method in route.methods & VERBS
    }
    documented = set(_operations(schema))
    assert len(documented) >= 60, "the contract shrank unexpectedly"
    assert walked == documented


def test_walker_resolves_prefixes_and_finds_the_websocket(contract: dict[str, Any]) -> None:
    app = contract["app"]
    routes = list(iter_app_routes(app))
    assert all(route.path.startswith("/") for route in routes)
    assert "/api/v1/games/{game_id}/missions/{mission_id}/actions" in {r.path for r in routes}
    # Nested twice: app -> api_router -> games.router.
    assert [socket.path for socket in websocket_routes(app)] == ["/api/v1/games/{game_id}/stream"]
    # The static mount is one entry, not a walk into the filesystem.
    assert [route.path for route in routes if route.kind == "mount"] == ["/static"]


def test_walker_handles_nested_includes_and_inherited_dependencies() -> None:
    """Synthetic app: three levels of include_router, guard on the middle one."""
    seen: list[str] = []

    async def guard() -> None:
        seen.append("guard")

    leaf = APIRouter()

    @leaf.get("/ping", response_model=MessageResponse)
    async def ping() -> MessageResponse:
        return MessageResponse(ok=True, message="pong")

    @leaf.websocket("/ws")
    async def ws() -> None:  # pragma: no cover - never connected here
        return None

    middle = APIRouter(prefix="/inner", dependencies=[Depends(guard)])
    middle.include_router(leaf, prefix="/leaf")
    outer = APIRouter(prefix="/outer")
    outer.include_router(middle)
    app = FastAPI()
    app.include_router(outer, prefix="/api")

    routes = {route.path: route for route in iter_routes(app.routes)}
    assert "/api/outer/inner/leaf/ping" in routes
    resolved = routes["/api/outer/inner/leaf/ping"]
    assert guard in resolved.dependency_calls(), "router-level dependency was lost"
    assert resolved.response_model is MessageResponse
    assert routes["/api/outer/inner/leaf/ws"].is_websocket

    with TestClient(app) as client:
        assert client.get("/api/outer/inner/leaf/ping").status_code == 200
    assert seen == ["guard"], "the resolved route is not the one being served"


def test_dependency_calls_walks_the_whole_tree() -> None:
    async def leaf() -> None:
        return None

    async def branch() -> None:
        return None

    async def trunk() -> None:
        return None

    class Node:
        def __init__(self, call: Any, children: list[Any]) -> None:
            self.call = call
            self.dependencies = children

    tree = Node(trunk, [Node(branch, [Node(leaf, [])])])
    assert dependency_calls(tree) == {trunk, branch, leaf}
    assert dependency_calls(None) == set()


# ---------------------------------------------------------------------------
# Security contract
# ---------------------------------------------------------------------------
def test_guarded_operations_declare_security_and_errors(contract: dict[str, Any]) -> None:
    app, schema = contract["app"], contract["schema"]
    operations = _operations(schema)
    guarded = {
        (route.path, method)
        for route in http_routes(app, schema_only=True)
        if require_game_write in route.dependency_calls()
        for method in route.methods & VERBS
    }
    assert len(guarded) >= 15, "guards disappeared from the handlers"
    for key in guarded:
        operation = operations[key]
        assert operation.get("security") == [{"SessionToken": []}, {"ApiKey": []}], key
        assert "401" in operation["responses"], key
        assert "409" in operation["responses"], key


def test_auth_headers_are_security_schemes_not_parameters(contract: dict[str, Any]) -> None:
    schema = contract["schema"]
    schemes = schema["components"]["securitySchemes"]
    assert schemes["SessionToken"]["name"] == "X-Session-Token"
    assert schemes["ApiKey"]["name"] == "X-API-Key"
    for key, operation in _operations(schema).items():
        for parameter in operation.get("parameters", []):
            assert parameter.get("name") not in {"X-Session-Token", "X-API-Key"}, key


def test_every_operation_documents_the_shared_error_envelope(contract: dict[str, Any]) -> None:
    schema = contract["schema"]
    assert "ErrorResponse" in schema["components"]["schemas"]
    for key, operation in _operations(schema).items():
        responses = operation["responses"]
        for status in ("422", "429", "500"):
            assert status in responses, (key, status)
        for status, entry in responses.items():
            if status[0] not in "45":
                continue
            ref = (
                entry.get("content", {})
                .get("application/json", {})
                .get("schema", {})
                .get("$ref", "")
            )
            # Prototype-01 URLs answer with `{"detail": ...}` and say so.
            allowed = {"ErrorResponse"}
            if not key[0].startswith("/api/v1"):
                allowed.add("LegacyConflictResponse")
            assert ref.rsplit("/", 1)[-1] in allowed, (key, status, ref)


def test_every_operation_has_a_typed_success_response(contract: dict[str, Any]) -> None:
    for key, operation in _operations(contract["schema"]).items():
        success = [status for status in operation["responses"] if status.startswith("2")]
        assert success, key
        for status in success:
            media = operation["responses"][status].get("content", {})
            if not media:  # 204-style responses carry no body by design
                continue
            schema_ref = media["application/json"]["schema"]
            assert schema_ref, key
            assert schema_ref.get("additionalProperties") is not True, (
                f"{key} returns an untyped object"
            )


def test_nasa_endpoints_document_the_upstream_failure(contract: dict[str, Any]) -> None:
    for (path, _method), operation in _operations(contract["schema"]).items():
        if path.startswith("/api/v1/nasa/") and not path.endswith("/status"):
            assert "502" in operation["responses"], path


# ---------------------------------------------------------------------------
# The audit gate itself
# ---------------------------------------------------------------------------
def test_route_audit_is_clean() -> None:
    from audit_routes import audit

    inventory, findings, summary = audit()
    errors = [finding for finding in findings if finding.severity == "error"]
    assert not errors, "\n".join(finding.render() for finding in errors)
    assert summary["operations"] == summary["http_published"]
    assert len(inventory) >= summary["operations"]


def test_audit_flags_an_unguarded_game_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove the gate bites: remove the allowlist and the legacy door is caught."""
    import audit_routes

    monkeypatch.setattr(
        audit_routes,
        "UNGUARDED_WRITES",
        {("post", "/api/v1/games"): "test"},
    )
    _inventory, findings, _summary = audit_routes.audit()
    codes = {(finding.code, finding.where) for finding in findings}
    assert ("unguarded_write", "POST /api/v1/games/{game_id}/resume") in codes


def test_exported_contract_matches_the_handlers() -> None:
    """`docs/openapi.json` is the frontend's source of truth – keep it fresh."""
    from export_openapi import DEFAULT_OUTPUT, build_schema, serialise

    committed = DEFAULT_OUTPUT.read_text(encoding="utf-8")
    assert committed == serialise(build_schema()), (
        "docs/openapi.json is stale – run `python scripts/export_openapi.py`"
    )


def test_readiness_stats_are_typed_not_free_form_objects(contract: dict[str, Any]) -> None:
    """The console and the HUD read these counters: they belong in the contract."""
    schemas = contract["schema"]["components"]["schemas"]
    readiness = schemas["ReadinessResponse"]["properties"]
    assert readiness["stream"]["$ref"].endswith("/StreamStats")
    assert readiness["nasa_cache"]["$ref"].endswith("/NasaCacheStats")
    assert set(schemas["StreamStats"]["properties"]) == {
        "games_watched",
        "subscribers",
        "published_events",
        "published_batches",
    }
    for name in ("StreamStats", "NasaCacheStats"):
        properties = schemas[name]["properties"]
        assert all(spec["type"] == "integer" for spec in properties.values()), name


# ---------------------------------------------------------------------------
# The bug the audit found
# ---------------------------------------------------------------------------
def test_legacy_write_is_guarded_when_auth_is_enabled(auth_client: TestClient) -> None:
    """The unversioned URL is a second door into the same save – lock it too."""
    created = auth_client.post("/games", json={"player_name": "Prototype"}).json()
    game_id = created["game_id"]
    # The legacy create response predates auth, so the token comes from /resume.
    token = auth_client.post(f"/api/v1/games/{game_id}/resume").json()["session_token"]
    url = f"/games/{game_id}/missions/water-leak/actions"

    anonymous = auth_client.post(url, json={"action": "scan_leak"})
    assert anonymous.status_code == 401, "legacy endpoint bypassed the session guard"

    allowed = auth_client.post(
        url, json={"action": "scan_leak"}, headers={"X-Session-Token": token}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["success"] is True


def test_reads_stay_public_while_writes_are_guarded(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, auth_enabled=True)
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v1/games", json={"player_name": "Reader"}).json()
        game_id = created["game_id"]
        assert client.get(f"/api/v1/games/{game_id}").status_code == 200
        assert (
            client.post(f"/api/v1/games/{game_id}/tick", json={"dt_seconds": 1}).status_code == 401
        )
