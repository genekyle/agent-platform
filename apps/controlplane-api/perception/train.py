"""Fit both witnesses on the labeled corpus and persist them as artifacts.

"Train" is generous: witness A is a TF-IDF centroid and witness B is an average of vectors, so
this is minutes of CPU and a JSON file each. That is the point — a correction can be folded in
live (`PrototypeBank.update`), and a full refit is cheap enough to run after every drive rather
than as a ceremony.

Artifacts follow the conventions the other trainers already use (`<artifacts>/models/<ts>__<type>/`
with `model.json` + `metrics.json`), so the registry, the cockpit and a future promotion gate see
them as the same kind of thing as the grounding and stage-observer models.

    cd apps/controlplane-api && ../../.venv/bin/python -m perception.train
    ../../.venv/bin/python -m perception.train --encoder apple --promote
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from perception.dataset import artifacts_root, load_rows
from perception.dom_witness import FEATURE_SET_VERSION, TfidfCentroidWitness, extract_tokens
from perception.encoders import get_encoder
from perception.observer import Observer
from perception.prototypes import PrototypeBank

MODEL_TYPE = "perception_observer_v1"

#: The `current` symlink-free pointer the runtime reads. A plain file so it survives being copied
#: between machines, same reason the other artifacts are JSON.
CURRENT = "perception_current.json"


def _utcnow() -> str:
    from training import utcnow_iso
    return utcnow_iso()


def fit(*, encoder_name: str = "apple") -> dict[str, Any]:
    """Fit witness A over every labeled capture and witness B over those with a screenshot."""
    rows, census = load_rows()
    dom_rows = [r for r in rows if r.artifact_path]
    dom = TfidfCentroidWitness().fit(
        (r.state, extract_tokens(r.artifact)) for r in dom_rows)

    encoder = get_encoder(encoder_name)
    visual_examples: list[tuple[str, list[float]]] = []
    for row in rows:
        if not row.screenshot_path:
            continue
        vec = encoder.embed(row.screenshot_path)
        if vec:
            visual_examples.append((row.state, vec))
    flush = getattr(encoder, "flush", None)
    if flush:
        flush()
    visual = PrototypeBank(encoder_name).fit(visual_examples)

    return {
        "dom": dom,
        "visual": visual,
        "encoder": encoder,
        "metrics": {
            "census": census,
            "encoder": encoder_name,
            "feature_set": FEATURE_SET_VERSION,
            "dom_rows": len(dom_rows),
            "dom_states": len(dom.centroids),
            "visual_rows": len(visual_examples),
            "visual_states": len(visual.prototypes),
            "examples_per_state": dict(Counter(r.state for r in rows).most_common()),
            "created_at": _utcnow(),
        },
    }


def save(fitted: dict[str, Any], *, promote: bool = False) -> Path:
    root = artifacts_root() / "models" / f"{_utcnow().replace(':', '-')}__{MODEL_TYPE}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "model.json").write_text(json.dumps({
        "model_type": MODEL_TYPE,
        "created_at": fitted["metrics"]["created_at"],
        "dom": fitted["dom"].to_dict(),
        "visual": fitted["visual"].to_dict(),
    }))
    (root / "metrics.json").write_text(json.dumps(fitted["metrics"], indent=2))
    if promote:
        (artifacts_root() / "models" / CURRENT).write_text(
            json.dumps({"model_dir": str(root), "promoted_at": _utcnow()}, indent=2))
    return root


def load_observer(model_dir: Optional[Path] = None) -> Optional[Observer]:
    """The runtime entry point: hand back a ready Observer, or None if nothing is promoted.

    None is a real answer and callers must handle it — an unpromoted perception stack means the
    controller runs exactly as it does today, not that it runs blind on a half-built witness.
    """
    if model_dir is None:
        pointer = artifacts_root() / "models" / CURRENT
        if not pointer.exists():
            return None
        try:
            model_dir = Path(json.loads(pointer.read_text())["model_dir"])
        except Exception:
            return None
    blob_path = Path(model_dir) / "model.json"
    if not blob_path.exists():
        return None
    blob = json.loads(blob_path.read_text())
    visual_blob = blob.get("visual") or {}
    encoder_name = visual_blob.get("encoder") or ""
    encoder = None
    if encoder_name:
        try:
            encoder = get_encoder(encoder_name)
        except ValueError:
            encoder = None
    return Observer(
        dom=TfidfCentroidWitness.from_dict(blob.get("dom") or {}),
        visual=PrototypeBank.from_dict(visual_blob) if visual_blob else None,
        encoder=encoder,
        visual_name=f"visual:{encoder_name}" if encoder_name else "visual",
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fit and persist the perception witnesses.")
    ap.add_argument("--encoder", default="apple", help="apple | pixel32 | clip")
    ap.add_argument("--promote", action="store_true",
                    help="point the runtime at this build (writes models/perception_current.json)")
    args = ap.parse_args(argv)

    fitted = fit(encoder_name=args.encoder)
    path = save(fitted, promote=args.promote)
    m = fitted["metrics"]
    print(f"dom:    {m['dom_rows']} rows over {m['dom_states']} states")
    print(f"visual: {m['visual_rows']} rows over {m['visual_states']} states ({m['encoder']})")
    print(f"saved:  {path}{'  (promoted)' if args.promote else ''}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
