"""The Supervisor's contract — what just happened, NAMED.

`decide()` answers *"what next?"* before an action. This module answers *"what just happened, and
is it what we meant?"* after one — replacing the loop's boolean `verified: true|false` with a
cited diagnosis drawn from a CLOSED failure taxonomy, plus a recovery selected from a CLOSED
playbook. See `docs/PLAN_supervisor.md`.

Why a taxonomy and not open-ended reasoning. The classes below were **mined from this repo's own
logs on 2026-07-20**, not borrowed from the literature: 13 `verified=False` decision rows, 23
non-`ok` intent outcomes, 34 handoffs, and ~30 hand-written incidents in LEARNINGS. They form a
power law, and classifying into a small closed set + selecting a play is something a deterministic
table does for free on most turns — which is what keeps the supervisor inside the $5/week cap
while still narrating every turn.

Two members were deliberately NOT seeded, because our data does not contain them: "unexpected
modal/interstitial" (plausible, unobserved — `StateDelta.appeared` will surface it the first time
it happens, as UNKNOWN, and that is how it earns its place) and "layout drift breaking selectors",
which *cannot* occur here because addressing is by role + accessible name, never a selector.

The rung-0 classifier at the bottom is PURE and must stay that way: it runs on every turn and its
cost is the reason legibility is free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from interaction.contract import STALE_STATE_OUTCOMES, Intent, Outcome
from interaction.decision import looks_like_selector
from interaction.delta import StateDelta

#: The stale-state outcomes as plain values (the loops compare strings off the wire).
_STALE_OUTCOME_VALUES = frozenset(o.value for o in STALE_STATE_OUTCOMES)

#: Bump on any change to a member's MEANING or to `verdict_to_prompt`'s FORMAT. Adding a new
#: FailureClass / RecoveryPlay member is backwards-compatible (old rows stay valid); renaming or
#: repurposing one is not — same discipline as DECISION_SCHEMA_VERSION and the Outcome taxonomy.
SUPERVISION_SCHEMA_VERSION = "v1"

#: Below this the supervisor's own proposal is not trusted enough to act on even after its class
#: has graduated. Mirrors DECISION_CONFIDENCE_THRESHOLD one altitude up.
VERDICT_CONFIDENCE_THRESHOLD = 0.75


class FailureClass(str, Enum):
    """What went wrong, from a closed vocabulary. Each member names the incident that earned it."""

    #: The action did what it meant to. Most turns.
    NONE = "none"

    #: The action reported success and the page did not move. Live 2026-07-19 (Longroad): a blocked
    #: Continue clicked 8× scoring 100% autonomous / 100% verified. 6 of the 13 verified=False rows.
    NO_PROGRESS = "no_progress"

    #: The semantic reference did not resolve on this page. 7 not_found decisions + 9 not_found and
    #: 8 not_opened intents = 24 occurrences, the largest bucket by a wide margin.
    CONTROL_NOT_FOUND = "control_not_found"

    #: The widget shows the value; `scan_required` still calls the field unanswered. The Ethnicity
    #: react-single-select (07-18), the distance pill (07-15), the Greenhouse date month (07-15).
    #: A composite widget needs the stage->commit protocol, not a click (interaction-layers §6/§7).
    STAGED_NOT_COMMITTED = "staged_not_committed"

    #: We acted, or classified, before the page settled. The Indeed location combobox producing
    #: "Manchester, NHu" on clear+type; `_current_state()` reporting the OLD state after a Continue
    #: had already navigated (both 07-18).
    RACE_SETTLE = "race_settle"

    #: The CDP target is gone. Its signature is a SUCCESSFUL EMPTY result, not an error — the
    #: proposer swallows target-discovery failures into `errors[]` and replies HTTP 200 (07-19).
    STALE_TAB = "stale_tab"

    #: The page is not in the state registry. 6 handoffs; feeds `page_state_candidates`.
    UNRECOGNIZED_STATE = "unrecognized_state"

    #: Signed out, or the session expired mid-drive. 12 handoffs (`not_authenticated`); "the
    #: SESSION is the real enemy" (07-15).
    AUTH_WALL = "auth_wall"

    #: A required control the scanner never saw is blocking the step — the form reads complete and
    #: Continue no-ops. The lone required acknowledgment checkbox (07-18).
    MISSED_REQUIRED_CONTROL = "missed_required_control"

    #: A captcha / anti-bot challenge / checkpoint. Never mined from a verified=False row because
    #: it escalates at classify and never gets driven — which is exactly the behaviour to keep.
    #: Present so the supervisor NARRATES it rather than filing the loop's loudest stop as UNKNOWN.
    CHALLENGE = "challenge"

    #: We cannot name it. The honest bucket, never a guess wearing a label — and the metric that
    #: tells us whether the taxonomy fits reality (PLAN_supervisor §8). Never graduates (§6).
    UNKNOWN = "unknown"


class RecoveryPlay(str, Enum):
    """What to do about it — a CLOSED playbook, deliberately not prose.

    The incoming design sketched free-text recoveries ("dismiss modal via close button"). A policy
    that emits prose has learned the wrong altitude (invariant #10), and the whole reason this
    approach works is that recovery is *selection* from a small set, which a model does reliably.
    If the right play is not here, that is an ESCALATE and a candidate new member — never an
    improvisation.
    """

    NONE = "none"                          # nothing to do; carry on
    RE_OBSERVE = "re_observe"              # look again before retrying (unexpected.RE_OBSERVE)
    SETTLE_AND_RETRY = "settle_and_retry"  # wait for the page to settle, then repeat the action
    RE_RESOLVE_TAB = "re_resolve_tab"      # re-discover the CDP target and retry once
    COMMIT_WIDGET = "commit_widget"        # run the stage->commit protocol on the staged control
    RESCAN_REQUIRED = "rescan_required"    # re-scan for a required control the form hid from us
    ESCALATE = "escalate"                  # hand up the ladder; never guess, never loop


#: The play each class calls for. A dict rather than a method so the mapping is inspectable, and
#: so promotion (PLAN_supervisor §6) can be gated per class against the play it would fire.
PLAY_FOR_CLASS: dict[str, str] = {
    FailureClass.NONE.value: RecoveryPlay.NONE.value,
    FailureClass.NO_PROGRESS.value: RecoveryPlay.SETTLE_AND_RETRY.value,
    FailureClass.CONTROL_NOT_FOUND.value: RecoveryPlay.RE_OBSERVE.value,
    FailureClass.STAGED_NOT_COMMITTED.value: RecoveryPlay.COMMIT_WIDGET.value,
    FailureClass.RACE_SETTLE.value: RecoveryPlay.SETTLE_AND_RETRY.value,
    FailureClass.STALE_TAB.value: RecoveryPlay.RE_RESOLVE_TAB.value,
    FailureClass.UNRECOGNIZED_STATE.value: RecoveryPlay.ESCALATE.value,
    FailureClass.AUTH_WALL.value: RecoveryPlay.ESCALATE.value,
    FailureClass.MISSED_REQUIRED_CONTROL.value: RecoveryPlay.RESCAN_REQUIRED.value,
    FailureClass.CHALLENGE.value: RecoveryPlay.ESCALATE.value,
    FailureClass.UNKNOWN.value: RecoveryPlay.ESCALATE.value,
}

#: Seed stuck-signal per class. These are PRIORS, not measurements — they order the escalation
#: ladder before we have enough graded verdicts to calibrate them, and §8's falsifiers are what
#: will replace them. A class whose signal never correlates with an actual stop is mis-set.
_STUCK_SIGNAL: dict[str, float] = {
    FailureClass.NONE.value: 0.0,
    FailureClass.CONTROL_NOT_FOUND.value: 0.5,
    FailureClass.STAGED_NOT_COMMITTED.value: 0.55,
    FailureClass.RACE_SETTLE.value: 0.5,
    FailureClass.NO_PROGRESS.value: 0.7,
    FailureClass.MISSED_REQUIRED_CONTROL.value: 0.7,
    FailureClass.UNRECOGNIZED_STATE.value: 0.8,
    FailureClass.STALE_TAB.value: 0.85,
    FailureClass.AUTH_WALL.value: 0.9,
    FailureClass.CHALLENGE.value: 1.0,
    FailureClass.UNKNOWN.value: 0.8,
}

#: Intents that SET a value on a field. Used to tell "the widget staged but didn't commit" apart
#: from "the button didn't do anything" — the two look identical without knowing what we tried.
_FIELD_SETTING_INTENTS = frozenset({
    Intent.SET_TEXT.value, Intent.SELECT_OPTION.value,
    Intent.SET_DATE.value, Intent.CHECK_GROUP.value,
})

#: What a diagnostic_request may ask for. Rung 0 may NEVER ask (see `classify`) — vision is an
#: instrument the reasoner reaches for, not a firehose (PLAN_supervisor §2).
DIAGNOSTIC_NONE = "none"
DIAGNOSTIC_SCREENSHOT = "screenshot"


@dataclass(frozen=True)
class SupervisorVerdict:
    """One post-action diagnosis. Journaled beside the DecisionRecord it judges."""

    #: Where we believe we are, in one sentence.
    state_hypothesis: str
    #: Expected X, observed Y — in one sentence. Empty when nothing was expected.
    expectation_delta: str
    #: [0,1]. How stuck we look, for ordering the escalation ladder.
    stuck_signal: float
    #: A FailureClass value.
    failure_class: str
    #: A RecoveryPlay value — never prose.
    proposed_recovery: str
    #: Semantic refs for the play (e.g. the field to commit). Selector-guarded like Decision.params.
    recovery_params: dict = field(default_factory=dict)
    #: DIAGNOSTIC_NONE | DIAGNOSTIC_SCREENSHOT. Rung 0 always emits NONE.
    diagnostic_request: str = DIAGNOSTIC_NONE
    #: One sentence saying WHY this class (not a restatement of it). Held to `is_real_rationale`.
    rationale: str = ""
    #: The inputs this rationale leans on — the receipts (§10, mirrors Decision.evidence).
    evidence: tuple[str, ...] = ()
    #: [0,1] in the class, not in the recovery.
    confidence: float = 0.0
    #: deterministic | model | teacher
    rung: str = "deterministic"

    @property
    def nominal(self) -> bool:
        """True when nothing went wrong — the common case, and the one that must stay free."""
        return self.failure_class == FailureClass.NONE.value

    def summary(self) -> str:
        """The one-line commentary the operator reads. Legible without a model, by design."""
        if self.nominal:
            return f"ok — {self.state_hypothesis}"
        return (f"{self.failure_class} ({self.stuck_signal:.2f}) — {self.expectation_delta} "
                f"-> {self.proposed_recovery}")


def sanitize_recovery_params(params: Optional[dict]) -> dict:
    """Drop any selector-shaped key or value (invariant #10). The supervisor names a FIELD to
    commit or a CONTROL to re-scan; addressing is the recipe's and the API's business."""
    out: dict = {}
    for k, v in (params or {}).items():
        if looks_like_selector(k) or looks_like_selector(v):
            continue
        out[str(k)] = v
    return out


# --- rung 0: the deterministic supervisor -------------------------------------------
def classify(
    *,
    outcome: Optional[str],
    verified: bool,
    delta: StateDelta,
    intent: Optional[str] = None,
    field_name: Optional[str] = None,
    state: Optional[str] = None,
    human_required: bool = False,
    expected_next: tuple[str, ...] = (),
    landed_state: Optional[str] = None,
    unanswered_count: int = 0,
) -> SupervisorVerdict:
    """`(what we did, what came back, what changed) -> a named verdict`. PURE, $0, every turn.

    Emits a FULL readable verdict even when nothing is wrong — legibility never required a model,
    and that is what makes the commentary pane free. Rung 1 (Haiku) exists only to refine what
    this returns as UNKNOWN, and only rung 1+ may ever request a screenshot.

    Order matters: the stop-states are checked before the mechanical ones so a captcha is never
    filed as "the control wasn't found".
    """
    where = f"on {state}" if state else "on an unrecognised page"

    def _v(cls: FailureClass, expectation: str, why: str, evidence: tuple[str, ...],
           conf: float, params: Optional[dict] = None) -> SupervisorVerdict:
        return SupervisorVerdict(
            state_hypothesis=f"{where}, after {intent or 'an action'}"
                             + (f" on {field_name!r}" if field_name else ""),
            expectation_delta=expectation,
            stuck_signal=_STUCK_SIGNAL[cls.value],
            failure_class=cls.value,
            proposed_recovery=PLAY_FOR_CLASS[cls.value],
            recovery_params=sanitize_recovery_params(params),
            diagnostic_request=DIAGNOSTIC_NONE,     # rung 0 may never ask for vision
            rationale=why,
            evidence=evidence,
            confidence=conf,
            rung="deterministic",
        )

    # --- stop-states first: these must never be laundered into a mechanical class -----
    if state == "captcha" or outcome == Outcome.BLOCKED.value:
        return _v(FailureClass.CHALLENGE,
                  "expected the step to proceed, got a challenge/checkpoint",
                  "a challenge is a hard stop for the human — never auto-solved, never retried",
                  ("state", "outcome"), 1.0)

    if human_required and state is None:
        # observe() marks an untrustworthy reading this way (a failed probe / stale tab).
        return _v(FailureClass.STALE_TAB,
                  "expected to read the page, could not observe the tab at all",
                  "the observation itself failed, so any diagnosis below it would be invented",
                  ("human_required", "state"), 0.9)

    if human_required:
        return _v(FailureClass.AUTH_WALL,
                  "expected to continue the task, got a human-required gate",
                  "a sign-in / credential gate is the operator's, always — the agent never "
                  "authenticates on its own",
                  ("human_required", "state"), 0.9)

    if state is None:
        return _v(FailureClass.UNRECOGNIZED_STATE,
                  "expected a known page state, got one that is not in the registry",
                  "we cannot plan from a page we cannot name; it is recorded as a candidate "
                  "state rather than guessed at",
                  ("state",), 0.85)

    # --- the action landed and the page moved: nominal --------------------------------
    if verified and delta.moved:
        return _v(FailureClass.NONE,
                  f"expected to advance, and the page moved ({delta.churn} controls changed)"
                  if delta.churn else "expected to advance, and the page moved",
                  "the intent verified and the observation changed — the action had an effect",
                  ("outcome", "delta.moved"), 1.0)

    # --- the protocol already named it: use the Outcome, don't re-infer it -------------
    # STALE_STATE_OUTCOMES (not_found / not_opened / ambiguous) plus no_option, which is the same
    # miss one level in: the control resolved, its option VOCABULARY did not. Both mean "the
    # semantic reference did not land", and both recover by re-observing rather than retrying.
    if outcome in _STALE_OUTCOME_VALUES or outcome == Outcome.NO_OPTION.value:
        return _v(FailureClass.CONTROL_NOT_FOUND,
                  f"expected to address {field_name or 'the control'}, the page does not have it",
                  "the semantic reference did not resolve here — the recipe is stale, or we are "
                  "not on the page we think we are",
                  ("outcome",), 0.9,
                  {"field": field_name} if field_name else None)

    # `not_staged` / `not_committed` ARE the stage-vs-commit failure, already discriminated by the
    # tier-2 protocol. Taking the endpoint's word for it beats inferring the same thing from the
    # delta below — and it is the one case where we know WHICH half of the protocol broke.
    if outcome in {Outcome.NOT_STAGED.value, Outcome.NOT_COMMITTED.value}:
        half = "stage" if outcome == Outcome.NOT_STAGED.value else "commit"
        return _v(FailureClass.STAGED_NOT_COMMITTED,
                  f"expected {field_name or 'the widget'} to take the value, the {half} step failed",
                  f"the tier-2 protocol reported the {half} half failing — a composite widget "
                  f"needs its open->stage->commit sequence, not a click",
                  ("outcome",), 0.9,
                  {"field": field_name} if field_name else None)

    # `committed_unconfirmed` is honest uncertainty, not a failure: the commit destroyed the very
    # execution context that would observe it. Rung 0 cannot resolve it — this is exactly the case
    # where rung 1 spending a screenshot earns its keep, so hand it up saying so.
    if outcome == Outcome.COMMITTED_UNCONFIRMED.value:
        return _v(FailureClass.UNKNOWN,
                  "the action fired; this layer cannot see whether it took",
                  "the commit tore down its own observer — confirmation has to come from OUTSIDE "
                  "the page, which rung 0 cannot do",
                  ("outcome",), 0.5)

    # --- it 'succeeded' and nothing changed: the treadmill family ---------------------
    if not delta.moved:
        # A field-setting intent that reported ok and left the unanswered count alone is the
        # stage-vs-commit signature: the widget shows the value, the form does not have it.
        if intent in _FIELD_SETTING_INTENTS and outcome == Outcome.OK.value:
            return _v(FailureClass.STAGED_NOT_COMMITTED,
                      f"expected {field_name or 'the field'} to become answered, it is still "
                      f"unanswered",
                      "a composite widget staged the value without committing it — it needs the "
                      "stage->commit protocol, not a click",
                      ("outcome", "delta.unanswered_delta"), 0.8,
                      {"field": field_name} if field_name else None)
        # A click on a form the scanner calls COMPLETE that advances nothing means something
        # required is on the page that the scan never saw.
        if intent == Intent.CLICK.value and unanswered_count == 0:
            return _v(FailureClass.MISSED_REQUIRED_CONTROL,
                      "expected the step to advance with every required field answered, "
                      "it did not move",
                      "the form scans as complete yet the advance no-ops — a required control "
                      "exists that the scan did not report",
                      ("outcome", "delta.moved", "unanswered"), 0.75)
        return _v(FailureClass.NO_PROGRESS,
                  "expected the page to change, it is byte-identical",
                  "the action reported success and achieved nothing — landing where you expected "
                  "is not the same as getting somewhere",
                  ("outcome", "delta.moved"), 0.85)

    # --- it moved, but not to where we said -------------------------------------------
    if not verified and expected_next:
        return _v(FailureClass.UNKNOWN,
                  f"expected to land on {', '.join(expected_next)}, landed on "
                  f"{landed_state or 'somewhere unrecorded'}",
                  "the page moved but not to a state this step expected; rung 0 has no rule that "
                  "names this, so it is handed up rather than guessed",
                  ("expected_next", "landed_state", "delta.moved"), 0.4)

    return _v(FailureClass.UNKNOWN,
              "expected a recognisable outcome, cannot name what happened",
              f"no rung-0 rule matches outcome={outcome!r} verified={verified} "
              f"moved={delta.moved}",
              ("outcome", "delta.moved"), 0.3)


def verdict_to_prompt(verdict: SupervisorVerdict) -> str:
    """The STABLE serialization of a verdict — what the NEXT decide() sees, and rung 1's input.
    FROZEN FORMAT, same discipline as `bundle_to_prompt` / `delta_to_prompt`."""
    return "\n".join([
        "# LAST STEP (supervisor)",
        f"class: {verdict.failure_class}",
        f"stuck: {verdict.stuck_signal:.2f}",
        f"hypothesis: {verdict.state_hypothesis}",
        f"delta: {verdict.expectation_delta}",
        f"recovery: {verdict.proposed_recovery}",
        f"why: {verdict.rationale}",
    ])
