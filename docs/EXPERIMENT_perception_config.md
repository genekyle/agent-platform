# Experiment — how should the agent perceive? (branch `perception-eyes-and-ears`, 2026-07-22)

**The question, operator-framed:** the DOM witness turned out stronger than the visual one *and*
better at novelty — so what do we build on top of, is vision still crucial, and is two-modality
cross-checking the right shape at all, or was the genuine need novelty verification all along?

**The metric that was missing, and it was the load-bearing one.** The two-witness case was made on
"rows where the witnesses agree are right 77.9%, split 48.2%" — a real 30-point gap — without ever
comparing it against the DOM witness's **own margin**, which was sitting in the same report at
`margin_separates = 0.774` and never read out. A cross-check that only re-derives a signal you
already had for free is not a cross-check. Everything below is that comparison, done properly.

Corpus: 174 labeled captures / 59 states; 151 scorable (classes with ≥2 examples). Leave-one-out
throughout. `perception/ablation.py` (Q1–Q4) and `perception/ears.py` (E1–E5).

---

## F1 — Agreement is WEAKER than the DOM's own margin on average, and far stronger where it counts

| signal, alone | AUROC separating the DOM's right answers from its wrong ones |
|---|---|
| **DOM margin** | **0.774** |
| DOM clarity (margin ÷ its own scale) | 0.753 |
| agreement (do the witnesses name the same state?) | 0.656 |
| **visual margin** | **0.503 — chance** |

But averaged signals hide conditional ones. Stratified by the DOM witness's own clarity:

| DOM clarity band | accuracy when the witnesses AGREE | when they SPLIT |
|---|---|---|
| **low** (n=50) | 60.0% | **20.0%** |
| mid (n=50) | 73.5% | 68.8% |
| high (n=51) | 94.4% | 73.3% |

**Read it as three separate facts.** (a) On its own, agreement tells you *less* than the number
the DOM already reports. (b) In the band where the DOM is unsure, a split is a 40-point collapse
to **one-in-five** — which is not a hint, it is a near-certain "do not act." (c) The visual
witness's *confidence* is worth exactly nothing (0.503); only its *label* carries information.

## F2 — Cross-modal novelty verification is real but modest, and OR beats AND

Leave-one-CLASS-out (a whole state withheld, then scored), at a **10% false-flag budget** on known
pages — the operating point, not an AUROC:

| configuration | AUROC | recall on genuinely-unseen states @ budget |
|---|---|---|
| DOM alone | 0.700 | 48.3% |
| visual alone | 0.693 | 44.4% |
| **AND** (both must flag) | 0.674 | 39.7% |
| **OR** (either may fire) | **0.743** | **50.3%** |

So the operator's hypothesis is **half right**: cross-modal novelty verification is a genuine
need, and it is worth about **+2 points of recall and +0.04 AUROC** — real, small, and only under
the OR rule. AND (the intuitive "verify it twice" reading) is *worse than either witness alone*,
because demanding both flag turns two mediocre detectors into one strict detector.

**The number that matters more than the comparison: we catch roughly HALF of genuinely-unseen
states, whatever we do.** That is the honest ceiling of prototype-distance novelty on 174
examples, and no fusion rule fixes it. Growing the corpus might; a second modality does not.

## F3 — Vision wins where the DOM is SATURATED, not where it is starved

Bucketed by how many tokens the DOM witness got (the starvation hypothesis: canvas/iframe pages
should be where pixels carry):

| bucket | tokens | DOM | visual | either right | rows only vision got |
|---|---|---|---|---|---|
| starved | 3–215 | 64.0% | 56.0% | 70.0% | 3 |
| middle | 217–502 | 78.0% | 60.0% | 84.0% | 3 |
| **rich** | **505–540** | **58.8%** | 58.8% | 74.5% | **8** |

**The hypothesis was wrong and the inverse is true.** The DOM witness is *worst* on the pages with
the most tokens, and that is where vision rescues the most rows. Reading: a 500-token page is a
long dense form whose distinguishing fields drown in nav, footer and chrome — the Workday phases
again. Note the confound to test before trusting this too far: `_MAX_TOTAL_TOKENS = 400` means
"rich" is really "truncated", so some of that weakness may be the cap rather than the page.

Consequence for the design: **the trigger for consulting vision is not "few AX nodes" — it is
"low DOM clarity"**, which F1 independently identifies as the band where the cross-check pays.
Two experiments, one trigger.

## F4 — The witness is a 5.6× improvement over the recipe matcher we already had

The baseline nobody had measured — `apply_recipe.describe_for_ats(ats, url, page_text)`, on the
same 151 rows:

| | recipe | DOM witness |
|---|---|---|
| names a state at all | **50.3%** | 100% |
| state correct | **11.9%** | **66.9%** |
| platform correct | 22.5% | 98.0% |
| phase correct | 13.2% | 75.5% |

