"""Interaction dialects — a site speaks ONE way, and the first widget teaches the rest.

Operator, 2026-08-11 (after the Cornerstone drive diagnosed four identical select failures one at
a time): *"once we honed in the best way to interact with the page, it then becomes that 'way'
throughout the rest of the interaction … narrow down the interaction and 'classify' it by giving
different options … without having to act like each action is truly novel."*

That is a claim about how the web is actually built, and the drives keep confirming it: a
platform renders ONE component library, so the protocol that works on its first select works on
its fifty-first. smartapply's option widgets are portal listboxes everywhere; Greenhouse's are
react-selects everywhere; Cornerstone's are native <select>s everywhere. Treating widget #2 as
novel re-pays widget #1's diagnosis for nothing.

So each (platform, widget FAMILY) carries a DIALECT — the protocol that has actually verified
there — learned on first success and offered first ever after:

    resolve order:  learned dialect  →  the classifier's verdict  →  remaining candidates,
                    cheapest-and-least-mutating first, structurally-impossible ones skipped

Trust but verify, always: a learned protocol is a PRIOR, not a license — every attempt still
confirms at the widget's own value_read_at, and a dialect that stops verifying loses its seat to
whatever wins the next cycle (sites redesign; a stale dialect must degrade to one wasted attempt,
never to wrong actions). Wins are recorded with evidence and counted, so the store doubles as a
map of which protocol each ATS speaks — recipe-shaped knowledge, learned at runtime, exactly like
ats_registry's company→ATS store.

Persisted under the capture server's data root (env-relocatable, worktree-safe). The store is
mechanism-layer state — HOW nodes are driven — so it lives beside the protocols that consume it,
not in the control plane's recipe layer.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()


def _store_path() -> Path:
    env = os.environ.get("MCP_OUTPUT_DIR")
    root = Path(env).resolve() if env else Path(__file__).resolve().parent.parent / "output"
    return root / "interaction_dialects.json"


#: Widget FAMILIES — the bucket a dialect is learned per. Coarser than WidgetType on purpose:
#: the family is the QUESTION ("how does this site do single-choice dropdowns?") and the
#: protocol is the ANSWER. One family per question keeps the store legible.
FAMILY_OPTION_SELECT = "option_select"

#: Candidate protocols per family, cheapest-and-least-mutating first. A protocol here must
#: (a) verify its own success at the widget's truth, and (b) fail CLEANLY on a widget of the
#: wrong shape — the native path refuses non-SELECT tags outright, the popup path times out
#: without staging, react_select requires its own DOM markers before typing. That clean-fail
#: property is what makes cycling safe on a real page.
CANDIDATES: dict[str, tuple[str, ...]] = {
    FAMILY_OPTION_SELECT: ("native_select", "aria_listbox", "react_select"),
}

#: Which candidates are structurally POSSIBLE given the widget's tag, when we know it. The
#: classifier usually answers first; this filter is for cycling past a wrong or missing
#: classification without attempting the impossible (a react_select keystroke dance on a bare
#: <select> mutates focus for nothing).
_TAG_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    FAMILY_OPTION_SELECT: {
        "select": ("native_select",),
        # non-select tags can be either portal shape; native is impossible.
        "_other": ("aria_listbox", "react_select"),
    },
}


def _load() -> dict[str, Any]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()) or {}
    except Exception:  # noqa: BLE001 — a corrupt store is an empty prior, never a crash
        return {}


def _save(data: dict[str, Any]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True))


#: PLATFORM IDS THAT ARE BUCKETS, NOT IDENTITIES. `company_site` is `ats_registry`'s catch-all for
#: "an employer's own careers page we do not recognise" — every unmapped site in the world shares
#: it. A real ATS id is the opposite kind of thing: it names ONE component library, which is the
#: entire premise of this module, so its dialect rightly generalises across every tenant AND every
#: engine that led there (a Workday found on LinkedIn speaks Workday).
#:
#: Keying both the same way was wrong in the direction that matters. Measured 2026-08-17: the store
#: held `company_site::option_select -> native_select` with the evidence `#areaInterest · selected
#: Information Technology` — Boston Children's BrassRing careers site — and that had become the
#: first-tried protocol for WAHVE's form, whose dropdowns are bare `<div class="dropdown-label">`
#: with no role and no <select> anywhere, where native_select cannot possibly win. One employer's
#: answer was being offered as every employer's prior.
#:
#: So a catch-all narrows to the SITE. Same rule the operator stated: label the working profile to
#: the website when there is no ATS to label it to.
_CATCH_ALL_PLATFORMS = frozenset({"", "company_site", "unknown"})


def host_of(url: str) -> str:
    """The bare host of a url, lowercased — the site identity a catch-all dialect is keyed on.

    Normalisation lives here rather than at the call sites so that the key a win is RECORDED under
    and the key it is later READ under cannot drift apart; that drift is silent and would present
    as a dialect that never seems to be learned.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "//" in raw:
        raw = raw.split("//", 1)[1]
    host = raw.split("/", 1)[0].split("?", 1)[0].split("@")[-1].split(":")[0]
    return host.lower().removeprefix("www.")


