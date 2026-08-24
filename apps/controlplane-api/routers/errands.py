"""Errand routes — the seam another domain calls when it needs a favour from this one.

This is the "open and usable by other domains" half of the Google work. `errands.py` holds the
rules and `gmail_recipe.py` the navigation; this exposes them as something a Career Search drive
can call mid-flow without knowing anything about Gmail — it names the errand, says who is asking,
and gets back a typed result.

The route is journaled through the SAME intent journal every other action uses
(`interaction.journal.log_intent`), not a private log. Reusing it is the point: an errand that
writes somewhere only an errand reads is the event-log mistake again — a record no trainer will
ever see. Journaled as `OBSERVE`, because that is honestly what this is: one structured
observation of a page, which is exactly what the existing intent vocabulary already has a word for.
Adding an eighth verb for "read an inbox" would split the vocabulary for no gain.

`errand_log` sits alongside it and answers the operator's question rather than the trainer's: who
asked, for what, and did it need a human.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

import errand_log
import errands
import gmail_recipe
from settings import settings

router = APIRouter()


class FetchLoginCodeRequest(BaseModel):
    #: The CALLER's domain_id. Required, not inferred: it is both the audit trail and the matching
    #: signal (an Indeed code comes from Indeed), and a default here would quietly match the wrong
    #: sender's mail.
    requested_by: str
    reason: str = ""
    #: Optional brand word to match on. Defaults to the caller's domain stem — `indeed_jobs` becomes
    #: `indeed`, because the registry id and the brand in an email address are deliberately
    #: different strings.
    sender_hint: Optional[str] = None
    #: Optional LIST of hints — domains and a company phrase — for callers whose mail brand is not
    #: their domain_id at all. The account rung's verification mail comes from the ATS's mail
    #: domain ("Boston Children's Hospital on BrassRing" → `@trm.brassring.com`), which no stem of
    #: any domain_id produces; derive these with `gmail_senders.senders_for(ats, company)`. Wins
    #: over `sender_hint` when both are given.
    sender_hints: Optional[list[str]] = None
    #: Freshness window. 15 minutes by default: most one-time codes expire inside 10.
    max_age_seconds: int = 900
    #: Which browser holds the signed-in Google profile. The errand is a tab hop, not a login —
    #: the provider's shared profile is already authenticated.
    browser_url: str = "http://127.0.0.1:9222"
    tab_id: Optional[str] = None
    tab_url: str = "mail.google.com"
    #: Where the caller wants to be put back. Recorded, not acted on — the seam a v2 planner's
    #: call-stack grows from (see errands.py).
    resume_hint: Optional[dict[str, Any]] = None


async def _capture_post(path: str, payload: dict, timeout: float = 30.0) -> dict:
    """POST to the capture server. Never raises — an unreachable browser is an honest
    `{ok: false}` the caller can act on, not a 500 it has to decode."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{settings.capture_server_url}{path}", json=payload)
            return response.json() or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _journal(result: "errands.ErrandResult", request: FetchLoginCodeRequest,
             *, url: str, duration_ms: int) -> None:
    """One journal row per errand. `value` goes through the journal's own redaction, which keeps
    the LENGTH and nothing else — the corpus learns "a 6-digit code was found here", which is the
    part that generalises, and never the code."""
    try:
        from interaction.contract import Intent, Outcome
        from interaction.journal import log_intent

        outcome = {
            errands.OK: Outcome.OK,
            errands.NOT_FOUND: Outcome.NOT_FOUND,
            errands.AMBIGUOUS: Outcome.AMBIGUOUS,
            errands.BLOCKED: Outcome.BLOCKED,
        }.get(result.status, Outcome.ERROR)

        log_intent(
            intent=Intent.OBSERVE,
            outcome=outcome,
            field="login_code",
            value=result.value,
            sensitive=True,          # forced, never inferred from the field name
            detail=f"errand fetch_login_code for {request.requested_by}: "
                   f"{result.escalation or 'code read from the inbox list'}",
            addressed_by="role_name",
            target="gmail:inbox_list",
            url=url,
            executed=True,
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001 — a journal write must never break the drive waiting on us
        pass


@router.post("/api/errands/fetch_login_code")
async def fetch_login_code(body: FetchLoginCodeRequest):
    """Read a one-time sign-in code out of the shared Google profile's inbox, for another domain.

    Returns the caller's typed result. `status` is one of ok / not_found / ambiguous / blocked, and
    the distinction that matters is between the first two and the last two: **not_found is
    retryable** (the mail may simply not have landed yet, no human needed), while ambiguous and
    blocked mean a drive is parked until someone looks.

    `status="ok"` does NOT mean the caller is authenticated. On 2026-07-10 this exact errand got
    Indeed past its email wall and Indeed then demanded phone 2FA — `expect_followup_factor` says
    so in the payload so no caller can read past it.
    """
    started = time.monotonic()
    request = errands.ErrandRequest(
        errand_id="fetch_login_code",
        requested_by=body.requested_by,
        reason=body.reason,
        params={"sender_hint": body.sender_hint, "sender_hints": body.sender_hints,
                "max_age_seconds": body.max_age_seconds},
        resume_hint=body.resume_hint,
    )

    route = errands.route("fetch_login_code")
    read = await _capture_post("/read_inbox", {
        "browser_url": body.browser_url,
        "tab_id": body.tab_id,
        "tab_url": body.tab_url,
    })

    url = read.get("url") or ""

    # BLOCKED covers three different ways the read can fail to be evidence, and all three need a
    # human rather than a retry: the browser is unreachable, the profile is signed out, or the list
    # itself could not be found (a stale tab, or a layout we do not know). The last one is the
    # important one — a reader that returns [] for "I could not find the list" is indistinguishable
    # from "there is no mail", and that silent-empty is the failure mode this whole codebase keeps
    # relearning. See the LinkedIn virtualised-list undercount.
    blocked_reason = None
    if not read.get("ok"):
        blocked_reason = (f"Could not read the inbox: {read.get('detail') or 'unknown error'}. "
                          f"Is the shared '{(route or {}).get('profile')}' browser running?")
    elif read.get("signed_in") is False:
        blocked_reason = ("The shared Google profile is signed out. One supervised sign-in "
                          "unblocks every Google surface at once — the agent never types this "
                          "password.")
    elif not read.get("list_found"):
        blocked_reason = ("Reached the tab but could not find the inbox list — a stale tab, or a "
                          "layout we do not know. Not reporting this as 'no mail'.")

    if blocked_reason:
        result = errands.ErrandResult(status=errands.BLOCKED, escalation=blocked_reason)
    else:
        result = errands.resolve_login_code(
            request,
            read.get("rows") or [],
            # The BROWSER's clock, not ours. The freshness window is a comparison between the mail's
            # timestamp and now, and both should come from the same machine — if the capture server
            # ever moves off this host, comparing its timestamps against our clock silently skews
            # every verdict.
            requested_at=_read_clock(read),
            max_age_seconds=body.max_age_seconds,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    _journal(result, body, url=url, duration_ms=duration_ms)
    record = errand_log.record(request, result, duration_ms=duration_ms)

    # The CALLER gets the code — it is the whole reason it asked. Everything else about this errand
    # (the log, the tile, the Errands tab, the activity feed) reads the public view.
    return {
        "ok": result.status == errands.OK,
        "status": result.status,
        "code": result.value,
        "expect_followup_factor": result.expect_followup_factor,
        "escalation": result.escalation,
        "evidence": result.evidence,
        "considered": result.considered,
        "route": route,
        "recorded_at": record["ts"],
        "where": gmail_recipe.describe_tab(url),
    }


def _read_clock(read: dict) -> datetime:
    """The moment the inbox was read, per the browser that read it. Falls back to our clock only if
    the reader gave us nothing parseable."""
    stamp = read.get("read_at")
    if isinstance(stamp, str) and stamp.strip():
        try:
            parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


@router.get("/api/errands")
def list_errands(limit: int = 50, requested_by: Optional[str] = None):
    """The errand history — what other domains asked for and what came back. Never carries a code."""
    return {
        "errands": errand_log.recent(limit=limit, requested_by=requested_by),
        "stats": errand_log.recent_stats(),
    }


@router.get("/api/errands/catalog")
def errand_catalog():
    """What errands exist, who serves them, and what they promise. The readable contract for a
    calling domain — and what the Gmail workspace's Errands tab renders when nothing has run yet."""
    return {"errands": [errands.spec()]}
