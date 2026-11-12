# Verification run · 2026-11-12

A dated snapshot of a **full `make check` run plus live-integration probes** on
the committed backend. It is evidence, not a gate: regenerate it any time with
`make check` (and `scripts/playthrough.py --json`). Nothing in the app reads this
file.

## Environment

| | |
| --- | --- |
| Python | 3.11.2 |
| fastapi | 0.162.5 |
| pydantic | 2.14.3 |
| SQLAlchemy | 2.0.57 |
| uvicorn | 0.54.0 |
| httpx | 0.28.1 |
| alembic | 1.20.1 |
| aiosqlite | 0.22.1 |
| pytest | 9.2.0 |
| ruff | 0.17.4 |
| mypy | 2.4.1 |

Installed from `requirements-dev.txt` into a fresh virtualenv; database = SQLite.
In Run 1 `make` was unavailable in the verification sandbox, so every target was
executed verbatim, one command at a time; Runs 2 and 3 had GNU Make and ran
`make check` itself (see Run 3 below).

## Gates

```
== ruff check ==       All checks passed!
== ruff format ==      108 files already formatted
== mypy ==             Success: no issues found in 80 source files
== content ==          15 missions / 95 actions checked – 0 errors, 0 warnings
== audit ==            routes_total 75 · http_published 67 · operations 67
                       websockets 1 · mounts 1 · guarded_operations 15
                       schemas 136 · findings {'info': 24}
                       0 error(s), 0 warning(s)
== contract ==         ✓ docs/openapi.json is up to date (67 operations)
== pytest ==           437 passed, 1 warning in 176.62s
                       TOTAL 6075 statements · 410 missed · 93% coverage
== playthrough ==      ✓ campaign completed (exit 0)
```

The single warning is a Starlette deprecation notice about `httpx` in
`fastapi.testclient`, emitted by the dependency itself.

## Campaign (public HTTP only, `--difficulty standard --seed 7`)

```
MISSION                        SCORE  GRADE  MISTAKES  STEPS
training_prep                 100.0%      S         0      7
analog_systems_check          100.0%      S         0      4
analog_device_fault           100.0%      S         0      6
analog_emergency              100.0%      S         0      8
analog_power_link             100.0%      S         0      6
analog_comms_blackout         100.0%      S         0      5
analog_water_leak             100.0%      S         0      4
analog_water_recycling        100.0%      S         0      6
mars_arrival                  100.0%      S         0      8
mars_rover_traverse           100.0%      S         0      6
crew_health                   100.0%      S         0      9
freefall_fire                 100.0%      S         0      8
martian_guide                 100.0%      S         0      7
lunar_landing                 100.0%      S         0      7
earth_trends                  100.0%      S         0      6

Missions completed : 15 / 15      Readiness : 100.0 (S)
Average score      : 100.0%       Playtime  : 259.5 s
Actions            : 93 ok / 0 mistakes
```

## Probes against a running server

`uvicorn app.main:app --host 0.0.0.0 --port 8000`

| Probe | Result |
| --- | --- |
| `GET /api/v1/health` | `status ok`, version `4.0.0` |
| `GET /api/v1/ready` | `ready` · database ✓ · content ✓ · 15 missions · 26 items |
| `GET /` (Mission Control) | `200 text/html`, 46,919 bytes, no external assets |
| `WS /api/v1/games/{id}/stream?after_seq=0` | `stream.ready` → `events` frames carrying `game.created`, `mission.started`, `action.succeeded` |
| `POST /api/v1/games` | `game_id` + `session_token` issued |
| `training_prep`, 7 actions | `200` each, report grade **S**, 0 mistakes |
| `analog_water_leak` → `repair_pipe` **before** `close_valve` | **409 `precondition_failed`** with `details.unmet`, water stays at 88 L |
| `POST /api/v1/earth/compare` (nile_delta · lst · 2001-2005 → 2021-2025 · `live=true`) | **`provenance: live`** — NASA POWER `T2M`, mean delta **+0.8858 °C (+4.06%)**, verdict `degrading`, 5 hotspots |
| same request repeated | `provenance: cache` (900 s TTL) |
| `GET /api/v1/nasa/apod` | **`provenance: live`** — NASA APOD |

The live probes need outbound network access. When it is missing (or `DEMO_KEY`
is throttled) the same endpoints answer with `provenance: bundled` plus a
`live_fallback_reason`, which is the designed degradation — the campaign above
runs fully offline.

---

# Run 2 · 2026-11-12 (fresh clone from GitHub, `9c4873f "v11"`)

The run above was made in the working tree. This one starts from
`git clone https://github.com/AbdElrhmanAhmed7/FastAPI-v11` — i.e. it verifies
**what actually reached GitHub**, which is a different question.

