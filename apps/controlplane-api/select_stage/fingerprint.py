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

# Bumping this re-hashes every state, so old cache entries cleanly miss and re-seed
# from live runs (the cache is a cache). Bump on any change to how the payload is built.
_FINGERPRINT_VERSION = "v4"

# Path segments that are clearly content ids, not route structure.
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_LONGHEX = re.compile(r"^[0-9a-f]{12,}$", re.I)
_DIGITS = re.compile(r"^\d+$")

# Volatile bits INSIDE an accessible name that shouldn't change a screen's identity:
# notification badges/counts ("(3)", "[12]"), currency amounts, and any bare number run
# (counts, timestamps, prices, ids baked into a label). Stripping these is what makes
# "Messages (3)" and "Messages (7)" the SAME screen so the cache stops missing on churn.
_COUNT_PAREN = re.compile(r"[(\[]\s*\d[\d.,]*\s*[)\]]")   # (3)  [12]
_CURRENCY = re.compile(r"[$€£¥]\s?\d[\d.,]*")             # $1,299.00
_NUMRUN = re.compile(r"\d[\d.,:]*")                        # any remaining number run
# Volatile STATUS words baked into top-nav/chrome accessible names — a badge or nav-active state
# that flips between visits without changing the SCREEN's identity: "Messenger, unread" vs
# "Messenger", "Your listings active" vs "Your listings", "… online". Stripping these is what
# lets a taught pick generalize across re-captures (the fingerprint stops drifting on nav chrome).
_VOLATILE_STATUS = re.compile(r"\b(?:unread|active|online|away|busy)\b")
_WS = re.compile(r"\s+")
_TRIM_CHARS = " ·•|,:-–—\t"


def _normalize_ax_name(name: str) -> str:
    """Lowercase + strip volatile tokens (counts, currency, numbers, nav status words) so the same
    control keeps one stable identity across visits. Returns '' for a purely-volatile label
    (a bare count/price), which the summary then drops."""
    s = (name or "").strip().lower()
    if not s:
        return ""
    s = _COUNT_PAREN.sub(" ", s)
    s = _CURRENCY.sub(" ", s)
    s = _NUMRUN.sub(" ", s)
    s = _VOLATILE_STATUS.sub(" ", s)
    return _WS.sub(" ", s).strip(_TRIM_CHARS)


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
    """Sorted, de-duplicated role|normalized-name identities of the candidate set
    (order-independent). Names are normalized to strip volatile tokens, and purely-volatile
    labels (bare counts/prices) are dropped — so incidental churn (a badge count ticking up,
    a new price) no longer busts the fingerprint, while the stable label set still identifies
    the screen."""
    seen: set[str] = set()
    for c in candidates:
        role = str(c.get("role", ""))
        name = _normalize_ax_name(c.get("caption") or c.get("name") or "")
        if not name:
            continue  # a bare count/number/icon carries no stable identity
        seen.add(f"{role}|{name}")
    return sorted(seen)


def dom_summary(clickables: Optional[list[dict[str, Any]]]) -> list[str]:
    """Optional secondary signal: the POSITION-FREE structural set of DOM clickables
    (de-duplicated tag|role). Absolute coordinates were dropped from the fingerprint on
    purpose — scroll and reflow move elements >100px and were busting the key on dynamic
    pages; the tag/role composition is the stable part worth keeping."""
    if not clickables:
        return []
    seen: set[str] = set()
    for el in clickables:
        tag = str(el.get("tag", ""))
        role = str(el.get("role", ""))
        seen.add(f"{tag}|{role}")
    return sorted(seen)


def compute(
    *,
    url: str,
    viewport: dict[str, Any],
    candidates: list[dict[str, Any]],
    task_goal: str,
    dom_clickables: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Stable sha256 fingerprint of the page state for cache keying."""
    # NB: the DOM/layout summary is deliberately EXCLUDED. It was an optional "sharpener", but in
    # practice it drifts on incidental layout churn (a stray tab/div appearing between visits) and
    # busted the cache on re-captures of the same screen. The role+accessible-name candidate set (ax)
    # is the real screen identity; keep the fingerprint to route + viewport + ax + goal. `dom_summary`
    # / `dom_clickables` are retained in the signature for callers but no longer keyed on.
    payload = {
        "v": _FINGERPRINT_VERSION,
        "route": route_template(url),
        "viewport": viewport_class(viewport.get("viewport_width", 0), viewport.get("viewport_height", 0)),
        "ax": ax_summary(candidates),
        "goal": (task_goal or "").strip().lower(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
