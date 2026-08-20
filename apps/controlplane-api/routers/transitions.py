"""The transition-corpus review surface (PLAN_step_runner.md, the inspect-and-correct step).

Three jobs, all read-mostly:
  * list the corpora and their health — is the corpus growing, what mix of verdicts, how much
    of it is still `unmodeled`, did the visual witness actually testify;
  * render each row so the operator can see WHAT THE SYSTEM WAS THINKING AT THE TIME — the
    belief (with the witnesses' own rationale, verbatim), the prediction declared before the
    act, the act, what changed, and how the verdict was reached;
  * write teacher corrections, both sides kept (PRINCIPLES §10) — `step_runner.correct_transition`
    owns the write; this router only translates addresses and errors.

The narration lives HERE, not in step_runner: the row is the training artifact and stays terse;
the English is presentation, derived on read so it can improve without touching stored data.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

import step_runner as sr
from settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------------
# Narration — the row, in the order the system lived it
# --------------------------------------------------------------------------------------------

def _short_url(u: str, limit: int = 70) -> str:
    if not u:
        return ""
    return u if len(u) <= limit else u[: limit - 1] + "…"


def _host(u: str) -> str:
    try:
        return urlparse(u).hostname or _short_url(u, 40)
    except Exception:  # noqa: BLE001
        return _short_url(u, 40)


def _narrate_belief(obs: Optional[dict[str, Any]]) -> str:
    """What the witnesses thought, in their own words. The `rationale` is written by the
    observer at decision time — surfacing it verbatim IS the point of this panel."""
    if not obs:
        return "no observation recorded"
    if not obs.get("ok"):
        return "the eyes were unavailable — this look failed and testified to nothing"
    belief = obs.get("belief")
    if not belief:
        return ("looked (URL + tabs + AX) but formed no belief — an identity-only look "
                "(credential posture) or the witnesses were not loadable")
    state = belief.get("state") or "unknown"
    unc = (belief.get("uncertainty") or {}).get("state")
    rationale = belief.get("rationale") or ""
    head = f"believed it was on “{state}”"
    if unc is not None:
        head += f" (uncertainty {unc})"
    return f"{head} — {rationale}" if rationale else head


def _narrate_expectation(exp: dict[str, Any]) -> str:
    kind = exp.get("kind") or "?"
    if kind == "url_param":
        return f"predicted the window would carry {exp.get('param')}={exp.get('value')}"
    if kind == "url_value":
        return f"predicted a window URL would carry {exp.get('value')!r}"
    if kind == "new_tab_or_nav":
        hosts = ", ".join(exp.get("hosts_hint") or []) or "an application host"
        return f"predicted a tab would open or navigate to {hosts}"
    if kind == "content_changed":
        return "predicted the page would visibly move (URL, tabs, or AX content)"
    if kind == "read_only":
        return "predicted no change — a read-only step, paired for the corpus"
    if kind == "unmodeled":
        return ("declared NO measured prediction — the step changes the world and this row "
                "exists so the expectation can be written from data")
    if kind == "expected_next":
        states = [s for s in (exp.get("value") or "").split("|") if s]
        return (f"predicted landing in one of: {', '.join(states)}" if states
                else "declared no expected landing states")
    return f"declared an unknown expectation kind {kind!r}"


def _narrate_action(action: dict[str, Any], rung: str) -> str:
    bits: list[str] = []
    label = action.get("action") or action.get("intent") or rung
    bits.append(str(label))
    for key in ("control", "target_host", "leg", "ats", "company", "expand"):
        if action.get(key):
            bits.append(f"{key}={action[key]}")
    if action.get("fields"):
        fields = action["fields"]
        shown = ", ".join(map(str, fields[:4])) + ("…" if len(fields) > 4 else "")
        bits.append(f"fields=[{shown}]")
    if action.get("initiator"):
        bits.append(f"by {action['initiator']}")
    return " · ".join(bits)


def _narrate_changes(changes: Optional[dict[str, Any]]) -> str:
    if changes is None:
        return "could not compare the two looks (one side was blind)"
    parts: list[str] = []
    if changes.get("url_changed"):
        parts.append(f"URL moved {_host(changes.get('url_before') or '')} → "
                     f"{_host(changes.get('url_after') or '')}")
    opened = changes.get("tabs_opened") or []
    if opened:
        parts.append("opened " + ", ".join(_host(t.get("url", "")) for t in opened[:3]))
    closed = changes.get("tabs_closed") or []
    if closed:
        parts.append(f"closed {len(closed)} tab(s)")
    nav = changes.get("tabs_navigated") or []
    if nav:
        parts.append("navigated " + ", ".join(
            f"{_host(t.get('from', ''))}→{_host(t.get('to', ''))}" for t in nav[:3]))
    added, removed = changes.get("elements_added") or [], changes.get("elements_removed") or []
    if added or removed:
        parts.append(f"AX content +{len(added)}/−{len(removed)} elements")
    if "landed_state" in changes:                       # the controller rows carry these instead
        parts.append(f"landed in “{changes.get('landed_state') or 'unknown'}”")
        if changes.get("unanswered_after") is not None:
            parts.append(f"{changes['unanswered_after']} unanswered field(s) after")
    # `visual_agreement` is the stored field's name, but the belief it compares comes from
    # whichever witnesses testified — with no screenshot that is the DOM witness alone, and
    # crediting "the visual witness" for a look the eyes never took would be the panel lying
    # about provenance (caught on the first real corpus render: 0/45 rows had a screenshot).
    va = changes.get("visual_agreement")
    if va is True:
        parts.append("before/after beliefs name the same state")
    elif va is False:
        parts.append("the after-belief names a DIFFERENT state")
    return "; ".join(parts) if parts else "nothing observable changed"


def _headline(row: dict[str, Any]) -> str:
    """Claim vs world, one sentence — the heart of no-rung-marks-itself-complete."""
    claimed, verdict = row.get("claimed") or "none", row.get("verdict") or "?"
    evidence = row.get("evidence") or ""
    if verdict == sr.READ_ONLY:
        return f"a read-only step — {evidence}"
    if verdict == sr.CONFIRMED:
        return f"claimed {claimed}; the world CONFIRMED it — {evidence}"
    if verdict == sr.MISMATCH:
        if claimed == "ok":
            return f"the action claimed ok, but the world DISAGREED — {evidence}"
        # A mismatch under a non-ok claim is AGREEMENT: both the action and the world say it
        # did not land. Reading it as disagreement was exactly the confusion this panel exists
        # to prevent (caught on the first real corpus render, 2026-08-04).
        return (f"the action reported {claimed}, and the world agrees nothing landed — "
                f"{evidence}")
    return f"claimed {claimed}; the world could not testify — {evidence}"


def _narrate_window(row: dict[str, Any]) -> str:
    """The rest of the window — the context a step operates inside and used to be blind to."""
    after = (row.get("after") or {}).get("window") or {}
    if not after:
        return "no window census taken"
    roles = after.get("roles") or {}
    shape = ", ".join(f"{n} {role}" for role, n in sorted(roles.items())) or "no tabs"
    line = f"{after.get('count', 0)} tab(s) — {shape}; window {after.get('health', '?')}"
    alert = (row.get("changes") or {}).get("window_alert")
    if alert:
        line += f" — NEEDS A LOOK: {alert.get('why')}"
    return line


def narrate(row: dict[str, Any]) -> dict[str, str]:
    """The row as the system lived it: believed → predicted → did → saw → settled."""
    return {
        "headline": _headline(row),
        "believed": _narrate_belief(row.get("before")),
        "expected": _narrate_expectation(row.get("expected") or {}),
        "did": _narrate_action(row.get("action") or {}, str(row.get("rung") or "")),
        "saw": _narrate_belief(row.get("after")),
        "changed": _narrate_changes(row.get("changes")),
        "window": _narrate_window(row),
    }


# --------------------------------------------------------------------------------------------
# Health — is this corpus becoming trainable?
# --------------------------------------------------------------------------------------------

def _health(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    verdicts: dict[str, int] = {}
    kinds: dict[str, int] = {}
    demoted = corrected = visual = believed = agreements = judged = 0
    states: set[str] = set()
    for row in rows:
        verdicts[row.get("verdict") or "?"] = verdicts.get(row.get("verdict") or "?", 0) + 1
        kind = (row.get("expected") or {}).get("kind") or "?"
        kinds[kind] = kinds.get(kind, 0) + 1
        if row.get("verdict") == sr.MISMATCH and row.get("claimed") == "ok":
            demoted += 1
        if row.get("teacher_correction"):
            corrected += 1
        before = row.get("before") or {}
        if (before.get("belief") or {}).get("state"):
            believed += 1
            states.add(before["belief"]["state"])
        after = row.get("after") or {}
        if (after.get("belief") or {}).get("state"):
            states.add(after["belief"]["state"])
        if before.get("screenshot") or after.get("screenshot"):
            visual += 1
        if row.get("verdict") in (sr.CONFIRMED, sr.MISMATCH):
            judged += 1
            if row.get("verdict") == sr.CONFIRMED:
                agreements += 1
    return {
        "rows": n,
        "verdicts": verdicts,
        "expectation_kinds": kinds,
        "demotions": demoted,
        "corrected": corrected,
        "distinct_states": len(states),
        "states": sorted(states),
        "with_belief": believed,
        "with_screenshot": visual,
        # Of the rows the verifier could actually judge, how often did claim and world agree?
        "claim_agreement": round(agreements / judged, 3) if judged else None,
        "unmodeled_share": round(kinds.get("unmodeled", 0) / n, 3) if n else None,
    }


# --------------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------------

@router.get("/api/transitions")
def list_transitions() -> dict[str, Any]:
    """Every corpus on disk plus overall health — the panel's landing view."""
    corpora = sr.list_corpora()
    all_rows: list[dict[str, Any]] = []
    for c in corpora:
        all_rows.extend(sr.read_transitions(c["key"], limit=1000))
    return {"corpora": corpora, "health": _health(all_rows)}


