"""The late-fusion embedding recipe (PLAN_inhouse_reasoner_v1 §3).

Three blocks, each L2-normalized then weighted, concatenated, and the whole re-normalized so
inner-product/L2 over stored vectors ranks like cosine:

- text   (512) — Apple NLEmbedding sentence vector over a composed "semantic sentence"
- vision (768) — Apple Vision FeaturePrint of the step screenshot (reuses perception's encoder
                 and its on-disk cache; a missing screenshot is a zero block, flagged)
- facets (128) — hashed categoricals (platform, ats, state, phase, task, route template)

The block layout is FIXED and recorded in the store, so evaluation can slice blocks back out
for ablation without re-embedding. Labels (intent / ref / rung) must NEVER leak into any block:
features describe the situation, the label is what the situation led to.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TEXT_DIM = 512
VISION_DIM = 768
FACET_DIM = 128
DIM = TEXT_DIM + VISION_DIM + FACET_DIM

TEXT_SLICE = slice(0, TEXT_DIM)
VISION_SLICE = slice(TEXT_DIM, TEXT_DIM + VISION_DIM)
FACET_SLICE = slice(TEXT_DIM + VISION_DIM, DIM)

# v0 weights — provisional until the ablation (§8 P3) measures which blocks carry signal.
DEFAULT_WEIGHTS = {"text": 1.0, "vision": 0.6, "facets": 0.8}

_MAX_TEXT_CHARS = 1200
_MAX_CANDIDATE_NAMES = 20


@dataclass
class PrecedentDoc:
    """One embeddable situation plus its label and provenance. `text`/`facets`/`screenshot`
    are FEATURES; everything else is label or metadata and stays out of the vector."""

    kind: str                       # "decision" | "transition_before" | "transition_after"
    source_key: str                 # unique, idempotent backfill key
    text: str = ""
    facets: dict[str, str] = field(default_factory=dict)
    screenshot: Optional[Path] = None
    # label + metadata (never embedded)
    intent: str = ""
    ref: str = ""
    verdict: str = ""
    teacher_label: str = ""
    session: str = ""
    ts: str = ""
    platform: str = ""
    ats: str = ""
    state: str = ""
    phase: str = ""
    task: str = ""
    artifact: str = ""


def _l2(vec: list[float]) -> list[float]:
    norm = sum(v * v for v in vec) ** 0.5
    if norm <= 1e-12:
        return vec
    return [v / norm for v in vec]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:_MAX_TEXT_CHARS]


def route_template(url: str) -> str:
    """Digits and long hex runs become placeholders so /jobs/4424504424 and /jobs/999 embed
    alike — the same normalization idea the fingerprint's route uses."""
    if not url:
        return ""
    url = re.sub(r"^https?://", "", url).split("?")[0]
    url = re.sub(r"\d+", "{n}", url)
    return re.sub(r"[0-9a-f]{8,}", "{h}", url)


class FusionEmbedder:
    """Composes the three blocks. NLEmbedding and the Vision encoder load lazily so importing
    this module costs nothing (and works off-macOS for pure-text paths in tests)."""

    def __init__(self, weights: Optional[dict[str, float]] = None) -> None:
        self.weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        self._nl = None
        self._nl_tried = False
        self._vision = None

    # -- blocks ---------------------------------------------------------------
    def text_block(self, text: str) -> tuple[list[float], bool]:
        text = _clean(text)
        if not text:
            return [0.0] * TEXT_DIM, False
        if not self._nl_tried:
            self._nl_tried = True
            try:
                from NaturalLanguage import NLEmbedding  # pyobjc, on-device

                self._nl = NLEmbedding.sentenceEmbeddingForLanguage_("en")
            except Exception:
                self._nl = None
        if self._nl is None:
            return [0.0] * TEXT_DIM, False
        raw = self._nl.vectorForString_(text)
        if raw is None or len(raw) != TEXT_DIM:
            return [0.0] * TEXT_DIM, False
        return _l2([float(v) for v in raw]), True

    def vision_block(self, screenshot: Optional[Path]) -> tuple[list[float], bool]:
        if screenshot is None:
            return [0.0] * VISION_DIM, False
        if self._vision is None:
            from perception.encoders import get_encoder

            self._vision = get_encoder("apple_featureprint")
        vec = self._vision.embed(Path(screenshot))
        if vec is None or len(vec) != VISION_DIM:
            return [0.0] * VISION_DIM, False
        return _l2([float(v) for v in vec]), True

    def facet_block(self, facets: dict[str, str]) -> list[float]:
        vec = [0.0] * FACET_DIM
        for key, value in sorted(facets.items()):
            value = _clean(str(value)).lower()
            if not value:
                continue
            digest = hashlib.md5(f"{key}={value}".encode()).digest()
            idx = int.from_bytes(digest[:4], "little") % FACET_DIM
            sign = 1.0 if digest[4] % 2 == 0 else -1.0  # signed hashing tempers collisions
            vec[idx] += sign
        return _l2(vec)

    # -- fusion ---------------------------------------------------------------
    def embed_doc(self, doc: PrecedentDoc) -> tuple[list[float], bool]:
        """Returns (vector, has_vision). Blocks are weighted then the whole is re-normalized."""
        text, _ = self.text_block(doc.text)
        vision, has_vision = self.vision_block(doc.screenshot)
        facets = self.facet_block(doc.facets)
        w = self.weights
        fused = (
            [v * w["text"] for v in text]
            + [v * w["vision"] for v in vision]
            + [v * w["facets"] for v in facets]
        )
        return _l2(fused), has_vision

    def flush(self) -> None:
        if self._vision is not None:
            self._vision.flush()


