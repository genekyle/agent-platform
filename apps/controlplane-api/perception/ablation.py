"""Ablation — does the second witness EARN its place, and where?

`bench.py` answers "which witness is most accurate". That is not the decision in front of us.
The decision is **what configuration of eyes and ears to build on**, and it turns on questions
bench.py structurally cannot answer, because every one of them is about INCREMENTAL value:

  Q1  Does the agreement flag predict error better than the DOM witness's OWN margin already
      does? (The report already said `margin_separates = 0.774` for dom:tfidf, and the whole
      two-witness argument was made without comparing against it. If agreement adds nothing
      *conditional on* margin, the cross-check is decoration.)
  Q2  Novelty at an OPERATING POINT, not as an AUROC. At a fixed false-flag budget, what share of
      genuinely-unseen states does each configuration catch — dom alone, visual alone, both-must-
      agree (AND), either-may-fire (OR)?
  Q3  Where does vision win CONDITIONALLY? Average accuracy hides the case it was bought for: a
      page whose AX tree is nearly empty (canvas, iframe, image challenge) starves the DOM witness
      and is exactly where pixels should carry. If vision wins nowhere in particular, "on average
      slightly worse" is the whole story and it should not ship.
  Q4  Does a two-stage (platform -> state-within-platform) beat the flat 59-way? Platform is 98%;
      conditioning on it shrinks every subsequent decision.

Everything runs off ONE cached leave-one-out pass (`predictions.json`) plus one cached
leave-one-CLASS-out pass (`novelty.json`), so the expensive fitting happens once and every
analysis after it is instant. Re-run with `--refit` when the corpus or a witness changes.

    cd apps/controlplane-api && ../../.venv/bin/python -m perception.ablation
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

from perception.bench import DomHarness, VisualHarness, auroc
from perception.dataset import Row, artifacts_root, load_rows

MIN_PER_CLASS = 2

#: The operating point novelty is judged at. Novelty is a percentile, so this IS the false-flag
#: budget on known pages — see `interaction.belief.NOVELTY_CEILING`.
FALSE_FLAG_BUDGET = 0.10


def _cache_path(name: str) -> Path:
    return artifacts_root() / "derived" / f"perception_ablation_{name}.json"


# --- pass 1: leave-one-ROW-out predictions from every witness -------------------------
def build_predictions(encoders: list[str], dom_family: str = "tfidf") -> list[dict]:
    rows, _census = load_rows()
    dom = DomHarness(dom_family)
    dom_rows = dom.prepare(rows)
    visuals: dict[str, VisualHarness] = {}
    for name in encoders:
        harness = VisualHarness(name)
        harness.prepare(rows)
        visuals[name] = harness

    usable = [r for r in dom_rows if all(r.filename in v.vectors for v in visuals.values())]
    counts = Counter(r.state for r in usable)
    scorable = [r for r in usable if counts[r.state] >= MIN_PER_CLASS]

    out: list[dict] = []
    for i, row in enumerate(scorable):
        train = [r for j, r in enumerate(scorable) if j != i]
        record: dict[str, Any] = {
            "filename": row.filename,
            "gold": row.state,
            "gold_platform": row.facets.platform,
            "gold_phase": row.facets.phase,
            # How much the DOM witness had to work with. The starvation axis for Q3.
            "dom_tokens": len(dom.tokens.get(row.filename) or ()),
            "class_size": counts[row.state],
        }
        d = dom.predict(dom.fit(train, lambda r: r.state), row)
        record["dom"] = {"pred": d.label, "margin": d.margin, "clarity": d.clarity,
                         "novelty": d.novelty, "right": d.label == row.state}
        for name, harness in visuals.items():
            v = harness.predict(harness.fit(train, lambda r: r.state), row)
            record[f"visual:{name}"] = {"pred": v.label, "margin": v.margin, "clarity": v.clarity,
                                        "novelty": v.novelty, "right": v.label == row.state}
        out.append(record)
    return out


# --- pass 2: leave-one-CLASS-out novelty (the honest unseen-state test) ---------------
def build_novelty(encoders: list[str], dom_family: str = "tfidf") -> list[dict]:
    rows, _ = load_rows()
    dom = DomHarness(dom_family)
    dom_rows = dom.prepare(rows)
    visuals = {}
    for name in encoders:
        h = VisualHarness(name)
        h.prepare(rows)
        visuals[name] = h
    usable = [r for r in dom_rows if all(r.filename in v.vectors for v in visuals.values())]

    counts = Counter(r.state for r in usable)
    classes = [c for c, n in counts.items() if n >= MIN_PER_CLASS]

    out: list[dict] = []
    for held in classes:
        train = [r for r in usable if r.state != held]
        if len({r.state for r in train}) < 2:
            continue
        dom_model = dom.fit(train, lambda r: r.state)
        vis_models = {n: h.fit(train, lambda r: r.state) for n, h in visuals.items()}
        for row in usable:
            rec: dict[str, Any] = {"held_out_class": held, "filename": row.filename,
                                   "unseen": row.state == held}
            rec["dom"] = dom.predict(dom_model, row).novelty
            for name, harness in visuals.items():
                rec[f"visual:{name}"] = harness.predict(vis_models[name], row).novelty
            out.append(rec)
    return out


# --- analyses (instant, off the cached passes) ---------------------------------------
def q1_error_prediction(preds: list[dict], visual_key: str) -> dict[str, Any]:
    """Does agreement add signal ON TOP OF the DOM witness's own margin?

    Three comparisons, not one: each signal alone against correctness, then — the one that
    actually decides it — agreement measured WITHIN margin bands. A signal that only looks useful
    marginally, and adds nothing once you condition on what you already had, is not a signal.
    """
    right = [p for p in preds if p["dom"]["right"]]
    wrong = [p for p in preds if not p["dom"]["right"]]

    def agree(p: dict) -> bool:
        return p["dom"]["pred"] == p[visual_key]["pred"]

    alone = {
        "dom_margin": auroc([p["dom"]["margin"] for p in right],
                            [p["dom"]["margin"] for p in wrong]),
        "dom_clarity": auroc([p["dom"]["clarity"] for p in right],
                             [p["dom"]["clarity"] for p in wrong]),
        "agreement": auroc([1.0 if agree(p) else 0.0 for p in right],
                           [1.0 if agree(p) else 0.0 for p in wrong]),
        "visual_margin": auroc([p[visual_key]["margin"] for p in right],
                               [p[visual_key]["margin"] for p in wrong]),
    }

    # Stratify by the DOM witness's own clarity, then ask whether agreement still separates
    # inside each band. This is the incremental test.
    ordered = sorted(preds, key=lambda p: p["dom"]["clarity"])
    third = max(1, len(ordered) // 3)
    bands = {"low_clarity": ordered[:third], "mid_clarity": ordered[third:2 * third],
             "high_clarity": ordered[2 * third:]}
    conditional = {}
    for band, group in bands.items():
        agreed = [p for p in group if agree(p)]
        split = [p for p in group if not agree(p)]
        conditional[band] = {
            "n": len(group),
            "accuracy_when_agree": round(sum(p["dom"]["right"] for p in agreed) / len(agreed), 4)
            if agreed else None,
            "n_agree": len(agreed),
            "accuracy_when_split": round(sum(p["dom"]["right"] for p in split) / len(split), 4)
            if split else None,
            "n_split": len(split),
        }
    return {"alone": alone, "conditional_on_dom_clarity": conditional}


def _rate_at_budget(scores_known: list[float], scores_unseen: list[float],
                    budget: float) -> dict[str, Any]:
    """Recall on genuinely-unseen states at a fixed false-flag rate on known ones."""
    if not scores_known or not scores_unseen:
        return {"threshold": None, "recall": None}
    ordered = sorted(scores_known)
    idx = min(len(ordered) - 1, int(round((1.0 - budget) * len(ordered))))
    threshold = ordered[idx]
    caught = sum(1 for s in scores_unseen if s >= threshold)
    flagged_known = sum(1 for s in scores_known if s >= threshold)
    return {
        "threshold": round(threshold, 4),
        "recall": round(caught / len(scores_unseen), 4),
        "false_flag_rate": round(flagged_known / len(scores_known), 4),
    }


def q2_novelty_operating_point(novelty: list[dict], visual_key: str,
                               budget: float = FALSE_FLAG_BUDGET) -> dict[str, Any]:
    """Novelty where it is actually used: at a threshold, catching unseen states.

    AUROC says how well a score ORDERS; it does not say what you catch at the budget you can
    afford. And it cannot express the two fusion rules that matter — AND (both must flag: fewer
    false alarms) and OR (either may fire: fewer misses).
    """
    known_dom = [r["dom"] for r in novelty if not r["unseen"]]
    unseen_dom = [r["dom"] for r in novelty if r["unseen"]]
    known_vis = [r[visual_key] for r in novelty if not r["unseen"]]
    unseen_vis = [r[visual_key] for r in novelty if r["unseen"]]
    # Fusions, computed per row so the pairing is preserved.
    known_and = [min(r["dom"], r[visual_key]) for r in novelty if not r["unseen"]]
    unseen_and = [min(r["dom"], r[visual_key]) for r in novelty if r["unseen"]]
    known_or = [max(r["dom"], r[visual_key]) for r in novelty if not r["unseen"]]
    unseen_or = [max(r["dom"], r[visual_key]) for r in novelty if r["unseen"]]

    return {
        "n_known": len(known_dom), "n_unseen": len(unseen_dom),
        "dom": {"auroc": auroc(unseen_dom, known_dom),
                **_rate_at_budget(known_dom, unseen_dom, budget)},
        "visual": {"auroc": auroc(unseen_vis, known_vis),
                   **_rate_at_budget(known_vis, unseen_vis, budget)},
        "and_both": {"auroc": auroc(unseen_and, known_and),
                     **_rate_at_budget(known_and, unseen_and, budget)},
        "or_either": {"auroc": auroc(unseen_or, known_or),
                      **_rate_at_budget(known_or, unseen_or, budget)},
    }


def q3_where_vision_wins(preds: list[dict], visual_key: str) -> dict[str, Any]:
    """Conditional value. Average accuracy hides the case vision was bought for.

    Bucketed by how many tokens the DOM witness got: a page with almost no AX tree (canvas,
    iframe, image challenge) starves witness A, and that is precisely where pixels should carry.
    """
    ordered = sorted(preds, key=lambda p: p["dom_tokens"])
    third = max(1, len(ordered) // 3)
    buckets = {
        "starved (fewest AX tokens)": ordered[:third],
        "middle": ordered[third:2 * third],
        "rich (most AX tokens)": ordered[2 * third:],
    }
    out = {}
    for name, group in buckets.items():
        if not group:
            continue
        dom_right = sum(p["dom"]["right"] for p in group)
        vis_right = sum(p[visual_key]["right"] for p in group)
        either = sum(1 for p in group if p["dom"]["right"] or p[visual_key]["right"])
        vis_only = sum(1 for p in group if p[visual_key]["right"] and not p["dom"]["right"])
        out[name] = {
            "n": len(group),
            "token_range": [group[0]["dom_tokens"], group[-1]["dom_tokens"]],
            "dom_accuracy": round(dom_right / len(group), 4),
            "visual_accuracy": round(vis_right / len(group), 4),
            "either_right": round(either / len(group), 4),
            "visual_rescues": vis_only,          # rows only the visual witness got
        }
    return out


def q4_two_stage(preds: list[dict], platform_of: dict[str, str]) -> dict[str, Any]:
    """Would conditioning on the platform help? A cheap upper bound, no refit.

    Counts the errors that a perfect platform gate would have removed: rows the DOM witness got
    wrong where its prediction was on the WRONG platform. Those are the errors a two-stage model
    can address; errors inside the right platform are not, and knowing which kind dominates says
    whether the two-stage build is worth doing before doing it.
    """
    wrong = [p for p in preds if not p["dom"]["right"]]
    cross = [p for p in wrong if platform_of.get(p["dom"]["pred"] or "") != p["gold_platform"]]
    return {
        "total": len(preds),
        "errors": len(wrong),
        "cross_platform_errors": len(cross),
        "within_platform_errors": len(wrong) - len(cross),
        "ceiling_if_platform_were_free": round(
            (sum(p["dom"]["right"] for p in preds) + len(cross)) / len(preds), 4) if preds else None,
    }


# --- driver ---------------------------------------------------------------------------
def load_or_build(name: str, builder: Callable[[], list[dict]], refit: bool) -> list[dict]:
    path = _cache_path(name)
    if not refit and path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    data = builder()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def run(encoders: list[str], dom_family: str, *, refit: bool = False) -> dict[str, Any]:
    preds = load_or_build("predictions", lambda: build_predictions(encoders, dom_family), refit)
    novelty = load_or_build("novelty", lambda: build_novelty(encoders, dom_family), refit)

    rows, _ = load_rows()
    platform_of = {r.state: r.facets.platform for r in rows}

    report: dict[str, Any] = {"n_rows": len(preds), "dom_family": dom_family, "per_encoder": {}}
    report["q4_two_stage"] = q4_two_stage(preds, platform_of)
    for enc in encoders:
        key = f"visual:{enc}"
        if not preds or key not in preds[0]:
            continue
        report["per_encoder"][enc] = {
            "q1_error_prediction": q1_error_prediction(preds, key),
            "q2_novelty_operating_point": q2_novelty_operating_point(novelty, key),
            "q3_where_vision_wins": q3_where_vision_wins(preds, key),
        }
    return report


def _print(report: dict[str, Any]) -> None:
    print(f"\n{report['n_rows']} scorable rows · dom={report['dom_family']}")

    for enc, block in report["per_encoder"].items():
        print(f"\n{'=' * 78}\nENCODER: {enc}")

        q1 = block["q1_error_prediction"]
        print("\nQ1 — does agreement predict error better than the DOM's own margin?")
        for signal, value in q1["alone"].items():
            print(f"   {signal:16} AUROC(right>wrong) = {value}")
        print("   conditional on the DOM's own clarity:")
        for band, b in q1["conditional_on_dom_clarity"].items():
            agree = f"{b['accuracy_when_agree']:.1%}" if b["accuracy_when_agree"] is not None else "—"
            split = f"{b['accuracy_when_split']:.1%}" if b["accuracy_when_split"] is not None else "—"
            print(f"     {band:14} n={b['n']:3}  agree {agree} (n={b['n_agree']:3})  "
                  f"split {split} (n={b['n_split']:3})")

        q2 = block["q2_novelty_operating_point"]
        print(f"\nQ2 — unseen-state detection at a {int(FALSE_FLAG_BUDGET * 100)}% false-flag budget "
              f"({q2['n_unseen']} unseen / {q2['n_known']} known observations)")
        for config in ("dom", "visual", "and_both", "or_either"):
            c = q2[config]
            recall = f"{c['recall']:.1%}" if c["recall"] is not None else "—"
            print(f"   {config:10} AUROC {c['auroc']}  recall@budget {recall}  "
                  f"(actual false-flag {c['false_flag_rate']})")

        print("\nQ3 — where does vision win? (bucketed by how many AX tokens the DOM witness got)")
        for bucket, b in block["q3_where_vision_wins"].items():
            print(f"   {bucket:26} n={b['n']:3} tokens {b['token_range'][0]}-{b['token_range'][1]:<5} "
                  f"dom {b['dom_accuracy']:.1%}  visual {b['visual_accuracy']:.1%}  "
                  f"either {b['either_right']:.1%}  vision-only rescues: {b['visual_rescues']}")

    q4 = report["q4_two_stage"]
    print(f"\n{'=' * 78}\nQ4 — would a platform gate help? {q4['errors']} DOM errors: "
          f"{q4['cross_platform_errors']} cross-platform, {q4['within_platform_errors']} within. "
          f"Ceiling with a free platform gate: {q4['ceiling_if_platform_were_free']:.1%}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Does the second witness earn its place, and where?")
    ap.add_argument("--encoders", default="apple,clip")
    ap.add_argument("--dom", default="tfidf")
    ap.add_argument("--refit", action="store_true", help="ignore the cached LOO passes")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    report = run([e.strip() for e in args.encoders.split(",") if e.strip()], args.dom,
                 refit=args.refit)
    _print(report)
    out = Path(args.out) if args.out else artifacts_root() / "derived" / "perception_ablation.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nfull report: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
