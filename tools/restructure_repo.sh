#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# NOVA – Beyond the Mission | one-shot repository layout migration.
#
# WHY THIS EXISTS
#   GitHub only reads workflows from `<git root>/.github/workflows`, and this
#   workflow's own filters point at `NOVA/backend/**`. Twice the project was
#   pushed inside a wrapper directory, so both were one level too deep and
#   **CI never ran a single gate** on the pushed tree:
#
#       <git root>/repo/.github/workflows/backend.yml       (delivery #1)
#       <git root>/upstream/.github/workflows/backend.yml   (delivery #2)
#
#   The backend suite could not catch the first one either: it discovered the
#   "repository root" by looking for the nearest `.github/workflows`, which
#   found the wrapper instead of the real git root. It now anchors on `.git`
#   (see tests/conftest.py::repository_root), so the wrapper fails 12 tests
#   instead of passing quietly – which is exactly how delivery #2 was caught.
#
#   This script promotes the project to the git root whatever the wrapper is
#   called. Every move is a `git mv`, so history follows the files.
#
# USAGE
#   bash tools/restructure_repo.sh          # apply (idempotent)
#   bash tools/restructure_repo.sh --dry-run
#
# Safe to re-run: once the project sits at the root it reports "already
# migrated". It refuses to guess when two wrappers could both be the project.
# ---------------------------------------------------------------------------
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

say() { printf '%s\n' "$*"; }
run() {
  if [[ "$DRY_RUN" == 1 ]]; then
    say "  [dry-run] $*"
  else
    say "  $*"
    "$@"
  fi
}

if [[ -d NOVA/backend ]]; then
  say "✓ already migrated – the project is at the git root ($ROOT)"
  exit 0
fi

# Find the wrapper: an immediate subdirectory holding NOVA/backend. The name is
# not hardcoded – `repo/`, `upstream/`, `NOVA-main/` are all the same mistake.
CANDIDATES=()
for dir in */; do
  dir="${dir%/}"
  [[ "$dir" == ".git" ]] && continue
  [[ -d "$dir/NOVA/backend" ]] && CANDIDATES+=("$dir")
done

if [[ "${#CANDIDATES[@]}" == 0 ]]; then
  say "✗ no directory holding NOVA/backend found under $ROOT – nothing to migrate." >&2
  exit 1
fi
if [[ "${#CANDIDATES[@]}" -gt 1 ]]; then
  say "✗ more than one project wrapper: ${CANDIDATES[*]} – refusing to guess." >&2
  exit 1
fi
WRAPPER="${CANDIDATES[0]}"
say "── wrapper detected: $WRAPPER/ ─────────────────────────────────────────"

say "── 1. promote the project to the git root ─────────────────────────────"
# Everything the wrapper holds belongs at the root; enumerate it instead of
# listing names, so a new top-level file is never silently left behind.
shopt -s dotglob nullglob
for path in "$WRAPPER"/*; do
  entry="$(basename "$path")"
  if [[ -e "$entry" ]]; then
    say "  ! $entry already exists at the root – leaving $WRAPPER/$entry alone"
  else
    run git mv "$WRAPPER/$entry" "$entry"
  fi
done
shopt -u dotglob nullglob
# Anything left over is unexpected: report it instead of deleting it.
if [[ -d "$WRAPPER" ]] && [[ -n "$(ls -A "$WRAPPER" 2>/dev/null || true)" ]]; then
  say "  ! $WRAPPER/ still holds: $(ls -A "$WRAPPER" | tr '\n' ' ')"
else
  run rmdir "$WRAPPER"
fi

say "── 2. delivery artefacts → releases/ ──────────────────────────────────"
run mkdir -p releases
# The previous session's patch is already merged in main, and its zip is
# superseded by the snapshot regenerated after this migration. Both stay
# recoverable from git history, so they are removed rather than duplicated.
for stale in nova-backend-finish.patch NOVA-backend-final.zip; do
  if [[ -f "$stale" ]]; then
    run git rm --quiet "$stale"
  fi
done

say "── 3. verification evidence → verification/ ───────────────────────────"
run mkdir -p verification/archive
for log in gates.log gates-final.log post-commit.log campaign.json; do
  if [[ -f "verification/$log" ]]; then
    run git mv "verification/$log" "verification/archive/$log"
  fi
done

say "── 4. drop the upload scratch file ────────────────────────────────────"
# uploads/ held a single .txt of copied GitHub chrome ("You signed in with
# another tab…"), not project content.
if [[ -d uploads ]]; then
  run git rm -r --quiet uploads
fi

say ""
say "✓ layout migration complete. Next:"
say "    cd NOVA/backend && make install     # a moved .venv has stale absolute paths"
say "    bash tools/verify.sh                # every gate, on the new layout"
say "    git commit -m 'chore: promote project to repo root, fix CI discovery'"
say "    git push origin main"
