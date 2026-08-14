"""How much did we actually see? — the type that makes "I did not look" unrepresentable.

    A reader's SCOPE is not the caller's QUESTION, and every silent bug in this system
    so far has been that gap going unrecorded.

WHY THIS EXISTS. On 2026-08-14 one live application produced six defects, and they were one
defect wearing six masks — a value in the domain of the answer where the truth was "I could not
look" or "I looked at part of it":

  * `options: [24 strings]` for a ~250-entry Country select, capped with no tell. The fill planner
    held the right answer, matched it against the list it was given, found nothing, and reported
    Country unanswered on a form one screen from Submit.
  * `valid: true` as the DEFAULT on fields nobody had validated — over a page printing, in red,
    that one of them was too long.
  * `_apply_tab(bb, obs)` answers "the SESSION's apply tab"; the caller read it as "THIS STEP's"
    and closed a live application's tab.
  * `steps_to_submit: 1` over a tab that had already been closed.

And the same shape is all through the log before that:

  * `/auth_state` → `logged_in: false, has_sign_in: false, has_account: false` on a signed-in
    session. It found no evidence and reported the absence as a negative (2026-08-13).
  * `/challenge_visibility` → `hcaptcha_count: 0, blocking: false, solved: true` over a form
    carrying two live challenges it could not see into. *A rail that reports "clear" when it
    cannot see is worse than no rail* (2026-08-13).
  * The census → `ok: true, unanswered: 0` with `url: ''`, because it can only enter documents it
    can reach (2026-08-12).
  * `files.length == 0`, which describes BOTH "nothing staged" and "the widget ingested it and
    reset the input" (2026-08-12).

The lesson is written into `docs/LEARNINGS.md` at least five times — "an address is a prediction",
"a probe that found nothing has not found no", "a scan that did not run is not a clean form", "a
capped list that does not say it is capped reads as the whole list". **It is recorded and not
enforced**, so it is re-learned at each new call site at a cost of roughly one live application
per lesson. This module is the enforcement point PRINCIPLES asks every invariant to have.

--------------------------------------------------------------------------------------
The one rule
--------------------------------------------------------------------------------------
A `Reading` is a value that knows whether it was taken, and how much of the thing it saw:

    Reading.measured(True, how="/auth_state saw the signed-in avatar")
    Reading.unmeasured("the frame is cross-origin; no document to read")
    Reading.partial(opts, shown=24, total=250, how="census option cap")

`bool(reading)` RAISES. That is the whole mechanism, and it is deliberately violent: `if
reading:` and `not reading` are exactly the two lines that turned every incident above into a
silent wrong answer, so they must not compile into working code. To get an answer you have to say
which question you are asking — `is_true()`, `is_false()`, `is_unmeasured()`, `value_or(default)`
— and each of those forces you to have thought about the third case.

`contains()` returns another `Reading`, not a `bool`, and that is the Country bug as a reusable
primitive: asking a PARTIAL list whether it holds "United States" answers UNMEASURED when the
needle is not among the part we saw, because absence from a sample is not absence.

Dependency-free and JSON-round-trippable (`as_dict` / `from_dict`), because these readings cross
the mcp ↔ controlplane process boundary as JSON, which is precisely where the type would otherwise
be erased back into a bare value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# --- the three states a reading can be in -----------------------------------------------------
#: We looked, and this is what is there.
MEASURED = "measured"
#: We could not look. NOT the same as looking and finding nothing — that is `measured(False)` /
#: `measured([])`, and conflating the two is the entire bug class this module exists to kill.
UNMEASURED = "unmeasured"
#: We looked at PART of it. Anything the sample does not settle is unmeasured, not negative.
PARTIAL = "partial"

STATES = (MEASURED, UNMEASURED, PARTIAL)


class Unmeasured(Exception):
    """Raised by `require()` — a caller demanded an answer we never took.

    Deliberately an exception rather than a returned default: `require` is for the paths where
    proceeding without the fact is the bug, and a default there is how "we could not look" gets
    laundered into "it is fine".
    """


@dataclass(frozen=True)
class Reading:
    """A value that carries how much of it we actually saw.

    Construct through the classmethods, never the fields — `Reading(MEASURED, ...)` positionally
    is one typo away from claiming a measurement nobody took.
    """

    status: str
    #: Private on purpose: reaching for `.value` on an unmeasured reading is the mistake. Go
    #: through `value_or` / `require`, both of which make you say what happens when we did not look.
    _value: Any = None
    #: WHAT WAS ACTUALLY LOOKED AT. Provenance travels with the fact (PRINCIPLES §1) — "measured"
    #: with no account of the measurement is a claim, not a reading.
    how: str = ""
    #: Why we could not look. UNMEASURED only, and required: an unexplained blank is the same
    #: dead end as a refusal with no reason.
    why: str = ""
    #: PARTIAL only — how many of how many.
    shown: Optional[int] = None
    total: Optional[int] = None
    #: Names of things this reading did NOT cover, for readers that can enumerate their own gaps.
    #: `staleness` invented this convention independently and it was the one honest thing on the
    #: panel the night the Workday session had been signed out (2026-08-12): it said "fresh" while
    #: its own `unmeasured: [page_age_s, cookie_ttl_s]` told the truth about what it had not asked.
    gaps: tuple[str, ...] = field(default_factory=tuple)

    # --- constructors -------------------------------------------------------------------------
    @classmethod
    def measured(cls, value: Any, *, how: str = "", gaps: tuple[str, ...] = ()) -> "Reading":
        """We looked and this is the answer. `how` should name the witness, not the conclusion."""
        return cls(MEASURED, value, how=how, gaps=tuple(gaps))

    @classmethod
    def unmeasured(cls, why: str) -> "Reading":
        """We could not look, and here is why. There is no value — asking for one raises."""
        return cls(UNMEASURED, None, why=why or "no reason given")

    @classmethod
    def partial(cls, value: Any, *, shown: int, total: int, how: str = "") -> "Reading":
        """We saw `shown` of `total`. Collapses to MEASURED when the sample IS the whole thing,
        so a caller never has to special-case "partial but actually complete"."""
        if total is not None and shown is not None and shown >= total:
            return cls.measured(value, how=how)
        return cls(PARTIAL, value, how=how, shown=shown, total=total)

    # --- the enforcement ----------------------------------------------------------------------
    def __bool__(self) -> bool:
        raise TypeError(
            "A Reading has three states and `bool()` only has two — this is the exact line that "
            "turns 'we could not look' into 'no'. Ask the question you mean: is_true(), "
            "is_false(), is_unmeasured(), is_complete(), or value_or(default). "
            f"(this reading: {self!r})")

    # --- queries ------------------------------------------------------------------------------
    def is_unmeasured(self) -> bool:
        return self.status == UNMEASURED

    def is_partial(self) -> bool:
        return self.status == PARTIAL

    def is_complete(self) -> bool:
        """Did we see the WHOLE thing? A partial reading is not a complete one, and the callers
        that gate on completeness (the census, the option matcher) are the ones that got bitten."""
        return self.status == MEASURED

    def is_true(self) -> bool:
        """True only when we looked AND the answer was yes. Unmeasured is never true."""
        return self.status != UNMEASURED and self._value is True

    def is_false(self) -> bool:
        """False only when we LOOKED and the answer was no.

        This is the asymmetry that matters and the reason both accessors exist: `not is_true()`
        silently folds "could not look" into "no", which is what `/auth_state` did on a signed-in
        session and what `/challenge_visibility` did over two live challenges.
        """
        return self.status != UNMEASURED and self._value is False

    def value_or(self, default: Any) -> Any:
        """The value, or `default` when we could not look. The default is the caller stating, at
        the call site, what an absent measurement should mean HERE — which is a decision, and
        belongs where it is made rather than in the reader."""
        return default if self.status == UNMEASURED else self._value

    def require(self, question: str = "") -> Any:
        """The value, or raise. For paths where proceeding without the fact is the defect."""
        if self.status == UNMEASURED:
            raise Unmeasured(
                f"{question or 'this'} was never measured: {self.why}")
        return self._value

    def contains(self, needle: Any, *, eq: Optional[Callable[[Any, Any], bool]] = None) -> "Reading":
        """Does this collection hold `needle`? Answers with a READING, not a bool.

        THE COUNTRY BUG AS A PRIMITIVE. A `PARTIAL` list that does not hold the needle has not
        established that the needle is absent — it has established that the needle is not in the
        part we saw. Answering `False` there is how a stored, correct "United States" became "not
        an option" against 24 of ~250 countries. Present is always decisive; absent is only
        decisive when the reading is complete.
        """
        if self.status == UNMEASURED:
            return Reading.unmeasured(f"cannot say whether {needle!r} is present: {self.why}")
        items = self._value or ()
        match = eq or (lambda a, b: a == b)
        if any(match(item, needle) for item in items):
            return Reading.measured(
                True, how=f"{needle!r} found among {len(items)} read"
                          + (f" via {self.how}" if self.how else ""))
        if self.status == PARTIAL:
            return Reading.unmeasured(
                f"{needle!r} is not among the {self.shown} of {self.total} we read"
                + (f" ({self.how})" if self.how else "")
                + " — absence from a sample is not absence")
        return Reading.measured(
            False, how=f"{needle!r} absent from all {len(items)}"
                       + (f" via {self.how}" if self.how else ""))

    def describe(self) -> str:
        """One line an operator can read. Surfaces are expected to render this rather than invent
        their own wording — a reading that is explained differently in three panels is three
        different facts to the person reading them."""
        if self.status == UNMEASURED:
            return f"not measured — {self.why}"
        if self.status == PARTIAL:
            return (f"partial — {self.shown} of {self.total} read"
                    + (f" ({self.how})" if self.how else ""))
        return (f"measured{f' — {self.how}' if self.how else ''}"
                + (f"; did not cover {', '.join(self.gaps)}" if self.gaps else ""))

    # --- the JSON boundary --------------------------------------------------------------------
    # These readings cross mcp <-> controlplane as JSON, which is exactly where a type gets erased
    # back into the bare value that caused the incident. Round-tripping is not a convenience here;
    # it is the difference between the guarantee holding across one process and across the system.
    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status, "value": self._value}
        for key in ("how", "why"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.status == PARTIAL:
            out["shown"], out["total"] = self.shown, self.total
        if self.gaps:
            out["gaps"] = list(self.gaps)
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "Reading":
        """Parse a reading back. A payload that is NOT a reading envelope becomes UNMEASURED
        rather than a measured guess — an old endpoint that has not been migrated has, by
        definition, not told us what it looked at, and pretending otherwise would re-create the
        bug at the boundary."""
        if isinstance(data, Reading):
            return data
        if not isinstance(data, dict) or data.get("status") not in STATES:
            return cls.unmeasured("payload carries no reading envelope (unmigrated reader?)")
        status = data["status"]
        if status == UNMEASURED:
            return cls.unmeasured(str(data.get("why") or "no reason recorded"))
        gaps = tuple(str(g) for g in (data.get("gaps") or ()))
        if status == PARTIAL:
            return cls(PARTIAL, data.get("value"), how=str(data.get("how") or ""),
                       shown=data.get("shown"), total=data.get("total"), gaps=gaps)
        return cls.measured(data.get("value"), how=str(data.get("how") or ""), gaps=gaps)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"Reading({self.describe()}; value={self._value!r})"


# --- helpers for the shapes that keep recurring -----------------------------------------------
def from_capped_list(items: list[Any], *, total: Optional[int], how: str = "") -> Reading:
    """A list some reader capped. `total` None means the reader did not say — which is itself
    unmeasured, not a licence to assume the list is whole.

    Written for the census option cap, and it fits every "top N" in the system: the results page,
    the AX candidate list, the events tail. Anywhere a payload silently truncates, a consumer is
    one `in` away from the Country bug.
    """
    if total is None:
        return Reading.unmeasured(
            f"a list of {len(items)} arrived with no total"
            + (f" ({how})" if how else "")
            + " — cannot tell a whole list from a capped one")
    return Reading.partial(items, shown=len(items), total=int(total), how=how)


# --- combining readings, and the asymmetry that is the whole reason both exist -----------------
#
# CHOOSING THE WRONG COMBINATOR IS THE BUG CLASS, NOT A STYLE QUESTION, so they are named for the
# question they answer rather than for their truth table. In three-valued logic a definite answer
# is only decisive in ONE direction per operator, and which direction that is depends entirely on
# what you are asking:
#
#   "is EVERYTHING clear?"   -> all_measured.  One definite NO settles it; one gap does not let
#                               you say YES. (You cannot certify a form complete over a section
#                               you could not read.)
#   "is ANYTHING blocking?"  -> any_measured.  One definite YES settles it; one gap does not let
#                               you say NO. (You cannot certify no captcha over a frame you could
#                               not enter — `/challenge_visibility` answering `blocking: false`
#                               over two live hCaptchas inside a cross-origin iframe, 2026-08-13.)
#
# Reach for the one whose SETTLING direction matches the answer you would act on. If acting on
# "no" is the dangerous move, you want `any_measured`.

def all_measured(*readings: Reading) -> Reading:
    """AND — "is every one of these true?"

    Settles on a definite FALSE (one false conjunct makes the AND false, whatever we could not
    read). A gap among otherwise-true readings is UNMEASURED, never true: certifying "all clear"
    over something we could not look at is the mistake this module exists to make impossible.
    """
    if not readings:
        return Reading.unmeasured("nothing to combine")
    for r in readings:
        if r.is_false():
            return Reading.measured(False, how=f"decided by: {r.how or r.describe()}")
    gaps = [r for r in readings if r.is_unmeasured()]
    if gaps:
        return Reading.unmeasured("; ".join(g.why for g in gaps))
    return Reading.measured(True, how="; ".join(r.how for r in readings if r.how))


def any_measured(*readings: Reading) -> Reading:
    """OR — "is any one of these true?"

    The mirror, and the one a SAFETY RAIL wants. Settles on a definite TRUE (one blocker we did
    see blocks, whatever else we could not read); a gap among otherwise-false readings is
    UNMEASURED, never false. "We found no captcha in the part we could see" is not "there is no
    captcha", and the difference is a form submitted into a challenge.
    """
    if not readings:
        return Reading.unmeasured("nothing to combine")
    for r in readings:
        if r.is_true():
            return Reading.measured(True, how=f"decided by: {r.how or r.describe()}")
    gaps = [r for r in readings if r.is_unmeasured()]
    if gaps:
        return Reading.unmeasured("; ".join(g.why for g in gaps))
    return Reading.measured(False, how="; ".join(r.how for r in readings if r.how))
