"""Where did we land? — the re-orientation step for leaving Indeed.

Operator, 2026-07-26: *"part of doing the 'out of indeed' applications requires sometimes a
re-orientation period because we're essentially always going into the unknown … so maybe like a
state like '<ATS> job landing page' … but we also need states like 'company page landing page'
which is another tough one because it needs to know context."*

--------------------------------------------------------------------------------------
Two questions, two sources — and that is the whole design
--------------------------------------------------------------------------------------
Classification was one question ("which ATS?") answered from the URL, which is why a real landing
reported `icims` and then stopped: naming the vendor says nothing about what is on the screen.

    WHICH PLATFORM   <- the URL / host        (ats_registry, already existed)
    WHAT KIND OF PAGE <- the CONTENT          (this module)
    the STATE         =  platform + kind      e.g. `icims_job_posting`

Splitting them is what makes "company careers page" expressible at all. It cannot be recognised by
host — every employer has a different one — so it is precisely the case where the platform axis
gives up (`company_site`) and only the content axis can say anything. Same page-kinds, any vendor.

--------------------------------------------------------------------------------------
The content is usually not in the document you are looking at
--------------------------------------------------------------------------------------
Measured live on the first real iCIMS landing (jobs-joslin.icims.com): the top document was 691
characters of the hospital's own homepage — patient care, donate, a 2019 copyright — and the job,
the description, the "Returning Candidate? Log back in!" line and the apply control were all
inside `#icims_content_iframe`, 4512 characters the top document never mentions.

So a classifier fed `page_text` alone would have called that landing a company marketing page with
complete confidence. `pick_content` takes the frames too and reads the richest readable one. An
employer's branded ATS wrapper is a NORMAL shape, not an oddity — it is how iCIMS ships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

# --- the page kinds we expect to meet on the way into an application ------------------
JOB_POSTING = "job_posting"        # one job, its description, an apply control
JOB_LIST = "job_list"              # a careers listing / search results — we are not on THE job
ACCOUNT_GATE = "account_gate"      # sign in or create an account before applying
APPLICATION_FORM = "application_form"
REVIEW = "review"                  # the summary before Submit — the gate screen, on every ATS
CONFIRMATION = "confirmation"      # submitted / thank you
GONE = "gone"                      # requisition closed, expired, 404
UNREADABLE = "unreadable"          # nothing to read — not the same as nothing there
UNKNOWN = "unknown"

#: Every REAL kind (the two non-answers excluded) — the vocabulary a learned witness may vote in.
KINDS = (JOB_POSTING, JOB_LIST, ACCOUNT_GATE, APPLICATION_FORM, REVIEW, CONFIRMATION, GONE)

#: Phrases that identify a kind, lowercased. Deliberately vendor-NEUTRAL: the point is that these
#: work on an employer's own careers page as well as on a named ATS, because that is the case the
#: platform axis cannot help with. Each list is evidence, and the matcher reports which phrases
#: hit so a wrong call can be corrected against what was actually on screen.
MARKERS: dict[str, tuple[str, ...]] = {
    # Checked FIRST — a confirmation page still contains most of the job's words.
    CONFIRMATION: (
        "application submitted", "thank you for applying", "we have received your application",
        "your application has been submitted", "thanks for applying",
        # WAS "application complete", REMOVED 2026-08-14. It is a substring of "Percent of
        # application completed 0%" — BrassRing's progress meter — so a form nobody had started
        # classified as a SENT application, decisively, on a page that also listed nine empty
        # required fields. The confirmation sense needs the copula; a progress label does not have
        # one.
        "your application is complete", "application is complete",
        # Cornerstone's terminal wording, read off the live confirmation 2026-08-11: "Thank You!
        # You have successfully applied to <job>". None of the six above matched it, so a sent
        # application read as `unknown` until this row.
        "successfully applied",
    ),
    GONE: (
        "no longer accepting applications", "this job is no longer available",
        "position has been filled", "requisition is closed", "job posting has expired",
        "page not found", "404",
    ),
    # Checked among the weighed kinds but listed before the form: a review screen still SHOWS the
    # form's words (it is the form, read back), so its own phrases must be able to outweigh them.
    REVIEW: (
        "review your application", "review and submit", "review your information",
        "application summary", "please review", "ready to submit",
    ),
    APPLICATION_FORM: (
        "required field", "* indicates a required", "first name", "last name",
        "upload your resume", "attach a resume", "work experience", "voluntary disclosures",
        "personal information",
    ),
    ACCOUNT_GATE: (
        "create an account", "sign in to continue", "returning candidate", "log back in",
        "already have an account", "create your profile", "sign up to apply",
        # The IDENTITY-FIRST shape, met live on iCIMS: before any form, the ATS asks for an email
        # to start or resume an application. It is the account wall wearing a friendlier label.
        "enter your information", "email address", "start your application",
        "resume your application",
        # THE CREDENTIAL FORM ITSELF, met live on BrassRing 2026-08-14 (Boston Children's). The
        # table had "already have an account" and not its inverse, "sign in to continue" and not a
        # bare sign-in form's own instruction — so a page reading "Sign in using username and
        # password / Forgot Username or Password? / Don't have an account yet?" classified as
        # UNKNOWN, the ladder said "genuinely new territory", and the account rung sat staged and
        # unreachable beside it.
        #
        # These are the phrases a login wall carries and essentially nothing else does. A password
        # recovery link exists to recover a password; an invitation to create an account is
        # offered where one is required. WEIGHED rather than decisive, so a real application form
        # with a "forgot password" link in its footer still classifies as the form it is.
        "forgot username", "forgot password", "don't have an account", "dont have an account",
        "sign in using", "show password",
    ),
    JOB_POSTING: (
        "job description", "overview", "responsibilities", "qualifications", "job id",
        "requisition", "apply for this job", "apply now", "job summary", "position summary",
        "what you'll do", "about the role",
    ),
    JOB_LIST: (
        "search results", "jobs found", "filter by", "sort by", "view all jobs",
        "current openings", "search jobs", "job openings",
    ),
}

#: DECISIVE kinds win on a single marker, because their phrases are unambiguous and their
#: consequence is severe: reading a confirmation as a posting would re-apply to a job we have just
#: applied to, and reading a dead requisition as a live one wastes a whole drive.
DECISIVE = (CONFIRMATION, GONE)

#: The rest are WEIGHED, not ordered, because a real page trips several at once. The live iCIMS
#: landing carried "Returning Candidate? / Log back in!" in its header — two account-gate phrases —
#: on a page that was plainly a job posting (job id, overview, responsibilities, qualifications,
#: apply). Strict precedence called it an account gate and would have sent the drive looking for a
#: login it did not need. A header link is not a wall; the weight of evidence says which it is.
#: Ties fall back to this order, most-specific first. REVIEW leads: it is the form read back, so
#: on a tie with the form's own words the more specific claim wins.
WEIGHED = (REVIEW, APPLICATION_FORM, ACCOUNT_GATE, JOB_POSTING, JOB_LIST)

ORDER = DECISIVE + WEIGHED

#: A page that reports how far through itself you are is a page you are still inside — UNLESS the
#: meter says you are all the way through. The first cut of this guard blocked on the PRESENCE of a
#: meter and broke the very page it was written beside: BrassRing's real confirmation reads
#: "Application Complete / Percent of application completed / 100% / Your application has been
#: submitted", so a genuinely sent application classified `unknown` (measured 2026-08-14, minutes
#: after the guard shipped). A meter is evidence in BOTH directions and has to be read, not just
#: detected.
_METERS = (
    re.compile(r"percent of [a-z ]{0,24}complet\w*\s*(\d{1,3})\s?%"),
    re.compile(r"\b(\d{1,3})\s?%\s*(?:complete|completed|done)"),
)
_STEPPER = re.compile(r"\bstep\s+(\d+)\s+of\s+(\d+)")


def wizard_position(text: str) -> Optional[dict[str, int]]:
    """The page's OWN statement of where it is in its flow — `{"step": 1, "of": 6}` — or None.

    The regex has been here since the confirmation guard was written, and until 2026-08-27 its
    reading was thrown away: `_reports_unfinished` asks it a yes/no question and discards the
    numbers. That is why the shared ATS cadence's "at most 1 screen from Submit" stayed
    optimistic through a SIX-step Paylocity wizard (2026-08-19) and a six-step Cornerstone one
    (08-24) — the page said so both times, in as many words, and nothing read it.

    Percent meters answer the same question in another dialect and are reported the same way, so
    a caller gets a position from whichever the page happens to use.
    """
    # LOWERCASED HERE, because this one is called with RAW page text. `_STEPPER`/`_METERS` are
    # lowercase patterns and `_reports_unfinished` relies on its callers having folded case
    # already — a convention that holds inside this module and would silently return None for
    # every real page ("Step 1 of 6") if a public entry point inherited it by accident.
    body = (text or "").lower()
    best: Optional[dict[str, int]] = None
    for m in _STEPPER.finditer(body):
        try:
            step, of = int(m.group(1)), int(m.group(2))
        except (TypeError, ValueError):
            continue
        if of > 0 and step > 0 and (best is None or of > best["of"]):
            best = {"step": step, "of": of}
    if best:
        return best
    for rx in _METERS:
        for m in rx.finditer(body):
            try:
                pct = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 <= pct <= 100:
                return {"percent": pct}
    return None


def _reports_unfinished(body: str) -> bool:
    """Does this page's own progress readout say it is NOT done? Only that answer blocks a
    confirmation — "100%" and "step 6 of 6" are a page agreeing it has finished."""
    for rx in _METERS:
        for m in rx.finditer(body):
            try:
                if int(m.group(1)) < 100:
                    return True
            except (TypeError, ValueError):
                continue
    for m in _STEPPER.finditer(body):
        try:
            if int(m.group(1)) < int(m.group(2)):
                return True
        except (TypeError, ValueError):
            continue
    return False

#: Phrases that are worth TWO ordinary markers because they are unambiguous on their own. A page
#: saying "Enter your information" above an email box is an identity step and nothing else — but
#: it is a THREE-LINE page, so a flat two-marker minimum called it unknown and the drive stalled
#: (live, iCIMS's email gate, 2026-07-26). Weighting beats lowering the minimum, which would let
#: a stray "overview" carry a whole classification.
STRONG: frozenset = frozenset({
    "enter your information", "returning candidate", "create an account", "log back in",
    "start your application", "resume your application", "already have an account",
    "upload your resume", "* indicates a required", "apply for this job",
    "review your application", "review and submit",
    # A credential form is a SHORT page — username, password, two links — so the same weighting
    # argument the iCIMS email gate earned applies here: these phrases are unambiguous on their
    # own, and a flat two-marker minimum on a five-line page reads as "unknown".
    "forgot username", "forgot password", "don't have an account", "dont have an account",
    "sign in using",
})


def _weight(hits: tuple) -> int:
    return sum(2 if h in STRONG else 1 for h in hits)


@dataclass(frozen=True)
class Landing:
    """What kind of page this is, and the words that say so."""

    kind: str
    evidence: tuple[str, ...] = ()
    source: str = ""            # which document the text came from (top / frame id)
    text_len: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "evidence": list(self.evidence),
                "source": self.source, "text_len": self.text_len}


def pick_content(top_text: str = "", frames: Optional[list[dict]] = None) -> tuple[str, str]:
    """The document actually worth classifying, as (text, source).

    A frame that is BIG ON SCREEN and has real text is the content frame, and it wins outright.
    That is the structural fact rather than a size heuristic: a branded wrapper delegates its whole
    body to one large frame and keeps only a header and a footer for itself. iCIMS's content frame
    measured 1249x1654; the tracking iframe beside it was 0x0.

    Length alone is not enough — a chatty wrapper footer can outweigh a terse job page, and this
    module's own first fixture did exactly that. Among several large readable frames the richest
    wins; if none qualifies, the top document is all there is, which is the plain-ATS case.
    """
    candidates = []
    for f in (frames or []):
        if not f.get("readable"):
            continue
        t = f.get("text") or ""
        # A SPARSE PAGE IS STILL THE CONTENT. This was `< 200` and it excluded exactly the state we
        # had just driven to: iCIMS's email gate is three lines ("Enter Your Information / Email"),
        # deliberately, and the classifier fell back to 691 characters of hospital wrapper and
        # called the whole thing unknown. Volume was never the signal — the frame's SIZE ON SCREEN
        # is, and that already excludes the 0x0 trackers. Keep only a floor low enough to reject a
        # frame that has genuinely rendered nothing.
        if len(t.strip()) < 20:
            continue
        # An offscreen or zero-size frame is a tracker/pixel, never the content. `0` is falsy, so
        # `if w and w < 200` skipped exactly the 0x0 case it was written for — test caught it.
        w, h = f.get("width"), f.get("height")
        if w is not None and h is not None and (int(w) < 200 or int(h) < 200):
            continue
        candidates.append((len(t), t, f.get("id") or f.get("name") or "frame"))

    if candidates:
        _, text, src = max(candidates, key=lambda c: c[0])
        return text, src
    return (top_text or ""), "top"


def classify_kind(text: str, *, source: str = "top") -> Landing:
    """Which kind of page is this, from its words alone. No URL, deliberately — this is the axis
    that has to work when the host tells us nothing."""
    body = " ".join((text or "").lower().split())
    if not body:
        return Landing(UNREADABLE, (), source, 0)

    # A PAGE MEASURING ITS OWN INCOMPLETENESS IS NOT A CONFIRMATION, whatever phrase it contains.
    #
    # DECISIVE means "one marker is enough", which is right for phrases that can only mean one
    # thing and severe when one turns out to have a second meaning. Live 2026-08-14 on BrassRing:
    # "Percent of application completed 0%" carries the substring "application complete", so the
    # first screen of an untouched application was classified `confirmation` — `steps_to_submit:
    # 0`, the flow reporting a finished application, on a form with nine empty required fields.
    # Marking a job applied-to that was never sent is the worst outcome in this system: it removes
    # the job from every future search and the operator never learns why.
    #
    # A progress meter is the tell, and it is unambiguous in the other direction — no confirmation
    # page reports what percentage of itself is done. Belt and braces with the marker fix, because
    # this failure is not one to catch only once.
    progressing = _reports_unfinished(body)

    # Decisive first: one unambiguous phrase is enough.
    for kind in DECISIVE:
        hits = tuple(m for m in MARKERS[kind] if m in body)
        if hits and not (kind == CONFIRMATION and progressing):
            return Landing(kind, hits[:4], source, len(body))

    # Then weigh the rest. Two markers minimum — a single "overview" is a word that appears
    # everywhere — and the most-evidenced kind wins, ties going to the most specific.
    scored = []
    for kind in WEIGHED:
        hits = tuple(m for m in MARKERS[kind] if m in body)
        w = _weight(hits)
        if w >= 2:
            scored.append((w, -WEIGHED.index(kind), kind, hits))
    if scored:
        _, _, kind, hits = max(scored)
        return Landing(kind, hits[:4], source, len(body))
    return Landing(UNKNOWN, (), source, len(body))


def landing_state(platform: str, kind: str) -> str:
    """The state id the corpus records: platform and page-kind, joined.

    `icims_job_posting`, `company_site_job_list`, `workday_account_gate`. One vocabulary across
    every vendor, so a recipe learned for one platform's account gate is recognisably the same
    SHAPE as another's — which is the point of naming the kind separately at all.
    """
    return f"{platform or 'unknown'}_{kind or UNKNOWN}"
