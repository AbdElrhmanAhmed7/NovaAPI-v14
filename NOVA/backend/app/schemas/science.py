"""Martian Guide science contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DataProvenance, Habitability
from app.schemas.common import Localised


class MineralSignature(BaseModel):
    """A curated NASA-derived mineral signature (oxide ranges + spectra)."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: Localised
    signature: dict[str, list[float]] = Field(
        default_factory=dict, description="oxide id -> [min, max] weight percent."
    )
    spectral_bands: list[str] = Field(default_factory=list)
    interpretation: Localised = Field(default_factory=Localised)
    habitability: Habitability = Habitability.low
    source: str = ""
    url: str | None = None


class SamplingSite(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: Localised
    coordinates: dict[str, float] = Field(default_factory=dict, description="lat / lon on Mars.")
    weights: dict[str, float] = Field(
        default_factory=dict, description="Relative likelihood of each mineral at this site."
    )
    note: Localised = Field(default_factory=Localised)


class ScienceReferenceResponse(BaseModel):
    minerals: list[MineralSignature] = Field(default_factory=list)
    sites: list[SamplingSite] = Field(default_factory=list)
    disclaimer: str = ""


class MineralMatch(BaseModel):
    """One candidate identification produced by the spectrometer."""

    model_config = ConfigDict(extra="allow")

    mineral_id: str
    name: Localised
    confidence_pct: float = Field(ge=0, le=100)
    oxides_in_range: int = 0
    oxides_checked: int = 0
    spectral_bands: list[str] = Field(default_factory=list)
    habitability: Habitability = Habitability.low
    interpretation: Localised = Field(default_factory=Localised)
    source: str = ""
    url: str | None = None


class SampleAnalysisResponse(BaseModel):
    """Result of running the spectrometer on one drilled sample."""

    model_config = ConfigDict(extra="allow")

    sample_id: str
    status: str = Field(default="analysed")
    rock_type: str
    confidence_pct: float = Field(ge=0, le=100)
    best_match: MineralMatch | None = None
    candidates: list[MineralMatch] = Field(default_factory=list)
    habitability: Habitability = Habitability.low
    interpretation: Localised = Field(default_factory=Localised)
    spectral_bands: list[str] = Field(default_factory=list)
    source: str = ""
    url: str | None = None
    provenance: DataProvenance = DataProvenance.simulated
    method: str = ""
    disclaimer: str = ""
    analysed_at: str = ""
    spectrometer_condition: int = Field(default=100, ge=0, le=100)
    calibration_warning: str | None = Field(
        default=None, description="Present when a worn spectrometer reduced confidence."
    )


class SampleLogResponse(BaseModel):
    sample_id: str
    logged: bool = True
    rock_type: str
    confidence_pct: float = 0.0
    note: str = ""


class SampleRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    sample_id: str
    game_id: str
    mission_id: str
    site: str
    rock_type: str
    composition: dict[str, float] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class SampleAnalyseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(default="", max_length=500)


class ScienceDatabaseResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    game_id: str
    samples_total: int
    samples_analysed: int
    by_rock_type: dict[str, int] = Field(default_factory=dict)
    samples: list[SampleRecord] = Field(default_factory=list)
    mineral_reference: list[MineralSignature] = Field(default_factory=list)
    sites: list[SamplingSite] = Field(default_factory=list)
    disclaimer: str = ""
