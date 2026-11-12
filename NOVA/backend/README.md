# NOVA – Beyond the Mission · Gameplay Backend

**Explore. Experience. Execute.**

Authoritative gameplay backend for the NOVA 3D web game (React + Vite + Three.js /
React Three Fiber + Rapier). The client owns movement, camera and animation; this
service owns **mission rules, resources, inventory, physics simulations,
continuity and persistence**.

> Not a dashboard and not a quiz. Every mission is *move → interact →
> consequence*: the player walks to a device, picks an action, and the server
> validates the preconditions and mutates the world. Repairing a pressurised
> pipe **fails**, wastes a kit and costs water.

| | |
| --- | --- |
| Stages / missions | **8 stages · 15 missions** (Training → Earth Analog → Mars → Crew Health → Freefall Fire → Martian Guide → Lunar Landing → Earth Trends) |
| HTTP surface | **67 published operations** + 1 WebSocket, 136 schemas – see [`docs/routes.md`](docs/routes.md) |
| Simulations | lunar lander, microgravity fire, crew vitals, rover traverse, Earth change detection |
| Persistence | SQLAlchemy 2 async · SQLite by default · Alembic migrations |
| Quality gates | pytest (**449 tests**, 93% coverage) · ruff · mypy · content validation · route audit · full campaign playthrough |
| Languages | English (`en`); the legacy `ar` field is an English compatibility alias |

---

## Quickstart

```bash
cd NOVA/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | What |
| --- | --- |
| `http://localhost:8000/` | **Mission Control** – a dependency-free dev console that plays the API (HUD, missions, simulators, event stream, service diagnostics) |
| `http://localhost:8000/docs` | Swagger UI (with an *Authorize* button for the session token) |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/openapi.json` | the published contract |
| `http://localhost:8000/api/v1/health` | liveness (cheap, rate-limit exempt) |
| `http://localhost:8000/api/v1/ready` | readiness: database, content pack, **stream fan-out**, NASA cache |

No `.env` needed for development: tables are created on startup and NASA access
falls back to the bundled sample payloads. Copy `.env.example` to `.env` when you
want to change anything.

Play the whole campaign without a browser:

```bash
python scripts/playthrough.py --in-process --difficulty standard --seed 7
```

---

## The gameplay contract

### Controls → endpoints

The client's key bindings are served by `GET /api/v1/content/controls`, so the
frontend and the backend cannot drift:

| Key | Action | Endpoint |
| --- | --- | --- |
| `W A S D` | move | `POST /games/{id}/tick` (`activity=walk`) |
| `Shift` | sprint | `POST /games/{id}/tick` (`activity=run`) |
| `Mouse` | look | *client only* |
| `Space` | jump | *client only* |
| `E` | interact | `POST /games/{id}/missions/{mission_id}/actions` |
| `F` | use tool | `POST /games/{id}/inventory/use` |
| `V` | first ↔ third person | *client only* |
| `Tab` | inventory | `GET /games/{id}/inventory` |

### Move → interact → consequence

`GET /games/{id}/missions/{mission_id}/interactions` returns the world objects
near the player, each with the interactions it offers **and whether they are
currently available** (`available`, `unmet`, `hint`). The HUD can grey out an
action instead of letting the player fail blindly – but the server still
enforces the rule if the client sends it anyway.

The water leak, end to end (real responses – shown on the **legacy**
Prototype-01 URL, which is a second door into the same engine and the same
guard; new clients use `/api/v1/games/{id}/missions/analog_water_leak/actions`):

```console
$ curl -sX POST localhost:8000/games/$GID/missions/water-leak/actions \
       -H 'Content-Type: application/json' -d '{"action":"repair_pipe"}'
409 {"detail": "The line is still pressurised – water sprays across the bay. Close the valve first."}

$ ... -d '{"action":"scan_leak"}'        # 200 Leak located at joint B-7, upstream of the valve.
$ ... -d '{"action":"close_valve"}'      # 200 Valve closed. Branch pressure dropping to zero.
$ ... -d '{"action":"repair_pipe"}'      # 200 Joint sealed. Leak stopped and 12 L recovered.
$ ... -d '{"action":"verify_pressure"}'  # 200 Loop holds pressure. Water system certified.
```

