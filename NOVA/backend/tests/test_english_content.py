"""English-only content contract.

The game ships English player-facing text. The legacy ``ar`` locale field is kept
as a compatibility alias, but it must contain the same English string as ``en``.
These gates prevent Arabic text from drifting back into content, API payloads,
the console, or the repository documentation.

The last two gates are the ones that matter for a player: they run the *whole*
public surface (all 67 operations, the failure paths, the legacy envelopes and
the WebSocket feed) and read every string that came back. Documentation is not
what a player sees – responses are.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.missions.registry import GameContent
from audit_player_text import Sample, analyse, run_audit
from conftest import Api, repository_root

BACKEND = Path(__file__).resolve().parents[1]
ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
TEXT_SUFFIXES = {
    ".md",
    ".json",
    ".py",
    ".html",
    ".sh",
    ".yml",
    ".yaml",
    ".log",
    ".txt",
    ".ini",
    ".toml",
    ".mako",
}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


def _assert_english_tree(value: Any, where: str) -> None:
    if isinstance(value, str):
        assert not ARABIC.search(value), f"Arabic text found at {where}: {value!r}"
    elif isinstance(value, dict):
        if "en" in value and "ar" in value:
            assert value["ar"] == value["en"], f"Locale alias drifted at {where}"
        for key, child in value.items():
            _assert_english_tree(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_english_tree(child, f"{where}[{index}]")


def test_content_pack_is_english_and_aliases_match(content: GameContent) -> None:
    files = sorted((BACKEND / "content").rglob("*.json"))
    assert files, "the content pack is empty"
    for path in files:
        _assert_english_tree(json.loads(path.read_text(encoding="utf-8")), str(path))


def test_public_api_payloads_are_english(client: TestClient, api: Api) -> None:
    api.start("training_prep")
    payloads = [
        client.get("/api/v1/content/catalogue").json(),
        client.get("/api/v1/content/controls").json(),
        client.get("/api/v1/earth/catalogue").json(),
        client.get("/api/v1/science/reference").json(),
        client.post(
            "/api/v1/earth/compare",
            json={
                "region": "nile_delta",
                "layer": "ndvi",
                "period_before": "2001-2005",
                "period_after": "2021-2025",
                "grid_size": 4,
            },
        ).json(),
        api.get("").json(),
        api.get("/continuity").json(),
        api.get("/missions/training_prep/interactions").json(),
    ]
    for index, payload in enumerate(payloads):
        _assert_english_tree(payload, f"payload[{index}]")


def test_repository_sources_and_docs_are_english() -> None:
    root = repository_root()
    if root is None:
        pytest.skip("backend is vendored outside its repository – no root documents to check")
    violations: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        # Generated delivery diffs are checked after regeneration, not as source text.
        if path.parent.name == "releases" and path.suffix in {".patch", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if ARABIC.search(line):
                violations.append(f"{path.relative_to(root)}:{line_number}")
    assert not violations, "Arabic text found in:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# The runtime gate: what the API actually says to a player
# ---------------------------------------------------------------------------
def test_the_text_auditor_really_detects_non_english() -> None:
    """A gate that always says "ok" is worse than no gate at all.

    These payloads never reach a client; they prove the auditor would fail on
    Arabic prose, on any other non-Latin script, and on a locale alias that
    drifted away from its English source.

    The samples are written as escape sequences on purpose: the repository-wide
    gate above scans source files for literal Arabic, and a test fixture must
    not be the reason it fails.
    """
    arabic = analyse(
        [
            Sample(
                "unit",
                "GET",
                "/x",
                200,
                {"message": "\u0623\u063a\u0644\u0642 \u0627\u0644\u0635\u0645\u0627\u0645"},
            )
        ]
    )
    assert [v["kind"] for v in arabic["violations"]] == ["arabic"]

    other_script = analyse([Sample("unit", "GET", "/x", 200, {"hint": "\u5173\u95ed\u9600\u95e8"})])
    assert [v["kind"] for v in other_script["violations"]] == ["non-latin"]

    drifted = analyse(
        [
            Sample(
                "unit",
                "GET",
                "/x",
                200,
                {"title": {"en": "Valve", "ar": "\u0635\u0645\u0627\u0645"}},
            )
        ]
    )
    assert {v["kind"] for v in drifted["violations"]} == {"arabic", "locale-alias"}

    clean = analyse([Sample("unit", "GET", "/x", 200, {"title": {"en": "Valve", "ar": "Valve"}})])
    assert clean["violations"] == []


def test_every_player_facing_api_string_is_english(client: TestClient) -> None:
    """Play the campaign, break the rules, tail the socket – then read everything.

    Covers mission text, hints, action results, hazard and failure messages,
    achievements, science narratives, Earth reports, simulator notes, the legacy
    Prototype-01 envelopes and the WebSocket frames. ``docs/`` is deliberately
    out of scope: a player never reads it.
    """
    report = run_audit(client, openapi=client.app.openapi())

    assert not report["violations"], "non-English player text:\n" + "\n".join(
        f"{v['where']} {v['field']}: {v['text']}" for v in report["violations"][:20]
    )
    assert report["operations_missed"] == [], (
        "new operations are not exercised by scripts/audit_player_text.py – add a probe: "
        + ", ".join(report["operations_missed"])
    )
    # The interesting halves must not silently disappear from the transcript.
    assert report["sources"].get("websocket", 0) > 0, "the WebSocket feed was not captured"
    assert report["sources"].get("legacy", 0) > 0, "the legacy routes were not captured"
    assert len(report["error_responses"]) >= 20, "the failure paths were not exercised"
    assert report["distinct_player_strings"] > 300, "suspiciously little player text was read"
