# NOVA – Beyond the Mission · Project Status

**Last updated: 2026-11-12** · Backend version `4.0.0` · Branch `main`

> This file is the reference for exactly where the project stands: what is finished,
> what was verified by running it rather than by claiming it, and what remains.
> Every number here comes from a command that was actually executed against the code
> in the repository.

---

## 1. Executive summary

**The backend is complete.** There is no missing mission, no half-working endpoint,
and no simulator returning fake numbers without saying they are simulated. The
service can now run the full campaign (8 stages / 15 missions) from astronaut
preparation through the Earth observation station report, entirely through the public
API — meaning any 3D client can play the game without needing gameplay logic in the
browser.

| Metric | Value | How it was verified |
| --- | --- | --- |
| Stages / missions | **8 / 15** | `content/missions/*.json` + `scripts/validate_content.py` |
| Validated actions | **95** (93 objectives + 2 side actions) | `validate_content.py --strict` |
| Published HTTP operations | **67** across 64 paths + 1 WebSocket | `scripts/audit_routes.py --check` |
| Response schemas | **136** | `docs/openapi.json` (OpenAPI 3.1) |
| Documented error codes | **25** in `x-error-codes` | `docs/openapi.json` |
| Guarded write operations | **15** (`require_game_write`) | Route audit |
| Tests | **451 tests – all passing** | `pytest` (offline) |
| Probes against a running server | **14 probes – 0 failures** | `scripts/live_probes.py` (uvicorn + WebSocket + NASA) |
| Code coverage | **94%** (6074 statements, 370 uncovered) | `pytest --cov=app` |
| Full campaign playthrough | **15/15 missions · 93 actions · 0 mistakes** | `scripts/playthrough.py --in-process` |
| Server-side simulators | **5** | `app/simulations/` |
| Items / resources / achievements | **26 / 4 / 20** (4 tiers) | `content/items.json`, `resources.json`, `achievements.json` |
| Code size | ~13.1k application lines + ~5.7k test lines + ~1.1k script lines | `wc -l` |
| Content language | English in every mission; `ar` retained as an English alias | `title/summary/hint/messages` |
| Player-facing API text | **67/67 operations · 996 responses · 56,453 strings – 0 non-English** | `scripts/audit_player_text.py` |

**The only remaining part of the project is the frontend (the 3D client).** That phase
is not part of the current delivery; the plan for starting it is in section 12.

---

## 2. How it was verified today (2026-11-12)

Verification environment: Python 3.11.2 · FastAPI 0.162.5 · Pydantic 2.14.3 ·
SQLAlchemy 2.0.57 · SQLite (aiosqlite). Installed directly from `requirements-dev.txt`.

### 2.1 Quality gates (the same ones CI runs)

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `ruff check .` | ✅ All checks passed |
| Format | `ruff format --check .` | ✅ 113 files already formatted |
| Types | `mypy app scripts` | ✅ no issues in 82 source files |
| Mission content | `validate_content.py --strict` | ✅ 15 missions / 95 actions – 0 errors, 0 warnings |
| Route audit | `audit_routes.py --check` | ✅ 0 errors, 0 warnings (24 intentional info notes) |
| API contract | `export_openapi.py --check` | ✅ `docs/openapi.json` up to date (67 operations) |
| Tests | `pytest --cov=app` | ✅ 451 passed · TOTAL 94% |
| Campaign | `playthrough.py --in-process --seed 7` | ✅ campaign completed (exit 0) |
| Real socket probes | `tools/verify.sh --live` | ✅ 14 probes · 0 failures (NASA POWER live) |

Operational note: an earlier run of this date installed `make`
(`apt-get install make` → GNU Make 4.3) to verify **the official entry point itself**
rather than equivalent commands: `make check` returned `exit 0`, and that log is kept in
`verification/2026-11-12/make-check.log`. The **final pass** (§9.6) ran in a sandbox
without `make`, so it used `bash tools/verify.sh --label 2026-11-12-final`, which executes
the same commands and adds the real-socket probe gate. The library versions in this run
are newer than the previous session (mypy 2.4.1, pytest 9.2.0, ruff 0.17.4,
FastAPI 0.162.5, Starlette 1.10.0), and every gate remained green without any code
changes. The full dated log and library versions are preserved in
[`NOVA/backend/docs/verification-2026-11-12.md`](NOVA/backend/docs/verification-2026-11-12.md).

### 2.2 Campaign result (every mission played through public HTTP)

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

