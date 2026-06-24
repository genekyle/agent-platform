"""HaikuPageStateV1 — the page-state TEACHER (L3 distillation source).

Given a screenshot + the domain's known page-states (id, name, description), Claude
Haiku classifies WHICH named state the page is in (or flags it as a new/unknown state).
This is the L3 analogue of the Haiku selector: a cheap teacher whose labels grow the
`observed_page_state` corpus that the coarse stage-observer and the fine page-state
classifier distill from — so every Haiku rep also teaches "where am I?", not just
"what do I click?".

Budget-gated from the first line (same rolling cap as the selector), prompt-cached
system prompt, usage logged with purpose="page_state". Same model + structured-output
discipline as haiku_selector. `client` is injectable so it's testable without the API.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any, Optional

import anthropic

import anthropic_usage

logger = logging.getLogger("controlplane.select.haiku_page_state")

MODEL = "claude-haiku-4-5"
MAX_STATES = 40  # cap the candidate-state menu we show the model

# Frozen structured-output contract — keep byte-stable (mirrors HAIKU_OUTPUT_SCHEMA).
HAIKU_PAGE_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "state_id": {"type": "string"},          # exact id from the menu, or "" if none fit
        "confidence": {"type": "number"},
        "needs_human": {"type": "boolean"},
        "is_new": {"type": "boolean"},            # true ⇒ none of the listed states match
        "proposed_name": {"type": "string"},      # short slug suggestion when is_new
    },
    "required": ["state_id", "confidence", "needs_human", "is_new"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You identify which named page-state a web screenshot is in. You are given a "
    "screenshot and a numbered menu of candidate states (id, name, description) for "
    "this site. Pick the SINGLE state_id whose description best matches what the "
    "screenshot actually shows. Reply ONLY via the schema: `state_id` = the exact id "
    "from the menu (empty string if none fit), `confidence` 0.0-1.0, `needs_human` = "
    "true if the screen is ambiguous or looks like a captcha/checkpoint, `is_new` = "
    "true if NONE of the listed states match (then give a short snake_case "
    "`proposed_name`). Judge by what is visibly on screen, not by the URL alone."
)


def _state_menu(candidate_states: list[dict[str, Any]]) -> str:
    rows = []
    for i, s in enumerate(candidate_states[:MAX_STATES], start=1):
        name = s.get("display_name") or s.get("state_id") or "?"
        desc = (s.get("description") or "").strip()
        desc = f" — {desc}" if desc else ""
        rows.append(f"{i}. id={s.get('state_id')!r} name={name!r}{desc}")
    return "\n".join(rows)


def _encode(screenshot_path: Path) -> str:
    from PIL import Image
    img = Image.open(screenshot_path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def classify(
    *,
    screenshot_path: str | Path,
    candidate_states: list[dict[str, Any]],
    url: str = "",
    page_text: str = "",
    budget_limit: Optional[float] = None,
    meta: Optional[dict[str, Any]] = None,
    client: Optional[Any] = None,
) -> dict[str, Any]:
    """Classify the page-state. Raises anthropic_usage.BudgetExceededError when over
    budget. Returns {state_id, confidence, needs_human, is_new, proposed_name, cost_usd}.
    `state_id` is "" when the model abstains; `is_new` flags a genuinely unseen state."""
    path = Path(screenshot_path)
    if not candidate_states:
        return {"state_id": "", "confidence": 0.0, "needs_human": True,
                "is_new": True, "proposed_name": "", "cost_usd": 0.0}

    anthropic_usage.enforce_budget(budget_limit)  # BUDGET GATE — before any spend

    img_b64 = _encode(path)
    snippet = (page_text or "")[:600]
    user_text = (
        f"URL: {url}\n\nCandidate states for this site:\n{_state_menu(candidate_states)}\n\n"
        + (f"Visible page text (truncated):\n{snippet}\n\n" if snippet else "")
        + "Which state_id does the screenshot show? Use is_new=true if none match."
    )
    client = client or anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=80,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text", "text": user_text},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": HAIKU_PAGE_STATE_SCHEMA}},
    )
    row = anthropic_usage.record_from_response(resp, purpose="page_state", meta=meta)

    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "{}")
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = {}

    valid_ids = {s.get("state_id") for s in candidate_states}
    state_id = parsed.get("state_id") or ""
    is_new = bool(parsed.get("is_new", False))
    # Guard against a hallucinated id that isn't in the menu.
    if state_id and state_id not in valid_ids:
        is_new = True
        proposed = parsed.get("proposed_name") or state_id
        state_id = ""
    else:
        proposed = parsed.get("proposed_name") or ""

    logger.info("haiku page_state: url=%s -> id=%r conf=%s is_new=%s cost=$%.6f",
                url, state_id, parsed.get("confidence"), is_new, row["cost_usd"])
    return {
        "state_id": state_id,
        "confidence": float(parsed.get("confidence", 0.0) or 0.0),
        "needs_human": bool(parsed.get("needs_human", False)),
        "is_new": is_new,
        "proposed_name": proposed,
        "cost_usd": row["cost_usd"],
    }
