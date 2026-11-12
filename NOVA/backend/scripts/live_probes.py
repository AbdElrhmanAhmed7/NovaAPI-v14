"""Live probes: a real uvicorn process, a real socket, real NASA when reachable.

The pytest suite drives the app in-process through Starlette's ``TestClient``.
That is the right tool for 437 assertions, but it never opens a socket: it
cannot prove that uvicorn serves the console, that a WebSocket client resumes
from its cursor, or that ``provenance: live`` really comes from NASA.

This script is the other half of the evidence. It boots the app exactly the way
production does (``uvicorn app.main:app``), then exercises the public API:

* liveness / readiness / the dependency-free Mission Control console,
* the write guard rejecting a token-less action with ``401``,
* two missions played through the reference autopilot,
* the canonical water-leak rule: repairing before closing the valve **fails**,
* ``WS /games/{id}/stream``: ``stream.ready``, live events, and a resume,
* a movement ``tick`` consuming oxygen (move → interact → consequence),
* NASA POWER (live, then the TTL cache) + APOD, reporting ``live`` / ``cache`` /
  ``bundled`` honestly, with the fallback reason when it degraded.

Usage::

    make probes                                    # from NOVA/backend
    python scripts/live_probes.py                    # free port, live NASA
    python scripts/live_probes.py --no-live          # offline (bundled fallback)
    python scripts/live_probes.py --json probes.json

Exit status is non-zero when a *required* probe fails. NASA reachability is
reported, never required: the designed degradation is a labelled fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from autopilot import Autopilot  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Probes:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, name: str, ok: bool, detail: str, *, required: bool = True) -> None:
        status = "PASS" if ok else ("FAIL" if required else "INFO")
        self.rows.append({"probe": name, "status": status, "detail": detail})
        mark = {"PASS": "✓", "FAIL": "✗", "INFO": "·"}[status]
        print(f"  {mark} {name:<26} {detail}")

    @property
    def failed(self) -> int:
        return sum(1 for row in self.rows if row["status"] == "FAIL")


def wait_ready(client: httpx.Client, process: subprocess.Popen[bytes], log: Path) -> dict[str, Any]:
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError(f"uvicorn exited early:\n{log.read_text(encoding='utf-8')[-2000:]}")
        try:
            response = client.get("/api/v1/ready")
            if response.status_code == 200:
                return dict(response.json())
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"server never became ready:\n{log.read_text(encoding='utf-8')[-2000:]}")


async def stream_frames(url: str, during: Any, expected: int = 2) -> list[dict[str, Any]]:
    """Read ``stream.ready``, run an HTTP action, then collect the event frames."""
    import websockets  # provided by uvicorn[standard]

    frames: list[dict[str, Any]] = []
    async with websockets.connect(url, open_timeout=15) as socket_:
        frames.append(json.loads(await asyncio.wait_for(socket_.recv(), 15)))
        during()  # a blocking HTTP call: the server is a separate process
        for _ in range(expected):
            try:
                frames.append(json.loads(await asyncio.wait_for(socket_.recv(), 10)))
            except TimeoutError:
                break
    return frames


def run(args: argparse.Namespace) -> int:
    port = args.port or free_port()
    base = f"http://{args.host}:{port}"
    probes = Probes()
    print(f"── NOVA live probes · {base}")

    with tempfile.TemporaryDirectory(prefix="nova-probes-") as tmp:
        env = {
            **os.environ,
            "NOVA_DATABASE_URL": f"sqlite+aiosqlite:///{Path(tmp) / 'probes.db'}",
            "NOVA_AUTO_CREATE_TABLES": "true",
            "NOVA_AUTH_ENABLED": "true",  # prove the guard on a real socket
            "NOVA_RATE_LIMIT_ENABLED": "false",
            "NOVA_NASA_ENABLED": "false" if args.no_live else "true",
            "NOVA_LOG_LEVEL": "WARNING",
            "NOVA_LOG_JSON": "false",
        }
        server_log = Path(tmp) / "uvicorn.log"
        with server_log.open("wb") as handle:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    args.host,
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ],
                cwd=ROOT,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(base_url=base, timeout=60.0) as client:
                    ready = wait_ready(client, process, server_log)
                    health = client.get("/api/v1/health").json()
                    probes.record(
                        "health",
                        health.get("status") == "ok",
                        f"status {health.get('status')} · version {health.get('version')}",
                    )
                    content_ok = bool(ready.get("content"))
                    probes.record(
                        "ready",
                        ready.get("status") == "ready" and bool(ready.get("database")),
                        f"database {ready.get('database')} · content {content_ok} · "
                        f"{ready.get('missions_loaded')} missions · "
                        f"{ready.get('items_loaded')} items",
                    )

                    console = client.get("/")
                    body = console.text
                    external = [
                        token
                        for token in body.replace('"', " ").replace("'", " ").split()
                        if token.startswith(("http://", "https://"))
                    ]
                    probes.record(
                        "console",
                        console.status_code == 200 and not external,
                        f"{console.status_code} · {len(console.content):,} bytes · "
                        f"{len(external)} external asset(s)",
                    )

                    created = client.post(
                        "/api/v1/games", json={"player_name": "Live probe", "seed": 7}
                    ).json()
                    game_id = created["game_id"]
                    token = created.get("session_token", "")
                    unguarded = client.post(f"/api/v1/games/{game_id}/missions/training_prep/start")
                    probes.record(
                        "write guard",
                        unguarded.status_code == 401,
                        f"token-less start → {unguarded.status_code} "
                        f"{(unguarded.json().get('error') or {}).get('code', '')}",
                    )

                    client.headers["X-Session-Token"] = token
                    # httpx.Client satisfies the autopilot's duck-typed protocol
                    # at runtime; its keyword-only signature is stricter than the
                    # Protocol, so the type checker needs the cast.
                    pilot = Autopilot(client=cast(Any, client))
                    pilot.game_id = game_id
                    pilot.session_token = token
                    for mission in ("training_prep", "analog_systems_check"):
                        outcome = pilot.play_mission(mission)
                        probes.record(
                            mission,
                            outcome.mistakes == 0,
                            f"{outcome.score_pct}% grade {outcome.grade} · "
                            f"{outcome.steps} steps · {outcome.mistakes} mistakes",
                        )

                    prefix = f"/api/v1/games/{game_id}"
                    client.post(f"{prefix}/missions/analog_water_leak/start")
                    water_before = client.get(f"{prefix}/resources").json()
                    early = client.post(
                        f"{prefix}/missions/analog_water_leak/actions",
                        json={"action": "repair_pipe"},
                    )
                    error = (early.json().get("error") or {}) if early.status_code >= 400 else {}
                    probes.record(
                        "leak order enforced",
                        early.status_code == 409 and error.get("code") == "precondition_failed",
                        f"repair before valve → {early.status_code} {error.get('code')} · "
                        f"unmet {(error.get('details') or {}).get('unmet')}",
                    )

                    moved = client.post(
                        f"{prefix}/tick", json={"dt_seconds": 60.0, "activity": "run"}
                    )
                    telemetry = moved.json()
                    probes.record(
                        "movement tick",
                        moved.status_code == 200,
                        f"60 s run → {moved.status_code} · "
                        f"oxygen {telemetry.get('resources', {}).get('oxygen')} · "
                        f"water {telemetry.get('resources', {}).get('water')} (leak active)",
                    )
                    water_drained = client.get(f"{prefix}/resources").json()

                    ws_url = (
                        f"ws://{args.host}:{port}/api/v1/games/{game_id}/stream"
                        f"?after_seq=0&token={token}"
                    )

                    def scan() -> None:
                        client.post(
                            f"{prefix}/missions/analog_water_leak/actions",
                            json={"action": "scan_leak"},
                        )

                    frames = asyncio.run(stream_frames(ws_url, scan))
                    kinds = [frame.get("type") for frame in frames]
                    events = [
                        event.get("type")
                        for frame in frames
                        for event in (frame.get("events") or [])
                    ]
                    probes.record(
                        "websocket stream",
                        kinds[:1] == ["stream.ready"] and "action.succeeded" in events,
                        f"frames {kinds} · events {events[:4]}",
                    )

                    cursor = max(
                        (
                            int(frame.get("next_seq") or frame.get("event_seq") or 0)
                            for frame in frames
                        ),
                        default=0,
                    )
                    resumed = asyncio.run(
                        stream_frames(
                            f"ws://{args.host}:{port}/api/v1/games/{game_id}/stream"
                            f"?after_seq={cursor}&token={token}",
                            lambda: None,
                            expected=1,
                        )
                    )
                    probes.record(
                        "stream resume",
                        bool(resumed) and resumed[0].get("type") == "stream.ready",
                        f"reconnect after_seq={cursor} → {resumed[0].get('type')} · "
                        f"event_seq {resumed[0].get('event_seq')}",
                    )

                    for action in ("close_valve", "repair_pipe", "verify_pressure"):
                        client.post(
                            f"{prefix}/missions/analog_water_leak/actions",
                            json={"action": action},
                        )
                    report = client.get(f"{prefix}/missions/analog_water_leak/report").json()
                    water_after = client.get(f"{prefix}/resources").json()
                    world = client.get(prefix).json().get("world", {})
                    probes.record(
                        "leak repaired",
                        report.get("status") == "completed" and world.get("leak_active") is False,
                        f"grade {report.get('grade')} ({report.get('mistakes')} deliberate "
                        f"mistake) · water {_water(water_before)} → {_water(water_drained)} "
                        f"→ {_water(water_after)} L · leak_active {world.get('leak_active')}",
                    )

                    if args.no_live:
                        probes.record(
                            "NASA (skipped)", True, "--no-live: offline run", required=False
                        )
                    else:
                        comparison = client.post(
                            "/api/v1/earth/compare",
                            json={
                                "region_id": "nile_delta",
                                "layer_id": "lst",
                                "period_before": "2001-2005",
                                "period_after": "2021-2025",
                                "live": True,
                            },
                        ).json()
                        probes.record(
                            "NASA POWER",
                            comparison.get("provenance") in {"live", "cache", "bundled"},
                            f"provenance {comparison.get('provenance')} · "
                            f"Δmean {comparison.get('mean_delta')} {comparison.get('unit')} "
                            f"({comparison.get('mean_delta_pct')}%) · "
                            f"{comparison.get('verdict')} · "
                            f"{len(comparison.get('hotspots') or [])} hotspots · "
                            f"{comparison.get('nasa_product') or 'no product'}"
                            + _reason(comparison),
                            required=False,
                        )
                        apod = client.get("/api/v1/nasa/apod").json()
                        probes.record(
                            "NASA APOD",
                            apod.get("provenance") in {"live", "cache", "bundled"},
                            f"provenance {apod.get('provenance')} · "
                            f"{(apod.get('data') or {}).get('title', 'no title')}" + _reason(apod),
                            required=False,
                        )
                        cached = client.post(
                            "/api/v1/earth/compare",
                            json={
                                "region_id": "nile_delta",
                                "layer_id": "lst",
                                "period_before": "2001-2005",
                                "period_after": "2021-2025",
                                "live": True,
                            },
                        ).json()
                        probes.record(
                            "NASA POWER cache",
                            cached.get("mean_delta") == comparison.get("mean_delta"),
                            f"provenance {cached.get('provenance')} · "
                            f"same Δmean {cached.get('mean_delta')} {cached.get('unit')}",
                            required=False,
                        )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

    print(f"\n{len(probes.rows)} probes · {probes.failed} failure(s)")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"base_url": base, "probes": probes.rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"report: {args.json}")
    return 1 if probes.failed else 0


def _reason(payload: dict[str, Any]) -> str:
    """A labelled fallback is a feature; hide it and the evidence lies.

    Two envelopes, two field names: the Earth comparison reports
    ``live_fallback_reason``, the NASA proxy reports ``reason``. Read both, or a
    throttled ``DEMO_KEY`` prints as a bare "provenance bundled" and the reader
    cannot tell a designed degradation from a broken integration.
    """
    reason = payload.get("live_fallback_reason") or payload.get("reason")
    if not reason:
        return ""
    # httpx errors carry a second documentation line; one probe = one line.
    flat = " ".join(str(reason).split())
    return f" · fallback: {flat[:150]}"


def _water(resources: Any) -> Any:
    """`GET …/resources` answers `{"resources": {"water": 88, …}}`."""
    if isinstance(resources, dict):
        return (resources.get("resources") or {}).get("water", "?")
    return "?"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    parser.add_argument("--no-live", action="store_true", help="skip outbound NASA calls")
    parser.add_argument("--json", default="", help="write a machine-readable report here")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