Missions completed : 15 / 15     Readiness : 100.0 (S)
Actions            : 93 ok / 0 mistakes
```

The script uses **public routes only**, so any mission that could not be completed
would have failed here. This is the strongest evidence that the content and engine
agree.

### 2.3 The “move → interact → consequence” rule verified on a running server

We ran `uvicorn app.main:app --host 0.0.0.0 --port 8000` and tested manually:

```
POST /api/v1/games                                  → game_id + session_token
POST …/missions/training_prep/actions ×7            → 200 for every step (grade S)
POST …/missions/analog_water_leak/actions
     {"action":"repair_pipe"} before closing the valve → 409 precondition_failed
     "The line is still pressurised – water sprays across the bay.
      Close the valve first."  + details.unmet
GET  …/resources                                    → water 88 (no free bonus)
GET  /                                              → 200 (Mission Control, 46.9 KB)
GET  /api/v1/ready                     → status ready · database true · content true
                                         missions_loaded 15 · items_loaded 26
WS   …/games/{id}/stream?after_seq=0   → stream.ready then events:
                                         game.created · mission.started · action.succeeded
```

So the failure really happens, the message tells the player what to do, and resources
change as a consequence of the action rather than as a number sent by the client.

### 2.4 Real NASA data (not fallback)

| Request | Result | Source |
| --- | --- | --- |
| `POST /api/v1/earth/compare` (nile_delta · lst · 2001-2005 → 2021-2025 · `live=true`) | **`provenance: "live"`** | NASA POWER `T2M` (MODIS MOD11A2 / MERRA-2) |
| The same request again | `provenance: "cache"` | Service cache (TTL 900s) |
| `GET /api/v1/nasa/apod` | **`provenance: "live"`** | NASA APOD |

The real result returned: land surface temperature in the Nile Delta **+0.89 °C
(+4.06%)** between the two periods, classified as `degrading`, with 5 hotspots and
their coordinates, plus an English narrative. These numbers came from the live service,
not from the bundled file.

If the network fails or `DEMO_KEY` is rate limited, the same endpoint returns
`provenance: "bundled"` with a written `live_fallback_reason` — gameplay does not stop.

---

## 3. Status of every layer

| Layer | Status | Test coverage | Notes |
| --- | :-: | --: | --- |
| `api/v1` (10 modules) | ✅ Complete | 88–100% | Every operation has a `response_model` |
| `api/legacy.py` | ✅ Compatible with Prototype-01 | 94% | Deprecated, same engine and same guards |
| `core` (errors, security, limits, cache, locks, routing) | ✅ Complete | 76–100% | `routing.py` handles FastAPI ≥ 0.160 |
| `missions` (definitions, engine, registry, validation) | ✅ Complete | 77–94% | Most of `validation.py` is failure messages |
| `systems` (resources, inventory, oxygen, scoring, continuity, achievements, events) | ✅ Complete | 88–100% | |
| `simulations` (5 simulators) | ✅ Complete | 94–99% | Server-side physics |
| `services` (game / simulation / earth / science) | ✅ Complete | 88–99% | |
| `db` + `migrations` | ✅ Complete | 88–100% | Alembic and `create_all` are identical (tested) |
| `integrations/nasa_client` | ✅ Complete | 100% | Retry + cache + fallback |
| `schemas` | ✅ Complete | 98–100% | 136 schemas |
| Mission Control console | ✅ Complete | 19 tests | One HTML file, no external libraries |
| CI/CD + Docker + Alembic | ✅ Complete | 4 workflow tests | 3 jobs: quality · tests · campaign |
| Documentation | ✅ Complete | New gate (section 9) | Root README + backend manual + routes + openapi |
| **3D frontend** | ⏳ **Not started** | — | Next phase |

---

## 4. The fifteen missions — all validated

| # | Stage | Mission | Environment | Objectives | Play result |
| --: | --- | --- | --- | --: | :-: |
| 1 | Training Facility | Mission Preparation | `training_facility` | 7 | S |
| 2 | Earth Analog | Habitat Systems Survey | `earth_analog` | 4 | S |
| 2 | Earth Analog | Water Leak Repair | `earth_analog` | 4 | S |
| 2 | Earth Analog | Connect the Power Module | `earth_analog` | 6 | S |
| 2 | Earth Analog | Start the Water Recycler | `earth_analog` | 6 | S |
| 2 | Earth Analog | Communications Blackout | `earth_analog` | 5 | S |
| 2 | Earth Analog | Faulty Science Device | `earth_analog` | 6 | S |
| 2 | Earth Analog | Emergency Drill | `earth_analog` | 8 | S |
| 3 | Mars | Mars Arrival & Exploration | `mars_surface` | 8 | S |
| 3 | Mars | Long-Range Rover Traverse | `mars_surface` | 5 | S |
| 4 | Crew Health | Health Monitoring | `medical_bay` | 8 | S |
| 5 | Freefall Fire | Flame in Freefall | `orbital_station` | 7 | S |
| 6 | Martian Guide | Geological Sampling | `mars_lab` | 7 | S |
| 7 | Lunar Landing | Lunar Landing Navigator | `simulation_center` | 6 | S |
| 8 | Earth Trends | Observation Station | `earth_observation` | 6 | S |

Each mission file defines: `world_objects` (the devices the player walks to),
`animation` and `duration_ms` for each objective (what the client plays), `requires`
(tools / world state / previous objectives), `effects` (world and resource changes and
tool wear), `score`, and English success/failure messages. `requires_missions` builds
the dependency tree, and the engine refuses to start a mission before its prerequisites.

**The leak example (verified above):** `scan_leak` → `close_valve` → `repair_pipe` →
`verify_pressure`. Water: 88 → 88 → 100 → 100 litres. Repairing before the valve:
409 + lost repair kit + lost water.

---

## 5. The systems that tie gameplay together

* **Resources:** water / oxygen / power / fuel, each with a cap and critical level,
  consumed as a consequence of actions and ticks — not as numbers from the client.
  `dt_seconds` is clamped server-side, so speeding up the clock is not a cheat.
* **Inventory and tool wear:** 26 items; tools wear with use, and maintenance consumes
  a maintenance kit or base power. Tool condition affects scientific analysis accuracy.
* **Oxygen and EVA:** different consumption rates inside and outside the base, suit
  integrity, and leaving a pressurised location changes the calculation.
* **Scoring and grades:** every mission has a grade (S/A/B/C…), mistakes and time, and
  the report breaks down the steps and results.
* **Mission Continuity:** preparation quality + tool maintenance + resource discipline
  + training grade → a readiness score that adjusts the start of later stages. Every
  modifier has a name, source and delta, so there is no hidden “magic multiplier”.
* **Events and streaming:** `WS /api/v1/games/{id}/stream?after_seq=N` is a **read
  model** over the persisted event log: a disconnect or slow consumer (`stream.resync`)
  resumes from its cursor and no event is lost.
* **Achievements:** 20 achievements across 4 tiers, with live progress for locked ones.
* **Saving and transfer:** export/import a save bundle (state + reports + samples +
  simulation runs), resume by re-issuing the token, and a maximum number of saved
  campaigns.

---

## 6. Simulators (server-side physics)

| Simulator | What it computes | Declared simplification |
| --- | --- | --- |
| `lunar_lander` | Lunar gravity 1.62 m/s², throttle 0–100%, attitude angle, fuel, fixed 0.1 s integration steps, landing softness evaluated inside the pad | Simplified model (vertical/horizontal plane), not real flight dynamics |
| `microgravity_fire` | Spherical flame without convection, oxygen-limited spread, ventilation effects, CO₂ suppression and cabin oxygen draw afterwards | Calibrated educational model (inspired by Saffire/FLEX), not CFD |
| `crew_vitals` | Fictional vitals + response to support actions in the correct order | **Fictional numbers and an educational simulation, not medical guidance** (the disclaimer is inside every response) |
| `rover_traverse` | Motion, battery, odometer, terrain hazards, manual driving or autopilot through waypoints | Speeds calibrated for gameplay (real rovers crawl at centimetres/second) |
| `earth_trends` | Two-period comparison on a cell grid: statistics, per-cell delta, hotspots, English narrative | Real POWER for temperature/precipitation; the rest is a declared educational model |

Every simulator runs in steps (`start` → `tick` → `reset`), draws power from the base,
and records every attempt in `simulation_runs` so the report can compare attempts.
The lunar simulator lives inside the **Simulation Center** — the game does not confuse
the Moon with Mars.

---

## 7. Scientific honesty (important for academic delivery)

Every data payload carries its provenance:

| Provenance | Meaning |
| --- | --- |
| `live` | Fetched from a NASA service now |
| `cache` | The same data from the service cache (explicit TTL) |
| `bundled` | An offline bundled copy because the network/key is unavailable |
| `simulated` | A simplified educational model, not a real measurement |

* The temperature (`lst`) and precipitation (`precipitation`) layers support real
  POWER data without an API key.
* The `ndvi` / `aerosol` / `night_lights` layers are educational models **labelled as
  such inside the payload**, not only in the documentation.
* The mineral chemistry in `content/science/mars_minerals.json` (6 minerals + 4 sites)
  uses simplified ranges inspired by published NASA results, and the spectra are
  simulated.
* The medical procedure, crew vitals and microgravity fire are all educational
  simulations.
* Cross-stage resource effects are **gameplay balance**, not physics, and that is
  declared.

---

## 8. Operations and security

* **Authentication:** every game write is guarded by `require_game_write` (15
  operations). `NOVA_AUTH_ENABLED=true` ⇒ `X-Session-Token` is required. The legacy
  Prototype-01 routes use the same guard (the audit caught and closed a real bypass).
* **Request limits:** rate limit (1800/60s by default, health exempt) + body size limit
  (256 KB) + rejection of campaigns above the maximum.
* **Idempotency:** resending the same action with the same key returns the first result
  instead of executing twice.
* **Uniform errors:** one envelope `{error:{code,message,details},request_id}` and 25
  error codes published in `x-error-codes`.
* **Database:** SQLite by default, any async URL works, and in production
  `NOVA_AUTO_CREATE_TABLES=false` + `alembic upgrade head`.
* **Deployment:** the Dockerfile runs migrations before uvicorn as a non-root user with
  a healthcheck, and `docker-compose.yml` uses a persistent volume.
* **CORS:** preconfigured for Vite ports and preview domains (`*.e2b.app` and others).

---

## 9. Session log

### 9.1 Previous session (documentation + documentation gate)

1. **`README.md` at the repository root** — the full project overview: idea, status of
   every layer, startup in 3 commands, file map, campaign table, control bindings to
   endpoints, and scientific honesty. (Previously missing: the root had no README.)
2. **`PROJECT_STATUS.md`** — this file: a status report based on actual runs.
3. **New documentation gate** `tests/test_project_docs.py` — fails if the root
   documentation numbers drift from the content pack or the published API contract
   (the same philosophy as the `openapi --check` gate: documentation is part of the
   delivery, not decoration).
4. **Cleanup** of `reference/scratch/`: removed two copies of `payloads*.json`
   (586 KB duplicated) and kept `capture.py` + a small README explaining how to
   regenerate them.
5. **Re-ran every gate** in the current environment and recorded the result (section 2).

### 9.2 This session (2026-11-12): from “the code is done” to “the GitHub repo is done”

We started from a clean clone of `AbdElrhmanAhmed7/FastAPI-v11` (commit `9c4873f v11`),
not from a local working tree — to verify **what actually reached GitHub**.

1. **A real defect was found and closed: the `upstream/` wrapper.** The pushed tree was
   entirely inside an `upstream/` directory, so `.github/workflows/backend.yml` was one
   level below the git root ⇒ **GitHub would never run a single job**. The first
   `pytest` on the clone returned **8 failed + 4 errors** (documentation and CI gates) —
   the gate built in the previous session doing its job. Fixed with
   `tools/restructure_repo.sh` after generalising it to work with **any wrapper name**
   (not just `repo/`); every move used `git mv`, so history is preserved. Details in
   [`MIGRATION.md`](MIGRATION.md) §7.
2. **The probes became an official script, not copied text.** `scripts/live_probes.py`
   existed but was not wired to any entry point: we added `make probes` and
   `make probes-offline`, and `tools/verify.sh` now runs it as a gate (offline by
   default, `--live` when the network is available). `verification/README.md` pointed
   at `tools/live_probes.sh`, **which did not exist** — fixed.
3. **Fallback honesty improved.** The probe now reads both fields
   (`live_fallback_reason` in the Earth envelope and `reason` in the NASA envelope) and
   prints them on one line. Previously, when APOD fell back it printed
   `provenance bundled` without a reason, and the reader could not distinguish designed
   degradation from a broken integration. Now the reason is written
   (`503 from api.nasa.gov with DEMO_KEY`).
4. **A new cache probe.** The documentation said “the same request again ⇒ cache”
   without evidence; now a probe verifies that the second call returns **the same
   delta** from the cache.
5. **Lint cleanup:** `ruff 0.17.4` reported `I001` on two test files (`conftest` import
   order) — fixed.
6. **`releases/README.md`** was created (the root linked to it but the file was missing
   ⇒ the link gate would have failed), and the generated `.zip` was added to
   `.gitignore` because it is a copy of the same commit tree.
7. **`make` became available in this environment** (it had been missing), so we ran
   `make check` and `make probes-offline` **literally**, not equivalent commands — the
   result is in section 2.
8. **Re-ran all gates + campaign + probes** and recorded them in
   `verification/2026-11-12/` (`gates.log` · `campaign.json` · `live-probes.log` ·
   `live-probes.json`).

### 9.3 This session (2026-11-12 · Run 2): the wrapper returned a third time — closed with a gate

We started from a clean clone of `AbdElrhmanAhmed7/FastAPI-v11.5` (commit
`434b2e3 v11.5`) — meaning we verified **what actually reached GitHub**, not a local
working tree.

1. **The real defect: the `repo/` wrapper returned.** The pushed tree was entirely
   inside `repo/`, so `.github/workflows/backend.yml` was one level below the git root ⇒
   **GitHub would never run a single job** on that repo. This was the third occurrence
   of the same mistake (`NOVA/` ← `repo/` ← `upstream/` ← `repo/`). Fixed with
   `bash tools/restructure_repo.sh` (every move used `git mv`, so history is preserved).
   Details in [`MIGRATION.md`](MIGRATION.md) §8.
2. **A new gate to prevent recurrence: `tools/check_layout.sh`.** The existing gate
   (`tests/test_ci_workflow.py`) needs `.venv` and ~3 minutes to say the layout is
   wrong. The new gate needs **only bash + git** and finishes in fractions of a second,
   so it can run **before** `make install` and before any push. It rejects: any wrapper
   name, a second nested copy, any workflow buried below the root, and path filters that
   do not match. `tools/verify.sh` was also fixed to call it as the **first** gate before
   the venv check.
3. **9 regression tests for the gate** (`tests/test_repo_layout_guard.py`): they plant
   the historical mistakes in temporary git repositories (`repo/`, `upstream/`,
   `NOVA-main/`) and verify that the gate rejects them and names the fix. A gate that
   always says “ok” is worse than no gate, because people trust it. Tests: 437 → **446**.
4. **`make test` now prints the test count.** `pyproject.toml` has
   `addopts = "-q …"`, and the `Makefile` added another `-q` ⇒ `-qq` hid the
   `446 passed` line from the CI log. We removed the duplicate `-q`, so the log now
   contains the number.
5. **Run evidence was fully refreshed** in this environment: `make check` (exit 0) +
   `tools/verify.sh --live` (9 gates + 14 probes). The previous session's run is
   preserved in `verification/archive/2026-11-12-run1/`, not deleted.
6. **NASA POWER was genuinely live in this run:** Nile Delta **+0.8858 °C (+4.06%)**
   `provenance: live`, and the second call `cache` with the same delta. APOD returned
   `bundled` because of **500 from api.nasa.gov with DEMO_KEY**, and the reason is
   written in the log — designed degradation, not a hidden failure.
7. **The delivery package was actually generated:** `releases/` contained only
   `README.md`, while §13 described a patch and zip that did not exist. Now
   `tools/make_release.sh` has been run and produced the patch + zip + `SHA256SUMS.txt`
   from a real commit.

### 9.4 English localization pass (2026-11-12)

All Arabic player-facing text was converted to English. The legacy `ar` locale field
was retained as an English compatibility alias so existing clients and saved payloads
do not break, while the catalogue now advertises `languages: ["en"]`. Documentation,
mission content, items, resources, achievements, science reference, Earth datasets,
runtime notes, console diagnostics and archived campaign narratives are English.

New regression gates in `tests/test_english_content.py` verify that:

1. the content pack contains no Arabic text and every `ar` alias equals `en`;
2. public API payloads (catalogue, controls, Earth, science, game snapshot,
   continuity and interactions) are English;
3. repository sources and documentation contain no Arabic text.

Verification in this pass:

| Gate | Result |
| --- | --- |
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ 112 files already formatted |
| `mypy app scripts` | ✅ no issues in 81 source files |
| `validate_content.py --strict` | ✅ 15 missions / 95 actions – 0 errors, 0 warnings |
| `audit_routes.py --check` | ✅ 0 errors, 0 warnings (24 intentional info notes) |
| `export_openapi.py --check` | ✅ contract up to date (67 operations, 136 schemas) |
| `pytest --cov=app` | ✅ 449 passed · TOTAL 93% |
| `playthrough.py --in-process --seed 7` | ✅ 15/15 missions · 93 actions · 0 mistakes |
| `live_probes.py --no-live` | ✅ 12 probes · 0 failures (console, guard, WebSocket, leak order) |

Evidence: `verification/2026-11-12/english-gates.log`,
`verification/2026-11-12/english-campaign.json`,
`verification/2026-11-12/english-probes.log` and
`verification/2026-11-12/english-probes.json`.

---

### 9.5 Player-facing API text: audited at runtime, not just in the files (2026-11-12)

§9.4 converted the content. The question that matters for a player is different:
**what does the API actually send back?** A file-level scan cannot answer it —
error messages, hazard text, achievement names, simulator notes, Earth narratives
and WebSocket frames are assembled at request time.

`scripts/audit_player_text.py` now answers it by playing the game:

1. the full campaign over public HTTP (success text for all 15 missions);
2. a second save that deliberately breaks the rules — locked mission, unknown
   action, missing item, worn tool, repair before closing the valve, bad payload,
   bad save bundle, request-validation errors (failure text, 51 error responses);
3. every catalogue, meta, NASA, legacy Prototype-01 and console surface;
4. the WebSocket feed tailed while an action lands;
5. then **every string of every response** is walked: Arabic (including
   presentation forms), any other non-Latin script, and any `ar` alias that
   drifted from its `en` source are violations.

| Run | Result |
| --- | --- |
| in-process (`pytest` + `make text`) | ✅ 996 responses · 56,445 strings · 473 distinct player strings · **67/67 operations** · 0 violations |
| real uvicorn socket (`--base-url`) | ✅ 996 responses · 56,560 strings · 476 distinct player strings · **67/67 operations** · 0 violations |

The gate is enforced in three places: `pytest` (`tests/test_english_content.py`,
which also asserts the auditor *detects* planted Arabic — a gate that cannot fail
is worse than no gate), `make text`, and a named CI step that uploads the report.
`tools/verify.sh` records it as `player-text.json` + `player-text.md`.

Two repository defects were fixed in the same pass:

1. **The wrapper returned a fourth time.** The pushed tree was `novaa/…`, so
   `.github/workflows/backend.yml` sat below the git root and 21 layout/doc tests
   failed. `bash tools/restructure_repo.sh` promoted the project with `git mv`;
   `tools/check_layout.sh` now says `✓ layout ok` and the suite is green again.
2. **The delivery patch still carried the pre-translation Arabic.**
   `releases/nova-backend-final.patch` was regenerated from the committed tree, so
   no file in the repository contains Arabic text any more.

Evidence: `verification/2026-11-12-english-api/gates.log` (10 gates),
`player-text.json` / `player-text.md` (in-process),
`player-text-live.json` / `player-text-live.md` (real socket),
`campaign.json`, `live-probes.json`.

---

### 9.6 Final pass (2026-11-12): the wrapper returned a fifth time — layout fixed, everything re-verified

The repository delivered to `AbdElrhmanAhmed7/Final` (commit `11cefe5`, message
`final`) was **again** a single wrapper directory:

```
<git root>/
└── repo/
    ├── .github/workflows/backend.yml   ← GitHub never read this (fifth time)
    ├── NOVA/backend/…
    ├── README.md · PROJECT_STATUS.md · MIGRATION.md
    ├── tools/ · verification/ · releases/ · reference/
