"""The play executor — `RecoveryPlay` -> an actual action. PLAN_supervisor S12b.

S12 gave the supervisor a diagnosis and a prescription and nothing that could fill it: every
`RecoveryPlay` was a label. This is the pharmacy.

Three disciplines, each of which is the whole point rather than a detail:

  * **Nothing runs until its CLASS has graduated.** `apply_play` takes an explicit
    `enabled_classes`, and the default is EMPTY — stage 1 is shadow (PLAN_supervisor §6), so the
    executor exists and does nothing until an operator names a class that has earned it. Promotion
    is per class, never global, and `UNKNOWN` never graduates.
  * **One attempt, ever.** The caller owns a one-shot latch. "Try harder" is precisely the
    behaviour the stale taxonomy exists to prevent (`unexpected.py`), and a recovery that loops is
    a treadmill with better manners.
  * **The stop-states are not recoverable and never will be.** A challenge, an auth wall, a page
    we cannot name — these hand to the human by definition, so they are refused here even if an
    operator enables them by mistake. That refusal is `_NEVER_RECOVER`, not a comment.

After a successful recovery the loop RE-OBSERVES and RE-DECIDES rather than re-firing the decision
that failed. The old decision was made against a page that has since changed (that is why we
recovered), so replaying it is exactly the stale-state mistake one level up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from interaction.decision import Decision
from interaction.supervision import FailureClass, RecoveryPlay, SupervisorVerdict

#: Classes whose remedy is a human, always. Enabling one of these is a configuration mistake, and
#: this frozenset is what makes it harmless rather than dangerous.
_NEVER_RECOVER = frozenset({
    FailureClass.CHALLENGE.value,        # never auto-solve a captcha. Not once, not ever.
    FailureClass.AUTH_WALL.value,        # the agent never authenticates on its own
    FailureClass.UNRECOGNIZED_STATE.value,   # we cannot act on a page we cannot name
    FailureClass.UNKNOWN.value,          # by construction: an unnamed failure has no known remedy
})

#: Plays the LOOP already performs through `unexpected.respond`. They need no executor and must
#: not get a second implementation here — two places deciding "re-observe or escalate" is the
#: exact duplication `unexpected.py` was extracted to end.
_LOOP_OWNED = frozenset({RecoveryPlay.NONE.value, RecoveryPlay.RE_OBSERVE.value,
                         RecoveryPlay.ESCALATE.value})

#: Classes an operator may enable. Populated by the promotion gate (≥20 instances of the class at
#: ≥70% agreement, PLAN_supervisor §6) — or by the operator naming one directly.
#:
#: PLATFORM_ERROR is the first graduate (operator-approved 2026-08-20, the audit build-out). The
#: argument, since promotion must carry one: the incident count clears the bar before the class
#: existed (`workday_error_retry` is 36 of 356 corpus rows — the 4th most common state — and every
#: one burned a human escalation), the remedy is deterministic (settle, re-observe, re-decide:
#: `apply_play` never re-fires the failed decision), and the play TOUCHES NOTHING on the page —
#: strictly safer than the escalation it replaces, which parked a drive on a page whose entire
#: content is "try again". UNKNOWN and the stop-states remain unpromotable by construction.
AUTONOMOUS_CLASSES: frozenset[str] = frozenset({FailureClass.PLATFORM_ERROR.value})


class RecoveryActuator(Protocol):
    """The IO half, injected so the policy above stays pure and offline-testable — the same seam
    shape as `Actuator`. Every method is best-effort and must never raise into the loop."""

    def settle(self) -> None:
        """Wait for the page to stop moving. The cheapest possible remedy, and the right one for
        a race (the location combobox, the classify-before-navigation-settles bug — 07-18)."""

    def re_resolve_tab(self) -> bool:
        """Re-discover the live CDP target. True if a fresh target was found and adopted."""

    def rescan_required(self) -> tuple[dict, ...]:
        """Look for required controls the ordinary scan missed, with a DIFFERENT instrument.
        Returns the controls found that `/scan_required` did not report (possibly empty)."""

    def commit_widget(self, field: str, value: str) -> bool:
        """Run the open->stage->commit protocol on a composite widget. True if it committed."""


@dataclass(frozen=True)
class PlayResult:
    """What a recovery attempt achieved. `retry=True` means the loop should carry on (re-observe
    and re-decide); False means fall through to the escalation path it would have taken anyway."""

    play: str
    attempted: bool
    retry: bool
    detail: str
    #: Anything the play learned that the operator or the journal should see — e.g. the required
    #: controls a deeper scan turned up. Semantic only; no selectors.
    found: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def skipped(self) -> bool:
        return not self.attempted


def _skip(play: str, why: str) -> PlayResult:
    return PlayResult(play=play, attempted=False, retry=False, detail=why)


def apply_play(
    verdict: SupervisorVerdict,
    decision: Decision,
    actuator: Optional[RecoveryActuator],
    *,
    enabled_classes: frozenset[str] = AUTONOMOUS_CLASSES,
    already_recovered: bool = False,
) -> PlayResult:
    """Run the verdict's prescribed play, if it is allowed to run. Never raises.

    Returns a PlayResult whose `attempted` is False whenever the play was refused — and the
    `detail` always says WHY, because a silently-skipped recovery is indistinguishable from a
    recovery that ran and did nothing, which is the class of ambiguity this whole plan exists to
    remove.
    """
    play = verdict.proposed_recovery
    cls = verdict.failure_class

    if play in _LOOP_OWNED:
        return _skip(play, "the loop's own policy owns this play (unexpected.respond)")
    if cls in _NEVER_RECOVER:
        return _skip(play, f"{cls} hands to the human by definition — never auto-recovered")
    if cls not in enabled_classes:
        return _skip(play, f"{cls} has not graduated (shadow mode) — diagnosis only")
    if already_recovered:
        return _skip(play, "one recovery attempt per step; a second would be a treadmill")
    if actuator is None:
        return _skip(play, "no recovery actuator wired")

    try:
        return _dispatch(play, verdict, decision, actuator)
    except Exception as exc:  # noqa: BLE001 — a failed recovery is a handoff, never a crash
        return PlayResult(play=play, attempted=True, retry=False,
                          detail=f"recovery raised: {type(exc).__name__}: {exc}")


def _dispatch(play: str, verdict: SupervisorVerdict, decision: Decision,
              actuator: RecoveryActuator) -> PlayResult:
    if play == RecoveryPlay.SETTLE_AND_RETRY.value:
        actuator.settle()
        return PlayResult(play=play, attempted=True, retry=True,
                          detail="waited for the page to settle; re-observing before deciding again")

    if play == RecoveryPlay.RE_RESOLVE_TAB.value:
        found = actuator.re_resolve_tab()
        return PlayResult(play=play, attempted=True, retry=found,
                          detail="re-resolved the live tab" if found else
                                 "the tab is gone and could not be re-found")

    if play == RecoveryPlay.RESCAN_REQUIRED.value:
        missed = tuple(actuator.rescan_required())
        # No news is NOT good news here: the form still scans complete and the advance still
        # no-ops, so we have learned nothing and must hand up rather than click again.
        return PlayResult(play=play, attempted=True, retry=bool(missed), found=missed,
                          detail=f"found {len(missed)} required control(s) the scan missed"
                                 if missed else
                                 "a deeper scan found nothing the ordinary scan missed")

    if play == RecoveryPlay.COMMIT_WIDGET.value:
        params = {**(decision.params or {}), **(verdict.recovery_params or {})}
        fieldname, value = params.get("field"), params.get("value")
        if not fieldname:
            return _skip(play, "no field to commit — the verdict named none and neither did the "
                               "decision")
        committed = actuator.commit_widget(str(fieldname), str(value or ""))
        return PlayResult(play=play, attempted=True, retry=committed,
                          detail=f"ran the stage->commit protocol on {fieldname!r}"
                                 if committed else
                                 f"the commit half still failed on {fieldname!r}")

    return _skip(play, f"no executor for play {play!r} — it is a name, not an action yet")