def _key(platform: str, family: str, site: str = "") -> str:
    p = (platform or "unknown").strip().lower()
    if p in _CATCH_ALL_PLATFORMS:
        h = host_of(site)
        # No host either: fall back to the old bucket rather than invent one. A shared prior is
        # bad; a key that changes shape depending on what we happened to know is worse.
        return f"site:{h}::{family}" if h else f"{p or 'unknown'}::{family}"
    return f"{p}::{family}"


def learned_protocol(platform: str, family: str, *, site: str = "") -> Optional[str]:
    """The protocol this platform's family has verified with, or None — a PRIOR, not a license."""
    with _lock:
        row = _load().get(_key(platform, family, site))
    return (row or {}).get("protocol") or None


def record_win(platform: str, family: str, protocol: str, *, evidence: str = "",
               site: str = "") -> None:
    """First verified success sets the dialect; repeats count. A DIFFERENT protocol winning
    replaces it (the site changed, or the first win was luck — either way the page outranks
    the record), with the displaced one kept in `history` so the change is on the record."""
    # A CATCH-ALL PLATFORM WITH A SITE IS STILL WORTH LEARNING, which is why this no longer
    # requires a truthy `platform`: an unmapped employer site has no ATS id and is exactly the
    # case the site key exists to serve. With neither a platform nor a site there is nothing to
    # attach the lesson to, and recording under a bucket is what this change is undoing.
    if not protocol or (not platform and not host_of(site)):
        return
    with _lock:
        data = _load()
        k = _key(platform, family, site)
        row = data.get(k) or {"platform": (platform or "").strip().lower(), "family": family,
                              "protocol": protocol, "wins": 0, "history": []}
        if host_of(site):
            row["site"] = host_of(site)
        if row.get("protocol") != protocol:
            row.setdefault("history", []).append(
                {"protocol": row.get("protocol"), "wins": row.get("wins", 0),
                 "displaced_at": datetime.now(timezone.utc).isoformat()})
            row.update({"protocol": protocol, "wins": 0})
        row["wins"] = int(row.get("wins", 0)) + 1
        row["last_win_at"] = datetime.now(timezone.utc).isoformat()
        if evidence:
            row["evidence"] = evidence[:200]
        data[k] = row
        _save(data)


def candidate_order(platform: str, family: str, *, classified: Optional[str] = None,
                    tag: Optional[str] = None, site: str = "") -> list[str]:
    """The protocols to try, in order: learned dialect → classifier's verdict → the rest,
    deduped, with structurally-impossible candidates dropped when the tag is known."""
    base = list(CANDIDATES.get(family) or ())
    allowed = None
    rules = _TAG_RULES.get(family) or {}
    if tag:
        allowed = set(rules.get(tag.strip().lower(), rules.get("_other", tuple(base))))
    order: list[str] = []
    for cand in [learned_protocol(platform, family, site=site), classified, *base]:
        if not cand or cand in order or cand not in base:
            continue
        if allowed is not None and cand not in allowed:
            continue
        order.append(cand)
    return order


def all_dialects() -> dict[str, Any]:
    """The whole store — read-only observability (the operator can see what each site speaks)."""
    with _lock:
        return _load()
