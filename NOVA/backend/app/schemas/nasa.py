"""NASA open-data contracts.

Every NASA payload travels in the same envelope and **always** declares its
provenance, so the UI can print "live NASA data" or "offline snapshot" without
guessing. The ``data`` models describe the upstream NASA shapes; they allow
extra keys because NASA adds fields without notice.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DataProvenance


class NasaEnvelope(BaseModel):
    """Common wrapper: payload + where it came from + when it was fetched."""

    model_config = ConfigDict(extra="allow")

    provenance: DataProvenance = Field(
        description="live = fetched now, cache = TTL cache, bundled = curated offline snapshot."
    )
    source: str = Field(examples=["NASA APOD"])
    fetched_at: str = Field(description="ISO-8601 UTC timestamp of the fetch.")
    url: str | None = Field(default=None, description="Upstream URL with the API key masked.")
    note: str | None = Field(default=None, description="Human-readable provenance note.")
    reason: str | None = Field(default=None, description="Why live data was not used.")
    stale: bool = Field(default=False, description="True when an expired cache entry was served.")


class ApodData(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    date: str
    explanation: str = ""
    media_type: str = "image"
    url: str = ""
    hdurl: str | None = None
    thumbnail_url: str | None = None
    copyright: str | None = None
    service_version: str | None = None


class ApodResponse(NasaEnvelope):
    data: ApodData


class MarsCamera(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | None = None
    name: str = ""
    rover_id: int | None = None
    full_name: str = ""


class MarsRover(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | None = None
    name: str = ""
    landing_date: str | None = None
    launch_date: str | None = None
    status: str | None = None


class MarsPhoto(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    sol: int
    img_src: str
    earth_date: str
    camera: MarsCamera = Field(default_factory=MarsCamera)
    rover: MarsRover = Field(default_factory=MarsRover)


class MarsPhotosData(BaseModel):
    model_config = ConfigDict(extra="allow")

    photos: list[MarsPhoto] = Field(default_factory=list)


class MarsPhotosResponse(NasaEnvelope):
    data: MarsPhotosData


class DonkiResponse(NasaEnvelope):
    """DONKI events. The shape depends on ``kind`` (GST, CME, FLR, …)."""

    data: list[dict[str, Any]] = Field(default_factory=list)


class NeoFeedData(BaseModel):
    model_config = ConfigDict(extra="allow")

    element_count: int = 0
    near_earth_objects: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict, description="Keyed by date (YYYY-MM-DD)."
    )


class NeoResponse(NasaEnvelope):
    data: NeoFeedData


class EpicCoordinates(BaseModel):
    model_config = ConfigDict(extra="allow")

    lat: float = 0.0
    lon: float = 0.0


class EpicImage(BaseModel):
    model_config = ConfigDict(extra="allow")

    identifier: str
    caption: str = ""
    image: str = ""
    date: str = ""
    centroid_coordinates: EpicCoordinates = Field(default_factory=EpicCoordinates)


class EpicResponse(NasaEnvelope):
    data: list[EpicImage] = Field(default_factory=list)


class NasaCacheStats(BaseModel):
    entries: int = 0
    hits: int = 0
    misses: int = 0


class NasaStatusResponse(BaseModel):
    enabled: bool
    api_key_configured: bool
    cache: NasaCacheStats = Field(default_factory=NasaCacheStats)
    note: str = ""
