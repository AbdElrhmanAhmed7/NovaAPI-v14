"""Microgravity fire, crew vitals and rover traverse models.

The fire model must behave *differently from Earth*: a spherical,
diffusion-limited flame, CO₂ that displaces breathable oxygen, and airflow that
feeds the fire until the zone is isolated.
"""

from __future__ import annotations

import pytest

from app.domain.enums import SIMULATION_DISCLAIMER, SimulationStatus
from app.simulations.crew_vitals import (
    SAFE_BANDS,
    STABLE_SECONDS_REQUIRED,
    CrewVitalsSimulation,
    VitalsState,
)
from app.simulations.microgravity_fire import (
    MIN_SAFE_O2,
    FireState,
    MicrogravityFireSimulation,
)
from app.simulations.rover_traverse import (
    ARRIVAL_RADIUS,
    RoverState,
    RoverTraverseSimulation,
)

FIRE = MicrogravityFireSimulation
VITALS = CrewVitalsSimulation
ROVER = RoverTraverseSimulation


def _fire(**params: object) -> FireState:
    state = FIRE.start("freefall_fire", 7, {"difficulty": "standard", **params})
    assert isinstance(state, FireState)
    return state


def _vitals(**params: object) -> VitalsState:
    state = VITALS.start("crew_health", 7, {"difficulty": "standard", **params})
    assert isinstance(state, VitalsState)
    return state


def _rover(**params: object) -> RoverState:
    state = ROVER.start("mars_rover_traverse", 7, {"difficulty": "standard", **params})
    assert isinstance(state, RoverState)
    return state


# ---------------------------------------------------------------------------
# Flame in freefall
# ---------------------------------------------------------------------------
def test_the_flame_is_spherical_and_cooler_than_an_earth_flame() -> None:
    state = _fire()
    FIRE.advance(state, {}, 1.0)
    telemetry = FIRE.telemetry(state)
    assert "spherical" in telemetry["fire"]["shape"]
    assert telemetry["fire"]["radius_m"] > 0
    # A diffusion-limited microgravity flame is far cooler than a buoyant one.
    assert telemetry["fire"]["temperature_k"] < 1_200


def test_unattended_fire_grows_and_consumes_oxygen() -> None:
    state = _fire()
    intensity_before, oxygen_before = state.intensity, state.oxygen_pct
    for _ in range(30):
        FIRE.advance(state, {}, 1.0)
    assert state.intensity > intensity_before
    assert state.oxygen_pct < oxygen_before
    assert state.co2_ppm > 450
    assert state.smoke > 0


def test_airflow_feeds_the_flame_until_the_zone_is_isolated() -> None:
    ventilated, isolated = _fire(), _fire()
    isolated.zone_isolated = True
    isolated.isolated_at = 0.0
    for _ in range(20):
        FIRE.advance(ventilated, {"ventilation": True}, 1.0)
        FIRE.advance(isolated, {"ventilation": True}, 1.0)
    assert ventilated.intensity > isolated.intensity
    assert "airflow_feeding_flame" in FIRE.telemetry(ventilated)["warnings"]
    assert "airflow_feeding_flame" not in FIRE.telemetry(isolated)["warnings"]


def test_co2_cannot_be_discharged_without_the_extinguisher() -> None:
    state = _fire()
    state.extinguisher_equipped = False
    before = state.co2_charges
    FIRE.advance(state, {"co2": 1.0}, 1.0)
    assert state.co2_charges == before
    assert state.co2_used == 0


def test_co2_suppresses_the_fire_but_displaces_breathable_oxygen() -> None:
    state = _fire()
    state.extinguisher_equipped = True
    state.zone_isolated = True
    state.isolated_at = 0.0
    oxygen_before = state.oxygen_pct
    FIRE.advance(state, {"co2": 1.0, "ventilation": False}, 1.0)
    assert state.co2_used > 0
    assert state.oxygen_pct < oxygen_before, "CO2 is not free: it displaces O2"
    assert state.co2_ppm > 450