```

`bash tools/check_layout.sh` on the fresh clone said
`✗ NOVA/backend is not at the git root`, i.e. **CI would not have run a single job**.
Nothing else was wrong with the tree: no stray database, no leftover translation
script, no stale patch, and a clean `git status`.

| Step | Command | Result |
| --- | --- | --- |
| Promote the project to the git root (191 files, history preserved) | `bash tools/restructure_repo.sh` | 191 × `git mv`, `repo/` removed |
| Layout gate | `bash tools/check_layout.sh` | ✓ layout ok |
| Rebuild the virtualenv (a moved `.venv` has stale absolute paths) | `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt` | Python 3.11.2 |
| Every gate, one command, dated log | `bash tools/verify.sh --label 2026-11-12-final` | **10 / 10 PASS** |
| Live preview server, database under `data/` (ignored) | `NOVA_DATABASE_URL=sqlite+aiosqlite:///./data/nova-preview.db uvicorn app.main:app --host 0.0.0.0 --port 8000` | ready · console · campaign playable |
| Smoke-test the server the user actually opens | `reference/scratch/preview_smoke.py --base-url http://127.0.0.1:8000` | ✓ rules enforced (see below) |
| Player-text audit over a real socket | `audit_player_text.py --base-url …` | 996 responses · 56,578 strings · 67/67 operations · 0 violations |
| Probes with live NASA | `live_probes.py` | **14 probes · 0 failures** · POWER `live` |

