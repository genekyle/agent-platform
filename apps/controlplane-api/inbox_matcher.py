"""The inbox → application matcher — the reader the tracker was missing.

`application_events` was built for this day: `source="gmail"` and its evidence shape have been in
the model since day one, `job_key` was minted as "the key Gmail will join on", and the audit that
scheduled this work (docs/ANALYSIS_system_gaps.md, "The Gmail reframe") measured the gap it
closes: 28 applications, exactly one event kind ever recorded, zero outcomes. The inbox READER
also already exists (`/read_inbox` on the capture server, subject-line only, no read receipt).
This module is the one missing piece between them: inbox row → which application → what happened.

Three verdicts, and the boundary between them is the whole design:

    record        — written to the timeline unattended. Only when BOTH halves are unambiguous:
                    exactly one application's company matches, and the kind is carried by phrasing
                    distinctive enough to bet on (automated confirmations, "your application was
                    viewed", and the strong rejection formulas).
    needs_review  — application-related, but a human should glance: the company matched more than
                    one application (or none, while the sender is a known ATS), the kind is only
                    weakly implied ("unfortunately" alone), or the kind is a HUMAN response
                    (interview / assessment / screening / recruiter contact). Human-response kinds
                    are never auto-written no matter how clear the phrasing — they are the
                    numerator of every response rate, and a false one poisons the number the
                    operator actually reads. Review rows arrive prefilled so resolving one is one
                    click, not a form.
    ignore        — not about any application: no ATS sender, no company match, no application
                    language. The inbox is a PERSONAL mailbox, so ignored mail is remembered as a
                    fingerprint ONLY (for idempotent re-sweeps) — subject, sender and snippet of
                    personal mail are never persisted (PRINCIPLES §4).

Matching is deliberately built from parts the system already trusts: sender domain → ats_id via
the registry's own hosts catalogue (plus a small additive map for MAIL-only domains, in the same
spirit as `submission_verifier.ATS_HINTS` — extra evidence for a platform we know, never a new
requirement), and company → job_key via `job_dedup.normalize_company`, the same normalizer the
dedup matcher is pinned to.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import ats_registry
import job_dedup

# --------------------------------------------------------------------------------------
# Sender → ATS
# --------------------------------------------------------------------------------------

#: MAIL domains that identify an ATS the registry already knows but that never appear as a WEB
#: host (so the hosts catalogue alone cannot see them). Additive only — an entry here may add an
#: attribution, never change what a registry host would have said. Substring-matched against the
#: sender's domain, same as the hosts loop.
ATS_MAIL_DOMAINS: dict[str, str] = {
    "indeedemail.com": "indeed_quick_apply",   # Indeed notifies from @indeedemail.com
    "greenhouse-mail.io": "greenhouse",        # Greenhouse sends via us.greenhouse-mail.io
    "workablemail.com": "workable",
    "adp.com": "adp",                          # hosts list only the workforcenow/myjobs subdomains
    "powerschool.com": "schoolspring",         # only auth.powerschool.com is a registry host
    "talent.icims.com": "icims",               # already covered by icims.com; named for clarity
}


def sender_address(sender: str) -> tuple[str, str]:
    """Split `/read_inbox`'s sender field into (address, display_name).

    The reader emits `"{email} {display name}"` with either half possibly empty — the address is
    whichever token carries an @, and everything else is the name.
    """
    words = (sender or "").split()
    address = next((w for w in words if "@" in w), "")
    name = " ".join(w for w in words if w != address)
    return address.lower().strip("<>"), name.strip()


def sender_ats(address: str) -> Optional[str]:
    """The ATS a sender domain names, or None for an unrecognised (usually personal) sender.

    Substring match against the registry's hosts, mirroring `classify_ats` — but unlike the URL
    classifier the ENGINE domains are kept in play: mail *from indeed.com about an application* is
    exactly the attribution we want, and no ATS mail domain contains an engine's, so there is no
    shadowing to guard against here.

    TANDEM SEAM (docs/PLAN_verify_email_leg.md Part 2): the verify-leg session is creating
    `gmail_senders.py` with a `classify_sender`; once it lands, this function should delegate to
    (and `ATS_MAIL_DOMAINS` should fold into) that one classifier so the verify leg and the
    matcher cannot disagree about who a sender is. Resolved at this branch's rebase, verify-leg
    merges first.
    """
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    if not domain:
        return None
    for needle, ats_id in ATS_MAIL_DOMAINS.items():
        if needle in domain:
            return ats_id
    for ats in ats_registry.ATS_PLATFORMS:
        if any(needle in domain for needle in ats.get("hosts") or ()):
            return ats["ats_id"]
    return None


# --------------------------------------------------------------------------------------
# Subject/snippet → event kind
# --------------------------------------------------------------------------------------

#: Phrase families, checked IN ORDER — a rejection that opens "thank you for your interest" must
#: classify as the rejection, so terminal/human kinds are tested before the confirmation family.
#: Kinds in `_REVIEW_ONLY_KINDS` are proposed but never auto-written (see module docstring).
_KIND_PHRASES: list[tuple[str, list[str]]] = [
    ("rejection", [
        "will not be moving forward", "not be moving forward", "not moving forward",
        "move forward with other candidates", "moving forward with other candidates",
        "pursue other candidates", "selected another candidate", "with other applicants",
        "no longer under consideration", "regret to inform", "position has been filled",
        "decided not to proceed", "not selected for",
    ]),
    ("interview_invite", [
        "schedule an interview", "interview invitation", "invite you to interview",
        "invited to interview", "for an interview", "schedule your interview",
    ]),
    ("assessment", [
        "assessment", "coding challenge", "take-home", "hackerrank", "codility",
        "testgorilla", "pymetrics",
    ]),
    ("screening_invite", [
        "phone screen", "screening call", "screening interview", "recruiter call",
    ]),
    ("viewed", [
        "application was viewed", "viewed your application", "resume was viewed",
        "viewed your resume",
    ]),
    ("confirmation", [
        "thank you for applying", "thanks for applying", "application received",
        "received your application", "application submitted", "application was sent",
        "successfully submitted", "application has been received",
        "application has been submitted", "thank you for your application",
        "confirm receipt of your application",
    ]),
]

#: Phrases that IMPLY a kind without being distinctive enough to write unattended — enough to
#: prefill the review row, never enough to bet the timeline on. "Unfortunately" is the canonical
#: member: in application mail it is almost always a rejection, and "almost" is the point.
_WEAK_KIND_PHRASES: list[tuple[str, list[str]]] = [
    ("rejection", ["unfortunately", "other candidates", "wish you the best", "not a match"]),
    ("interview_invite", ["interview", "your availability", "schedule a time", "schedule time"]),
    ("recruiter_contact", ["reached out", "connect with you", "your background"]),
    ("confirmation", ["your application to", "you applied", "your recent application"]),
]

#: Kinds a matcher may propose but never write unattended — the employer-response tier that every
#: response rate is computed from, plus anything a human should own.
_REVIEW_ONLY_KINDS = frozenset({
    "interview_invite", "assessment", "screening_invite", "recruiter_contact",
})

#: Words that say "this mail is about a job application" even when no kind phrase lands — used
#: only to decide review-vs-ignore for mail from an unrecognised sender.
_APPLICATION_WORDS = re.compile(
    r"\b(application|applied|candidate|interview|recruiter|hiring|job|position|requisition)\b",
    re.IGNORECASE)


def classify_kind(subject: str, snippet: str) -> tuple[Optional[str], bool, str]:
    """(kind, strong, matched_phrase) read off the subject+snippet, or (None, False, "").

    `strong` means the phrase belongs to the distinctive family — eligible for unattended writing
    if the kind itself is (rejection/viewed/confirmation). A weak phrase only prefills review.
    """
    hay = f"{subject} {snippet}".lower()
    for kind, phrases in _KIND_PHRASES:
        for phrase in phrases:
            if phrase in hay:
                return kind, True, phrase
    for kind, phrases in _WEAK_KIND_PHRASES:
        for phrase in phrases:
            if phrase in hay:
                return kind, False, phrase
    return None, False, ""


# --------------------------------------------------------------------------------------
# Company → application
# --------------------------------------------------------------------------------------

@dataclass
class Candidate:
    """One application this mail could be about, and why."""
    job_key: str
    company: str
    title: str
    ats: Optional[str]
    score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"job_key": self.job_key, "company": self.company, "title": self.title,
                "ats": self.ats, "score": round(self.score, 3), "reasons": self.reasons}


def _tokens(text: str) -> set[str]:
    """Text reduced the same way company names are, so the two sides compare like-for-like."""
    return set(job_dedup.normalize_company(text).split())


def match_candidates(hay_text: str, applications: list[dict[str, Any]],
                     ats_id: Optional[str] = None) -> list[Candidate]:
    """Applications whose company is named in the mail, strongest first.

    `applications` rows carry {job_key, company, company_norm, title, ats}. The company must
    match nearly whole (≥ 0.75 of its identifying tokens): partial overlap on generic words is
    how "Metro Credit Union" would otherwise claim every credit union's mail. Sender-ATS
    agreement is recorded as a reason and a boost, never required — most ATS mail a tenant sends
    comes from the tenant's own address space, not the vendor's.
    """
    hay = _tokens(hay_text)
    out: list[Candidate] = []
    for app in applications:
        company_tokens = set((app.get("company_norm") or "").split())
        if not company_tokens:
            continue
        overlap = company_tokens & hay
        coverage = len(overlap) / len(company_tokens)
        if coverage < 0.75:
            continue
        cand = Candidate(job_key=app["job_key"], company=app.get("company") or "",
                         title=app.get("title") or "", ats=app.get("ats"), score=coverage,
                         reasons=[f"company tokens {sorted(overlap)} named in the mail"])
        if ats_id and app.get("ats") == ats_id:
            cand.score += 0.2
            cand.reasons.append(f"sender ATS {ats_id!r} agrees with the application's")
        if len(_tokens(app.get("title") or "") & hay) >= 2:
            cand.score += 0.1
            cand.reasons.append("title words also present")
        out.append(cand)
    out.sort(key=lambda c: -c.score)
    return out


# --------------------------------------------------------------------------------------
# The decision
# --------------------------------------------------------------------------------------

RECORD, REVIEW, IGNORE = "record", "review", "ignore"


@dataclass
class Decision:
    action: str                                  # record | review | ignore
    kind: Optional[str] = None
    job_key: Optional[str] = None
    ats_id: Optional[str] = None
    candidates: list[Candidate] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def fingerprint(row: dict[str, Any]) -> str:
    """The identity of one inbox row across sweeps. The list reader never opens a thread, so
    there is no message id — sender + subject + received timestamp is the stable surrogate."""
    address, _ = sender_address(str(row.get("sender") or ""))
    basis = "|".join([address, str(row.get("subject") or ""), str(row.get("received_at") or "")])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def decide(row: dict[str, Any], applications: list[dict[str, Any]]) -> Decision:
    """What to do with one inbox row, given the applications on file.

    Pure and side-effect free: the caller owns persistence, idempotency and the event write, so
    this can be tested against real rows without a database.
    """
    subject = str(row.get("subject") or "")
    snippet = str(row.get("snippet") or "")
    address, name = sender_address(str(row.get("sender") or ""))
    ats_id = sender_ats(address)

    kind, strong, phrase = classify_kind(subject, snippet)
    candidates = match_candidates(f"{subject} {snippet} {name}", applications, ats_id=ats_id)

    reasons: list[str] = []
    if ats_id:
        reasons.append(f"sender {address!r} is {ats_id}")
    if kind:
        reasons.append(f"{'strong' if strong else 'weak'} {kind} phrasing: {phrase!r}")

    # Nothing ties this mail to an application: not the sender, not a company, not the language.
    if not ats_id and not candidates and not (kind or _APPLICATION_WORDS.search(f"{subject} {snippet}")):
        return Decision(action=IGNORE, reasons=["no ATS sender, no company match, "
                                                "no application language"])

    unambiguous = len(candidates) == 1
    if unambiguous and kind and strong and kind not in _REVIEW_ONLY_KINDS:
        reasons.append(f"single company match: {candidates[0].company}")
        return Decision(action=RECORD, kind=kind, job_key=candidates[0].job_key,
                        ats_id=ats_id, candidates=candidates, reasons=reasons)

    # Application-related but not safe to write: surface it, prefilled with the best guess.
    if not candidates:
        reasons.append("no application's company matched")
    elif not unambiguous:
        reasons.append(f"{len(candidates)} applications match")
    if kind in _REVIEW_ONLY_KINDS:
        reasons.append("employer-response kinds always take a human glance")
    elif kind and not strong:
        reasons.append("phrasing too weak to write unattended")
    elif not kind:
        reasons.append("no recognised phrasing — kind unknown")
    return Decision(action=REVIEW, kind=kind, ats_id=ats_id,
                    job_key=candidates[0].job_key if unambiguous else None,
                    candidates=candidates, reasons=reasons)


def event_evidence(row: dict[str, Any], *, ats_id: Optional[str] = None) -> dict[str, Any]:
    """The `ApplicationEvent.evidence` payload for a gmail-sourced event. The documented shape is
    {message_id, from_address, subject}; the list reader never sees a message id, so the sweep
    fingerprint stands in as the durable reference."""
    address, name = sender_address(str(row.get("sender") or ""))
    return {
        "from_address": address, "sender_name": name,
        "subject": str(row.get("subject") or "")[:300],
        "received_at": row.get("received_at"),
        "fingerprint": fingerprint(row),
        **({"ats_id": ats_id} if ats_id else {}),
    }


def parse_received_at(row: dict[str, Any]) -> Optional[datetime]:
    """The mail's own timestamp as a datetime, or None — `occurred_at` should be when it happened
    in the world, and the reader already did the locale-aware parsing in the page."""
    stamp = row.get("received_at")
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
