"""Settings and the shipped `.env.example`.

`.env.example` is documentation that people copy verbatim, so it is treated as
a contract:

* every setting the application reads must appear there (a new knob that nobody
  can discover is a knob nobody tunes),
* the values it advertises must be the real defaults – except the secret, which
  is deliberately a placeholder,
* it must parse: dotenv escaping of the CORS regex is a classic silent failure.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings, reset_settings_cache

EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"

#: The only value the example is allowed to change: a placeholder, not a default.
PLACEHOLDERS = {"secret_key"}


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A developer's exported NOVA_* variables must not decide the result."""
    import os

    for name in list(os.environ):
        if name.startswith("NOVA_"):
            monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def documented_names() -> set[str]:
    """Active *and* commented-out entries – both teach the operator."""
    text = EXAMPLE.read_text(encoding="utf-8")
    return {name.lower() for name in re.findall(r"^#?\s*NOVA_([A-Z0-9_]+)=", text, flags=re.M)}


def test_every_setting_is_documented_in_the_example_env() -> None:
    missing = set(Settings.model_fields) - documented_names()
    assert not missing, f".env.example does not mention: {sorted(missing)}"


def test_the_example_env_documents_no_unknown_variables() -> None:
    unknown = documented_names() - set(Settings.model_fields)
    assert not unknown, f".env.example advertises settings that do not exist: {sorted(unknown)}"


def test_the_example_env_ships_the_real_defaults() -> None:
    from_example = Settings(_env_file=EXAMPLE)
    defaults = Settings(_env_file=None)
    drifted = {
        name: (getattr(defaults, name), getattr(from_example, name))
        for name in Settings.model_fields
        if name not in PLACEHOLDERS and getattr(defaults, name) != getattr(from_example, name)
    }
    assert not drifted, f".env.example disagrees with the code defaults: {drifted}"
    assert from_example.secret_key == "change-me-in-production"


def test_the_example_env_parses_including_the_cors_regex() -> None:
    """Backslashes in an unquoted dotenv value must survive verbatim."""
    settings = Settings(_env_file=EXAMPLE)
    assert settings.cors_origin_regex == r"https://.*\.(e2b\.app|vercel\.app|netlify\.app)"
    assert re.match(settings.cors_origin_regex, "https://5173-abc.e2b.app")
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert settings.is_sqlite and not settings.is_production


def test_the_environment_wins_over_the_example_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVA_ENVIRONMENT", "prod")
    monkeypatch.setenv("NOVA_STREAM_QUEUE_SIZE", "64")
    settings = Settings(_env_file=EXAMPLE)
    assert settings.environment == "prod" and settings.is_production
    assert settings.stream_queue_size == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stream_queue_size", 0),
        ("stream_batch_size", 5000),
        ("stream_heartbeat_seconds", 0),
        ("stream_reconcile_seconds", 601),
        ("stream_max_connections_per_game", 65),
        ("max_tick_seconds", 0),
        ("environment", "staging"),
        ("default_difficulty", "impossible"),
    ],
)
def test_out_of_range_knobs_are_refused_at_boot(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = Settings(_env_file=None, frontend_origins=" http://a.test , ,http://b.test ")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_log_level_is_normalised_and_settings_are_cached() -> None:
    assert Settings(_env_file=None, log_level="debug").log_level == "DEBUG"
    assert get_settings() is get_settings()
    first = get_settings()
    reset_settings_cache()
    assert get_settings() is not first
