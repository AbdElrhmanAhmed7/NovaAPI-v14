# NOVA – Beyond the Mission

**Explore. Experience. Execute.**

A 3D web game about an astronaut exploring Mars and building a sustainable research base.
This is not a dashboard and not a quiz game: the player **moves through the world**,
walks up to a device, interacts with it, and the server decides whether the action is
allowed and actually changes the resources.

> **Current project status:** the **backend is complete** and verified (15 missions,
> 67 HTTP operations, 451 tests, 94% coverage, and the full campaign is playable through
> the public API). The frontend (React Three Fiber) is the next phase and starts from
> the ready API contract. Full details are in [`PROJECT_STATUS.md`](PROJECT_STATUS.md).

| Layer | Status | Reference |
| --- | --- | --- |
| Gameplay backend (FastAPI) | ✅ **Complete** – v4.0.0 | [`NOVA/backend/README.md`](NOVA/backend/README.md) |
| Mission content (English) | ✅ 8 stages · 15 missions · 95 actions | [`NOVA/backend/content/`](NOVA/backend/content) |
| Physics simulators | ✅ 5 server-side simulators | `app/simulations/` |
| Progress saving + reports | ✅ SQLAlchemy 2 async + Alembic | `app/db/`, `migrations/` |
| NASA data | ✅ Live POWER + source-labelled fallback | `app/integrations/`, `app/services/earth_service.py` |
| Frontend API contract | ✅ OpenAPI 3.1 exported and verified in CI | [`docs/openapi.json`](NOVA/backend/docs/openapi.json) |
| Dependency-free dev console | ✅ Mission Control at `/` | `app/static/index.html` |
| 3D client (R3F + Rapier) | ⏳ Next phase – not built yet | Delivery plan in `PROJECT_STATUS.md` |

---

## Start in 3 commands

```bash
cd NOVA/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/` → **Mission Control**: a console that actually plays the
API (HUD, missions, simulators, event stream, service diagnostics) with no build step.
`/docs` provides Swagger UI with an Authorize button.

Saves go to `NOVA/backend/data/nova.db` by default, and `data/*.db` is gitignored —
keep any local/preview database under `data/` (override with `NOVA_DATABASE_URL`) so a
running server never leaves untracked files in the tree.

Play the whole campaign without a browser and without a running server:

```bash
.venv/bin/python scripts/playthrough.py --in-process --difficulty standard --seed 7
```

Verify that everything is healthy (the same gates CI runs):

```bash
make check     # ruff · mypy · validate_content · audit_routes · openapi · pytest · playthrough
```

---

## What NOVA is

NOVA is a **third-person 3D web game** (with a `V` toggle to first person) about an
astronaut preparing for, travelling to and surviving a Mars research mission.
The design rule for every mission is **move → interact → consequence**:

1. the player walks to the device,
2. reaches a specific world object,
3. interacts and picks an action,
4. the character plays an animation / the environment reacts,
5. a real result lands: mission state and resources change.

The water leak is the canonical example. There is no quiz about how to fix a pipe:
the player scans the manifold, closes the isolation valve, then repairs the joint.
Repairing **before** closing the valve fails, wastes a repair kit and costs water —
the server enforces the order, not the client.

The split of responsibility is deliberate:

* **Client (planned):** rendering, movement, camera, animation, input, HUD.
* **Backend (done):** mission rules and preconditions, resources, inventory and tool
  wear, oxygen and EVA, physics simulations, scoring, continuity, persistence,
  events, and every number the HUD displays.

## Repository layout

The project sits **at the repository root** – `.github/workflows/backend.yml`
must live there or GitHub never runs it (see [`MIGRATION.md`](MIGRATION.md)).

