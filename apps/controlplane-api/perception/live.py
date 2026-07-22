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
          prior: tuple[str, ...] = ()) -> Optional[dict]:
    """Perceive one live observation. Returns `BeliefState.as_dict()`, or None if unavailable.

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
                artifact=artifact_from_live(url=url, title=title, ax_candidates=ax_candidates),
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
                form_state: Optional[dict[str, Any]] = None) -> Optional[Path]:
    """Trigger a `/capture` for this turn and return the screenshot path, or None.

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
            return None
        return screenshot_for_artifact(filename)
    except Exception:
        logger.exception("perception: capture failed; the drive continues without a new row")
        return None
