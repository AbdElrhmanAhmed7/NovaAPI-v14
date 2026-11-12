"""The CI workflow is part of the deliverable – and it silently was not running.

GitHub only reads workflows from ``.github/workflows`` **at the repository
root**. The backend shipped its workflow inside ``NOVA/.github/workflows``, so
every gate in it (ruff, mypy, content validation, the route audit, pytest, the
campaign playthrough) was decoration: no push ever triggered it. The path
filters also still pointed at an older ``nova_v7/`` layout.

It happened a second time, one level up: the delivered tree wrapped everything
in ``repo/``, so the workflow sat at ``repo/.github/workflows`` while the filters
said ``NOVA/backend/**``. These tests now anchor on the **git root** (see
``conftest.repository_root``) instead of the nearest ``.github`` directory –
otherwise the gate happily agrees with whatever wrapper it is sitting in.

These tests keep the workflow discoverable, pointed at the real directory, and
in sync with `make check`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import repository_root

BACKEND = Path(__file__).resolve().parents[1]
MAKEFILE = BACKEND / "Makefile"
README = BACKEND / "README.md"


def _repo_root() -> Path:
    root = repository_root()
    if root is None:
        pytest.skip("backend is vendored outside its repository – no workflow to check")
    return root


@pytest.fixture(scope="module")
def workflow() -> tuple[Path, str]:
    path = _repo_root() / ".github" / "workflows" / "backend.yml"
    assert path.is_file(), f"GitHub will never run {path}"
    return path, path.read_text(encoding="utf-8")


def test_the_workflow_lives_where_github_looks_for_it(workflow: tuple[Path, str]) -> None:
    root = _repo_root()
    # Depth-limited on purpose: a virtualenv holds thousands of directories, and
    # walking it would make this gate slower without making it any sharper.
    nested = [
        path
        for depth in ("*", "*/*", "*/*/*")
        for suffix in ("yml", "yaml")
        for path in root.glob(f"{depth}/.github/workflows/*.{suffix}")
    ]
    assert not nested, f"workflows GitHub ignores (buried below the git root): {nested}"


def test_the_workflow_targets_the_real_backend_directory(workflow: tuple[Path, str]) -> None:
    _path, text = workflow
    relative = BACKEND.relative_to(_repo_root()).as_posix()
    assert f'paths: ["{relative}/**"' in text
    assert f"working-directory: {relative}\n" in text
    assert f"cache-dependency-path: {relative}/requirements-dev.txt" in text
    for artefact in re.findall(r"^\s+path: (\S+)$", text, flags=re.M):
        assert artefact.startswith(relative + "/"), artefact
        assert (BACKEND / artefact[len(relative) + 1 :]).parent.is_dir(), artefact


def test_ci_runs_every_gate_the_makefile_promises(workflow: tuple[Path, str]) -> None:
    _path, text = workflow
    check = re.search(r"^check: ([^#]+)##", MAKEFILE.read_text(encoding="utf-8"), flags=re.M)
    assert check, "make check no longer declares its dependencies"
    commands = {
        "lint": ["ruff check .", "ruff format --check ."],
        "typecheck": ["mypy app scripts"],
        "content": ["scripts/validate_content.py --strict"],
        "audit": ["scripts/audit_routes.py --check"],
        "contract": ["scripts/export_openapi.py --check"],
        "test": ["pytest"],
        "playthrough": ["scripts/playthrough.py"],
    }
    for target in check.group(1).split():
        for command in commands[target]:
            assert command in text, f"CI never runs `make {target}` ({command})"


def test_no_reference_to_the_retired_layout_survives(workflow: tuple[Path, str]) -> None:
    _path, text = workflow
    for name, document in (("workflow", text), ("README", README.read_text(encoding="utf-8"))):
        assert "nova_v7" not in document, f"{name} still points at the old nova_v7/ layout"