Gate results in this run (all from `verification/2026-11-12-final/gates.log`):

| Gate | Result |
| --- | --- |
| `ruff check .` | ✅ All checks passed |
| `ruff format --check .` | ✅ 113 files already formatted |
| `mypy app scripts` | ✅ no issues in 82 source files |
| `validate_content.py --strict` | ✅ 15 missions / 95 actions – 0 errors, 0 warnings |
| `audit_routes.py --check` | ✅ 0 errors, 0 warnings (24 intentional info notes) |
| `export_openapi.py --check` | ✅ contract up to date (67 operations, 136 schemas) |
| `pytest --cov=app` | ✅ **451 passed** · TOTAL **94%** (206.7 s) |
| `playthrough.py --in-process --seed 7` | ✅ 15/15 missions · 93 actions · 0 mistakes · readiness 100 (S) |
| `audit_player_text.py` (in-process) | ✅ 996 responses · 56,453 strings · 473 distinct · 67/67 operations |
| `live_probes.py --no-live` | ✅ 12 probes · 0 failures |

The only warning in the suite is Starlette's own deprecation notice about `httpx`
in `TestClient` (Starlette 1.10). It is upstream, not a project defect, and it does
not affect any assertion.

**The live preview was checked as a player would.** `reference/scratch/preview_smoke.py`
is new in this pass: `live_probes.py` boots its own throwaway server, which cannot
prove that the server *the user is looking at* works. The harness points at the
running base URL and asserts readiness, a console with **0 external assets** (it must
render inside a sandboxed iframe), the prerequisite missions, and the canonical rule:

