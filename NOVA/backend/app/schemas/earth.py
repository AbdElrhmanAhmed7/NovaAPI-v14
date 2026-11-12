"""Earth Trends contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Localised
from app.simulations.earth_trends import GridCell, LayerSpec, RegionSpec


class EarthCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: str = Field(default="nile_delta", examples=["nile_delta", "amazon_basin"])
    layer_id: str = Field(default="ndvi", examples=["ndvi", "lst", "precipitation"])
    period_before: str = Field(default="2001-2005", pattern=r"^\d{4}-\d{4}$")
    period_after: str = Field(default="2021-2025", pattern=r"^\d{4}-\d{4}$")
    live: bool = Field(
        default=False, description="Try NASA POWER live data first (falls back to bundled)."
    )
    grid_size: int = Field(default=8, ge=4, le=16)


class EarthReportRequest(EarthCompareRequest):
    title: str = Field(default="", max_length=120)


class EarthRegionView(RegionSpec):
    """One selectable region (bounding box, biome, English name)."""


class EarthLayerView(LayerSpec):
    """One environmental layer and the NASA product behind it."""


class EarthCatalogueResponse(BaseModel):
    regions: list[EarthRegionView]
    layers: list[EarthLayerView]
    periods: list[str] = Field(examples=[["2001-2005", "2021-2025"]])
    default_comparison: list[str] = Field(default_factory=list)
    live_capable_layers: list[str] = Field(default_factory=list)
    note: Localised = Field(default_factory=Localised)


class GridStatistics(BaseModel):
    mean: float = 0.0
    min: float = 0.0
    max: float = 0.0
    std: float = 0.0


class PeriodStatistics(BaseModel):
    before: GridStatistics = Field(default_factory=GridStatistics)
    after: GridStatistics = Field(default_factory=GridStatistics)


class Hotspot(BaseModel):
    """One cell whose change is worth showing on the 3D globe."""

    row: int
    col: int
    lat: float
    lon: float
    before: float
    after: float
    delta: float
    delta_pct: float


class EarthAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    region: str
    layer: str
    unit: str
    period_before: str
    period_after: str
    provenance: str
    mean_delta: float
    mean_delta_pct: float
    direction: str
    verdict: str
    cells_changed: int
    cells_improved: int
    cells_degraded: int
    hotspots: list[Hotspot] = Field(default_factory=list)
    narrative: dict[str, str] = Field(default_factory=dict)
    nasa_product: str = ""
    nasa_url: str = ""
    title: str | None = Field(default=None, description="Set on archived reports.")


class EarthComparisonResponse(EarthAnalysisResponse):
    """Full change-detection result, including both grids for the heatmap."""

    region_name: Localised = Field(default_factory=Localised)
    layer_name: Localised = Field(default_factory=Localised)
    biome: str = ""
    requested_live: bool = False
    live_supported: bool = False
    live_fallback_reason: str | None = None
    sources: list[str] = Field(default_factory=list)
    statistics: PeriodStatistics = Field(default_factory=PeriodStatistics)
    grid_before: list[GridCell] = Field(default_factory=list)
    grid_after: list[GridCell] = Field(default_factory=list)


class EarthReportResponse(BaseModel):
    """An archived Earth Trends report (the mission deliverable)."""

    model_config = ConfigDict(extra="allow")

    report_id: str
    game_id: str
    region_id: str
    layer_id: str
    period_before: str
    period_after: str
    provenance: str = Field(
        description="live | cache | bundled | simulated | mixed (periods differ in provenance)."
    )
    verdict: str
    mean_delta: float
    analysis: EarthAnalysisResponse | None = Field(default=None)
    created_at: str | None = Field(default=None, description="Present on stored reports.")