# -- doc composers ------------------------------------------------------------
def doc_from_decision(row: dict) -> Optional[PrecedentDoc]:
    """DecisionRecord journal row -> doc. Features come from the bundle snapshot (what was
    seen); the label is the decided intent (+ best-effort ref from params)."""
    intent = _clean(row.get("intent") or "")
    if not intent:
        return None
    snap = row.get("bundle_snapshot") or {}
    unanswered = snap.get("unanswered") or []
    if isinstance(unanswered, list):
        unanswered_txt = "; ".join(_clean(str(u)) for u in unanswered[:12])
    else:
        unanswered_txt = _clean(str(unanswered))
    text = " | ".join(
        part
        for part in (
            _clean(snap.get("goal_text") or ""),
            f"state {snap.get('state')}" if snap.get("state") else "",
            f"route {route_template(snap.get('url') or row.get('url') or '')}",
            f"unanswered: {unanswered_txt}" if unanswered_txt else "",
            f"expecting {snap.get('expected_next')}" if snap.get("expected_next") else "",
        )
        if part
    )
    params = row.get("params") or {}
    ref = ""
    if isinstance(params, dict):
        ref = _clean(str(params.get("ref") or params.get("control") or params.get("field") or ""))
    facets = {
        "platform": snap.get("ats") or row.get("ats") or "",
        "ats": snap.get("ats") or row.get("ats") or "",
        "state": snap.get("state") or row.get("state") or "",
        "phase": snap.get("phase") or "",
        "task": snap.get("task") or row.get("task") or "",
        "route": route_template(snap.get("url") or row.get("url") or ""),
    }
    shot = _clean(row.get("capture_screenshot") or "")
    return PrecedentDoc(
        kind="decision",
        source_key=f"decision:{row.get('ts')}:{row.get('bundle_digest', '')[:12]}",
        text=text,
        facets={k: v for k, v in facets.items() if v},
        screenshot=Path(shot) if shot else None,  # basename; backfill resolves the dir
        intent=intent,
        ref=ref,
        session=str(row.get("session_id") or ""),
        ts=str(row.get("ts") or ""),
        platform=str(snap.get("ats") or row.get("ats") or ""),
        ats=str(snap.get("ats") or row.get("ats") or ""),
        state=str(snap.get("state") or row.get("state") or ""),
        phase=str(snap.get("phase") or ""),
        task=str(snap.get("task") or row.get("task") or ""),
        artifact=_clean(row.get("capture_artifact") or ""),
    )


def _observation_text(obs: dict, extra: str = "") -> str:
    cands = obs.get("candidates") or []
    names = []
    for cand in cands[:_MAX_CANDIDATE_NAMES]:
        try:
            role, name = cand[0], cand[1]
        except Exception:
            continue
        if name:
            names.append(f"{role} {name}")
    belief = obs.get("belief") or {}
    return " | ".join(
        part
        for part in (
            _clean(obs.get("title") or ""),
            f"state {belief.get('state')}" if belief.get("state") else "",
            f"route {route_template(obs.get('url') or '')}",
            f"controls: {'; '.join(names)}" if names else "",
            _clean(extra),
        )
        if part
    )


def doc_from_transition(row: dict, half: str) -> Optional[PrecedentDoc]:
    """Transition row half -> doc. The BEFORE half is labeled with the act taken from it
    (the trainable pair); the AFTER half is a state exemplar labeled with where it landed."""
    obs = row.get(half) or {}
    if not obs:
        return None
    action = row.get("action") or {}
    belief = obs.get("belief") or {}
    facets_src = belief.get("facets") or {}
    intent = _clean(str(action.get("intent") or action.get("action") or "")) if half == "before" else ""
    extra = ""
    if half == "after":
        changes = row.get("changes") or {}
        extra = _clean(str(changes.get("page_says") or ""))
    shot = obs.get("screenshot") or ""
    facets = {
        "platform": facets_src.get("platform") or "",
        "ats": action.get("ats") or "",
        "state": belief.get("state") or "",
        "phase": row.get("rung") or "",
        "route": route_template(obs.get("url") or ""),
    }
    teacher = row.get("teacher_correction") or {}
    return PrecedentDoc(
        kind=f"transition_{half}",
        source_key=f"transition:{row.get('session_id')}:{row.get('ts')}:{half}",
        text=_observation_text(obs, extra),
        facets={k: str(v) for k, v in facets.items() if v},
        screenshot=Path(shot) if shot else None,
        intent=intent,
        ref=_clean(str(action.get("control") or "")) if half == "before" else "",
        verdict=_clean(str(row.get("verdict") or "")),
        teacher_label=_clean(str(teacher.get("verdict") or "")),
        session=str(row.get("session_id") or ""),
        ts=str(row.get("ts") or ""),
        platform=str(facets_src.get("platform") or ""),
        ats=str(action.get("ats") or ""),
        state=str(belief.get("state") or ""),
        phase=str(row.get("rung") or ""),
        artifact=_clean(str(obs.get("artifact") or "")),
    )