```
✗ repair BEFORE closing the valve → HTTP 409 precondition_failed
  "The line is still pressurised – water sprays across the bay. Close the valve first."
  unmet: ['scan_leak', 'close_valve', 'leak.valve_closed']
✓ scan → close valve → repair → verify → score 88.89 · grade A · 1 recorded mistake
  water 100 → 88 → 100 L · 27 events in the log
```

The preview database lives at `NOVA/backend/data/nova-preview.db`, which
`NOVA/backend/.gitignore` already ignores (`data/*.db`, `data/*.db-wal`,
`data/*.db-shm`) — player saves are not source, and `git status` stays clean while
the server runs.

Two housekeeping defects were fixed in the same pass:

1. **`releases/SHA256SUMS.txt` described artefacts that were not in the tree.** The
   hashes were regenerated by `tools/make_release.sh --base 11cefe5` from the committed
   tree, so the file and the patch match again (the `.zip` stays gitignored by design).
2. **The preview smoke evidence was not reproducible.** It is now a committed harness
   with a documented command instead of an ad-hoc sequence of requests.

Evidence: `verification/2026-11-12-final/` — `gates.log` (10 gates), `campaign.json`,
`player-text.json` / `.md`, `player-text-live.json` / `.md` / `.log`,
`live-probes.json` (offline), `live-probes-live.json` / `.log` (NASA POWER `live`,
APOD `bundled` because `api.nasa.gov` returned HTTP 500 — the labelled fallback is the
designed behaviour), and `preview-smoke.log`.

