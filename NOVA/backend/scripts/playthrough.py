#!/usr/bin/env python3
"""Reference CLI: plays the entire NOVA campaign through the public HTTP API.

This is the executable entry point for :mod:`autopilot`. It uses *only* public
endpoints, so a clean run proves the API is sufficient for a real 3D client.

Examples
--------
Play in-process (no server needed, temporary database)::

    python scripts/playthrough.py --in-process --difficulty standard --seed 7

Play against a running server::

    uvicorn app.main:app --port 8000
    python scripts/playthrough.py --base-url http://127.0.0.1:8000

Write a machine-readable campaign report (used by CI)::

    python scripts/playthrough.py --in-process --json build/campaign.json

Exit codes: ``0`` every mission completed, ``1`` a mission could not be
finished (a real backend or content bug), ``2`` bad CLI usage / connection.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from autopilot import Autopilot, PlaythroughError  # noqa: E402

BANNER = "NOVA – Beyond the Mission · Explore. Experience. Execute."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="playthrough",
        description="Autoplay the NOVA campaign end to end through the public API.",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--in-process",
        action="store_true",
        help="Build the app in this process (no server, temporary SQLite file).",
    )
    target.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL of a running backend (default: %(default)s).",
    )
    parser.add_argument("--player", default="Autopilot", help="Player name for the save.")
    parser.add_argument(
        "--difficulty",
        choices=("cadet", "standard", "veteran"),
        default="standard",
        help="Difficulty preset (default: %(default)s).",
    )
    parser.add_argument(
        "--seed", type=int, default=7, help="Deterministic seed (default: %(default)s)."
    )
    parser.add_argument("--prefix", default="/api/v1", help="API prefix (default: %(default)s).")
    parser.add_argument(
        "--json", dest="json_out", type=Path, help="Write the campaign report as JSON."
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the final summary.")
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Fail if any mission scores below this percentage (CI gate).",
    )
    return parser


@contextmanager
def in_process_client() -> Iterator[Any]:
    """Boot the real app against a throwaway database."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    with tempfile.TemporaryDirectory(prefix="nova-play-") as tmp:
        settings = Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{Path(tmp) / 'playthrough.db'}",
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


def summarise(report: dict[str, Any], min_score: float) -> int:
    outcomes = report.get("autopilot", [])
    print("\n" + "─" * 74)
    print(f"{'MISSION':<28}{'SCORE':>8}{'GRADE':>7}{'MISTAKES':>10}{'STEPS':>7}")
    print("─" * 74)
    worst = 100.0
    for outcome in outcomes:
        worst = min(worst, float(outcome["score_pct"]))
        print(
            f"{outcome['mission_id']:<28}{outcome['score_pct']:>7.1f}%"
            f"{outcome['grade']:>7}{outcome['mistakes']:>10}{outcome['steps']:>7}"
        )
    print("─" * 74)
    print(
        f"Missions completed : {report.get('missions_completed')} / {report.get('missions_total')}"
    )
    print(f"Readiness          : {report.get('readiness')} ({report.get('readiness_grade')})")
    print(f"Average score      : {report.get('average_score_pct')}%")
    print(f"Playtime           : {report.get('total_playtime_seconds')} s")
    stats = report.get("stats", {})
    print(
        f"Actions            : {stats.get('successful_actions')} ok / "
        f"{stats.get('mistakes')} mistakes"
    )
    if worst < min_score:
        print(f"\n✗ a mission scored {worst:.1f}% (< --min-score {min_score:.1f}%)")
        return 1
    if report.get("missions_completed") != report.get("missions_total"):
        print("\n✗ campaign incomplete – some missions never finished")
        return 1
    print("\n✓ campaign completed")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.quiet:
        print(BANNER)

    factory = in_process_client if args.in_process else lambda: http_client(args.base_url)
    try:
        with factory() as client:
            pilot = Autopilot(client, prefix=args.prefix, verbose=not args.quiet)
            report = pilot.run_campaign(
                player_name=args.player, difficulty=args.difficulty, seed=args.seed
            )
    except PlaythroughError as exc:
        print(f"\n✗ playthrough failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # connection refused, import error, …
        print(f"\n✗ could not run the playthrough: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        if not args.quiet:
            print(f"\n→ report written to {args.json_out}")

    return summarise(report, args.min_score)


if __name__ == "__main__":
    raise SystemExit(main())