Water goes `88 → 88 → 100 → 100` litres: the resource change is a *consequence*
of the repair, not a number the client is allowed to set.

### Mission Continuity System

`GET /games/{id}/continuity` explains every carry-over modifier: preparation
quality, tool maintenance, resource discipline and training grade feed the
readiness score and the starting conditions of later stages. Nothing is hidden
in a magic multiplier – each modifier carries a label, a source mission and a
delta, so the mission report can show *why* the rover started at 80% battery.

### Live events & service diagnostics

`WS /api/v1/games/{id}/stream?after_seq=N` is a **read model** over the persisted
event log: the in-process bus is only a doorbell, so a reconnect (or a slow
consumer that gets `stream.resync`) refills from its cursor and never loses an
event. `GET /api/v1/ready` exposes the same machinery to an operator:

```json
{
  "status": "ready", "database": true, "content": true,
  "missions_loaded": 15, "items_loaded": 26,
  "nasa_cache": {"entries": 0, "hits": 0, "misses": 0},
  "stream": {"games_watched": 1, "subscribers": 2,
             "published_events": 37, "published_batches": 9},
  "db_calls_in_flight": 0
}
```

Both blocks are typed (`StreamStats`, `NasaCacheStats`), so the generated client
gets real fields instead of `Record<string, unknown>`. The console's **Service**
card renders them and re-probes whenever a socket opens or closes – the
subscriber count you see is the socket you just opened. Counters are
process-local: with several workers each one reports its own fan-out.

### Scientific honesty

Every payload that contains data labels its provenance: `live`, `cache`,
`bundled` or `simulated`. Educational simplifications (crew vitals, microgravity
fire, medical procedure) are stated in the payload itself, not only in the docs.
The lunar lander lives in the **Simulation Center** – the game never pretends
the Moon and Mars are the same place.

---

## Layout

```
backend/
├── app/
│   ├── main.py                 application factory, lifespan, error envelope
│   ├── config.py               NOVA_* settings (pydantic-settings)
│   ├── api/
│   │   ├── v1/                 meta, content, games, missions, loadout,
│   │   │                       simulations, earth, science, nasa, stream
│   │   └── legacy.py           Prototype-01 URLs (deprecated, same engine)
│   ├── core/                   logging, errors, middleware, security,
│   │   │                       rate limit, cache, locks, openapi, routing
│   │   └── routing.py          route introspection (FastAPI ≥0.160 safe)
│   ├── db/                     models, repositories, async session
│   ├── domain/                 enums, GameState document, catalogues
│   ├── missions/               definitions, engine, registry, validation
│   ├── simulations/            lunar_lander, microgravity_fire, crew_vitals,
│   │                           rover_traverse, earth_trends
│   ├── systems/                resources, inventory, oxygen, scoring,
│   │                           continuity, achievements, events
│   ├── services/               game / simulation / earth / science services
│   ├── schemas/                pydantic request & response models
│   └── static/index.html       Mission Control dev console
├── content/                    mission blueprints, items, resources,
│                               achievements, science, NASA fallbacks
├── migrations/                 Alembic environment + revisions
├── scripts/                    see below
├── tests/                      449 tests, no network required
└── docs/                       openapi.json, routes.md, routes.json,
                                verification-2026-11-12.md
```

The CI workflow lives at the **repository root** (`.github/workflows/backend.yml`)
and filters on `NOVA/backend/**` – GitHub ignores nested `.github` directories,
so a workflow parked next to the service would never run.

### Scripts

