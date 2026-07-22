"""The live seam — perceive a running tab, and grow the corpus while doing it.

Two jobs, deliberately in one module because they are the same idea from both ends:

1. **Sense.** Turn the surfaces a live turn already has (url, page text, AX candidates, and a
   screenshot when one exists) into a `BeliefState`, using the SAME featurizer the trainer used.
   The live path has no capture artifact, so it synthesizes the artifact SHAPE rather than
   growing a second featurizer — two featurizers is how the corpus and the runtime quietly stop
   describing the same page (the `application_answers` drift, LEARNINGS 2026-06).

2. **Capture.** Operator-directed 2026-07-22: **a drive should always be collecting.** Every
   observation the controller makes is a free, pre-labeled-by-the-recipe training example for
   every model in the stack — the DOM witness, the prototype bank, grounding, L3 — and for three
   months we drove without keeping any of it (the 2026-07-16 reckoning). `/capture` already
   writes the artifact + screenshot + AX sidecar; this just makes the controller call it.

Both are BEST-EFFORT by construction. Perception is an aid, not a dependency: a witness that
cannot be loaded, a capture server that is down, or a screenshot that never arrives must leave
the drive running exactly as it ran before. `None` is a real answer everywhere here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Process-wide, lazily loaded. Fitting is minutes; loading is a JSON read, but the encoder is
#: worth keeping warm across turns. `_LOADED` distinguishes "not tried yet" from "tried, nothing
#: promoted" so a missing artifact costs one attempt, not one per turn.
_OBSERVER: Optional[Any] = None
_LOADED = False


def observer() -> Optional[Any]:
    """The promoted Observer, or None. None means the controller behaves exactly as before."""
    global _OBSERVER, _LOADED
    if not _LOADED:
        _LOADED = True
        try:
            from perception.train import load_observer
            _OBSERVER = load_observer()
        except Exception:
            logger.exception("perception: could not load the promoted observer")
            _OBSERVER = None
    return _OBSERVER


def reset_observer() -> None:
    """Drop the cached observer — call after re-fitting so a drive picks the new one up."""
    global _OBSERVER, _LOADED
    _OBSERVER, _LOADED = None, False


def artifact_from_live(*, url: str = "", title: str = "",
                       ax_candidates: Optional[list[dict]] = None) -> dict[str, Any]:
    """Live surfaces -> the capture-artifact shape the featurizer already reads.

    Reads `caption or name` from an AX candidate, matching `fingerprint.ax_summary` exactly, so
    the witness and the fingerprint cannot disagree about what a control is called.

    THE FALLBACK, not the main path (measured live 2026-07-22). A synthesized artifact is
    strictly thinner than the `/capture` artifacts the witnesses are fitted on — it carries no
    `ph:` (AX candidates have no placeholders), no `title:`, no `flag:`, and it duplicates one
    capped candidate list into both element views, so the featurizer reads the first 60 controls
    twice instead of 120 distinct ones. Scored against the promoted witness on a page whose state
    has 20 training examples, that gap is the difference between cosine **0.82-0.86** (a real
    capture) and **0.2755** (this synthesis) — which saturated the class-conditional novelty
    percentile at 1.00 on every page and pinned the whole controller to RED. Prefer
    `CapturedTurn.artifact`; use this only when the capture did not land.
    """
    elements = []
    ranked = []
    for cand in (ax_candidates or [])[:200]:
        if not isinstance(cand, dict):
            continue
        role = str(cand.get("role") or "")
        name = str(cand.get("caption") or cand.get("name") or "")
        if not (role or name):
            continue
        ranked.append({"target": {"role": role, "label": name}})
        elements.append({"role": role, "name": name, "text": "", "placeholder": ""})
    return {
        "acquisition": {
            "page_identity": {"url": url, "title": title},
            "actionable_elements": elements,
        },
        "ranked_candidates": ranked,
    }


def sense(*, url: str = "", page_text: str = "", title: str = "",
          ax_candidates: Optional[list[dict]] = None,
          screenshot_path: Optional[Path] = None,
          domain_id: str = "",
          artifact: Optional[dict[str, Any]] = None,
          prior: tuple[str, ...] = ()) -> Optional[dict]:
    """Perceive one live observation. Returns `BeliefState.as_dict()`, or None if unavailable.

    `artifact` is the capture this turn ALREADY wrote (`CapturedTurn.artifact`). Pass it whenever
    it exists: the corpus the witnesses were fitted on is made of exactly these, so featurizing
    the real thing makes the live and training views identical **by construction** rather than by
    a synthesis that has to be kept in sync. The 2026-07-22 live run is what forced this — the
    synthesized artifact scored cosine 0.2755 where a real capture of the same state scores 0.82,
    and the resulting novelty of 1.00 on every page made the controller ungovernable. This module
    always meant to have one featurizer; it turned out one featurizer over two artifact SHAPES is
    the same drift wearing a different hat.

    `prior` is the recipe's `expected_next` — the transition prior. Passing it costs nothing and
    is the cheapest evidence in the system: the recipe already predicted where this action should
    land, and an observation that agrees with the prediction is more trustworthy than one that
    does not (never the reverse — a page may legitimately branch).
    """
    eng = observer()
    if eng is None:
        return None
    try:
        from perception.observer import Observation
        belief = eng.observe(
            Observation(
                artifact=artifact if artifact else artifact_from_live(
                    url=url, title=title, ax_candidates=ax_candidates),
                page_text=page_text or "",
                screenshot_path=screenshot_path,
                url=url,
                domain_id=domain_id,
            ),
            prior=prior,
        )
        return belief.as_dict()
    except Exception:
        logger.exception("perception: sense failed; the drive continues without a belief")
        return None


@dataclass(frozen=True)
class CapturedTurn:
    """What one turn's `/capture` produced: the artifact itself and its screenshot.

    Both halves are optional and `CapturedTurn()` is the honest "nothing landed" — perception is
    an aid, never a dependency. The artifact is carried rather than discarded because it is the
    exact shape the witnesses were fitted on, so `sense()` can featurize it instead of
    synthesizing a thinner lookalike (see `artifact_from_live`).
    """

    artifact: Optional[dict[str, Any]] = None
    screenshot: Optional[Path] = None


def read_artifact(artifact_filename: str) -> Optional[dict[str, Any]]:
    """The capture artifact `/capture` just wrote, or None."""
    import json

    from perception.dataset import artifacts_root
    try:
        return json.loads((artifacts_root() / "observer-traces" / artifact_filename).read_text())
    except Exception:
        return None


def screenshot_for_artifact(artifact_filename: str) -> Optional[Path]:
    """The screenshot belonging to a capture, read from the artifact — never guessed.

    Worth stating because guessing looks reasonable and is wrong: the artifact is named for the
    capture's timestamp and the screenshot for `datetime.now()` at WRITE time, with a different
    scenario suffix, so `<artifact stem>.png` names a file that does not exist. The artifact
    records the real reference under `acquisition.screenshots` (the same list the control plane
    stores as `TrainingCapture.screenshot_refs`), and — per the 2026-07-22 stale-path finding —
    the stored absolute path may point at a directory that has since been renamed, so resolve by
    filename under the current root when it does.
    """
    import json

    from perception.dataset import artifacts_root
    root = artifacts_root()
    try:
        artifact = json.loads((root / "observer-traces" / artifact_filename).read_text())
    except Exception:
        return None
    refs = ((artifact.get("acquisition") or {}).get("screenshots") or [])
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        raw = ref.get("image_path") or ref.get("path")
        if raw and Path(raw).exists():
            return Path(raw)
        name = ref.get("filename") or (Path(raw).name if raw else "")
        if name:
            candidate = root / "observer-screenshots" / name
            if candidate.exists():
                return candidate
    return None


def capture_now(post: Any, addr: dict[str, Any], *, scenario: str = "controller_turn",
                task: str = "", state: Optional[str] = None,
                domain_id: str = "",
                form_state: Optional[dict[str, Any]] = None) -> CapturedTurn:
    """Trigger a `/capture` for this turn and hand back what it wrote.

    Returns a `CapturedTurn`; an empty one means nothing landed and the drive continues without
    a new row. The artifact is returned alongside the screenshot because `sense()` should
    featurize the real capture rather than a synthesized lookalike — reading it costs nothing
    here, since resolving the screenshot already opens the same file.

    `post` is the caller's own HTTP helper (the actuator's `_post`), so this adds no second
    transport and inherits the caller's timeouts and error handling. The corpus grows through
    the endpoint that already writes artifact + screenshot + AX sidecar — never through a
    parallel path (PRINCIPLES §8).
    """
    try:
        body = dict(addr or {})
        body["scenario"] = scenario
        body["task_context"] = {"task": task, "state": state}
        body["training_metadata"] = {"domain_id": domain_id, "source": "controller"}
        # The required-field set this turn already scanned. Passing it is what makes the corpus
        # able to answer the question that is now the ENTIRE remaining error budget: which form
        # phase of this ATS are we on? The capture cannot re-derive it, so if the caller does not
        # hand it over it is lost — and it was, for every capture before today.
        if form_state:
            body["form_state"] = form_state
        result = post("/capture", body) or {}
        filename = result.get("filename") or ""
        if not filename:
            return CapturedTurn()
        return CapturedTurn(artifact=read_artifact(filename),
                            screenshot=screenshot_for_artifact(filename))
    except Exception:
        logger.exception("perception: capture failed; the drive continues without a new row")
        return CapturedTurn()
