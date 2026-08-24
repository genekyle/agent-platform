"""The requisition id — the one job identity that survives crossing engines.

`applied_index` has scored a `requisition` tier since it was written ("certain enough to act on",
between the canonical tier and the fuzzy warning). Measured 2026-08-24: **`jobs.requisition_id`
was 0 of 614.** The tier could never fire. Nothing was broken — nothing ever filled the column.

Why it matters more than the ids we do keep: an Indeed `jk` rotates per search session, and a
LinkedIn `currentJobId` is LinkedIn's alone, so the same posting met on two engines can only ever
FUZZY-warn — the exact near-miss that skips "Data Analyst II" for having applied to "Data Analyst
I". The ATS's own requisition id is the employer's identity for the req: stable, engine-neutral,
and printed in the apply URL we already drive through every single time.

WHERE THE VALUE COMES FROM, AND WHY THIS IS A TABLE. Each ATS prints its req id in its own URL
shape. That is knowledge ABOUT VENDORS, which belongs in a table that a new row extends — not in
branching code that a new vendor has to be threaded through (the repo's own reader-vs-cadence
rule). An ATS nobody has mapped returns None, which is the honest answer: `applied_index` simply
falls through to the tier below, exactly as it does today.
"""

from __future__ import annotations

import re
from typing import Optional

#: ONE ROW PER ATS: the pattern whose first group is the requisition id, matched against the URL.
#: Anchored on the vendor's own path grammar rather than "any number in the url", because a
#: careless pattern that matched a page number would file two different reqs under one id — and a
#: WRONG requisition match is the expensive direction: `applied_index` treats this tier as certain
#: enough to ACT on, so a false hit silently skips a job the operator never applied to.
REQ_PATTERNS: dict[str, tuple[str, ...]] = {
    # bilh.wd1.myworkdayjobs.com/External/job/<place>/<slug>_JR88822
    "workday": (r"_(JR[-_]?\d{3,})\b", r"/(R-?\d{4,})\b"),
    # job-boards.greenhouse.io/hoodhp/jobs/5325374008
    "greenhouse": (r"/jobs/(\d{6,})\b",),
    # careers-odysseyconsult.icims.com/jobs/8308/data-business-analyst/job
    "icims": (r"/jobs/(\d{3,})/",),
    # recruiting.paylocity.com/.../Details/3298471
    "paylocity": (r"/Details/(\d{4,})\b",),
    # sjobs.brassring.com/...&jobId=2242816
    "brassring": (r"[?&]jobId=(\d{4,})\b",),
    # <tenant>.csod.com/ux/ats/careersite/4/home/requisition/1234
    "cornerstone": (r"/requisition/(\d{2,})\b",),
    # career5.successfactors.eu/...&career_job_req_id=12345
    "successfactors": (r"career_job_req_id=(\d{3,})\b", r"/job/(\d{5,})\b"),
    # <tenant>.dayforcehcm.com/CandidatePortal/.../Posting/12345
    "dayforce": (r"/Posting/(\d{3,})\b",),
    # <tenant>.peopleadmin.com/postings/12345
    "peopleadmin": (r"/postings/(\d{3,})\b",),
    # workforcenow.adp.com/...&jobId=123456
    "adp": (r"[?&]jobId=([\w-]{4,})\b",),
}

#: Hosts that are JOB BOARDS, never the employer's ATS. Their ids (`jk`, `currentJobId`) are the
#: board's own handle for a posting and rotate or differ per engine — the very reason this module
#: exists. Extracting from them would manufacture a cross-engine "match" that means nothing.
_BOARD_HOSTS = ("indeed.com", "linkedin.com", "ziprecruiter.com", "glassdoor.com",
                "monster.com", "dice.com", "simplyhired.com")


def is_board_url(url: str) -> bool:
    """True for a job-board URL — a posting's address on an aggregator, not on the employer's ATS."""
    host = str(url or "").split("//", 1)[-1].split("/", 1)[0].lower()
    return any(h in host for h in _BOARD_HOSTS)


def extract(url: str, ats: Optional[str] = None) -> Optional[str]:
    """The requisition id printed in an ATS apply URL, or None.

    `ats` narrows to that vendor's patterns when the caller knows it (the drive always does). With
    no `ats`, every pattern is tried and the FIRST match wins — safe because the patterns are
    anchored on distinct path grammars, and honest because a miss is None rather than a guess.
    """
    u = str(url or "").strip()
    if not u or is_board_url(u):
        return None
    families = (ats.lower(),) if ats and ats.lower() in REQ_PATTERNS else tuple(REQ_PATTERNS)
    for family in families:
        for pattern in REQ_PATTERNS[family]:
            hit = re.search(pattern, u, re.I)
            if hit:
                return hit.group(1).upper()
    return None
