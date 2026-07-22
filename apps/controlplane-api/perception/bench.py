"""The perception bench — decide the encoder on numbers, before building on it.

Runs every witness over one corpus and one split, and answers four questions in the order they
matter (PLAN_perception_v1 §5 S18, §8):

  1. **At which FACET is each witness accurate?** Exact state / platform / phase, separately.
     Measured 2026-07-22 with a throwaway script: vision was 93% at platform and 55% at state.
     If that holds, the visual witness must report the platform and shut up about the phase.
  2. **Can it detect a state it has never seen?** Leave-one-CLASS-out: drop a whole state from
     the bank, then compare the novelty it scores against the novelty in-distribution examples
     score. This is the real out-of-distribution test — same-vs-different cosine pairs flatter a
     weak encoder, because two examples of DIFFERENT known states are not novel at all.
  3. **Does DISAGREEMENT predict failure?** The plan's cleanest falsifier: if turns where the two
     witnesses disagree are no likelier to be wrong than turns where they agree, witness B is
     decoration and we should not ship it.
  4. **Is a 600 MB download worth it?** pixel32 is the free baseline; CLIP has to beat it AND
     Apple's free native encoder by enough to justify the weight.

Leave-one-out, not a held-out split: with a median of 3 examples per state, a 20% holdout would
be measuring the sampler. LOO refits per fold — 174 rows makes that seconds, and it is honest.

    cd apps/controlplane-api && ../../.venv/bin/python -m perception.bench
    ../../.venv/bin/python -m perception.bench --encoders apple,pixel32,clip
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

from perception.dataset import Row, artifacts_root, load_rows
from perception.dom_witness import NaiveBayesWitness, TfidfCentroidWitness, extract_tokens
from perception.encoders import get_encoder
from perception.prototypes import PrototypeBank, Prediction

MIN_PER_CLASS = 2  # a class with one example cannot be scored leave-one-out


# --- metrics ---------------------------------------------------------------------------
def auroc(positives: list[float], negatives: list[float]) -> Optional[float]:
    """P(a random positive scores above a random negative). Rank-based, ties counted half."""
    if not positives or not negatives:
        return None
    merged = sorted([(s, 1) for s in positives] + [(s, 0) for s in negatives])
    ranks: dict[int, float] = {}
    i = 0
    rank_sum_pos = 0.0
    while i < len(merged):
        j = i
        while j + 1 < len(merged) and merged[j + 1][0] == merged[i][0]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            if merged[k][1] == 1:
                rank_sum_pos += avg_rank
        i = j + 1
    n_pos, n_neg = len(positives), len(negatives)
    u = rank_sum_pos - n_pos * (n_pos + 1) / 2.0
    return round(u / (n_pos * n_neg), 4)


def _facet(row: Row, facet: str) -> str:
    return getattr(row.facets, facet)


# --- the two witness harnesses ---------------------------------------------------------
class VisualHarness:
    """Prototype bank over one encoder's vectors."""

    def __init__(self, encoder_name: str) -> None:
        self.encoder_name = encoder_name
        self.encoder = get_encoder(encoder_name)
        self.name = f"visual:{encoder_name}"
        self.vectors: dict[str, list[float]] = {}

    def prepare(self, rows: list[Row]) -> list[Row]:
        kept: list[Row] = []
        for row in rows:
            if not row.screenshot_path:
                continue
            vec = self.encoder.embed(row.screenshot_path)
            if vec:
                self.vectors[row.filename] = vec
                kept.append(row)
        flush = getattr(self.encoder, "flush", None)
        if flush:
            flush()
        return kept

    def fit(self, rows: list[Row], label_of: Callable[[Row], str]) -> Any:
        return PrototypeBank(self.encoder_name).fit(
            (label_of(r), self.vectors[r.filename]) for r in rows)

    def predict(self, model: Any, row: Row) -> Prediction:
        return model.predict(self.vectors[row.filename])


