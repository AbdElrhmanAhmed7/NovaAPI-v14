"""Suit oxygen model and the Mission Continuity System.

Oxygen must be server-authoritative (a hacked client cannot freeze life
support) and continuity must be *explainable*: same play → same carry-over.
"""

from __future__ import annotations

import pytest

from app.domain.enums import Activity, Difficulty, Environment, MissionStatus
from app.domain.state import GameState
from app.missions.engine import MissionEngine
from app.missions.registry import GameContent
from app.systems.continuity import ContinuitySystem
from app.systems.events import EventRecorder
from app.systems.oxygen_system import (
    BASE_RATES,
    CRITICAL_LEVEL,
    WARNING_LEVEL,
    OxygenSystem,
)
from app.systems.resource_manager import ResourceManager


# ---------------------------------------------------------------------------
# Oxygen
# ---------------------------------------------------------------------------
def test_running_costs_more_oxygen_than_walking(state: GameState, content: GameContent) -> None:
    state.environment = Environment.mars_surface
    oxygen = OxygenSystem(state, content.catalog)
    assert oxygen.rate_for(Activity.run.value) > oxygen.rate_for(Activity.walk.value)
    assert oxygen.rate_for(Activity.walk.value) > oxygen.rate_for(Activity.idle.value)


def test_pressurised_interiors_are_cheaper_than_eva(state: GameState, content: GameContent) -> None:
    oxygen = OxygenSystem(state, content.catalog)
    state.environment = Environment.earth_analog
    sheltered = oxygen.rate_for(Activity.walk.value)
    state.environment = Environment.mars_surface
    exposed = oxygen.rate_for(Activity.walk.value)
    assert sheltered < exposed


def test_a_damaged_suit_leaks_faster(state: GameState, content: GameContent) -> None:
    state.environment = Environment.mars_surface
    oxygen = OxygenSystem(state, content.catalog)
    healthy = oxygen.rate_for(Activity.eva.value)
    oxygen.damage_suit(60)
    damaged = oxygen.rate_for(Activity.eva.value)
    assert damaged > healthy


@pytest.mark.parametrize(
    ("difficulty", "expected_order"),
    [(Difficulty.cadet, -1), (Difficulty.standard, 0), (Difficulty.veteran, 1)],
)
def test_difficulty_scales_consumption(
    state: GameState, content: GameContent, difficulty: Difficulty, expected_order: int
) -> None:
    state.environment = Environment.mars_surface
    state.difficulty = Difficulty.standard
    baseline = OxygenSystem(state, content.catalog).rate_for(Activity.eva.value)
    state.difficulty = difficulty
    rate = OxygenSystem(state, content.catalog).rate_for(Activity.eva.value)
    if expected_order < 0:
        assert rate < baseline
    elif expected_order > 0:
        assert rate > baseline
    else:
        assert rate == baseline


def test_dt_is_clamped_so_a_paused_tab_cannot_fast_forward(
    state: GameState, content: GameContent
) -> None:
    oxygen = OxygenSystem(state, content.catalog)
    result = oxygen.tick(9_999.0, Activity.run.value)
    assert result["dt_seconds"] == 5.0


def test_fractional_consumption_accumulates(state: GameState, content: GameContent) -> None:
    """Slow ticks must still cost oxygen even when each tick is < 1 unit."""
    state.environment = Environment.mars_surface
    oxygen = OxygenSystem(state, content.catalog)
    resources = ResourceManager(state, content.catalog)
    before = resources.get("oxygen")
    for _ in range(400):
        oxygen.tick(1.0, Activity.run.value)
    assert resources.get("oxygen") < before


def test_unknown_activity_falls_back_to_idle(state: GameState, content: GameContent) -> None:
    oxygen = OxygenSystem(state, content.catalog)
    result = oxygen.tick(1.0, "moonwalk")
    assert result["activity"] == Activity.idle.value
    assert result["rate_per_second"] == pytest.approx(oxygen.rate_for(Activity.idle.value))


def test_low_oxygen_raises_alarms_and_damages_the_suit(
    state: GameState, content: GameContent
) -> None:
    oxygen = OxygenSystem(state, content.catalog)
    resources = ResourceManager(state, content.catalog)
    resources.apply({"oxygen": -(resources.get("oxygen") - WARNING_LEVEL)})
    assert oxygen.tick(1.0)["alarm"] == "warning"

    resources.apply({"oxygen": -(resources.get("oxygen") - CRITICAL_LEVEL)})
    integrity_before = state.suit.integrity
    result = oxygen.tick(4.0)
    assert result["alarm"] == "critical"
    assert state.suit.integrity < integrity_before


def test_oxygen_never_goes_negative(state: GameState, content: GameContent) -> None:
    oxygen = OxygenSystem(state, content.catalog)
    resources = ResourceManager(state, content.catalog)
    resources.apply({"oxygen": -resources.get("oxygen")})
    for _ in range(50):
        oxygen.tick(5.0, Activity.run.value)
    assert resources.get("oxygen") == 0


def test_eva_toggle_sets_flag_and_is_reported(state: GameState, content: GameContent) -> None:
    oxygen = OxygenSystem(state, content.catalog)
    assert oxygen.set_eva(True)["eva_active"] is True
    assert state.has_flag("eva_active") is True
    oxygen.set_eva(False)
    assert state.has_flag("eva_active") is False


