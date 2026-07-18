"""The frozen contract of the Controller (V1) — the missing `decide()`.

    observe() -> decide() -> act()

`contract.py` froze what the actor may DO (the Intent vocabulary) and what an action
RESULTED IN (the Outcome taxonomy). This module freezes the two shapes the *reasoner*
speaks in: the `Bundle` it reads (observe) and the `Decision` it emits (decide). A
`DecisionRecord` joins the two to an outcome and lands on the same append-only spine as
the intent journal.

--------------------------------------------------------------------------------------
Why the Bundle is frozen, and why `bundle_to_prompt` is part of the contract
--------------------------------------------------------------------------------------
This copies the load-bearing trick from `resolve_answer.SemanticQuestion`: the object a
reasoner receives is A PROMPT SURFACE TODAY AND A TRAINING FEATURE SET TOMORROW. The Haiku
rung is prompted with `bundle_to_prompt(bundle)`; the local L4 policy will be trained on the
same serialization. So the serialization is not a convenience — it is the feature contract,
and a drift in it silently invalidates every journaled row that came before. Treat
`bundle_to_prompt`'s format as frozen the same way the Intent members are frozen; snapshot
it in a test so a careless edit fails loudly.

--------------------------------------------------------------------------------------
The two join keys, reconciled (this bit is easy to get wrong)
--------------------------------------------------------------------------------------
Everywhere else in this codebase "fingerprint" means the AX state sha256 from
`fingerprint.compute` — and it is OPPORTUNISTIC: it only exists when an AX scan already ran
(see `journal.IntentRecord.fingerprint`). But the controller must journal a joinable row on
EVERY step, including the many where no AX scan ran. So the Bundle carries BOTH:

  - `route`       — `route_template(url)`, ALWAYS present, costs nothing. The cheap join key.
  - `fingerprint` — the AX sha256, present only when a scan ran. The sharp join key.

The spine rule "no row without a fingerprint" (PLAN_controller_v1 §1) is enforced against
the ALWAYS-present key: `log_decision` refuses a row with no `route`. That keeps the rule
satisfiable on every step while preserving the codebase's meaning of `fingerprint`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Bump on any change to a shape's MEANING or to `bundle_to_prompt`'s FORMAT — both are the
# feature contract a distilled policy trains against, and a mid-corpus drift makes the rows
# before it untrainable. Adding a new optional field is backwards-compatible (old rows stay
# valid); renaming or re-ordering the prompt is not.
DECISION_SCHEMA_VERSION = "v1"

#: Below this, the controller ASKS rather than acts — the same floor `resolve_answer` uses,
#: one altitude up. Deterministic rungs (recipe/cache) score 1.0; only a model returns less.
DECISION_CONFIDENCE_THRESHOLD = 0.75

#: The rungs of the decide cascade, cheapest first — the join between a Decision and WHO made
#: it. Kept as strings (not an enum) so the journal stays a plain corpus, but centralised here
#: so a typo is catchable and the UI can enumerate them.
RUNGS = ("recipe", "cache", "model", "teacher", "human")


# --- the Open Brain: a "why" is training signal only if it's really there (PRINCIPLES §10) ------
#: What the `rationale` column fills with when a rung emits a template or a teaching seam forgot
#: to solicit one. §10 treats these as MISSING reasoning (not present-but-terse): `summarize()`
#: counts them and the eval fails a teacher/golden row that carries one, so the teacher's real
#: "why" cannot silently degrade to a stub the way `cli_reviewer` used to hardcode "operator
#: correction". Lowercased, stripped comparison.
PLACEHOLDER_RATIONALES = frozenset({
    "", "operator correction", "correction", "teacher correction", "manual", "n/a", "na",
    "none", "-", "--", "x", "t", "r", "login", "test", "todo", "tbd", "fixme", "wip", "placeholder",
})


def is_real_rationale(rationale: Optional[str]) -> bool:
    """True when a rationale carries actual reasoning (§10 — the Open Brain).

    A real "why" is non-empty, is not a known stub, and clears a small length floor — enough to
    tell "clicked Continue because every required field on this step is now answered" apart from
    "x". The teacher's reasoning only *transfers* to the students if it is actually written down;
    this is the predicate the placeholder-metric and the teacher/golden-row gate agree on.
    """
    if not rationale:
        return False
    s = rationale.strip().lower()
    return s not in PLACEHOLDER_RATIONALES and len(s) >= 8


# --- selector guard (invariant #10) -------------------------------------------------
#: Params and bundle fields must carry SEMANTIC references (field names, values), never a
#: selector / xpath / backend_node_id — those are the recipe's and the API's business, and a
#: policy that learns to emit them has learned the wrong altitude. This predicate is the one
#: place that decides "does this string look like addressing?"; the journal and the M3 parser
#: both call it so they cannot disagree.
_SELECTOR_SHAPE = re.compile(
    r"""(^\s*[.#\[]) |          # .class  #id  [attr=…]
        (::?[a-z-]+\() |         # :nth-child(  ::after(
        (\bdata-automation-id\b) |
        (backend_node_id) |
        (/html|//\*|/\w+\[) |    # xpath-ish
        (\bnode\s+\d+\b)         # "node 4821" — the addressed_by=backend_node_id shape
    """,
    re.I | re.X,
)


def looks_like_selector(value: Any) -> bool:
    """True when a string looks like a selector/xpath/node-id rather than a semantic name.

    Deliberately biased toward catching: a false positive costs one rejected param (the caller
    re-emits a semantic name), a false negative lets addressing leak into the policy's feature
    set, which is the failure this guard exists to prevent.
    """
    return isinstance(value, str) and bool(_SELECTOR_SHAPE.search(value))


# --- the form half, sanitised -------------------------------------------------------
#: The semantic keys a scanned-required field contributes to the Bundle. `/scan_required`
#: also returns `selector` (`#id`) and `value_read_at` (`[class*=singleValue]`) — both
#: selector-shaped, dropped by invariant #10 — and `value_preview`, a slice of the field's
#: current value, dropped by PRINCIPLES §4 (never journal a value; the reasoner only needs to
#: know the field is unanswered/invalid, which `answered`/`valid` already say).
_UNANSWERED_KEEP = ("field", "kind", "required_via", "answered", "valid")


def sanitize_unanswered(items: Any) -> tuple[dict, ...]:
    """Reduce raw `/scan_required` rows to the semantic form facts (no selectors, no values)."""
    out: list[dict] = []
    for it in items or ():
        if isinstance(it, dict):
            out.append({k: it[k] for k in _UNANSWERED_KEEP if k in it})
    return tuple(out)


# --- the Bundle: what the controller OBSERVES ---------------------------------------
@dataclass(frozen=True)
class Bundle:
    """The reasoner's input. Composed from surfaces that already exist (state observer,
    scan_required, TaskSpec, the journal tail) — see `controller/bundle.py`. Frozen because
    it is the feature contract; keep it small and stable.
    """

    # --- goal: "what are we trying to do"
    task: str                             # TaskSpec.name (or a free label)
    goal_text: str                        # free-text goal
    done: bool                            # TaskSpec.is_complete(url, page_text)

    # --- state: "where are we"
    url: str
    route: str                            # route_template(url) — the always-present join key
    state: Optional[str]                  # map_url_to_state / describe_tab
    is_branch: bool
    human_required: bool
    ats: Optional[str] = None             # classify_ats(url) — which recipe owns this state
    fingerprint: Optional[str] = None     # AX sha256 — opportunistic (present iff a scan ran)
    branch_note: str = ""

    # --- recipe context: "what does the corpus say comes next"
    recipe_step: Optional[int] = None
    next_action: Optional[str] = None     # the recipe's PROSE action (inert until compiled)
    expected_next: tuple[str, ...] = ()
    lessons: str = ""                     # the ATS's LESSONS, serialised

    # --- form ground truth: "what is actually unanswered"
    unanswered: tuple[dict, ...] = ()     # sanitized scan_required output

    # --- short history: "what just happened"
    recent: tuple[dict, ...] = ()         # last k (intent, field, outcome) rows, k<=5


# --- the Decision: what the controller EMITS ----------------------------------------
@dataclass(frozen=True)
class Decision:
    """One intent, one expectation, one confidence. The policy's whole output."""

    intent: str                           # a contract.Intent value — nothing else, ever
    params: dict                          # field/value refs only, NO selectors (invariant #10)
    confidence: float
    rung: str                             # recipe | cache | model | teacher | human
    rationale: str                        # one sentence; journaled, becomes training signal
    expected_next: tuple[str, ...] = ()   # states this should land on; a miss => escalate
    escalate: bool = False                # True -> hand up the ladder instead of acting
    # The Bundle keys this rationale CITES — the teachable receipts (§10, mirrors v2's
    # PlanStep.evidence). e.g. ("state", "unanswered[0].field", "recent[-1].outcome"). Makes the
    # "why" a citation the students can check, not a vibe; appended LAST so positional callers and
    # every existing journalled row stay valid (schema note above: adding an optional field is safe).
    evidence: tuple[str, ...] = ()

    @property
    def acts(self) -> bool:
        """Whether this decision results in an action (vs. an escalation/hand-up)."""
        return not self.escalate


# --- the record: Decision + Bundle digest + outcome, on the spine -------------------
@dataclass
class DecisionRecord:
    """One decide() call, start to finish. Column order groups by the question it answers.
    Keep columns STABLE — this is a corpus. Add, don't rename.
    """

    # --- identity
    ts: str
    schema_version: str
    intent: str
    rung: str
    outcome: Optional[str] = None         # Outcome value AFTER acting; None if escalated/pending

    # --- the decision (what L4 learns to emit)
    params: dict = field(default_factory=dict)
    confidence: float = 0.0
    escalate: bool = False
    rationale: str = ""
    evidence: tuple[str, ...] = ()        # Bundle keys the rationale cites (§10 — the Open Brain)
    expected_next: tuple[str, ...] = ()

    # --- verify: did the intent land where it expected?
    landed_state: Optional[str] = None
    verified: Optional[bool] = None       # landed_state in expected_next (None until observed)

    # --- the bundle it decided on (digest + the join columns; NO PII, NO selectors)
    bundle_digest: str = ""
    task: str = ""
    state: Optional[str] = None
    ats: Optional[str] = None
    route: str = ""                       # the always-present spine join key
    fingerprint: Optional[str] = None     # opportunistic AX sha256
    url: Optional[str] = None

    # --- teaching (M4): a golden row carries BOTH the proposal and the correction
    golden: bool = False
    proposed_intent: Optional[str] = None
    proposed_params: Optional[dict] = None
    proposed_rung: Optional[str] = None
    # §10 — the Open Brain: keep the PROPOSAL's reasoning too, not just what it proposed. On a
    # correction the contrast between the backstop's wrong "why" and the teacher's right "why" is
    # the densest lesson in the corpus; dropping it (as the code did before) threw that away.
    proposed_rationale: Optional[str] = None
    proposed_evidence: tuple[str, ...] = ()

    # --- measurement (M5): a shadow row decided without acting, beside a teacher step
    shadow: bool = False
    # A PII/selector-free snapshot of the Bundle, stored on the rows that are REPLAY CASES
    # (golden + shadow) so the offline replay suite can re-run decide() on the exact input.
    bundle_snapshot: Optional[dict] = None

    # --- cost / provenance
    session_id: Optional[str] = None
    duration_ms: int = 0
    cost_usd: float = 0.0


# --- the frozen serialization: prompt today, feature set tomorrow -------------------
def _fmt_bool(b: Optional[bool]) -> str:
    return "yes" if b else "no"


def _fmt_unanswered(items: tuple[dict, ...]) -> str:
    if not items:
        return "  (none — form is complete or has no required fields)"
    lines = []
    for it in items:
        lines.append(
            f"  - {it.get('field', '?')} [{it.get('kind', '?')}] "
            f"{it.get('required_via', '?')} "
            f"answered={_fmt_bool(it.get('answered'))} valid={_fmt_bool(it.get('valid'))}"
        )
    return "\n".join(lines)


def _fmt_recent(items: tuple[dict, ...]) -> str:
    if not items:
        return "  (no prior steps this run)"
    lines = []
    for it in items:
        verb = it.get("intent", "?")
        fld = it.get("field")
        tgt = f' "{fld}"' if fld else ""
        lines.append(f"  - {verb}{tgt} -> {it.get('outcome', '?')}")
    return "\n".join(lines)


def bundle_to_prompt(bundle: Bundle) -> str:
    """The STABLE serialization of a Bundle — the reasoner's prompt and L4's feature set.

    FROZEN FORMAT. This is the contract surface: change it and you change what every distilled
    policy sees, invalidating the rows journaled under the old shape. A snapshot test guards it.
    """
    expected = ", ".join(bundle.expected_next) or "(unknown)"
    step = "?" if bundle.recipe_step is None else str(bundle.recipe_step)
    parts = [
        "# GOAL",
        f"task: {bundle.task}",
        f"goal: {bundle.goal_text}",
        f"done: {_fmt_bool(bundle.done)}",
        "",
        "# STATE",
        f"ats: {bundle.ats or '(unknown)'}",
        f"state: {bundle.state or '(unknown)'}",
        f"url: {bundle.url}",
        f"branch: {_fmt_bool(bundle.is_branch)}",
        f"human_required: {_fmt_bool(bundle.human_required)}",
    ]
    if bundle.branch_note:
        parts.append(f"branch_note: {bundle.branch_note}")
    parts += [
        "",
        "# RECIPE",
        f"step: {step}",
        f"next_action: {bundle.next_action or '(none)'}",
        f"expected_next: {expected}",
    ]
    if bundle.lessons:
        parts += ["", "# LESSONS", bundle.lessons]
    parts += [
        "",
        f"# UNANSWERED ({len(bundle.unanswered)})",
        _fmt_unanswered(bundle.unanswered),
        "",
        "# RECENT",
        _fmt_recent(bundle.recent),
    ]
    return "\n".join(parts)


def replay_snapshot(bundle: Bundle) -> dict:
    """The minimal, PII/selector-free projection of a Bundle sufficient to re-run `decide()`
    deterministically offline. Stored on golden/shadow rows so a correction becomes a permanent,
    self-contained regression case (drops url/lessons — route is the join key, lessons only feed
    the model rung, which CI does not replay)."""
    return {
        "task": bundle.task,
        "goal_text": bundle.goal_text,
        "done": bundle.done,
        "url": bundle.route,          # route, not the raw url (join key, no query PII)
        "route": bundle.route,
        "state": bundle.state,
        "is_branch": bundle.is_branch,
        "human_required": bundle.human_required,
        "ats": bundle.ats,
        "fingerprint": bundle.fingerprint,
        "recipe_step": bundle.recipe_step,
        "expected_next": list(bundle.expected_next),
        "unanswered": [dict(u) for u in bundle.unanswered],   # already sanitised
    }


def bundle_digest(bundle: Bundle) -> str:
    """A stable, PII-free sha256 of a Bundle — what a DecisionRecord references instead of
    storing the whole thing. Keyed on the SEMANTIC content that identifies the decision point
    (task/state/route/fingerprint + the unanswered field-set), not on volatile prose."""
    payload = {
        "v": DECISION_SCHEMA_VERSION,
        "task": bundle.task,
        "ats": bundle.ats,
        "state": bundle.state,
        "route": bundle.route,
        "fingerprint": bundle.fingerprint,
        "done": bundle.done,
        "is_branch": bundle.is_branch,
        "unanswered": sorted(str(it.get("field", "")) for it in bundle.unanswered),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
