"""Gates — the Layer-3 invariant checks that run AFTER select and BEFORE act.

A `Gate` (see `runtime.loop.Gate`) inspects a decided action against the live page
and returns a block dict to REFUSE it (the loop then escalates to a human) or None to
allow it through. Gates are what make the loop structurally unable to fire past a
blocking state no matter how confident the selector was.

The primary gate here is the captcha gate: our only eyes are CDP-AX, which cannot see a
reCAPTCHA in its iframe, so before acting we probe the capture server's
`/challenge_visibility` for a LIVE, BLOCKING challenge (a visible widget whose own token
is still empty — not fooled by an invisible enterprise preload). If one is up, we refuse
the action and hand off; a human solves it, then the run resumes. We never auto-solve.

Gates compose: `compose(g1, g2, ...)` runs them in order and the first block wins.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import httpx

from select_stage.schema import ActionId, SelectionResult

from .loop import Gate, Observation

logger = logging.getLogger("controlplane.runtime.gate")


# Accessible-name patterns for IRREVERSIBLE / consequential actions that must not fire
# autonomously without explicit operator approval (submit an application, create an
# account, send). Matches the standing rule that entering/finishing an apply needs a
# per-prospect human OK — see the apply-approval + verify-identity guidance.
_CONSEQUENTIAL_NAME = re.compile(
    r"\b(submit|send application|finish( and)? submit|confirm (and )?submit|"
    r"create account|sign\s?up|place order|send)\b", re.I)


def _target_name(result: SelectionResult, observation: Observation) -> str:
    """Accessible name of the action's target, resolved from the observation's candidates."""
    bid = result.target_backend_node_id
    if bid is None:
        return ""
    for c in observation.ax_candidates:
        cid = c.get("backend_node_id") or (c.get("_debug") or {}).get("backend_node_id")
        if cid is not None and int(cid) == int(bid):
            return (c.get("caption") or c.get("name") or "").strip()
    return ""


def consequential_gate(*, allow: bool = False) -> Optional[Gate]:
    """Refuse an irreversible action (a `submit`, or a click on a Submit/Apply-final/
    Create-Account control) unless `allow=True`, converting it into a human handoff.

    This is what keeps execute-by-default safe: the loop can drive a whole flow up to the
    consequential click, then STOP for the operator's explicit approval instead of firing
    it. Returns None when `allow=True` (transparent), so the caller opts in to autonomy."""
    if allow:
        return None

    def _gate(result: SelectionResult, observation: Observation) -> Optional[dict[str, Any]]:
        name = _target_name(result, observation)
        is_submit = result.action_id == ActionId.SUBMIT
        if is_submit or _CONSEQUENTIAL_NAME.search(name or ""):
            logger.info("consequential_gate: refusing %s on %r — needs operator approval",
                        result.action_id.value, name)
            return {
                "reason": "needs_approval",
                "target": name,
                "action": result.action_id.value,
                "guidance": "This is an irreversible action (submit / final apply / account creation). "
                            "The operator must approve it. Verify the job identity, then either do this "
                            "click by hand or resume the run with approval for this step.",
            }
        return None

    return _gate


_CAPTCHA_GUIDANCE = ("A human must solve the captcha (check the box / complete the challenge), "
                     "then resume the run. Never auto-solve.")


def probe_captcha(*, capture_server_url: str, browser_url: str,
                  tab_id: Optional[str] = None, tab_url: Optional[str] = None,
                  timeout: float = 8.0) -> Optional[dict[str, Any]]:
    """One live probe of mcp `/challenge_visibility` for a BLOCKING captcha (a visible
    challenge/checkbox whose token is unfilled — not fooled by an invisible enterprise
    preload). Returns a block dict when blocking, else None. Fail-CLOSED-to-None on any
    error (unreachable probe → no captcha claimed). Shared by the pre-act gate and the
    post-failure diagnostic."""
    url = capture_server_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{url}/challenge_visibility",
                            json={"browser_url": browser_url, "tab_id": tab_id, "tab_url": tab_url})
            r.raise_for_status()
            vis = r.json() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("captcha probe failed: %s", exc)
        return None
    if vis.get("ok") and vis.get("blocking"):
        return {"reason": "captcha", "guidance": _CAPTCHA_GUIDANCE, "visibility": vis}
    return None


def captcha_gate(*, capture_server_url: str, browser_url: str,
                 tab_id: Optional[str] = None, tab_url: Optional[str] = None,
                 timeout: float = 8.0) -> Gate:
    """Build a Gate that refuses any action while a captcha is actively blocking the tab.
    Fail-OPEN (a flaky probe allows the action through). NOTE: live runs now prefer the
    cheaper POST-FAILURE diagnostic (`probe_captcha` after N failed attempts) over this
    proactive per-step gate; this remains available for flows that want a hard pre-act stop."""
    def _gate(result: SelectionResult, observation: Observation) -> Optional[dict[str, Any]]:
        block = probe_captcha(capture_server_url=capture_server_url, browser_url=browser_url,
                              tab_id=tab_id, tab_url=tab_url, timeout=timeout)
        if block is not None:
            logger.info("captcha_gate: BLOCKING challenge present — refusing action")
        return block

    return _gate


def compose(*gates: Optional[Gate]) -> Optional[Gate]:
    """Chain gates: run in order, first block wins. Drops None gates; returns None if
    none remain (so `run_loop(gate=None)` stays the no-gate default)."""
    active = [g for g in gates if g is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _composed(result: SelectionResult, observation: Observation) -> Optional[dict[str, Any]]:
        for g in active:
            block = g(result, observation)
            if block is not None:
                return block
        return None

    return _composed