def _shot_names(row: dict[str, Any]) -> dict[str, Optional[str]]:
    """The row stores a filesystem PATH per half; the serving endpoint takes a bare basename.
    Derived on read so the client never handles paths (and the stored row stays untouched)."""
    out: dict[str, Optional[str]] = {}
    for half in ("before", "after"):
        raw = (row.get(half) or {}).get("screenshot") or ""
        out[half] = raw.replace("\\", "/").rsplit("/", 1)[-1] if raw else None
    return out


@router.get("/api/transitions/{key}")
def read_corpus(key: str, limit: int = 200) -> dict[str, Any]:
    """One corpus, oldest first, each row narrated. The raw row rides along untouched —
    the narration is derived and the training artifact stays the authority."""
    rows = sr.read_transitions(key, limit=limit)
    if not rows:
        raise HTTPException(status_code=404,
                            detail=f"No transition corpus named {key!r}. The corpus appears "
                                   f"after the first wrapped step runs in a live drive.")
    return {"key": key, "health": _health(rows),
            "rows": [{**row, "narration": narrate(row), "screenshots": _shot_names(row)}
                     for row in rows]}


#: Belief-uncertainty ceiling for a row to feed the transition table. The observer clamps a
#: witness SPLIT to >= 0.5 and a lone visual witness higher still, so `< 0.5` admits only rows
#: where the witnesses actually agreed on a state. The first real corpus (45 rows, every belief
#: at uncertainty 1.0 — the AX scan ran dry) is exactly what this gate exists to keep out:
#: training the planner's edges on states nobody confidently observed teaches junk roads.
MAX_TRAIN_UNCERTAINTY = 0.5