---

## 10. Known limits and deliberately deferred decisions

| Item | Situation | Decision |
| --- | --- | --- |
| 24 operations still have free-form `object` fields | `info` note from the audit | Intentional: variable telemetry + legacy routes. The client generates their types from `docs/openapi.json` |
| Event bus and rate limit are process-local | Not Redis | Enough for one worker; horizontal scaling needs Redis (documented) |
| SQLite by default | Suitable for the game/demo | Postgres by changing only `NOVA_DATABASE_URL` |
| `DEMO_KEY` for NASA | Very limited rate | Set a free `NOVA_NASA_API_KEY`; the fallback protects gameplay |
| NDVI / aerosol / night-lights | Educational model | Real MODIS data needs Earthdata login + tile processing |
| Player accounts / public leaderboard | No login | The `game_id` itself is a secret capability, and the token is per campaign |
| Simulator physics | Educationally simplified | Declared in every payload |

---

## 11. How to run and verify it yourself

```bash
# From the repository root: 0.1 seconds, no install — will GitHub see the workflow?
bash tools/check_layout.sh

cd NOVA/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# Server + console
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
#   /            Mission Control (a console that plays the API)
#   /docs        Swagger UI
#   /api/v1/ready Service diagnostics

# All gates (what CI runs)
make check

# Extra evidence on a real server: WebSocket + console + NASA
make probes            # Live NASA (needs network)
make probes-offline    # The same without network (12 probes)

# From the repository root: all of the above + dated evidence
bash tools/verify.sh --live

# Or manually if make is unavailable:
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app scripts
.venv/bin/python scripts/validate_content.py --strict
.venv/bin/python scripts/audit_routes.py --check
.venv/bin/python scripts/export_openapi.py --check
.venv/bin/pytest --cov=app --cov-report=term-missing
.venv/bin/python scripts/playthrough.py --in-process --json campaign.json
```