| Script | Purpose |
| --- | --- |
| `scripts/playthrough.py` | plays all 15 missions over public HTTP; `--in-process`, `--base-url`, `--json` |
| `scripts/autopilot.py` | the campaign driver used by the playthrough and the test fixtures |
| `scripts/validate_content.py` | static mission checks (reachability, references, i18n); `--strict` |
| `scripts/audit_routes.py` | route inventory + contract gate; writes `docs/routes.md` / `.json` |
| `scripts/export_openapi.py` | writes `docs/openapi.json`; `--check` fails on drift |
| `scripts/live_probes.py` | boots a real uvicorn and probes HTTP + WebSocket + NASA; `--no-live`, `--json` |
| `scripts/audit_player_text.py` | plays the campaign + failure paths and scans every API string for non-English text; `--base-url`, `--json`, `--markdown` |

---

## Configuration

Everything is optional and prefixed `NOVA_` – see [`.env.example`](.env.example).
The ones that matter most:

| Variable | Default | Notes |
| --- | --- | --- |
| `NOVA_DATABASE_URL` | `sqlite+aiosqlite:///./data/nova.db` | any SQLAlchemy async URL |
| `NOVA_AUTO_CREATE_TABLES` | `true` | set `false` in production and run Alembic |
| `NOVA_AUTH_ENABLED` | `false` | `true` ⇒ writes need `X-Session-Token` |
| `NOVA_FRONTEND_ORIGINS` | Vite dev servers | comma separated |
| `NOVA_CORS_ORIGIN_REGEX` | preview hosts | e.g. `https://.*\.e2b\.app` |
| `NOVA_RATE_LIMIT_REQUESTS` | `1800` / 60 s | health endpoints are exempt |
| `NOVA_NASA_API_KEY` | `DEMO_KEY` | get a free key at api.nasa.gov |
| `NOVA_NASA_ENABLED` | `true` | `false` ⇒ bundled fallbacks, no network |
| `NOVA_VALIDATE_CONTENT_STRICT` | `true` | refuse to boot on an unwinnable mission |

### Persistence & migrations

```bash
alembic upgrade head                     # production deploy
alembic revision --autogenerate -m "…"   # after changing app/db/models.py
alembic -x url=sqlite+aiosqlite:///./data/other.db upgrade head
```

`tests/test_migrations.py` asserts that `alembic upgrade head` and
`Base.metadata.create_all` produce **identical** schemas, that no autogenerate
diff is pending, and that the app really runs on a migrated database with
`auto_create_tables=False`.

The mutable save is one JSON document (`game_sessions.state`); events, mission
results, science samples, simulation runs and Earth reports are normalised
tables so they can be queried across campaigns.

---

## Quality gates

```bash
pytest                                   # 451 tests, offline
pytest --cov=app --cov-report=term-missing
ruff check . && ruff format --check .
mypy app scripts
python scripts/validate_content.py --strict
python scripts/audit_routes.py --check
python scripts/export_openapi.py --check
python scripts/playthrough.py --in-process
python scripts/audit_player_text.py       # player-facing API text is English
```

`make check` runs all of them. The last full run (dependency versions, gate
output, campaign table and live NASA probes) is recorded in
[`docs/verification-2026-11-12.md`](docs/verification-2026-11-12.md).

There is one gate `make check` deliberately does **not** run, because CI must
stay hermetic: the socket probes.

```bash
make probes           # real uvicorn, real WebSocket client, live NASA
make probes-offline   # same, no outbound calls (deterministic)
```

`pytest` drives the app in-process, so it cannot prove that uvicorn serves the
console, that a reconnecting WebSocket resumes from its cursor, or that
`provenance: live` really came from NASA. `scripts/live_probes.py` is that other
half of the evidence: 12 probes offline, 14 with NASA. NASA reachability is
reported, never required – a labelled `bundled` fallback is a pass.

The same "docs are a deliverable" rule applies to the repository root:
`tests/test_project_docs.py` fails if the campaign table in `README.md` or
`PROJECT_STATUS.md` drifts from `content/missions/`, if a local link stops
resolving, or if the status report quotes a stale operation/schema count from
`docs/openapi.json`.

### The route audit