_MIN_TRAIN_ROWS = 2      # the trainer's own floor; below it there is no split to fit


def _artifacts_root():
    from deps import _artifacts_dir
    return _artifacts_dir()


class _CorpusEdge:
    """The attribute shape `state_transition.build_transition_dataset` reads — a transition-
    corpus row wearing a capture's interface, so the existing trainer is reused, not rebuilt."""

    def __init__(self, *, filename: str, from_state: str, to_state: str, action: str):
        self.artifact_filename = filename
        self.observed_page_state = from_state
        self.post_action_state = to_state
        self.action_type_hint = action
        self.domain_id = "career_search"


@router.post("/api/transitions/train")
def train_from_corpus() -> dict[str, Any]:
    """Train-as-we-go: rebuild the planner's state-transition table from every corpus row whose
    witnesses were CONFIDENT on both sides. Skips are counted loudly, never silently — when
    nothing is eligible the response says which gate ate the rows, because "0 trained" and
    "45 rows all blind" must not read alike."""
    import state_transition

    eligible: list[_CorpusEdge] = []
    skipped = {"no_belief": 0, "uncertain": 0, "mismatched_act": 0}
    taught = 0
    for c in sr.list_corpora():
        for row in sr.read_transitions(c["key"], limit=1000):
            before = (row.get("before") or {}).get("belief") or {}
            after = (row.get("after") or {}).get("belief") or {}
            # A TEACHER WHO WATCHED THE DRIVE OUTRANKS TWO WITNESSES THAT SPLIT. The observer
            # clamps any split to uncertainty >= 0.5 — correctly, since it measures the leader as
            # right one time in five there — so a witness-only gate trains on nothing until the
            # prototypes are strong, and the prototypes only get strong from labeled data. The
            # teacher's label is the way out of that circle, and it is exactly what the review
            # surface produces. The witnesses' own belief stays untouched in the row.
            correction = row.get("teacher_correction") or {}
            t_before, t_after = correction.get("before_state"), correction.get("after_state")
            if t_before and t_after:
                eligible.append(_CorpusEdge(
                    filename=(row.get("before") or {}).get("artifact")
                    or f"{c['key']}:{row.get('index')}",
                    from_state=t_before, to_state=t_after,
                    action=str(row.get("rung") or "any")))
                taught += 1
                continue
            # THE VERDICT IS READ, NOT IGNORED (2026-08-20). A `mismatch` row is the world
            # disagreeing with the act's declared expectation — confident witnesses over a
            # mismatched act trained the edge exactly as hard as a confirmed one, which teaches
            # the planner the roads we most doubt. Only a teacher label (above) can rehabilitate
            # such a row; the witness-confidence tier below never sees it.
            if row.get("verdict") == "mismatch":
                skipped["mismatched_act"] += 1
                continue
            b_state, a_state = before.get("state"), after.get("state")
            if not b_state or not a_state:
                skipped["no_belief"] += 1
                continue
            b_unc = (before.get("uncertainty") or {}).get("state")
            a_unc = (after.get("uncertainty") or {}).get("state")
            if (b_unc is None or a_unc is None
                    or b_unc >= MAX_TRAIN_UNCERTAINTY or a_unc >= MAX_TRAIN_UNCERTAINTY):
                skipped["uncertain"] += 1
                continue
            eligible.append(_CorpusEdge(
                filename=(row.get("before") or {}).get("artifact")
                or f"{c['key']}:{row.get('index')}",
                from_state=b_state, to_state=a_state, action=str(row.get("rung") or "any")))

    if len(eligible) < _MIN_TRAIN_ROWS:
        return {"ok": False, "reason": "insufficient_confident_rows",
                "eligible": len(eligible), "taught": taught, "skipped": skipped,
                "detail": (f"Only {len(eligible)} row(s) are trainable "
                           f"({taught} teacher-labeled, the rest needing uncertainty < "
                           f"{MAX_TRAIN_UNCERTAINTY} on both sides): {skipped['no_belief']} had "
                           f"no belief, {skipped['uncertain']} were too uncertain. Label rows in "
                           f"the review panel — a teacher who watched the drive outranks two "
                           f"witnesses that split.")}

    result = state_transition.train_transition_model(_artifacts_root(), captures=eligible)
    return {**result, "eligible": len(eligible), "taught": taught, "skipped": skipped}


