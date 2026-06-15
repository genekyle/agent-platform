"""StateFingerprintV1 — a stable, reusable signature of a page state.

The fingerprint is the cache key's backbone: two visits to "the same screen"
must produce the same fingerprint so the cache can reuse a prior pick without
paying Haiku again. We template dynamic URL segments (so /item/123 and /item/456
fingerprint identically), bucket the viewport (so a 1px resize doesn't bust it),
and summarize the candidate set by stable identity (role+name), not coordinates.

Prefer a compact partial-AX summary over a full-tree dump — cheaper and more
stable. DOM/layout summary is optional and only sharpens the fingerprint when
present.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional
from urllib.parse import urlsplit

# Path segments that are clearly content ids, not route structure.
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_LONGHEX = re.compile(r"^[0-9a-f]{12,}$", re.I)
_DIGITS = re.compile(r"^\d+$")


def _template_segment(seg: str) -> str:
    if not seg:
        return seg
    if _DIGITS.match(seg) or _UUID.match(seg) or _LONGHEX.match(seg):
        return "{id}"
    # mixed token with a long digit run (e.g. "post_8839201") -> templated tail
    if re.search(r"\d{5,}", seg):
        return re.sub(r"\d{5,}", "{id}", seg)
    return seg


def route_template(url: str) -> str:
    """host + path with dynamic segments templated; query/fragment dropped."""
    parts = urlsplit(url or "")
    host = (parts.netloc or "").lower()
    segs = [_template_segment(s) for s in parts.path.split("/")]
    path = "/".join(segs).rstrip("/") or "/"
    return f"{host}{path}"


def viewport_class(width: int, height: int) -> str:
    """Bucket viewport width (a resize within a bucket won't change the fp)."""
    w = int(width or 0)
    if w < 768:
        bucket = "sm"
    elif w < 1280:
        bucket = "md"
    elif w < 1920:
        bucket = "lg"
    else:
        bucket = "xl"
    orient = "portrait" if int(height or 0) > w else "landscape"
    return f"{bucket}-{orient}"


def ax_summary(candidates: list[dict[str, Any]]) -> list[str]:
    """Sorted role|name identities of the candidate set (order-independent)."""
    out = []
    for c in candidates:
        role = str(c.get("role", ""))
        name = (c.get("caption") or c.get("name") or "").strip()
        out.append(f"{role}|{name}")
    return sorted(out)


def dom_summary(clickables: Optional[list[dict[str, Any]]]) -> list[str]:
    """Optional: compact summary of DOM clickable nodes (tag|role|coarse-pos).
    Coarse 100px position bucket keeps it stable against minor layout shifts."""
    if not clickables:
        return []
    out = []
    for el in clickables:
        tag = str(el.get("tag", ""))
        role = str(el.get("role", ""))
        rect = el.get("rect") or el.get("bbox") or {}
        gx = int(float(rect.get("x", 0)) // 100) if rect else 0
        gy = int(float(rect.get("y", 0)) // 100) if rect else 0
        out.append(f"{tag}|{role}|{gx},{gy}")
    return sorted(out)


def compute(
    *,
    url: str,
    viewport: dict[str, Any],
    candidates: list[dict[str, Any]],
    task_goal: str,
    dom_clickables: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Stable sha256 fingerprint of the page state for cache keying."""
    payload = {
        "route": route_template(url),
        "viewport": viewport_class(viewport.get("viewport_width", 0), viewport.get("viewport_height", 0)),
        "ax": ax_summary(candidates),
        "dom": dom_summary(dom_clickables),
        "goal": (task_goal or "").strip().lower(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
