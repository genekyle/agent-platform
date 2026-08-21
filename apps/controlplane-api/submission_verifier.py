"""Is this page telling us an application was actually SENT?

WHY THIS EXISTS. `submitted` is the one flag that means success, and the system deliberately
refuses to infer it — nothing marks a real application as sent on its own. But "only a human may
say so" is not the same as "only a human may *know*", and the difference was costing us data: on
2026-08-19 a Paylocity application reached `Jobs/Success/4382310` reading *"Your application has
been received!"*, every organ in the cockpit agreed, and the ledger still said `now` because a
button did not land. The operator's ruling: **a verified confirmation is enough to record it.**

    "don't let that guard ruin data if i become lazy or miss that step ... if there is an
    application sent confirmation or anything of that nature, you ... will always have the right
    to set something as applied/done, especially if we have a verifier."

So the guard moves from *who pressed it* to **what was seen**. This module is that verifier, and
its whole contract is that it never returns a bare yes: a `Verdict` carries the signals that fired
and the text they matched, so an application recorded as sent can always be argued with.

FLEXIBLE BY CONSTRUCTION. The generic tier knows nothing about any particular ATS — it reads the
shapes every confirmation page in this family shares (a success-ish URL segment, a "we received
your application" sentence, a thank-you title) and it is what runs on an ATS nobody has met before.
`ATS_HINTS` only ever *adds* evidence for a platform we have measured; it can raise confidence and
it can never be required. Meeting a new ATS therefore degrades to "score it on the generic signals
and say that is what happened", which is the behaviour we want at 2am on an unknown host.

SHAREABLE. Pure functions over `(url, title, text)` — no browser, no session, no database. The
capture server, the cockpit, the runtime loop and a future L3 witness all ask the same question
the same way and get the same evidence back, which is the property the `__questionOf`/`__kindOf`
lessons say to build for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

#: A signal is (id, weight, why). Weight is evidence strength, not probability: STRONG signals can
#: carry a verdict alone, SUPPORTING ones only corroborate. Nothing here is tuned — these are the
#: shapes read off real confirmation pages, and they are meant to be argued with in review.
STRONG, SUPPORTING, WEAK = 1.0, 0.5, 0.25

#: Terminal-looking URL segments. Deliberately matched as PATH SEGMENTS, not substrings: a job
#: board with `/company/success-factors/` in a listing URL is not a confirmation page, and the
#: 08-19 census lesson (a name that matches by accident is worse than no match) applies to routes
#: as much as to controls.
_URL_SEGMENTS = {
    "success": STRONG, "confirmation": STRONG, "confirm": SUPPORTING, "submitted": STRONG,
    "thank-you": STRONG, "thankyou": STRONG, "thanks": SUPPORTING, "complete": SUPPORTING,
    "completed": SUPPORTING, "post-apply": STRONG, "applicationsubmitted": STRONG,
    "received": SUPPORTING, "finish": WEAK, "done": WEAK,
}

#: Sentences a confirmation page says. These are the strongest evidence available because they are
#: the site making the claim in its own words, in the first person, about THIS application.
_TEXT_PHRASES: tuple[tuple[str, float], ...] = (
    (r"your application (?:has been|was) (?:received|submitted|sent)", STRONG),
    (r"application (?:has been|was) (?:successfully )?(?:received|submitted|sent)", STRONG),
    (r"we(?:'ve| have) received your application", STRONG),
    (r"thank you for (?:applying|your application|your interest)", SUPPORTING),
    (r"application (?:is )?complete", SUPPORTING),
    (r"successfully (?:applied|submitted)", STRONG),
    (r"you(?:'ve| have) (?:already )?applied", SUPPORTING),
    (r"application submitted", STRONG),
    (r"we will (?:contact|be in touch|review)", WEAK),
    (r"confirmation (?:number|id|code)", SUPPORTING),
)

#: Words in a page TITLE. A title is chosen by the site to name the page, so it is better evidence
#: than body text that merely happens to contain a word.
_TITLE_WORDS = {
    "application successful": STRONG, "application received": STRONG, "application submitted": STRONG,
    "success": SUPPORTING, "thank you": SUPPORTING, "confirmation": SUPPORTING, "submitted": SUPPORTING,
}

#: DISQUALIFIERS. A page still asking for something is not a confirmation, whatever else it says —
#: a review step routinely contains "submit your application" and a validation error routinely
#: contains "is required". These do not merely fail to add evidence, they REMOVE the verdict, which
#: is what stops the 08-19 family of confident-wrong readings.
_DISQUALIFIERS: tuple[tuple[str, str], ...] = (
    (r"\bis required\b", "the page is still reporting a required field"),
    (r"\bplease (?:complete|correct|fill|enter|review and)\b", "the page is still asking for input"),
    (r"\berror\b.{0,40}\bsubmit", "the page is reporting a submit error"),
)

#: Per-platform additions. ONLY ever additive: an entry raises confidence for a host we have
#: measured and its absence never lowers one. `url_re` and `text_re` are extra evidence; nothing
#: here can veto the generic tier.
ATS_HINTS: dict[str, dict[str, Any]] = {
    "paylocity": {
        "url_re": r"/Recruiting/Jobs/Success/\d+",
        "text_re": r"your application has been received",
        "why": "measured live 2026-08-19 (Isabella Stewart Gardner Museum, req 4382310)",
    },
    "indeed_quick_apply": {
        "url_re": r"/post-apply|indeedapply/postapply",
        "text_re": r"your application has been submitted",
        "why": "the smartapply terminal; matches the indeed_apply_submitted page state",
    },
    "workday": {
        "url_re": r"/jobTasks/completed/application",
        "text_re": r"application submitted|we have received your application submission",
        "why": "the post-submit candidate-home modal; measured live 2026-08-21 (Ocean Spray): "
               "/en-US/<tenant>/jobTasks/completed/application?source=LinkedIn with an "
               "'Application Submitted — Thank you for applying!' dialog",
    },
}

#: Below this a page is not called submitted. One STRONG signal clears it; two SUPPORTING ones do
#: not, on purpose — "thank you for your interest" appears on rejection pages too.
CONFIRM_THRESHOLD = 1.0


@dataclass
class Signal:
    """One piece of evidence, and the text that produced it."""
    id: str
    weight: float
    matched: str
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "weight": self.weight, "matched": self.matched, "why": self.why}


@dataclass
class Verdict:
    """What we think, how sure, and — always — what we saw.

    `submitted` is the answer; `signals` is the argument. A caller that records an application as
    sent should store `evidence_line()` beside it, so the claim can be checked later against the
    page it came from rather than taken on trust.
    """
    submitted: bool
    score: float
    signals: list[Signal] = field(default_factory=list)
    disqualified_by: Optional[str] = None
    platform: str = ""
    platform_known: bool = False

    @property
    def confidence(self) -> str:
        if self.disqualified_by:
            return "none"
        if self.score >= 2.0:
            return "high"
        if self.score >= CONFIRM_THRESHOLD:
            return "medium"
        return "low"

    def evidence_line(self) -> str:
        """One human sentence naming what was seen — for a journal entry or a flag's detail."""
        if self.disqualified_by:
            return f"NOT a confirmation: {self.disqualified_by}"
        if not self.signals:
            return "no confirmation signal found on this page"
        seen = "; ".join(f"{s.id}={s.matched!r}" for s in self.signals[:4])
        tail = "" if self.platform_known else " (generic signals only — this platform has no hint entry)"
        return f"score {self.score:.2f} ({self.confidence}) from {seen}{tail}"

    def as_dict(self) -> dict[str, Any]:
        return {"submitted": self.submitted, "score": round(self.score, 3),
                "confidence": self.confidence, "platform": self.platform,
                "platform_known": self.platform_known,
                "disqualified_by": self.disqualified_by,
                "signals": [s.as_dict() for s in self.signals],
                "evidence": self.evidence_line()}


