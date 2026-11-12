"""The layout guard: the cheapest gate in the project, and the most violated.

Three deliveries in a row were pushed with the whole project inside a wrapper
directory, so ``.github/workflows/backend.yml`` sat below the git root and
GitHub never ran a single job. ``test_ci_workflow.py`` catches that – but it
needs the repository to *be* the checkout, and it runs inside a 3-minute suite.

``tools/check_layout.sh`` exists to answer the same question in milliseconds
with nothing but bash and git, so it can run before ``make install`` and before
every push. These tests plant the historical mistakes in throwaway git repos and
assert the guard actually rejects them: a guard that only ever prints "ok" is
worse than no guard, because it is trusted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import repository_root

BACKEND = Path(__file__).resolve().parents[1]
GUARD = "tools/check_layout.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _repo_root() -> Path:
    root = repository_root()
    if root is None:
        pytest.skip("backend is vendored outside its repository – no layout to guard")
    return root


@pytest.fixture(scope="module")
def root() -> Path:
    return _repo_root()


def test_the_guard_ships_with_the_repository(root: Path) -> None:
    assert (root / GUARD).is_file(), f"{GUARD} is missing"


def test_verify_runs_the_guard_before_it_needs_a_virtualenv(root: Path) -> None:
    """Order matters: a wrapped tree makes every later gate agree with the wrapper."""
    text = (root / "tools" / "verify.sh").read_text(encoding="utf-8")
    assert GUARD in text, "tools/verify.sh never calls the layout guard"
    assert text.index(GUARD) < text.index(".venv/bin/python"), (
        "the layout guard must run before the virtualenv check – it needs no install"
    )


def _run(cwd: Path, script: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        env=None if not env else {**os.environ, **env},
    )


def _plant(tmp_path: Path, *, wrapper: str | None = None) -> tuple[Path, Path]:
    """Build a minimal git repo shaped like a delivery, and copy the real guard in."""
    project = tmp_path if wrapper is None else tmp_path / wrapper
    workflow = project / ".github" / "workflows" / "backend.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_repo_root() / ".github" / "workflows" / "backend.yml", workflow)
    (project / "NOVA" / "backend").mkdir(parents=True, exist_ok=True)
    guard = tmp_path / GUARD
    guard.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_repo_root() / GUARD, guard)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path, guard


def test_a_correct_layout_passes(tmp_path: Path) -> None:
    repo, guard = _plant(tmp_path)
    result = _run(repo, guard)
    assert result.returncode == 0, result.stderr
    assert "layout ok" in result.stdout


@pytest.mark.parametrize("wrapper", ["repo", "upstream", "NOVA-main"])
def test_every_historical_wrapper_is_rejected(tmp_path: Path, wrapper: str) -> None:
    """The guard must not hardcode one wrapper name – that is how it recurs."""
    repo, guard = _plant(tmp_path, wrapper=wrapper)
    result = _run(repo, guard)
    assert result.returncode == 1, f"{wrapper}/ was accepted: {result.stdout}"
    assert "restructure_repo.sh" in result.stderr, "the failure must name the fix"


def test_a_second_copy_beside_the_root_project_is_rejected(tmp_path: Path) -> None:
    repo, guard = _plant(tmp_path)
    (repo / "repo" / "NOVA" / "backend").mkdir(parents=True)
    result = _run(repo, guard)
    assert result.returncode == 1
    assert "wrapper detected" in result.stderr


def test_a_buried_workflow_is_rejected(tmp_path: Path) -> None:
    """Correct root project, but a second workflow GitHub would silently ignore."""
    repo, guard = _plant(tmp_path)
    buried = repo / "nested" / ".github" / "workflows" / "backend.yml"
    buried.parent.mkdir(parents=True)
    buried.write_text("name: ignored\n", encoding="utf-8")
    result = _run(repo, guard)
    assert result.returncode == 1
    assert "below the git root" in result.stderr


def test_a_vendored_copy_without_git_metadata_is_skipped(tmp_path: Path) -> None:
    """A zip download has no git root, so CI discovery is not a question yet."""
    guard = tmp_path / GUARD
    guard.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_repo_root() / GUARD, guard)
    result = _run(tmp_path, guard, GIT_CEILING_DIRECTORIES=str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "skipped" in result.stdout
