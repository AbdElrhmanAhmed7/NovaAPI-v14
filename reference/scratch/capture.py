"""Scratch harness: capture real payloads for every weakly-typed endpoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # reference/scratch
REPO = HERE.parents[1]                          # repository root
ROOT = REPO / "NOVA" / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from autopilot import Autopilot  # noqa: E402

OUT = HERE / "payloads.json"
DB = HERE / "capture.db"
if DB.exists():
    DB.unlink()

settings = Settings(
    environment="test",
    database_url=f"sqlite+aiosqlite:///{DB}",
    nasa_enabled=False,
    rate_limit_enabled=False,
    log_level="ERROR",
    log_json=False,
    content_dir=ROOT / "content",
    _env_file=None,
)

payloads: dict[str, object] = {}

with TestClient(create_app(settings)) as client:
    pilot = Autopilot(client=client)
    pilot.run_campaign(player_name="Capture", difficulty="standard", seed=7)
    gid = pilot.game_id

    def grab(name: str, method: str, url: str, body: dict | None = None) -> None:
        response = (
            client.get(url) if method == "GET" else client.post(url, json=body or {})
        )
        payloads[name] = {
            "url": url,
            "method": method,
            "status": response.status_code,
            "body": response.json() if response.content else None,
        }
        print(f"{response.status_code} {method} {url}")

    grab("content.catalogue", "GET", "/api/v1/content/catalogue")
    grab("content.items", "GET", "/api/v1/content/items")
    grab("content.resources", "GET", "/api/v1/content/resources")
    grab("content.missions", "GET", "/api/v1/content/missions")
    grab("content.mission", "GET", "/api/v1/content/missions/analog_water_leak")
    grab("content.controls", "GET", "/api/v1/content/controls")
    grab("content.validation", "GET", "/api/v1/content/validation")
    grab("suit", "GET", f"/api/v1/games/{gid}/suit")
    grab("suit.eva", "POST", f"/api/v1/games/{gid}/suit/eva", {"active": True})
    grab("sim.scene.lander", "GET", f"/api/v1/games/{gid}/missions/lunar_landing/simulation/scene")
    grab("sim.scene.rover", "GET", f"/api/v1/games/{gid}/missions/mars_rover_traverse/simulation/scene")
    grab("sim.scene.fire", "GET", f"/api/v1/games/{gid}/missions/freefall_fire/simulation/scene")
    grab("sim.scene.vitals", "GET", f"/api/v1/games/{gid}/missions/crew_health/simulation/scene")
    grab("earth.catalogue", "GET", "/api/v1/earth/catalogue")
    grab(
        "earth.compare",
        "POST",
        "/api/v1/earth/compare",
        {"region_id": "nile_delta", "layer_id": "ndvi", "period_before": "2001-2005",
         "period_after": "2021-2025", "live": False, "grid_size": 6},
    )
    reports = client.get(f"/api/v1/games/{gid}/earth/reports").json()
    if reports:
        rid = reports[0]["report_id"]
        grab("earth.report", "GET", f"/api/v1/games/{gid}/earth/reports/{rid}")
    grab("science.reference", "GET", "/api/v1/science/reference")
    samples = client.get(f"/api/v1/games/{gid}/science/samples").json()
    if samples:
        sid = samples[0]["sample_id"]
        grab("science.analyse", "POST", f"/api/v1/games/{gid}/science/samples/{sid}/analyse")
        grab("science.log", "POST", f"/api/v1/games/{gid}/science/samples/{sid}/log", {"note": "capture"})
    for name in ("status", "apod", "mars-photos", "donki", "neo", "epic"):
        grab(f"nasa.{name}", "GET", f"/api/v1/nasa/{name}")
    grab("legacy.resources", "GET", f"/games/{gid}/resources")
    grab("legacy.inventory", "GET", f"/games/{gid}/inventory")
    grab("sim.descriptors", "GET", "/api/v1/simulations")
    grab("game.snapshot", "GET", f"/api/v1/games/{gid}")

OUT.write_text(json.dumps(payloads, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nwrote {OUT} ({OUT.stat().st_size} bytes)")