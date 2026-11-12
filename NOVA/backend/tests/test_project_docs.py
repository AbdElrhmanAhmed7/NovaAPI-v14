"""The root documents are part of the deliverable, so they get a gate too.

``docs/openapi.json`` already fails CI when it drifts from the code, and the
route audit fails when a handler loses its guard. The project README and the
status report deserve the same treatment: they are what a new teammate (or a
supervisor) reads first, and a stale mission table or a broken link is a real
defect, not cosmetics.

These tests read the *sources of truth* – the mission content pack and the
exported OpenAPI contract – and assert the root documents still agree with them.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from conftest import repository_root

BACKEND = Path(__file__).resolve().parents[1]
DOCS = ("README.md", "PROJECT_STATUS.md")

# | 2 | Earth Analog | Water Leak Repair | `earth_analog` | 4 | …
ROW = re.compile(r"^\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|\s*`([a-z_]+)`\s*\|\s*(\d+)\s*\|", re.M)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _repo_root() -> Path:
    root = repository_root()
    if root is None:
        pytest.skip("backend is vendored outside its repository – no root documents to check")
    return root


@pytest.fixture(scope="module")
def root() -> Path:
    return _repo_root()


@pytest.fixture(scope="module")
def campaign_facts() -> Counter[tuple[int, str, int]]:
    """(stage, environment, objectives) for every authored mission."""
    facts: Counter[tuple[int, str, int]] = Counter()
    for path in sorted((BACKEND / "content" / "missions").glob("*.json")):
        mission = json.loads(path.read_text(encoding="utf-8"))
        facts[(mission["stage"], mission["environment"], len(mission["objectives"]))] += 1
    assert facts, "the content pack is empty"
    return facts


@pytest.mark.parametrize("name", DOCS)
def test_the_root_document_exists_and_says_something(root: Path, name: str) -> None:
    path = root / name
    assert path.is_file(), f"{name} is missing from the repository root"
    text = path.read_text(encoding="utf-8")
    assert len(text) > 1500, f"{name} is a stub"
    assert "NOVA" in text
    assert "nova_v7" not in text, f"{name} points at the retired layout"


@pytest.mark.parametrize("name", DOCS)
def test_every_local_link_resolves(root: Path, name: str) -> None:
    text = (root / name).read_text(encoding="utf-8")
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        resolved = (root / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"{name} links to a path that does not exist: {target}"


@pytest.mark.parametrize("name", DOCS)
def test_the_campaign_table_matches_the_content_pack(
    root: Path, name: str, campaign_facts: Counter[tuple[int, str, int]]
) -> None:
    text = (root / name).read_text(encoding="utf-8")
    rows = ROW.findall(text)
    documented: Counter[tuple[int, str, int]] = Counter(
        (int(stage), environment, int(objectives))
        for stage, _stage, _title, environment, objectives in rows
    )
    assert documented == campaign_facts, (
        f"{name} campaign table drifted from content/missions – "
        f"missing {campaign_facts - documented}, invented {documented - campaign_facts}"
    )


def test_the_status_report_quotes_the_published_contract(root: Path) -> None:
    spec = json.loads((BACKEND / "docs" / "openapi.json").read_text(encoding="utf-8"))
    text = (root / "PROJECT_STATUS.md").read_text(encoding="utf-8")
    figures = {
        "operations": sum(
            1
            for item in spec["paths"].values()
            for method in item
            if method in {"get", "post", "put", "patch", "delete"}
        ),
        "paths": len(spec["paths"]),
        "schemas": len(spec["components"]["schemas"]),
        "error codes": len(spec["x-error-codes"]),
    }
    for label, value in figures.items():
        assert re.search(rf"\b{value}\b", text), f"PROJECT_STATUS.md never mentions {value} {label}"
    assert spec["info"]["version"] in text, "PROJECT_STATUS.md quotes a different backend version"


def test_the_readme_points_at_the_backend_manual_and_the_contract(root: Path) -> None:
    text = (root / "README.md").read_text(encoding="utf-8")
    for target in ("NOVA/backend/README.md", "NOVA/backend/docs/openapi.json", "PROJECT_STATUS.md"):
        assert target in text, f"README.md never links to {target}"
    assert "make check" in text, "README.md does not tell anyone how to verify the build"