class DomHarness:
    """Token witness (TF-IDF centroid or Naive Bayes) over the capture artifact."""

    def __init__(self, family: str) -> None:
        self.family = family
        self.name = f"dom:{family}"
        self.tokens: dict[str, list[str]] = {}

    def prepare(self, rows: list[Row]) -> list[Row]:
        kept: list[Row] = []
        for row in rows:
            if not row.artifact_path:
                continue
            toks = extract_tokens(row.artifact)
            if toks:
                self.tokens[row.filename] = toks
                kept.append(row)
        return kept

    def fit(self, rows: list[Row], label_of: Callable[[Row], str]) -> Any:
        witness = TfidfCentroidWitness() if self.family == "tfidf" else NaiveBayesWitness()
        return witness.fit((label_of(r), self.tokens[r.filename]) for r in rows)

    def predict(self, model: Any, row: Row) -> Prediction:
        return model.predict(self.tokens[row.filename])


# --- evaluations -----------------------------------------------------------------------
def loo_accuracy(harness: Any, rows: list[Row], facet: str) -> dict[str, Any]:
    """Leave-one-out accuracy predicting `facet` (state_id / platform / phase)."""
    label_of = (lambda r: r.state) if facet == "state_id" else (lambda r: _facet(r, facet))
    counts = Counter(label_of(r) for r in rows)
    scorable = [r for r in rows if counts[label_of(r)] >= MIN_PER_CLASS]
    if not scorable:
        return {"n": 0, "accuracy": None}

    correct = 0
    confusion: Counter = Counter()
    margins_right: list[float] = []
    margins_wrong: list[float] = []
    for i, row in enumerate(scorable):
        train = [r for j, r in enumerate(scorable) if j != i]
        model = harness.fit(train, label_of)
        pred = harness.predict(model, row)
        gold = label_of(row)
        if pred.label == gold:
            correct += 1
            margins_right.append(pred.margin)
        else:
            confusion[(gold, pred.label)] += 1
            margins_wrong.append(pred.margin)
    n = len(scorable)
    return {
        "n": n,
        "classes": len({label_of(r) for r in scorable}),
        "accuracy": round(correct / n, 4),
        "margin_separates": auroc(margins_right, margins_wrong),
        "top_confusions": [{"gold": g, "pred": p, "n": c}
                           for (g, p), c in confusion.most_common(6)],
    }


def loo_state_eval(harness: Any, rows: list[Row]) -> dict[str, Any]:
    """One LOO pass over STATE labels, scored at three altitudes by PROJECTION.

    The distinction matters and the first bench run found it: training a witness directly on a
    facet label averages `workday_sign_in`, `indeed_login_email` and `login_wall` into one
    "sign_in" centroid — four vendors' chrome smeared together — and it scored *worse* than
    predicting the state and reading the facet off the answer. So the observer's shape is:
    **predict the state you know, then project.** Facets are a lens on the answer, not a
    separate model to train.
    """
    counts = Counter(r.state for r in rows)
    scorable = [r for r in rows if counts[r.state] >= MIN_PER_CLASS]
    if not scorable:
        return {"n": 0}
    by_state = {r.state: r.facets for r in rows}

    hits = {"state_id": 0, "platform": 0, "phase": 0}
    confusion: Counter = Counter()
    margins_right: list[float] = []
    margins_wrong: list[float] = []
    for i, row in enumerate(scorable):
        train = [r for j, r in enumerate(scorable) if j != i]
        pred = harness.predict(harness.fit(train, lambda r: r.state), row)
        if pred.label == row.state:
            hits["state_id"] += 1
            margins_right.append(pred.margin)
        else:
            confusion[(row.state, pred.label)] += 1
            margins_wrong.append(pred.margin)
        predicted_facets = by_state.get(pred.label or "")
        for facet in ("platform", "phase"):
            if predicted_facets and getattr(predicted_facets, facet) == _facet(row, facet):
                hits[facet] += 1
    n = len(scorable)
    return {
        "n": n,
        "classes": len({r.state for r in scorable}),
        "state_id": {"accuracy": round(hits["state_id"] / n, 4)},
        "platform": {"accuracy": round(hits["platform"] / n, 4)},
        "phase": {"accuracy": round(hits["phase"] / n, 4)},
        "margin_separates": auroc(margins_right, margins_wrong),
        "top_confusions": [{"gold": g, "pred": p, "n": c} for (g, p), c in confusion.most_common(6)],
    }