def test_over_discharging_co2_kills_the_crew_atmosphere() -> None:
    state = _fire()
    state.extinguisher_equipped = True
    state.co2_charges = 50.0
    for _ in range(60):
        if state.status != SimulationStatus.running:
            break
        FIRE.advance(state, {"co2": 1.0, "ventilation": False}, 1.0)
    assert state.status == SimulationStatus.failed
    assert state.result is not None
    assert state.result["reason"] == "cabin_oxygen_critical"
    assert state.oxygen_pct < MIN_SAFE_O2


def test_the_full_correct_procedure_succeeds() -> None:
    state = _fire()
    state.extinguisher_equipped = True
    state.gloves_equipped = True
    state.zone_isolated = True
    state.isolated_at = 5.0
    state.ventilation_on = False

    for _ in range(400):
        if state.status != SimulationStatus.running:
            break
        if state.intensity > 0.02 and state.co2_charges > 0.05 and state.oxygen_pct > 19.0:
            command = {"co2": 0.35, "ventilation": False, "scrubber": False}
        else:
            command = {"co2": 0.0, "ventilation": False, "scrubber": True}
        FIRE.advance(state, command, 1.0)

    assert state.status == SimulationStatus.succeeded, state.result
    assert state.suppressed is True
    assert state.atmosphere_safe is True
    assert state.result is not None
    assert state.result["reason"] == "fire_out_atmosphere_safe"
    assert state.result["score_pct"] > 40


def test_the_scrubber_recovers_the_cabin_after_suppression() -> None:
    state = _fire()
    state.intensity = 0.0
    state.suppressed = True
    state.co2_ppm = 7_000.0
    state.smoke = 0.6
    FIRE.advance(state, {"scrubber": True}, 1.0)
    assert state.co2_ppm < 7_000.0
    assert state.smoke < 0.6


def test_the_blanket_only_helps_a_small_flame() -> None:
    small, big = _fire(), _fire()
    small.intensity, big.intensity = 0.2, 0.9
    small.zone_isolated = big.zone_isolated = True
    for _ in range(10):
        FIRE.advance(small, {"blanket": True}, 1.0)
        FIRE.advance(big, {"blanket": True}, 1.0)
    assert small.blanket_deployed and big.blanket_deployed
    assert small.intensity < 0.2, "a blanket smothers a small flame"
    assert big.intensity > 0.5, "but it cannot smother a large one"


def test_fire_telemetry_contract() -> None:
    telemetry = FIRE.telemetry(_fire())
    assert {"kind", "status", "fire", "cabin", "systems", "warnings", "result"} <= set(telemetry)
    assert {"intensity", "radius_m", "temperature_k", "shape", "suppressed"} <= set(
        telemetry["fire"]
    )
    assert {"oxygen_pct", "co2_ppm", "smoke", "temperature_c", "atmosphere_safe"} <= set(
        telemetry["cabin"]
    )


def test_fire_run_times_out() -> None:
    state = _fire()
    state.elapsed = FIRE.max_duration - 0.05
    FIRE.advance(state, {}, 1.0)
    assert state.status == SimulationStatus.failed
    assert state.result is not None
    assert state.result["reason"] == "timeout"


def test_fire_is_deterministic() -> None:
    a, b = _fire(), _fire()
    for _ in range(20):
        FIRE.advance(a, {"co2": 0.1}, 0.5)
        FIRE.advance(b, {"co2": 0.1}, 0.5)
    assert a.model_dump() == b.model_dump()


# ---------------------------------------------------------------------------
# Crew vitals (educational simulation, never medical advice)
# ---------------------------------------------------------------------------
def test_the_crew_starts_out_of_band() -> None:
    state = _vitals()
    assert state.in_band() is False
    assert VITALS.telemetry(state)["out_of_band"]


def test_every_payload_carries_the_medical_disclaimer() -> None:
    state = _vitals()
    assert state.disclaimer == SIMULATION_DISCLAIMER
    assert VITALS.telemetry(state)["disclaimer"] == SIMULATION_DISCLAIMER
    assert "never be used" in SIMULATION_DISCLAIMER


def test_untreated_crew_deteriorates_and_the_run_fails() -> None:
    state = _vitals()
    for _ in range(600):
        if state.status != SimulationStatus.running:
            break
        VITALS.advance(state, {}, 1.0)
    assert state.status == SimulationStatus.failed
    assert state.result is not None
    assert state.result["reason"] in {"critical_desaturation", "timeout"}


