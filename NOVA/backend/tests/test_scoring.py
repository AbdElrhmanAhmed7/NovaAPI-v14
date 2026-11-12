"""Scoring, grading and readiness maths.

The most important rule here is the *mistake cap*: a player who floods the bay
must never walk away with a perfect score, no matter how fast they were.
"""

from __future__ import annotations

import pytest

from app.domain.enums import Grade
from app.missions.registry import GameContent
from app.systems.scoring import grade_for, readiness_from_scores, score_mission


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, Grade.S.value),
        (95, Grade.S.value),
        (94.9, Grade.A.value),
        (85, Grade.A.value),
        (84.9, Grade.B.value),
        (70, Grade.B.value),
        (69.9, Grade.C.value),
        (50, Grade.C.value),
        (49.9, Grade.D.value),
        (0, Grade.D.value),
    ],
)
def test_grade_boundaries(score: float, expected: str) -> None:
    assert grade_for(score) == expected


def test_a_flawless_fast_run_can_reach_100(content: GameContent) -> None:
    definition = content.missions.get("analog_water_leak")
    assert definition is not None
    report = score_mission(
        definition,
        earned_points=definition.max_score,
        mistakes=0,
        elapsed_seconds=10.0,
    )
    assert report["score_pct"] == 100.0
    assert report["grade"] == Grade.S.value
    assert report["passed"] is True
    assert report["mistake_penalty"] == 0


def test_a_mistake_can_never_be_masked_by_the_time_bonus(content: GameContent) -> None:
    """Regression: speed used to buy back a perfect score after a flood."""
    definition = content.missions.get("analog_water_leak")
    assert definition is not None
    report = score_mission(
        definition,
        earned_points=definition.max_score,
        mistakes=1,
        elapsed_seconds=0.5,  # as fast as physically possible
    )
    assert report["mistake_penalty"] > 0
    assert report["score_pct"] < 100.0
    # The cap is exactly one penalty below perfection.
    expected = (
        100.0 * (definition.max_score - definition.scoring.mistake_penalty) / definition.max_score
    )
    assert report["score_pct"] == pytest.approx(expected, abs=0.05)


def test_more_mistakes_monotonically_reduce_the_score(content: GameContent) -> None:
    definition = content.missions.get("analog_water_leak")
    assert definition is not None
    scores = [
        score_mission(
            definition, earned_points=definition.max_score, mistakes=n, elapsed_seconds=30.0
        )["score_pct"]
        for n in range(6)
    ]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) > 1
    assert scores[-1] >= 0.0


def test_score_never_leaves_the_0_100_band(content: GameContent) -> None:
    definition = content.missions.get("analog_water_leak")
    assert definition is not None
    for mistakes in (0, 1, 5, 50, 1000):
        for elapsed in (0.0, 1.0, 10_000.0):
            report = score_mission(
                definition,
                earned_points=definition.max_score,
                mistakes=mistakes,
                elapsed_seconds=elapsed,
                optional_done=3,
            )
            assert 0.0 <= report["score_pct"] <= 100.0
            assert report["grade"] in {g.value for g in Grade}


def test_optional_work_earns_an_efficiency_bonus(content: GameContent) -> None:
    definition = content.missions.get("training_prep")
    assert definition is not None
    without = score_mission(
        definition, earned_points=definition.max_score * 0.6, mistakes=0, elapsed_seconds=0.0
    )
    with_optional = score_mission(
        definition,
        earned_points=definition.max_score * 0.6,
        mistakes=0,
        elapsed_seconds=0.0,
        optional_done=2,
    )
    assert with_optional["efficiency_bonus"] >= without["efficiency_bonus"]
    assert with_optional["score_pct"] >= without["score_pct"]


def test_pass_flag_follows_the_mission_threshold(content: GameContent) -> None:
    definition = content.missions.get("analog_water_leak")
    assert definition is not None
    threshold = definition.scoring.pass_score_pct
    low = score_mission(
        definition, earned_points=definition.max_score * 0.1, mistakes=4, elapsed_seconds=9_999.0
    )
    assert low["score_pct"] < threshold
    assert low["passed"] is False


def test_readiness_weights_later_missions_more() -> None:
    early_good = readiness_from_scores([100.0, 0.0], base=50.0)
    late_good = readiness_from_scores([0.0, 100.0], base=50.0)
    assert late_good > early_good


def test_readiness_without_history_returns_the_base() -> None:
    assert readiness_from_scores([], base=42.0) == 42.0


def test_readiness_stays_inside_the_band() -> None:
    assert 0.0 <= readiness_from_scores([0.0] * 10, base=0.0) <= 100.0
    assert 0.0 <= readiness_from_scores([100.0] * 10, base=100.0) <= 100.0
