"""Write-time vector banking — the W1 rider (PLAN_inhouse_reasoner_v1 §4).

The crank is the BANK, not a batch job someone remembers: a decision row landing in the
journal and a transition row landing in the corpus each embed into `vectors.db` at their one
choke point. Both hooks are best-effort aids — they must never sink the drive they observe —
and the backfill CLI remains the idempotent sweep for anything a failure here misses
(`source_key` makes the overlap harmless).

Wiring: `install()` (called at API startup) registers `on_decision_record` as a decision-journal
sink; `step_runner.record_transition` calls `on_transition_row` directly (same app, no cycle).
`settings.precedent_write_vectors` turns the whole rider off.
"""
from __future__ import annotations

import os
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()
_stores: dict[Path, Any] = {}   # keyed by data root — one per root, so tests with tmp roots
_embedder = None                # and a re-pointed live process each get the right store
_installed = False


def _enabled() -> bool:
    try:
        from settings import settings

        return bool(settings.precedent_write_vectors)
    except Exception:  # noqa: BLE001 — no settings, no rider; never an error
        return False


def _data_root() -> Path:
    """The REAL corpus root. Env override first (tests, and worktree sessions pointing at the
    main checkout's data — the cockpit-reach-parity rule), then the same resolution
    `step_runner._transitions_dir` uses."""
    env = os.environ.get("PRECEDENT_DATA_ROOT")
    if env:
        return Path(env)
    from settings import settings

    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent.parent / base).resolve()
    return base


def _ensure(root: Path) -> tuple[Any, Any]:
    """The store for this data root + the shared embedder, created on first use so importing
    this module is free."""
    global _embedder
    if root not in _stores:
        from .embedder import DEFAULT_WEIGHTS, FusionEmbedder
        from .store import VectorStore

        store = VectorStore(root / "vectors.db")
        store.set_info("weights", DEFAULT_WEIGHTS)
        _stores[root] = store
        if _embedder is None:
            _embedder = FusionEmbedder()
    return _stores[root], _embedder


def _bank(doc) -> Optional[int]:
    from .backfill import _resolve_screenshot

    root = _data_root()
    store, embedder = _ensure(root)
    with _lock:
        if store.has(doc.source_key):
            return None
        _resolve_screenshot(doc, root)
        vec, has_vision = embedder.embed_doc(doc)
        pid = store.add(doc, vec, has_vision)
        store.commit()
        embedder.flush()
        return pid


def on_decision_record(record: Any) -> None:
    """Decision-journal sink: one journaled decision -> one vector. Receives the REDACTED
    record after the row landed, so nothing reaches the store that the journal refused."""
    if not _enabled():
        return
    try:
        from .embedder import doc_from_decision

        row = asdict(record) if is_dataclass(record) else dict(record)
        doc = doc_from_decision(row)
        if doc is not None:
            _bank(doc)
    except Exception:  # noqa: BLE001 — an aid, never a dependency
        pass


def on_transition_row(row: dict) -> None:
    """Transition-corpus hook: one banked row -> two vectors (before + after halves)."""
    if not _enabled():
        return
    try:
        from .embedder import doc_from_transition

        for half in ("before", "after"):
            doc = doc_from_transition(row, half)
            if doc is not None:
                _bank(doc)
    except Exception:  # noqa: BLE001
        pass


def on_qa_row(row: dict) -> None:
    """QA-journal hook (§11 item 3): one answered question -> one vector. The row arrives
    already redaction-gated by `qa_journal.record_qa`, so nothing sensitive reaches the store."""
    if not _enabled():
        return
    try:
        from .embedder import doc_from_qa

        doc = doc_from_qa(row)
        if doc is not None:
            _bank(doc)
    except Exception:  # noqa: BLE001
        pass


def install() -> bool:
    """Register the decision sink. Idempotent; returns whether the rider is live."""
    global _installed
    if not _enabled():
        return False
    if _installed:
        return True
    try:
        from interaction.decision_journal import register_decision_sink

        register_decision_sink(on_decision_record)
        _installed = True
        return True
    except Exception:  # noqa: BLE001
        return False
