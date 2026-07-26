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

from dataclasses import dataclass
from typing import Any, Optional

# --- the page kinds we expect to meet on the way into an application ------------------
JOB_POSTING = "job_posting"        # one job, its description, an apply control
JOB_LIST = "job_list"              # a careers listing / search results — we are not on THE job
ACCOUNT_GATE = "account_gate"      # sign in or create an account before applying
APPLICATION_FORM = "application_form"
CONFIRMATION = "confirmation"      # submitted / thank you
GONE = "gone"                      # requisition closed, expired, 404
UNREADABLE = "unreadable"          # nothing to read — not the same as nothing there
UNKNOWN = "unknown"

#: Phrases that identify a kind, lowercased. Deliberately vendor-NEUTRAL: the point is that these
#: work on an employer's own careers page as well as on a named ATS, because that is the case the
#: platform axis cannot help with. Each list is evidence, and the matcher reports which phrases
#: hit so a wrong call can be corrected against what was actually on screen.
MARKERS: dict[str, tuple[str, ...]] = {
    # Checked FIRST — a confirmation page still contains most of the job's words.
    CONFIRMATION: (
        "application submitted", "thank you for applying", "we have received your application",
        "your application has been submitted", "application complete", "thanks for applying",
    ),
    GONE: (
        "no longer accepting applications", "this job is no longer available",
        "position has been filled", "requisition is closed", "job posting has expired",
        "page not found", "404",
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
#: Ties fall back to this order, most-specific first.
WEIGHED = (APPLICATION_FORM, ACCOUNT_GATE, JOB_POSTING, JOB_LIST)

ORDER = DECISIVE + WEIGHED

#: Phrases that are worth TWO ordinary markers because they are unambiguous on their own. A page
#: saying "Enter your information" above an email box is an identity step and nothing else — but
#: it is a THREE-LINE page, so a flat two-marker minimum called it unknown and the drive stalled
#: (live, iCIMS's email gate, 2026-07-26). Weighting beats lowering the minimum, which would let
#: a stray "overview" carry a whole classification.
STRONG: frozenset = frozenset({
    "enter your information", "returning candidate", "create an account", "log back in",
    "start your application", "resume your application", "already have an account",
    "upload your resume", "* indicates a required", "apply for this job",
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

    # Decisive first: one unambiguous phrase is enough.
    for kind in DECISIVE:
        hits = tuple(m for m in MARKERS[kind] if m in body)
        if hits:
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
