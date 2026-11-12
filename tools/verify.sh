#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# NOVA – Beyond the Mission | run every quality gate, from the repository root.
#
# `cd NOVA/backend && make check` is the canonical entry point. This script runs
# the *same* commands for environments without `make` (the verification sandbox
# had none), and it tees everything into a dated log so a run is evidence.
#
#   bash tools/verify.sh                 # all gates, log under verification/
#   bash tools/verify.sh --fast          # skip pytest + campaign + probes (seconds)
#   bash tools/verify.sh --live          # probes may reach NASA (needs network)
#   bash tools/verify.sh --label my-run  # verification/my-run/gates.log
#
# The gates are `make check` plus one extra: the socket probes, which boot a real
# uvicorn and prove the console, the write guard and the WebSocket resume. They
# run offline by default so this script never depends on NASA being reachable.
#
# Exit status is non-zero if any gate fails, and the failing gate is named in
# the summary – no more scrolling 400 lines to find out what broke.
# ---------------------------------------------------------------------------
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/NOVA/backend"
FAST=0
LIVE=0
LABEL="$(date -u +%Y-%m-%d)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fast) FAST=1 ;;
    --live) LIVE=1 ;;
    --label) LABEL="${2:?--label needs a value}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# Gate 0: the layout guard needs neither a virtualenv nor a network, so it runs
# first and fails in milliseconds. A wrapped tree makes every later gate lie.
if ! bash "$ROOT/tools/check_layout.sh"; then
  echo "✗ repository layout is wrong – run tools/restructure_repo.sh first" >&2
  exit 2
fi

if [[ ! -x "$BACKEND/.venv/bin/python" ]]; then
  echo "✗ $BACKEND/.venv is missing – run: cd NOVA/backend && make install" >&2
  exit 2
fi
BIN="$BACKEND/.venv/bin"
OUT="$ROOT/verification/$LABEL"
mkdir -p "$OUT"
LOG="$OUT/gates.log"

declare -a NAMES=() RESULTS=()
cd "$BACKEND"

gate() { # gate <name> <command...>
  local name="$1"; shift
  {
    echo ""
    echo "════════ $name ════════"
    echo "\$ $*"
  } >>"$LOG"
  if "$@" >>"$LOG" 2>&1; then
    NAMES+=("$name"); RESULTS+=("PASS")
    printf '  ✅ %s\n' "$name"
  else
    local code=$?
    NAMES+=("$name"); RESULTS+=("FAIL($code)")
    printf '  ❌ %s (exit %s) – see %s\n' "$name" "$code" "$LOG"
  fi
}

{
  echo "### NOVA verification · $(date -u +%FT%TZ)"
  echo "root: $ROOT"
  echo "python: $("$BIN/python" -V 2>&1)"
  "$BIN/pip" freeze 2>/dev/null | grep -iE '^(fastapi|pydantic|sqlalchemy|uvicorn|httpx|alembic|aiosqlite|pytest|ruff|mypy)=' || true
} >"$LOG"

echo "── NOVA gates → $LOG"
gate "ruff check"      "$BIN/ruff" check .
gate "ruff format"     "$BIN/ruff" format --check .
gate "mypy"            "$BIN/mypy" app scripts
gate "content"         "$BIN/python" scripts/validate_content.py --strict
gate "route audit"     "$BIN/python" scripts/audit_routes.py --check
gate "openapi"         "$BIN/python" scripts/export_openapi.py --check
if [[ "$FAST" == 0 ]]; then
  gate "pytest"        "$BIN/pytest" --cov=app --cov-report=term-missing
  gate "campaign"      "$BIN/python" scripts/playthrough.py --in-process \
                       --difficulty standard --seed 7 --json "$OUT/campaign.json"
  gate "player text"   "$BIN/python" scripts/audit_player_text.py --quiet \
                       --json "$OUT/player-text.json" --markdown "$OUT/player-text.md"
  PROBES_ARGS=(--json "$OUT/live-probes.json")
  [[ "$LIVE" == 1 ]] || PROBES_ARGS+=(--no-live)
  gate "socket probes" "$BIN/python" scripts/live_probes.py "${PROBES_ARGS[@]}"
fi

echo ""
echo "── summary ──"
FAILED=0
for i in "${!NAMES[@]}"; do
  printf '  %-14s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}"
  [[ "${RESULTS[$i]}" == PASS ]] || FAILED=1
done
echo "log: $LOG"
if [[ "$FAILED" == 0 ]]; then
  echo "✓ all gates passed"
else
  echo "✗ at least one gate failed"
fi
exit "$FAILED"
