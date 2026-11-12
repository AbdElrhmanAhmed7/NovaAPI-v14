"""Inventory, tool wear, consumables (F key) and the resource pool.

These are the systems that make *consequences* real: a wasted kit is gone, a
neglected scanner fails on Mars, and an over-cap refill never creates matter.
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    EquipmentFailureError,
    InsufficientResourceError,
    InvalidInputError,
    MissingItemError,
)
from app.domain.state import GameState
from app.missions.registry import GameContent
from app.systems.inventory_system import FAILURE_THRESHOLD, WARNING_THRESHOLD, InventorySystem
from app.systems.resource_manager import ResourceManager


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
def test_adding_a_durable_tool_registers_its_condition(
    state: GameState, content: GameContent
) -> None:
    inventory = InventorySystem(state, content.catalog)
    assert inventory.condition("scanner") == 100
    inventory.add("scanner")
    assert inventory.has("scanner")
    assert "scanner" in state.equipment
    assert inventory.condition("scanner") == 100


def test_stacks_are_clamped_to_max_stack(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    cap = content.catalog.items["repair_kit"].max_stack
    inventory.add("repair_kit", cap + 10)
    assert inventory.count("repair_kit") == cap


def test_removing_more_than_carried_is_rejected_atomically(
    state: GameState, content: GameContent
) -> None:
    inventory = InventorySystem(state, content.catalog)
    inventory.add("repair_kit", 1)
    inventory.add("wrench", 1)
    with pytest.raises(MissingItemError):
        inventory.apply({"repair_kit": -2, "wrench": -1})
    # Nothing was applied: the wrench is still there.
    assert inventory.count("repair_kit") == 1
    assert inventory.count("wrench") == 1


def test_unknown_items_are_rejected(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    with pytest.raises(MissingItemError) as excinfo:
        inventory.apply({"warp_drive": 1})
    assert excinfo.value.details["missing"][0]["reason"] == "unknown_item"


def test_wear_accumulates_and_triggers_a_tool_failure(
    state: GameState, content: GameContent
) -> None:
    inventory = InventorySystem(state, content.catalog)
    inventory.add("scanner")
    for _ in range(10):
        inventory.wear("scanner", 10)
    assert inventory.condition("scanner") == 0
    with pytest.raises(EquipmentFailureError) as excinfo:
        inventory.check_tool_ready("scanner")
    assert excinfo.value.details["item"] == "scanner"
    assert state.equipment["scanner"].failure_count == 1


def test_degraded_tools_appear_before_they_fail(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    inventory.add("wrench")
    inventory.wear("wrench", 100 - WARNING_THRESHOLD + 5)
    degraded = {entry["item"] for entry in inventory.degraded_tools()}
    assert "wrench" in degraded
    # Still usable: warning is not failure.
    inventory.check_tool_ready("wrench")
    inventory.wear("wrench", 100)
    assert inventory.condition("wrench") <= FAILURE_THRESHOLD
    with pytest.raises(EquipmentFailureError):
        inventory.check_tool_ready("wrench")


def test_maintenance_restores_condition_and_is_counted(
    state: GameState, content: GameContent
) -> None:
    inventory = InventorySystem(state, content.catalog)
    inventory.add("scanner")
    inventory.wear("scanner", 70)
    assert inventory.condition("scanner") == 30
    inventory.restore("scanner", 45)
    assert inventory.condition("scanner") == 75
    assert state.equipment["scanner"].maintenance_count == 1
    inventory.check_tool_ready("scanner")


def test_restore_clamps_at_100_and_rejects_consumables(
    state: GameState, content: GameContent
) -> None:
    inventory = InventorySystem(state, content.catalog)
    inventory.add("scanner")
    inventory.restore("scanner", 500)
    assert inventory.condition("scanner") == 100
    with pytest.raises(InvalidInputError):
        inventory.restore("repair_kit", 10)


def test_using_an_oxygen_tank_refills_the_reserve(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    resources = ResourceManager(state, content.catalog)
    resources.apply({"oxygen": -50})
    before = resources.get("oxygen")
    inventory.add("oxygen_tank")
    result = inventory.use("oxygen_tank")
    assert resources.get("oxygen") == min(before + 40, resources.cap("oxygen"))
    assert inventory.count("oxygen_tank") == 0
    assert result["changes"]


def test_maintenance_kit_targets_the_most_worn_tool(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    inventory.add("scanner")
    inventory.add("wrench")
    inventory.wear("scanner", 60)
    inventory.wear("wrench", 10)
    inventory.add("maintenance_kit")
    result = inventory.use("maintenance_kit")
    assert result["target"] == "scanner"
    assert inventory.condition("scanner") > 40


def test_using_an_item_you_do_not_carry_fails(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    with pytest.raises(MissingItemError):
        inventory.use("oxygen_tank")


def test_mission_only_items_cannot_be_used_directly(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    inventory.add("medkit")
    with pytest.raises(InvalidInputError):
        inventory.use("medkit")


def test_unknown_item_use_is_invalid_input(state: GameState, content: GameContent) -> None:
    inventory = InventorySystem(state, content.catalog)
    with pytest.raises(InvalidInputError):
        inventory.use("tricorder")


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------
def test_resource_spend_is_atomic(state: GameState, content: GameContent) -> None:
    resources = ResourceManager(state, content.catalog)
    resources.apply({"water": -int(resources.get("water"))})  # empty the tank
    before = resources.all()
    with pytest.raises(InsufficientResourceError):
        resources.apply({"water": -5, "power": -1})
    assert resources.all() == before


def test_positive_deltas_clamp_at_the_cap(state: GameState, content: GameContent) -> None:
    resources = ResourceManager(state, content.catalog)
    cap = resources.cap("water")
    changes = resources.apply({"water": cap * 3})
    assert resources.get("water") == cap
    assert changes[0]["after"] == cap


def test_consumption_and_recovery_are_tracked(state: GameState, content: GameContent) -> None:
    resources = ResourceManager(state, content.catalog)
    resources.apply({"water": -10})
    resources.apply({"water": 4})
    assert state.stats.resources_consumed["water"] >= 10
    assert state.stats.resources_recovered["water"] >= 4


def test_critical_resources_are_reported(state: GameState, content: GameContent) -> None:
    resources = ResourceManager(state, content.catalog)
    definition = content.catalog.resources["oxygen"]
    resources.apply({"oxygen": -(resources.get("oxygen") - definition.critical_below)})
    assert "oxygen" in resources.critical()


def test_unknown_resource_is_rejected(state: GameState, content: GameContent) -> None:
    resources = ResourceManager(state, content.catalog)
    with pytest.raises(InsufficientResourceError) as excinfo:
        resources.apply({"dilithium": -1})
    assert excinfo.value.details["shortfalls"][0]["reason"] == "unknown_resource"


def test_refill_tops_up_to_the_cap(state: GameState, content: GameContent) -> None:
    resources = ResourceManager(state, content.catalog)
    resources.apply({"power": -20})
    resources.refill("power")
    assert resources.get("power") == resources.cap("power")


def test_ratio_is_a_fraction_of_the_cap(state: GameState, content: GameContent) -> None:
    resources = ResourceManager(state, content.catalog)
    assert 0.0 <= resources.ratio("water") <= 1.0


@pytest.mark.parametrize("amount", [0, -5])
def test_consume_and_add_reject_non_positive_amounts(
    state: GameState, content: GameContent, amount: int
) -> None:
    resources = ResourceManager(state, content.catalog)
    with pytest.raises(ValueError):
        resources.consume("water", amount)
    with pytest.raises(ValueError):
        resources.add("water", amount)
