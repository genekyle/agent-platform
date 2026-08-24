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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import job_dedup
from application_events import EMPLOYER_RESPONSE_KINDS

# --------------------------------------------------------------------------------------
# Sender → ATS
# --------------------------------------------------------------------------------------

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

    Delegates to `gmail_senders.classify_sender` — the ONE table both the verify leg and this
    matcher read, so the two directions cannot disagree about who a sender is (tandem seam,
    resolved at rebase as planned; this matcher's interim mail-domain table folded into
    `gmail_senders.ATS_MAIL_DOMAINS`). The shared classifier suffix-anchors the domain, so a
    lookalike like deadp.com no longer attributes to adp — and unlike the URL-side
    `classify_ats`, engine domains stay in play: mail *from indeed.com about an application* is
    exactly the attribution wanted.
    """
    import gmail_senders

    return gmail_senders.classify_sender(address)


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
        "pursue other candidates", "selected another candidate",
        "no longer under consideration", "regret to inform", "position has been filled",
        "decided not to proceed",
    ]),
    ("interview_invite", [
        "schedule an interview", "interview invitation", "invite you to interview",
        "invited to interview", "schedule your interview",
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
    # "not selected for" / "with other applicants" were strong until the review caught the
    # conditional future: "IF you are not selected for an interview, your resume will be kept on
    # file" is CONFIRMATION boilerplate, and a phrase that fires inside it must never write a
    # terminal unattended.
    ("rejection", ["unfortunately", "other candidates", "wish you the best", "not a match",
                   "not selected for", "with other applicants"]),
    # "for an interview" belongs down here with "interview": confirmation boilerplate uses it
    # conditionally ("IF you are selected for an interview…"), same trap as "not selected for".
    ("interview_invite", ["interview", "your availability", "schedule a time", "schedule time"]),
    ("recruiter_contact", ["reached out", "connect with you", "your background"]),
    ("confirmation", ["your application to", "you applied", "your recent application"]),
]

#: Kinds a matcher may propose but never write unattended — DERIVED from the employer-response
#: tier the response rate is computed from, so a kind added there inherits the human gate
#: automatically (the hand-copied set had already drifted: it was missing `offer`). Rejection is
#: the one deliberate exception: its strong formulas are distinctive automated boilerplate, argued
#: in the module docstring.
_REVIEW_ONLY_KINDS = frozenset(EMPLOYER_RESPONSE_KINDS - {"rejection"})



def classify_kind(subject: str, snippet: str) -> tuple[Optional[str], bool, str]:
    """(kind, strong, matched_phrase) read off the subject+snippet, or (None, False, "").

    `strong` means the phrase belongs to the distinctive family — eligible for unattended writing
    if the kind itself is (rejection/viewed/confirmation). A weak phrase only prefills review.

    The strong lists hold only DEFINITE, past-tense formulas on purpose: phrasing that can appear
    conditionally inside confirmation boilerplate ("IF you are not selected for an interview,
    your resume will be kept on file") lives in the weak tier, because the family order tries
    rejection first and a conditional future must never win that race unattended.
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
    there is no message id — sender + subject + received timestamp is the stable surrogate.

    The reader emits `received_at: null` exactly when Gmail's title timestamp fails Date-parse —
    and emits the raw `received_text` for that case. Fold it in as the fallback, or two distinct
    mails with the same sender and subject (recurring "your application was viewed" mail, or a
    locale change nulling EVERY date) collapse to one identity and the second is skipped forever.
    Rows with a parsed timestamp keep the exact pre-fix basis, so existing ledger fingerprints
    stay valid.
    """
    address, _ = sender_address(str(row.get("sender") or ""))
    stamp = str(row.get("received_at") or row.get("received_text") or "")
    basis = "|".join([address, str(row.get("subject") or ""), stamp])
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

    # Review needs something a human can ACT on: an application it might belong to, or an event
    # it might be. An ATS sender alone is neither — engine domains send daily job-alert digests,
    # and routing those to review persists their content and buries the queue (the review that
    # caught this measured the ignore branch as UNREACHABLE for engine mail). Same for bare
    # application words in personal mail ("how's the job hunt?"): §4 says fingerprint only. The
    # tradeoff is honest: a real outcome mail naming neither a known company nor any recognised
    # phrasing is one we could not have filed anyway.
    if not candidates and not kind:
        reasons.append("no matched application and no event phrasing"
                       + (" — ATS sender but likely an alert/digest" if ats_id else ""))
        return Decision(action=IGNORE, ats_id=ats_id, reasons=reasons)

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
    fingerprint stands in as the durable reference.

    An explicit `row["fingerprint"]` wins over recomputing: the confirm path reconstructs the
    reader row from the LEDGER, whose datetime round-trips differently than the reader emitted it
    (Z vs +00:00), so a recompute there could never match the ledger row it came from. Sweep-path
    identity is unaffected — `sweep()` fingerprints the raw reader row itself.
    """
    address, name = sender_address(str(row.get("sender") or ""))
    return {
        "from_address": address, "sender_name": name,
        "subject": str(row.get("subject") or "")[:300],
        "received_at": row.get("received_at"),
        "fingerprint": str(row.get("fingerprint") or "") or fingerprint(row),
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
