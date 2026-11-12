"""Earth Trends – Earth Observation Station.

Region + layer + two periods in, change detection out. Every payload states
its provenance (``live`` NASA POWER, ``cache``, or a labelled educational model).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.dependencies import EarthDep, GameId, GamesDep, WriteGuard
from app.schemas.earth import (
    EarthCatalogueResponse,
    EarthCompareRequest,
    EarthComparisonResponse,
    EarthReportRequest,
    EarthReportResponse,
)

router = APIRouter(tags=["Earth Trends"])


@router.get(
    "/earth/catalogue",
    response_model=EarthCatalogueResponse,
    summary="Regions, layers, periods and live-data capability",
)
async def catalogue(earth: EarthDep) -> dict[str, Any]:
    return earth.catalogue()


@router.post(
    "/earth/compare",
    response_model=EarthComparisonResponse,
    summary="Compare two periods (preview, nothing saved)",
    responses={422: {"description": "Unknown region/layer/period, or identical periods"}},
)
async def compare(payload: EarthCompareRequest, earth: EarthDep) -> dict[str, Any]:
    return await earth.compare(
        region_id=payload.region_id,
        layer_id=payload.layer_id,
        period_before=payload.period_before,
        period_after=payload.period_after,
        live=payload.live,
        grid_size=payload.grid_size,
    )


@router.post(
    "/games/{game_id}/earth/reports",
    response_model=EarthReportResponse,
    summary="Run the analysis and archive it as a mission report",
    dependencies=[WriteGuard],
)
async def save_report(
    game_id: GameId, payload: EarthReportRequest, earth: EarthDep, games: GamesDep
) -> dict[str, Any]:
    # Validates the session before doing any (potentially slow) analysis.
    await games.read_state(game_id)
    analysis = await earth.compare(
        region_id=payload.region_id,
        layer_id=payload.layer_id,
        period_before=payload.period_before,
        period_after=payload.period_after,
        live=payload.live,
        grid_size=payload.grid_size,
    )
    async with games.transaction(game_id) as tx:
        report = await earth.save_report(
            game_id, analysis, repository=tx.earth, title=payload.title
        )
        tx.state.report_ids.append(report["report_id"])
        tx.recorder.emit(
            "earth.report_saved",
            {**report, "title": payload.title},
            mission_id="earth_trends",
        )
    # Read the stored row back so POST and GET return the *same* document
    # (titled analysis, provenance and created_at), not two different shapes.
    return await earth.get_report(game_id, report["report_id"])


@router.get(
    "/games/{game_id}/earth/reports",
    response_model=list[EarthReportResponse],
    summary="Archived Earth reports for this campaign",
)
async def list_reports(game_id: GameId, earth: EarthDep, games: GamesDep) -> list[dict[str, Any]]:
    await games.read_state(game_id)
    return await earth.list_reports(game_id)


@router.get(
    "/games/{game_id}/earth/reports/{report_id}",
    response_model=EarthReportResponse,
    summary="One archived Earth report (full grids)",
    responses={404: {"description": "Report not found in this campaign"}},
)
async def get_report(
    game_id: GameId, report_id: str, earth: EarthDep, games: GamesDep
) -> dict[str, Any]:
    await games.read_state(game_id)
    return await earth.get_report(game_id, report_id)
