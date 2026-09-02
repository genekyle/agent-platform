"""The precedent rung — the student seat filled by RETRIEVAL (PLAN_inhouse_reasoner_v1 §2 M0).

`local_reasoner.py` is this seat's LLM sibling and its docstring still holds: no local model
can sit there on this hardware. This module fills the seat with the mechanism that needs no
model at all — k-NN over our own journaled precedents in `vectors.db`. It answers the same
`DecisionReasoner` contract (`Bundle -> Optional[Decision]`), costs $0 per call, and ABSTAINS
honestly: an empty or far neighborhood returns None, which the cascade reads as "the seat
declined", exactly like every other rung.

Shadow-first (wired in `controller/shadow.py`): every crank journals what this rung WOULD have
decided beside what the teacher did, which is the per-scenario agreement data the two-bar gate
promotes on. Acting rides `settings.precedent_acting` (default OFF) and, when on, its rung name
sits in `teach.PROPOSE_RUNGS` — a precedent proposal is reviewed, never rung-0-trusted.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from interaction.decision import Bundle, Decision

RUNG = "precedent"

K = 15
#: Provisional floors (calibration owed — §8 P1's follow-up): fewer usable neighbors than this,
#: or a nearest neighbor farther than this (L2 over unit vectors, 0=identical, 2=opposite),
#: is an empty neighborhood and the seat abstains rather than guesses.
MIN_NEIGHBORS = 3
MAX_NEAREST_DISTANCE = 0.85

#: Intents whose target journals under `field` (the fill family) vs `ref` (the press family).
#: The proposal mirrors the journal's own params convention so the exact bar compares like
#: with like. Value-bearing params are NEVER proposed — values come from the fill system, and
#: the corpus redacts them anyway (a precedent cannot know your phone number, only that the
#: phone field is what gets filled here).
_FIELD_INTENTS = frozenset({"set_text", "select_option", "check_group", "set_date", "upload"})


def _query_vector(bundle: Bundle):
    """Embed the live Bundle through the SAME composer the corpus was banked with."""
    from . import rider
    from .embedder import compose_decision_text, PrecedentDoc

    root = rider._data_root()
    _, embedder = rider._ensure(root)
    text = compose_decision_text(
        goal_text=bundle.goal_text or "",
        state=bundle.state,
        url=bundle.route or bundle.url or "",
        ax_identities=bundle.ax_identities,
        unanswered=[dict(u) for u in bundle.unanswered],
        expected_next=list(bundle.expected_next or ()),
    )
    shot: Optional[Path] = None
    cap = bundle.capture or {}
    name = cap.get("screenshot_filename") if isinstance(cap, dict) else None
    if name:
        candidate = root / "observer-screenshots" / str(name)
        shot = candidate if candidate.exists() else None
    doc = PrecedentDoc(
        kind="query", source_key="query",
        text=text,
        facets={k: v for k, v in {
            "platform": bundle.ats or "",
            "ats": bundle.ats or "",
            "state": bundle.state or "",
            "phase": bundle.phase or "",
            "task": bundle.task or "",
            "route": bundle.route or "",
        }.items() if v},
        screenshot=shot,
    )
    vec, _ = embedder.embed_doc(doc)
    store, _ = rider._ensure(root)
    return store, vec


def propose(bundle: Bundle) -> Optional[Decision]:
    """The seat's answer for one Bundle, or None (abstain). Never raises."""
    try:
        store, vec = _query_vector(bundle)
        neighbors = [n for n in store.knn(vec, k=K, kinds=["decision", "transition_before"])
                     if n.get("intent")]
        if len(neighbors) < MIN_NEIGHBORS:
            return None
        nearest = float(neighbors[0]["distance"])
        if nearest > MAX_NEAREST_DISTANCE:
            return None

        votes: dict[str, float] = defaultdict(float)
        for n in neighbors:
            votes[n["intent"]] += 1.0 / (float(n["distance"]) + 0.1)
        intent, top_weight = max(votes.items(), key=lambda kv: kv[1])
        share = top_weight / sum(votes.values())
        supporters = [n for n in neighbors if n["intent"] == intent]
        ref = next((n["ref"] for n in supporters if n.get("ref")), "")

        #: share says how unanimous the neighborhood was; the nearest-distance damp says how
        #: much this situation resembles it at all. Both provisional until calibrated (§8).
        confidence = round(share * max(0.0, 1.0 - nearest / 2.0), 4)

        params: dict[str, Any] = {}
        if ref:
            params = {"field": ref} if intent in _FIELD_INTENTS else {"ref": ref}
        top = supporters[0]
        rationale = (f"precedent vote: {len(supporters)}/{len(neighbors)} {intent} "
                     f"(nearest d={nearest:.2f}, {top.get('ats') or '?'}:"
                     f"{top.get('state') or '?'})")
        evidence = tuple(
            f"{n['kind']} {n.get('ats') or '?'}:{n.get('state') or '?'} -> "
            f"{n['intent']}{(' ' + n['ref']) if n.get('ref') else ''} (d={n['distance']:.2f})"
            for n in neighbors[:3]
        )
        return Decision(intent=intent, params=params, confidence=confidence, rung=RUNG,
                        rationale=rationale, evidence=evidence)
    except Exception:  # noqa: BLE001 — the seat abstains; it never takes the cascade down
        return None


def reasoner():
    """The seat as a `DecisionReasoner` — hand this to `decide(model=...)`."""
    return propose
