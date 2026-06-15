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
from pathlib import Path
from typing import Any, Optional

from settings import settings

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
