#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# NOVA – Beyond the Mission | repository layout guard (no dependencies).
#
# WHY THIS EXISTS
#   GitHub only reads workflows from `<git root>/.github/workflows`. Three
#   deliveries in a row were pushed with the whole project inside a wrapper
#   directory (`NOVA/`, then `repo/`, then `upstream/`, then `repo/` again), so
#   the workflow was one level too deep and **CI never ran a single gate**:
#
#       <git root>/repo/.github/workflows/backend.yml   ← GitHub ignores this
#
#   The pytest suite catches it (tests/test_ci_workflow.py anchors on `.git`),
#   but that needs a virtualenv and ~3 minutes. This guard needs only bash+git
#   and finishes in milliseconds, so it can run *before* anything is installed
#   and before every push.
#
# USAGE
#   bash tools/check_layout.sh            # exit 0 = GitHub will see the workflow
#   bash tools/check_layout.sh --quiet    # only print on failure
#
# FIX (if it fails)
#   bash tools/restructure_repo.sh        # promotes the project, via `git mv`
# ---------------------------------------------------------------------------
set -uo pipefail

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

say() { [[ "$QUIET" == 1 ]] || printf '%s\n' "$*"; }
fail() {
  printf '✗ %s\n' "$*" >&2
  printf '  Fix: bash tools/restructure_repo.sh\n' >&2
  exit 1
}

if ! ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  # A vendored copy (zip download, no .git) cannot be checked for CI discovery.
  say "· no git metadata – layout guard skipped (not a clone)"
  exit 0
fi
cd "$ROOT"

# 1. The project must sit at the git root, not one level below it.
[[ -d NOVA/backend ]] || fail "NOVA/backend is not at the git root ($ROOT)"

# 2. The workflow must be exactly where GitHub looks for it.
WORKFLOW=".github/workflows/backend.yml"
[[ -f "$WORKFLOW" ]] || fail "missing $WORKFLOW – GitHub will never run CI"

# 3. No wrapper: an immediate subdirectory holding a second copy of the project.
for dir in */; do
  dir="${dir%/}"
  [[ "$dir" == ".git" || "$dir" == "NOVA" ]] && continue
  if [[ -d "$dir/NOVA/backend" ]]; then
    fail "wrapper detected: $dir/NOVA/backend – the project is nested twice"
  fi
done

# 4. No workflow buried below the root, where GitHub silently ignores it.
#    Depth-limited: a virtualenv holds thousands of directories.
BURIED=""
for depth in "*" "*/*" "*/*/*"; do
  for suffix in yml yaml; do
    for path in $depth/.github/workflows/*.$suffix; do
      [[ -f "$path" ]] && BURIED="$BURIED $path"
    done
  done
done
[[ -z "$BURIED" ]] || fail "workflows GitHub ignores (below the git root):$BURIED"

# 5. The workflow's own filters must match the real directory, or it triggers
#    on nothing even when it is discovered.
grep -q 'paths: \["NOVA/backend/\*\*"' "$WORKFLOW" ||
  fail "$WORKFLOW path filters do not match NOVA/backend/**"
grep -q 'working-directory: NOVA/backend' "$WORKFLOW" ||
  fail "$WORKFLOW working-directory is not NOVA/backend"

say "✓ layout ok – git root = project root, GitHub will run .github/workflows/backend.yml"
exit 0