Half of the pages we have actually met, the recipe cannot name at all — it only knows states
somebody wrote a marker for. (Caveat, stated because it flatters the witness: capture artifacts
never stored page text, so the recipe was fed a reconstruction from element text. Live it would
score somewhat better. Not 5× better.)

## F5 — The ears are made of ONE thing: accessible names

Drop-one-namespace, leave-one-out:

| features | state | Δ |
|---|---|---|
| all | 66.9% | — |
| without `route:` | 66.9% | **0.0** |
| without `title:` | 66.9% | 0.0 |
| without `role:` | 66.9% | 0.0 |
| without `ph:` / `flag:` | 66.9% | 0.0 |
| **without `tok:`** (accessible names) | 58.3% | **−8.6** |
| **without `txt:`** (element text) | **68.9%** | **+2.0** |
| `route:` only | 39.1% | −27.8 |

**All of the signal is in the accessible names of the controls.** Route carries a lot alone (39%)
and adds *nothing* once names are present — it is redundant, not useful. And `txt:` is **actively
harmful**: removing it makes the witness better.

That last one also settles **E3, the train/serve skew**, in the best possible way: `txt:` is
precisely the namespace where the trainer (element text) and the runtime (page text) put different
words. Dropping it removes the skew *and* improves accuracy. Two problems, one deletion.

There is also a pleasing convergence here worth stating: PRINCIPLES §6 says drive by **role +
accessible name** because it is the only addressing that survives DOM churn. It turns out the same
signal is the only one worth *perceiving* with. The layer we act through and the layer we see
through are the same layer.

## F6 — The two-stage platform→state model is dead, measured twice

- **Ceiling analysis:** of 50 DOM errors, **1** is cross-platform and **49** are within. A perfect
  free platform gate would take 66.9% → 67.5%.
- **Built and measured:** platform stage 98.7%, then state-within-platform → **57.6%**, i.e. **9
  points worse than flat**. Conditioning starves each sub-model; our scarce resource is data, not
  model capacity.

The corollary is the important part: **the whole remaining error is intra-platform phase
discrimination** — `workday_my_information` vs `questions` vs `voluntary_disclosures`. That is
also exactly what the visual witness cannot do (same chrome). Neither eye nor ear currently solves
the one problem that is left.

---

## The configuration this argues for: a cascade, not a committee

Perception should obey the same rule as every other layer here — *cheapest tool that is confident*
— rather than running both modalities on every turn and averaging them.

**Ears — always on, every turn, $0.** The DOM witness over accessible names. Drop `txt:`. Keep
`route:` as a join key, not as a feature (it earns nothing). Its **margin** is the primary
uncertainty signal, and it is the best single signal we have (0.774).

**Eyes — conditional, ~0.2 s when invoked.** Consult the visual witness only when
1. the DOM's clarity falls in the **low band** (F1: split → 20% accuracy — the strongest gate we
   have), **or**
2. the DOM's novelty is near the ceiling (F2: OR-fusion is the only configuration that beats DOM
   alone), **or**
3. the action is **consequential** (a stricter bar is already the human's rail).

Use only the visual witness's **label**. Never its margin (0.503 — chance).

On this corpus that fires vision on roughly a third of turns instead of all of them, and it fires
it exactly where it changes an answer. Which is the honest reply to "is vision still crucial?" —
**not as a second opinion, as a conditional tie-breaker and half of an OR-gate for novelty.** If
the corpus doubles and F1's low band stops separating, vision drops to a requested diagnostic
(the same status a screenshot has in `PLAN_supervisor` §2) and nothing else in the design moves.

## What to build next, in order

1. **Drop `txt:` from the featurizer.** +2 points and it removes the train/serve skew. One line.
2. **Aim the ears at the field set** (E5, running): 49 of 50 errors are intra-platform phase
   confusions, and what distinguishes those phases is *which form controls are present*, currently
   diluted among hundreds of nav/chrome tokens. Test form-controls-only and form-controls-weighted
   feature sets, and raising the 400-token cap (F3's confound).
3. **Make the eyes conditional** — the cascade above, replacing the always-both observer.
4. **Then, and only then, more data.** F2's ceiling (~50% of unseen states caught) is a
   sample-size problem, not a modelling one.

## What died here

- **Two-stage platform→state** (F6) — measured worse, twice.
- **The starvation hypothesis for vision** (F3) — the inverse is true.
- **AND-fusion for novelty** (F2) — worse than either witness alone.
- **"Two witnesses, always both"** as the default shape (F1) — the value is conditional, so the
  configuration should be too.
- **The visual witness's confidence** as an input to anything (F1) — 0.503 is chance.