def test_suit_repair_clamps_at_100(state: GameState, content: GameContent) -> None:
    oxygen = OxygenSystem(state, content.catalog)
    oxygen.damage_suit(40)
    assert oxygen.repair_suit(10) == 70
    assert oxygen.repair_suit(500) == 100


def test_every_activity_has_a_rate() -> None:
    assert set(BASE_RATES) == {activity.value for activity in Activity}


# ---------------------------------------------------------------------------
# Continuity
# ---------------------------------------------------------------------------
def _play_water_leak(state: GameState, content: GameContent, *, mistake: bool) -> dict:
    """Reach the leak mission and play it with or without the flagship error."""
    engine = MissionEngine(content)
    recorder = EventRecorder(state)
    inventory_items = {
        "scanner": 1,
        "wrench": 1,
        "repair_kit": 2,
        "multimeter": 1,
        "maintenance_kit": 1,
        "power_cell": 1,
        "oxygen_tank": 1,
    }
    from app.systems.inventory_system import InventorySystem

    InventorySystem(state, content.catalog).apply(inventory_items)
    for mission_id in ("training_prep", "analog_systems_check", "analog_water_leak"):
        state.mission(mission_id).status = MissionStatus.available
    state.world["leak_active"] = True  # normally set by check_water_loop
    engine.start(state, "analog_water_leak", recorder)
    engine.execute(state, "analog_water_leak", "scan_leak", recorder)
    if mistake:
        from app.core.errors import PreconditionFailedError

        with pytest.raises(PreconditionFailedError):
            engine.execute(state, "analog_water_leak", "repair_pipe", recorder)
    for action in ("close_valve", "repair_pipe", "verify_pressure"):
        engine.execute(state, "analog_water_leak", action, recorder)
    return state.mission("analog_water_leak").model_dump()


def test_a_clean_leak_repair_produces_a_positive_water_modifier(
    state: GameState, content: GameContent
) -> None:
    _play_water_leak(state, content, mistake=False)
    modifier = next(m for m in state.continuity.modifiers if m.key == "leak:water")
    assert modifier.value > 0
    assert modifier.reason
    assert modifier.applied is False, "resource carry-over is deferred to the next mission"


def test_a_botched_leak_repair_produces_a_negative_water_modifier(
    state: GameState, content: GameContent
) -> None:
    _play_water_leak(state, content, mistake=True)
    modifier = next(m for m in state.continuity.modifiers if m.key == "leak:water")
    assert modifier.value < 0
    assert "wrong-order" in modifier.reason.lower()


def test_pending_modifiers_apply_once_when_the_next_mission_starts(
    state: GameState, content: GameContent
) -> None:
    _play_water_leak(state, content, mistake=False)
    continuity = ContinuitySystem(state, content.catalog)
    resources = ResourceManager(state, content.catalog)
    resources.apply({"water": -30})  # make room under the cap
    before = resources.get("water")

    applied = continuity.apply_pending("analog_water_recycling")
    assert applied, "the water modifier must land on the next mission"
    assert resources.get("water") > before

    again = continuity.apply_pending("analog_power_link")
    assert again == [], "a modifier must never be applied twice"


def test_modifiers_do_not_stack_forever(state: GameState, content: GameContent) -> None:
    _play_water_leak(state, content, mistake=False)
    keys = [m.key for m in state.continuity.modifiers]
    assert len(keys) == len(set(keys))


def test_readiness_and_grade_are_recomputed(state: GameState, content: GameContent) -> None:
    _play_water_leak(state, content, mistake=False)
    assert 0.0 <= state.continuity.readiness <= 100.0
    assert state.continuity.readiness_grade in {"S", "A", "B", "C", "D"}
    assert state.continuity.history


def test_continuity_summary_is_english_and_disclaimed(
    state: GameState, content: GameContent
) -> None:
    summary = ContinuitySystem(state, content.catalog).summary()
    assert set(summary["note"]) == {"en", "ar"}
    assert "simplified" in summary["note"]["en"]
    assert summary["note"]["ar"] == summary["note"]["en"]


def test_metric_resolution_supports_every_prefix(state: GameState, content: GameContent) -> None:
    definition = content.missions.get("analog_water_leak")
    assert definition is not None
    progress = state.mission("analog_water_leak")
    continuity = ContinuitySystem(state, content.catalog)
    state.set_flag("valve_closed", True)
    state.world["leak"] = {"valve_closed": True}
    resolve = lambda metric: continuity.resolve_metric(  # noqa: E731
        metric, definition, progress, {"score_pct": 88.0}
    )
    assert resolve("score_pct") == 88.0
    assert resolve("flag:valve_closed") == 1.0
    assert resolve("flag:nonexistent") == 0.0
    assert resolve("resource:water") == float(state.resources["water"])
    assert resolve("equipment:scanner") == 100.0
    assert resolve("stat:actions") == 0.0
    assert resolve("world:leak.valve_closed") == 1.0
    assert resolve("nonsense") == 0.0
    assert 0.0 <= resolve("resource_pct:water") <= 100.0
