"""build_bundle — compose the observation surfaces into one frozen Bundle.

This is the OBSERVE half of observe() -> decide() -> act(). It is COMPOSITION, not
construction: every input already exists, and the point is to assemble them into the exact
shape the reasoner (and, later, L4) reads — see `interaction.decision.Bundle`.

  goal half   — task_spec.spec_for + is_complete            ("are we done?")
  state half  — apply_recipe.describe_for_ats (per ATS)     ("where are we?")
  form half   — /scan_required's `unanswered`, sanitised    ("what's actually unanswered?")
  history half— the last k decision rows                    ("what just happened?")
  join keys   — route_template (always) + AX fingerprint    ("how does this row join?")

KEPT PURE / NO IO, deliberately: the caller fetches the live surfaces (scan via the MCP
endpoint, the journal tail from disk) and passes them in, so a bundle can be REBUILT
byte-identically from journaled inputs — which is what makes the replay evals (M5) possible.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import apply_recipe
import ats_registry
import task_spec
from interaction.decision import Bundle, sanitize_unanswered
from interaction.delta import identities_from_ax
from interaction.fingerprint import route_template

#: Cap the serialised lessons so a bundle prompt stays cheap. The full LESSONS dicts are
#: paragraphs; the reasoner needs their GIST, and a distilled policy will learn which lesson
#: matters rather than re-reading all of them each step. Truncate per-entry and in total.
_LESSON_ENTRY_MAX = 220
_LESSON_TOTAL_MAX = 1600


def _serialize_lessons(lessons: dict[str, Any]) -> str:
    """A compact `- key: gist` rendering of an ATS's LESSONS dict, length-capped."""
    if not lessons:
        return ""
    lines: list[str] = []
    used = 0
    for key, val in lessons.items():
        gist = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        gist = " ".join(gist.split())
        if len(gist) > _LESSON_ENTRY_MAX:
            gist = gist[:_LESSON_ENTRY_MAX] + "…"
        line = f"- {key}: {gist}"
        if used + len(line) > _LESSON_TOTAL_MAX:
            lines.append(f"- (+{len(lessons) - len(lines)} more lessons truncated)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def _shape_recent(journal_tail: Optional[list[dict]]) -> tuple[dict, ...]:
    """Defensively reduce journal rows to the history triple the Bundle carries. Accepts
    either decision rows (`params.field`) or intent rows (`field`) — both join the same way."""
    out: list[dict] = []
    for r in (journal_tail or [])[-5:]:
        if not isinstance(r, dict):
            continue
        fld = r.get("field")
        if fld is None:
            fld = (r.get("params") or {}).get("field") if isinstance(r.get("params"), dict) else None
        out.append({"intent": r.get("intent"), "field": fld, "outcome": r.get("outcome")})
    return tuple(out)


def build_bundle(
    task: str,
    url: str,
    page_text: str = "",
    *,
    goal_text: str = "",
    ats: Optional[str] = None,
    scan: Optional[list[dict]] = None,
    journal_tail: Optional[list[dict]] = None,
    fingerprint: Optional[str] = None,
    ax_candidates: Optional[list[dict]] = None,
    belief: Optional[dict] = None,
    window: Optional[dict] = None,
    staleness: Optional[dict] = None,
) -> Bundle:
    """Assemble the controller's input for ONE tab. Pure — no IO, no network.

    Args:
        task: the TaskSpec name (or a free label) — routes the goal check.
        url, page_text: the live tab's address and text (read-only observation).
        goal_text: free-text goal; defaults to the matched spec's description, else the task.
        ats: override the ATS classification (e.g. when an embedded iframe host is known).
        scan: `/scan_required`'s `unanswered` list, verbatim — sanitised here (no selectors/PII).
        journal_tail: the run's last few decision/intent rows — the history half.
        fingerprint: the AX state sha256, when a scan produced one (opportunistic, may be None).
        ax_candidates: `/ax_scan`'s candidates, reduced here to `role|name` identities — the raw
            material for the state delta (`interaction/delta.py`). NOT rendered into the prompt.
        belief: a serialized `BeliefState` from the two-witness observer, when one is promoted.
        window: a serialized `controller.window.WindowState` — what ELSE is open. Pure
            passthrough, like `belief`; None when nothing listed the tabs.
            Passed IN rather than computed here on purpose: this function is pure, and the
            observer needs a screenshot off the wire. None is the normal case until a witness is
            fitted, and it must stay a real answer (PLAN_perception_v1 §3.3).
        staleness: a serialized `perception.staleness.Staleness` — how old this view is and
            what that costs. Pure passthrough, like `belief`: assessed by the caller, which
            is the only place that knows when we last acted. None when nobody assessed it.
    """
    ats = ats or ats_registry.classify_ats(url)
    desc = apply_recipe.describe_for_ats(ats, url, page_text)
    state = desc.get("state")

    spec = task_spec.spec_for(task=task, task_goal=goal_text)
    done = bool(spec and spec.is_complete(url, page_text)) or \
        apply_recipe.is_terminal_state(ats, state)

    return Bundle(
        task=task,
        goal_text=goal_text or (spec.description if spec else task),
        done=done,
        url=url,
        route=route_template(url),
        state=state if state and state != "unknown" else None,
        is_branch=bool(desc.get("is_branch")),
        human_required=bool(desc.get("human_required")),
        ats=ats,
        fingerprint=fingerprint,
        branch_note=desc.get("branch_note") or "",
        recipe_step=desc.get("recipe_step"),
        next_action=desc.get("next_action"),
        expected_next=tuple(desc.get("expected_next") or ()),
        lessons=_serialize_lessons(apply_recipe.lessons_for(ats)),
        unanswered=sanitize_unanswered(scan),
        recent=_shape_recent(journal_tail),
        ax_identities=identities_from_ax(ax_candidates),
        belief=belief,
        window=window,
        staleness=staleness,
    )