def test_supportive_care_stabilises_the_crew() -> None:
    state = _vitals()
    command = {
        "monitored": True,
        "oxygen_support": True,
        "salts_administered": True,
        "resting": True,
        "cooling_applied": True,
    }
    for _ in range(400):
        if state.status != SimulationStatus.running:
            break
        VITALS.advance(state, command, 1.0)
    assert state.status == SimulationStatus.succeeded, state.result
    assert state.in_band() is True
    assert state.stable_seconds >= STABLE_SECONDS_REQUIRED
    assert state.result is not None
    assert state.result["disclaimer"] == SIMULATION_DISCLAIMER


def test_monitoring_first_scores_better_than_treating_blind() -> None:
    monitored_first, treated_blind = _vitals(), _vitals()
    for _ in range(400):
        if monitored_first.status != SimulationStatus.running:
            break
        VITALS.advance(
            monitored_first,
            {
                "monitored": True,
                "oxygen_support": True,
                "salts_administered": True,
                "resting": True,
                "cooling_applied": True,
            },
            1.0,
        )
    for _ in range(400):
        if treated_blind.status != SimulationStatus.running:
            break
        VITALS.advance(
            treated_blind,
            {
                "oxygen_support": True,
                "salts_administered": True,
                "resting": True,
                "cooling_applied": True,
                "monitored": False,
            },
            1.0,
        )
    assert monitored_first.treatments[0] == "monitored"
    assert monitored_first.result is not None and treated_blind.result is not None
    assert monitored_first.result["score_pct"] > treated_blind.result["score_pct"]


def test_treatments_are_recorded_once_in_order() -> None:
    state = _vitals()
    for _ in range(5):
        VITALS.advance(state, {"monitored": True, "oxygen_support": True}, 1.0)
    assert state.treatments == ["monitored", "oxygen_support"]


def test_vitals_telemetry_exposes_bands_for_the_hud() -> None:
    telemetry = VITALS.telemetry(_vitals())
    assert set(telemetry["bands"]) == set(SAFE_BANDS)
    assert telemetry["stable_required"] == STABLE_SECONDS_REQUIRED
    assert {"spo2_pct", "heart_rate_bpm", "temperature_c"} <= set(telemetry["vitals"])


def test_veteran_difficulty_starts_sicker() -> None:
    cadet = VITALS.start("crew_health", 7, {"difficulty": "cadet"})
    veteran = VITALS.start("crew_health", 7, {"difficulty": "veteran"})
    assert veteran.spo2_pct < cadet.spo2_pct
    assert veteran.heart_rate_bpm > cadet.heart_rate_bpm


# ---------------------------------------------------------------------------
# Rover traverse
# ---------------------------------------------------------------------------
def test_the_rover_starts_with_waypoints_and_hazards() -> None:
    state = _rover()
    assert len(state.waypoints) >= 2
    assert len(state.hazards) == 4
    assert any(wp.required for wp in state.waypoints)


def test_throttle_drives_the_rover_forward() -> None:
    state = _rover()
    ROVER.advance(state, {"throttle": 1.0, "heading": 0.0}, 1.0)
    assert state.speed > 0
    assert state.x > 0
    assert state.odometer_m > 0
    assert state.battery_pct < 100.0


def test_the_rover_never_exceeds_its_top_speed() -> None:
    state = _rover()
    for _ in range(60):
        ROVER.advance(state, {"throttle": 1.0}, 1.0)
    assert state.speed <= 4.0 + 1e-6


def test_autopilot_reaches_the_target_waypoint() -> None:
    state = _rover()
    target = next(wp for wp in state.waypoints if wp.required)
    for _ in range(600):
        if state.status != SimulationStatus.running or target.reached:
            break
        ROVER.advance(state, {"target": target.id, "throttle": 0.9}, 1.0)
    assert target.reached is True
    assert target.id in state.arrived