def leave_class_out_novelty(harness: Any, rows: list[Row]) -> dict[str, Any]:
    """Drop a whole state from the bank; does its novelty score exceed a known state's?

    The honest unknown-state test, and the one the scoreboard's "novelty detection accuracy"
    means. A witness that cannot do this is a classifier, not an observer.
    """
    counts = Counter(r.state for r in rows)
    classes = [c for c, n in counts.items() if n >= MIN_PER_CLASS]
    if len(classes) < 3:
        return {"n_classes": len(classes), "auroc": None}

    unseen_scores: list[float] = []
    seen_scores: list[float] = []
    for held in classes:
        train = [r for r in rows if r.state != held]
        if len({r.state for r in train}) < 2:
            continue
        model = harness.fit(train, lambda r: r.state)
        for row in rows:
            pred = harness.predict(model, row)
            (unseen_scores if row.state == held else seen_scores).append(pred.novelty)
    if not unseen_scores:
        return {"n_classes": len(classes), "auroc": None}
    return {
        "n_classes": len(classes),
        "held_out_mean_novelty": round(sum(unseen_scores) / len(unseen_scores), 4),
        "in_dist_mean_novelty": round(sum(seen_scores) / len(seen_scores), 4),
        "auroc": auroc(unseen_scores, seen_scores),
    }


def agreement_analysis(visual: VisualHarness, dom: DomHarness, rows: list[Row],
                       facet: str = "state_id") -> dict[str, Any]:
    """The plan's cleanest falsifier: does DISAGREEMENT predict a wrong answer?

    Both witnesses are refit leave-one-out on the shared rows, then every row is bucketed by
    whether they named the same label. If accuracy-when-agreeing is not clearly above
    accuracy-when-disagreeing, the second witness earns nothing and should not ship.
    """
    label_of = (lambda r: r.state) if facet == "state_id" else (lambda r: _facet(r, facet))
    counts = Counter(label_of(r) for r in rows)
    scorable = [r for r in rows if counts[label_of(r)] >= MIN_PER_CLASS]
    if len(scorable) < 4:
        return {"n": len(scorable)}

    buckets = {"agree": [0, 0], "disagree": [0, 0]}   # [correct, total]
    dom_when_disagree = [0, 0]
    vis_when_disagree = [0, 0]
    for i, row in enumerate(scorable):
        train = [r for j, r in enumerate(scorable) if j != i]
        v_pred = visual.predict(visual.fit(train, label_of), row)
        d_pred = dom.predict(dom.fit(train, label_of), row)
        gold = label_of(row)
        agree = v_pred.label == d_pred.label
        key = "agree" if agree else "disagree"
        # When they agree there is one answer; when they disagree we score the DOM witness's,
        # because that is what the system does today (witness A leads, B is the second opinion).
        chosen = d_pred.label
        buckets[key][1] += 1
        if chosen == gold:
            buckets[key][0] += 1
        if not agree:
            dom_when_disagree[1] += 1
            vis_when_disagree[1] += 1
            dom_when_disagree[0] += int(d_pred.label == gold)
            vis_when_disagree[0] += int(v_pred.label == gold)

    def rate(pair: list[int]) -> Optional[float]:
        return round(pair[0] / pair[1], 4) if pair[1] else None

    return {
        "n": len(scorable),
        "agree_n": buckets["agree"][1], "agree_accuracy": rate(buckets["agree"]),
        "disagree_n": buckets["disagree"][1], "disagree_accuracy": rate(buckets["disagree"]),
        "when_disagree_dom_right": rate(dom_when_disagree),
        "when_disagree_visual_right": rate(vis_when_disagree),
    }


