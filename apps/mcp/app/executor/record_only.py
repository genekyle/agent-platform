"""RecordOnlyDriver — logs the intended action, executes nothing.

The safety / data-collection mode: the selector decides what to do, but the
driver does NOT move the cursor or click. Instead it records the intended action
(target, action, CSS point) so a human performs it under supervision and the
selector's decision is captured for the corpus. This is the server-side analogue
of the Movement Playground recordings, and the default-safe driver before the
input model is trusted to act autonomously.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.artifacts import ARTIFACTS_DIR

from .driver import ActionRequest, ExecResult, TrajectoryDriver, target_css_point

logger = logging.getLogger("mcp.executor.record_only")


def _log_path() -> Path:
    p = ARTIFACTS_DIR / "cache" / "executor_intents.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


class RecordOnlyDriver(TrajectoryDriver):
    name = "record_only"

    async def move_and_act(self, *, browser_url, request: ActionRequest, tab_id=None, tab_url=None, start=None) -> ExecResult:
        x, y = target_css_point(request.target_bbox, request.device_scale_factor)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action_id": request.action_id,
            "backend_node_id": request.backend_node_id,
            "target_bbox": request.target_bbox,
            "css_point": [x, y],
            "value": request.value,
            "tab_url": tab_url,
            "executed": False,
            "mode": "record_only",
        }
        try:
            with _log_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            pass
        logger.info("record_only: logged intent %s at css(%.1f,%.1f) — NOT executed", request.action_id, x, y)
        return ExecResult(ok=True, driver=self.name, action_id=request.action_id, css_point=(x, y),
                          detail="recorded intent; not executed (human performs it)",
                          extra={"executed": False})
