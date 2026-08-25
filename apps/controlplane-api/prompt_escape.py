"""When a mandatory dropdown does not contain the truth — the escape ladder, and its guard.

Operator, 2026-08-24, driving Workday's School or University: *"logic/recipe as to how to figure
out these mandatory drop-downs ... is to check for true statements like University of Santo Tomas
or if they have an 'other' option which would require some logic to check through different
'other option' like the possibility of 'N/A', 'Unavailable', 'Other', etc."*

THE SHAPE OF THE PROBLEM. A prompt-backed required field accepts only what its list contains. The
operator's real answer is often absent — a Philippine university in a US health system's list, a
degree taxonomy that stops at the ones the employer hires for. Typing the truth into a `selectinput`
LOOKS like it worked and commits nothing (that is the widget-self-declaration lesson next door), so
the drive stalls on a field it believes it answered. What the site actually offers instead is an
escape hatch, and every vendor spells it differently: BPS's 4,983-entry school list ended with
**"Other Foreign Educational Institution"** — twelve entries past where our sample stopped
(2026-08-21).

THE ORDER IS THE POINT, AND IT IS A TABLE. Try the truth first, always. Only if the list cannot
hold it do we descend, most-specific escape first, so a list that offers BOTH "Other Foreign
Educational Institution" and a bare "Other" gets the one that says more. Nothing here invents a
value: every candidate must be an option the LIST offered, and the one used is reported so the
record says the answer was fitted, not stated.

THE GUARD IS THE OTHER HALF. An escape is a claim about the WORLD, and on some questions that
claim is a lie with consequences: sponsorship, work authorization, citizenship, clearance,
veteran/disability self-ID, criminal history. "Other" on a school list is a shrug; "Other" on
"do you require sponsorship" is an answer the operator never gave, on the exact class of question
that disqualifies (the 08-21 radio that silently held Yes). Those fields refuse the ladder and
escalate — the operator answers, or nobody does.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

#: MOST SPECIFIC FIRST. A list offering both a qualified escape and a bare one should get the
#: qualified one — it carries more meaning to a human reader downstream.
ESCAPE_LADDER: tuple[str, ...] = (
    "other foreign educational institution",   # BPS/PowerSchool, measured 2026-08-21
    "other institution",
    "other school",
    "not listed",
    "none of the above",
    "not applicable",
    "unavailable",
    "prefer not to say",
    "other",
    "n/a",
    "na",
)

#: FIELDS WHERE AN ESCAPE WOULD BE A CLAIM, NOT A SHRUG. Matched against the field's own label.
#: Deliberately broad: a false positive here costs one escalation, a false negative can cost the
#: application (or state something untrue about the operator's status).
_LOAD_BEARING = (
    r"sponsor", r"work authoriz", r"authorized to work", r"citizen", r"visa", r"green card",
    r"clearance", r"veteran", r"disab", r"gender", r"race", r"ethnic", r"felony", r"convict",
    r"background check", r"drug", r"salary", r"compensation", r"start date", r"notice period",
)


def is_load_bearing(field_label: str) -> bool:
    """True when 'Other' on this question would assert something about the operator that they did
    not say. Such a field never takes an escape — it escalates."""
    label = (field_label or "").lower()
    return any(re.search(p, label) for p in _LOAD_BEARING)


def escape_for(field_label: str, options: Sequence[str]) -> Optional[str]:
    """The best escape the LIST ACTUALLY OFFERS, or None.

    `options` is what the open prompt showed — never a guess. Returns the option verbatim (the
    caller must click the site's own string, not our normalised one).
    """
    if is_load_bearing(field_label):
        return None
    by_norm = {re.sub(r"[^a-z0-9 ]", " ", (o or "").lower()).strip(): o for o in options if o}
    for candidate in ESCAPE_LADDER:
        for norm, original in by_norm.items():
            # Anchored: "other" must not match "Mother's maiden name" or "Another campus".
            if norm == candidate or norm.startswith(candidate + " "):
                return original
    return None


#: THE PAGE'S OWN INSTRUCTION, WHICH OUTRANKS THIS MODULE'S LADDER. Workday tenants write the
#: escape token into the section's help text, and it is DIFFERENT PER SECTION on the same screen:
#: SolutionHealth's Education block says *"please type OTHER and hit the ENTER button"* while its
#: Certifications block, four inches lower, says *"please select NO CERTIFICATION NEEDED and hit
#: enter button"*. Applying Education's token to the certification field typed a value that list
#: does not contain — it looked filled and committed nothing (live 2026-08-24). The operator named
#: the gap exactly: *"we need to have questions lined up to which input we're dealing with and know
#: that there's very important CONTEXT"*. A generic ladder is the FALLBACK; the instruction that
#: governs THIS field is the answer whenever the page states one.
_INSTRUCTION_RE = re.compile(
    r"please\s+(?:type|select|enter)\s+"
    r"[\"\u201c\u2018']?(?P<token>[A-Z][A-Z0-9 /&'-]{2,48}?)[\"\u201d\u2019']?\s*"
    r"(?:and\s+(?:hit|press)|,|\.|$)", re.I)


def stated_escape(instruction: str) -> Optional[str]:
    """The literal token a section's instruction tells us to use, or None.

    Matched case-sensitively on the TOKEN because these are shouted in the copy ("type OTHER",
    "select NO CERTIFICATION NEEDED") — which is also what makes them safe to lift verbatim: the
    site is naming a value in its own list, so we are quoting it rather than guessing at it.
    """
    hit = _INSTRUCTION_RE.search(instruction or "")
    if not hit:
        return None
    token = (hit.group("token") or "").strip(" .,'\"")
    # A sentence like "please select the most recent completed degree" is guidance, not a token:
    # a real token is SHOUTED, so require the match to be predominantly upper-case.
    letters = [c for c in token if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return None
    return token


def plan(field_label: str, truth: str, options: Sequence[str],
         instruction: str = "") -> dict:
    """What to select, and WHY — the whole decision, reportable.

    `resolution` is `truth` when the list holds the operator's real answer, `escape` when it does
    not and a hatch exists, `escalate` when it does not and none does (or the question is one an
    escape may not answer). A drive that escalates here has lost nothing: the field is named, the
    options are in hand, and the operator answers once.
    """
    exact = next((o for o in options if (o or "").strip().lower() == (truth or "").strip().lower()),
                 None)
    if exact:
        return {"resolution": "truth", "value": exact,
                "why": f"the list holds the operator's own answer ({exact!r})"}
    # THE SECTION'S OWN WORDS FIRST — see `_INSTRUCTION_RE`. Still refused on a load-bearing
    # question: a site telling us to type OTHER on a sponsorship field would not make it true.
    stated = stated_escape(instruction) if not is_load_bearing(field_label) else None
    if stated:
        return {"resolution": "stated", "value": stated,
                "why": f"the section's own instruction names {stated!r} for a field like this"}
    hatch = escape_for(field_label, options)
    if hatch:
        return {"resolution": "escape", "value": hatch,
                "why": f"{truth!r} is not in this list; {hatch!r} is the site's own escape and "
                       f"claims nothing about the operator"}
    return {"resolution": "escalate", "value": None,
            "why": (f"{truth!r} is not in this list and "
                    + ("this question may not be answered by an escape"
                       if is_load_bearing(field_label)
                       else "the list offers no escape option"))}
