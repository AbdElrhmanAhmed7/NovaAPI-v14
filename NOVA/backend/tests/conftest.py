"""Shared pytest fixtures.

Every test gets its own SQLite file and its own application instance, so tests
are isolated and can run in any order. NASA access is disabled by default –
the suite must pass on a plane.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.config import Settings  # noqa: E402
from app.domain.state import GameState  # noqa: E402
from app.main import create_app  # noqa: E402
from app.missions.registry import GameContent, load_content  # noqa: E402
from app.services.game_service import create_initial_state  # noqa: E402


@pytest.fixture(scope="session")
def content() -> GameContent:
    """The bundled content pack (immutable, loaded once)."""
    return load_content(ROOT / "content")


def repository_root() -> Path | None:
    """The **git** root – not the nearest directory that happens to hold `.github`.

    GitHub only reads workflows from ``<git root>/.github/workflows``. An earlier
    delivery wrapped the whole project in a ``repo/`` directory, so the nearest
    ``.github/workflows`` was ``repo/.github/workflows``: the repository gates
    passed locally while CI never triggered a single job on the pushed tree.
    Anchoring on ``.git`` is what makes that class of mistake visible.

    Returns ``None`` for a vendored copy with no git metadata and no workflow
    directory, so callers can skip instead of failing for the wrong reason.
    """
    backend = Path(__file__).resolve().parent.parent
    for parent in backend.parents:
        if (parent / ".git").exists():  # a file in worktrees, a directory normally
            return parent
    for parent in backend.parents:
        if (parent / ".github" / "workflows").is_dir():
            return parent
    return None


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "test",
        "database_url": f"sqlite+aiosqlite:///{tmp_path / 'nova-test.db'}",
        "auto_create_tables": True,
        "nasa_enabled": False,
        "rate_limit_enabled": False,
        "auth_enabled": False,
        "log_json": False,
        "log_level": "ERROR",
        "content_dir": ROOT / "content",
        "secret_key": "test-secret",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def module_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """One app for a whole module – for suites that need a long campaign."""
    settings = make_settings(tmp_path_factory.mktemp("nova-module"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def campaign(module_client: TestClient) -> Any:
    """A fully played campaign (every mission completed) driven over HTTP.

    Late-stage endpoints (science, Earth trends, simulator history) are only
    reachable after the chain unlocks, so the suite pays for one real playthrough
    instead of faking state. Uses the same public API a game client would.
    """
    from autopilot import Autopilot

    pilot = Autopilot(client=module_client)
    report = pilot.run_campaign(player_name="Fixture Commander", difficulty="standard", seed=11)
    return SimpleNamespace(
        pilot=pilot,
        client=module_client,
        game_id=pilot.game_id,
        token=pilot.session_token,
        report=report,
        api=Api(module_client, pilot.game_id, pilot.session_token),
    )


@pytest.fixture
def auth_client(tmp_path: Path) -> Iterator[TestClient]:
    """An app with session-token authentication enabled."""
    with TestClient(create_app(make_settings(tmp_path, auth_enabled=True))) as test_client:
        yield test_client


@pytest.fixture
def game(client: TestClient) -> dict[str, Any]:
    """A fresh campaign: returns the full create-game response."""
    response = client.post(
        "/api/v1/games", json={"player_name": "Test Commander", "difficulty": "standard", "seed": 7}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def game_id(game: dict[str, Any]) -> str:
    return str(game["game_id"])


@pytest.fixture
def state(content: GameContent) -> GameState:
    """A pure in-memory save document for domain-level tests."""
    from app.domain.enums import Difficulty
    from app.missions.engine import MissionEngine

    document = create_initial_state(
        content, player_name="Unit", difficulty=Difficulty.standard, seed=7, game_id="nova_unit"
    )
    MissionEngine(content).bootstrap(document)
    return document


class Api:
    """Tiny convenience wrapper so tests read like the game flow."""

    def __init__(self, client: TestClient, game_id: str, token: str = "") -> None:
        self.client = client
        self.game_id = game_id
        self.headers = {"X-Session-Token": token} if token else {}

    def url(self, path: str) -> str:
        return f"/api/v1/games/{self.game_id}{path}"

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.client.get(self.url(path), headers=self.headers, **kwargs)

    def post(self, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        headers = {**self.headers, **kwargs.pop("headers", {})}
        return self.client.post(self.url(path), json=payload or {}, headers=headers, **kwargs)

    def start(self, mission_id: str) -> Any:
        return self.post(f"/missions/{mission_id}/start")

    def act(
        self, mission_id: str, action: str, payload: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        return self.post(
            f"/missions/{mission_id}/actions",
            {"action": action, "payload": payload or {}},
            **kwargs,
        )

    def mission(self, mission_id: str) -> Any:
        return self.get(f"/missions/{mission_id}")


@pytest.fixture
def api(client: TestClient, game: dict[str, Any]) -> Api:
    return Api(client, game["game_id"], game["session_token"])


def complete_training(api: Api) -> None:
    """Walk the Training Facility so later missions unlock with a good loadout."""
    api.start("training_prep")
    for action, payload in (
        ("review_brief", {}),
        ("collect_oxygen", {}),
        ("take_repair_tools", {}),
        ("take_scanner", {}),
        ("inspect_rover", {"check_mode": "full"}),
        ("suit_check", {}),
        ("confirm_readiness", {}),
    ):
        response = api.act("training_prep", action, payload)
        assert response.status_code == 200, response.text


def complete_systems_check(api: Api) -> None:
    api.start("analog_systems_check")
    for action in ("check_ventilation", "check_power_bus", "check_water_loop", "log_system_report"):
        response = api.act("analog_systems_check", action)
        assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# WebSocket helpers
#
# Reading a fixed number of frames is how a stream suite becomes flaky: the
# server batches whatever is queued, so "3 messages" is not a contract. These
# helpers use the protocol itself as the synchronisation point instead.
# ---------------------------------------------------------------------------
def stream_url(game_id: str, after_seq: int = 0, token: str | None = None, **params: Any) -> str:
    query: dict[str, Any] = {"after_seq": after_seq, **params}
    if token:
        query["token"] = token
    encoded = "&".join(f"{key}={value}" for key, value in query.items() if value is not None)
    return f"/api/v1/games/{game_id}/stream?{encoded}"


def read_until(
    socket: WebSocketTestSession,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Read frames until one matches (bounded, so a regression fails instead of hanging)."""
    frames: list[dict[str, Any]] = []
    for _ in range(limit):
        frame = socket.receive_json()
        frames.append(frame)
        if predicate(frame):
            return frames
    raise AssertionError(
        f"no matching frame within {limit} reads (got {[f.get('type') for f in frames]})"
    )


def sync(socket: WebSocketTestSession) -> list[dict[str, Any]]:
    """Ping barrier – collect every frame the server owed us, up to the pong.

    The inbox is FIFO and the handler flushes queued events *before* answering a
    client frame, so ``ping`` → ``pong`` means "everything committed before this
    call has been delivered". Works for the initial replay too: the reader task
    only starts once the backfill is done.
    """
    socket.send_text("ping")
    return read_until(socket, lambda frame: frame.get("type") == "pong")


def events_in(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the event frames of a capture, in delivery order."""
    return [event for frame in frames if frame.get("type") == "events" for event in frame["events"]]
