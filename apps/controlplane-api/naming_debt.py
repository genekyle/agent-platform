"""Naming debt — the ranked report of screens we keep meeting without really knowing their name.

SESSION 15 ("count the unknowns"): every unnamed screen so far was discovered by a drive tripping
over it — discovery-by-collision, no order, no end. This turns the backlog into a ranked work
queue by counting what the transition corpus already holds.

WHAT "UNNAMED" ACTUALLY LOOKS LIKE HERE, measured 2026-08-27 over the live corpus (1136 belief
halves): **zero** halves carry a null or literal-"unknown" state — the witnesses always emit their
nearest label — while **660 (58%)** are AMBIGUOUS (state uncertainty >= 0.5, a split, or novelty
at the ceiling). That is the name-borrowing failure from the retrospective in corpus form: a
missing name presents as a *confidently wrong* one, never as a blank. So this report ranks on
ambiguity and on one-name-covering-many-shapes, not on nulls.

THE SITUATION KEY IS STRUCTURAL, NOT THE FULL FINGERPRINT — and that is a deliberate trade,
written down so it can be falsified. `StateFingerprintV1` keys on the full AX set, which contains
page CONTENT (job titles on a results page), so every visit to a list page mints a fresh
fingerprint and a per-fingerprint count would drown the queue in one-offs. Here a situation is
`route_template(url)` + the CHROME subset of the AX summary: interactive roles only, names capped
at 40 chars post-normalization (content-bearing labels like "Dismiss <job title>" fall out).
Falsifier: if the top of the queue is one real screen shattered across many keys, this summary is
still too content-sensitive — tighten the name cap or drop a role, and say so in LEARNINGS. If two
genuinely different screens share a key, it is too coarse — the `split_names` section exists to
catch exactly that from the other side (one NAME whose situations diverge).

NAMING IS THE OPERATOR'S CALL. This module counts and exhibits (screenshots ride on every entry);
it never mints a state name — every prior name in this repo came from a screen someone could
describe in a sentence, and that bar stays.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from interaction import fingerprint as fp

#: Roles that are a screen's CHROME — what the page offers to do — as opposed to what it happens
#: to list today. Links and headings are excluded on purpose: on results/list pages they carry the
#: content (job titles), and a key that shifts with the content counts visits, not screens.
_CHROME_ROLES = frozenset({
    "button", "tab", "combobox", "searchbox", "textbox", "checkbox", "radio",
    "menuitem", "switch", "slider", "spinbutton",
})
#: Post-normalization length cap: chrome labels are short ("Date posted", "Easy Apply"); long
#: names are almost always content leaking through a control ("Dismiss Senior Data Analyst …").
_NAME_CAP = 40
#: Deterministic ceiling on the chrome set, so one pathological page cannot make keys unstable.
_CHROME_MAX = 60

UNNAMED = "(unnamed)"

#: A situation must recur to queue. The first live run (2026-08-27) put 300 of 302 situations in
#: the queue — nearly every half trips an ambiguity signal on undertrained witnesses, which is
#: true and useless as a WORK queue — and one-off keys on list pages are content churn, not
#: screens. Three meetings is the same bar the healthy-capture rule uses for "a screen we keep
#: meeting". One-offs are counted, never hidden.
MIN_ENCOUNTERS = 3


def _chrome_summary(candidates: list[Any]) -> list[str]:
    """The stable chrome identities of a half's candidate set. Accepts the transition row's
    [(role, name), ...] pairs (lists after JSON) or dicts, and reuses the fingerprint module's
    name normalization so this stays one vocabulary, not a second one that can drift."""
    dicts = []
    for c in candidates or []:
        if isinstance(c, dict):
            dicts.append(c)
        elif isinstance(c, (list, tuple)) and len(c) >= 2:
            dicts.append({"role": c[0], "name": c[1]})
    out = [entry for entry in fp.ax_summary(dicts)
           if entry.split("|", 1)[0] in _CHROME_ROLES and len(entry) <= _NAME_CAP + 12]
    return out[:_CHROME_MAX]


def situation_key(url: str, candidates: list[Any]) -> str:
    """A 12-hex structural key for 'this kind of screen': templated route + chrome set."""
    payload = {"route": fp.route_template(url or ""), "chrome": _chrome_summary(candidates)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def _shot_basename(raw: Optional[str]) -> Optional[str]:
    return (raw or "").replace("\\", "/").rsplit("/", 1)[-1] or None


def _half_reading(half: dict[str, Any]) -> dict[str, Any]:
    """One half of a transition row, read for naming purposes. `ambiguous` is the load-bearing
    verdict: an unassessed state axis counts as ambiguous (nothing measured the name — the
    uncertainty dict fills unassessed axes with 1.0, so the `assessed` list is the gate)."""
    belief = half.get("belief") or {}
    state = (belief.get("state") or "").strip()
    assessed = belief.get("assessed") or []
    unc = (belief.get("uncertainty") or {}).get("state")
    novelty = (belief.get("uncertainty") or {}).get("novelty")
    split = (belief.get("agreement") == "split")
    if not belief or not state or state == "unknown":
        ambiguous = True
    elif "state" not in assessed:
        ambiguous = True
    elif unc is not None and unc >= 0.5:
        ambiguous = True
    elif split or ("novelty" in assessed and novelty is not None and novelty >= 0.90):
        ambiguous = True
    else:
        ambiguous = False
    return {
        "state": state if state and state != "unknown" else UNNAMED,
        "uncertainty": unc, "ambiguous": ambiguous, "split": split,
        "url": half.get("url") or "", "candidates": half.get("candidates") or [],
        "screenshot": _shot_basename(half.get("screenshot")),
    }


def build_naming_debt(rows_by_key: dict[str, list[dict[str, Any]]],
                      *, limit: int = 25) -> dict[str, Any]:
    """Pure over already-read corpora: {corpus_key: rows} -> the ranked report."""
    situations: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    halves = named = ambiguous_halves = blank_halves = 0

    for corpus_key, rows in rows_by_key.items():
        for row in rows:
            for side in ("before", "after"):
                half = row.get(side) or {}
                if not (half.get("url") or half.get("candidates")):
                    continue
                # A blank tab is a moment mid-navigation, not a screen anyone can name; the first
                # live run ranked `about:blank` #1 in the queue. Counted, excluded from keying.
                if (half.get("url") or "").startswith("about:"):
                    blank_halves += 1
                    continue
                halves += 1
                r = _half_reading(half)
                if r["state"] != UNNAMED:
                    named += 1
                if r["ambiguous"]:
                    ambiguous_halves += 1
                key = situation_key(r["url"], r["candidates"])
                s = situations.setdefault(key, {
                    "situation": key, "encounters": 0, "ambiguous": 0, "splits": 0,
                    "called": {}, "hosts": set(), "routes": set(),
                    "first_ts": row.get("ts"), "last_ts": row.get("ts"), "exemplar": None,
                })
                s["encounters"] += 1
                s["ambiguous"] += int(r["ambiguous"])
                s["splits"] += int(r["split"])
                s["called"][r["state"]] = s["called"].get(r["state"], 0) + 1
                route = fp.route_template(r["url"])
                s["routes"].add(route)
                s["hosts"].add(route.split("/", 1)[0])
                ts = str(row.get("ts") or "")
                s["first_ts"] = min(s["first_ts"] or ts, ts) or None
                s["last_ts"] = max(s["last_ts"] or ts, ts) or None
                # exemplar: newest half that has a screenshot — the operator names SCREENS
                if r["screenshot"] and (s["exemplar"] is None or ts >= s["exemplar"]["ts"]):
                    s["exemplar"] = {"key": corpus_key, "index": row.get("index"), "half": side,
                                     "ts": ts, "url": r["url"], "state": r["state"],
                                     "uncertainty": r["uncertainty"],
                                     "screenshot": r["screenshot"]}
                n = by_name.setdefault(r["state"], {"state": r["state"], "encounters": 0,
                                                    "situations": {}, "routes": set()})
                n["encounters"] += 1
                n["routes"].add(route)
                sit = n["situations"].setdefault(key, {"situation": key, "encounters": 0,
                                                       "screenshot": None, "url": r["url"]})
                sit["encounters"] += 1
                if r["screenshot"]:
                    sit["screenshot"] = r["screenshot"]

    # THE QUEUE: recurrent situations that need a name or dispute the one they have. Ordered by
    # encounters — the top few almost certainly cover most meetings, which is the burn-down.
    queue = []
    one_offs = 0
    for s in situations.values():
        if s["encounters"] < MIN_ENCOUNTERS:
            one_offs += 1
            continue
        share = s["ambiguous"] / s["encounters"] if s["encounters"] else 0.0
        wants_attention = (share >= 0.5) or (len(s["called"]) > 1) or (UNNAMED in s["called"])
        if not wants_attention:
            continue
        queue.append({
            "situation": s["situation"], "encounters": s["encounters"],
            "ambiguous": s["ambiguous"], "ambiguous_share": round(share, 3),
            "splits": s["splits"],
            "called": sorted(({"state": k, "n": v} for k, v in s["called"].items()),
                             key=lambda e: -e["n"]),
            "hosts": sorted(s["hosts"]), "routes": sorted(s["routes"]),
            "first_ts": s["first_ts"], "last_ts": s["last_ts"], "exemplar": s["exemplar"],
        })
    queue.sort(key=lambda e: e["last_ts"] or "", reverse=True)
    queue.sort(key=lambda e: e["encounters"], reverse=True)   # stable: ties stay newest-first

    # ONE NAME, MANY SHAPES — the trap the brief names: the preferences landing classifies as
    # linkedin_job_search, not unknown, so "classifies as unknown" is too narrow a filter. A name
    # whose halves span structurally distinct situations is a name possibly covering two screens;
    # the screenshots are here so the operator can SEE whether the split is real.
    # Gated on RECURRENT situations: the first live run credited indeed_search_results with 50
    # "situations" that were mostly one-off keys minted by content churn on a list page — content
    # sensitivity is the stated falsifier of the chrome key, and recurrence is the cheap filter
    # that keeps the section honest without a smarter key. Both counts are reported.
    split_names = []
    for n in by_name.values():
        if n["state"] == UNNAMED:
            continue
        recurrent = [s for s in n["situations"].values() if s["encounters"] >= MIN_ENCOUNTERS]
        if len(recurrent) < 2:
            continue
        examples = sorted(recurrent, key=lambda e: -e["encounters"])[:3]
        split_names.append({
            "state": n["state"], "situations": len(recurrent),
            "situations_incl_one_offs": len(n["situations"]),
            "encounters": n["encounters"], "routes": sorted(n["routes"]),
            "examples": examples,
        })
    split_names.sort(key=lambda e: (-e["situations"], -e["encounters"]))

    return {
        "halves": halves, "named": named, "ambiguous_halves": ambiguous_halves,
        "blank_halves": blank_halves,
        "distinct_situations": len(situations), "one_off_situations": one_offs,
        "queue": queue[:limit], "queue_total": len(queue),
        "not_shown": max(0, len(queue) - limit),
        "split_names": split_names[:10], "split_names_total": len(split_names),
        "method": ("situation = route_template + chrome-role AX subset (names capped at "
                   f"{_NAME_CAP} chars); ambiguous = state uncertainty >= 0.5, a witness split, "
                   f"novelty >= 0.90, or an unassessed state axis; queue and split_names gated "
                   f"on >= {MIN_ENCOUNTERS} encounters — one-offs counted, not shown"),
    }


def naming_report(*, limit: int = 25) -> dict[str, Any]:
    """Read every corpus and build the report. The resolved root and row count travel in the
    payload so an empty answer can never read as a clean one (the ats_backfill lesson: a wrong
    data root looks exactly like a healthy empty corpus)."""
    import step_runner as sr

    root = sr._transitions_dir()
    rows_by_key: dict[str, list[dict[str, Any]]] = {}
    total_rows = 0
    for c in sr.list_corpora():
        rows = sr.read_transitions(c["key"], limit=1000)
        rows_by_key[c["key"]] = rows
        total_rows += len(rows)
    report = build_naming_debt(rows_by_key, limit=limit)
    report.update({
        "ok": True, "root": str(root), "corpora": len(rows_by_key), "rows": total_rows,
    })
    if total_rows == 0:
        report["note"] = ("no transition rows at this root — if that is unexpected, check "
                          "OBSERVER_ARTIFACTS_DIR (a worktree resolves the relative default to "
                          "its own empty tree)")
    return report
