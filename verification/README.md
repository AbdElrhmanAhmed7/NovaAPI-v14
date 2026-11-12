# verification · Run evidence

Every file here is **the output of a command that was actually executed**, not a
description. No code in `NOVA/backend/app/` reads these files — they are evidence for
humans (and for academic review).

## Files

The current delivery evidence is **`2026-11-12-final/`** — the run after the fifth
`repo/` wrapper was removed (§9.6 of `PROJECT_STATUS.md`). Earlier runs of the same
date are kept unchanged for traceability.

| Path | What it is | How to regenerate it |
| --- | --- | --- |
| `2026-11-12-final/gates.log` | **Current run.** 10 gates: layout · ruff · mypy · content · route audit · contract · pytest with coverage (451 passed, 94%) · campaign · player text · socket probes | `bash tools/verify.sh --label 2026-11-12-final` |
| `2026-11-12-final/campaign.json` | Full campaign report: 15/15 missions, 93 actions, 0 mistakes, readiness 100 (S) | Same command above |
| `2026-11-12-final/player-text.json` / `.md` | Player-facing text audit, in-process: 996 responses · 56,453 strings · 67/67 operations · 0 violations | Same command above (or `make text`) |
| `2026-11-12-final/player-text-live.json` / `.md` / `.log` | The same audit against the **running preview server**: 996 responses · 56,578 strings · 67/67 operations | `cd NOVA/backend && .venv/bin/python scripts/audit_player_text.py --base-url http://127.0.0.1:8000 --json … --markdown …` |
| `2026-11-12-final/live-probes.json` | Offline socket probes (12 probes, 0 failures) | `bash tools/verify.sh --label 2026-11-12-final` |
| `2026-11-12-final/live-probes-live.json` / `.log` | Probes with real NASA: 14 probes, 0 failures · POWER `live` · POWER `cache` · APOD `bundled` (NASA returned 500) | `cd NOVA/backend && .venv/bin/python scripts/live_probes.py --json …` |
| `2026-11-12-final/preview-smoke.log` | The server the user opens (port 8000): readiness, console with 0 external assets, repair-before-valve refused with 409, correct order completes, 27 events | `cd NOVA/backend && .venv/bin/python ../../reference/scratch/preview_smoke.py --base-url http://127.0.0.1:8000` |
| `2026-11-12/gates.log` | Earlier run: all gates, ruff · mypy · content · route audit · contract · pytest with coverage · campaign · real socket probes | `bash tools/verify.sh --label 2026-11-12` |
| `2026-11-12/campaign.json` | Full campaign report (15 missions) in JSON: scores, mistakes, resources, readiness | Same command above (or `scripts/playthrough.py --json …`) |
| `2026-11-12/live-probes.log` | Probes against a running server: HTTP + WebSocket + live NASA data (human-readable text) | `cd NOVA/backend && make probes` |
| `2026-11-12/live-probes.json` | The same probes in JSON (each probe has `status` and `detail`) | `make probes` inside `tools/verify.sh`, or `python scripts/live_probes.py --json …` |
| `2026-11-12/make-check.log` | `make check` literally — the same CI entry point (ruff · mypy · content · audit · contract · pytest · campaign) and exit 0 | `cd NOVA/backend && make check 2>&1 \| tee ../../verification/2026-11-12/make-check.log` |
| `2026-11-12/preview-smoke.log` | Manual play against the server the user actually opens (port 8000): the leak failed before the valve and succeeded after it | Superseded by the reproducible harness — see `2026-11-12-final/preview-smoke.log` |
| `2026-11-12/english-gates.log` | English localization pass: all quality gates, 449 tests with coverage, and the full campaign | Run the commands listed in `PROJECT_STATUS.md` §9.4 |
| `2026-11-12/english-campaign.json` | Campaign report from the English localization pass | `scripts/playthrough.py --in-process --json …` |
| `2026-11-12/english-probes.log` / `.json` | Offline socket probes from the English localization pass (12 probes, 0 failures) | `scripts/live_probes.py --no-live --json …` |
| `archive/2026-11-12-run1/` | The previous session's run (before the third `repo/` wrapper fix) — preserved for traceability | — |
| `archive/gates.log`, `archive/gates-final.log`, `archive/post-commit.log` | Older session runs — preserved for traceability | — |
| `archive/campaign.json` | Campaign report from an older run | — |

> **Localization note:** archived campaign reports keep their original run facts
> (scores, resources, timestamps); only the narrative locale fields were converted to
> English aliases.

## How to re-run the whole verification yourself

```bash
bash tools/check_layout.sh          # first: 0.1 seconds, no install
cd NOVA/backend && make install     # or python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cd ../.. && bash tools/verify.sh    # all gates, writes a dated log
```

* `tools/check_layout.sh` — the layout gate: verifies that **git root = project root**
  and that `.github/workflows/backend.yml` is where GitHub reads it. It needs only
  bash + git, so it works before `make install`. `tools/verify.sh` calls it as the first
  gate, and if it fails it stops immediately and tells you to run
  `tools/restructure_repo.sh`.

* `tools/verify.sh --fast` — only the fast gates (without pytest, campaign and probes)
  if you want to verify formatting, types, content and the contract in seconds.
* `tools/verify.sh --live` — the probes actually talk to NASA (needs network). Without
  `--live` the probes run offline and use the bundled copy, so verification does not fail
  if the network drops or `DEMO_KEY` is rate limited.
* `cd NOVA/backend && make probes` — the probes alone against a real uvicorn server
  (a free port, a temporary database, and authentication **enabled** to prove the guard).
  `make probes-offline` does the same without any external request.

**The difference between `make check` and `tools/verify.sh`:** `make check` is what CI
runs (offline, in-process, no socket). `tools/verify.sh` does the same things **plus**
the probes: real uvicorn + a real WebSocket client + the Mission Control console served
from an actual port. That is what in-process tests cannot prove.

## What you should see if everything is healthy

```
layout          ✓ layout ok – GitHub will run .github/workflows/backend.yml
ruff check      All checks passed!
ruff format     113 files already formatted
mypy            Success: no issues found in 82 source files
content         15 missions / 95 actions checked – 0 errors, 0 warnings
route audit     0 error(s), 0 warning(s)   (24 intentional info notes)
openapi         ✓ docs/openapi.json is up to date (67 operations)
pytest          451 passed · TOTAL 94%
campaign        ✓ campaign completed · 15 / 15 · 93 ok / 0 mistakes
player text     ✓ 996 responses · 67/67 operations · every string English
socket probes   12 probes · 0 failure(s)   (14 with --live: POWER + APOD + cache)
```

### A note about the “113 files” number

`ruff 0.17` does not count only Python files: it also includes Markdown files (it formats
the code blocks inside them) in the set of files it checks. So the number is
**110 Python modules + 3 Markdown files**. `migrations/versions/` is excluded in
`pyproject.toml`. The number changes when you add a new Python or Markdown file — that is
normal and not a defect, and it does not change any judgement about the code.

### A note about the probes and NASA

The NASA probe is **not required**: if it returns `provenance: bundled` with a written
reason (`fallback: …`), that is a design success, not a network failure. What fails the
gate is the service leaving the player without data or hiding the source.