# --- driver ----------------------------------------------------------------------------
def run(encoders: list[str], dom_families: list[str], *,
        direct_facets: tuple[str, ...] = ()) -> dict[str, Any]:
    rows, census = load_rows()
    report: dict[str, Any] = {"census": census, "witnesses": {}, "fusion": {}}

    prepared: dict[str, tuple[Any, list[Row]]] = {}
    for family in dom_families:
        h = DomHarness(family)
        prepared[h.name] = (h, h.prepare(rows))
    for enc in encoders:
        try:
            h = VisualHarness(enc)
            kept = h.prepare(rows)
        except Exception as exc:  # a missing encoder is a reportable result, not a crash
            report["witnesses"][f"visual:{enc}"] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        if not kept:
            report["witnesses"][f"visual:{enc}"] = {"error": "no embeddable screenshots"}
            continue
        prepared[h.name] = (h, kept)

    for name, (harness, subset) in prepared.items():
        entry: dict[str, Any] = {"rows": len(subset)}
        entry.update(loo_state_eval(harness, subset))
        for facet in direct_facets:
            entry[f"direct_{facet}"] = loo_accuracy(harness, subset, facet)
        entry["novelty"] = leave_class_out_novelty(harness, subset)
        report["witnesses"][name] = entry

    # Fusion is only meaningful on rows BOTH witnesses can see.
    visual_names = [n for n in prepared if n.startswith("visual:")]
    dom_names = [n for n in prepared if n.startswith("dom:")]
    for vname in visual_names:
        for dname in dom_names:
            vh, vrows = prepared[vname]
            dh, drows = prepared[dname]
            shared_files = {r.filename for r in vrows} & {r.filename for r in drows}
            shared = [r for r in vrows if r.filename in shared_files]
            report["fusion"][f"{vname}+{dname}"] = agreement_analysis(vh, dh, shared)
    return report


def _print(report: dict[str, Any]) -> None:
    c = report["census"]
    print(f"\ncorpus: {c['labeled']} labeled · {c['with_artifact']} with artifact · "
          f"{c['with_screenshot']} with screenshot ({c['missing_screenshot']} MISSING, "
          f"{c.get('screenshot_by_stale_path', 0)} found only by filename — stale absolute path)")
    print("\n%-26s %6s %8s %8s %8s %10s" % ("witness", "rows", "state", "platform", "phase", "ood_auroc"))
    print("-" * 74)
    for name, entry in report["witnesses"].items():
        if "error" in entry:
            print("%-26s %s" % (name, entry["error"]))
            continue
        def acc(f: str) -> str:
            v = (entry.get(f) or {}).get("accuracy")
            return f"{v:.1%}" if v is not None else "—"
        ood = (entry.get("novelty") or {}).get("auroc")
        print("%-26s %6d %8s %8s %8s %10s" % (
            name, entry["rows"], acc("state_id"), acc("platform"), acc("phase"),
            f"{ood:.3f}" if ood is not None else "—"))
    print("\nfusion — does disagreement predict failure?")
    for pair, f in report["fusion"].items():
        if not f.get("agree_n"):
            print(f"  {pair}: not enough shared rows ({f.get('n')})")
            continue
        print(f"  {pair}: agree {f['agree_accuracy']:.1%} (n={f['agree_n']}) vs "
              f"disagree {f['disagree_accuracy']:.1%} (n={f['disagree_n']}) · "
              f"when split, dom right {f['when_disagree_dom_right']:.0%} / "
              f"visual right {f['when_disagree_visual_right']:.0%}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Score perception witnesses on the labeled corpus.")
    ap.add_argument("--encoders", default="apple,pixel32",
                    help="comma-separated: apple, pixel32, clip (clip downloads ~600MB — wifi only)")
    ap.add_argument("--dom", default="tfidf,nb", help="comma-separated: tfidf, nb")
    ap.add_argument("--out", default="", help="write the full JSON report here")
    ap.add_argument("--direct-facets", default="",
                    help="also train directly on these facets (comparison; slow) e.g. platform,phase")
    args = ap.parse_args(argv)

    report = run([e.strip() for e in args.encoders.split(",") if e.strip()],
                 [d.strip() for d in args.dom.split(",") if d.strip()],
                 direct_facets=tuple(f.strip() for f in args.direct_facets.split(",") if f.strip()))
    _print(report)
    out = Path(args.out) if args.out else artifacts_root() / "derived" / "perception_bench.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\nfull report: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
