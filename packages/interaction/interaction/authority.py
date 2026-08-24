"""Authority — WHO decides this turn, and who executes it.

`decide()` answers *"what next?"*. `supervision.classify()` answers *"what just happened?"*. This
module answers the question sitting between them and never asked before: **may the local system
act on this by itself?**

Until now that was answered two ways, both global and neither about the step in front of us:

  * `teach.PROPOSE_RUNGS` — review anything not rung 0. Keyed on WHO proposed, never on whether
    THIS transition has ever been proven.
  * `run_controller` returning `STATUS_ESCALATED` — the drive is simply over.

So the only settings were *ask always* or *never ask*, and an escalation ended the application
rather than borrowing the wheel for one step. The result was the standing complaint: the teacher
does everything, off the record, and the inner layers are never tested on the cases that matter.

The fix is a per-turn, per-transition authority decision over three inputs that are already
computed elsewhere and were never combined:

  maturity  what THIS transition has earned          (controller/maturity.py — journal-derived)
  belief    how unsure we are, per axis              (interaction/belief.py — the two witnesses)
  reach     can the executor operate this page       (controller/reach.py — the AX/widget probe)

`reach` is the input the design nearly went without, and it is the one the operator named:
*"the observer is great until we can't do anything about it."* Knowing precisely where you are is
worth nothing if the local executor cannot touch the page — which is why it outranks belief below.
It is also what separates ORANGE from RED, i.e. **teacher instruction** from **teacher control**:
if the tools reach the page, the teacher supplies the missing MEANING and the local actuator still
acts and verifies; only when they do not does the teacher take the wheel.

Frozen and versioned like `contract.py`, `decision.py`, `belief.py` and `supervision.py`: the mode
is journaled on every row, so it is a training feature and its serialization is part of the
contract, not a convenience.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

#: Bump on any change to a member's MEANING or to `authority_to_prompt`'s FORMAT. Adding a member
#: is backwards-compatible (old rows stay valid); renaming or repurposing one is not — the same
#: discipline as DECISION_SCHEMA_VERSION, the Outcome taxonomy and FailureClass.
AUTHORITY_SCHEMA_VERSION = "v1"


class Maturity(str, Enum):
    """What ONE transition has earned, from a closed ladder.

    The unit is a transition — `(task, from_state, intent, ref) -> expected_state` — and not a
    site, because an ATS is never uniformly built: a Workday application routinely contains ten
    proven sections and one questionnaire nobody has ever seen. Grading the whole vendor forces a
    choice between driving the unknown part blind and hand-holding the known nine.
    """

    #: Never encountered. The default, and deliberately the STRICTEST — absence of evidence is
    #: never permission. Every new tenant, every new phase, starts here.
    UNSEEN = "unseen"

    #: The teacher completed it at least once, on the record.
    DEMONSTRATED = "demonstrated"

    #: There is enough for the local system to attempt it — a compiled `IntentProgram`, or repeat
    #: verified evidence. "Can try", not "can be trusted".
    REPLAYABLE = "replayable"

    #: The local system does it and it works, but not enough times, or not yet unwatched.
    TESTING = "testing"

    #: Consistently completed AND verified. The only rung that acts without asking anyone.
    CERTIFIED = "certified"

    #: It used to work and just stopped. NOT the same as UNSEEN: we hold prior knowledge that is
    #: now suspect, which is a different remedy (inspect the change) from never having been here.
    REGRESSED = "regressed"


#: Weakest → strongest. REGRESSED sits beside DEMONSTRATED on purpose: a transition that broke is
#: worth about what one old demonstration is worth, and certainly not what it was worth yesterday.
MATURITY_ORDER: tuple[str, ...] = (
    Maturity.UNSEEN.value,
    Maturity.REGRESSED.value,
    Maturity.DEMONSTRATED.value,
    Maturity.REPLAYABLE.value,
    Maturity.TESTING.value,
    Maturity.CERTIFIED.value,
)


def maturity_rank(maturity: str) -> int:
    """Position on the ladder; an unrecognised value ranks lowest (fails safe, never open)."""
    try:
        return MATURITY_ORDER.index(maturity)
    except ValueError:
        return 0


class ControlMode(str, Enum):
    """Who decides, and who executes. A CLOSED set of four — the distinction that matters most is
    ORANGE vs RED, and it is a distinction about the EXECUTOR, not about difficulty."""

    #: Inner reasoner decides · local interaction layer executes. Recognised state, certified
    #: transition, tools reach the page. Runs unwatched.
    GREEN = "green"

    #: Local proposes · teacher approves or corrects · local executes. The existing propose-approve
    #: gate (`teach.py`), now selected by what the TRANSITION has earned rather than by which rung
    #: happened to propose.
    YELLOW = "yellow"

    #: Teacher supplies ONE bounded semantic action · **local executes and verifies it**. For a
    #: state we cannot name or answer, on a page our tools can still operate. The teacher
    #: contributes meaning, not keystrokes — which is what keeps the action journaled, replayable
    #: and trainable (PRINCIPLES §8).
    ORANGE = "orange"

    #: Teacher drives, bounded, through the journaled endpoints and `/probe`. Reserved for pages
    #: the local executor genuinely cannot operate, and for genuine novelty. Never a licence for a
    #: bespoke script: if the API cannot work the page, the ticket closes as a CAPABILITY GAP and
    #: the deliverable is a new endpoint (§8 — discovery's output is an endpoint).
    RED = "red"


#: Weakest local authority → strongest. Used to CAP a mode (`min_mode`), never to average two.
MODE_ORDER: tuple[str, ...] = (
    ControlMode.RED.value,
    ControlMode.ORANGE.value,
    ControlMode.YELLOW.value,
    ControlMode.GREEN.value,
)

#: Modes in which the local actuator performs the action. ORANGE is in this set and that is the
#: whole point of it: the teacher decides, the SYSTEM acts, so the step lands in the journal like
#: every other step instead of vanishing into a chat window.
LOCALLY_EXECUTED = frozenset({ControlMode.GREEN.value, ControlMode.YELLOW.value,
                              ControlMode.ORANGE.value})

#: Modes that pause the drive for the teacher. YELLOW pauses too (propose-approve), but it pauses
#: holding a proposal; these two pause holding a QUESTION.
NEEDS_TEACHER = frozenset({ControlMode.ORANGE.value, ControlMode.RED.value})


def weakest(*modes: str) -> str:
    """The least-authority mode among those given. Capping is the only legal way to combine two
    mode opinions — averaging authority is how a system talks itself into acting."""
    return min(modes, key=lambda m: MODE_ORDER.index(m) if m in MODE_ORDER else 0)


@dataclass(frozen=True)
class TransitionKey:
    """The unit maturity is tracked against: **the state you are on, and the action you take**.

    **`task` is deliberately not part of it.** It was, until the registry was first run over the
    real corpus (2026-07-22): the same twelve-success `indeed_apply_questions / click / Continue`
    history was split in two because some rows carry `task="indeed"` and others
    `task="indeed_quick_apply"`. A free-text label drifting across sessions must not be able to
    reset a transition's track record, and it carries no information the state does not — an
    `indeed_apply_questions` page is that page whatever the run was called. Task is kept on the
    stat for reporting.

    `ref` is the SEMANTIC reference the action targeted — a field name or a control's accessible
    name — never a selector (invariant #10). It is part of the key because "click Continue on the
    questions page" and "click Back on the questions page" are different transitions with
    different track records, and collapsing them would let one certify the other.

    **The destination is deliberately NOT part of the key**, though the incoming design's coverage
    table shows it as a column. One action legitimately lands in several places here: Indeed skips
    steps that are already prefilled, which is why `apply_recipe`'s edges are `expect` LISTS rather
    than single successors. Keying on the landing would shatter one transition's history into three
    thin ones and nothing would ever accumulate enough evidence to certify. The landings are
    recorded on the STAT instead, where they are reporting rather than identity.
    """

    from_state: str
    intent: str
    ref: str = ""                 # params.field or params.control; "" when the intent takes none

    def as_str(self) -> str:
        """A stable, greppable id for journals and the coverage table."""
        return f"{self.from_state}|{self.intent}|{self.ref}"

    def as_dict(self) -> dict[str, Any]:
        return {"from_state": self.from_state, "intent": self.intent, "ref": self.ref}


@dataclass(frozen=True)
class ActuationReach:
    """Can the local executor operate this page? Distinct from "do we know where we are".

    `gaps` is the interesting half: each entry names something the executor cannot address here —
    an unresolvable field, a widget type no tier-2 protocol covers, a page with no addressable
    controls at all. It is the SPEC FOR THE NEXT ENDPOINT, which is why it travels with the
    verdict instead of being collapsed into the boolean.
    """

    can_operate: bool
    gaps: tuple[str, ...] = ()
    #: Set when nobody ran the probe. NOT the same as `can_operate=False`: unprobed means unknown,
    #: and unknown caps authority at YELLOW rather than forcing RED (see `authority`).
    probed: bool = True

    @classmethod
    def unprobed(cls) -> "ActuationReach":
        return cls(can_operate=True, gaps=(), probed=False)

    def as_dict(self) -> dict[str, Any]:
        return {"can_operate": self.can_operate, "gaps": list(self.gaps), "probed": self.probed}


@dataclass(frozen=True)
class AuthorityVerdict:
    """The mode plus WHY — journaled beside the DecisionRecord it governs.

    The reason is not decoration. A drive where authority changes hands five times is unreadable
    without it, and "why did it ask me about this step?" is the first question an operator has.
    """

    mode: str
    reason: str
    maturity: str
    blocking_axis: str = ""              # the BeliefState axis that blocked, "" if none
    gaps: tuple[str, ...] = ()           # reach gaps, when reach is what decided
    consequential: bool = False

    @property
    def locally_executed(self) -> bool:
        return self.mode in LOCALLY_EXECUTED

    @property
    def needs_teacher(self) -> bool:
        return self.mode in NEEDS_TEACHER

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": AUTHORITY_SCHEMA_VERSION, "mode": self.mode,
                "reason": self.reason, "maturity": self.maturity,
                "blocking_axis": self.blocking_axis, "gaps": list(self.gaps),
                "consequential": self.consequential}

    def summary(self) -> str:
        """The one line an operator reads in the commentary. Free, like the supervisor's."""
        return f"{self.mode} [{self.maturity}] — {self.reason}"


#: Maturity at or above this acts unwatched. A named constant rather than an inline comparison so
#: the promotion gate (`docs/CONTROLLER_PROMOTION.md`) and the loop cannot drift apart.
GREEN_AT = Maturity.CERTIFIED.value

#: The best mode obtainable without having run the reach probe. Acting unwatched on a page nobody
#: confirmed we can operate is exactly the "successful empty result" family of failure (the stale
#: tab, 2026-07-19), so an unprobed turn may be reviewed but never run free.
UNPROBED_CEILING = ControlMode.YELLOW

#: Ceiling applied when the PROMOTION GATE has not been cleared for this scenario. Same value and
#: the same logic as `UNPROBED_CEILING`: a check that was not performed, or was performed and did
#: not pass, caps the turn at "local proposes, the teacher approves" — it never grants autonomy.
UNPROMOTED_CEILING = ControlMode.YELLOW


@dataclass(frozen=True)
class PromotionStanding:
    """Whether a scenario has cleared the two-bar promotion gate (CONTROLLER_PROMOTION.md).

    Carried as a plain value object so this module stays pure and knows nothing about journals,
    metrics or scenarios — the CALLER measures, this module only applies the consequence.

    `measured=False` is the honest default and it BLOCKS, for the same reason
    `ActuationReach.unprobed()` blocks: absence of evidence is the strictest answer, never
    permission (this module's own header). A gate whose default answer is "yes" is not a gate —
    the same rule `metrics.is_promotable` applies by defaulting `exact_n` to 0.
    """

    measured: bool = False
    eligible: bool = False
    detail: str = ""

    @staticmethod
    def unmeasured() -> "PromotionStanding":
        return PromotionStanding(measured=False, eligible=False,
                                 detail="no agreement measured for this scenario")

    @property
    def permits_autonomy(self) -> bool:
        return self.measured and self.eligible

    def as_dict(self) -> dict[str, Any]:
        return {"measured": self.measured, "eligible": self.eligible, "detail": self.detail}


def _blocking_axis(belief: Any, *, consequential: bool) -> str:
    """Which belief axis should stop us, or "".

    `belief` is accepted as `BeliefState`, as its `as_dict()` form, or as None, because it arrives
    from three places: the observer directly (live), a journaled row (replay/eval), and nothing at
    all (no observer fitted — which is still the default). The dict form is rebuilt through
    `BeliefState.from_dict` rather than re-deriving the ceilings here: two implementations of a
    policy is two policies, and the drive and the eval scoring it must agree exactly.

    None means NOT ASSESSED, and an axis
    nobody assessed does not block: the deterministic `map_url_to_state` reading that produced the
    maturity key is itself the evidence, and it is the same evidence the transition was certified
    under. Treating an unfitted observer as maximal doubt would strand every drive at ORANGE and
    punish the system for a component it has not been given yet.
    """
    if belief is None:
        return ""
    if isinstance(belief, dict):
        from interaction.belief import BeliefState
        belief = BeliefState.from_dict(belief)
    blocks = getattr(belief, "blocks", None)
    if not callable(blocks):
        return ""
    return blocks(consequential=consequential) or ""


def authority(*, maturity: str, belief: Any = None,
              reach: Optional[ActuationReach] = None,
              consequential: bool = False,
              standing: Optional[PromotionStanding] = None) -> AuthorityVerdict:
    """Who owns this turn. PURE, $0, every turn — same discipline as `supervision.classify`.

    Checked in this order, first hit wins. The ORDER is the design:

      1. reach   — cannot operate the page             -> RED
      2. novelty — never been anywhere like this       -> ORANGE  (was RED until 2026-07-22)
      3. maturity UNSEEN                               -> ORANGE
      4. belief  — unsure about state/element/answer   -> ORANGE
      5. maturity below CERTIFIED                      -> YELLOW
      6. otherwise                                     -> GREEN, capped if reach was not probed

    **Reach outranks belief** because a confident label on a page we cannot touch buys nothing, and
    because reach is precisely what distinguishes "the teacher should tell us what this MEANS"
    (ORANGE) from "the teacher has to drive" (RED). **RED is therefore branch 1 and nothing else**:
    it is the CAPABILITY verdict, and every other block is a knowledge gap the teacher can answer
    with meaning rather than keystrokes. **Novelty still outranks the other belief axes** for the
    reason `BeliefState.blocks` already gives — a page we have never seen can still produce a
    confident nearest-neighbour label, and acting on that is the failure the second witness exists
    to prevent — it just does not take the wheel away to say so.

    `consequential` does NOT get its own branch here. It tightens every belief comparison to
    `CONSEQUENTIAL_CEILING` (0.10) by being threaded into `blocks()`, and the irreversible action
    itself is held for the operator by `loop.CONSEQUENTIAL_INTENTS` before this is ever consulted.
    Authority never approves a Submit; it only says how sure we were when we reached one.
    """
    probe = reach if reach is not None else ActuationReach.unprobed()
    mat = maturity or Maturity.UNSEEN.value

    def _v(mode: ControlMode, reason: str, *, axis: str = "",
           gaps: tuple[str, ...] = ()) -> AuthorityVerdict:
        return AuthorityVerdict(mode=mode.value, reason=reason, maturity=mat,
                                blocking_axis=axis, gaps=gaps, consequential=consequential)

    # 1 — the tools do not reach the page. The operator's caveat, as the first branch.
    if probe.probed and not probe.can_operate:
        gap_text = ", ".join(probe.gaps[:3]) or "no addressable controls"
        return _v(ControlMode.RED,
                  f"the local executor cannot operate this page ({gap_text}) — the teacher drives, "
                  f"and the gap is the spec for an endpoint",
                  gaps=probe.gaps)

    axis = _blocking_axis(belief, consequential=consequential)

    # 2 — genuine novelty. Not "which of the known states is this", but "is this known at all".
    #
    # ORANGE, not RED, and the correction is the plan's own principle applied to its own table.
    # `reach.BLOCKING_GAP_PREFIXES` draws the ORANGE/RED line at KNOWLEDGE vs CAPABILITY: a page we
    # cannot name but our tools can work is a knowledge gap, and a bounded teacher instruction
    # routes around it while the LOCAL actuator performs and verifies the step — which is what
    # keeps it journaled. Novelty is the purest knowledge gap there is, and branch 1 has already
    # returned for every page we cannot operate, so anything reaching here is operable by
    # construction.
    #
    # Grading it RED made the drive of 2026-07-22 unrunnable and did so for a reason worth
    # recording: RED accepts only `takeover_done` or `abort`, because `authority_seam.takeover`
    # assumes RED means "the executor is what could not reach the page". So a novelty-RED — with
    # `reach_gaps` EMPTY every single time — had no path back into the loop except the teacher
    # taking the wheel, and the teaching path §2 specifies was unreachable exactly when reach was
    # fine. That is `PLAN_progressive_autonomy` falsifier #4 ("RED fires constantly → too strict,
    # and the teacher is being called for pages the executor could have worked"), observed live.
    #
    # Nothing is loosened by this: ORANGE still means the teacher chooses the action, the belief
    # is still blocked, and the local system still acts on nobody's authority but the teacher's.
    if axis == "novelty":
        return _v(ControlMode.ORANGE,
                  "this page is unlike anywhere we have been — a nearest-neighbour label here "
                  "would be a confident guess, which is the failure we bought a second witness "
                  "to catch; the teacher names the action and the local executor performs it",
                  axis=axis)

    # 3 — no track record. Absence of evidence is the strictest mode, never permission.
    if mat == Maturity.UNSEEN.value:
        return _v(ControlMode.ORANGE,
                  "no record of this transition ever being made — the teacher supplies the next "
                  "action, the local executor performs and verifies it")

    # 4 — we can touch the page, we just do not know enough to choose. Exactly ORANGE's case.
    if axis:
        return _v(ControlMode.ORANGE,
                  f"unsure about {axis} on a page our tools can operate — the teacher supplies the "
                  f"meaning, the local executor acts",
                  axis=axis)

    # 5 — known and workable, but not yet proven enough to run unwatched.
    if maturity_rank(mat) < maturity_rank(GREEN_AT):
        return _v(ControlMode.YELLOW,
                  f"transition is {mat}, not {GREEN_AT} — local proposes, the teacher approves or "
                  f"corrects, local executes")

    # 6 — CERTIFIED BY TRACK RECORD IS NOT ENOUGH. The transition has earned its rung on what it
    # DID; the promotion gate asks the other question — does the local controller AGREE with the
    # teacher on this scenario, on both bars (CONTROLLER_PROMOTION.md, ruled 2026-08-22)?
    #
    # Two different kinds of evidence, deliberately both required: maturity is derived from ACTED
    # rows (`maturity.key_for_row` skips shadow rows), agreement from SHADOW/golden pairs. A
    # transition can have a spotless action history while the controller, asked to choose for
    # itself on that page, still picks differently from the teacher — and it is the second thing,
    # not the first, that autonomy actually depends on.
    #
    # Note the ORDER: this sits AFTER the belief and maturity branches so their reasons still win
    # when they apply, and BEFORE the GREEN return so nothing reaches autonomy around it.
    stand = standing if standing is not None else PromotionStanding.unmeasured()
    if not stand.permits_autonomy:
        return _v(UNPROMOTED_CEILING,
                  f"{mat} by track record, but the promotion gate is not cleared for this "
                  f"scenario ({stand.detail or 'no standing supplied'}) — local proposes, the "
                  f"teacher approves or corrects")

    # 7 — certified AND promoted. The only rung that acts without asking, and only if we checked
    # we can act.
    if not probe.probed:
        return _v(UNPROBED_CEILING,
                  "certified, but nobody probed whether the executor can operate this page — "
                  "reviewed rather than run free")
    return _v(ControlMode.GREEN,
              "certified transition, witnesses content, tools reach the page — running locally")


def authority_to_prompt(verdict: AuthorityVerdict) -> str:
    """The STABLE serialization a reasoner reads and a policy trains on. FROZEN FORMAT, same
    discipline as `bundle_to_prompt` / `belief_to_prompt` / `verdict_to_prompt`."""
    lines = [
        "# AUTHORITY",
        f"mode: {verdict.mode}",
        f"maturity: {verdict.maturity}",
        f"why: {verdict.reason}",
    ]
    if verdict.blocking_axis:
        lines.append(f"blocked_on: {verdict.blocking_axis}")
    if verdict.gaps:
        lines.append("gaps: " + ", ".join(verdict.gaps[:5]))
    if verdict.consequential:
        lines.append("consequential: yes (strict ceiling applied)")
    return "\n".join(lines)
