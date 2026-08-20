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


#: Self-supervision gate: a transition half may label ITSELF when the step's verdict is
#: `confirmed` (the act declared where it would land and the world agreed) AND the witnesses'
#: own state-uncertainty is under this ceiling. Same number as the transition trainer's
#: MAX_TRAIN_UNCERTAINTY (routers/transitions.py) and for the same reason: the observer clamps a
#: witness split to >= 0.5, so `< 0.5` admits only rows where the witnesses actually agreed.
#: Three independent sources concurring — declared expectation, observed world, witness belief —
#: is a label nobody had to write. Found 2026-08-20: 146 confirmed rows sat beside 16 teacher
#: labels, and the trainer read only the teacher's.
MAX_SELF_LABEL_UNCERTAINTY = 0.5


@dataclass
class Row:
    filename: str
    state: str
    url: str
    domain_id: str
    artifact_path: Optional[Path]
    screenshot_path: Optional[Path]
    facets: StateFacets

    #: How the screenshot resolved: "path" | "filename" (the stale-path fallback) | "".
    #: Kept per-row so the census can report drift in EVERY source — a path that resolves only
    #: by fallback is provenance rot worth seeing, wherever the row came from (module header).
    screenshot_how: str = ""

    #: Who authored the label: "db" (TrainingCapture), "teacher" (a correction written by someone
    #: who watched the drive), or "self" (the confirmed-verdict gate above). Provenance rides
    #: with the row so the census can A/B the corpora — a self-supervised corpus that cannot be
    #: compared against the teacher-only one is a corpus you cannot argue with.
    label_source: str = "db"

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


def _resolve_screenshot(raw: Optional[str]) -> tuple[Optional[Path], str]:
    """Path first, then filename under the current root — the stale-path rule, for a bare path
    string (the transition-corpus convention) rather than a refs list."""
    if not raw:
        return None, ""
    if Path(raw).exists():
        return Path(raw), "path"
    name = Path(raw).name
    if name:
        candidate = artifacts_root() / "observer-screenshots" / name
        if candidate.exists():
            return candidate, "filename"
    return None, ""


def _self_label(row: dict[str, Any], half: str) -> str:
    """The state this half may claim for ITSELF, or "" — the MAX_SELF_LABEL_UNCERTAINTY gate.

    Strictly `verdict == "confirmed"`: a `read_only` row never declared an expectation, so there
    is no third witness to concur, and a `mismatch` is the world disagreeing — the one row that
    must never label itself.
    """
    if row.get("verdict") != "confirmed":
        return ""
    belief = ((row.get(half) or {}).get("belief")) or {}
    state = (belief.get("state") or "").strip()
    unc = (belief.get("uncertainty") or {}).get("state")
    if not state or unc is None or unc >= MAX_SELF_LABEL_UNCERTAINTY:
        return ""
    return state


