#!/usr/bin/env python
"""Backfill TrainingCapture.ax_candidate_count from the on-disk .ax.json sidecars.

The column (v16) records the AX faucet's per-capture yield. It's populated going
forward straight from the /capture response, but historical rows default to 0 —
which would make GET /api/training/coverage report every old capture as "dry" even
when its sidecar has candidates. The sidecar's `proposal_count` is the ground truth
for a past capture, so re-derive the column from it. Idempotent: safe to re-run.

Usage (from repo root, with the project's .venv):
    .venv/bin/python apps/controlplane-api/scripts/backfill_ax_candidate_count.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import SessionLocal  # noqa: E402
from models import TrainingCapture  # noqa: E402
from settings import settings  # noqa: E402


def _traces_dir() -> Path:
    return Path(settings.observer_artifacts_dir) / "observer-traces"


def main() -> None:
    traces_dir = _traces_dir()
    db = SessionLocal()
    updated = changed = missing = dry = 0
    try:
        for capture in db.query(TrainingCapture).all():
            sidecar = traces_dir / f"{capture.artifact_filename}.ax.json"
            if not sidecar.exists():
                missing += 1
                continue
            try:
                count = int(json.loads(sidecar.read_text()).get("proposal_count", 0) or 0)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {capture.artifact_filename}: unreadable sidecar ({exc})")
                continue
            updated += 1
            if count == 0:
                dry += 1
            if capture.ax_candidate_count != count:
                capture.ax_candidate_count = count
                changed += 1
        db.commit()
    finally:
        db.close()

    print(
        f"backfill done: {updated} captures had a sidecar, {changed} rows updated, "
        f"{dry} genuinely dry (0 AX candidates), {missing} without a sidecar (pre-2026-06-15)."
    )


if __name__ == "__main__":
    main()
