"""Stop-state escalation rules — the "STOP AND INVOLVE HUMAN" gate.

Part of the CLASSIFY stage (the very first step of the per-step loop): before we
ever try to propose/select an action, check whether the current screen is one we
must hand to a human — captchas, 2FA/security checkpoints, account locks. These
are deterministic, zero-cost checks (Layer 1): a URL/text/AX-emptiness signal,
no LLM. Matching one short-circuits the whole loop straight to escalation.

Rules are seeded here and persisted to <artifacts>/cache/escalation_rules.json so
they can be edited/extended without code changes. Each rule references an example
capture (the labeled screenshot) so the eventual classifier has training data.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from settings import settings

# Challenge widgets render in their OWN iframe with a recognizable host, so even when the
# top-level page (e.g. an Indeed search-results URL) shows no captcha signal in its url/text,
# the browser's target/frame list does. This is the gap that left a reCAPTCHA'd page reading
# needs_human=false: url/text checks see only the main document, never the iframe.
#
# strength distinguishes an ACTIVE challenge (a popup/interactive frame is up → a human must
# act → hard block) from a PASSIVE widget (an invisible/enterprise badge that's on many pages
# and isn't blocking → just slow down). That split is the semi-supervised "by the book →
# proceed; real challenge → stop" line, and it avoids alert-fatigue from invisible badges.
_CHALLENGE_FRAME_PATTERNS: list[tuple[str, str, str]] = [
    # (provider, strength, regex over the frame URL)
    ("recaptcha",           "active",  r"recaptcha/(enterprise|api2)/bframe"),  # the image-challenge popup
    ("hcaptcha",            "active",  r"hcaptcha\.com/(captcha|challenge)"),
    ("cloudflare_turnstile", "active", r"challenges\.cloudflare\.com"),
    ("arkose_funcaptcha",   "active",  r"(funcaptcha|arkoselabs)\.com"),
    ("datadome",            "active",  r"datadome"),
    ("perimeterx",          "active",  r"perimeterx|/px\.js|px-cdn"),
    ("recaptcha_checkbox",  "passive", r"recaptcha/(enterprise|api2)/anchor"),  # the "I'm not a robot" widget
]


def detect_block_frames(frame_urls: Optional[list[str]]) -> Optional[dict[str, Any]]:
    """Scan the browser's full frame/target URL list for a challenge widget. Returns
    {provider, strength, url, reason} for the strongest match (active beats passive), or
    None. Deterministic, $0 — the iframe host is the signal AX and the top-level url/text
    both miss. ACTIVE = a human must solve it now (hard block); PASSIVE = a widget is present
    but not necessarily blocking (slow down, don't stop)."""
    best: Optional[dict[str, Any]] = None
    for u in frame_urls or []:
        ul = (u or "").lower()
        for provider, strength, pattern in _CHALLENGE_FRAME_PATTERNS:
            if re.search(pattern, ul):
                hit = {"provider": provider, "strength": strength, "url": (u or "")[:120],
                       "reason": f"{provider} challenge frame present "
                                 f"({'a human must solve it — never auto-solve' if strength == 'active' else 'passive widget — proceed with care'})."}
                if strength == "active":
                    return hit  # active is decisive
                best = best or hit
    return best

def downgrade_block_if_hidden(block: Optional[dict[str, Any]],
                              visibility: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Refine a detect_block_frames hit with a live visibility probe. detect_block_frames is URL-only
    ($0) and calls a reCAPTCHA bframe 'active' on mere presence — but Indeed preloads the reCAPTCHA
    Enterprise iframes invisibly on every page, so that over-triggers a human stop. `visibility` is the
    driver's /challenge_visibility result ({ok, challenge_visible, checkbox_visible, ...}). An ACTIVE
    block whose challenge/checkbox isn't actually shown is downgraded to PASSIVE (advisory). Pure +
    conservative: missing/ambiguous visibility, or anything visibly shown, stays ACTIVE. The caller
    only supplies a probe for the iframe providers we can introspect (recaptcha/hcaptcha)."""
    if not block or block.get("strength") != "active":
        return block
    if not visibility or not visibility.get("ok"):
        return block
    # Preferred signal: the probe's derived `blocking` flag (a visible challenge/checkbox whose OWN
    # token is still empty — not fooled by an invisible Enterprise scorer's passed token, and it flips
    # to not-blocking the instant the human solves it). Keep ACTIVE only while genuinely blocking.
    if "blocking" in visibility:
        if visibility.get("blocking"):
            # STAYS ACTIVE — but carry the probe, because "active" is not one thing: `blocks_typing`
            # reads it to tell a form-footer checkbox (gates submit) from a visible interstitial
            # (gates the page). Without this the reading is thrown away at the one moment it
            # distinguishes them, and every active block looks maximally blocking.
            out = dict(block)
            out["visibility"] = visibility
            return out
        out = dict(block)
        out["strength"] = "passive"
        out["reason"] = (f"{block.get('provider')} present but not blocking "
                         f"({'solved' if visibility.get('solved') else 'preloaded/hidden'}) — advisory.")
        out["visibility"] = visibility
        return out
    # Back-compat: an older probe without `blocking` — fall back to raw visibility.
    if visibility.get("challenge_visible") or visibility.get("checkbox_visible"):
        out = dict(block)
        out["visibility"] = visibility
        return out
    out = dict(block)
    out["strength"] = "passive"
    out["reason"] = (f"{block.get('provider')} preloaded but not shown "
                     f"(no visible challenge/checkbox) — advisory, not blocking.")
    out["visibility"] = visibility
    return out


def blocks_typing(block: Optional[dict[str, Any]]) -> bool:
    """Should this block stop us TYPING INTO THE PAGE, as opposed to stopping us submitting?

    Two different things wear the word "challenge", and treating them alike cost a live drive
    (2026-08-27, BambooHR): a reCAPTCHA CHECKBOX sitting in the form's footer gates the SUBMIT
    button — the fields above it are ordinary fields, and filling them is what a human does
    before ticking it. A visible CHALLENGE interstitial (the image grid) is the other thing: it
    overlays the page and demands the human's attention right now.

    The old rule refused to fill on either, which produced a race the operator had to win: the
    guard says "clear it yourself before filling", the human ticks the box, and reCAPTCHA expires
    that tick in ~2 minutes — which is exactly how the live form arrived at *"Verification
    expired. Check the checkbox again."* Filling first and ticking last is both safer and the
    order a human actually uses.

    Conservative on every uncertain path, because this is the rail that keeps us off a challenged
    page: no visibility data (an un-introspectable provider — a Facebook checkpoint, a probe that
    failed) stays blocking. Only a probe that positively reports "a checkbox is shown and no
    challenge is" opens typing.

    THIS DOES NOT RELAX SUBMIT. The apply ladder's own guard still refuses to advance on any
    active block, so a form filled under a pending checkbox still cannot be sent until a human
    ticks it. Typing is reversible; sending is not.
    """
    if not block or block.get("strength") != "active":
        return False
    vis = block.get("visibility") or {}
    if not vis.get("ok"):
        return True                       # nothing measured -> keep the conservative stop
    if vis.get("challenge_visible"):
        return True                       # an interstitial is up; the page is not ours to touch
    return not vis.get("checkbox_visible")


# Built-in seed rules. `signals` is OR-matched: any matching signal escalates.
#   url_contains      — substring(s) in the page URL
#   text_contains     — substring(s) in the page's visible text (case-insensitive)
#   max_ax_candidates — escalate if AX yielded <= N candidates AND a url/text hint
#                       also matched (AX blindness on a sensitive flow)
_SEED_RULES: list[dict[str, Any]] = [
    {
        "name": "security_checkpoint_captcha",
        "reason": "Captcha / security checkpoint — a human must solve it; the agent is blind (reCAPTCHA lives in an iframe AX can't see).",
        "signals": {
            "url_contains": ["two_step_verification", "checkpoint", "captcha", "recaptcha", "/challenge"],
            "text_contains": ["i'm not a robot", "recaptcha", "verification challenge", "confirm you're human", "are you a robot"],
            "max_ax_candidates": 1,
        },
        "example_capture": None,  # set to the labeled capture filename when seeded
    },
]


def _rules_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    path = base / "cache" / "escalation_rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_rules() -> list[dict[str, Any]]:
    """Load persisted rules, seeding the file on first use."""
    path = _rules_path()
    if not path.exists():
        path.write_text(json.dumps(_SEED_RULES, indent=2), encoding="utf-8")
        return list(_SEED_RULES)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return list(_SEED_RULES)


def set_example_capture(rule_name: str, filename: str) -> None:
    """Attach an example capture filename to a rule (the labeled stop-state)."""
    rules = load_rules()
    for r in rules:
        if r.get("name") == rule_name:
            r["example_capture"] = filename
    _rules_path().write_text(json.dumps(rules, indent=2), encoding="utf-8")


def check(*, url: str, page_text: str = "", ax_candidate_count: int = 0) -> Optional[dict[str, Any]]:
    """Return the matching stop-rule (escalate) or None (proceed). Zero cost."""
    url_l = (url or "").lower()
    text_l = (page_text or "").lower()
    for rule in load_rules():
        sig = rule.get("signals", {})
        url_hit = any(s in url_l for s in sig.get("url_contains", []))
        text_hit = any(s in text_l for s in sig.get("text_contains", []))
        max_ax = sig.get("max_ax_candidates")
        ax_hit = (max_ax is not None and ax_candidate_count <= max_ax and (url_hit or text_hit))
        if url_hit or text_hit or ax_hit:
            return {
                "rule": rule["name"],
                "reason": rule.get("reason", "stop-state matched"),
                "matched": {"url": url_hit, "text": text_hit, "ax_blind": ax_hit},
            }
    return None
