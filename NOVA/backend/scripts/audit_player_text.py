#!/usr/bin/env python3
"""Audit the language of every string the public API can put in front of a player.

``docs/`` is documentation. This gate is about the payloads a 3D client actually
renders: mission titles, hints, action results, failure messages, hazard text,
achievements, science narratives, Earth reports, simulator notes, WebSocket
events and the legacy Prototype-01 envelopes.

How it works
------------
1. play the whole campaign through the public API (success text),
2. deliberately break the rules on a second save (failure text),
3. call every catalogue, meta, NASA, legacy and console surface,
4. tail the WebSocket feed while an action is executed,
5. walk **every string of every response** and report anything that is not
   English, plus any ``ar`` locale alias that drifted away from ``en``.

Examples
--------
    python scripts/audit_player_text.py                      # in-process
    python scripts/audit_player_text.py --base-url http://127.0.0.1:8000
    python scripts/audit_player_text.py --json report.json --markdown text.md

Exit codes: ``0`` English only, ``1`` non-English player text found,
``2`` the audit could not run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from autopilot import Autopilot  # noqa: E402

#: Arabic (incl. presentation forms) – the language this project migrated away from.
ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
#: Any other non-Latin script would be just as unreadable for an English client.
NON_LATIN = re.compile(
    r"[\u0400-\u04FF\u0530-\u05FF\u0590-\u05FF\u0900-\u097F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]"
)

#: Response keys whose value is prose the player reads (used for the text catalogue).
PLAYER_KEYS = {
    "advice",
    "briefing",
    "caption",
    "conclusion",
    "controls",
    "description",
    "detail",
    "disclaimer",
    "explanation",
    "feedback",
    "guidance",
    "hint",
    "interpretation",
    "label",
    "message",
    "name",
    "narrative",
    "note",
    "notes",
    "objective",
    "prompt",
    "reason",
    "recommendation",
    "recommendations",
    "summary",
    "text",
    "tip",
    "title",
    "unmet",
    "warning",
}

BANNER = "NOVA – Beyond the Mission · player-facing text audit"


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
@dataclass
class Sample:
    """One response body, tagged with the phase that produced it."""

    source: str
    method: str
    path: str
    status: int
    payload: Any


class Recorder:
    """A transparent client wrapper: same responses, plus a transcript.

    ``Autopilot`` only needs ``get``/``post``, so the campaign can be recorded
    without touching the playthrough code.
    """

    def __init__(self, client: Any, source: str = "campaign") -> None:
        self.client = client
        self.source = source
        self.samples: list[Sample] = []

    def _record(self, method: str, response: Any) -> Any:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        path = response.request.url.path
        self.samples.append(Sample(self.source, method, path, response.status_code, payload))
        return response

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._record("GET", self.client.get(url, **kwargs))

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._record("POST", self.client.post(url, **kwargs))

    def delete(self, url: str, **kwargs: Any) -> Any:
        return self._record("DELETE", self.client.delete(url, **kwargs))

    def frame(self, payload: Any, path: str) -> None:
        self.samples.append(Sample("websocket", "WS", path, 101, payload))


# ---------------------------------------------------------------------------
# String walking
# ---------------------------------------------------------------------------
def iter_strings(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from iter_strings(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_strings(child, f"{path}[{index}]")


def locale_drift(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    """Every ``{"en": …, "ar": …}`` pair whose alias is not the English string."""
    if isinstance(value, dict):
        en, ar = value.get("en"), value.get("ar")
        if isinstance(en, str) and isinstance(ar, str) and ar != en:
            yield path, f"ar={ar!r} != en={en!r}"
        for key, child in value.items():
            yield from locale_drift(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from locale_drift(child, f"{path}[{index}]")


def leaf_key(path: str) -> str:
    tail = path.rsplit(".", 1)[-1]
    return tail.split("[", 1)[0]


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
def _try(rec: Recorder, label: str, call: Any) -> None:
    """Run a probe; a rejected request is *data*, not an audit failure."""
    try:
        call()
    except Exception as exc:
        rec.samples.append(Sample(rec.source, "ERR", label, 0, f"{type(exc).__name__}: {exc}"))


def _catalogues(rec: Recorder) -> None:
    rec.source = "catalogues"
    for path in (
        "/api/v1/health",
        "/api/v1/ready",
        "/api/v1/version",
        "/api/v1/content/catalogue",
        "/api/v1/content/controls",
        "/api/v1/content/items",
        "/api/v1/content/resources",
        "/api/v1/content/missions",
        "/api/v1/content/validation",
        "/api/v1/earth/catalogue",
        "/api/v1/science/reference",
        "/api/v1/simulations",
        "/api/v1/leaderboard",
        "/api/v1/nasa/status",
        "/api/v1/nasa/apod",
        "/api/v1/nasa/donki",
        "/api/v1/nasa/epic",
        "/api/v1/nasa/mars-photos",
        "/api/v1/nasa/neo",
        "/api/v1/games",
    ):
        _try(rec, path, lambda p=path: rec.get(p))
    missions = rec.get("/api/v1/content/missions").json()
    for mission in missions:
        _try(rec, "blueprint", lambda mid=mission["id"]: rec.get(f"/api/v1/content/missions/{mid}"))


def _campaign(rec: Recorder, seed: int) -> Autopilot:
    rec.source = "campaign"
    pilot = Autopilot(rec, verbose=False)
    pilot.run_campaign(player_name="Text Auditor", difficulty="standard", seed=seed)
    return pilot


def _player_views(rec: Recorder, pilot: Autopilot) -> None:
    """Every read a HUD, debrief screen or lab screen performs after a campaign."""
    rec.source = "player-views"
    base = f"/api/v1/games/{pilot.game_id}"
    headers = pilot.headers()
    for path in (
        "",
        "/achievements",
        "/continuity",
        "/events",
        "/export",
        "/inventory",
        "/missions",
        "/reports",
        "/resources",
        "/science",
        "/science/samples",
        "/simulation-runs",
        "/suit",
        "/earth/reports",
    ):
        _try(rec, path, lambda p=path: rec.get(f"{base}{p}", headers=headers))

    catalogue = rec.get(f"{base}/missions", headers=headers).json()
    for entry in catalogue:
        mid = entry["id"]
        for suffix in ("", "/interactions", "/report", "/simulation", "/simulation/scene"):
            _try(
                rec,
                "mission-view",
                lambda m=mid, s=suffix: rec.get(f"{base}/missions/{m}{s}", headers=headers),
            )
        _try(
            rec,
            "mission-report",
            lambda m=mid: rec.get(f"{base}/reports/{m}", headers=headers),
        )

    samples = rec.get(f"{base}/science/samples", headers=headers).json()
    for sample in samples:
        sid = sample["sample_id"]
        _try(rec, "sample", lambda s=sid: rec.get(f"{base}/science/samples/{s}", headers=headers))
        _try(
            rec,
            "analyse",
            lambda s=sid: rec.post(f"{base}/science/samples/{s}/analyse", json={}, headers=headers),
        )
        _try(
            rec,
            "log",
            lambda s=sid: rec.post(
                f"{base}/science/samples/{s}/log",
                json={"note": "Logged by the text audit."},
                headers=headers,
            ),
        )

    earth = rec.get(f"{base}/earth/reports", headers=headers).json()
    for report in earth:
        rid = report["report_id"]
        _try(
            rec, "earth-report", lambda r=rid: rec.get(f"{base}/earth/reports/{r}", headers=headers)
        )

    _try(
        rec,
        "earth-compare",
        lambda: rec.post(
            "/api/v1/earth/compare",
            json={
                "region_id": "nile_delta",
                "layer_id": "lst",
                "period_before": "2001-2005",
                "period_after": "2021-2025",
                "grid_size": 4,
            },
        ),
    )
    _try(
        rec,
        "earth-report-save",
        lambda: rec.post(
            f"{base}/earth/reports",
            json={
                "region_id": "nile_delta",
                "layer_id": "ndvi",
                "period_before": "2001-2005",
                "period_after": "2021-2025",
                "grid_size": 4,
                "title": "Delta vegetation watch",
            },
            headers=headers,
        ),
    )
    _try(rec, "resume", lambda: rec.post(f"{base}/resume", json={}, headers=headers))
    _try(
        rec,
        "tick",
        lambda: rec.post(
            f"{base}/tick", json={"dt_seconds": 5, "activity": "walk"}, headers=headers
        ),
    )
    _try(
        rec, "eva-on", lambda: rec.post(f"{base}/suit/eva", json={"active": True}, headers=headers)
    )
    _try(
        rec,
        "eva-off",
        lambda: rec.post(f"{base}/suit/eva", json={"active": False}, headers=headers),
    )
    _try(
        rec,
        "use-item",
        lambda: rec.post(f"{base}/inventory/use", json={"item_id": "oxygen_tank"}, headers=headers),
    )
    _try(
        rec,
        "maintain",
        lambda: rec.post(f"{base}/inventory/maintain", json={"item_id": "wrench"}, headers=headers),
    )
    _try(
        rec,
        "sim-reset",
        lambda: rec.post(
            f"{base}/missions/lunar_landing/simulation/reset", json={}, headers=headers
        ),
    )


def _new_game(rec: Recorder, name: str) -> dict[str, Any]:
    return rec.post(
        "/api/v1/games", json={"player_name": name, "difficulty": "standard", "seed": 7}
    ).json()


def _complete_training(rec: Recorder, base: str, headers: dict[str, str]) -> None:
    rec.post(f"{base}/missions/training_prep/start", json={}, headers=headers)
    for action, payload in (
        ("review_brief", {}),
        ("collect_oxygen", {}),
        ("take_repair_tools", {}),
        ("take_scanner", {}),
        ("inspect_rover", {"check_mode": "full"}),
        ("suit_check", {}),
        ("confirm_readiness", {}),
    ):
        rec.post(
            f"{base}/missions/training_prep/actions",
            json={"action": action, "payload": payload},
            headers=headers,
        )


def _failures(rec: Recorder) -> dict[str, Any]:
    """The text a player sees when they do the wrong thing – the important half."""
    rec.source = "failures"
    created = _new_game(rec, "Rule Breaker")
    gid = created["game_id"]
    headers = {"X-Session-Token": created["session_token"]}
    base = f"/api/v1/games/{gid}"

    def act(mission: str, action: str, payload: dict[str, Any] | None = None) -> Any:
        return rec.post(
            f"{base}/missions/{mission}/actions",
            json={"action": action, "payload": payload or {}},
            headers=headers,
        )

    _try(
        rec,
        "locked-mission",
        lambda: rec.post(f"{base}/missions/mars_arrival/start", json={}, headers=headers),
    )
    _try(
        rec, "unknown-mission", lambda: rec.get(f"{base}/missions/does_not_exist", headers=headers)
    )
    _try(
        rec,
        "unknown-mission-start",
        lambda: rec.post(f"{base}/missions/does_not_exist/start", json={}, headers=headers),
    )
    _try(rec, "action-before-start", lambda: act("training_prep", "review_brief"))
    _try(rec, "unknown-action", lambda: act("training_prep", "does_not_exist"))
    _try(
        rec,
        "report-before-completion",
        lambda: rec.get(f"{base}/missions/training_prep/report", headers=headers),
    )
    _try(
        rec,
        "missing-item-use",
        lambda: rec.post(f"{base}/inventory/use", json={"item_id": "oxygen_tank"}, headers=headers),
    )
    _try(
        rec,
        "missing-item-maintain",
        lambda: rec.post(
            f"{base}/inventory/maintain", json={"item_id": "scanner"}, headers=headers
        ),
    )
    _try(
        rec,
        "unknown-sample",
        lambda: rec.post(f"{base}/science/samples/nova_none/analyse", json={}, headers=headers),
    )
    _try(
        rec,
        "unknown-earth-report",
        lambda: rec.get(f"{base}/earth/reports/nova_none", headers=headers),
    )
    _try(
        rec,
        "no-simulation",
        lambda: rec.get(f"{base}/missions/training_prep/simulation", headers=headers),
    )
    _try(
        rec,
        "no-scene",
        lambda: rec.get(f"{base}/missions/training_prep/simulation/scene", headers=headers),
    )
    _try(
        rec,
        "sim-tick-without-run",
        lambda: rec.post(
            f"{base}/missions/training_prep/simulation/tick",
            json={"command": {}, "dt_seconds": 0.5},
            headers=headers,
        ),
    )
    _try(
        rec,
        "restart-locked",
        lambda: rec.post(f"{base}/missions/mars_arrival/restart", json={}, headers=headers),
    )
    _try(rec, "unknown-game", lambda: rec.get("/api/v1/games/nova_missing_session"))
    _try(
        rec,
        "bad-save",
        lambda: rec.post("/api/v1/games/import", json={"format": "not-a-save", "state": {}}),
    )
    _try(rec, "bad-player-name", lambda: rec.post("/api/v1/games", json={"player_name": "x" * 80}))
    _try(
        rec,
        "bad-difficulty",
        lambda: rec.post("/api/v1/games", json={"player_name": "Ok", "difficulty": "impossible"}),
    )
    _try(
        rec,
        "bad-region",
        lambda: rec.post(
            "/api/v1/earth/compare",
            json={
                "region_id": "atlantis",
                "layer_id": "lst",
                "period_before": "2001-2005",
                "period_after": "2021-2025",
            },
        ),
    )
    _try(
        rec,
        "same-periods",
        lambda: rec.post(
            "/api/v1/earth/compare",
            json={
                "region_id": "nile_delta",
                "layer_id": "lst",
                "period_before": "2001-2005",
                "period_after": "2001-2005",
            },
        ),
    )

    # The flagship rule: repairing a live pipe must fail, in English.
    _complete_training(rec, base, headers)
    rec.post(f"{base}/missions/analog_systems_check/start", json={}, headers=headers)
    for action in ("check_ventilation", "check_power_bus", "check_water_loop", "log_system_report"):
        act("analog_systems_check", action)
    rec.post(f"{base}/missions/analog_water_leak/start", json={}, headers=headers)
    _try(rec, "valve-before-scan", lambda: act("analog_water_leak", "close_valve"))
    act("analog_water_leak", "scan_leak")
    _try(rec, "repair-live-pipe", lambda: act("analog_water_leak", "repair_pipe"))
    _try(
        rec,
        "bad-payload-choice",
        lambda: act("analog_water_leak", "repair_pipe", {"method": "banana"}),
    )
    _try(
        rec,
        "completed-start",
        lambda: rec.post(f"{base}/missions/analog_systems_check/start", json={}, headers=headers),
    )
    _try(
        rec,
        "completed-restart",
        lambda: rec.post(f"{base}/missions/analog_systems_check/restart", json={}, headers=headers),
    )
    _try(
        rec,
        "not-usable-item",
        lambda: rec.post(f"{base}/inventory/use", json={"item_id": "wrench"}, headers=headers),
    )
    return {"game_id": gid, "headers": headers, "base": base}


def _legacy(rec: Recorder) -> None:
    rec.source = "legacy"
    created = rec.post("/games", json={"player_name": "Prototype Client"}).json()
    gid = created["game_id"]
    for path in ("/state", "/resources", "/inventory", "/missions/water-leak"):
        _try(rec, path, lambda p=path: rec.get(f"/games/{gid}{p}"))
    _try(
        rec,
        "legacy-conflict",
        lambda: rec.post(
            f"/games/{gid}/missions/water-leak/actions", json={"action": "repair_pipe"}
        ),
    )
    _try(
        rec,
        "legacy-unknown-action",
        lambda: rec.post(f"/games/{gid}/missions/water-leak/actions", json={"action": "nope"}),
    )
    _try(rec, "legacy-delete", lambda: rec.delete(f"/api/v1/games/{gid}"))
    _try(rec, "deleted-game", lambda: rec.get(f"/api/v1/games/{gid}"))


def _websocket(rec: Recorder, session: dict[str, Any]) -> None:
    """Tail the live feed while an action lands – event text is player text too."""
    rec.source = "websocket"
    gid, headers, base = session["game_id"], session["headers"], session["base"]
    path = f"/api/v1/games/{gid}/stream"
    token = headers.get("X-Session-Token", "")
    query = f"/api/v1/games/{gid}/stream?after_seq=0" + (f"&token={token}" if token else "")

    def drain(socket: Any) -> None:
        socket.send_text("ping")
        for _ in range(80):
            frame = socket.receive_json()
            rec.frame(frame, path)
            if frame.get("type") == "pong":
                return

    def play(socket: Any) -> None:
        drain(socket)
        rec.post(
            f"{base}/missions/analog_water_leak/actions",
            json={"action": "close_valve", "payload": {}},
            headers=headers,
        )
        drain(socket)

    try:
        if hasattr(rec.client, "websocket_connect"):  # in-process TestClient
            with rec.client.websocket_connect(query) as socket:
                play(socket)
        else:  # a real socket against a running uvicorn
            with _live_socket(rec.client, query) as socket:
                play(socket)
    except Exception as exc:
        rec.samples.append(Sample("websocket", "ERR", path, 0, f"{type(exc).__name__}: {exc}"))


@contextmanager
def _live_socket(client: Any, query: str) -> Iterator[Any]:
    """Adapt ``websockets.sync`` to the two calls the drain loop needs."""
    from websockets.sync.client import connect

    base = str(client.base_url).rstrip("/")
    url = base.replace("https://", "wss://").replace("http://", "ws://") + query

    class _Socket:
        def __init__(self, socket: Any) -> None:
            self.socket = socket

        def send_text(self, text: str) -> None:
            self.socket.send(text)

        def receive_json(self) -> Any:
            return json.loads(self.socket.recv(timeout=15))

    with connect(url, open_timeout=15) as socket:
        yield _Socket(socket)


def _console(rec: Recorder) -> None:
    rec.source = "console"
    _try(rec, "console", lambda: rec.get("/"))


def _save_transfer(rec: Recorder, pilot: Autopilot) -> None:
    rec.source = "save-transfer"
    base = f"/api/v1/games/{pilot.game_id}"
    bundle = rec.get(f"{base}/export", headers=pilot.headers()).json()
    _try(rec, "import", lambda: rec.post("/api/v1/games/import", json=bundle))


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def analyse(samples: list[Sample], openapi: dict[str, Any] | None = None) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    player_text: dict[str, set[str]] = defaultdict(set)
    typography: dict[str, int] = defaultdict(int)
    error_responses: list[dict[str, Any]] = []
    strings = 0

    for sample in samples:
        where = f"{sample.method} {sample.path} [{sample.status}] ({sample.source})"
        if sample.status >= 400 or sample.status == 0:
            error_responses.append(
                {
                    "source": sample.source,
                    "method": sample.method,
                    "path": sample.path,
                    "status": sample.status,
                    "error": sample.payload.get("error")
                    if isinstance(sample.payload, dict)
                    else None,
                    "detail": sample.payload.get("detail")
                    if isinstance(sample.payload, dict)
                    else None,
                }
            )
        for path, text in iter_strings(sample.payload):
            strings += 1
            if ARABIC.search(text):
                violations.append({"where": where, "field": path, "kind": "arabic", "text": text})
            elif NON_LATIN.search(text):
                violations.append(
                    {"where": where, "field": path, "kind": "non-latin", "text": text}
                )
            for char in text:
                if ord(char) > 127:
                    typography[char] += 1
            key = leaf_key(path)
            if key in PLAYER_KEYS and text.strip() and not text.startswith(("http", "/")):
                player_text[key].add(text)
        for path, drift in locale_drift(sample.payload):
            violations.append(
                {"where": where, "field": path, "kind": "locale-alias", "text": drift}
            )

    covered: set[str] = set()
    operations: set[str] = set()
    if openapi:
        templates = []
        for route, ops in openapi.get("paths", {}).items():
            pattern = re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", route) + "$")
            for method in ops:
                if method in {"get", "post", "put", "patch", "delete"}:
                    operations.add(f"{method.upper()} {route}")
                    templates.append((f"{method.upper()} {route}", pattern))
        for sample in samples:
            for name, pattern in templates:
                if name.startswith(sample.method + " ") and pattern.match(sample.path):
                    covered.add(name)

    return {
        "responses_scanned": len(samples),
        "strings_scanned": strings,
        "distinct_player_strings": sum(len(values) for values in player_text.values()),
        "operations_covered": len(covered),
        "operations_total": len(operations),
        "operations_missed": sorted(operations - covered),
        "violations": violations,
        "error_responses": error_responses,
        "non_ascii_typography": dict(sorted(typography.items(), key=lambda kv: -kv[1])),
        "player_text": {key: sorted(values) for key, values in sorted(player_text.items())},
        "sources": {
            source: sum(1 for sample in samples if sample.source == source)
            for source in sorted({sample.source for sample in samples})
        },
    }


def run_audit(
    client: Any, *, seed: int = 7, openapi: dict[str, Any] | None = None
) -> dict[str, Any]:
    rec = Recorder(client)
    _catalogues(rec)
    pilot = _campaign(rec, seed)
    _player_views(rec, pilot)
    session = _failures(rec)
    _websocket(rec, session)
    _legacy(rec)
    _save_transfer(rec, pilot)
    _console(rec)
    return analyse(rec.samples, openapi)


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# NOVA – player-facing API text (English catalogue)",
        "",
        f"- responses scanned: **{report['responses_scanned']}**",
        f"- strings scanned: **{report['strings_scanned']}**",
        f"- distinct player strings: **{report['distinct_player_strings']}**",
        f"- operations exercised: **{report['operations_covered']}/{report['operations_total']}**",
        f"- non-English violations: **{len(report['violations'])}**",
        "",
        "Every string below came out of a real API response (campaign, failure paths,",
        "legacy routes and the WebSocket feed). Documentation is not included.",
        "",
    ]
    if report["operations_missed"]:
        lines += ["## Operations not exercised", ""]
        lines += [f"- `{op}`" for op in report["operations_missed"]]
        lines.append("")
    for key, values in report["player_text"].items():
        lines += [f"## `{key}` ({len(values)})", ""]
        lines += [f"- {value}" for value in values]
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
@contextmanager
def in_process_client() -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    with tempfile.TemporaryDirectory(prefix="nova-text-") as tmp:
        settings = Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{Path(tmp) / 'audit.db'}",
            nasa_enabled=False,
            rate_limit_enabled=False,
            auth_enabled=False,
            log_level="ERROR",
            log_json=False,
            content_dir=ROOT / "content",
            _env_file=None,
        )
        with TestClient(create_app(settings)) as client:
            yield client


@contextmanager
def http_client(base_url: str) -> Iterator[Any]:
    import httpx

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        yield client


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit_player_text",
        description="Scan every player-facing API string for non-English text.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--in-process", action="store_true", help="Boot the app here (default).")
    target.add_argument("--base-url", help="Audit a running backend instead.")
    parser.add_argument("--seed", type=int, default=7, help="Deterministic campaign seed.")
    parser.add_argument("--json", dest="json_out", type=Path, help="Write the full report as JSON.")
    parser.add_argument("--markdown", dest="md_out", type=Path, help="Write the text catalogue.")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.quiet:
        print(BANNER)

    factory = http_client(args.base_url) if args.base_url else in_process_client()
    try:
        with factory as client:
            openapi: dict[str, Any] | None = None
            if hasattr(client, "app"):
                openapi = client.app.openapi()
            else:
                response = client.get("/openapi.json")
                if response.status_code == 200:
                    openapi = response.json()
            report = run_audit(client, seed=args.seed, openapi=openapi)
    except Exception as exc:
        print(f"\n✗ could not run the audit: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(markdown(report), encoding="utf-8")

    print(f"responses scanned : {report['responses_scanned']}")
    print(f"strings scanned   : {report['strings_scanned']}")
    print(f"player strings    : {report['distinct_player_strings']}")
    print(f"operations        : {report['operations_covered']}/{report['operations_total']}")
    for source, count in report["sources"].items():
        print(f"  {source:<16} {count:>5} responses")
    if report["violations"]:
        print(f"\n✗ {len(report['violations'])} non-English player strings:")
        for violation in report["violations"][:40]:
            print(f"  {violation['kind']:<12} {violation['where']} {violation['field']}")
            print(f"    {violation['text'][:160]}")
        return 1
    print("\n✓ every player-facing string is English")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
