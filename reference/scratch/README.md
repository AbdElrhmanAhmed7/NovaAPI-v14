# scratch – developer harnesses

Two small harnesses live here. Neither is imported by `app/`, `scripts/` or `tests/`,
and neither is part of CI: they exist to answer questions the gates cannot.

## `capture.py` – payload capture

`capture.py` plays a short campaign in-process and dumps the **real** response
body of every endpoint whose schema still exposes a free-form object (telemetry,
legacy dictionaries, simulation scenes, Earth grids…). It was used to decide
which of those payloads deserved a proper pydantic model.

```bash
cd NOVA/backend
.venv/bin/python ../../reference/scratch/capture.py     # writes payloads.json here
```

The generated `payloads.json` / `payloads_baseline.json` dumps (~290 KB each) are
**not committed** – they are reproducible artefacts, and the diff between two
runs is only interesting while you are actively typing a schema. Regenerate them
when you need a before/after comparison; nothing in `app/`, `scripts/` or `tests/`
reads them.

The authoritative, always-fresh contract is
[`NOVA/backend/docs/openapi.json`](../../NOVA/backend/docs/openapi.json), and
`python scripts/audit_routes.py --check` is the gate that reports how many
operations still expose free-form fields.

## `preview_smoke.py` – smoke-test a server that is already running

`scripts/live_probes.py` boots its **own** uvicorn on a free port with a throwaway
database, which is right for CI evidence but says nothing about the server a human
is looking at (a local `uvicorn`, or a hosted preview). This harness takes a
`--base-url` and checks what a player notices first:

* `GET /api/v1/ready` — database, content pack, mission and item counts;
* `GET /` — the Mission Control console, and that it references **no external
  assets**, because a sandboxed preview iframe has no network;
* a campaign is created and the two prerequisite missions are played over HTTP;
* the canonical water-leak rule: `repair_pipe` **before** `close_valve` must be
  refused with `409 precondition_failed` (a guard that stops rejecting is a bug);
* the correct order completes the mission, and the event log recorded it.

```bash
cd NOVA/backend
# the database stays under data/, which .gitignore already covers
NOVA_DATABASE_URL="sqlite+aiosqlite:///./data/nova-preview.db" \
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# in a second shell
.venv/bin/python ../../reference/scratch/preview_smoke.py --base-url http://127.0.0.1:8000
```

Exit status is non-zero if any check fails. The committed output of the last run is
[`verification/2026-11-12-final/preview-smoke.log`](../../verification/2026-11-12-final/preview-smoke.log).
