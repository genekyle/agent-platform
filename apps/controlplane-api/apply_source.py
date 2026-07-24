"""Source of application — resolving "How did you hear about us?" from where the apply came from.

Operator, 2026-07-24: the answer to "how did you hear" is not a stored constant, it is a function
of CONTEXT. This application came from Indeed, so the answer is the job-board leaf "Indeed" (or its
sibling "SimplyHired", which Indeed owns) — but *"these aren't always the options, other is
acceptable if indeed is not present."* And it foreshadows the next domains: from LinkedIn the leaf
is "LinkedIn", and so on.

So this maps an apply SOURCE to an ORDERED list of prompt values to try, most-specific first,
always ending in "Other". The live prompt decides which of them actually exist — we hand the list
to `/select_prompt` (the Workday prompt driver we already built) and it selects the first that is
offered. "Other" is the universal floor: every "how did you hear" prompt has it, so we can always
answer honestly even when the exact source is not listed.

Pure data + a resolver. No I/O. The knowledge lives here (editable) rather than in an endpoint, so
adding LinkedIn later is a one-line change, not a code change in the driver.
"""

from __future__ import annotations

from typing import Optional

#: Apply source -> the leaf values to prefer, in order. Kept small and honest: only sources we
#: actually originate from. Indeed and SimplyHired are siblings (Indeed owns SimplyHired), so each
#: lists the other as a fallback — a SimplyHired-branded form still came from the Indeed network.
_SOURCE_LEAVES: dict[str, list[str]] = {
    "indeed": ["Indeed", "SimplyHired"],
    "simplyhired": ["SimplyHired", "Indeed"],
    "linkedin": ["LinkedIn"],
    "ziprecruiter": ["ZipRecruiter"],
    "glassdoor": ["Glassdoor"],
}

#: Always the last resort, and always present on a "how did you hear" prompt — so the resolver can
#: never return an empty list and we can always answer without inventing.
FALLBACK = "Other"


def source_from_job_id(job_id: Optional[str]) -> str:
    """The source platform from a job ref like 'indeed:abc123' -> 'indeed'. '' when unknown."""
    return (job_id or "").split(":", 1)[0].strip().lower()


def source_candidates(source: Optional[str]) -> list[str]:
    """Ordered prompt values to try for this source, most-specific first, ending in Other.

    Never empty: an unknown source still gets ['Other'], which is a truthful answer to "how did
    you hear" for any application we cannot place. De-duplicated case-insensitively so a source
    whose only real answer is Other does not list it twice.
    """
    leaves = _SOURCE_LEAVES.get((source or "").strip().lower(), [])
    out: list[str] = []
    seen: set[str] = set()
    for v in [*leaves, FALLBACK]:
        if v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def known_sources() -> list[str]:
    """The sources we have a specific answer for — for the UI and for a quick 'do we know this?'."""
    return sorted(_SOURCE_LEAVES)
