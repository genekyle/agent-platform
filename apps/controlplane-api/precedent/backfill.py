"""One-shot backfill: embed the historical corpus into the vector store.

Usage (from apps/controlplane-api, venv python):
    PERCEPTION_CACHE_DIR=<data_root>/derived/embeddings \\
    python -m precedent.backfill --data-root /path/to/apps/mcp/output

Idempotent on source_key — safe to re-run; only new rows embed. Prints counts, timing, and
the vision-coverage number (the 58/773 decision-capture gap will show here until the
write-time rider closes it).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .embedder import DEFAULT_WEIGHTS, FusionEmbedder, doc_from_decision, doc_from_transition
from .store import VectorStore


def _iter_jsonl(path: Path):
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _resolve_screenshot(doc, data_root: Path) -> None:
    """Transitions store absolute paths; decisions store basenames. Resolve both against the
    real screenshot dir and drop refs whose file is gone (loudly counted, never silently)."""
    if doc.screenshot is None:
        return
    p = Path(doc.screenshot)
    if not p.is_absolute():
        p = data_root / "observer-screenshots" / p.name
    doc.screenshot = p if p.exists() else None


def run(data_root: Path, db_path: Path, limit: int | None = None) -> dict:
    store = VectorStore(db_path)
    embedder = FusionEmbedder()
    store.set_info("weights", DEFAULT_WEIGHTS)
    store.set_info("data_root", str(data_root))

    docs = []
    transitions_dir = data_root / "transitions"
    for f in sorted(transitions_dir.glob("session_*.jsonl")):
        for row in _iter_jsonl(f):
            for half in ("before", "after"):
                doc = doc_from_transition(row, half)
                if doc:
                    docs.append(doc)
    journal = data_root / "cache" / "decision_journal.jsonl"
    if journal.exists():
        for row in _iter_jsonl(journal):
            doc = doc_from_decision(row)
            if doc:
                docs.append(doc)
    if limit:
        docs = docs[:limit]

    t0 = time.time()
    added = skipped = missing_shot = 0
    for i, doc in enumerate(docs):
        if store.has(doc.source_key):
            skipped += 1
            continue
        wanted_shot = doc.screenshot is not None
        _resolve_screenshot(doc, data_root)
        if wanted_shot and doc.screenshot is None:
            missing_shot += 1
        vec, has_vision = embedder.embed_doc(doc)
        store.add(doc, vec, has_vision)
        added += 1
        if added % 100 == 0:
            store.commit()
            embedder.flush()
            print(f"  {added} embedded ({time.time() - t0:.0f}s)", flush=True)
    store.commit()
    embedder.flush()

    counts = store.counts()
    report = {
        "docs_seen": len(docs),
        "added": added,
        "skipped_existing": skipped,
        "screenshot_refs_broken": missing_shot,
        "seconds": round(time.time() - t0, 1),
        "store": counts,
        "db": str(db_path),
    }
    store.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True, type=Path)
    ap.add_argument("--db", type=Path, default=None,
                    help="default: <data-root>/vectors.db")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    db = args.db or (args.data_root / "vectors.db")
    report = run(args.data_root, db, args.limit)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
