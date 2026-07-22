"""Ears — what should witness A actually listen to, and does it beat what we already had?

Three questions the accuracy table cannot answer, in the order that decides whether to build:

  E1  **What does the witness add over the RECIPE?** `apply_recipe.describe_for_ats(ats, url,
      page_text)` already names a state from the url and text markers, deterministically, for
      free, today. If it scores near the witness, the witness is a rebuild of URL matching with
      extra steps and the honest move is to keep the recipe and spend the effort elsewhere. This
      is the baseline the whole DOM-witness idea has never been measured against.

  E2  **Which feature namespaces carry the signal?** Drop-one-out over `route: / title: / role: /
      tok: / txt: / ph: / flag:`. Two things fall out: what the ears should be made of, and
      whether `route:` is doing all the work — which would be E1's answer wearing a different hat.

  E3  **Is there a train/serve skew?** The trainer reads a capture artifact (which has element
      text but no page text); the live path has page text but element text is empty. Both land in
      the `txt:` namespace. If `txt:` matters, the runtime and the corpus are describing the same
      page with different words — the exact drift the shared featurizer was supposed to prevent.

    cd apps/controlplane-api && ../../.venv/bin/python -m perception.ears
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from perception.dataset import Row, artifacts_root, load_rows
from perception.dom_witness import TfidfCentroidWitness, extract_tokens

MIN_PER_CLASS = 2

#: The namespaces `extract_tokens` emits. Dropping one answers "what would we lose without it?"
NAMESPACES = ("route", "title", "role", "tok", "txt", "ph", "flag")


def _page_text_from(artifact: dict) -> str:
    """Approximate the live path's page text from a capture artifact.

    Not the real thing and marked as such: the artifact never stored page text (that surface was
    only added to the live observer in 2026-07-20), so the closest honest stand-in is the visible
    text of the actionable elements. Good enough to ask whether the RECIPE can name the state;
    not good enough to claim a recipe number is what it would score live.
    """
    acq = artifact.get("acquisition") or {}
    parts = []
    for el in (acq.get("actionable_elements") or [])[:120]:
        for key in ("text", "name", "label", "placeholder"):
            val = (el.get(key) or "").strip()
            if val:
                parts.append(val)
    title = ((acq.get("page_identity") or {}).get("title") or "")
    return " ".join([title] + parts)[:8000]


# --- E1: the baseline nobody measured ------------------------------------------------
def recipe_baseline(rows: list[Row]) -> dict[str, Any]:
    """Score the deterministic recipe matcher on the same rows the witness is scored on."""
    import apply_recipe
    import ats_registry

    counts = Counter(r.state for r in rows)
    scorable = [r for r in rows if counts[r.state] >= MIN_PER_CLASS]
    hits = {"state": 0, "platform": 0, "phase": 0}
    named = 0
    confusion: Counter = Counter()
    by_state = {r.state: r.facets for r in rows}

    for row in scorable:
        ats = ats_registry.classify_ats(row.url)
        desc = apply_recipe.describe_for_ats(ats, row.url, _page_text_from(row.artifact))
        pred = desc.get("state")
        if pred and pred != "unknown":
            named += 1
        if pred == row.state:
            hits["state"] += 1
        else:
            confusion[(row.state, pred)] += 1
        facets = by_state.get(pred or "")
        if facets and facets.platform == row.facets.platform:
            hits["platform"] += 1
        if facets and facets.phase == row.facets.phase:
            hits["phase"] += 1

    n = len(scorable) or 1
    return {
        "n": len(scorable),
        "named_a_state": round(named / n, 4),
        "state": round(hits["state"] / n, 4),
        "platform": round(hits["platform"] / n, 4),
        "phase": round(hits["phase"] / n, 4),
        "top_misses": [{"gold": g, "pred": p, "n": c} for (g, p), c in confusion.most_common(6)],
    }


# --- E2/E3: what the ears are made of ------------------------------------------------
def _loo_accuracy(rows: list[Row], tokens: dict[str, list[str]]) -> dict[str, Any]:
    counts = Counter(r.state for r in rows)
    scorable = [r for r in rows if counts[r.state] >= MIN_PER_CLASS and tokens.get(r.filename)]
    if not scorable:
        return {"n": 0, "state": None}
    by_state = {r.state: r.facets for r in rows}
    hits = {"state": 0, "platform": 0, "phase": 0}
    for i, row in enumerate(scorable):
        train = [r for j, r in enumerate(scorable) if j != i]
        witness = TfidfCentroidWitness().fit((r.state, tokens[r.filename]) for r in train)
        pred = witness.predict(tokens[row.filename])
        if pred.label == row.state:
            hits["state"] += 1
        facets = by_state.get(pred.label or "")
        if facets and facets.platform == row.facets.platform:
            hits["platform"] += 1
        if facets and facets.phase == row.facets.phase:
            hits["phase"] += 1
    n = len(scorable)
    return {"n": n, "state": round(hits["state"] / n, 4),
            "platform": round(hits["platform"] / n, 4), "phase": round(hits["phase"] / n, 4)}


def feature_ablation(rows: list[Row]) -> dict[str, Any]:
    base_tokens = {r.filename: extract_tokens(r.artifact) for r in rows if r.artifact_path}
    out = {"all": _loo_accuracy(rows, base_tokens)}
    for ns in NAMESPACES:
        dropped = {fn: [t for t in toks if not t.startswith(f"{ns}:")]
                   for fn, toks in base_tokens.items()}
        out[f"without_{ns}"] = _loo_accuracy(rows, dropped)
    # And the inverse for the one that matters most: route ALONE.
    only_route = {fn: [t for t in toks if t.startswith("route:")]
                  for fn, toks in base_tokens.items()}
    out["only_route"] = _loo_accuracy(rows, only_route)
    no_route_no_title = {fn: [t for t in toks
                              if not t.startswith("route:") and not t.startswith("title:")]
                         for fn, toks in base_tokens.items()}
    out["without_route_or_title"] = _loo_accuracy(rows, no_route_no_title)
    return out


#: Roles that make a page a FORM PHASE rather than a page. The hypothesis E5 tests: 49 of 50
#: state errors are *within* the right platform — telling Workday's `my_information` from its
#: `questions` from its `voluntary_disclosures` — and what actually distinguishes those is the
#: FIELD SET, which is currently diluted among four hundred nav/footer/chrome tokens.
_FORM_ROLES = frozenset({"textbox", "combobox", "checkbox", "radio", "listbox", "spinbutton",
                         "searchbox", "slider", "switch", "input", "select", "textarea",
                         "radiogroup", "menuitemcheckbox", "menuitemradio"})


def _variant_tokens(artifact: dict, mode: str, cap: int = 400) -> list[str]:
    """Feature-set variants over the same artifact. One knob at a time, deliberately."""
    from perception.dom_witness import _route, _tokens

    acq = artifact.get("acquisition") or {}
    identity = acq.get("page_identity") or {}
    url = identity.get("url") or ""
    feats: list[str] = []
    if mode != "form_only":
        feats.append(f"route:{_route(url)}")
        for tok in _tokens(identity.get("title") or "", 12):
            feats.append(f"title:{tok}")

    seen = 0
    sources = [(c.get("target") or {}) for c in (artifact.get("ranked_candidates") or [])]
    sources += [{"role": el.get("role") or el.get("tag"), "label": el.get("name") or el.get("label")}
                for el in (acq.get("actionable_elements") or [])]
    for src in sources[:120]:
        role = str(src.get("role") or "").strip().lower()
        name = src.get("label") or src.get("name") or ""
        is_form = role in _FORM_ROLES
        if mode == "form_only" and not is_form:
            continue
        for tok in _tokens(name):
            if mode == "form_weighted" and is_form:
                feats.extend([f"field:{tok}"] * 3)   # a form control counts triple
            feats.append(f"tok:{tok}")
            seen += 1
        if seen > cap:
            break
    return feats


def feature_variants(rows: list[Row]) -> dict[str, Any]:
    """E5 — can the ears be aimed at the thing that actually separates the phases?"""
    from perception.dom_witness import extract_tokens

    variants = {
        "baseline (all, cap 400)": lambda a: extract_tokens(a),
        "no txt: (drop the skewed namespace)": lambda a: [t for t in extract_tokens(a)
                                                          if not t.startswith("txt:")],
        "form controls only": lambda a: _variant_tokens(a, "form_only"),
        "form controls weighted 3x": lambda a: _variant_tokens(a, "form_weighted"),
        "all, cap 1200": lambda a: _variant_tokens(a, "plain", cap=1200),
    }
    out = {}
    for name, fn in variants.items():
        tokens = {r.filename: fn(r.artifact) for r in rows if r.artifact_path}
        out[name] = _loo_accuracy(rows, tokens)
        out[name]["median_tokens"] = sorted(len(t) for t in tokens.values())[len(tokens) // 2]
    return out


def two_stage(rows: list[Row]) -> dict[str, Any]:
    """Platform first, then state WITHIN that platform — measured, not estimated.

    The reason to expect a win: platform is ~98% and conditioning on it turns one 59-way decision
    into a 6-way plus a ~10-way. The reason it might not: a per-platform witness trains on a
    sixth of the data, and our data is the scarce resource, not our model capacity.
    """
    tokens = {r.filename: extract_tokens(r.artifact) for r in rows if r.artifact_path}
    counts = Counter(r.state for r in rows)
    scorable = [r for r in rows if counts[r.state] >= MIN_PER_CLASS and tokens.get(r.filename)]
    hits = 0
    platform_hits = 0
    for i, row in enumerate(scorable):
        train = [r for j, r in enumerate(scorable) if j != i]
        # stage 1 — platform
        stage1 = TfidfCentroidWitness().fit((r.facets.platform, tokens[r.filename]) for r in train)
        platform = stage1.predict(tokens[row.filename]).label
        if platform == row.facets.platform:
            platform_hits += 1
        # stage 2 — state within the predicted platform (fall back to everything if empty)
        within = [r for r in train if r.facets.platform == platform] or train
        stage2 = TfidfCentroidWitness().fit((r.state, tokens[r.filename]) for r in within)
        if stage2.predict(tokens[row.filename]).label == row.state:
            hits += 1
    n = len(scorable) or 1
    return {"n": len(scorable), "stage1_platform": round(platform_hits / n, 4),
            "two_stage_state": round(hits / n, 4)}


def run() -> dict[str, Any]:
    rows, census = load_rows()
    rows = [r for r in rows if r.artifact_path]
    return {
        "census": census,
        "e1_recipe_baseline": recipe_baseline(rows),
        "e2_feature_ablation": feature_ablation(rows),
        "e5_feature_variants": feature_variants(rows),
        "e4_two_stage": two_stage(rows),
    }


def _print(report: dict[str, Any]) -> None:
    e1 = report["e1_recipe_baseline"]
    print(f"\nE1 — the deterministic recipe matcher on the same {e1['n']} rows")
    print(f"   names a state at all: {e1['named_a_state']:.1%}")
    print(f"   state {e1['state']:.1%} · platform {e1['platform']:.1%} · phase {e1['phase']:.1%}")
    for m in e1["top_misses"][:4]:
        print(f"     miss: {m['gold']} -> {m['pred']} ({m['n']})")

    print("\nE2/E3 — feature ablation (leave-one-out, TF-IDF centroid)")
    base = report["e2_feature_ablation"]["all"]
    print(f"   {'all features':26} state {base['state']:.1%} · platform {base['platform']:.1%} "
          f"· phase {base['phase']:.1%}")
    for key, val in report["e2_feature_ablation"].items():
        if key == "all" or not val.get("state"):
            continue
        delta = val["state"] - base["state"]
        print(f"   {key:26} state {val['state']:.1%} ({delta:+.1%}) · "
              f"platform {val['platform']:.1%} · phase {val['phase']:.1%}")

    print("\nE5 — aiming the ears at what separates the phases")
    for name, val in (report.get("e5_feature_variants") or {}).items():
        if not val.get("state"):
            continue
        print(f"   {name:38} state {val['state']:.1%} · platform {val['platform']:.1%} "
              f"· phase {val['phase']:.1%}  (median {val['median_tokens']} tokens)")

    t = report["e4_two_stage"]
    print(f"\nE4 — two-stage: platform {t['stage1_platform']:.1%} then state-within-platform "
          f"{t['two_stage_state']:.1%} (flat was {base['state']:.1%})")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="What should witness A listen to?")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    report = run()
    _print(report)
    out = Path(args.out) if args.out else artifacts_root() / "derived" / "perception_ears.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nfull report: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
