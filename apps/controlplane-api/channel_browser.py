"""Channel browser supervision — one persistent, health-checked browser per sales channel.

An agent shouldn't re-launch or re-discover a browser on every action. Each channel (Facebook
today) gets ONE persistent Chrome profile that we ATTACH to when it's healthy and only relaunch
when it's actually dead. "Healthy" means its CDP endpoint answers — NOT merely that we once
recorded a process id. Trusting a stale pid is exactly what made "launch" hand back a dead browser
and feel impossible to connect to; the reachability check is the fix.

This module holds the pure, testable parts: the per-channel config and the CDP reachability probe.
The db-touching attach/relaunch logic lives in main.py (`_ensure_channel_browser`) where the Chrome
launch helpers and the session model already are.
"""

from __future__ import annotations

from typing import Optional

import httpx

# Per-channel config. `profile` is the persistent Chrome user-data-dir name; `home` is where the
# browser sits idle + where the login form lives; `tab_url` is the substring used to pick the tab
# to drive; `login_path` is the mcp driver that fills THIS channel's login form.
CHANNELS: dict[str, dict] = {
    "facebook_marketplace": {
        "profile": "facebook",
        "home": "https://www.facebook.com/",
        "tab_url": "facebook.com",
        "domain_id": "facebook_marketplace",
        "login_path": "/facebook_login",
        "label": "Facebook Marketplace",
    },
}


def channel_config(channel: str) -> Optional[dict]:
    return CHANNELS.get(channel)


def cdp_reachable(port: Optional[int], timeout: float = 1.5) -> bool:
    """True iff a Chrome DevTools endpoint actually answers on `port` — the honest "is the browser
    alive and driveable" check. A recorded pid can be stale (window closed, crash, reload); the CDP
    /json/version probe never lies."""
    if not port:
        return False
    try:
        with httpx.Client(timeout=timeout) as client:
            return client.get(f"http://127.0.0.1:{port}/json/version").status_code == 200
    except Exception:
        return False
