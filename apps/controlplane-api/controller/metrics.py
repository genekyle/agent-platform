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

#: The promotion bars (CONTROLLER_PROMOTION.md). TWO of them, both required, because `loose` is
#: INTENT-ONLY FOR EVERY CLICK DECISION by construction: a click's params carry `control`, not
#: `field`, so `_field()` returns None on both sides and a proposal to click "A" scores as
#: agreement against a teacher who clicked something else entirely (measured 2026-08-22; pinned in
#: `test_controller_metrics.py`). A scenario could therefore clear a loose-only gate while reaching
#: for the wrong button every time — and `open_pane`/`enter_apply`, the phases the rail is most
#: confident about, are exactly those click-shaped turns.
#:
#: `loose` is deliberately left untouched as the headline number so it stays comparable with every
#: figure recorded before this (08-06, 08-22). `exact` is the second bar, set lower because it is a
#: strictly harder test — it compares full params after value-ref resolution, so it also fails on a
#: right-button-different-phrasing miss that nobody would call a defect.
#:
#: OPERATOR-TUNABLE. These are named constants, not literals buried in a comparison, precisely so
#: retuning is a one-line, reviewable change.
PROMOTION_LOOSE_BAR = 0.90
PROMOTION_EXACT_BAR = 0.85
PROMOTION_MIN_N = 25

#: How many rows must be able to TESTIFY about control choice before the `exact` bar means
#: anything. Without it the bar is decidable by a single observation: measured 2026-08-22,
#: `indeed_quick_apply:indeed_job_posting` had 67 pairs and **exact_n = 1**, so one lucky row
#: would have read as exact = 1.000 and carried the scenario through a gate it has no evidence
#: for. Same window size as `PROMOTION_MIN_N` by default — the exact bar deserves its own 25, not
#: a borrowed one.
#:
#: That this is currently unreachable on our best scenario is a TRUE statement about our evidence,
#: and it names its own remedy: the crank seam does not journal the control it clicked on most
#: rungs (131 of 294 pairs carry no teacher params), so the fix is upstream in what gets recorded,
#: not in this number.
PROMOTION_MIN_EXACT_N = 25


def _field(params: Any) -> Any:
    return (params or {}).get("field") if isinstance(params, dict) else None


def _norm_param(v: Any) -> Any:
    """Case- and whitespace-fold a param value for the `exact` comparison.

    NOT a weakening of the bar — the two sides are two RENDERINGS of one name. The controller
    reads `role|name` AX identities, which the interaction layer normalises to lowercase; the
    teacher records the label as the page renders it. Measured 2026-08-22: of 87 value-differences
    on intent-matching pairs, **72 were case-only** (`'Continue'` vs `'continue'`). Comparing raw
    strings would make `exact` a test of string formatting rather than of which control was
    chosen, and the bar built on it would be unpassable for a reason nobody could act on.
    """
    return " ".join(str(v).split()).lower() if isinstance(v, str) else v


def _params_agree(teacher: Any, proposed: Any) -> bool:
    t = {k: _norm_param(v) for k, v in (teacher or {}).items()}
    p = {k: _norm_param(v) for k, v in (proposed or {}).items()}
    return t == p


def _has_no_param_claim(row: dict[str, Any]) -> bool:
    """Did the TEACHER side record no params while the proposal named some?

    Such a row cannot testify about control choice: there is nothing to compare against. It comes
    from the crank seam, which synthesises the teacher Decision from `_RUNG_INTENT` when the acted
    step carried no params — 61 of 294 pairs on 2026-08-22. Counting them as `exact` misses would
    blame the controller for a JOURNALING gap, and counting them as hits would flatter it, so they
    are excluded from the `exact` denominator and reported (`exact_unscoreable`) rather than
    silently dropped. Same principle as `step_runner.verify`'s "BLIND IS NOT WRONG": an
    observation that saw nothing does not get to testify either way.
    """
    return not (row.get("params") or {}) and bool(row.get("proposed_params") or {})