FastAPI ≥ 0.160 no longer flattens `include_router()` into `app.routes`: an
included router stays a nested `_IncludedRouter` with **no `.routes` attribute**.
Any hand-written walk silently sees 9 of 75 routes here. That is not cosmetic –
`core.openapi` used it to find guarded handlers, so the published contract had
**no `security` block and no `401` responses** even though the handlers were
protected.

`app/core/routing.py` is now the only place that enumerates routes (it handles
both FastAPI layouts, mounts and websockets), and `scripts/audit_routes.py`
turns it into a gate:

* a published endpoint without a `response_model`,
* a game-scoped write without `require_game_write` (**this caught a real auth
  bypass on the legacy water-leak URL**),
* a guarded operation whose OpenAPI lost `security` / `401` / `409`,
* auth headers leaking into `parameters`,
* any disagreement between the walker and the published document.

---

## Frontend integration

```bash
# typed client from the committed contract
npx openapi-typescript docs/openapi.json -o src/api/nova.d.ts
```

1. `POST /api/v1/games` → keep `game_id` (and `session_token` when auth is on).
2. `GET /api/v1/games/{id}` → bootstrap the HUD from one snapshot.
3. `POST /api/v1/games/{id}/tick` ~1×/second (**not every frame**) with the
   player's activity; `dt_seconds` is clamped server-side.
4. `E` → `POST …/missions/{mission_id}/actions`, with an `Idempotency-Key`
   header so a retried request replays the first result instead of firing twice.
5. Subscribe to `WS /api/v1/games/{id}/stream?after_seq=N` for events, or poll
   `GET …/events?after_seq=N`. The stream is a read model: a reconnect never
   loses an event.

Errors always use one envelope (legacy Prototype-01 URLs excepted):

```json
{
  "error": {
    "code": "precondition_failed",
    "message": "Close the isolation valve before repairing the pipe.",
    "details": {"unmet": [{"type": "flag", "id": "leak.valve_closed"}]}
  },
  "request_id": "req_6f1c2a9b"
}
```

`GET /openapi.json` carries `x-error-codes` – the full vocabulary for the
frontend's switch statement.

### Security model

* Writes are guarded per session; reads are public if you know the id.
* The `game_id` is an unguessable capability, and `POST …/resume` re-issues its
  token – that is the "returning player" path, so treat a save id as a secret.
* Turn `NOVA_AUTH_ENABLED=true` before exposing the service publicly, set
  `NOVA_SECRET_KEY`, and keep the rate limiter on.
* The legacy `/games/...` URLs run the **same** guard as v1.

---

## Deployment

```bash
docker build -t nova-backend .
docker run -p 8000:8000 -e NOVA_AUTH_ENABLED=true -e NOVA_SECRET_KEY=… nova-backend
# or
docker compose up --build
```

The image runs `alembic upgrade head` before uvicorn, as a non-root user, with a
health check against `/api/v1/health`. Behind a proxy, forward `X-Request-ID`
and keep websockets enabled for `/api/v1/games/*/stream`.

---

## Summary

This backend is the **authoritative source of gameplay state**: the frontend
(React Three Fiber) moves and renders animation, while the server decides whether
an action is allowed and changes the resources. No mission is solved by answering a
question: the player must move to the device, interact, and the consequence is
computed on the server (repairing the pipe before closing the valve → failure +
water loss).

* 8 stages and 15 missions, with all player-facing content in English.
* The Mission Continuity System explains every modifier and its source; there are
  no hidden numbers.
* The simulators (lunar landing, microgravity fire, crew vitals, rover) are computed
  on the server, and every data payload labels its source: `live` / `cache` /
  `bundled` / `simulated`. Educational simplifications are stated inside the payload.
* Run it with `uvicorn app.main:app --reload`, then open `/` (Mission Control) or
  `/docs`.
* The console's **Service** card reads `/api/v1/ready`: database, content, active
  stream sockets and published events. It refreshes automatically whenever a socket
  opens or closes.
* Full verification: `make check` (tests + ruff + mypy + content validation + route
  audit + full campaign playthrough).

---

## License

MIT.
