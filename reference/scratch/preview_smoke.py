"""Scratch harness: smoke-test the server a human actually opens in a browser.

``scripts/live_probes.py`` boots its *own* uvicorn on a free port with a throwaway
database. That proves the app works; it does not prove that the **preview server
the user is looking at** works. This harness points at a running base URL and
checks the things a player notices first:

* ``/api/v1/ready`` reports the database and the content pack,
* ``/`` serves the Mission Control console with **no external assets** (it must
  render inside a sandboxed preview iframe with no network),
* a campaign can be created and the prerequisite missions played over HTTP,
* the canonical water-leak rule is enforced by the server: repairing **before**
  closing the isolation valve is refused with ``409 precondition_failed``,
* the correct order (scan → close valve → repair → verify) completes the mission,
* the event log recorded what happened (the WebSocket read model).

Usage::

    cd NOVA/backend
    NOVA_DATABASE_URL="sqlite+aiosqlite:///./data/nova-preview.db" \
        .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
    .venv/bin/python ../../reference/scratch/preview_smoke.py \
        --base-url http://127.0.0.1:8000

Exit status is non-zero if any check fails – including the guard *not* rejecting
the premature repair, which is the whole point of the mission.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent  # reference/scratch
ROOT = HERE.parents[1] / "NOVA" / "backend"
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402

from autopilot import Autopilot  # noqa: E402

PREFIX = "/api/v1"
LEAK = "analog_water_leak"
PREREQUISITES = ("training_prep", "analog_systems_check")
ORDER = ("scan_leak", "close_valve", "repair_pipe", "verify_pressure")
EXTERNAL_ASSET = re.compile(r'(?:src|href)\s*=\s*["\']https?://', re.I)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    failures: list[str] = []
    print(f"── NOVA live preview smoke test · {args.base_url} (the server the user opens)")

    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        ready = client.get(f"{PREFIX}/ready")
        body: dict[str, Any] = ready.json() if ready.status_code == 200 else {}
        print(
            f"ready          {ready.status_code} · status {body.get('status')} · "
            f"db {body.get('database')} · {body.get('missions_loaded')} missions · "
            f"{body.get('items_loaded')} items"
        )
        if ready.status_code != 200 or body.get("status") != "ready":
            failures.append(f"readiness: {ready.status_code} {ready.text[:200]}")

        console = client.get("/")
        external = len(EXTERNAL_ASSET.findall(console.text))
        print(
            f"console        {console.status_code} · {len(console.content):,} bytes · "
            f"external assets {external}"
        )
        if console.status_code != 200 or external:
            failures.append(f"console: {console.status_code}, {external} external assets")

        pilot = Autopilot(client=client)
        pilot.create_game("Preview smoke", difficulty="standard", seed=args.seed)
        game = pilot.game_id
        headers = pilot.headers()
        print(f"game created   201 · {game}")

        for mission_id in PREREQUISITES:
            outcome = pilot.play_mission(mission_id)
            print(
                f"{mission_id:<14} 200 · {outcome.steps} steps · "
                f"score {outcome.score_pct} · grade {outcome.grade}"
            )

        base = f"{PREFIX}/games/{game}/missions/{LEAK}"
        started = client.post(f"{base}/start", json={}, headers=headers)
        if started.status_code >= 400:
            failures.append(f"start {LEAK}: {started.status_code} {started.text[:200]}")
            print(f"✗ could not start {LEAK}: {started.status_code}")
            return _summary(failures)

        premature = client.post(
            f"{base}/actions", json={"action": "repair_pipe", "payload": {}}, headers=headers
        )
        error = premature.json().get("error", {})
        print(
            f"\n{'✗' if premature.status_code == 409 else '!'} repair BEFORE closing the valve "
            f"→ HTTP {premature.status_code} {error.get('code')}"
        )
        print(f'  "{error.get("message")}"')
        view = client.get(base).json()
        repair = next(o for o in view["objectives"] if o["id"] == "repair_pipe")
        print(f"  unmet: {[item['id'] for item in repair['unmet']]}")
        resources = client.get(f"{PREFIX}/games/{game}/resources").json()
        print(f"  resources now: {json.dumps(resources)[:150]}")
        if premature.status_code != 409 or error.get("code") != "precondition_failed":
            failures.append(f"guard did not reject the premature repair: {premature.status_code}")

        print("\n✓ correct order (scan → close valve → repair → verify):")
        for action in ORDER:
            response = client.post(
                f"{base}/actions", json={"action": action, "payload": {}}, headers=headers
            )
            payload = response.json()
            message = payload.get("message", payload.get("error", {}).get("message", ""))
            print(f"  {action:<16} → {response.status_code} · {message}")
            if response.status_code >= 400:
                failures.append(f"{action}: {response.status_code} {message}")

        report = client.get(f"{base}/report").json()
        print(
            f"  report: score {report.get('score_pct')} · grade {report.get('grade')} · "
            f"mistakes {report.get('mistakes')}"
        )
        if report.get("mistakes") != 1:
            failures.append(f"expected exactly 1 recorded mistake, got {report.get('mistakes')}")
        after = client.get(f"{PREFIX}/games/{game}/resources").json()
        print(f"  resources after: {json.dumps(after)[:150]}")

        events = client.get(f"{PREFIX}/games/{game}/events").json()
        count = len(events.get("events", []))
        print(f"\nevents logged  200 · {count} events · next_seq {events.get('next_seq')}")
        if count == 0:
            failures.append("event log is empty")

    return _summary(failures)


def _summary(failures: list[str]) -> int:
    print("")
    if failures:
        for failure in failures:
            print(f"✗ {failure}")
        print(f"✗ preview smoke: {len(failures)} failure(s)")
        return 1
    print("✓ preview smoke: the running server plays the game and enforces the rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
