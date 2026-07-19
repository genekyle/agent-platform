"""Tab finder — the "tab manager". Locate the live browser + tab hosting a given site, across ALL
live sessions, so the account login / create-account drives target the RIGHT Chrome instead of a
hardcoded default.

Why this exists (found live 2026-07-18): the account drives defaulted to `browser_url=9322` (an old
"training Chrome" port that is usually DEAD) and `tab_url="myworkdayjobs.com"`. When the real ATS tab
lives on a DIFFERENT session's Chrome — which is exactly where the cross-site apply lands (e.g. the
`indeed` session's Chrome on 9328, holding an Indeed tab + jobs.bilh.org + bilh.wd1.myworkdayjobs.com)
— the scan hit a dead/wrong browser and "didn't see" the form, with the reason swallowed. This finds
the actual live tab by URL, across every session's Chrome.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

import channel_browser
from models import TrainingSession

# Never drive the cockpit itself, whatever port it's on.
_COCKPIT_HOSTS = ("localhost:5173", "127.0.0.1:5173", "localhost:5199", "127.0.0.1:5199")

# ATS id -> URL host substrings that identify its live tab (broad; the account's own login_url host
# is tried first and is more specific).
_ATS_HOSTS: dict[str, list[str]] = {
    "workday": ["myworkdayjobs.com"],
    "greenhouse": ["greenhouse.io", "boards.greenhouse"],
    "icims": ["icims.com"],
    "appvault": ["appvault", "careerswave"],
    "oracle": ["oraclecloud.com", "taleo.net"],
    "phenom": ["phenompeople"],
}


# Sessions where ATS account creation is ever relevant — Career-Search context ONLY (operator
# directive 2026-07-19: "look for live indeed/workday/career-search sessions only, THEN the tab").
# We never need to drive account creation from a Facebook/Gmail session, so we don't scan them.
_CAREER_SEARCH_DOMAINS = {"indeed_jobs", "indeed", "career_search"}


def _ats_ids() -> set[str]:
    try:
        import ats_registry
        return {str(a.get("ats_id")) for a in ats_registry.ATS_PLATFORMS}
    except Exception:  # noqa: BLE001
        return set()


def is_career_search_session(domain_id: Optional[str]) -> bool:
    """True for a session where the account-creation feature applies: Indeed / Career Search / an
    ATS. Port/pid/session-id churn, but the DOMAIN is stable — so we scope by domain, not port."""
    d = (domain_id or "").lower()
    if d in _CAREER_SEARCH_DOMAINS or d in _ats_ids():
        return True
    return any(k in d for k in ("indeed", "career", "workday", "greenhouse", "icims", "ats"))


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def _list_page_tabs(port: int) -> list[dict]:
    try:
        with httpx.Client(timeout=1.5) as c:
            targets = c.get(f"http://127.0.0.1:{port}/json").json()
        return [t for t in targets if t.get("type") == "page"]
    except Exception:  # noqa: BLE001
        return []


def live_tabs(db: Session, *, career_only: bool = False) -> list[dict]:
    """Every open page tab across LIVE session Chromes (newest first): {browser_url, tab_id, tab_url,
    title, port, session_id, domain_id, career_search}. Cockpit filtered out. `career_only` restricts
    to Career-Search sessions (Indeed / Career Search / ATS) — where the account feature lives."""
    out: list[dict] = []
    for s in db.scalars(select(TrainingSession).order_by(TrainingSession.created_at.desc())).all():
        port = s.chrome_debug_port
        if not port or not channel_browser.cdp_reachable(port, timeout=0.5):
            continue
        career = is_career_search_session(s.domain_id)
        if career_only and not career:
            continue
        for t in _list_page_tabs(port):
            url = str(t.get("url", ""))
            if any(h in url for h in _COCKPIT_HOSTS):
                continue
            out.append({
                "browser_url": f"http://127.0.0.1:{port}", "tab_id": t.get("id"),
                "tab_url": url, "title": t.get("title", ""), "port": port,
                "session_id": s.id, "domain_id": s.domain_id, "career_search": career,
            })
    return out


def ats_url_substrings(account: dict[str, Any]) -> list[str]:
    """Host substrings that identify the account's live ATS tab: its stored `login_url` host FIRST
    (most specific — the tenant), then the ATS platform's known domains."""
    subs: list[str] = []
    host = _host_of((account or {}).get("login_url") or "")
    if host:
        subs.append(host)
    subs.extend(_ATS_HOSTS.get((account or {}).get("ats_id") or "", []))
    # de-dupe, keep order
    seen: set[str] = set()
    return [s for s in subs if not (s in seen or seen.add(s))]


def resolve_target(db: Session, account: dict[str, Any], *, tab_id: Optional[str] = None,
                   browser_url: Optional[str] = None) -> dict[str, Any]:
    """Decide which live browser+tab a drive should scan. An explicit `tab_id` (operator picked a
    tab) wins; otherwise find the account's ATS tab in CAREER-SEARCH sessions ONLY (operator
    directive: don't scan Facebook/Gmail browsers). On failure returns a structured reason + a `hint`
    naming the career-search tabs seen (and any match sitting in a NON-career session), so the
    operator gets a real, actionable message instead of an opaque 'no form'."""
    if tab_id:
        return {"found": True, "browser_url": browser_url, "tab_url": None, "tab_id": tab_id,
                "explicit": True}
    subs = ats_url_substrings(account)
    all_tabs = live_tabs(db)   # one probe pass; each tab carries the career_search flag

    def _match(t: dict) -> bool:
        return any(s in t["tab_url"].lower() for s in subs)

    for t in all_tabs:
        if t.get("career_search") and _match(t):
            return {"found": True, "browser_url": t["browser_url"], "tab_url": None,
                    "tab_id": t["tab_id"], "tab": t, "explicit": False}

    cs = [f"{t['domain_id']}:{t['tab_url'][:55]}" for t in all_tabs if t.get("career_search")]
    elsewhere = [f"{t['domain_id']}:{t['tab_url'][:55]}" for t in all_tabs
                 if _match(t) and not t.get("career_search")]
    hint = f"Career-search tabs open: {cs or 'none'}."
    if elsewhere:
        hint += (f" The ATS tab is open in a NON-career session ({elsewhere}) — open it in the "
                 "Indeed / Career-Search browser and retry.")
    return {"found": False, "looked_for": subs, "hint": hint}
