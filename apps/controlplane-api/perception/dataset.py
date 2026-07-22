"""The labeled perception corpus — one row per capture that can teach a witness.

Deliberately assembled from what we ALREADY wrote and never read: the capture artifact (witness
A's features), the screenshot on disk (witness B's), and `observed_page_state` (the label). No new
capture path, no new corpus — the 2026-07-16 reckoning's rule.

**The stale-path trap (found 2026-07-22, and it nearly cost us half the corpus).** `screenshot_refs`
stores an ABSOLUTE path, captured at write time. 101 of the 174 labeled rows point at
`apps/mcp-mock/output/observer-screenshots/…` — a directory that was renamed to `apps/mcp` months
ago. Every one of those files still exists; the pointer is what rotted. A first pass at this
corpus read the paths, found them missing, and concluded the June screenshots had been pruned.
They had not. So resolution goes **path first, then filename under the current artifacts root**,
and the census reports both — a path that resolves only by fallback is a row whose provenance
drifted, and that is worth seeing rather than silently repairing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from perception.facets import StateFacets, facets_for


def artifacts_root() -> Path:
    """Anchor the (relative-by-default) artifacts dir to this package, like every other reader."""
    from settings import settings
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent.parent / base).resolve()
    return base


@dataclass
class Row:
    filename: str
    state: str
    url: str
    domain_id: str
    artifact_path: Optional[Path]
    screenshot_path: Optional[Path]
    facets: StateFacets

    _artifact: Optional[dict[str, Any]] = None

    @property
    def artifact(self) -> dict[str, Any]:
        if self._artifact is None:
            try:
                self._artifact = json.loads(self.artifact_path.read_text())
            except Exception:
                self._artifact = {}
        return self._artifact


def _screenshot_path(refs: Any) -> tuple[Optional[Path], str]:
    """Returns (path, how) where `how` is "path" | "filename" | "" — see the stale-path note."""
    if not isinstance(refs, list) or not refs:
        return None, ""
    ref = refs[0] if isinstance(refs[0], dict) else {}
    raw = ref.get("image_path") or ref.get("path")
    if raw and Path(raw).exists():
        return Path(raw), "path"
    name = ref.get("filename") or (Path(raw).name if raw else "")
    if name:
        candidate = artifacts_root() / "observer-screenshots" / name
        if candidate.exists():
            return candidate, "filename"
    return None, ""


def load_rows(*, require_screenshot: bool = False) -> tuple[list[Row], dict[str, int]]:
    """Every labeled capture, with whatever surfaces survive. Returns (rows, census)."""
    from db import SessionLocal
    from models import TrainingCapture

    root = artifacts_root()
    traces = root / "observer-traces"
    rows: list[Row] = []
    census = {"labeled": 0, "with_artifact": 0, "with_screenshot": 0, "missing_screenshot": 0,
              "missing_artifact": 0, "screenshot_by_stale_path": 0}

    session = SessionLocal()
    try:
        captures = session.query(TrainingCapture).filter(
            TrainingCapture.observed_page_state.isnot(None)).all()
        for cap in captures:
            state = (cap.observed_page_state or "").strip()
            if not state:
                continue
            census["labeled"] += 1
            artifact_path = traces / cap.artifact_filename
            if not artifact_path.exists():
                census["missing_artifact"] += 1
                artifact_path = None
            else:
                census["with_artifact"] += 1
            shot, how = _screenshot_path(cap.screenshot_refs)
            if shot:
                census["with_screenshot"] += 1
                if how == "filename":
                    census["screenshot_by_stale_path"] += 1
            else:
                census["missing_screenshot"] += 1
            if require_screenshot and not shot:
                continue
            rows.append(Row(
                filename=cap.artifact_filename,
                state=state,
                url=cap.url or "",
                domain_id=cap.domain_id or "",
                artifact_path=artifact_path,
                screenshot_path=shot,
                facets=facets_for(state, url=cap.url or "", domain_id=cap.domain_id or ""),
            ))
    finally:
        session.close()
    return rows, census
