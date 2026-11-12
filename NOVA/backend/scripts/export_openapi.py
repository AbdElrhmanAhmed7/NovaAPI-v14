#!/usr/bin/env python3
"""Export the published OpenAPI contract for the React client.

    python scripts/export_openapi.py                     # write docs/openapi.json
    python scripts/export_openapi.py --check             # CI: fail on drift
    python scripts/export_openapi.py --output /tmp/o.json

The document is the hand-off to the frontend team:

    npx openapi-typescript docs/openapi.json -o src/api/nova.d.ts

``core.openapi.customise_openapi`` already sorts paths and schemas, so the file
diffs cleanly in review. ``--check`` regenerates it in memory and compares, which
catches "handler changed but the contract was not re-exported" in CI.

No database is touched: building the app and reading its schema never enters the
lifespan, so this runs anywhere (including a cold CI container).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

DEFAULT_OUTPUT = ROOT / "docs" / "openapi.json"


def build_schema() -> dict[str, Any]:
    """The contract, independent of any local ``.env``."""
    app = create_app(Settings(_env_file=None, nasa_enabled=False))
    return dict(app.openapi())


def serialise(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="Compare with the committed file; write nothing."
    )
    args = parser.parse_args()

    schema = build_schema()
    document = serialise(schema)
    operations = sum(
        1
        for methods in schema.get("paths", {}).values()
        for method in methods
        if method in {"get", "post", "put", "patch", "delete"}
    )

    if args.check:
        if not args.output.is_file():
            print(f"✗ {args.output} is missing – run scripts/export_openapi.py")
            return 1
        if args.output.read_text(encoding="utf-8") != document:
            print(f"✗ {args.output} is stale – run scripts/export_openapi.py and commit it")
            return 1
        print(f"✓ {args.output} is up to date ({operations} operations)")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(document, encoding="utf-8")
    print(
        f"wrote {args.output} "
        f"({len(schema.get('paths', {}))} paths, {operations} operations, "
        f"{len(schema.get('components', {}).get('schemas', {}))} schemas, "
        f"{args.output.stat().st_size} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
