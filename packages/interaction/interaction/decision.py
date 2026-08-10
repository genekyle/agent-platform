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
#: `student` sits ABOVE `model` in trust order and BELOW it in the tuple's cheapest-first ordering
#: — it is the local, $0 policy (PRINCIPLES §9: the student is the central cog; Haiku is a cheap
#: backstop, not a student). Added 2026-07-20 when the seat was first filled. Additive by
#: construction: every row journaled before today keeps a valid rung, so no version bump.
RUNGS = ("recipe", "cache", "student", "model", "teacher", "human")

#: Why a Decision handed up, CLOSED like every other taxonomy here. Each names a different
#: problem with a different remedy, which is the point: "no program for a page I can name" is a
#: compile job, "I cannot name this page" is a labelling job, and "the model wasn't sure" is a
#: teaching job. Collapsing them into one `escalate=True` threw that away.
ESCALATION_AXES = (
    "none",             # not escalating
    "unknown_state",    # the page is not in the registry — label it
    "no_program",       # a known state with no compiled program — teach it, then it compiles
    "stale_program",    # there was a program and the site moved under it — recompile
    "branch",           # off-spine by the recipe's own account — the operator decides
    "human_required",   # sign-in / credentials / a stop-state. Never ours, ever.
    "model_declined",   # the reasoner honestly returned nothing rather than guessing
    "low_confidence",   # it had an answer and the answer was not good enough to act on
    "task_complete",    # nothing left to decide
)


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

    # --- the raw material for the state DELTA (PLAN_supervisor §1)
    #: The screen's `role|normalized-name` identity set, from `delta.identities_from_ax`. Carried
    #: so the loop can DIFF consecutive observations — the supervisor's always-on cheap sense.
    #: DELIBERATELY NOT RENDERED by `bundle_to_prompt`: a few hundred identities inline is the
    #: raw-AX dump this architecture keeps out of prompts (PLAN_reasoner_v2 §4). What a model
    #: sees is the small, capped `delta_to_prompt(...)` summary instead. Appended last, defaulted,
    #: and prompt-invisible, so every row journaled before it stays valid (schema note above).
    ax_identities: tuple[str, ...] = ()

    #: --- perception: WHO says we are here, and how unsure they are about what
    #: `BeliefState.as_dict()` from the two-witness observer (PLAN_perception_v1 §3.3), or None
    #: when nothing is promoted — which is a real answer and must stay one: an unfitted
    #: perception stack means the controller runs exactly as it did before, not that it runs
    #: blind on a half-built witness.
    #:
    #: Carried as a plain dict rather than the dataclass so a journaled row round-trips through
    #: JSON without importing the perception package (the trainer reads rows on machines that
    #: never fitted a witness). Appended last, defaulted, and — like `ax_identities` —
    #: DELIBERATELY NOT RENDERED by `bundle_to_prompt` while the observer is in shadow: putting
    #: it in the prompt changes the feature contract, and a mid-corpus drift there makes every
    #: row before it untrainable. It goes into the prompt in one deliberate, version-bumped
    #: change once shadow agreement says it earns its tokens, not as a side effect of wiring.
    belief: Optional[dict] = None

    #: --- the window: "what ELSE is open, and is any of it in the way"
    #: `controller.window.WindowState.as_dict()`, or None when nothing listed the tabs.
    #:
    #: The controller drove tabs it could not see until 2026-07-23: everything above is SINGULAR —
    #: one url, one state — so `decide()` could not reason about the window, the supervisor could
    #: not name a tab fault, and the maturity registry could never learn one. Three incidents on
    #: 2026-07-22 were all this single blind spot: a capture that photographed a stale tab, an
    #: Apply click whose new tab the drive never noticed, and a Submit that bounced because the
    #: tab it was pressed on had gone stale.
    #:
    #: UNLIKE `belief` and `ax_identities`, this one IS rendered — see `window_to_prompt` — and
    #: the reason is that the reasoner cannot act on what it cannot read, which was the whole
    #: complaint. The feature-contract risk is handled by the renderer instead of by omission: it
    #: emits NOTHING when `window` is absent, so every bundle journaled before this field existed
    #: produces a byte-identical prompt and the replay evals stay comparable. Counts and roles
    #: only, never a url per tab — this rides in a prompt and into every journaled row.
    window: Optional[dict] = None

    #: --- staleness: "is what we are looking at still true, and what does that cost us"
    #: `perception.staleness.Staleness.as_dict()`, or None when nothing assessed it.
    #:
    #: The axis none of the others cover. Perception says WHERE we are, `reach` says whether we
    #: can touch it, `unexpected` says whether it is where we expected — and a drive can pass all
    #: three on a view that went stale twenty minutes ago.
    #:
    #: PROTOTYPE (2026-07-26): the thresholds behind `level` are guesses, and the field exists
    #: mostly to JOURNAL THE RAW SIGNAL VALUES so they can be fitted from our own history. So it
    #: follows `belief`, not `window`: appended last, defaulted, and DELIBERATELY NOT RENDERED by
    #: `bundle_to_prompt`. Putting an unmeasured level in front of the reasoner would change the
    #: feature contract on the strength of a guess, and every row journaled before it would stop
    #: being comparable. It goes into the prompt, if ever, in one deliberate version-bumped change
    #: once the thresholds are measured — not as a side effect of wiring it up.
    staleness: Optional[dict] = None

    #: --- applied: "have we already applied to this job?" — `applied_index.AppliedVerdict`, or
    #: None when nobody asked.
    #:
    #: Unlike `staleness`, this IS rendered into the prompt, because it is a fact read out of our
    #: own database rather than a threshold we guessed, and it changes what the right next action
    #: is: there is no good next move on a job we have already applied to. It cost a whole drive
    #: to learn that the hard way — reopening a step, hopping a branded wrapper into Workday and
    #: signing in, only to be told "You've already applied for this job" (2026-07-27).
    #:
    #: Rendered ONLY when present and non-empty, exactly like `window`, so every bundle journaled
    #: before this field existed still produces a byte-identical prompt and the replay evals stay
    #: comparable.
    applied: Optional[dict] = None

    #: --- capture: what the controller SAW this turn — durable references to the observation's
    #: artifact and screenshot, or None when nothing was collected (`collect=False` credential
    #: posture, or the capture simply failed).
    #:
    #: Shape: `{"artifact": <observer-traces JSON basename>, "screenshot": <absolute path str>,
    #: "screenshot_filename": <observer-screenshots basename>}` — the basenames are the durable
    #: refs (the 2026-07-22 stale-path finding: directories get renamed, filenames survive), the
    #: absolute path is a best-effort convenience matching the transition-corpus convention.
    #:
    #: Follows `belief`/`staleness`, not `applied`: appended last, defaulted, and DELIBERATELY NOT
    #: RENDERED by `bundle_to_prompt` — the reasoner's feature contract does not change because we
    #: started keeping receipts. Also DELIBERATELY NOT in `bundle_digest`: two observations of the
    #: same decision point must keep the same identity even though each has its own screenshot.
    #: It exists because 117 journaled decisions carried zero image references (2026-08-09 audit):
    #: the evidence the teacher decided FROM was captured, used once, and orphaned. A correction
    #: without its screenshot is half a training pair.
    capture: Optional[dict] = None


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

    #: WHY this decision is handing up, from a closed vocabulary (`ESCALATION_AXES`). Empty on a
    #: decision that acts.
    #:
    #: It exists because an escalation used to be information-free: `intent="observe"`,
    #: `confidence=0.0`, no hypothesis. So the hardest turns — precisely the ones the teacher gets
    #: paid for — produced no scoreable local prediction, and `shadow_agreement` could never
    #: measure the system on them. Naming the axis makes an escalation a DIAGNOSIS ("I know where
    #: I am, I have no program for it" is a different problem from "I cannot name this page"), and
    #: pairing it with a real proposed intent makes the local layers measurable even on the turns
    #: they lose. Appended last and defaulted, so every journaled row stays valid.
    escalation_axis: str = ""

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

    # --- the supervisor's verdict on THIS action (PLAN_supervisor §1). Appended optional
    # columns, not a second corpus: `decision_journal.jsonl` is already written every controller
    # step, and a parallel supervision file would repeat the 2026-07-16 corpus reckoning. These
    # are what a distilled supervisor trains on, joined to the decision that earned them.
    supervisor_class: Optional[str] = None        # a FailureClass value
    supervisor_recovery: Optional[str] = None     # a RecoveryPlay value
    supervisor_stuck: Optional[float] = None      # [0,1]
    supervisor_rung: Optional[str] = None         # deterministic | model | teacher
    supervisor_rationale: str = ""                # the "why" — is_real_rationale applies (§10)
    supervisor_evidence: tuple[str, ...] = ()     # the inputs cited
    #: What actually changed as a result of this action — the graded label. Kept as the small
    #: scalars (not the identity lists) so a row stays a corpus row and not an AX dump.
    delta_moved: Optional[bool] = None
    delta_churn: Optional[int] = None

    #: --- perception (PLAN_perception_v1 §3.3). The scalars only, same rule as `delta_*`: a row
    #: is a corpus row, not a dump. `belief_state` is what the WITNESSES concluded, which may
    #: differ from `state` (what the recipe's URL/text matcher concluded) — and the rows where
    #: they differ are the ones worth reading. `belief_agreement` is the measured signal: rows
    #: where the two witnesses agree are right 77.9% of the time versus 48.2% when they split.
    belief_state: Optional[str] = None
    belief_agreement: Optional[str] = None        # agree | split | one_sided | no_evidence
    belief_novelty: Optional[float] = None        # [0,1] — percentile, 0.90 = the flag ceiling
    belief_state_uncertainty: Optional[float] = None

    #: --- authority: WHO owned this turn, and why (interaction/authority.py).
    #: `control_mode` is load-bearing rather than descriptive: `controller/maturity.py` reads it
    #: back to count clean propose-approve runs, which is one of the two things certification
    #: requires. A row with no mode contributes nothing to promotion — which is exactly right for
    #: every row journaled before authority existed.
    control_mode: Optional[str] = None            # green | yellow | orange | red
    authority_reason: str = ""                    # one line: why this mode, for the operator
    transition_maturity: Optional[str] = None     # the ladder rung this transition sat on
    authority_axis: str = ""                      # the belief axis that blocked, if one did
    reach_gaps: tuple[str, ...] = ()              # what the executor could not operate

    #: Why a Decision handed up (`ESCALATION_AXES`). Kept beside the proposal it handed up WITH,
    #: so an escalated row is a scoreable prediction rather than a blank — see `Decision`.
    escalation_axis: str = ""

    # --- cost / provenance
    session_id: Optional[str] = None
    duration_ms: int = 0
    cost_usd: float = 0.0

    #: --- staleness (PROTOTYPE 2026-07-26): how old the view was when this decision was made.
    #: The RAW AGES are the point, not the level. The thresholds behind `staleness_level` are
    #: unmeasured guesses, so journaling only the level would bake today's guess into the corpus
    #: and leave nothing to fit. With the seconds here, the calibration question — "at what value
    #: does the next action's failure rate rise?" — is answerable from rows we are already
    #: writing, against `outcome` and `verified` in this same row.
    #: `staleness_rules` stamps which guess produced the level, so rows written under different
    #: thresholds are never pooled into one fit.
    staleness_level: Optional[str] = None         # fresh | yellow | orange | red
    staleness_verdict: Optional[str] = None       # continue | refresh | renew | handoff
    staleness_rules: Optional[str] = None
    staleness_idle_s: Optional[float] = None
    staleness_page_age_s: Optional[float] = None

    #: --- capture: what this decision was looking at, by durable NAME (never pixels, never
    #: paths that go stale — basenames resolve under the current artifacts root and the existing
    #: `GET /api/observations/screenshots/{filename}` route serves them). None on rows journaled
    #: from a `collect=False` credential flow, honestly. Flattened from `Bundle.capture` at the
    #: `record_for` choke point like belief and staleness, so no seam can journal a decision and
    #: silently drop what it saw — the exact 0-of-117 hole the 2026-08-09 audit measured.
    capture_artifact: Optional[str] = None        # observer-traces JSON basename
    capture_screenshot: Optional[str] = None      # observer-screenshots PNG basename


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