def _matches(row: dict[str, Any], mode: str) -> bool:
    """Does the controller's proposal match what actually happened, at the given strictness?
    loose = same intent AND same field; exact = same intent AND the same params, compared
    case/whitespace-insensitively (see `_norm_param`)."""
    if row.get("intent") != row.get("proposed_intent"):
        return False
    if mode == "exact":
        return _params_agree(row.get("params"), row.get("proposed_params"))
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
        "loose_agreement": 0.0, "exact_agreement": 0.0,
        "by_scenario": [], "by_category": {},
        "bars": {"loose": PROMOTION_LOOSE_BAR, "exact": PROMOTION_EXACT_BAR,
                 "min_n": PROMOTION_MIN_N, "min_exact_n": PROMOTION_MIN_EXACT_N},
        "eligible": [],
    }
    if not pairs:
        return report

    per_scenario: dict[str, dict[str, int]] = {}
    categories: dict[str, int] = {}
    agree = loose_agree = exact_agree = exact_n = unscoreable = 0
    for r in pairs:
        ok = _matches(r, match)
        # BOTH bars are computed on every call regardless of `match`, so a caller can never be
        # handed one number and mistake it for the gate. `agreement` keeps meaning whatever mode
        # was asked for (unchanged); `loose_agreement`/`exact_agreement` are unambiguous.
        ok_loose = _matches(r, "loose")
        scoreable = not _has_no_param_claim(r)
        ok_exact = _matches(r, "exact") if scoreable else False
        agree += 1 if ok else 0
        loose_agree += 1 if ok_loose else 0
        exact_agree += 1 if ok_exact else 0
        exact_n += 1 if scoreable else 0
        unscoreable += 0 if scoreable else 1
        key = _scenario_key(r)
        s = per_scenario.setdefault(key, {"n": 0, "agree": 0, "loose": 0,
                                          "exact": 0, "exact_n": 0})
        s["n"] += 1
        s["agree"] += 1 if ok else 0
        s["loose"] += 1 if ok_loose else 0
        s["exact"] += 1 if ok_exact else 0
        s["exact_n"] += 1 if scoreable else 0
        if not ok:
            cat = _category(r)
            categories[cat] = categories.get(cat, 0) + 1

    report["agree"] = agree
    report["disagree"] = len(pairs) - agree
    report["agreement"] = round(agree / len(pairs), 4)
    report["loose_agreement"] = round(loose_agree / len(pairs), 4)
    # `exact` is scored over the rows that CAN testify about control choice; the excluded count
    # rides along so a shrinking denominator is visible rather than silent (no silent caps).
    report["exact_agreement"] = round(exact_agree / exact_n, 4) if exact_n else 0.0
    report["exact_n"] = exact_n
    report["exact_unscoreable"] = unscoreable
    report["by_category"] = dict(sorted(categories.items(), key=lambda kv: kv[1], reverse=True))
    # ADDITIVE per-scenario shape: `agreement`/`agree`/`n` keep their existing meaning and place
    # (the cockpit's promotion panel reads them), and the two-bar keys are added beside them.
    report["by_scenario"] = sorted(
        ({"scenario": k, "n": v["n"], "agree": v["agree"],
          "agreement": round(v["agree"] / v["n"], 4),
          "loose_agreement": round(v["loose"] / v["n"], 4),
          "exact_agreement": round(v["exact"] / v["exact_n"], 4) if v["exact_n"] else 0.0,
          "exact_n": v["exact_n"],
          "exact_unscoreable": v["n"] - v["exact_n"],
          "eligible": is_promotable(v["n"], v["loose"] / v["n"],
                                    (v["exact"] / v["exact_n"]) if v["exact_n"] else 0.0,
                                    v["exact_n"])}
         for k, v in per_scenario.items()),
        key=lambda x: (-x["n"], x["scenario"]))
    report["eligible"] = [s["scenario"] for s in report["by_scenario"] if s["eligible"]]
    return report


def is_promotable(n: int, loose: float, exact: float, exact_n: int = 0) -> bool:
    """Does one scenario clear the promotion gate? BOTH bars, each over a big enough window.

    The `and` is the whole point: a scenario passing `loose` while failing `exact` is NOT
    eligible, because on click-shaped turns `loose` cannot see WHICH control was proposed. See
    the constants above for why, and CONTROLLER_PROMOTION.md for the ruling.

    `exact_n` guards against the opposite failure — passing `exact` on almost no evidence. It
    defaults to 0 so a caller that omits it can never accidentally promote: with no stated
    testimony count, the exact window is empty and the gate refuses.
    """
    return (n >= PROMOTION_MIN_N
            and exact_n >= PROMOTION_MIN_EXACT_N
            and loose >= PROMOTION_LOOSE_BAR
            and exact >= PROMOTION_EXACT_BAR)