def test_reaching_every_required_waypoint_completes_the_traverse() -> None:
    state = _rover()
    for _ in range(2_000):
        if state.status != SimulationStatus.running:
            break
        pending = next((wp for wp in state.waypoints if wp.required and not wp.reached), None)
        command = {"target": pending.id, "throttle": 0.9} if pending else {"throttle": 0.3}
        ROVER.advance(state, command, 1.0)
    assert state.status == SimulationStatus.succeeded, state.result
    assert state.result is not None
    assert state.result["reason"] == "all_waypoints_reached"


def test_driving_through_a_hazard_damages_the_rover() -> None:
    state = _rover()
    hazard = state.hazards[0]
    state.x, state.y = hazard.x, hazard.y
    ROVER.advance(state, {"throttle": 0.1}, 0.2)
    assert hazard.hit is True
    assert state.damage > 0
    assert any(entry["event"] == "hazard.hit" for entry in state.log)


def test_a_hazard_is_only_counted_once() -> None:
    state = _rover()
    hazard = state.hazards[0]
    state.x, state.y = hazard.x, hazard.y
    ROVER.advance(state, {"throttle": 0.0}, 0.5)
    damage = state.damage
    ROVER.advance(state, {"throttle": 0.0}, 0.5)
    assert state.damage == damage


def test_damage_reduces_the_achievable_speed() -> None:
    healthy, damaged = _rover(), _rover()
    damaged.damage = 120.0
    for _ in range(30):
        ROVER.advance(healthy, {"throttle": 1.0}, 1.0)
        ROVER.advance(damaged, {"throttle": 1.0}, 1.0)
    assert damaged.speed < healthy.speed


def test_an_empty_battery_strands_the_rover() -> None:
    state = _rover()
    state.battery_pct = 0.05
    for _ in range(20):
        if state.status != SimulationStatus.running:
            break
        ROVER.advance(state, {"throttle": 1.0}, 1.0)
    assert state.status == SimulationStatus.failed
    assert state.stranded is True
    assert state.result is not None
    assert state.result["reason"] == "battery_depleted"


def test_parking_lets_the_solar_panel_trickle_charge() -> None:
    parked, driving = _rover(), _rover()
    for _ in range(20):
        ROVER.advance(parked, {"throttle": 0.0}, 1.0)
        ROVER.advance(driving, {"throttle": 1.0}, 1.0)
    assert parked.battery_pct > driving.battery_pct


def test_unknown_waypoint_targets_are_ignored() -> None:
    state = _rover()
    ROVER.advance(state, {"target": "olympus_mons", "throttle": 0.5}, 1.0)
    assert state.target is None
    assert state.autopilot is False


def test_arrival_radius_is_used_for_waypoint_capture() -> None:
    state = _rover()
    waypoint = state.waypoints[0]
    state.x, state.y = waypoint.x + ARRIVAL_RADIUS - 1, waypoint.y
    ROVER.advance(state, {"throttle": 0.0}, 0.2)
    assert waypoint.reached is True


def test_rover_telemetry_is_honest_about_the_scale() -> None:
    telemetry = ROVER.telemetry(_rover())
    assert {"position", "heading_deg", "battery_pct", "waypoints", "hazards"} <= set(telemetry)
    assert "game-scaled" in telemetry["note"]


def test_rover_scene_geometry_is_serialisable() -> None:
    state = _rover()
    dumped = ROVER.dump(state)
    restored = ROVER.load(dumped)
    assert restored.model_dump() == state.model_dump()


@pytest.mark.parametrize("simulation", [FIRE, VITALS, ROVER], ids=["fire", "vitals", "rover"])
def test_every_model_round_trips_through_a_save(simulation: type) -> None:
    state = simulation.start("mission", 3, {})
    simulation.advance(state, {}, 1.0)
    restored = simulation.load(simulation.dump(state))
    assert restored.model_dump() == state.model_dump()
    assert restored.status in {SimulationStatus.running, SimulationStatus.failed}


@pytest.mark.parametrize("simulation", [FIRE, VITALS, ROVER], ids=["fire", "vitals", "rover"])
def test_no_model_accepts_a_client_supplied_score(simulation: type) -> None:
    """Commands are intent only: a score/position injection must be ignored."""
    state = simulation.start("mission", 3, {})
    simulation.advance(state, {"score_pct": 100.0, "status": "succeeded", "fuel": 9e9}, 0.5)
    assert state.status == SimulationStatus.running
    assert state.result is None