class CorrectionBody(BaseModel):
    index: int
    ts: str = ""                 # the row's own timestamp — guards against a moved corpus
    # None = a note (or an explicit agreement) without changing the verdict.
    verdict: Optional[str] = None
    note: str
    by: str = "operator"
    # Ground-truth STATE labels. Supply both and the row becomes trainable regardless of what
    # the witnesses made of the page — this is the seam that turns review into training data.
    before_state: str = ""
    after_state: str = ""


def train_after_label() -> None:
    """The background crank behind train-on-label (2026-08-09): a state label just landed, so
    everything that label feeds refits — the planner's transition table AND the perception
    witnesses (a labeled transition is two witness examples via
    `perception.dataset.transition_label_rows`; until then labels reached only the edge table
    and the witnesses stayed frozen, which is the cold-start circle wearing a new coat).

    Best-effort by design: training must never fail a label write — the label is the valuable
    thing, the refit can always be re-run.
    """
    try:
        train_from_corpus()
    except Exception:  # noqa: BLE001 — the label is already safe; the refit is re-runnable
        logger.exception("train_after_label: transition-table refit failed")
    try:
        from perception import train as perception_train

        fitted = perception_train.fit()
        perception_train.save(fitted, promote=True)
    except Exception:  # noqa: BLE001
        logger.exception("train_after_label: witness refit failed")


@router.post("/api/transitions/{key}/correct")
def correct_row(key: str, body: CorrectionBody,
                background: BackgroundTasks = None) -> dict[str, Any]:
    """Write one teacher correction. The note is REQUIRED: a correction should cite what the
    row's own evidence shows — it is a training signal, not a second opinion.

    When the correction carries state labels and `settings.train_on_label` is on, the refit
    runs in the background after the response — label, then learn, every time."""
    if not body.note.strip():
        raise HTTPException(status_code=422,
                            detail="A correction needs a note citing the row's own evidence — "
                                   "an unexplained override teaches nothing.")
    if bool(body.before_state) != bool(body.after_state):
        raise HTTPException(status_code=422,
                            detail="A state label needs BOTH sides — a transition is an edge, and "
                                   "half an edge trains nothing.")
    try:
        row = sr.correct_transition(key, body.index, verdict=body.verdict,
                                    note=body.note.strip(), by=body.by, expected_ts=body.ts,
                                    before_state=body.before_state.strip(),
                                    after_state=body.after_state.strip())
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    trained = False
    if body.before_state and settings.train_on_label and background is not None:
        background.add_task(train_after_label)
        trained = True
    return {"ok": True, "row": {**row, "narration": narrate(row)}, "training_queued": trained}
