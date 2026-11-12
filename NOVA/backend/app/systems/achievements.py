"""Achievements – long-horizon goals that span the whole campaign.

Design rules
------------
* **Data driven**: every badge lives in ``content/achievements.json``; adding one
  never touches Python.
* **Derived, never authoritative**: a badge is a *view* over the save document
  (mission progress, resources, stats, simulation results). Nothing in the
  gameplay economy depends on it, so a bad content edit cannot break a mission.
* **Idempotent**: :meth:`AchievementSystem.sync` unlocks a badge once, records
  the timestamp in the save and emits ``achievement.unlocked``. Re-evaluating
  after every mutation is therefore cheap and safe.
* **Explainable**: locked badges report ``progress_pct`` so the HUD can show a
  filling bar instead of a mystery padlock.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.errors import ContentError
from app.core.utils import clamp, round_to
from app.domain.catalog import LocalisedText
from app.domain.enums import MissionStatus
from app.domain.state import AchievementUnlock, GameState
from app.missions.definitions import MissionLibrary
from app.systems.events import EventRecorder

CriteriaType = Literal[
    "mission_completed",
    "stage_completed",
    "missions_completed",
    "campaign_completed",
    "flawless_mission",
    "grade_at_least",
    "readiness_at_least",
    "resource_at_least",
    "resources_at_least",
    "samples_collected",
    "samples_logged",
    "simulation_score",
    "distance_travelled",
    "eva_time",
    "tool_condition",
    "mistakes_at_most",
]

#: Grade ordering used by ``grade_at_least`` (S is best).
GRADE_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}


class AchievementCriteria(BaseModel):
    """One testable condition. Unused fields stay ``None``."""

    type: CriteriaType
    mission_id: str | None = None
    stage: int | None = None
    count: int | None = Field(default=None, ge=1)
    value: float | None = None
    grade: str | None = None
    resource: str | None = None
    values: dict[str, float] = Field(default_factory=dict)
    min_missions: int = 0
    min_tools: int = 0

    @model_validator(mode="after")
    def _require_arguments(self) -> AchievementCriteria:
        required: dict[str, tuple[str, ...]] = {
            "mission_completed": ("mission_id",),
            "flawless_mission": ("mission_id",),
            "grade_at_least": ("mission_id", "grade"),
            "stage_completed": ("stage",),
            "missions_completed": ("count",),
            "readiness_at_least": ("value",),
            "resource_at_least": ("resource", "value"),
            "resources_at_least": ("values",),
            "samples_collected": ("count",),
            "samples_logged": ("count",),
            "simulation_score": ("mission_id", "value"),
            "distance_travelled": ("value",),
            "eva_time": ("value",),
            "tool_condition": ("value",),
            "mistakes_at_most": ("value",),
        }
        for field_name in required.get(self.type, ()):
            if getattr(self, field_name) in (None, {}, ""):
                raise ValueError(f"criteria '{self.type}' requires '{field_name}'")
        if self.grade is not None and self.grade not in GRADE_ORDER:
            raise ValueError(f"unknown grade '{self.grade}'")
        return self


class AchievementDefinition(BaseModel):
    id: str
    tier: str = "bronze"
    icon: str = "badge"
    name: LocalisedText
    description: LocalisedText
    criteria: AchievementCriteria
    hidden: bool = False


class AchievementLibrary(BaseModel):
    version: int = 1
    tiers: dict[str, int] = Field(default_factory=lambda: {"bronze": 10})
    achievements: list[AchievementDefinition] = Field(default_factory=list)

    @classmethod
    def load(cls, content_dir: Path) -> AchievementLibrary:
        path = content_dir / "achievements.json"
        try:
            library = cls.model_validate(json.loads(path.read_text("utf-8")))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ContentError(f"Cannot read achievements content: {exc}") from exc
        seen: set[str] = set()
        for achievement in library.achievements:
            if achievement.id in seen:
                raise ContentError(f"Duplicate achievement id '{achievement.id}'.")
            seen.add(achievement.id)
            if achievement.tier not in library.tiers:
                raise ContentError(
                    f"Achievement '{achievement.id}' uses unknown tier '{achievement.tier}'."
                )
        return library

    def points(self, tier: str) -> int:
        return int(self.tiers.get(tier, 0))

    @property
    def total_points(self) -> int:
        return sum(self.points(a.tier) for a in self.achievements)

    def describe(self) -> list[dict[str, Any]]:
        return [
            {**a.model_dump(mode="json"), "points": self.points(a.tier)} for a in self.achievements
        ]


class AchievementView(BaseModel):
    id: str
    tier: str
    icon: str
    name: LocalisedText
    description: LocalisedText
    points: int
    unlocked: bool
    unlocked_at: datetime | None = None
    progress_pct: float = 0.0


def _ratio(value: float, target: float) -> float:
    if target <= 0:
        return 100.0
    return round_to(clamp(100.0 * value / target, 0.0, 100.0), 1)


class AchievementSystem:
    """Evaluates the badge catalogue against one save document."""

    def __init__(
        self,
        state: GameState,
        library: AchievementLibrary,
        missions: MissionLibrary,
    ) -> None:
        self.state = state
        self.library = library
        self.missions = missions

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------
    def _mission_progress(self, mission_id: str) -> float:
        progress = self.state.missions.get(mission_id)
        definition = self.missions.get(mission_id)
        if progress is None or definition is None:
            return 0.0
        if progress.status == MissionStatus.completed:
            return 100.0
        total = max(1, len(definition.objectives))
        return _ratio(len(progress.completed_objectives), total)

    def _completed_count(self) -> int:
        return sum(
            1
            for progress in self.state.missions.values()
            if progress.status == MissionStatus.completed
        )

    def _simulation_score(self, mission_id: str) -> float:
        run = self.state.simulations.get(mission_id) or {}
        result = run.get("result") or {}
        try:
            return float(result.get("score_pct") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def evaluate(self, criteria: AchievementCriteria) -> tuple[bool, float]:
        """Return ``(unlocked, progress_pct)`` for one badge."""
        state = self.state
        kind = criteria.type

        if kind == "mission_completed":
            pct = self._mission_progress(criteria.mission_id or "")
            return pct >= 100.0, pct

        if kind == "flawless_mission":
            progress = state.missions.get(criteria.mission_id or "")
            completed = progress is not None and progress.status == MissionStatus.completed
            clean = completed and progress is not None and progress.mistakes == 0
            return clean, 100.0 if clean else self._mission_progress(criteria.mission_id or "")

        if kind == "grade_at_least":
            progress = state.missions.get(criteria.mission_id or "")
            achieved = GRADE_ORDER.get((progress.grade if progress else None) or "", -1)
            target_rank = GRADE_ORDER.get(criteria.grade or "S", 4)
            unlocked = achieved >= target_rank and achieved >= 0
            return unlocked, 100.0 if unlocked else _ratio(max(0, achieved), target_rank)

        if kind == "stage_completed":
            members = [m.id for m in self.missions.ordered() if m.stage == criteria.stage]
            if not members:
                return False, 0.0
            done = sum(
                1
                for mission_id in members
                if (p := state.missions.get(mission_id)) is not None
                and p.status == MissionStatus.completed
            )
            pct = _ratio(done, len(members))
            return done == len(members), pct

        if kind == "missions_completed":
            done = self._completed_count()
            needed = int(criteria.count or 1)
            return done >= needed, _ratio(done, needed)

        if kind == "campaign_completed":
            total = max(1, len(self.missions.missions))
            done = self._completed_count()
            return state.finished or done >= total, _ratio(done, total)

        if kind == "readiness_at_least":
            target = float(criteria.value or 0)
            return state.continuity.readiness >= target, _ratio(state.continuity.readiness, target)

        if kind == "resource_at_least":
            target = float(criteria.value or 0)
            current = float(state.resources.get(criteria.resource or "", 0))
            gate = self._completed_count() >= criteria.min_missions
            return gate and current >= target, _ratio(current, target)

        if kind == "resources_at_least":
            targets = criteria.values or {}
            gate = self._completed_count() >= criteria.min_missions
            ratios = [
                _ratio(float(state.resources.get(rid, 0)), float(target))
                for rid, target in targets.items()
            ]
            pct = round_to(sum(ratios) / len(ratios), 1) if ratios else 0.0
            unlocked = gate and all(
                float(state.resources.get(rid, 0)) >= float(target)
                for rid, target in targets.items()
            )
            return unlocked, pct

        if kind == "samples_collected":
            needed = int(criteria.count or 1)
            return state.stats.samples_collected >= needed, _ratio(
                state.stats.samples_collected, needed
            )

        if kind == "samples_logged":
            needed = int(criteria.count or 1)
            return state.stats.samples_logged >= needed, _ratio(state.stats.samples_logged, needed)

        if kind == "simulation_score":
            target = float(criteria.value or 0)
            score = self._simulation_score(criteria.mission_id or "")
            return score >= target, _ratio(score, target)

        if kind == "distance_travelled":
            target = float(criteria.value or 0)
            return state.stats.distance_metres >= target, _ratio(
                state.stats.distance_metres, target
            )

        if kind == "eva_time":
            target = float(criteria.value or 0)
            return state.stats.eva_seconds >= target, _ratio(state.stats.eva_seconds, target)

        if kind == "tool_condition":
            target = float(criteria.value or 0)
            conditions = [tool.condition for tool in state.equipment.values()]
            if len(conditions) < max(1, criteria.min_tools):
                return False, 0.0
            worst = float(min(conditions)) if conditions else 0.0
            return worst >= target, _ratio(worst, target)

        if kind == "mistakes_at_most":
            limit = float(criteria.value or 0)
            gate = self._completed_count() >= criteria.min_missions
            mistakes = float(state.stats.mistakes)
            pct = round_to(clamp(100.0 * (1.0 - mistakes / max(limit, 1.0)), 0.0, 100.0), 1)
            return gate and mistakes <= limit, pct

        return False, 0.0  # pragma: no cover - exhaustive Literal

    # ------------------------------------------------------------------
    # Views & unlocking
    # ------------------------------------------------------------------
    def views(self) -> list[AchievementView]:
        views: list[AchievementView] = []
        for definition in self.library.achievements:
            unlock = self.state.achievements.get(definition.id)
            unlocked_now, pct = self.evaluate(definition.criteria)
            if unlock is not None:
                unlocked_now, pct = True, 100.0
            views.append(
                AchievementView(
                    id=definition.id,
                    tier=definition.tier,
                    icon=definition.icon,
                    name=definition.name,
                    description=definition.description,
                    points=self.library.points(definition.tier),
                    unlocked=unlock is not None or unlocked_now,
                    unlocked_at=unlock.unlocked_at if unlock else None,
                    progress_pct=100.0 if unlock is not None else pct,
                )
            )
        return views

    def summary(self) -> dict[str, Any]:
        views = self.views()
        unlocked = [view for view in views if view.unlocked]
        return {
            "unlocked": len(unlocked),
            "total": len(views),
            "points": sum(view.points for view in unlocked),
            "points_possible": self.library.total_points,
            "completion_pct": _ratio(len(unlocked), max(1, len(views))),
        }

    def sync(self, recorder: EventRecorder) -> list[dict[str, Any]]:
        """Unlock whatever is now earned. Returns the newly unlocked badges."""
        newly: list[dict[str, Any]] = []
        for definition in self.library.achievements:
            if definition.id in self.state.achievements:
                continue
            unlocked, _ = self.evaluate(definition.criteria)
            if not unlocked:
                continue
            points = self.library.points(definition.tier)
            record = AchievementUnlock(tier=definition.tier, points=points)
            self.state.achievements[definition.id] = record
            payload = {
                "id": definition.id,
                "name": definition.name.model_dump(),
                "tier": definition.tier,
                "icon": definition.icon,
                "points": points,
                "unlocked_at": record.unlocked_at.isoformat(),
                "total_points": self.points_total(),
            }
            newly.append(payload)
            recorder.emit("achievement.unlocked", payload)
        return newly

    def points_total(self) -> int:
        return sum(record.points for record in self.state.achievements.values())
