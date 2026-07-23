"""Wiring — the live `AuthorityFn` and the live `TeacherSeat`.

`run_controller` is pure control-flow by design: it performs no IO, which is what lets the whole
four-mode park/resume path be exercised offline with a fake actuator in microseconds. So the parts
that DO touch the world — the maturity registry (reads the journal), the reach probe (reads the
recipe table), the inbox (reads and writes a file) — are assembled here and injected.

Everything in this module is composition. If you find yourself putting a RULE here, it belongs in
`interaction/authority.py` (pure), `controller/maturity.py` (pure over rows) or
`controller/reach.py` (pure over a bundle) — a rule that lives in the wiring is a rule the offline
suite cannot reach.
"""

from __future__ import annotations

from typing import Optional

from interaction.authority import ActuationReach, AuthorityVerdict, authority
from interaction.decision import Bundle, Decision

from controller import inbox as inbox_mod
from controller import maturity as maturity_mod
from controller import reach as reach_mod
from controller.loop import CONSEQUENTIAL_INTENTS, TakeoverResult


def default_authority(*, registry: Optional[maturity_mod.MaturityRegistry] = None):
    """The live `(bundle, decision) -> AuthorityVerdict`.

    Note where `consequential` comes from: `loop.CONSEQUENTIAL_INTENTS`, the SAME set the loop
    holds for the operator. One definition, threaded to the one place that tightens every belief
    ceiling — an observer applying the strict bar while authority does not (or the reverse) is a
    silent inconsistency at precisely the irreversible step.
    """
    reg = registry or maturity_mod.registry()

    def _authority(bundle: Bundle, decision: Decision) -> AuthorityVerdict:
        consequential = decision.intent in CONSEQUENTIAL_INTENTS
        key = maturity_mod.key_for(bundle.state or "", decision.intent, decision.params)
        rung = reg.get(key) if bundle.state else maturity_mod.Maturity.UNSEEN.value
        try:
            probe = reach_mod.probe(bundle, decision)
        except Exception:  # noqa: BLE001
            # A probe that raises must not decide the turn by accident. "Unprobed" is an honest
            # answer with a defined consequence (capped at YELLOW), unlike a swallowed exception.
            probe = ActuationReach.unprobed()
        return authority(maturity=rung, belief=bundle.belief, reach=probe,
                         consequential=consequential)

    return _authority


class InboxSeat:
    """The live `TeacherSeat`, backed by the file inbox.

    Both methods post the FULL escalation package — the frozen prompts plus the local system's own
    prediction — so the teacher answers from what the policy saw, and so the row that comes back
    is scoreable against what the local layers proposed.
    """

    def __init__(self, *, session_id: str = "", timeout: float = inbox_mod.DEFAULT_PARK_SECONDS,
                 poll: float = inbox_mod.POLL_SECONDS, on_park=None, on_answer=None) -> None:
        self.session_id = session_id
        self.timeout = timeout
        self.poll = poll
        self.on_park = on_park
        self.on_answer = on_answer

    # -- the package ---------------------------------------------------------------
    @staticmethod
    def _package(bundle: Bundle, decision: Decision,
                 verdict: Optional[AuthorityVerdict]) -> dict:
        from interaction.authority import authority_to_prompt
        from interaction.belief import BeliefState, belief_to_prompt
        from interaction.decision import bundle_to_prompt

        belief_prompt = ""
        if bundle.belief:
            try:
                belief_prompt = belief_to_prompt(BeliefState.from_dict(bundle.belief))
            except Exception:  # noqa: BLE001 — a rendering failure must not block the ask
                belief_prompt = ""
        return {
            "session_id": "", "task": bundle.task, "state": bundle.state, "url": bundle.url,
            "mode": verdict.mode if verdict else "",
            "maturity": verdict.maturity if verdict else "",
            "authority_reason": verdict.reason if verdict else "",
            "bundle_prompt": bundle_to_prompt(bundle),
            "belief_prompt": belief_prompt,
            "authority_prompt": authority_to_prompt(verdict) if verdict else "",
            # The local prediction, ALWAYS. §4 of the design: the inner layers must be scored on
            # the turns the teacher is paid for, or they are never scored where it counts.
            "prediction": {
                "intent": decision.intent, "params": dict(decision.params or {}),
                "confidence": decision.confidence, "rung": decision.rung,
                "rationale": decision.rationale,
                "escalation_axis": decision.escalation_axis,
                "expected_next": list(decision.expected_next),
                "evidence": list(decision.evidence),
            },
            "reach_gaps": list(verdict.gaps) if verdict else [],
        }

    # -- ORANGE --------------------------------------------------------------------
    def instruct(self, bundle: Bundle, decision: Decision,
                 verdict: Optional[AuthorityVerdict]) -> Optional[Decision]:
        package = self._package(bundle, decision, verdict)
        package["session_id"] = self.session_id
        request, response = inbox_mod.ask_and_wait(
            kind=inbox_mod.RequestKind.INSTRUCT.value, timeout=self.timeout, poll=self.poll,
            **package)
        if self.on_park:
            self.on_park(request)
        if self.on_answer:
            self.on_answer(request, response)
        if not response:
            return None
        if response.get("action") != inbox_mod.ResponseAction.INSTRUCT.value:
            # `escalate` and anything else mean "not mine either" — hand further up rather than
            # inventing an action out of a refusal.
            return None
        return inbox_mod.decision_from_response(response)

    # -- RED -----------------------------------------------------------------------
    def takeover(self, bundle: Bundle, decision: Decision,
                 verdict: Optional[AuthorityVerdict]) -> TakeoverResult:
        package = self._package(bundle, decision, verdict)
        package["session_id"] = self.session_id
        request, response = inbox_mod.ask_and_wait(
            kind=inbox_mod.RequestKind.TAKEOVER.value, timeout=self.timeout, poll=self.poll,
            **package)
        if self.on_park:
            self.on_park(request)
        if self.on_answer:
            self.on_answer(request, response)
        if not response:
            return TakeoverResult(resumed=False, timed_out=True,
                                  detail="nobody answered the takeover ticket")
        action = response.get("action")
        if action == inbox_mod.ResponseAction.ABORT.value:
            return TakeoverResult(resumed=False, aborted=True,
                                  detail=response.get("rationale", "") or "teacher aborted")
        if action == inbox_mod.ResponseAction.TAKEOVER_DONE.value:
            return TakeoverResult(resumed=True,
                                  detail=response.get("rationale", "") or "checkpoint reached",
                                  new_tab_id=response.get("new_tab_id") or None)
        # Any other answer to a takeover ticket (escalate, or an instruction we cannot execute
        # because the executor is what could not reach the page) ends the detour honestly.
        return TakeoverResult(resumed=False, aborted=True,
                              detail=f"takeover answered {action!r} — not a resumption")
