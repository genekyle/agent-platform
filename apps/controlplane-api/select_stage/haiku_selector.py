"""HaikuSelectorV1 — Layer 5, the gated catchall (migrates som_picker.py).

Numbered marks on the screenshot + numbered candidate list → Claude Haiku →
strict JSON via the FROZEN HAIKU_OUTPUT_SCHEMA. Budget-gated from the first line
(escalate on BudgetExceededError), prompt-cached system prompt, usage logged.
Returns the model's pick mapped back to a Candidate; the orchestrator turns it
into a SelectionResult.

Model: claude-haiku-4-5 (no date suffix), vision + structured outputs.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import json
import logging
from pathlib import Path
from typing import Any, Optional

import anthropic

import anthropic_usage

from .schema import HAIKU_OUTPUT_SCHEMA, ActionId, Candidate, ReasonCode, resolve_mark

logger = logging.getLogger("controlplane.select.haiku")

MODEL = "claude-haiku-4-5"
MAX_MARKS = 30

_INTERACTIVE = frozenset({
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "switch", "menuitem", "tab", "option", "slider", "spinbutton",
})

# Stable system prompt — marked cacheable (kept byte-identical across calls).
_SYSTEM = (
    "You are a precise UI grounding assistant. You see a screenshot with numbered "
    "red marks tagging candidate UI elements, plus a numbered list (role + name). "
    "Given a target/goal, choose the single best-matching numbered candidate and "
    "the action to take on it. Reply ONLY via the schema: `mark` = candidate "
    "number (0 if none match), `action_id` = the action (click/type/select/scroll/"
    "submit/clear/none), `confidence` 0.0-1.0, `needs_human` = true if you are "
    "unsure or the screen looks like a captcha/checkpoint, `reason_code` = one of "
    "som_pick/low_confidence/no_match/unknown_state."
)


def _prioritize(candidates: list[Candidate], width: int, height: int) -> list[Candidate]:
    def key(c: Candidate) -> tuple:
        b = c.bbox or {}
        x, y, w, h = (float(b.get(k, 0)) for k in ("x", "y", "width", "height"))
        on_screen = 0 <= x < width and 0 <= y < height and w > 0 and h > 0
        return (on_screen, c.role in _INTERACTIVE)
    return sorted(candidates, key=key, reverse=True)


def _draw_marks(screenshot_path: Path, candidates: list[Candidate]) -> str:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=22)
    except Exception:
        font = ImageFont.load_default()
    for c in candidates:
        b = c.bbox or {}
        x, y = float(b.get("x", 0)), float(b.get("y", 0))
        w, h = float(b.get("width", 0)), float(b.get("height", 0))
        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=2)
        by = max(0, y - 24)
        draw.rectangle([x, by, x + 26, by + 24], fill=(255, 0, 0))
        draw.text((x + 3, by + 1), str(c.mark), fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def _lines(candidates: list[Candidate]) -> str:
    rows = []
    for c in candidates:
        name = c.name or "(no name)"
        rows.append(f"{c.mark}. role={c.role or '?'} name={name!r}")
    return "\n".join(rows)


def pick(
    *,
    screenshot_path: str | Path,
    candidates: list[Candidate],
    task_goal: str,
    budget_limit: Optional[float] = None,
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Run the Haiku pick. Raises anthropic_usage.BudgetExceededError when over
    budget (orchestrator escalates). Returns:
        {action_id, candidate, confidence, needs_human, reason_code, cost_usd, mark}
    """
    path = Path(screenshot_path)
    if not candidates:
        return {"action_id": ActionId.NONE, "candidate": None, "confidence": 0.0,
                "needs_human": True, "reason_code": ReasonCode.NO_MATCH, "cost_usd": 0.0, "mark": 0}

    anthropic_usage.enforce_budget(budget_limit)  # BUDGET GATE — before any spend

    from PIL import Image
    with Image.open(path) as _img:
        width, height = _img.size
    # Prioritize on-screen + interactive, cap, then RE-NUMBER 1..N so that the
    # mark drawn/shown == the list position resolve_mark() uses (prioritize
    # reorders, so the original AX-order marks would no longer line up).
    marked = [dataclasses.replace(c, mark=i)
              for i, c in enumerate(_prioritize(candidates, width, height)[:MAX_MARKS], start=1)]

    img_b64 = _draw_marks(path, marked)
    user_text = (
        f"Goal / target: {task_goal!r}\n\nNumbered candidates:\n{_lines(marked)}\n\n"
        "Which number is the target, and what action? Use mark 0 if none match."
    )
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=80,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
            {"type": "text", "text": user_text},
        ]}],
        output_config={"format": {"type": "json_schema", "schema": HAIKU_OUTPUT_SCHEMA}},
    )
    row = anthropic_usage.record_from_response(resp, purpose="som_pick", meta=meta)

    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "{}")
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = {}
    mark = int(parsed.get("mark", 0) or 0)
    candidate = resolve_mark(mark, marked)
    try:
        action_id = ActionId(parsed.get("action_id", "click"))
    except ValueError:
        action_id = ActionId.CLICK
    try:
        reason_code = ReasonCode(parsed.get("reason_code", "som_pick"))
    except ValueError:
        reason_code = ReasonCode.SOM_PICK

    logger.info("haiku pick: goal=%r mark=%d action=%s conf=%s cost=$%.6f",
                task_goal, mark, action_id.value, parsed.get("confidence"), row["cost_usd"])
    return {
        "action_id": action_id,
        "candidate": candidate,
        "confidence": float(parsed.get("confidence", 0.0) or 0.0),
        "needs_human": bool(parsed.get("needs_human", False)),
        "reason_code": reason_code,
        "cost_usd": row["cost_usd"],
        "mark": mark,
    }