def transition_label_rows(*, include_self_supervised: bool = True) -> list[Row]:
    """Labeled transition halves as witness training rows — teacher corrections first, and then
    (2026-08-20) the corpus labeling ITSELF where the evidence concurs.

    The transition corpus is the spine (2026-08-09 refocus): it is the only corpus that grows
    during drives, every row carries its screenshots, and a teacher correction stamps BOTH
    sides with a state — which is exactly the (state, artifact, screenshot) triple a witness
    trains on. Until this reader existed, those labels reached only the planner's edge table
    and the witnesses stayed frozen on the pre-2026-07-30 DB corpus — the cold-start circle
    (labels can't accrue because witnesses are uncertain because labels don't accrue) closed
    only on paper. One labeled transition is two witness examples; this is where they enter.

    The self-supervised tier (`_self_label`) runs strictly BELOW the teacher: an artifact the
    teacher has spoken about never takes a self label, in either direction — a correction is a
    disagreement with the belief by construction, so letting the belief also train would feed
    the witness both sides of an argument the teacher already settled.
    """
    root = artifacts_root() / "transitions"
    if not root.exists():
        return []
    raw: list[dict[str, Any]] = []
    for path in sorted(root.glob("session_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except Exception:
                continue

    seen: set[tuple[str, str]] = set()
    teacher_spoke: set[str] = set()      # artifacts whose label the teacher owns
    rows: list[Row] = []

    def _emit(row: dict[str, Any], half: str, state: str, source: str) -> None:
        obs = row.get(half) or {}
        artifact_name = obs.get("artifact") or ""
        if not state or not artifact_name:
            return
        if source == "self" and artifact_name in teacher_spoke:
            return
        key = (artifact_name, state)
        if key in seen:      # an after-half is often the next row's before-half
            return
        if source == "self" and any(a == artifact_name for a, _ in seen):
            return           # a different state already claimed this artifact — do not contradict
        seen.add(key)
        if source == "teacher":
            teacher_spoke.add(artifact_name)
        artifact_path = artifacts_root() / "observer-traces" / artifact_name
        shot, how = _resolve_screenshot(obs.get("screenshot"))
        rows.append(Row(
            filename=artifact_name,
            state=state,
            url=obs.get("url") or "",
            domain_id="",
            artifact_path=artifact_path if artifact_path.exists() else None,
            screenshot_path=shot,
            facets=facets_for(state, url=obs.get("url") or "", domain_id=""),
            screenshot_how=how,
            label_source=source,
        ))

    # Pass 1 — the teacher's word, and the full set of artifacts it covers.
    for row in raw:
        correction = row.get("teacher_correction") or {}
        for half, label_key in (("before", "before_state"), ("after", "after_state")):
            _emit(row, half, (correction.get(label_key) or "").strip(), "teacher")

    # Pass 2 — the corpus's own confirmed rows, strictly beneath the teacher.
    if include_self_supervised:
        for row in raw:
            if row.get("teacher_correction"):
                continue      # the teacher looked at this row; its beliefs are sub judice
            for half in ("before", "after"):
                _emit(row, half, _self_label(row, half), "self")
    return rows


def load_rows(*, require_screenshot: bool = False,
              include_transitions: bool = True) -> tuple[list[Row], dict[str, int]]:
    """Every labeled capture, with whatever surfaces survive. Returns (rows, census).

    `include_transitions` folds in `transition_label_rows()` — teacher-labeled drive
    transitions, the growing half of the corpus. On by default because a label the witnesses
    never train on is a label paid for and shelved; the flag exists for A/B comparisons.
    """
    from db import SessionLocal
    from models import TrainingCapture

    root = artifacts_root()
    traces = root / "observer-traces"
    rows: list[Row] = []
    census = {"labeled": 0, "with_artifact": 0, "with_screenshot": 0, "missing_screenshot": 0,
              "missing_artifact": 0, "screenshot_by_stale_path": 0, "from_transitions": 0}

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
                screenshot_how=how,
            ))
    finally:
        session.close()

    if include_transitions:
        census["superseded_by_teacher"] = 0
        census["from_self_supervision"] = 0
        known = {(r.filename, r.state) for r in rows}
        by_filename = {r.filename: i for i, r in enumerate(rows)}
        for row in transition_label_rows():
            if (row.filename, row.state) in known:
                continue
            if row.filename in by_filename:
                if row.label_source != "teacher":
                    # A SELF label never overrules a human-written DB label — the ranking is
                    # teacher > db > self, and a contradiction between the lower two is exactly
                    # the case where somebody once bothered to write the label down.
                    continue
                # SAME capture, DIFFERENT label: the teacher who watched the drive outranks the
                # older DB label — training both would feed the witness contradictory ground
                # truth on exactly the states someone bothered to correct. The DB row is
                # replaced, not merely joined, and the census says so.
                rows[by_filename[row.filename]] = row
                known.add((row.filename, row.state))
                census["superseded_by_teacher"] += 1
                census["from_transitions"] += 1
                if row.screenshot_how == "filename":
                    census["screenshot_by_stale_path"] += 1
                continue
            census["labeled"] += 1
            census["from_transitions"] += 1
            if row.label_source == "self":
                census["from_self_supervision"] += 1
            if row.artifact_path:
                census["with_artifact"] += 1
            else:
                census["missing_artifact"] += 1
            if row.screenshot_path:
                census["with_screenshot"] += 1
                if row.screenshot_how == "filename":
                    census["screenshot_by_stale_path"] += 1
            else:
                census["missing_screenshot"] += 1
            if require_screenshot and not row.screenshot_path:
                continue
            known.add((row.filename, row.state))
            by_filename[row.filename] = len(rows)
            rows.append(row)
    return rows, census
