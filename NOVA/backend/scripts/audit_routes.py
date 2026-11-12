#!/usr/bin/env python3
"""Audit the HTTP contract: inventory every route and gate the risky mistakes.

    python scripts/audit_routes.py                 # human readable + docs/
    python scripts/audit_routes.py --json          # machine readable
    python scripts/audit_routes.py --check         # CI gate (no files written)
    python scripts/audit_routes.py --strict        # warnings fail too

Why this exists
---------------
FastAPI >= 0.160 keeps included routers as nested ``_IncludedRouter`` objects,
so anything that walks ``app.routes`` by hand under-reports (it used to see 9 of
75 routes here). :mod:`app.core.routing` fixes the traversal; this script turns
it into a *reviewable inventory* plus checks that catch the mistakes a human
reviewer misses:

* a published endpoint with no ``response_model`` (untyped contract),
* a game-scoped write with no session guard (auth bypass),
* a guarded endpoint whose OpenAPI operation lost its ``security`` / ``401``,
* auth headers leaking into ``parameters`` instead of ``security``,
* routes the walker cannot see but OpenAPI can (or vice versa).

Exit code 0 = clean, 1 = errors (or warnings with ``--strict``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, get_args, get_origin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.core.routing import (  # noqa: E402
    HTTP_METHODS,
    ResolvedRoute,
    http_routes,
    iter_app_routes,
    websocket_routes,
)
from app.dependencies import require_game_write  # noqa: E402
from app.main import create_app  # noqa: E402

DOCS_DIR = ROOT / "docs"

#: Writes that are deliberately unguarded, with the reason. Anything else that
#: mutates a game must run ``require_game_write``.
UNGUARDED_WRITES: dict[tuple[str, str], str] = {
    ("post", "/api/v1/games"): "Creates the session; there is no game id to authorise yet.",
    ("post", "/api/v1/games/import"): "Creates a *new* session from an exported bundle.",
    ("post", "/api/v1/games/{game_id}/resume"): (
        "Token recovery: the unguessable game id is the credential for a returning player."
    ),
    ("post", "/api/v1/earth/compare"): "Stateless analysis, not scoped to a save (rate limited).",
    ("post", "/games"): "Legacy session creation, mirrors POST /api/v1/games.",
}

#: Endpoints whose response is intentionally free-form (physics telemetry,
#: world flags, NASA upstream payloads). Reported, never failed.
FREEFORM_OK = ("telemetry", "world", "data", "params", "command", "result", "analysis", "payload")


@dataclass
class Finding:
    severity: str  # error | warning | info
    code: str
    where: str
    message: str

    def render(self) -> str:
        icon = {"error": "✗", "warning": "!", "info": "·"}[self.severity]
        return f"  {icon} [{self.code}] {self.where}\n      {self.message}"


def _operations(schema: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    verbs = HTTP_METHODS - {"head", "options", "trace"}
    return {
        (path, method): operation
        for path, methods in schema.get("paths", {}).items()
        for method, operation in methods.items()
        if method in verbs
    }


def _response_schema(operation: dict[str, Any]) -> dict[str, Any]:
    for status, entry in sorted(operation.get("responses", {}).items()):
        if str(status).startswith("2"):
            return dict(entry.get("content", {}).get("application/json", {}).get("schema", {}))
    return {}


def _loose_fields(schema: dict[str, Any], response: dict[str, Any]) -> list[str]:
    """Properties of the 2xx model that accept arbitrary JSON."""
    ref = response.get("$ref", "")
    name = ref.rsplit("/", 1)[-1] if ref else ""
    model = schema.get("components", {}).get("schemas", {}).get(name, {})
    loose: list[str] = []
    for field, spec in (model.get("properties") or {}).items():
        if field in FREEFORM_OK:
            continue
        candidates = [spec, *spec.get("anyOf", [])]
        for candidate in candidates:
            if candidate.get("type") == "object" and not candidate.get("properties"):
                loose.append(field)
                break
            if candidate.get("type") == "array":
                items = candidate.get("items", {})
                if items.get("type") == "object" and not items.get("properties"):
                    loose.append(field)
                    break
    return loose


def _model_name(route: ResolvedRoute) -> str:
    """Readable model name, keeping generic parameters (``list[ItemDefinition]``)."""
    return _render_type(route.response_model)


def _render_type(model: Any) -> str:
    if model is None:
        return "—"
    origin = get_origin(model)
    if origin is not None:
        args = ", ".join(_render_type(arg) for arg in get_args(model))
        return f"{getattr(origin, '__name__', str(origin))}[{args}]"
    return getattr(model, "__name__", None) or str(model).replace("typing.", "")


def audit() -> tuple[list[dict[str, Any]], list[Finding], dict[str, Any]]:
    # Deterministic settings: the inventory must not depend on a local .env.
    app = create_app(Settings(_env_file=None, nasa_enabled=False))
    schema = app.openapi()
    operations = _operations(schema)

    routes = list(iter_app_routes(app))
    published = http_routes(app, schema_only=True)
    inventory: list[dict[str, Any]] = []
    findings: list[Finding] = []

    walked = {
        (route.path, method) for route in published for method in route.methods & HTTP_METHODS
    }
    documented = set(operations)
    for path, method in sorted(documented - walked):
        findings.append(
            Finding(
                "error",
                "route_not_walked",
                f"{method.upper()} {path}",
                "OpenAPI publishes this route but the route walker cannot see it "
                "(nested include_router blind spot).",
            )
        )
    for path, method in sorted(walked - documented):
        findings.append(
            Finding(
                "error",
                "route_not_documented",
                f"{method.upper()} {path}",
                "Route is registered but missing from the published contract.",
            )
        )

    for route in sorted(published, key=lambda r: (r.tags[:1] or [""], r.path, sorted(r.methods))):
        calls = route.dependency_calls()
        guarded = require_game_write in calls
        for method in sorted(route.methods & HTTP_METHODS):
            operation = operations.get((route.path, method), {})
            where = f"{method.upper()} {route.path}"
            response = _response_schema(operation)
            loose = _loose_fields(schema, response)

            inventory.append(
                {
                    "method": method.upper(),
                    "path": route.path,
                    "tag": (route.tags or ("—",))[0],
                    "summary": route.summary or "",
                    "response_model": _model_name(route),
                    "status_code": route.status_code or 200,
                    "guarded": guarded,
                    "idempotent": any(
                        parameter.get("name") == "Idempotency-Key"
                        for parameter in operation.get("parameters", [])
                    ),
                    "errors_documented": sorted(
                        status
                        for status in operation.get("responses", {})
                        if str(status)[0] in "45"
                    ),
                    "loose_fields": loose,
                    "deprecated": bool(operation.get("deprecated")),
                }
            )

            if route.response_model is None:
                findings.append(
                    Finding("error", "missing_response_model", where, "No typed response model.")
                )
            if not route.summary:
                findings.append(Finding("warning", "missing_summary", where, "No OpenAPI summary."))
            if not route.tags:
                findings.append(Finding("warning", "missing_tag", where, "No OpenAPI tag."))

            is_write = method in {"post", "put", "patch", "delete"}
            scoped = "{game_id}" in route.path
            if is_write and scoped and not guarded:
                reason = UNGUARDED_WRITES.get((method, route.path))
                if reason is None:
                    findings.append(
                        Finding(
                            "error",
                            "unguarded_write",
                            where,
                            "Mutates a game session without require_game_write; "
                            "with NOVA_AUTH_ENABLED=true any caller who knows a game id "
                            "can write to it.",
                        )
                    )
            if guarded:
                if not operation.get("security"):
                    findings.append(
                        Finding(
                            "error",
                            "missing_security",
                            where,
                            "Guarded handler but the operation declares no security scheme.",
                        )
                    )
                for status in ("401", "409"):
                    if status not in operation.get("responses", {}):
                        findings.append(
                            Finding(
                                "error",
                                "missing_error_response",
                                where,
                                f"Guarded handler does not document {status}.",
                            )
                        )
            for parameter in operation.get("parameters", []):
                if parameter.get("in") == "header" and parameter.get("name") in {
                    "X-Session-Token",
                    "X-API-Key",
                }:
                    findings.append(
                        Finding(
                            "error",
                            "auth_header_in_parameters",
                            where,
                            f"{parameter['name']} must be a security scheme, not a parameter.",
                        )
                    )
            if loose:
                findings.append(
                    Finding(
                        "info",
                        "freeform_fields",
                        where,
                        "Untyped object fields: " + ", ".join(loose),
                    )
                )

    for socket in websocket_routes(app):
        inventory.append(
            {
                "method": "WS",
                "path": socket.path,
                "tag": (socket.tags or ("Stream",))[0],
                "summary": "Live event tail (read model)",
                "response_model": "—",
                "status_code": None,
                "guarded": False,
                "idempotent": False,
                "errors_documented": [],
                "loose_fields": [],
                "deprecated": False,
            }
        )

    summary = {
        "routes_total": len(routes),
        "http_published": len(published),
        "operations": len(operations),
        "websockets": len(websocket_routes(app)),
        "mounts": sum(1 for route in routes if route.kind == "mount"),
        "guarded_operations": sum(1 for row in inventory if row["guarded"]),
        "schemas": len(schema.get("components", {}).get("schemas", {})),
        "by_tag": dict(sorted(Counter(row["tag"] for row in inventory).items())),
        "findings": dict(sorted(Counter(finding.severity for finding in findings).items())),
    }
    return inventory, findings, summary


def render_markdown(inventory: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# NOVA API – route inventory",
        "",
        "Generated by `python scripts/audit_routes.py`. Do not edit by hand.",
        "",
        f"**{summary['operations']} published operations** · "
        f"{summary['guarded_operations']} guarded writes · "
        f"{summary['websockets']} websocket · {summary['schemas']} schemas",
        "",
        "`Auth` = the handler runs `require_game_write` (needs `X-Session-Token` "
        "when `NOVA_AUTH_ENABLED=true`).",
        "",
    ]
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for row in inventory:
        by_tag.setdefault(row["tag"], []).append(row)
    for tag, rows in by_tag.items():
        lines += [
            f"## {tag}",
            "",
            "| Method | Path | Response | Auth | Summary |",
            "| --- | --- | --- | :-: | --- |",
        ]
        for row in sorted(rows, key=lambda r: (r["path"], r["method"])):
            deprecated = " *(deprecated)*" if row["deprecated"] else ""
            lines.append(
                f"| `{row['method']}` | `{row['path']}` | `{row['response_model']}` | "
                f"{'🔒' if row['guarded'] else '·'} | {row['summary']}{deprecated} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    parser.add_argument("--check", action="store_true", help="Do not write docs; fail on errors.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    args = parser.parse_args()

    inventory, findings, summary = audit()
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    if args.json:
        print(
            json.dumps(
                {
                    "summary": summary,
                    "routes": inventory,
                    "findings": [asdict(finding) for finding in findings],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print("NOVA route audit")
        print("=" * 72)
        for key, value in summary.items():
            if key != "by_tag":
                print(f"  {key:22} {value}")
        print()
        for finding in findings:
            if finding.severity != "info" or args.strict:
                print(finding.render())
        infos = [f for f in findings if f.severity == "info"]
        if infos:
            print(f"  · {len(infos)} operation(s) still expose free-form object fields")
        print()
        print(f"{len(errors)} error(s), {len(warnings)} warning(s)")

    if not args.check:
        args.docs_dir.mkdir(parents=True, exist_ok=True)
        (args.docs_dir / "routes.json").write_text(
            json.dumps({"summary": summary, "routes": inventory}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        (args.docs_dir / "routes.md").write_text(
            render_markdown(inventory, summary), encoding="utf-8"
        )
        if not args.json:
            print(f"wrote {args.docs_dir / 'routes.md'} and routes.json")

    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
