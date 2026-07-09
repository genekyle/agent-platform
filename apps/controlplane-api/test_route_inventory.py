"""Guardrail for the main.py -> routers/ split (see docs/PLAN_main-split.md).

The split is a pure MOVE: routes change files, not paths. This test pins the full
set of (METHOD, path) the app serves against route_inventory.json, so a route that
gets silently dropped or renamed during the shuffle fails a test instead of shipping.

If you INTENTIONALLY add/remove a route, regenerate the snapshot by writing the
current set to route_inventory.json:
    cd apps/controlplane-api && ../../.venv/bin/python -c "import json, test_route_inventory as t; \
json.dump(sorted(t._current_routes()), open('route_inventory.json','w'), indent=2)"
"""
import json
from pathlib import Path

import main

SNAPSHOT = Path(__file__).with_name("route_inventory.json")


def _current_routes() -> set[str]:
    routes: set[str] = set()
    for r in main.app.routes:
        methods = getattr(r, "methods", None)
        if not methods:
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            routes.add(f"{m} {r.path}")
    return routes


def test_route_inventory_unchanged():
    current = _current_routes()
    expected = set(json.loads(SNAPSHOT.read_text()))
    dropped = expected - current
    added = current - expected
    assert not dropped and not added, (
        f"Route inventory drifted from route_inventory.json.\n"
        f"  DROPPED (gone since snapshot): {sorted(dropped)}\n"
        f"  ADDED   (new since snapshot):  {sorted(added)}\n"
        f"If this change is intentional, regenerate route_inventory.json."
    )
