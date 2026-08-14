"""A refusal that carries the way out — because one that does not is still a dead end.

    The system knew exactly what was wrong and said so, and the seat had no button.

WHY THIS EXISTS. On 2026-08-13 three correct backend refusals reached an operator who could act on
none of them, and the session's own summary named the pattern: *a state the system can enter must
have a way out that the operator can press.* Three instances were fixed by hand. **Four more
appeared on 08-14** — the parked strip hid the row the focus was not showing; a terminal flag could
only be pressed on the CURRENT step, so skipping a queued duplicate needed curl; an open step whose
tab had been closed still offered "Continue"; an ambiguous resolve had no way to say "I mean this
one".

That is not a bug that keeps getting fixed. It is a class that keeps REGENERATING, and the reason
is structural: **a refusal is a string, its exit is hand-built somewhere else, and nothing binds
them.** Every new refusal starts life without one, and whether it ever gets an affordance depends
on somebody noticing.

--------------------------------------------------------------------------------------
The one rule
--------------------------------------------------------------------------------------
You cannot construct a `Refusal` without either naming a pressable exit or saying why there is
none. The constructor raises. That is the same violence `Reading.__bool__` uses, applied to the
other half of the same problem: `measured` stops the system claiming what it did not look at,
`refusal` stops it declining without saying what to do instead.

    Refusal(what="…", why="…", exit=Exit(label="Reopen it", endpoint="/apply_reopen", body={…}))
    Refusal(what="…", why="…", no_exit_because="the credential is the operator's to type; "
                                               "nothing here may hold it (PRINCIPLES §4)")

`no_exit_because` is a real answer and deliberately allowed — a captcha, a credential, a federal
self-identification are all cases where the honest move is to hand over and there IS no button we
may offer. What it is not is a default: it must SAY who acts instead, it is greppable, and its
population is countable, so "we quietly stopped offering exits" cannot happen without showing up
in a diff.

Prose still works everywhere a string was returned before (`str(refusal)`), so migrating a call
site is additive: the sentence the operator already read is unchanged, and the button is new.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: The shortest `no_exit_because` that can actually be read as a reason. Below this it is a
#: shrug, and a shrug is how the field turns into the default it exists not to be.
_MIN_REASON = 24


@dataclass(frozen=True)
class Exit:
    """The pressable way out. Shaped like every other cockpit action so the surface can render it
    without knowing which refusal produced it — `label` is what the button says, `endpoint` is
    relative to the session (`/apply_reopen`), `body` is what it posts."""

    label: str
    endpoint: str
    body: dict[str, Any] = field(default_factory=dict)
    #: What pressing it DOES, in the operator's terms. Rides as the button's title, and it is the
    #: half that stops a truthful button from being a mysterious one.
    why: str = ""
    #: Set when the exit is irreversible (sends an application, spends a query). The cockpit
    #: styles it apart; carried rather than re-derived, because a UI that decided this for itself
    #: by matching on a label would send an application the day somebody reworded a button.
    consequential: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "endpoint": self.endpoint, "body": dict(self.body),
                "why": self.why, "consequential": self.consequential}


@dataclass(frozen=True)
class Refusal:
    """Something we declined to do, why, and what can be done about it.

    `evidence` carries the structured facts the refusal was made FROM — the form census, the
    candidate list, the tab that was missing — so the surface can render the WORK rather than
    prose pointing at it. That was the 2026-08-10 audit's core finding and it is why refusals
    already ship `form_scan`; this just gives it a name and a home.
    """

    what: str
    why: str
    exit: Optional[Exit] = None
    no_exit_because: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (self.what or "").strip():
            raise ValueError("a refusal must say WHAT was refused")
        if not (self.why or "").strip():
            raise ValueError("a refusal must say WHY — a bare 'no' is the dead end this type "
                             "exists to prevent")
        reason = (self.no_exit_because or "").strip()
        if self.exit is not None and reason:
            raise ValueError("a refusal has an exit OR a reason there is none, never both — "
                             "which one is it?")
        if self.exit is None and not reason:
            raise ValueError(
                f"{self.what!r} refuses with no way out. Name the exit "
                "(`exit=Exit(label=…, endpoint=…)`) or say why there cannot be one "
                "(`no_exit_because=…`, naming who acts instead). A truthful refusal the "
                "operator cannot act on is still a dead end.")
        if self.exit is None and len(reason) < _MIN_REASON:
            raise ValueError(
                f"`no_exit_because` must be a reason somebody can read, naming who acts instead "
                f"— got {reason!r}. This field is the honest answer for a captcha or a "
                f"credential; it is not a way to skip the question.")

    # --- rendering ---------------------------------------------------------------------------
    def __str__(self) -> str:
        """The prose the operator reads. Every migrated call site returned a sentence before, and
        keeps returning the same sentence — the exit is additive."""
        def _sentence(text: str) -> str:
            text = (text or "").strip()
            # Callers write their own punctuation and most already end in a full stop; appending
            # one unconditionally is how "…profile knows.." reaches an operator.
            return text if not text or text[-1] in ".!?:" else f"{text}."
        parts = [_sentence(self.what), _sentence(self.why)]
        if self.exit is not None:
            parts.append(_sentence(self.exit.why or self.exit.label))
        return " ".join(p for p in parts if p).replace("  ", " ").strip()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"what": self.what, "why": self.why, "detail": str(self)}
        if self.exit is not None:
            out["exit"] = self.exit.as_dict()
        else:
            out["no_exit_because"] = self.no_exit_because
        if self.evidence:
            out["evidence"] = self.evidence
        return out

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Refusal"]:
        """Parse one back, or None if the payload is not a refusal envelope. Never guesses: an
        unmigrated caller has not told us its exit, and inventing one would be the same defect
        as inventing a measurement."""
        if isinstance(data, Refusal):
            return data
        if not isinstance(data, dict) or not data.get("what") or not data.get("why"):
            return None
        ex = data.get("exit")
        exit_ = Exit(label=str(ex.get("label") or ""), endpoint=str(ex.get("endpoint") or ""),
                     body=dict(ex.get("body") or {}), why=str(ex.get("why") or ""),
                     consequential=bool(ex.get("consequential"))) if isinstance(ex, dict) else None
        return cls(what=str(data["what"]), why=str(data["why"]), exit=exit_,
                   no_exit_because=("" if exit_ else str(data.get("no_exit_because")
                                                         or "recorded without a reason")),
                   evidence=dict(data.get("evidence") or {}))


def handed_over(what: str, why: str, *, to: str, evidence: Optional[dict] = None) -> Refusal:
    """The refusal with no button, said properly: WHO acts instead is the whole content.

    For the boundaries this system does not cross — a captcha, a credential, a 2FA code, a
    protected-class self-identification. Never auto-solved, never guessed, and the handover is the
    correct outcome rather than a missing feature.
    """
    return Refusal(what=what, why=why, evidence=evidence or {},
                   no_exit_because=f"this is {to}'s to do, and nothing here may do it for them")
