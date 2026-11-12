"""Inventory, resources and suit contracts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UseItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=48, examples=["oxygen_tank"])
    target: str | None = Field(
        default=None, description="Optional target, e.g. the tool to service."
    )


class MaintainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=48, examples=["scanner"])


class ItemUseResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    item: str
    message: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    inventory: dict[str, int] = Field(default_factory=dict)
    resources: dict[str, int] = Field(default_factory=dict)
    equipment: dict[str, Any] = Field(default_factory=dict)


class MaintenanceResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    item: str
    condition: int
    method: str
    inventory: dict[str, int] = Field(default_factory=dict)
    resources: dict[str, int] = Field(default_factory=dict)


class EquipmentView(BaseModel):
    """Wear & tear of one durable tool (feeds the Mission Continuity System)."""

    condition: int = Field(default=100, ge=0, le=100)
    maintenance_count: int = 0
    failure_count: int = 0
    calibrated: bool = True


class DegradedTool(BaseModel):
    item: str
    condition: int = Field(ge=0, le=100)


class InventoryCapacity(BaseModel):
    distinct_items: int = 0
    total_units: int = 0
    mass_kg: float = 0.0


class InventoryResponse(BaseModel):
    game_id: str
    inventory: dict[str, int] = Field(description="item_id -> quantity carried.")
    equipment: dict[str, EquipmentView] = Field(default_factory=dict)
    degraded_tools: list[DegradedTool] = Field(default_factory=list)
    capacity: InventoryCapacity = Field(default_factory=InventoryCapacity)


class ResourcesResponse(BaseModel):
    game_id: str
    resources: dict[str, int]
    caps: dict[str, int] = Field(default_factory=dict)
    ratios: dict[str, float] = Field(default_factory=dict)
    critical: list[str] = Field(default_factory=list)


class SuitView(BaseModel):
    integrity: int = Field(default=100, ge=0, le=100)
    eva_active: bool = False
    co2_filter: int = Field(default=100, ge=0, le=100)
    oxygen_alarm: str | None = Field(
        default=None, description="Set when oxygen crosses a warning threshold."
    )
    last_activity: str = "idle"


class SuitResponse(BaseModel):
    """Everything the suit HUD needs: integrity, EVA state and drain rates."""

    game_id: str
    suit: SuitView
    oxygen: int
    oxygen_cap: int
    drain_rates: dict[str, float] = Field(
        default_factory=dict, description="Oxygen units per second, keyed by activity."
    )
    environment: str


class EvaResponse(BaseModel):
    eva_active: bool
    oxygen: int