def window_to_prompt(window: Optional[dict]) -> str:
    """The compact `# WINDOW` block: what ELSE is open, and is any of it in the way.

    Returns "" when there is nothing to say, and that is load-bearing rather than tidy: a bundle
    journaled before `Bundle.window` existed renders exactly the prompt it always rendered, so the
    replay evals and shadow-agreement numbers stay comparable across the change.

    Counts and roles only. A url per tab would put the whole window into every prompt and every
    journaled row, which is the raw-dump this architecture keeps out of prompts.
    """
    if not window or not window.get("count"):
        return ""
    roles = window.get("roles") or {}
    role_text = ", ".join(f"{k}={v}" for k, v in roles.items()) or "(none)"
    lines = [f"tabs: {window.get('count')} (budget {window.get('budget')})",
             f"roles: {role_text}",
             f"active: {window.get('active_role') or '(unknown)'}"]
    if window.get("health") and window.get("health") != "ok":
        lines.append(f"health: {window.get('health')} — the window is not in a normal state")
    for a in window.get("anomalies") or []:
        lines.append(f"anomaly[{a.get('kind')}]: {a.get('why')}")
    if window.get("over_budget"):
        lines.append("over_budget: yes — a cluttered window slows every operation in it")
    closable = window.get("closable") or []
    if closable:
        lines.append(f"closable: {len(closable)} ({', '.join(c.get('role', '?') for c in closable)})")
    return "\n".join(lines)


