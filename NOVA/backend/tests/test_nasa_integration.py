"""NASA open data: caching, retries, provenance and offline fallbacks.

The rule the game depends on is *gameplay never breaks because api.nasa.gov is
down* – and every payload says where it came from. Both are verified here with a
mocked transport, so the suite never touches the network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.cache import TTLCache
from app.core.errors import ExternalServiceError
from app.integrations.nasa_client import NasaClient
from app.main import create_app
from app.missions.registry import GameContent
from conftest import make_settings

pytestmark = pytest.mark.nasa

APOD = {
    "title": "Jezero Crater from orbit",
    "date": "2024-03-02",
    "explanation": "HiRISE view of the delta.",
    "media_type": "image",
    "url": "https://example.invalid/apod.jpg",
}


def nasa_settings(tmp_path: Any, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "nasa_enabled": True,
        "nasa_max_retries": 0,
        "nasa_timeout_seconds": 2.0,
        "nasa_api_key": "SECRET-KEY-123",
    }
    base.update(overrides)
    return make_settings(tmp_path, **base)


def client_with(
    settings: Settings, handler: Any, fallback: dict[str, Any] | None = None
) -> NasaClient:
    return NasaClient(
        settings,
        fallback=fallback if fallback is not None else {"apod": APOD},
        transport=httpx.MockTransport(handler),
    )


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------
async def test_cache_hits_misses_and_expiry() -> None:
    cache: TTLCache[dict[str, int]] = TTLCache(ttl_seconds=0.05)
    assert await cache.get("k") is None
    await cache.set("k", {"v": 1})
    assert await cache.get("k") == {"v": 1}
    assert cache.stats() == {"entries": 1, "hits": 1, "misses": 1}

    await asyncio.sleep(0.08)
    assert await cache.get("k") is None
    # An expired entry is still readable when the caller opts into staleness.
    assert await cache.get("k", allow_stale=True) == {"v": 1}


async def test_cache_evicts_the_oldest_entry() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60, max_entries=2)
    await cache.set("a", "1")
    await asyncio.sleep(0.01)
    await cache.set("b", "2")
    await asyncio.sleep(0.01)
    await cache.set("c", "3")
    assert await cache.get("a") is None
    assert await cache.get("b") == "2"
    assert await cache.get("c") == "3"


async def test_cache_clear_and_per_key_ttl() -> None:
    cache: TTLCache[str] = TTLCache(ttl_seconds=60)
    await cache.set("short", "x", ttl_seconds=0.01)
    await cache.set("long", "y")
    await asyncio.sleep(0.03)
    assert await cache.get("short") is None
    assert await cache.get("long") == "y"
    await cache.clear()
    assert cache.stats()["entries"] == 0


# ---------------------------------------------------------------------------
# Client behaviour
# ---------------------------------------------------------------------------
async def test_live_payload_is_labelled_and_the_key_is_masked(tmp_path: Any) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=APOD)

    nasa = client_with(nasa_settings(tmp_path), handler)
    try:
        payload = await nasa.apod("2024-03-02")
    finally:
        await nasa.close()

    assert payload["provenance"] == "live"
    assert payload["data"] == APOD
    assert payload["source"] == "NASA APOD"
    assert payload["fetched_at"].endswith("Z")
    assert "SECRET-KEY-123" not in payload["url"]
    assert "***" in payload["url"]
    assert dict(seen[0].url.params)["date"] == "2024-03-02"


async def test_second_call_is_served_from_cache(tmp_path: Any) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=APOD)

    nasa = client_with(nasa_settings(tmp_path), handler)
    try:
        first = await nasa.apod()
        second = await nasa.apod()
    finally:
        await nasa.close()

    assert first["provenance"] == "live"
    assert second["provenance"] == "cache"
    assert calls == 1
    assert nasa.cache_stats()["hits"] == 1


async def test_disabled_integration_never_touches_the_network(tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("network call while NASA is disabled")

    nasa = client_with(nasa_settings(tmp_path, nasa_enabled=False), handler)
    payload = await nasa.apod()
    assert payload["provenance"] == "bundled"
    assert payload["reason"] == "live data disabled"
    assert "offline snapshot" in payload["note"]
    assert nasa.cache_stats()["entries"] == 0


async def test_retries_are_bounded_then_the_bundled_snapshot_is_served(tmp_path: Any) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "upstream down"})

    nasa = client_with(nasa_settings(tmp_path, nasa_max_retries=1), handler)
    try:
        payload = await nasa.apod()
    finally:
        await nasa.close()

    assert calls == 2, "one attempt plus one retry"
    assert payload["provenance"] == "bundled"
    assert payload["data"] == APOD
    assert payload["reason"]


async def test_a_stale_cache_entry_beats_the_bundled_snapshot(tmp_path: Any) -> None:
    state = {"fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["fail"]:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, json=APOD)

    nasa = client_with(nasa_settings(tmp_path, nasa_cache_ttl_seconds=0), handler)
    try:
        assert (await nasa.apod())["provenance"] == "live"
        await asyncio.sleep(0.02)
        state["fail"] = True
        payload = await nasa.apod()
    finally:
        await nasa.close()

    assert payload["provenance"] == "cache"
    assert payload["stale"] is True
    assert payload["data"] == APOD


async def test_no_fallback_and_no_network_is_an_explicit_502(tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    nasa = client_with(nasa_settings(tmp_path), handler, fallback={})
    try:
        with pytest.raises(ExternalServiceError) as exc:
            await nasa.apod()
    finally:
        await nasa.close()
    assert exc.value.status_code == 502


async def test_malformed_json_is_treated_as_a_failure(tmp_path: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    nasa = client_with(nasa_settings(tmp_path), handler)
    try:
        payload = await nasa.apod()
    finally:
        await nasa.close()
    assert payload["provenance"] == "bundled"


@pytest.mark.parametrize(
    ("call", "fragment", "params"),
    [
        ("mars_photos", "/mars-photos/api/v1/rovers/curiosity/photos", {"sol": "1000"}),
        ("epic", "/EPIC/api/natural", {}),
        ("donki", "/DONKI/GST", {"startDate": "2024-01-01"}),
        ("neo_feed", "/neo/rest/v1/feed", {"end_date": "2024-01-08"}),
    ],
)
async def test_each_endpoint_addresses_the_right_product(
    tmp_path: Any, call: str, fragment: str, params: dict[str, str]
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    nasa = client_with(nasa_settings(tmp_path), handler)
    kwargs: dict[str, Any] = {
        "mars_photos": {"sol": 1000, "camera": "FHAZ", "page": 2},
        "epic": {},
        "donki": {"start_date": "2024-01-01", "end_date": "2024-01-08"},
        "neo_feed": {"start_date": "2024-01-01", "end_date": "2024-01-08"},
    }[call]
    try:
        await getattr(nasa, call)(**kwargs)
    finally:
        await nasa.close()

    url = str(seen[0].url)
    assert fragment in url
    for _key, value in params.items():
        assert value in url
    assert "SECRET-KEY-123" in url, "the key is sent upstream, only masked in responses"


async def test_power_needs_no_api_key(tmp_path: Any) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"properties": {"parameter": {"T2M": {}}}})

    nasa = client_with(nasa_settings(tmp_path), handler)
    try:
        await nasa.power_monthly(
            latitude=30.0, longitude=31.0, parameters=["T2M"], start_year=2001, end_year=2005
        )
    finally:
        await nasa.close()

    assert "power.larc.nasa.gov" in str(seen[0].url)
    assert "api_key" not in dict(seen[0].url.params)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------
def test_endpoints_label_provenance_over_http(tmp_path: Any, content: GameContent) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    settings = nasa_settings(tmp_path, nasa_max_retries=0)
    nasa = NasaClient(
        settings,
        fallback=content.read_json("nasa/fallback.json"),
        transport=httpx.MockTransport(handler),
    )
    with TestClient(create_app(settings, nasa=nasa)) as client:
        for path in ("apod", "mars-photos", "donki", "neo", "epic"):
            response = client.get(f"/api/v1/nasa/{path}")
            assert response.status_code == 200, (path, response.text)
            body = response.json()
            assert body["provenance"] == "bundled", path
            assert body["source"] and body["fetched_at"]
            assert body["reason"]

        status = client.get("/api/v1/nasa/status").json()
        assert status["enabled"] is True
        assert status["api_key_configured"] is True
        # A failed upstream is never cached: the next call retries the network.
        assert status["cache"]["entries"] == 0
        assert status["cache"]["misses"] >= 5
        assert status["cache"]["hits"] == 0
        assert "DEMO_KEY" in status["note"]


def test_status_reports_the_demo_key(tmp_path: Any, content: GameContent) -> None:
    settings = make_settings(tmp_path, nasa_enabled=False)
    with TestClient(create_app(settings)) as client:
        status = client.get("/api/v1/nasa/status").json()
        assert status["enabled"] is False
        assert status["api_key_configured"] is False
        assert client.get("/api/v1/nasa/apod").json()["provenance"] == "bundled"


@pytest.mark.parametrize(
    ("path", "query"),
    [
        ("/api/v1/nasa/apod", {"date": "not-a-date"}),
        ("/api/v1/nasa/mars-photos", {"sol": 9000}),
        ("/api/v1/nasa/mars-photos", {"page": 0}),
        ("/api/v1/nasa/mars-photos", {"camera": "WAY_TOO_LONG_NAME"}),
        ("/api/v1/nasa/donki", {"start_date": "2024-13-45"}),
    ],
)
def test_query_validation(client: TestClient, path: str, query: dict[str, Any]) -> None:
    response = client.get(path, params=query)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
