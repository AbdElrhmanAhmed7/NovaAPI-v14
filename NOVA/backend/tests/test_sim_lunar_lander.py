"""Server-authoritative physics: the lunar lander.

These tests assert the *model*, not the autopilot: gravity, mass-thrust
coupling, fuel burn, frame-rate independence and the touchdown rules.
"""

from __future__ import annotations

import math

import pytest

from app.domain.enums import SimulationKind, SimulationStatus
from app.simulations.base import MAX_SUBSTEP, MAX_TICK_SECONDS
from app.simulations.lunar_lander import (
    DRY_MASS,
    G0,
    MAX_THRUST,
    MOON_GRAVITY,
    PAD_HALF_WIDTH,
    SAFE_VERTICAL_SPEED,
    SPECIFIC_IMPULSE,
    LanderState,
    LunarLanderSimulation,
)

SIM = LunarLanderSimulation


def _run(**params: object) -> LanderState:
    state = SIM.start("lunar_landing", 7, {"difficulty": "standard", **params})
    assert isinstance(state, LanderState)
    return state


def test_moon_gravity_is_the_real_value() -> None:
    assert pytest.approx(1.62) == MOON_GRAVITY


def test_free_fall_accelerates_at_moon_gravity() -> None:
    state = _run()
    before = state.vertical_speed
    SIM.advance(state, {"throttle": 0.0}, 1.0)
    # Semi-implicit Euler over 10 substeps: dv == -g * dt exactly.
    assert state.vertical_speed - before == pytest.approx(-MOON_GRAVITY * 1.0, abs=1e-6)
    assert state.fuel == state.initial_fuel, "no thrust, no burn"


def test_hover_throttle_matches_the_weight_of_the_lander() -> None:
    state = _run()
    hover = (state.mass * MOON_GRAVITY) / MAX_THRUST
    SIM.advance(state, {"throttle": hover}, 1.0)
    assert abs(state.vertical_speed - (-35.0)) < 0.35


def test_fuel_burn_follows_the_rocket_equation() -> None:
    state = _run()
    before = state.fuel
    SIM.advance(state, {"throttle": 1.0}, 1.0)
    expected = MAX_THRUST * 1.0 / (SPECIFIC_IMPULSE * G0)
    assert before - state.fuel == pytest.approx(expected, rel=0.02)


def test_thrust_vector_trades_lift_for_translation() -> None:
    state = _run()
    x_before, vx_before = state.position_x, state.horizontal_speed
    SIM.advance(state, {"throttle": 0.9, "attitude": 1.0}, 1.0)
    assert state.attitude > 0
    assert state.horizontal_speed > vx_before, "tilting right accelerates right"
    assert state.position_x > x_before


def test_attitude_is_rate_limited_and_clamped() -> None:
    state = _run()
    for _ in range(20):
        SIM.advance(state, {"throttle": 0.0, "attitude": 1.0}, 1.0)
    assert state.attitude == pytest.approx(45.0)


def test_commands_are_clamped_not_trusted() -> None:
    state = _run()
    SIM.advance(state, {"throttle": 99.0, "attitude": -50.0}, 0.5)
    assert 0.0 <= state.throttle <= 1.0
    assert state.attitude >= -45.0


def test_empty_tanks_produce_no_thrust() -> None:
    state = _run()
    state.fuel = 0.0
    before = state.vertical_speed
    SIM.advance(state, {"throttle": 1.0}, 1.0)
    assert state.fuel == 0.0
    assert state.vertical_speed < before
    assert "fuel_depleted" in state.warnings


def test_a_fuel_starved_burn_cannot_overdraw_the_tank() -> None:
    state = _run()
    state.fuel = 0.05
    SIM.advance(state, {"throttle": 1.0}, 1.0)
    assert state.fuel == 0.0


def test_outcome_is_independent_of_client_frame_rate() -> None:
    """10 × 0.1 s must equal 1 × 1.0 s: no frame-rate advantage."""
    coarse, fine = _run(), _run()
    command = {"throttle": 0.55, "attitude": 0.2}
    SIM.advance(coarse, command, 1.0)
    for _ in range(10):
        SIM.advance(fine, command, 0.1)
    assert coarse.altitude == pytest.approx(fine.altitude, abs=0.05)
    assert coarse.fuel == pytest.approx(fine.fuel, abs=0.05)
    assert coarse.position_x == pytest.approx(fine.position_x, abs=0.05)


def test_ticks_longer_than_the_cap_are_truncated() -> None:
    state = _run()
    SIM.advance(state, {"throttle": 0.0}, 30.0)
    assert state.elapsed == pytest.approx(MAX_TICK_SECONDS, abs=1e-6)


def test_substeps_never_exceed_the_integration_limit() -> None:
    steps, h = SIM.substeps(1.0)
    assert h <= MAX_SUBSTEP + 1e-9
    assert steps == 10


def test_deterministic_for_the_same_seed() -> None:
    a, b = _run(), _run()
    for _ in range(30):
        SIM.advance(a, {"throttle": 0.5, "attitude": 0.1}, 0.5)
        SIM.advance(b, {"throttle": 0.5, "attitude": 0.1}, 0.5)
    assert a.model_dump() == b.model_dump()