def applied_to_prompt(applied: Optional[dict]) -> str:
    """One line about whether this job already has an application on file, or "" for silence.

    Silence is the normal case and it must stay byte-for-byte silent: a bundle nobody asked the
    question of, and a bundle whose answer was "no", both render nothing. Only a positive answer
    is worth the reasoner's attention, and the two positives read differently on purpose —
    `applied` is a fact to act on, `likely_applied` is a similarity worth a human glance.
    """
    if not isinstance(applied, dict):
        return ""
    status = str(applied.get("status") or "")
    if status not in ("applied", "likely_applied"):
        return ""
    when = str(applied.get("applied_at") or "")[:10]
    matched = applied.get("matched_on") or "?"
    if status == "applied":
        return (f"ALREADY APPLIED (matched on {matched}"
                + (f", {when}" if when else "") + ") — do not apply again")
    return (f"possibly already applied (matched on {matched}"
            + (f", {when}" if when else "") + ") — worth checking before applying")


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
    window = window_to_prompt(bundle.window)
    if window:
        parts += ["", "# WINDOW", window]
    applied = applied_to_prompt(bundle.applied)
    if applied:
        parts += ["", "# APPLIED", applied]
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
        # Perception travels with the replay case: a decision made while the witnesses disagreed
        # is a DIFFERENT decision point from the same page with them agreeing, and a replay that
        # dropped that would silently re-run the easy version of the case. Already PII-free by
        # construction (state ids, floats, witness names — `BeliefState.as_dict`).
        "belief": dict(bundle.belief) if bundle.belief else None,
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
