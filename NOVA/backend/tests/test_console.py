"""Mission Control console – the static dev client served at ``/``.

The console is the reference client for the gameplay API: it is a single
dependency-free HTML file that must keep working inside a sandboxed iframe and
behind a reverse proxy. These tests treat it as a *contract*, not as markup:

* it is self-contained (no CDN, no build step, works offline),
* it only uses relative URLs (never ``localhost`` – the browser is not the server),
* every DOM id the script touches actually exists (the classic console typo),
* every endpoint the script calls exists in the OpenAPI schema,
* the simulator controllers cover exactly the simulators the backend ships.

If someone renames a route or an element id, this suite fails instead of the
console silently breaking in the browser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

CONSOLE_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"
API_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# A very small JS reader: enough to resolve the console's string concatenations
# ---------------------------------------------------------------------------
def _split_top_level(expression: str, separator: str = "+") -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    current = ""
    for char in expression:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'`":
            quote = char
            current += char
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == separator and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _first_argument(call_body: str) -> str:
    """Return the first argument of ``api(...)`` (stops at a top-level comma)."""
    depth = 0
    quote = ""
    for index, char in enumerate(call_body):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'`":
            quote = char
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                return call_body[:index]
            depth -= 1
        elif char == "," and depth == 0:
            return call_body[:index]
    return call_body


class ConsoleScript:
    """Static reader for the inline console script."""

    PLACEHOLDER = "__param__"

    def __init__(self, html: str) -> None:
        self.html = html
        self.script = "\n".join(re.findall(r"<script>(.*?)</script>", html, flags=re.S))
        self.symbols: dict[str, str] = {"BASE": "", "API": API_PREFIX}
        for name, body in re.findall(
            r"const\s+(\w+)\s*=\s*(?:\(\)\s*=>\s*)?([^;\n]+);", self.script
        ):
            if name not in self.symbols and ('"' in body or "'" in body):
                self.symbols[name] = body

    # -- DOM ---------------------------------------------------------------
    @property
    def dom_ids(self) -> set[str]:
        return set(re.findall(r'\bid="([^"]+)"', self.html))

    @property
    def referenced_ids(self) -> set[str]:
        return set(re.findall(r'\$\("([^"]+)"\)', self.script))

    # -- API ---------------------------------------------------------------
    def resolve(self, expression: str, depth: int = 0) -> str | None:
        """Resolve a JS string concatenation into a path with placeholders."""
        if depth > 6:
            return None
        chunks: list[str] = []
        for token in _split_top_level(expression):
            token = token.strip()
            if not token:
                continue
            if token[:1] in {'"', "'", "`"} and token[-1:] == token[:1]:
                chunks.append(token[1:-1])
                continue
            call = re.fullmatch(r"(\w+)\(\)", token)
            if call and call.group(1) in self.symbols:
                inner = self.resolve(self.symbols[call.group(1)], depth + 1)
                if inner is None:
                    return None
                chunks.append(inner)
                continue
            if re.fullmatch(r"[\w.]+", token) and token in self.symbols:
                inner = self.resolve(self.symbols[token], depth + 1)
                if inner is None:
                    return None
                chunks.append(inner)
                continue
            # Anything else is a runtime value: an id, a token, a timestamp…
            chunks.append(self.PLACEHOLDER)
        return "".join(chunks)

    def api_paths(self) -> dict[str, str]:
        """Every literal ``api(...)`` call → resolved path (expression kept for errors)."""
        found: dict[str, str] = {}
        for match in re.finditer(r"\bapi\(", self.script):
            expression = _first_argument(self.script[match.end() :])
            resolved = self.resolve(expression)
            if resolved is None or not resolved.startswith("/"):
                continue
            found[expression.strip()] = resolved.split("?")[0]
        return found


@pytest.fixture(scope="module")
def console() -> ConsoleScript:
    return ConsoleScript(CONSOLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def openapi(client: TestClient) -> dict[str, Any]:
    return client.get("/openapi.json").json()


def _route_matchers(schema: dict[str, Any]) -> list[re.Pattern[str]]:
    return [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$") for path in schema["paths"]]


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------
def test_console_is_served_at_the_root(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>NOVA · Mission Control</title>" in response.text


def test_console_is_also_reachable_as_a_static_asset(client: TestClient) -> None:
    response = client.get("/static/index.html")
    assert response.status_code == 200
    assert response.text == client.get("/").text


def test_console_works_behind_a_proxy_prefix(tmp_path: Path) -> None:
    """A relative API base means the console survives ``/nova/`` style mounts."""
    from conftest import make_settings

    with TestClient(create_app(make_settings(tmp_path)), root_path="/nova") as proxied:
        page = proxied.get("/")
        assert page.status_code == 200
        assert proxied.get("/api/v1/health").status_code == 200
    script = ConsoleScript(page.text)
    assert 'new URL(".", window.location.href)' in script.script


# ---------------------------------------------------------------------------
# Self-contained and proxy-safe
# ---------------------------------------------------------------------------
def test_console_has_no_external_dependencies(console: ConsoleScript) -> None:
    external = re.findall(r'(?:src|href)\s*=\s*"(https?:)?//[^"]+"', console.html)
    assert external == [], f"console must work offline, found external assets: {external}"
    assert "<script src" not in console.html
    assert "@import" not in console.html


def test_console_never_hardcodes_a_host(console: ConsoleScript) -> None:
    for forbidden in ("localhost", "127.0.0.1", "0.0.0.0", "http://", "https://"):
        assert forbidden not in console.script, f"console script must stay relative: {forbidden}"


def test_console_builds_the_websocket_url_from_the_page_origin(console: ConsoleScript) -> None:
    assert 'window.location.protocol === "https:" ? "wss:" : "ws:"' in console.script
    assert "window.location.host" in console.script


def test_console_states_the_simulation_disclaimer(console: ConsoleScript) -> None:
    assert "educational" in console.html.lower()
    assert "never real medical" in console.html


# ---------------------------------------------------------------------------
# DOM wiring – the classic "undefined is not an object" console bug
# ---------------------------------------------------------------------------
def test_every_referenced_element_exists(console: ConsoleScript) -> None:
    missing = console.referenced_ids - console.dom_ids
    assert not missing, f"script touches missing element ids: {sorted(missing)}"


def test_element_ids_are_unique(console: ConsoleScript) -> None:
    ids = re.findall(r'\bid="([^"]+)"', console.html)
    duplicates = {name for name in ids if ids.count(name) > 1}
    assert not duplicates, f"duplicate element ids: {sorted(duplicates)}"


def test_every_button_is_wired(console: ConsoleScript) -> None:
    """A button that no handler listens to is a dead control."""
    buttons = set(re.findall(r'<button[^>]*\bid="([^"]+)"', console.html))
    wired = set(re.findall(r'\$\("(\w+)"\)\.addEventListener', console.script))
    dead = buttons - wired
    assert not dead, f"buttons without a click handler: {sorted(dead)}"


# ---------------------------------------------------------------------------
# API contract – the console may only call routes that exist
# ---------------------------------------------------------------------------
def test_console_only_calls_real_endpoints(console: ConsoleScript, openapi: dict[str, Any]) -> None:
    matchers = _route_matchers(openapi)
    calls = console.api_paths()
    assert len(calls) >= 15, "the reader stopped understanding the console script"
    unknown = {
        expression: path
        for expression, path in calls.items()
        if not any(matcher.match(API_PREFIX + path) for matcher in matchers)
    }
    assert not unknown, f"console calls endpoints that do not exist: {unknown}"


def test_console_covers_the_whole_gameplay_loop(
    console: ConsoleScript, openapi: dict[str, Any]
) -> None:
    called = set(console.api_paths().values())
    for required in (
        "/games",
        "/games/__param__",
        "/games/__param__/tick",
        "/games/__param__/missions",
        "/games/__param__/missions/__param__/start",
        "/games/__param__/missions/__param__/actions",
        "/games/__param__/missions/__param__/interactions",
        "/games/__param__/missions/__param__/report",
        "/games/__param__/inventory/use",
        "/games/__param__/inventory/maintain",
        "/games/__param__/suit/eva",
        "/content/missions/__param__",
        "/health",
    ):
        assert required in called, f"console never exercises {required}"


def test_console_stream_target_is_a_real_websocket_route(
    client: TestClient, game_id: str, console: ConsoleScript
) -> None:
    """The URL the console builds must actually accept a socket."""
    assert '"/stream?after_seq="' in console.script
    with client.websocket_connect(f"/api/v1/games/{game_id}/stream?after_seq=0") as socket:
        ready = socket.receive_json()
    assert ready["type"] == "stream.ready"
    assert ready["game_id"] == game_id


def test_console_autopilots_match_the_shipped_simulators(
    console: ConsoleScript, client: TestClient
) -> None:
    kinds = {entry["kind"] for entry in client.get("/api/v1/simulations").json()}
    controllers = set(re.findall(r"^  (\w+)\(", console.script, flags=re.M))
    defaults = set(
        re.findall(r"(\w+):\s*\{", console.script.split("function defaultCommand")[1][:400])
    )
    assert kinds <= controllers, f"no autopilot for: {sorted(kinds - controllers)}"
    assert kinds <= defaults, f"no default command for: {sorted(kinds - defaults)}"


# ---------------------------------------------------------------------------
# Service card – the readiness probe the operator reads while playing
# ---------------------------------------------------------------------------
def _service_reader(script: str) -> str:
    start = script.index("async function refreshService()")
    return script[start : script.index("\n}", start)]


def test_console_probes_readiness(console: ConsoleScript) -> None:
    assert "/ready" in set(console.api_paths().values())
    assert "service readiness" in console.html  # also available in the inspector
    assert "guard(refreshService)" in console.script


def test_every_readiness_field_the_console_renders_really_exists(
    console: ConsoleScript, client: TestClient, game_id: str
) -> None:
    """The Service card must not print ``undefined`` after a payload change."""
    reader = _service_reader(console.script)
    with client.websocket_connect(f"/api/v1/games/{game_id}/stream?after_seq=0") as socket:
        assert socket.receive_json()["type"] == "stream.ready"
        body = client.get("/api/v1/ready").json()
        assert body["stream"]["subscribers"] == 1, "the console counts this very socket"
    assert client.get("/api/v1/ready").json()["stream"]["subscribers"] == 0

    top = set(re.findall(r"\br\.(\w+)", reader))
    stream = set(re.findall(r"\bs\.(\w+)", reader))
    cache = set(re.findall(r"\bcache\.(\w+)", reader))
    assert top and stream and cache, "the reader stopped understanding the Service card"
    assert top <= set(body), f"console reads unknown readiness fields: {sorted(top - set(body))}"
    assert stream <= set(body["stream"]), f"unknown stream stats: {sorted(stream)}"
    assert cache <= set(body["nasa_cache"]), f"unknown cache stats: {sorted(cache)}"


def test_console_reprobes_the_fan_out_when_a_socket_opens_and_closes(
    console: ConsoleScript,
) -> None:
    """A stale subscriber count is worse than none: re-probe on both edges."""
    ready_block = console.script.split('if (msg.type === "stream.ready")')[1][:200]
    assert "refreshService" in ready_block
    close_block = console.script.split("ws.onclose =")[1][:220]
    assert "refreshService" in close_block


# ---------------------------------------------------------------------------
# Regressions found by driving the console against the live API
# ---------------------------------------------------------------------------
def test_side_actions_are_renderable_objects(api: Any) -> None:
    """``side_actions`` is a list of ids – the console needs the views beside it.

    Rendering ``Object.assign({side: true}, "collect_extra_sample")`` produced
    rows with ``data-act="undefined"``; ``side_objectives`` fixes that.
    """
    api.start("training_prep")
    view = api.mission("analog_device_fault").json()
    assert view["side_actions"], "fixture mission should declare optional work"
    assert isinstance(view["side_actions"][0], str)
    views = {objective["id"]: objective for objective in view["side_objectives"]}
    assert set(views) == set(view["side_actions"])
    for objective in views.values():
        assert objective["title"]["en"]
        assert isinstance(objective["available"], bool)
        assert isinstance(objective["completed"], bool)
        assert objective["target"]


def test_console_and_blueprint_agree_on_payload_specs(client: TestClient, api: Any) -> None:
    blueprint = client.get("/api/v1/content/missions/training_prep").json()
    specs = {objective["id"]: objective for objective in blueprint["objectives"]}
    assert specs["inspect_rover"]["payload"][0]["name"] == "check_mode"
    view = api.mission("training_prep").json()
    assert {objective["id"] for objective in view["objectives"]} == set(specs)