def test_different_seeds_produce_different_approaches() -> None:
    a = SIM.start("lunar_landing", 1, {})
    b = SIM.start("lunar_landing", 999, {})
    assert (a.position_x, a.horizontal_speed) != (b.position_x, b.horizontal_speed)


def test_a_hard_impact_crashes_and_is_capped() -> None:
    state = _run()
    state.altitude = 2.0
    state.vertical_speed = -40.0
    SIM.advance(state, {"throttle": 0.0}, 1.0)
    assert state.status == SimulationStatus.failed
    assert state.result is not None
    assert state.result["reason"] in {"hard_touchdown", "off_pad"}
    assert state.result["score_pct"] <= 45.0
    assert abs(state.result["vertical_speed"]) > SAFE_VERTICAL_SPEED


def test_a_soft_centred_touchdown_succeeds() -> None:
    state = _run()
    state.altitude = 1.0
    state.vertical_speed = -1.0
    state.horizontal_speed = 0.0
    state.position_x = 0.0
    state.attitude = 0.0
    SIM.advance(state, {"throttle": 0.0}, 1.0)
    assert state.status == SimulationStatus.succeeded
    assert state.result is not None
    assert state.result["reason"] == "nominal_landing"
    assert state.result["score_pct"] > 60


def test_landing_outside_the_pad_fails_even_when_soft() -> None:
    state = _run()
    state.position_x = PAD_HALF_WIDTH * 6
    state.altitude = SIM.terrain_height(state, state.position_x) + 0.5
    state.vertical_speed = -0.5
    state.horizontal_speed = 0.0
    SIM.advance(state, {"throttle": 0.0}, 1.0)
    assert state.status == SimulationStatus.failed
    assert state.result is not None
    assert state.result["reason"] == "off_pad"


def test_the_pad_is_flat_and_terrain_is_deterministic() -> None:
    state = _run()
    assert SIM.terrain_height(state, 0.0) == 0.0
    assert SIM.terrain_height(state, PAD_HALF_WIDTH - 1) == 0.0
    profile = SIM.terrain_profile(state, span=300.0, samples=31)
    assert len(profile) == 31
    assert all(set(point) == {"x", "height"} for point in profile)
    assert all(point["height"] >= 0 for point in profile)
    assert profile == SIM.terrain_profile(state, span=300.0, samples=31)


def test_running_out_of_time_fails_the_run() -> None:
    state = _run()
    state.elapsed = SIM.max_duration - 0.05
    state.altitude = 100_000.0  # never reaches the ground
    SIM.advance(state, {"throttle": 0.0}, 1.0)
    assert state.status == SimulationStatus.failed
    assert state.result is not None
    assert state.result["reason"] == "timeout"


def test_a_finished_run_ignores_further_commands() -> None:
    state = _run()
    state.altitude = 1.0
    state.vertical_speed = -1.0
    SIM.advance(state, {"throttle": 0.0}, 1.0)
    frozen = state.model_dump()
    SIM.advance(state, {"throttle": 1.0}, 1.0)
    assert state.model_dump() == frozen


def test_telemetry_exposes_the_full_pilot_contract() -> None:
    state = _run()
    telemetry = SIM.telemetry(state)
    required = {
        "kind",
        "status",
        "altitude_agl",
        "altitude_msl",
        "vertical_speed",
        "horizontal_speed",
        "position_x",
        "attitude",
        "throttle",
        "fuel",
        "fuel_pct",
        "pad_x",
        "pad_half_width",
        "distance_to_pad",
        "warnings",
        "result",
        "limits",
        "elapsed",
    }
    assert required <= set(telemetry)
    assert telemetry["kind"] == SimulationKind.lunar_lander.value
    assert telemetry["limits"]["gravity"] == MOON_GRAVITY
    assert telemetry["altitude_agl"] <= telemetry["altitude_msl"]


def test_low_fuel_and_fast_descent_raise_warnings() -> None:
    state = _run()
    state.fuel = state.initial_fuel * 0.1
    state.vertical_speed = -80.0
    SIM.advance(state, {"throttle": 0.0}, 0.1)
    assert "fuel_low" in state.warnings
    assert "descent_too_fast" in state.warnings


def test_mass_shrinks_as_fuel_burns() -> None:
    state = _run()
    assert state.mass == DRY_MASS + state.fuel
    SIM.advance(state, {"throttle": 1.0}, 1.0)
    assert state.mass == DRY_MASS + state.fuel
    assert state.mass < DRY_MASS + state.initial_fuel


def test_save_and_restore_round_trips_the_run() -> None:
    state = _run()
    SIM.advance(state, {"throttle": 0.6, "attitude": 0.3}, 1.0)
    restored = SIM.load(SIM.dump(state))
    assert isinstance(restored, LanderState)
    assert restored.model_dump() == state.model_dump()
    assert restored.status == SimulationStatus.running


def test_gravity_is_not_earth_gravity() -> None:
    """A copy-paste of 9.81 would silently break the whole simulator."""
    assert MOON_GRAVITY < G0 / 5
    assert math.isclose(MOON_GRAVITY, 1.62)