## What the clone proved immediately

The pushed tree wrapped the whole project in `upstream/`, so the git root held
one directory and nothing else. `pytest` on that clone:

```
8 failed, 425 passed, 4 errors
  tests/test_project_docs.py   … root README/PROJECT_STATUS not at the git root
  tests/test_ci_workflow.py    … .github/workflows/backend.yml not at the git root
```

That is the layout gate doing its job: GitHub reads workflows only from
`<git root>/.github/workflows`, and the workflow filters on `NOVA/backend/**`,
so **CI would never have run a single job** on that push. Fixed with
`bash tools/restructure_repo.sh` (now wrapper-name agnostic, every move a
`git mv`); see [`MIGRATION.md`](../../../MIGRATION.md) §7.

Two lint findings were fixed at the same time (`ruff 0.17.4` import ordering in
`tests/test_ci_workflow.py` and `tests/test_project_docs.py`).

## Gates after the migration

```
== ruff check ==       All checks passed!
== ruff format ==      110 files already formatted
== mypy ==             Success: no issues found in 81 source files
== content ==          15 missions / 95 actions checked – 0 errors, 0 warnings
== audit ==            routes_total 75 · http_published 67 · operations 67
                       websockets 1 · mounts 1 · guarded_operations 15
                       schemas 136 · findings {'info': 24}
                       0 error(s), 0 warning(s)
== contract ==         ✓ docs/openapi.json is up to date (67 operations)
== pytest ==           437 passed, 1 warning in 177.35s
                       TOTAL 6075 statements · 409 missed · 93% coverage
== campaign ==         15 / 15 · readiness 100.0 (S) · 93 ok / 0 mistakes
== socket probes ==    14 probes · 0 failure(s)
```

Command: `bash tools/verify.sh --label 2026-11-12 --live` (from the git root).
Same dependency versions as Run 1 (Python 3.11.2 · pytest 9.2.0 · ruff 0.17.4 ·
mypy 2.4.1), installed into a fresh virtualenv **after** the move — a relocated
`.venv` keeps stale absolute paths in its console scripts.

## The probes are now a script, not a transcript

`scripts/live_probes.py` boots `uvicorn` on a free port with a temporary
database and `NOVA_AUTH_ENABLED=true`, then exercises the public surface.
`make probes` (live NASA) / `make probes-offline` (deterministic) /
`tools/verify.sh [--live]` all run it, and it writes both a readable log and
`verification/<date>/live-probes.json`.

| Probe | Result |
| --- | --- |
| health | `status ok` · version `4.0.0` |
| ready | database ✓ · content ✓ · 15 missions · 26 items |
| console | `200` · 47,278 bytes · **0 external assets** |
| write guard | token-less mission start → **401 unauthorized** |
| `training_prep` | 100.0% grade **S** · 7 steps · 0 mistakes |
| `analog_systems_check` | 100.0% grade **S** · 4 steps · 0 mistakes |
| leak order enforced | `repair_pipe` before `close_valve` → **409 precondition_failed** + 3 `unmet` entries with hints |
| movement tick | 60 s run → `200` · oxygen 105 · water 88 (leak draining) |
| websocket stream | `stream.ready` → `events` frames: `game.created`, `mission.started`, `action.succeeded` |
| stream resume | reconnect `after_seq=23` → `stream.ready` · `event_seq 23` (no event lost) |
| leak repaired | grade **A** (1 deliberate mistake) · water **100 → 88 → 100 L** · `leak_active false` |
| NASA POWER | **`provenance live`** · Δmean **+0.8858 °C (+4.06%)** · `degrading` · 5 hotspots · MODIS MOD11A2 / MERRA-2 `T2M` |
| NASA APOD | `provenance bundled` · *Perseverance Selfie at Rochette* · `fallback: 503 from api.nasa.gov (DEMO_KEY)` |
| NASA POWER cache | `provenance cache` · identical Δmean — the 900 s TTL really serves the same numbers |

Two honesty fixes came out of writing this down:

* the probe now reads **both** fallback fields (`live_fallback_reason` on the
  Earth envelope, `reason` on the NASA envelope). Before, a throttled APOD
  printed a bare `provenance bundled` and the reader could not tell a designed
  degradation from a broken integration;
* the cache probe is new. The docs claimed "same request → `cache`"; now a probe
  asserts the second call returns the *same delta* from cache.

`NASA APOD` is **not a required probe**: `api.nasa.gov` answered 500/503 for
`DEMO_KEY` during this run, and the service did exactly what it is designed to
do — serve the bundled snapshot and label the reason. The campaign never
depends on the network.