```
.
├── README.md                 ← you are here (project overview)
├── PROJECT_STATUS.md         ← what is finished, verified, and what comes next
├── MIGRATION.md              ← the repo/ → root layout move, and how to push it
├── .github/workflows/backend.yml   CI: lint · types · content · audit · contract · tests · campaign
├── NOVA/
│   └── backend/              the authoritative gameplay service (FastAPI)
│       ├── app/
│       │   ├── api/v1/       meta, content, games, missions, loadout,
│       │   │                 simulations, earth, science, nasa, stream
│       │   ├── api/legacy.py Prototype-01 URLs (deprecated, same engine)
│       │   ├── core/         logging, errors, middleware, security, rate limit,
│       │   │                 cache, locks, openapi, route introspection
│       │   ├── db/           models, repositories, async session
│       │   ├── domain/       enums, GameState document, catalogues
│       │   ├── missions/     definitions, engine, registry, validation
│       │   ├── simulations/  lunar_lander, microgravity_fire, crew_vitals,
│       │   │                 rover_traverse, earth_trends
│       │   ├── systems/      resources, inventory, oxygen, scoring,
│       │   │                 continuity, achievements, event bus
│       │   ├── services/     game / simulation / earth / science
│       │   ├── schemas/      pydantic request & response models (136 schemas)
│       │   └── static/       Mission Control dev console
│       ├── content/          15 mission blueprints, items, resources,
│       │                     achievements, science, NASA fallbacks
│       ├── migrations/       Alembic environment + revisions
│       ├── scripts/          playthrough, autopilot, validate_content,
│       │                     audit_routes, export_openapi
│       ├── tests/            451 tests, no network required
│       └── docs/             openapi.json, routes.md, routes.json, verification
├── tools/                    repo-level helpers (no `make` required)
│   ├── check_layout.sh       layout guard: bash+git only, milliseconds
│   ├── verify.sh             every gate, one command, writes a log
│   ├── make_release.sh       regenerates the patch + zip in releases/
│   └── restructure_repo.sh   the one-shot layout migration (idempotent)
├── verification/             dated evidence: gate logs, campaign report, probes
├── releases/                 generated delivery artefacts (patch + zip)
└── reference/scratch/        harnesses: capture.py (payload dumps, not committed)
                              preview_smoke.py (smoke-test a running server)
```

## The campaign — 8 stages, 15 missions

Every mission is authored in `content/missions/*.json` with English text, world
objects, animations, preconditions, effects and scoring. The legacy `ar` locale
field is retained as an English alias so older clients keep working.
`requires_missions` builds the dependency graph; the engine refuses to start a
mission whose prerequisites are unmet.

| # | Stage | Mission | Environment | Objectives |
| --: | --- | --- | --- | --: |
| 1 | Training Facility | Mission Preparation | `training_facility` | 7 |
| 2 | Earth Analog | Habitat Systems Survey | `earth_analog` | 4 |
| 2 | Earth Analog | Water Leak Repair | `earth_analog` | 4 |
| 2 | Earth Analog | Connect the Power Module | `earth_analog` | 6 |
| 2 | Earth Analog | Start the Water Recycler | `earth_analog` | 6 |
| 2 | Earth Analog | Communications Blackout | `earth_analog` | 5 |
| 2 | Earth Analog | Faulty Science Device | `earth_analog` | 6 |
| 2 | Earth Analog | Emergency Drill | `earth_analog` | 8 |
| 3 | Mars Arrival | Mars Arrival & Exploration | `mars_surface` | 8 |
| 3 | Mars Arrival | Long-Range Rover Traverse | `mars_surface` | 5 |
| 4 | Crew Health | Health Monitoring | `medical_bay` | 8 |
| 5 | Freefall Fire | Flame in Freefall | `orbital_station` | 7 |
| 6 | Martian Guide | Geological Sampling | `mars_lab` | 7 |
| 7 | Lunar Landing | Lunar Landing Navigator | `simulation_center` | 6 |
| 8 | Earth Trends | Observation Station | `earth_observation` | 6 |

93 objectives + 2 optional side actions = **95 validated actions**, ~117 minutes of
authored play. The Moon landing lives in the **Simulation Center**: the game never
pretends the Moon and Mars are the same place.

## Controls → endpoints

The client's key bindings are served by `GET /api/v1/content/controls`, so the
frontend and the backend cannot drift.

| Key | Action | Endpoint |
| --- | --- | --- |
| `W A S D` / `Shift` | move / sprint | `POST /games/{id}/tick` (`activity=walk|run`) |
| `Mouse` / `Space` / `V` | look / jump / camera toggle | *client only* |
| `E` | interact | `POST /games/{id}/missions/{mission_id}/actions` |
| `F` | use tool | `POST /games/{id}/inventory/use` |
| `Tab` | inventory | `GET /games/{id}/inventory` |

`GET …/interactions` returns the nearby world objects with `available`, `unmet` and
`hint` for each interaction, so the HUD can grey out an impossible action instead
of letting the player fail blindly — while the server still enforces the rule.

## Mission Continuity System

`GET /api/v1/games/{id}/continuity` explains every carry-over modifier: preparation
quality, tool maintenance, resource discipline and the training grade feed a
readiness score and the starting conditions of later stages. Each modifier carries
a label, a source mission and a delta, so a report can show *why* the rover started
at 80% battery. The modifiers are **gameplay balance, not physics** — and the
payload says so.

## Scientific honesty

Every data payload labels its provenance: `live`, `cache`, `bundled` or `simulated`.

* **Earth Trends** can query **NASA POWER** monthly climate data (no API key) for the
  temperature and precipitation layers; NDVI, aerosol and night-lights layers use a
  clearly-labelled educational model.
