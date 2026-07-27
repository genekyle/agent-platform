"""Errands — the cross-domain detour, given a home.

An **errand** is a bounded favour one domain asks of another and then RETURNS from: Indeed's
"sign in with a code instead" needs a one-time code that only Gmail can see, so Career Search
hops to the Google provider, reads the code, and comes back to the login form it left. The shape
is a call, not a hand-off — the caller keeps its place in the queue and resumes.

Until now this existed as three disconnected declarations and no code: a tab role named `errand`
(`controller/window.py`), a goal id named `fetch_login_code` (`seed.py`), and a `code_delivery`
pointer in the `google` provider that nothing read (`providers.py`). It ran exactly once, live,
on 2026-07-10 — driven by hand — and LEARNINGS closed that entry with "STILL TODO: codify the
errand as a reusable recipe/endpoint". This module is that codification.

Three design commitments, each paid for by something we already learned:

* **Transport-agnostic.** `resolve_login_code` takes inbox ROWS, never a browser. Today those rows
  come from CDP-AX against the signed-in Google profile; the operator's stated direction is that
  Drive-family surfaces eventually move to the Google API. A resolver that takes rows doesn't care
  which one produced them, so that migration is a new reader and not a rewrite of the contract
  every other domain calls.
* **Never guess a credential.** A one-time code typed wrong is not a free retry — it burns an
  attempt and, enough times, locks the account whose loss "cascades everywhere" (the reason we
  refuse to drive Google's password page at all). So an ambiguous or unlabelled match ESCALATES.
  A `NOT_FOUND` costs one human glance; a wrong guess can cost the account.
* **Getting the code is not getting in.** 2026-07-10 again: the email code got Indeed past the
  email wall and it *then* demanded phone 2FA. `ErrandResult.expect_followup_factor` says so out
  loud, so no caller reads `status="ok"` as "authenticated".

NOT a call-stack. `PLAN_cadence_northstar.md` defers real errand SEQUENCING (a planner pushing and
popping detour frames) to v2, and this module deliberately doesn't pre-build it: one errand, called
synchronously, returning a typed result. `ErrandRequest.resume_hint` is the seam that grows into a
frame when the planner arrives — it records where the caller wants to be put back, and nothing
today reads it but the journal.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import providers

# --- outcomes ---------------------------------------------------------------------------
# Deliberately the SAME words as interaction.Outcome, because an errand fails in the same shapes a
# widget intent does and a second vocabulary for the same idea is how two subsystems drift apart.
OK = "ok"                    # a code was found, and we can prove it is the caller's and fresh
NOT_FOUND = "not_found"      # nothing matched — the mail may not have arrived yet; caller may retry
AMBIGUOUS = "ambiguous"      # >1 distinct candidate code — refuse and escalate, never pick
BLOCKED = "blocked"          # the inbox couldn't be read (signed out / challenge) — human needed

#: Statuses that mean "a human has to look at this now" rather than "try again shortly".
ESCALATING = frozenset({AMBIGUOUS, BLOCKED})


@dataclass(frozen=True)
class ErrandRequest:
    """What one domain asks another for. `requested_by` is the CALLER's domain_id — it is both the
    audit trail and the matching signal (an Indeed code comes from Indeed), which is why it is
    required rather than inferred."""

    errand_id: str                      # "fetch_login_code"
    requested_by: str                   # caller domain_id, e.g. "indeed_jobs"
    reason: str = ""                    # why, in the caller's words — journaled per PRINCIPLES §10
    params: dict[str, Any] = field(default_factory=dict)
    #: Where to put the caller back when this returns. Unread today by design — see the module
    #: docstring: this is the seam the v2 planner's call-stack grows from, not a stub of it.
    resume_hint: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class ErrandResult:
    """The typed answer. `value` is the SECRET (a login code) and is never journaled raw — the
    journal writer redacts it to its length. Callers get it; the corpus gets `[redacted:6]`."""

    status: str
    value: Optional[str] = None
    #: What the decision was made FROM — the subject line we read it out of, the sender, the
    #: timestamp. PRINCIPLES §10: a teacher decision carries its cited evidence, so a wrong answer
    #: is diagnosable later instead of being an opaque string.
    evidence: dict[str, Any] = field(default_factory=dict)
    #: Populated on every non-ok status: what a human should be told, in one sentence.
    escalation: str = ""
    #: Candidates considered and why each was rejected — the difference between "no mail arrived"
    #: and "mail arrived but we could not prove it was fresh", which need opposite responses.
    considered: list[dict[str, Any]] = field(default_factory=list)
    #: An email code gets you PAST the email wall. It does not get you in. Seen live 2026-07-10.
    expect_followup_factor: bool = True

    def as_public(self) -> dict[str, Any]:
        """The result with the secret withheld — safe for logs, the activity feed, and the UI.
        The caller that asked gets `value` off the dataclass; nothing else should ever see it."""
        out = asdict(self)
        out["value"] = None
        out["has_value"] = self.value is not None
        return out


# --- routing: the provider constant finally gets a reader ---------------------------------
def route(errand_id: str) -> Optional[dict[str, str]]:
    """Which domain + goal serves this errand, resolved from the PROVIDER group rather than a
    second table here. `providers.google.auth.code_delivery` has declared
    `{via_domain: gmail, goal: fetch_login_code}` since 2026-07-09 with nothing reading it; this is
    the reader. Routing through the provider (not a hardcoded "gmail") is what lets the answer move
    when the surface does — a Google-API-backed reader changes the member domain, not every caller.
    """
    for provider in providers.list_providers():
        delivery = (provider.get("auth") or {}).get("code_delivery") or {}
        if delivery.get("goal") == errand_id:
            return {
                "provider_id": provider["provider_id"],
                "domain_id": delivery["via_domain"],
                "goal_id": delivery["goal"],
                # The ONE shared profile every member of the provider authenticates through. This
                # is the field that makes an errand cheap: the profile is already signed in, so the
                # detour is a tab hop, not a login.
                "profile": provider.get("profile"),
            }
    return None


# --- fetch_login_code -------------------------------------------------------------------
#: Ordered most-specific first; the first hit wins. Every pattern requires a CODE KEYWORD near the
#: digits, because the alternative — "grab the longest number in the subject" — reads order
#: numbers, dollar amounts and years as credentials. A bare number is handled below: it is
#: recorded as considered-and-rejected, never returned.
#
# The gap between the keyword and the digits is `[^\d\n]{0,15}?` — anything but a digit, LAZY.
# Both halves are deliberate. Allowing non-digit filler is what lets one pattern read "code: 123456"
# and "your code is 123456", which are the two shapes Indeed alone sends. Making it lazy is what
# keeps the prefix on a prefixed code: greedy, " is G-" gets eaten as filler and "G-418302" comes
# back as "418302" — a code that looks perfectly valid, is one character short of correct, and
# fails at the login form with no clue why.
_CODE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "Sign in to Indeed with code: 123456" / "your code is 123456" / "code is G-123456"
    (re.compile(r"\bcode\b[^\d\n]{0,15}?([A-Z]{0,2}-?\d{4,8})\b", re.I), "code-labelled"),
    # "123456 is your verification code"
    (re.compile(r"\b([A-Z]{0,2}-?\d{4,8})\b[^\n]{0,24}?\bis your\b", re.I), "is-your-code"),
    # "OTP: 4821" / "passcode 908213" / "PIN 5567". Note `\bcode\b` above cannot fire inside
    # "passcode" — there is no word boundary mid-word — so these do not collide.
    (re.compile(r"\b(?:otp|passcode|pin|token)\b[^\d\n]{0,12}?(\d{4,8})\b", re.I), "otp-labelled"),
]

#: A standalone 4–8 digit run with no code keyword anywhere near it. Used ONLY to tell the operator
#: "there is a number here but nothing calls it a code" — never to answer.
_BARE_NUMBER = re.compile(r"\b\d{4,8}\b")


def extract_code(text: str) -> Optional[tuple[str, str]]:
    """Pull a one-time code out of one line of text. Returns `(code, which_pattern)` or None.

    Read the SUBJECT with this, not the body. Indeed and most senders put the code in the subject
    line, so the inbox LIST is enough and we never open the thread — fewer steps, less churn, and
    no read-receipt on a mail we only needed to glance at (LEARNINGS 2026-07-10).
    """
    for pattern, label in _CODE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(1).upper(), label
    return None


def mask_codes(text: str) -> str:
    """Replace every code-shaped token in a line of text with `[code:N]`.

    The subject line is our best evidence AND it is where the code lives — "Sign in to Indeed with
    code: 418302" cites itself. So evidence quoted anywhere durable (the errand log, the Errands
    tab, the activity feed, an escalation message a human reads over someone's shoulder) goes
    through here first. The caller that asked gets the real value off `ErrandResult.value`; the
    record of what happened keeps the sentence and loses the secret.

    This also covers the codes we REJECTED — a stale row's subject carries a real, recently-valid
    code, and "we didn't use it" is not a reason to write it down forever.
    """
    out = text or ""
    tokens: set[str] = set()
    for pattern, _ in _CODE_PATTERNS:
        tokens.update(m.group(1) for m in pattern.finditer(out))
    for token in sorted(tokens, key=len, reverse=True):
        out = out.replace(token, f"[code:{len(token)}]")
    return out


def _parse_ts(value: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 → aware datetime. Returns None rather than a guess: an unparseable
    timestamp must fail the freshness check, not silently pass it."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _matches_caller(row: dict[str, Any], sender_hints: list[str]) -> bool:
    """Is this row plausibly the mail our caller triggered? Matched on sender AND subject, because
    senders vary per ATS tenant ("no-reply@indeed.com", "indeed@match.indeed.com") while the brand
    word survives in one of the two."""
    hay = f"{row.get('sender') or ''} {row.get('subject') or ''}".lower()
    return any(hint in hay for hint in sender_hints if hint)


def _sender_hints(request: ErrandRequest) -> list[str]:
    """What to match the mail against. An explicit `sender_hint` wins; otherwise derive it from the
    caller's domain_id, stripping our registry suffix (`indeed_jobs` → `indeed`) because the
    registry id and the brand in an email address are deliberately different strings — the exact
    `indeed_jobs` vs `indeed` split that already cost us a wrong rollup once."""
    explicit = request.params.get("sender_hint")
    if explicit:
        return [str(explicit).lower()]
    stem = (request.requested_by or "").lower()
    for suffix in ("_jobs", "_marketplace", "_search"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return [stem] if stem else []


def resolve_login_code(
    request: ErrandRequest,
    rows: list[dict[str, Any]],
    *,
    requested_at: Optional[datetime] = None,
    max_age_seconds: int = 900,
) -> ErrandResult:
    """Pick the caller's one-time code out of inbox rows, or refuse and say why.

    `rows` are whatever read the inbox produced — `{sender, subject, snippet, received_at}`. Nothing
    here touches a browser, which is the point: the same function serves a CDP-AX reader today and
    a Google-API reader later.

    The freshness rule is the one that matters and the easiest to get wrong. A login-code inbox is
    FULL of old codes from previous attempts, all of them still matching every other filter. So a
    row must prove it is newer than the moment the caller asked (`requested_at`) — and a row whose
    timestamp we cannot parse FAILS that proof rather than passing it. Returning yesterday's code
    to today's form is a silent failure: the login just says "invalid" and nothing looks broken.
    """
    now = requested_at or datetime.now(timezone.utc)
    hints = _sender_hints(request)
    considered: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []

    for row in rows or []:
        subject = str(row.get("subject") or "")
        sender = str(row.get("sender") or "")
        # Everything from here on is quotable evidence, so the subject is masked ONCE, at the top,
        # before it can be copied into a note, a hit, or an escalation string. Matching still runs
        # against the raw `subject` below.
        note = {"sender": sender[:80], "subject": mask_codes(subject)[:120]}

        if hints and not _matches_caller(row, hints):
            considered.append({**note, "rejected": f"sender/subject does not mention {hints[0]!r}"})
            continue

        received = _parse_ts(row.get("received_at"))
        if received is None:
            # Not a parse nit — without a timestamp we cannot distinguish this attempt's code from
            # the last one's, and the two are indistinguishable in every other respect.
            considered.append({**note, "rejected": "no readable received_at — cannot prove freshness"})
            continue
        age = (now - received).total_seconds()
        if age > max_age_seconds:
            considered.append({**note, "rejected": f"stale — {int(age)}s old, limit {max_age_seconds}s"})
            continue
        if age < -60:
            # Clock skew beyond a minute means the two sides disagree about now; a "fresh" verdict
            # computed from disagreeing clocks isn't evidence of anything.
            considered.append({**note, "rejected": "received_at is in the future — clock skew"})
            continue

        # SUBJECT first, snippet only as a fallback — see extract_code.
        found = extract_code(subject) or extract_code(str(row.get("snippet") or ""))
        if not found:
            reason = ("contains a number but nothing labels it a code"
                      if _BARE_NUMBER.search(subject) else "no code-shaped token")
            considered.append({**note, "rejected": reason})
            continue

        code, pattern = found
        hits.append({**note, "code": code, "pattern": pattern, "received_at": received.isoformat(),
                     "age_seconds": int(age)})

    distinct = {hit["code"] for hit in hits}

    if not hits:
        return ErrandResult(
            status=NOT_FOUND,
            considered=considered,
            escalation=(f"No fresh login code for {request.requested_by} in the last "
                        f"{max_age_seconds // 60} minutes. The mail may not have arrived yet — "
                        f"retry, or check the inbox by hand."),
        )

    if len(distinct) > 1:
        # Two different codes both look valid. This happens when the caller requested a code twice
        # and only the newer one works — but "newer" among near-simultaneous mail is exactly the
        # judgement we refuse to make on a credential.
        return ErrandResult(
            status=AMBIGUOUS,
            considered=considered + hits,
            escalation=(f"{len(distinct)} different codes match {request.requested_by} in the "
                        f"freshness window. Refusing to choose — a wrong code burns a login "
                        f"attempt. Operator picks."),
        )

    best = max(hits, key=lambda h: h["received_at"])
    return ErrandResult(
        status=OK,
        value=best["code"],
        evidence={
            "sender": best["sender"],
            "subject": best["subject"],
            "received_at": best["received_at"],
            "age_seconds": best["age_seconds"],
            "matched_pattern": best["pattern"],
            "matched_on": hints[0] if hints else None,
        },
        considered=considered,
    )


def spec() -> dict[str, Any]:
    """What this errand is and what it promises — the readable contract for a calling domain, and
    what the Errands tab renders. Mirrors `recipe_spec()` on the recipe modules."""
    return {
        "errand_id": "fetch_login_code",
        "route": route("fetch_login_code"),
        "params": {
            "sender_hint": "optional — brand word to match; defaults to the caller's domain stem",
            "max_age_seconds": "optional — freshness window, default 900 (15 min)",
        },
        "returns": {
            "ok": "value = the code; evidence cites the subject line it came from",
            "not_found": "no fresh match — the caller may retry, no human needed yet",
            "ambiguous": "more than one candidate — refuses to choose, operator picks",
            "blocked": "the inbox could not be read (signed out / challenge) — human needed",
        },
        "guarantees": [
            "Reads the SUBJECT, not the thread — the inbox list is enough, so no mail is opened.",
            "Never returns a code it cannot prove is newer than the request.",
            "Never guesses: an unlabelled number is reported, not returned.",
            "The code is redacted to its length in the journal, and MASKED inside the subject line "
            "the evidence quotes — including the subjects of codes it rejected.",
            "status=ok does NOT mean authenticated; expect a follow-on factor.",
        ],
    }
