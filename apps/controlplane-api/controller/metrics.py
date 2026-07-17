"""Shadow agreement — the one promotion number (PLAN_controller_v1 §6).

Agreement is measured on PAIRED rows: any decision journal row that carries BOTH what happened
(`intent`/`params` — the teacher's or the acted decision) AND what the controller would have
done (`proposed_intent`/`proposed_params`). Two kinds of row are paired by construction:

  - shadow rows  (M5): the controller decided silently beside the teacher; proposed = controller,
    actual = teacher. Free to collect for rung-0 shadows.
  - golden rows  (M4): a correction; proposed = the controller's rejected proposal, actual = the
    teacher's fix. A correction is a disagreement by definition — which is exactly right.

The falsifier (PLAN §6, carried verbatim into CONTROLLER_PROMOTION.md): if agreement stays flat
while corrections accumulate, the BUNDLE is missing a feature the teacher is using — the bundle
shape is the first suspect, not the model.
"""

from __future__ import annotations

from typing import Any


def _field(params: Any) -> Any:
    return (params or {}).get("field") if isinstance(params, dict) else None


def _matches(row: dict[str, Any], mode: str) -> bool:
    """Does the controller's proposal match what actually happened, at the given strictness?
    loose = same intent AND same field; exact = same intent AND identical params."""
    if row.get("intent") != row.get("proposed_intent"):
        return False
    if mode == "exact":
        return (row.get("params") or {}) == (row.get("proposed_params") or {})
    return _field(row.get("params")) == _field(row.get("proposed_params"))


def _category(row: dict[str, Any]) -> str:
    """Why a pair disagreed — the taxonomy that tells us whether to fix the bundle, the prompt,
    or the programs (Session 04's correction categories)."""
    if row.get("intent") != row.get("proposed_intent"):
        return "wrong_intent"
    if _field(row.get("params")) != _field(row.get("proposed_params")):
        return "wrong_field"
    return "wrong_params"


def _scenario_key(row: dict[str, Any]) -> str:
    ats = row.get("ats") or "?"
    state = row.get("state") or "?"
    return f"{ats}:{state}"


def shadow_agreement(rows: list[dict[str, Any]], *, match: str = "loose") -> dict[str, Any]:
    """Per-scenario agreement between the controller and the teacher over paired rows.

    Returns the overall number, the per-scenario breakdown (the promotion unit — a scenario is
    gated on its OWN agreement, never a global average), the disagreement categories, and N.
    Empty when there are no paired rows yet (an honest zero, not a fabricated 100%).
    """
    pairs = [r for r in rows if r.get("proposed_intent") is not None]
    report: dict[str, Any] = {
        "match": match, "n": len(pairs), "agree": 0, "disagree": 0, "agreement": 0.0,
        "by_scenario": [], "by_category": {},
    }
    if not pairs:
        return report

    per_scenario: dict[str, dict[str, int]] = {}
    categories: dict[str, int] = {}
    agree = 0
    for r in pairs:
        ok = _matches(r, match)
        agree += 1 if ok else 0
        key = _scenario_key(r)
        s = per_scenario.setdefault(key, {"n": 0, "agree": 0})
        s["n"] += 1
        s["agree"] += 1 if ok else 0
        if not ok:
            cat = _category(r)
            categories[cat] = categories.get(cat, 0) + 1

    report["agree"] = agree
    report["disagree"] = len(pairs) - agree
    report["agreement"] = round(agree / len(pairs), 4)
    report["by_category"] = dict(sorted(categories.items(), key=lambda kv: kv[1], reverse=True))
    report["by_scenario"] = sorted(
        ({"scenario": k, "n": v["n"], "agree": v["agree"],
          "agreement": round(v["agree"] / v["n"], 4)} for k, v in per_scenario.items()),
        key=lambda x: (-x["n"], x["scenario"]))
    return report
