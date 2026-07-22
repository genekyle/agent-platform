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

## F7 — Aiming the ears at the field set: the hypothesis was not refuted, it was never tested

The obvious follow-up to F6 (all remaining error is intra-platform phase confusion) is that what
distinguishes `workday_my_information` from `questions` from `voluntary_disclosures` is **which
form controls are present**, currently diluted among hundreds of nav/footer tokens. Four feature
sets, leave-one-out:

| feature set | state | median tokens |
|---|---|---|
| baseline (all, cap 400) | 66.9% | 382 |
| **drop `txt:`** | **68.9%** | 292 |
| form controls only | **39.9%** | **7** |
| form controls weighted 3× | 68.2% | 199 |
| all, cap 1200 | 67.5% | 196 |

**Read the median-token column before the accuracy column.** "Form controls only" produced *seven
tokens per page*. The filter matched almost nothing — these artifacts store `role: ""` with a
separate `tag`, so form controls are **not identifiable in the corpus at all**. That is not a
refutation of the hypothesis; it is a measurement that never happened.

The real field set does exist — `/scan_required`'s `unanswered` list, which names exactly the
required controls on the page — but it has never been written into a capture artifact. It reaches
the live `Bundle` and stops there. **So the highest-value experiment in this whole document is
currently un-runnable, and the fix is a capture change, not a model change**: record the scan
beside the artifact so the corpus carries the one signal that separates the states we actually
confuse. The always-collect change (`LiveActuator(collect=True)`) is the vehicle; it needs the
scan attached.

Also settled here: raising the token cap does not help (67.5%), so F3's "rich bucket" weakness is
a property of dense pages, not of truncation.

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

## Built on this branch

1. ✅ **`txt:` deleted from the featurizer** (`FEATURE_SET_VERSION = v3`). +2.0 points, and it
   removes the train/serve skew in the same stroke.
2. ✅ **The eyes made conditional** — `Observer.should_consult_eyes()`, thresholds taken from the
   data rather than chosen: `VISION_CLARITY_FLOOR = 0.6` (above the cut the ears are right 80.2%,
   at or below it 40.0%), `VISION_NOVELTY_FLOOR = 0.80` (so the eyes get a say before a belief is
   declared novel, since OR is the only fusion that beats one witness), plus consequential
   actions and unreadable ears. Fires on ~42% of turns on this corpus.
3. ✅ **`not_consulted` is its own agreement value.** A row where the cascade skipped the eyes is
   not a row where they agreed, and the corpus must not conflate the two.
4. ✅ **A lone visual witness can never read as sure** (`VISUAL_ONLY_UNCERTAINTY`), because its
   confidence is chance.

## What to build next, in order

1. **Capture `/scan_required` beside the artifact.** F7: the one signal that separates the states
   we actually confuse is live-only and has never been written to the corpus, which makes the
   highest-value experiment here un-runnable. This is a capture change, not a model change.
2. **Re-run F7 once the corpus has field sets.** If the field-set witness closes intra-platform
   phase confusion, it is worth more than everything else on this list combined — that confusion
   is the *entire* remaining error budget.
3. **Then, and only then, more data for novelty.** F2's ~50%-of-unseen-states ceiling is a
   sample-size problem, not a modelling one, and no fusion rule moves it.

## What died here

- **Two-stage platform→state** (F6) — measured worse, twice.
- **The starvation hypothesis for vision** (F3) — the inverse is true.
- **AND-fusion for novelty** (F2) — worse than either witness alone.
- **"Two witnesses, always both"** as the default shape (F1) — the value is conditional, so the
  configuration should be too.
- **The visual witness's confidence** as an input to anything (F1) — 0.503 is chance.
