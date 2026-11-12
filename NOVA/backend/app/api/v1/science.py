"""Martian Guide – the in-game science database.

Samples are drilled during the mission (a mission action), then analysed and
logged here. Compositions are a clearly-labelled educational simulation
matched against curated NASA-derived mineral signatures.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.dependencies import GameId, GamesDep, ScienceDep, WriteGuard
from app.schemas.science import (
    SampleAnalyseRequest,
    SampleAnalysisResponse,
    SampleLogResponse,
    SampleRecord,
    ScienceDatabaseResponse,
    ScienceReferenceResponse,
)

router = APIRouter(tags=["Science"])


@router.get(
    "/science/reference",
    response_model=ScienceReferenceResponse,
    summary="Curated mineral signatures and sampling sites",
)
async def reference(science: ScienceDep) -> dict[str, Any]:
    return {
        "minerals": science.minerals(),
        "sites": science.sites(),
        "disclaimer": science.dataset["disclaimer"],
    }


@router.get(
    "/games/{game_id}/science",
    response_model=ScienceDatabaseResponse,
    summary="The player's science database",
)
async def database(game_id: GameId, science: ScienceDep, games: GamesDep) -> dict[str, Any]:
    await games.read_state(game_id)
    return await science.database_view(game_id)


@router.get(
    "/games/{game_id}/science/samples",
    response_model=list[SampleRecord],
    summary="Collected samples",
)
async def samples(game_id: GameId, science: ScienceDep, games: GamesDep) -> list[dict[str, Any]]:
    await games.read_state(game_id)
    return await science.list_samples(game_id)


@router.get(
    "/games/{game_id}/science/samples/{sample_id}",
    response_model=SampleRecord,
    summary="One sample with its composition and analysis",
)
async def sample(
    game_id: GameId, sample_id: str, science: ScienceDep, games: GamesDep
) -> dict[str, Any]:
    await games.read_state(game_id)
    return await science.get_sample(game_id, sample_id)


@router.post(
    "/games/{game_id}/science/samples/{sample_id}/analyse",
    response_model=SampleAnalysisResponse,
    summary="Run the spectrometer (tool condition affects confidence)",
    dependencies=[WriteGuard],
    responses={404: {"description": "Sample not found in this campaign"}},
)
async def analyse(game_id: GameId, sample_id: str, games: GamesDep) -> dict[str, Any]:
    return await games.analyse_sample(game_id, sample_id)


@router.post(
    "/games/{game_id}/science/samples/{sample_id}/log",
    response_model=SampleLogResponse,
    summary="Record the finding in the science database",
    dependencies=[WriteGuard],
    responses={
        404: {"description": "Sample not found in this campaign"},
        422: {"description": "Sample has not been analysed yet"},
    },
)
async def log_finding(
    game_id: GameId,
    sample_id: str,
    payload: SampleAnalyseRequest,
    games: GamesDep,
) -> dict[str, Any]:
    return await games.log_finding(game_id, sample_id, note=payload.note)
