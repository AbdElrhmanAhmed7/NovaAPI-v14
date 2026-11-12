#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# NOVA – Beyond the Mission | regenerate the delivery artefacts in releases/.
#
#   bash tools/make_release.sh                      # base = origin/main
#   bash tools/make_release.sh --base a645012       # explicit base commit
#   bash tools/make_release.sh --name nova-v4       # artefact base name
#
# Produces (from the *committed* tree, so it never packages a virtualenv,
# a cache, or a local database):
#
#   releases/<name>.patch   git format-patch base..HEAD  → apply with `git am`
#   releases/<name>.zip     git archive HEAD             → full source snapshot
#   releases/SHA256SUMS.txt integrity hashes for both
#
# `releases/` itself is excluded from the zip: an archive that contains the
# previous archive doubles in size every release for no benefit.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BASE="origin/main"
NAME="nova-backend-final"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base) BASE="${2:?--base needs a revision}"; shift ;;
    --name) NAME="${2:?--name needs a value}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if ! git rev-parse --verify --quiet "$BASE" >/dev/null; then
  echo "✗ base revision '$BASE' not found. Pass --base <commit> (e.g. --base HEAD~1)." >&2
  exit 2
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "✗ working tree is dirty – commit first, the release must match a commit." >&2
  git status --short >&2
  exit 2
fi

mkdir -p releases
PATCH="releases/$NAME.patch"
ZIP="releases/$NAME.zip"

echo "── patch: $BASE..HEAD → $PATCH"
# `releases/` is excluded for the same reason as in the zip: a patch that carries
# the previous patch doubles every release – and re-introduces text the project
# has already retired (the pre-translation Arabic delivery, for example).
git format-patch "$BASE"..HEAD --stdout -- . ':(exclude)releases' >"$PATCH"
if [[ ! -s "$PATCH" ]]; then
  echo "  ! no commits between $BASE and HEAD – removing the empty patch"
  rm -f "$PATCH"
fi

echo "── snapshot: HEAD → $ZIP"
git archive --format=zip --prefix=NOVA/ -o "$ZIP" HEAD -- . ':(exclude)releases'

echo "── hashes → releases/SHA256SUMS.txt"
python3 - "$PATCH" "$ZIP" <<'PY'
import hashlib
import pathlib
import sys

lines = []
for name in sys.argv[1:]:
    path = pathlib.Path(name)
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
        print(f"  {path.name}: {path.stat().st_size / 1024:.0f} KB")
pathlib.Path("releases/SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

echo ""
echo "✓ release written. Apply the patch on a clean clone with:"
echo "    git am $PATCH"
echo ""
echo "⚠ the zip wraps the tree in a NOVA/ folder (git archive --prefix)."
echo "  If you upload through the GitHub UI: cd INTO that folder and drag its"
echo "  contents, never the folder itself – a wrapper puts .github/workflows below"
echo "  the git root and GitHub then runs no CI at all."
echo "  Check after any upload:  bash tools/check_layout.sh   # ✓ layout ok"