---

## 12. Next step: the frontend (not part of the current delivery)

The backend is ready to receive a 3D client immediately. Suggested order:

1. **Generate the typed client:**
   `npx openapi-typescript NOVA/backend/docs/openapi.json -o src/api/nova.d.ts`
2. **Client structure:** `game/player` (Astronaut, Movement, CameraController),
   `game/environments` (Mars, EarthAnalog, Moon…), `game/systems`
   (Interaction, Inventory, Oxygen, Physics), `game/missions`, `game/ui`.
3. **Game loop:** `tick` roughly once per second (not every frame), `E` calls
   `…/actions` with an `Idempotency-Key`, and animation comes from `animation` +
   `duration_ms` in the objective definition.
4. **HUD:** from one snapshot (`GET /games/{id}`) + the event stream; buttons are
   disabled from `interactions.unmet` instead of letting the player fail blindly.
5. **Assets:** Blender → glTF, and Rapier for client-side collision only (authoritative
   results always come from the server).

---

## 13. Delivery files

The tree is now **at the repository root** (as GitHub requires), and the new commit
sits above `11cefe5 ("final")` — the commit that was actually pushed to
`AbdElrhmanAhmed7/Final` and was still inside a `repo/` wrapper (§9.6):

| File | What it is |
| --- | --- |
| Repository root | `README.md` · `PROJECT_STATUS.md` · `MIGRATION.md` · `.github/` · `NOVA/` · `tools/` · `verification/` · `releases/` · `reference/` |
| `releases/nova-backend-final.patch` | This session's delta only (the `git mv` migration + the final verification pass) – apply with `git am` on a clean clone of `11cefe5` |
| `releases/nova-backend-final.zip` | Full tree snapshot (`git archive HEAD`) without `releases/` – **not committed to git** (`releases/*.zip` is in `.gitignore`), generated locally |
| `releases/SHA256SUMS.txt` | Hashes of the generated files (`sha256sum -c`) |
| `verification/2026-11-12-final/` | **The current delivery evidence:** 10 gates, campaign, player-text audit (in-process + real socket), offline and live probes, preview smoke test |
| `verification/2026-11-12/gates.log` | Earlier run of the same date: ruff · mypy · content · audit · contract · pytest with coverage · campaign · probes |
| `verification/2026-11-12/campaign.json` | Campaign report JSON (15 missions, scores, resources, readiness) |
| `verification/2026-11-12/live-probes.log` / `.json` | Probes against real uvicorn + NASA (text + JSON) |
| `verification/2026-11-12/make-check.log` | `make check` literally (the official CI entry point) – exit 0 |
| `verification/2026-11-12-english-api/` | The English API pass: 10 gates, campaign, probes and the player-text audit |
| `verification/archive/2026-11-12-run1/` | Previous session's run before the wrapper fix – preserved for traceability |
| `reference/scratch/preview_smoke.py` | Smoke test for the server a human opens (readiness, console, leak order, events) |
| `NOVA/backend/docs/verification-2026-11-12.md` | Dated verification snapshot with library versions (Run 1 + Run 2 + Run 3) |

```bash
# On a clean clone from GitHub (still inside a repo/ wrapper):
git clone https://github.com/AbdElrhmanAhmed7/Final && cd Final
git am /path/to/nova-backend-final.patch    # performs the git mv migration + this session
bash tools/check_layout.sh                  # ✓ layout ok
cd NOVA/backend && make install
cd ../.. && bash tools/verify.sh --live      # all gates + campaign + probes
git push origin main                        # requires your GitHub permissions
```

> **Important:** if you upload the tree through the GitHub UI instead of `git push`,
> drag the **contents** of the folder, not the folder itself. Uploading a whole folder
> is what created the `repo/` wrapper five times. After any upload:
> `bash tools/check_layout.sh` must say `✓ layout ok`.

**After pushing:** open the **Actions** tab on GitHub. A workflow named `backend` must
appear and run (3 jobs). If it does not, there is another wrapper around the project —
`bash tools/restructure_repo.sh` fixes it.

**Bottom line: the backend is closed and verified — 15/15 missions, 451 tests, 94%
coverage, 14 probes against a running server, real NASA POWER data working, and the
campaign fully playable through the public API. What remains in the project is the
3D frontend.**
