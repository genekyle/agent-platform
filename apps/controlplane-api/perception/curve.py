"""Is DATA the bridge? — the learning curve, and what precision we can already buy.

The operator's question, and it is the right one to ask before collecting anything: the witness is
at ~69% and a system that acts needs better than that. Does more data close the gap, or are we
about to spend months of drives on a curve that has already flattened?

Three measurements, and the third one reframes the question:

  C1  **The learning curve.** Hold the class set FIXED (states with enough examples to subsample),
      then vary how many examples per class the witness may train on. If accuracy is still
      climbing at the right edge, data is the bridge and we know roughly what it buys per example.
      If it has flattened, more of the same data is the wrong investment.

  C2  **Accuracy by class depth.** The corpus is 59 states over 174 rows — a median of 3. Scoring
      the well-observed states separately from the singletons says whether "69%" is one number or
      two populations wearing one average.

  C3  **Precision at coverage.** The number that actually gates acting is not accuracy — it is
      accuracy ON THE TURNS THE SYSTEM CHOOSES TO ACT ON. A witness that is 69% overall but 95%
      on the half of turns it is most sure about is already deployable, with the other half
      escalating. This is what the cascade buys TODAY, before any new data.

    cd apps/controlplane-api && ../../.venv/bin/python -m perception.curve
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from perception.dataset import Row, artifacts_root, load_rows
from perception.dom_witness import TfidfCentroidWitness, extract_tokens
from perception.encoders import get_encoder
from perception.prototypes import PrototypeBank

#: Fixed seed: the subsample must be the same across k so the curve measures data, not luck.
SEED = 20260722


def _loo(rows: list[Row], tokens: dict[str, list[str]]) -> Optional[float]:
    if len(rows) < 2:
        return None
    hits = 0
    for i, row in enumerate(rows):
        train = [r for j, r in enumerate(rows) if j != i]
        witness = TfidfCentroidWitness().fit((r.state, tokens[r.filename]) for r in train)
        if witness.predict(tokens[row.filename]).label == row.state:
            hits += 1
    return round(hits / len(rows), 4)


def _loo_visual(rows: list[Row], vectors: dict[str, list[float]]) -> Optional[float]:
    if len(rows) < 2:
        return None
    hits = 0
    for i, row in enumerate(rows):
        train = [r for j, r in enumerate(rows) if j != i]
        bank = PrototypeBank("enc").fit((r.state, vectors[r.filename]) for r in train)
        if bank.predict(vectors[row.filename]).label == row.state:
            hits += 1
    return round(hits / len(rows), 4)


def c1_learning_curve(rows: list[Row], tokens: dict[str, list[str]],
                      vectors: dict[str, list[float]], *, depth: int = 6) -> dict[str, Any]:
    """Accuracy vs examples-per-class, over a FIXED class set.

    Restricting to "classes with >= k examples" as k grows would change the class set at every
    point and measure task difficulty rather than data volume — an easy way to draw a curve that
    means nothing. So: pick the classes deep enough to subsample once, then vary the depth.
    """
    counts = Counter(r.state for r in rows)
    deep = [c for c, n in counts.items() if n >= depth]
    if len(deep) < 3:
        return {"classes": len(deep), "note": f"fewer than 3 states have {depth} examples"}

    rng = random.Random(SEED)
    by_state: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.state in deep:
            by_state[row.state].append(row)
    for state in by_state:
        rng.shuffle(by_state[state])

    points = []
    for k in range(2, depth + 1):
        subset = [r for state in by_state for r in by_state[state][:k]]
        points.append({
            "examples_per_class": k,
            "n_rows": len(subset),
            "dom": _loo(subset, tokens),
            "visual": _loo_visual(subset, vectors) if vectors else None,
        })
    return {"classes": len(deep), "class_names": sorted(deep), "points": points}


def c2_accuracy_by_depth(rows: list[Row], tokens: dict[str, list[str]],
                         vectors: dict[str, list[float]]) -> dict[str, Any]:
    """Is 69% one population or two? Scored with the FULL corpus training each time — only the
    evaluated rows are bucketed, so this measures how well a state is known, not how much data
    the model had."""
    counts = Counter(r.state for r in rows)
    scorable = [r for r in rows if counts[r.state] >= 2]
    buckets: dict[str, list[Row]] = {"2 examples": [], "3-4 examples": [], "5+ examples": []}
    for row in scorable:
        n = counts[row.state]
        key = "2 examples" if n == 2 else ("3-4 examples" if n <= 4 else "5+ examples")
        buckets[key].append(row)

    out = {}
    for name, group in buckets.items():
        if not group:
            continue
        dom_hits = 0
        vis_hits = 0
        for row in group:
            train = [r for r in scorable if r.filename != row.filename]
            witness = TfidfCentroidWitness().fit((r.state, tokens[r.filename]) for r in train)
            dom_hits += int(witness.predict(tokens[row.filename]).label == row.state)
            if vectors:
                bank = PrototypeBank("enc").fit((r.state, vectors[r.filename]) for r in train)
                vis_hits += int(bank.predict(vectors[row.filename]).label == row.state)
        out[name] = {"n": len(group), "states": len({r.state for r in group}),
                     "dom": round(dom_hits / len(group), 4),
                     "visual": round(vis_hits / len(group), 4) if vectors else None}
    return out


def c3_precision_at_coverage(rows: list[Row], tokens: dict[str, list[str]]) -> dict[str, Any]:
    """What precision can we already buy by ABSTAINING?

    The deployable question is not "how accurate is the witness" but "how accurate is it on the
    turns it chooses to answer". Sort by the witness's own clarity, take the top X%, measure
    accuracy there. The remainder is not a failure — it is an escalation, which the ladder
    already knows how to handle and which the teacher is there to absorb.
    """
    counts = Counter(r.state for r in rows)
    scorable = [r for r in rows if counts[r.state] >= 2]
    if len(scorable) < 2:
        # An empty corpus is a configuration problem (wrong artifacts root), not a result.
        # Say so rather than crashing three frames deep in a slice.
        return {"n": len(scorable), "points": [],
                "note": "no scorable rows — check OBSERVER_ARTIFACTS_DIR"}
    scored = []
    for i, row in enumerate(scorable):
        train = [r for j, r in enumerate(scorable) if j != i]
        witness = TfidfCentroidWitness().fit((r.state, tokens[r.filename]) for r in train)
        pred = witness.predict(tokens[row.filename])
        scored.append((pred.clarity, pred.label == row.state))
    scored.sort(key=lambda x: -x[0])

    out = []
    for coverage in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        take = max(1, int(len(scored) * coverage))
        window = scored[:take]
        out.append({
            "coverage": coverage,
            "n_acted": take,
            "precision": round(sum(1 for _c, ok in window if ok) / take, 4),
            "clarity_floor": round(window[-1][0], 4),
        })
    return {"n": len(scored), "points": out}


def run(*, encoder_name: str = "apple", depth: int = 6) -> dict[str, Any]:
    rows, census = load_rows()
    rows = [r for r in rows if r.artifact_path]
    tokens = {r.filename: extract_tokens(r.artifact) for r in rows}
    rows = [r for r in rows if tokens.get(r.filename)]

    vectors: dict[str, list[float]] = {}
    try:
        encoder = get_encoder(encoder_name)
        for row in rows:
            if row.screenshot_path:
                vec = encoder.embed(row.screenshot_path)
                if vec:
                    vectors[row.filename] = vec
        flush = getattr(encoder, "flush", None)
        if flush:
            flush()
    except Exception:
        vectors = {}
    # The visual curve is only honest on rows BOTH witnesses can see.
    vis_rows = [r for r in rows if r.filename in vectors]

    return {
        "census": census,
        "c1_learning_curve": c1_learning_curve(vis_rows or rows, tokens,
                                               vectors if vis_rows else {}, depth=depth),
        "c2_accuracy_by_depth": c2_accuracy_by_depth(rows, tokens, vectors),
        "c3_precision_at_coverage": c3_precision_at_coverage(rows, tokens),
    }


def _print(report: dict[str, Any]) -> None:
    c1 = report["c1_learning_curve"]
    print(f"\nC1 — learning curve over a FIXED set of {c1.get('classes')} states")
    if c1.get("points"):
        print("   examples/class   rows   dom     visual")
        for p in c1["points"]:
            vis = f"{p['visual']:.1%}" if p.get("visual") is not None else "—"
            print(f"   {p['examples_per_class']:>12}   {p['n_rows']:>4}   "
                  f"{p['dom']:.1%}   {vis}")

    print("\nC2 — is 69% one population or two? (accuracy by how well-observed the state is)")
    for name, b in report["c2_accuracy_by_depth"].items():
        vis = f"{b['visual']:.1%}" if b.get("visual") is not None else "—"
        print(f"   {name:14} n={b['n']:3} ({b['states']:2} states)  dom {b['dom']:.1%}  visual {vis}")

    print("\nC3 — precision if we only act on the turns we are most sure about")
    print("   coverage  acted  precision  clarity floor")
    for p in report["c3_precision_at_coverage"]["points"]:
        print(f"   {p['coverage']:>7.0%}  {p['n_acted']:>5}  {p['precision']:>8.1%}  "
              f"{p['clarity_floor']:>12.2f}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Is data the bridge? Learning curve + coverage.")
    ap.add_argument("--encoder", default="apple")
    ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    report = run(encoder_name=args.encoder, depth=args.depth)
    _print(report)
    out = Path(args.out) if args.out else artifacts_root() / "derived" / "perception_curve.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"\nfull report: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
