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


def live_tabs(db: Session) -> list[dict]:
    """Every open page tab across all LIVE session Chromes (newest session first), each as
    {browser_url, tab_id, tab_url, title, port, session_id, domain_id}. The cockpit is filtered out."""
    out: list[dict] = []
    for s in db.scalars(select(TrainingSession).order_by(TrainingSession.created_at.desc())).all():
        port = s.chrome_debug_port
        if not port or not channel_browser.cdp_reachable(port, timeout=0.5):
            continue
        for t in _list_page_tabs(port):
            url = str(t.get("url", ""))
            if any(h in url for h in _COCKPIT_HOSTS):
                continue
            out.append({
                "browser_url": f"http://127.0.0.1:{port}", "tab_id": t.get("id"),
                "tab_url": url, "title": t.get("title", ""), "port": port,
                "session_id": s.id, "domain_id": s.domain_id,
            })
    return out


def find_tab(db: Session, url_substrings: list[str]) -> Optional[dict]:
    """The first live tab whose URL contains any of `url_substrings` (newest session first)."""
    subs = [u.lower() for u in url_substrings if u]
    for tab in live_tabs(db):
        u = tab["tab_url"].lower()
        if any(s in u for s in subs):
            return tab
    return None


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
    tab) wins; otherwise DISCOVER the account's ATS tab across live sessions (the fix for the dead
    hardcoded default). On failure returns a structured 'why not' + what live tabs DO exist, so the
    operator gets a real message instead of an opaque 'no form'."""
    if tab_id:
        return {"found": True, "browser_url": browser_url, "tab_url": None, "tab_id": tab_id,
                "explicit": True}
    subs = ats_url_substrings(account)
    disc = find_tab(db, subs)
    if disc:
        return {"found": True, "browser_url": disc["browser_url"], "tab_url": None,
                "tab_id": disc["tab_id"], "tab": disc, "explicit": False}
    return {"found": False, "looked_for": subs,
            "live_tabs": [{"url": t["tab_url"][:80], "session": t["domain_id"]} for t in live_tabs(db)]}