---

## Run 3 · 2026-11-12 · fresh clone of `FastAPI-v11.5` (the tree GitHub actually had)

Runs 1 and 2 verified a local working tree. Run 3 started from
`git clone https://github.com/AbdElrhmanAhmed7/FastAPI-v11.5` at commit
`434b2e3 (v11.5)` — i.e. it verified **what was published**, not what was intended.

### What the clone revealed

The whole project was inside a `repo/` wrapper again (third recurrence), so
`.github/workflows/backend.yml` sat at `repo/.github/workflows/backend.yml`.
GitHub only reads workflows from the git root, therefore **no CI job would ever
have run on that repository**. Fixed with `tools/restructure_repo.sh` (every move
a `git mv`, so history follows the files), and guarded from now on by
`tools/check_layout.sh` + 9 regression tests
(`tests/test_repo_layout_guard.py`) that plant `repo/`, `upstream/` and
`NOVA-main/` wrappers in throwaway git repositories and assert they are rejected.

### Environment differences from Runs 1–2

| | |
| --- | --- |
| GNU Make | **4.3 — installed in this sandbox** (`apt-get install make`), so `make check` itself ran, not hand-copied equivalents |
| pytest | 9.2.0 · ruff 0.17.4 · mypy 2.4.1 (newer than Run 1; **no application code changed to accommodate them**) |
| Interpreter | Python 3.11.2, fresh `.venv` from `requirements-dev.txt` |

### Gates — `make check` exit 0, then `tools/verify.sh --live` exit 0

```
== layout ==           ✓ layout ok – git root = project root
== ruff check ==       All checks passed!
== ruff format ==      111 files already formatted
== mypy ==             Success: no issues found in 81 source files
== content ==          15 missions / 95 actions checked – 0 errors, 0 warnings
== route audit ==      0 error(s), 0 warning(s)   (24 info findings, intentional)
== openapi ==          ✓ docs/openapi.json is up to date (67 operations)
== pytest ==           446 passed, 1 warning in 176.31s
                       TOTAL 6075 statements · 410 missed · 93% coverage
== campaign ==         ✓ campaign completed · 15 / 15 missions
                       Readiness 100.0 (S) · 93 ok / 0 mistakes
== socket probes ==    14 probes · 0 failure(s)
```

`make test` also stopped hiding its own count: `pyproject.toml` already passes
`-q` through `addopts`, and the Makefile added a second `-q`, so CI logs showed
dots but never the `446 passed` line. The duplicate flag was removed.

### Probes — run twice (inside `verify.sh`, then `make probes` standalone)

Both runs: **14 probes · 0 failures**, on a real uvicorn socket with auth enabled.
Same results as Run 2, including the two that matter most for honesty:

| Probe | Result |
| --- | --- |
| NASA POWER | **`provenance live`** · Δmean **+0.8858 °C (+4.06%)** · `degrading` · 5 hotspots · MODIS MOD11A2 / MERRA-2 `T2M` |
| NASA POWER cache | `provenance cache` · identical Δmean 0.8858 °C |
| NASA APOD | `provenance bundled` · fallback reason printed: **500 from `api.nasa.gov` with `DEMO_KEY`** |
| stream resume | reconnect `after_seq=23` → `stream.ready` · `event_seq 23` |
| write guard | token-less mission start → **401 unauthorized** |

### Extra evidence: the server a human actually opens

`uvicorn app.main:app --host 0.0.0.0 --port 8000` was left running and played by
hand (`verification/2026-11-12/preview-smoke.log`):

```
ready          200 · db true · 15 missions · 26 items
console        200 · 47,278 bytes · 0 external assets
repair BEFORE close_valve → 409 precondition_failed
  "The line is still pressurised – water sprays across the bay. Close the valve first."
  unmet: ['scan_leak', 'close_valve', 'leak.valve_closed']
scan → close_valve → repair_pipe → verify_pressure → 200 each
water 88 → 100 L · report grade A (1 deliberate mistake) · 27 events logged
```

That is the design rule — **move → interact → consequence** — enforced by the
server on the exact build that is being delivered.

### Artefacts

| Path | What |
| --- | --- |
| `verification/2026-11-12/make-check.log` | `make check` verbatim, exit 0 |
| `verification/2026-11-12/gates.log` | all 9 gates incl. coverage, campaign and probes |
| `verification/2026-11-12/campaign.json` | machine-readable campaign report |
| `verification/2026-11-12/live-probes.log` / `.json` | `make probes` text + JSON |
| `verification/2026-11-12/preview-smoke.log` | the hand-played leak mission on port 8000 |
| `verification/archive/2026-11-12-run1/` | the previous session's run, preserved |