* **APOD / EPIC / DONKI / NEO / Mars photos** come from `api.nasa.gov` with a bundled
  offline snapshot as fallback (`DEMO_KEY` is heavily rate limited).
* **Crew vitals, the medical procedure and microgravity fire** are educational
  simulations. The disclaimer travels inside the payload, not only in the docs.

## Quality gates

```bash
bash tools/check_layout.sh    # 0.1 s, no install: will GitHub even see the workflow?
make check                    # from NOVA/backend
make text                     # every player-facing API string is English
bash tools/verify.sh          # same gates + socket probes, from the repo root
bash tools/verify.sh --live   # …and let the probes reach NASA
```

`tools/check_layout.sh` runs first and needs neither a virtualenv nor a network.
It exists because **five deliveries in a row** were pushed inside a wrapper
directory (`NOVA/`, `repo/`, `upstream/`, `repo/`, `repo/` again), which puts
`.github/workflows/backend.yml` below the git root — and GitHub then runs
*nothing*, silently. The guard rejects any wrapper name, a second nested copy,
and any buried workflow; `tools/restructure_repo.sh` fixes it with `git mv`.
Nine regression tests plant those exact mistakes in throwaway git repos.

`make check` runs ruff (lint + format), mypy, static mission-content validation, the
route audit (guards, typed responses, security blocks), the OpenAPI freshness check,
the offline pytest suite with coverage, and a full 15-mission campaign played through
public HTTP. CI (`.github/workflows/backend.yml`) runs the same gates in three jobs
and uploads the coverage and campaign reports. `tools/verify.sh` writes its output to
`verification/<date>/gates.log` so a run is always evidence, not a claim.

`make text` (`scripts/audit_player_text.py`) is the language gate for what a
player actually reads: it plays the campaign, breaks the rules on a second save,
tails the WebSocket feed, and reads **every string of every response** — 67/67
operations, 996 responses, 56k strings, zero non-English. Documentation is out of
scope by design. The same audit runs inside pytest and as a named CI step, and it
can be pointed at a running server with `--base-url`.

`tools/verify.sh` adds one gate CI deliberately skips, because CI must stay
hermetic: **the socket probes** (`make probes` / `make probes-offline`). pytest
drives the app in-process, so it cannot prove that uvicorn serves the console,
that a reconnecting WebSocket resumes from its cursor, or that `provenance: live`
really came from NASA. 12 probes offline, 14 with NASA — and NASA reachability is
reported, never required: a labelled `bundled` fallback is a pass.

The probes boot their own throwaway server. To check the server you are actually
looking at — a local `uvicorn` or a hosted preview — point the smoke harness at it:

```bash
cd NOVA/backend
.venv/bin/python ../../reference/scratch/preview_smoke.py --base-url http://127.0.0.1:8000
```

It asserts readiness, a console with zero external assets, and the canonical rule
(repair before closing the valve → `409 precondition_failed`), then completes the
mission in the correct order and reads the event log.

## Frontend integration

```bash
npx openapi-typescript NOVA/backend/docs/openapi.json -o src/api/nova.d.ts
```

* `POST /api/v1/games` → keep `game_id` (and `session_token` when auth is on).
* `GET /api/v1/games/{id}` → bootstrap the HUD from one snapshot.
* `POST /api/v1/games/{id}/tick` about once per second (**not every frame**).
* `E` → `POST …/missions/{mission_id}/actions` with an `Idempotency-Key` header.
* `WS /api/v1/games/{id}/stream?after_seq=N` for live events — a reconnect resumes
  from its cursor and never loses an event.

Planned client stack: React + Vite + Three.js / React Three Fiber + Rapier + Tailwind,
with Chart.js for the science and Earth panels. Assets in Blender.

## Documents

| Document | What |
| --- | --- |
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | Status of every layer, verification results, and the next step |
| [`MIGRATION.md`](MIGRATION.md) | Moving the layout from `repo/` to the root, and how to push it |
| [`NOVA/backend/README.md`](NOVA/backend/README.md) | Backend manual: contract, config, deployment, security |
| [`NOVA/backend/docs/routes.md`](NOVA/backend/docs/routes.md) | Generated route inventory (67 operations) |
| [`NOVA/backend/docs/openapi.json`](NOVA/backend/docs/openapi.json) | The published contract (OpenAPI 3.1, 64 paths, 136 schemas) |
| [`NOVA/backend/docs/verification-2026-11-12.md`](NOVA/backend/docs/verification-2026-11-12.md) | Dated verification snapshot (gates + live probes) |
| [`verification/README.md`](verification/README.md) | Index of the run logs and the campaign report |
| [`releases/README.md`](releases/README.md) | The delivery artefacts and how to regenerate them |
| `NOVA/backend/.env.example` | Every `NOVA_*` setting with its default |

## License

MIT.
