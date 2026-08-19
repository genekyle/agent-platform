"""WHAT does this application ask for, and WHEN? — the third axis.

Two axes already exist and they are the right two:

    WHICH PLATFORM    <- the URL / host      (`ats_registry`)
    WHAT KIND OF PAGE <- the content         (`apply_landing`: job_posting, account_gate,
                                              application_form, review, confirmation)

They answer *where am I*. They do not answer the question that actually decides whether a drive
can finish: **what will this thing demand of me, and at which screen?** Operator, 2026-08-19,
after three applications through three different vendors in one day:

    "everything will need a resume input but each case on how we provide it will be different
     such as the way to do it and even the timing of when they ask it … the cadence is generally
     the same, the only thing really that changes are the interaction profile and the specific
     steps to get the general steps completed."

That is this module. `REQUIREMENTS` is the closed vocabulary of general steps; an `Observation`
pins one requirement to the (platform, page-kind) where it was actually SEEN. The résumé slot
measured on 2026-08-19 landed in three different places — Indeed's first screen, Paylocity's form
step 1, PeopleAdmin's **account-creation** page, before the application exists at all — and that
is the whole point: same requirement, three timings, and only the timing has to be learned.

--------------------------------------------------------------------------------------
Deliberately an observation ledger, NOT a rule book
--------------------------------------------------------------------------------------
The operator's own worry, in their words: *"i am also scared and wary of over-generalizing where
if we have a bunch of recipes that a particular process goes one way, then maybe we might start
making assumptions we don't want."*

So nothing here ever says "PeopleAdmin requires a cover letter". It says **"cover_letter observed
at job_posting on peopleadmin, 1 of 1 flows"** — a count with a denominator, and the phrase that
matched. A planner may lean on `seen/total` and must not read a single sighting as a law. The
failure this guards against is the one this repo keeps paying for: a summary that is silent about
what it did not examine reads exactly like a summary that examined everything.

`detect` is vendor-neutral by construction — it reads what a page SAYS, so it works on an
employer's own careers site as well as on a named vendor, which is the case the platform axis
cannot help with.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# --- the general steps -----------------------------------------------------------------
#: The closed vocabulary. Closed on purpose: the operator's observation is that the possible
#: demands of a third-party application are NOT unlimited. A demand we meet that is not in here is
#: a real discovery and should be added deliberately, not absorbed silently as "other".
RESUME = "resume"
COVER_LETTER = "cover_letter"
REFERENCES = "references"
ACCOUNT = "account"
SUPPLEMENTAL_QUESTIONS = "supplemental_questions"
WORK_HISTORY = "work_history"
EDUCATION_HISTORY = "education_history"
EEO = "eeo"
ESIGNATURE = "esignature"
WORK_AUTHORIZATION = "work_authorization"
SALARY_EXPECTATION = "salary_expectation"
AVAILABILITY = "availability"
ASSESSMENT = "assessment"
PORTFOLIO = "portfolio"
TRANSCRIPT = "transcript"
WRITING_SAMPLE = "writing_sample"

REQUIREMENTS = (RESUME, COVER_LETTER, REFERENCES, ACCOUNT, SUPPLEMENTAL_QUESTIONS, WORK_HISTORY,
                EDUCATION_HISTORY, EEO, ESIGNATURE, WORK_AUTHORIZATION, SALARY_EXPECTATION,
                AVAILABILITY, ASSESSMENT, PORTFOLIO, TRANSCRIPT, WRITING_SAMPLE)

#: Which of these a drive cannot satisfy on its own, whatever the profile holds. `account` needs
#: credentials typed by the operator; `assessment` is a test taken by a person. Naming them here
#: is what lets a planner say "this flow will stop" BEFORE spending twenty minutes reaching it.
NEEDS_HUMAN = frozenset({ACCOUNT, ASSESSMENT})

#: Phrase evidence per requirement, lowercased. Vendor-neutral and deliberately narrow: a false
#: positive here becomes a planner's wrong assumption, which is the exact failure mode the
#: operator flagged.
MARKERS: dict[str, tuple[str, ...]] = {
    RESUME: (r"\bresume\b", r"\bcv\b", r"curriculum vitae"),
    COVER_LETTER: (r"cover letter",),
    REFERENCES: (r"\breferences?\b", r"referee"),
    ACCOUNT: (r"create an account", r"create account", r"sign in to apply", r"log in to apply",
              r"you must have an account", r"returning candidate"),
    SUPPLEMENTAL_QUESTIONS: (r"supplemental questions?", r"screening questions?",
                             r"questions from the employer", r"additional questions?"),
    WORK_HISTORY: (r"work history", r"employment history", r"work experience"),
    EDUCATION_HISTORY: (r"education history", r"educational background"),
    EEO: (r"equal employment opportunity", r"voluntary self-?identification", r"eeo\b",
          r"veteran status", r"disability status"),
    ESIGNATURE: (r"agree to sign electronically", r"electronic signature", r"e-?signature",
                 r"certify (?:and affirm )?that the (?:facts|information)"),
    WORK_AUTHORIZATION: (r"authorized to work", r"work authorization", r"legally authorized"),
    SALARY_EXPECTATION: (r"desired salary", r"salary expectation", r"expected salary",
                         r"compensation expectation"),
    AVAILABILITY: (r"available to start", r"start date", r"availability date"),
    ASSESSMENT: (r"\bassessment\b", r"skills test", r"take a test", r"pre-?employment test"),
    PORTFOLIO: (r"\bportfolio\b", r"work samples?"),
    TRANSCRIPT: (r"\btranscripts?\b",),
    WRITING_SAMPLE: (r"writing sample",),
}

#: A phrase that only counts when the page is DECLARING a requirement rather than mentioning one.
#: "Documents Needed to Apply" and "Required Documents" are declarations; the word "resume" inside
#: a job description is not. Used to weight, never to veto — a form field is a declaration too.
_DECLARATION_CONTEXT = (r"documents? needed to apply", r"required documents?",
                        r"please (?:attach|upload|provide)", r"required fields?",
                        r"you must", r"is required", r"\(required\)")


@dataclass(frozen=True)
class Sighting:
    """One requirement, seen on one page, with the words that said so."""
    requirement: str
    matched: str
    declared: bool          # was it stated as a requirement, or merely mentioned?

    def as_dict(self) -> dict[str, Any]:
        return {"requirement": self.requirement, "matched": self.matched, "declared": self.declared}


@dataclass
class Observation:
    """A requirement pinned to WHERE it was met: platform + page kind.

    `kind` is an `apply_landing` KIND, so this joins straight onto the existing spine rather than
    inventing a parallel vocabulary.
    """
    platform: str
    kind: str
    requirement: str
    declared: bool = False
    note: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.platform, self.kind, self.requirement)

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "kind": self.kind, "requirement": self.requirement,
                "declared": self.declared, "note": self.note}


def detect(text: str, *, declared_only: bool = False) -> list[Sighting]:
    """Which requirements does this page's text speak about, and did it DECLARE them?

    Vendor-neutral: it reads the page, not the host. `declared_only` keeps just the ones stated as
    requirements, which is what a planner should reason from — a job description mentioning the
    word "portfolio" is not a portfolio requirement.
    """
    low = " ".join((text or "").lower().split())
    if not low:
        return []
    declaring = any(re.search(p, low) for p in _DECLARATION_CONTEXT)
    out: list[Sighting] = []
    for req in REQUIREMENTS:
        for pattern in MARKERS[req]:
            hit = re.search(pattern, low)
            if not hit:
                continue
            # A requirement is DECLARED when the page is in a declaring context and the phrase sits
            # near that language; approximated by the page-level flag, which is honest about being
            # an approximation rather than pretending to parse layout.
            out.append(Sighting(req, hit.group(0), declaring))
            break
    return [s for s in out if s.declared] if declared_only else out


def observe(platform: str, kind: str, text: str, *, note: str = "") -> list[Observation]:
    """Turn one page into Observations — the row a drive contributes to the ledger."""
    return [Observation(platform=platform or "unknown", kind=kind or "unknown",
                        requirement=s.requirement, declared=s.declared, note=note)
            for s in detect(text)]


def summarise(observations: Iterable[Observation], *, flows_by_platform: Optional[dict[str, int]] = None
              ) -> dict[str, Any]:
    """What we have SEEN, per platform — counts with denominators, never rules.

    `flows_by_platform` is how many complete flows we have driven for each platform. Without it a
    caller gets raw counts and no denominator, and the summary says so: `flows` is `None` and
    `confidence` is "unknown". That is deliberate — a requirement seen once out of one flow and a
    requirement seen once out of twenty look identical without it, and only one of them is a rule.
    """
    flows = flows_by_platform or {}
    per: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for ob in observations:
        row = per[ob.platform].setdefault(
            ob.requirement, {"requirement": ob.requirement, "seen": 0, "kinds": [], "declared": False})
        row["seen"] += 1
        row["declared"] = row["declared"] or ob.declared
        if ob.kind not in row["kinds"]:
            row["kinds"].append(ob.kind)

    out: dict[str, Any] = {}
    for platform, rows in per.items():
        total = flows.get(platform)
        items = []
        for row in rows.values():
            row = dict(row)
            row["flows"] = total
            row["confidence"] = ("unknown" if not total
                                 else "observed" if row["seen"] < total
                                 else "consistent" if total >= 3
                                 else "provisional")
            row["needs_human"] = row["requirement"] in NEEDS_HUMAN
            items.append(row)
        out[platform] = {
            "flows": total,
            "requirements": sorted(items, key=lambda r: (-r["seen"], r["requirement"])),
            "caveat": ("no complete flows counted for this platform — these are sightings, "
                       "not a recipe" if not total else
                       f"{total} flow(s) driven; a requirement seen fewer times than that is not "
                       f"a rule"),
        }
    return out


def blockers(observations: Iterable[Observation], *, platform: str) -> list[str]:
    """Requirements observed for this platform that a drive cannot satisfy alone.

    The cheap question a planner should ask BEFORE entering: will this stop on a human?
    """
    seen = {ob.requirement for ob in observations if ob.platform == platform}
    return sorted(seen & NEEDS_HUMAN)
