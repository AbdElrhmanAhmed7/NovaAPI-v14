"""Route introspection that survives FastAPI's lazy router inclusion.

FastAPI 0.160 stopped flattening ``include_router()`` into ``app.routes``.
An included router is now kept as a single ``_IncludedRouter`` node and its
children are materialised *per inclusion context* (prefix, tags, dependencies
are combined lazily). That node has **no** ``.routes`` attribute, so the classic

    for route in app.routes:
        nested = getattr(route, "routes", None)   # -> None on _IncludedRouter

walk silently stops at the app boundary. In NOVA it saw 9 nodes instead of 69
routes, which meant ``core.openapi`` found *zero* guarded operations and shipped
a contract with no ``security`` block and no ``401`` responses.

This module is the single place that knows how to enumerate routes. It works on
both layouts:

* **flattened** (FastAPI < 0.160): ``app.routes`` already holds every
  ``APIRoute`` with its full path – nothing to expand.
* **nested** (FastAPI >= 0.160): ``_IncludedRouter.effective_route_contexts()``
  yields the fully resolved routes, prefixes and inherited dependencies
  included. We duck-type it instead of importing a private class, so the code
  keeps working across versions.

Consumers: :mod:`app.core.openapi` (security + error contract),
``scripts/audit_routes.py`` (route inventory / CI gate) and the tests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Host, Mount, Route, WebSocketRoute

#: HTTP verbs that appear in an OpenAPI document.
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})

#: Verbs that mutate state and therefore need the session guard.
WRITE_METHODS = frozenset({"post", "put", "patch", "delete"})


@dataclass(frozen=True)
class ResolvedRoute:
    """One route, with its *effective* path and inherited dependencies.

    ``kind`` is ``http``, ``websocket``, ``mount`` or ``other``. ``raw`` keeps
    the underlying object (``APIRoute``, ``_EffectiveRouteContext``, …) for
    callers that need something this dataclass does not expose.
    """

    path: str
    methods: frozenset[str] = frozenset()
    name: str = ""
    kind: str = "other"
    endpoint: Callable[..., Any] | None = None
    dependant: Any = None
    response_model: Any = None
    summary: str | None = None
    description: str = ""
    tags: tuple[str, ...] = ()
    status_code: int | None = None
    include_in_schema: bool = True
    dependencies: tuple[Any, ...] = ()
    raw: Any = field(default=None, repr=False, compare=False)

    @property
    def is_websocket(self) -> bool:
        return self.kind == "websocket"

    @property
    def write_methods(self) -> frozenset[str]:
        return self.methods & WRITE_METHODS

    def dependency_calls(self) -> set[Any]:
        """Every dependency callable, at any depth (guards can be nested)."""
        return dependency_calls(self.dependant)


def dependency_calls(dependant: Any) -> set[Any]:
    """Flatten a ``Dependant`` tree into the set of called callables."""
    seen: set[Any] = set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        call = getattr(node, "call", None)
        if call is not None and call not in seen:
            seen.add(call)
        stack.extend(getattr(node, "dependencies", None) or [])
    return seen


def _join(prefix: str, path: str) -> str:
    """Join a mount prefix and a child path the way Starlette does."""
    if not prefix:
        return path
    if not path or path == "/":
        return prefix or "/"
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"


def _methods(route: Any) -> frozenset[str]:
    raw = getattr(route, "methods", None) or set()
    return frozenset(str(method).lower() for method in raw)


def _tags(route: Any) -> tuple[str, ...]:
    return tuple(str(getattr(tag, "value", tag)) for tag in (getattr(route, "tags", None) or []))


def _classify(route: Any, methods: frozenset[str]) -> str:
    """Route kind from its *type* – never from duck-typed attributes.

    Every Starlette ``Route`` owns an ``.app`` (the wrapped handler), so
    ``hasattr(route, "app")`` would mislabel every HTTP route as a mount.
    """
    if isinstance(route, (APIWebSocketRoute, WebSocketRoute)):
        return "websocket"
    if isinstance(route, (Mount, Host)):
        return "mount"
    if isinstance(route, (APIRoute, Route)):
        return "http"
    if methods & HTTP_METHODS:
        return "http"
    return "other"


def _from_context(context: Any) -> ResolvedRoute | None:
    """Normalise a FastAPI ``_EffectiveRouteContext`` (already prefixed)."""
    starlette_route = getattr(context, "starlette_route", None)
    source = context if getattr(context, "path", "") else starlette_route
    if source is None:
        return None
    path = getattr(source, "path", "") or getattr(source, "path_format", "")
    if not path:
        return None
    methods = _methods(source)
    kind = _classify(getattr(context, "original_route", source), methods)
    return ResolvedRoute(
        path=path,
        methods=methods,
        name=getattr(source, "name", "") or "",
        kind=kind,
        endpoint=getattr(source, "endpoint", None),
        dependant=getattr(context, "dependant", None),
        response_model=getattr(context, "response_model", None),
        summary=getattr(context, "summary", None),
        description=getattr(context, "description", "") or "",
        tags=_tags(source),
        status_code=getattr(context, "status_code", None),
        include_in_schema=bool(getattr(context, "include_in_schema", True)),
        dependencies=tuple(getattr(context, "dependencies", None) or ()),
        raw=context,
    )


def _from_route(route: Any, prefix: str) -> ResolvedRoute:
    """Normalise a plain Starlette/FastAPI route object."""
    methods = _methods(route)
    return ResolvedRoute(
        path=_join(prefix, getattr(route, "path", "") or ""),
        methods=methods,
        name=getattr(route, "name", "") or "",
        kind=_classify(route, methods),
        endpoint=getattr(route, "endpoint", None),
        dependant=getattr(route, "dependant", None),
        response_model=getattr(route, "response_model", None),
        summary=getattr(route, "summary", None),
        description=getattr(route, "description", "") or "",
        tags=_tags(route),
        status_code=getattr(route, "status_code", None),
        include_in_schema=bool(getattr(route, "include_in_schema", True)),
        dependencies=tuple(getattr(route, "dependencies", None) or ()),
        raw=route,
    )


def iter_routes(routes: Iterable[Any], *, prefix: str = "") -> Iterator[ResolvedRoute]:
    """Depth-first walk over *every* reachable route, prefixes resolved.

    Handles included routers (both FastAPI layouts), ``Mount`` and ``Host``.
    Static mounts are reported as one ``mount`` entry – their files are not
    routes and must not pollute the inventory.
    """
    for route in routes:
        # FastAPI >= 0.160: lazily included router.
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for context in contexts():
                resolved = _from_context(context)
                if resolved is not None:
                    yield resolved
            continue

        # FastAPI >= 0.160 can also hand us contexts directly.
        if hasattr(route, "original_route") and hasattr(route, "include_in_schema"):
            resolved = _from_context(route)
            if resolved is not None:
                yield resolved
            continue

        resolved = _from_route(route, prefix)
        yield resolved

        # Mount / Host: recurse into the sub-application when it has routes.
        nested_app = getattr(route, "app", None)
        nested = getattr(nested_app, "routes", None) or getattr(route, "routes", None)
        if nested:
            child_prefix = resolved.path if resolved.kind == "mount" else prefix
            yield from iter_routes(nested, prefix=child_prefix)


def iter_app_routes(app: Any) -> Iterator[ResolvedRoute]:
    """Every route of a FastAPI application (see :func:`iter_routes`)."""
    return iter_routes(app.routes)


def http_routes(app: Any, *, schema_only: bool = False) -> list[ResolvedRoute]:
    """HTTP routes, optionally limited to the ones published in OpenAPI."""
    return [
        route
        for route in iter_app_routes(app)
        if route.kind == "http"
        and route.methods & HTTP_METHODS
        and (route.include_in_schema or not schema_only)
    ]


def websocket_routes(app: Any) -> list[ResolvedRoute]:
    return [route for route in iter_app_routes(app) if route.is_websocket]


def guarded_routes(app: Any, guard: Callable[..., Any]) -> list[ResolvedRoute]:
    """Routes whose dependency tree runs *guard* (e.g. ``require_game_write``)."""
    return [route for route in iter_app_routes(app) if guard in route.dependency_calls()]