def _segments(url: str) -> list[str]:
    """Lowercased path segments, with separators normalised so `thank-you` and `thank_you` agree."""
    path = re.sub(r"^[a-z]+://[^/]+", "", (url or "").lower())
    path = path.split("?", 1)[0].split("#", 1)[0]
    return [re.sub(r"[_\s]+", "-", seg) for seg in path.split("/") if seg]


def verify(url: str = "", title: str = "", text: str = "", *, platform: str = "",
           extra_hints: Optional[dict[str, Any]] = None) -> Verdict:
    """Did this page confirm an application was sent?

    Every argument is optional because callers see different amounts: a tab listing has url+title
    and no body, a capture has all three. Missing input simply produces less evidence — it never
    produces a wrong confident answer.
    """
    url, title, text = (url or ""), (title or ""), (text or "")
    low_text, low_title = text.lower(), title.lower()
    signals: list[Signal] = []

    # A page still demanding input is not a confirmation, no matter what else it contains.
    for pattern, why in _DISQUALIFIERS:
        hit = re.search(pattern, low_text)
        if hit:
            return Verdict(submitted=False, score=0.0, signals=[], disqualified_by=why,
                           platform=platform, platform_known=platform in ATS_HINTS)

    for seg in _segments(url):
        weight = _URL_SEGMENTS.get(seg)
        if weight:
            signals.append(Signal("url_segment", weight, seg, "the route names a terminal page"))

    for pattern, weight in _TEXT_PHRASES:
        hit = re.search(pattern, low_text)
        if hit:
            signals.append(Signal("page_text", weight, hit.group(0), "the site says so in its own words"))

    for word, weight in _TITLE_WORDS.items():
        if word in low_title:
            signals.append(Signal("title", weight, word, "the site named the page this"))

    hint = dict(ATS_HINTS.get(platform) or {})
    hint.update(extra_hints or {})
    if hint:
        why = hint.get("why", "")
        if hint.get("url_re") and re.search(hint["url_re"], url, re.I):
            signals.append(Signal(f"hint:{platform or 'custom'}:url", STRONG, hint["url_re"], why))
        if hint.get("text_re") and re.search(hint["text_re"], low_text, re.I):
            signals.append(Signal(f"hint:{platform or 'custom'}:text", STRONG, hint["text_re"], why))

    score = sum(s.weight for s in signals)
    return Verdict(submitted=score >= CONFIRM_THRESHOLD, score=score, signals=signals,
                   platform=platform, platform_known=platform in ATS_HINTS)


def verify_tabs(tabs: Iterable[dict[str, Any]], *, platform: str = "") -> Verdict:
    """The best verdict across a window's tabs — the confirmation may not be the focused one.

    Returns the highest-scoring tab's verdict, so a window holding both a search tab and a
    confirmation tab answers with the confirmation.
    """
    best = Verdict(submitted=False, score=-1.0, platform=platform,
                   platform_known=platform in ATS_HINTS)
    for tab in tabs or ():
        v = verify(tab.get("url", ""), tab.get("title", ""), tab.get("text", ""), platform=platform)
        if v.score > best.score:
            best = v
    if best.score < 0:
        best.score = 0.0
    return best
