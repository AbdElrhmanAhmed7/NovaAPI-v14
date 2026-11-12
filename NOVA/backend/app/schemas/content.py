"""Static content contracts (catalogue, blueprints, controls, validation).

These models are the *typed* face of ``content/*.json``. The 3D client boots
against them, so every field a designer can author is documented in OpenAPI
and therefore in the generated TypeScript client.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.catalog import ItemDefinition, ResourceDefinition
from app.domain.enums import Environment, SimulationKind
from app.missions.definitions import (
    MissionDefinition,
    ScienceNote,
    WorldObject,
)
from app.schemas.common import Localised


class ItemView(ItemDefinition):
    """One inventory item definition (name, mass, wear, tags)."""


class ResourceView(ResourceDefinition):
    """One resource definition (unit, starting value, cap, critical level)."""


class ControlBinding(BaseModel):
    """A key binding and the endpoint that gives it meaning.

    ``api`` is ``null`` for purely client-side actions (camera, jump): the
    server never needs to hear about them.
    """

    keys: list[str] = Field(examples=[["W", "A", "S", "D"]])
    action: str = Field(examples=["move"])
    api: str | None = Field(
        default=None, examples=["POST /games/{id}/missions/{mission_id}/actions"]
    )


class ControlsResponse(BaseModel):
    controls: list[ControlBinding]
    note: Localised = Field(default_factory=Localised)


class CatalogueResponse(BaseModel):
    """Everything the client needs at boot: items, resources and bindings."""

    items: list[ItemView]
    resources: list[ResourceView]
    environments: list[Environment]
    simulations: list[SimulationKind]
    controls: list[ControlBinding]
    languages: list[str] = Field(
        default_factory=lambda: ["en"],
        description="Supported content languages. All player-facing content is English.",
    )


class MissionBlueprintSummary(BaseModel):
    """Designer-facing mission overview (no player state, no action rules)."""

    id: str
    stage: int = Field(ge=1, le=8)
    title: Localised
    summary: Localised
    environment: Environment
    briefing: list[Localised] = Field(default_factory=list)
    requires_missions: list[str] = Field(default_factory=list)
    simulation: SimulationKind | None = None
    objectives: list[str] = Field(default_factory=list, description="Objective ids, in order.")
    side_actions: list[str] = Field(default_factory=list)
    world_objects: list[WorldObject] = Field(default_factory=list)
    science_notes: list[ScienceNote] = Field(default_factory=list)
    estimated_minutes: int = 8
    disclaimer: str | None = None


class MissionBlueprint(MissionDefinition):
    """The full authored mission: objectives, conditions, effects and hazards.

    This is the same document the engine executes, so the frontend can
    pre-build the scene (world objects, prompts, animations) without guessing.
    """


class ValidationIssueView(BaseModel):
    severity: str = Field(examples=["error", "warning"])
    code: str = Field(examples=["unreachable_flag"])
    message: str
    mission_id: str | None = None
    where: str | None = None


class ContentValidationResponse(BaseModel):
    """Result of the static content audit (also runs at startup)."""

    ok: bool
    missions_checked: int
    actions_checked: int
    errors: int
    warnings: int
    issues: list[ValidationIssueView] = Field(default_factory=list)
