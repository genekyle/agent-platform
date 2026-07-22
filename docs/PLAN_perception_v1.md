# PLAN — Perception v1: two witnesses, compositional states, and the muscles that flex

**Status: proposed 2026-07-22 (operator-directed re-anchor).** This is not another layer on the
pile — it **retires a north star** and replaces it with one sized to what we have measured about
ourselves. It supersedes the "the student eventually becomes the reasoner" framing in PRINCIPLES §9
and the endgame paragraph in `PROJECT_STATUS.md`. It does **not** retire the supervisor
(`PLAN_supervisor.md`) or controller v1 — both survive intact and get *more* important, because
under this plan they are the muscles, not the scaffolding.

---

## 0. The pivot, stated plainly

**Old north star:** the inner system gets strong enough that learned scenarios run without Claude at
all; the student becomes its own teacher; "Claude-free" is a per-scenario graduation.

**What two months actually taught us:**

- A local model that *reasons* is not on the table on this machine. Measured 2026-07-20:
  Gemma 4 E2B is a 7.2 GB resident blob that took **50 s to emit one word** and drove the swapfile
  to 14.3 GB; llama3.2:1b fits and scores **0/4**, inventing application answers against a prompt
  that forbids it (LEARNINGS 2026-07-20 (5)).
- The *getting-unstuck* path never needed a model anyway. Rung-0 supervision names the failure from
  a 10-class taxonomy at **$0**, `RecoveryPlay` prescribes the play, `controller/recovery.py`
  executes it. The reasoning that matters most is already deterministic.
- The domain is not novel. It is a small, enumerable set of states that recombine: Indeed apply,
  Workday's six phases, Greenhouse's one long form. The end goals are written down. The failure
  modes are a power law of eight classes mined from our own logs.

**New north star.** Claude is the **novel reasoner, indefinitely and by design** — not a
placeholder for a student that will one day replace it. The local system's job is not to think; it
is to **perceive accurately, act on rails, verify honestly, and know precisely when it doesn't
know**. The flywheel is no longer "one revolution of one automated system." The brain has already
laid the path; what has to get strong is the **inner loops — the muscles** — and muscles get strong
by repetition against resistance, not by growing a second brain.

Three consequences, and they are the whole plan:

1. **Perception gets a second witness.** DOM/AX can lie (custom widgets, iframes, misleading
   labels); pixels can lie differently (misread text, ambiguous forms). Two witnesses with
   *different failure modes* is the only cheap way to earn confidence — and, more importantly, the
   only cheap way to detect that we are somewhere genuinely new.
2. **Generalization comes from the state vocabulary, not from a bigger model.** A new Workday
   tenant is not a new problem; it is known phases wearing new chrome. If states are compositional,
   knowledge transfers for free. If they stay flat strings, every tenant is a fresh cold start.
3. **Every escalation must return a reusable lesson with a scope.** Claude teaching the same
   Workday page ten times across ten tenants is the failure this plan exists to prevent.

---

## 1. The measurement that shaped this plan (run 2026-07-22, before writing a line of it)

Apple's Vision framework ships a native image embedder (`VNGenerateImageFeaturePrint`) — 768-dim,
**0.18 s/screenshot, zero download, zero API cost, already an installed dependency**. Ran it over
every labeled capture whose screenshot still exists on disk (73 captures, 33 states, 18 states with
≥2 examples) and scored leave-one-out 1-NN:

| Question the witness is asked | Result |
|---|---|
| "Which exact page-state is this?" | **55.2%** (32/58) |
| "Which platform/family is this?" (`indeed_*` / `workday_*` / `fb_*` …) | **93.1%** (54/58) |
| "Is this the same state as that?" (same vs different cosine, ~AUROC) | **0.836** |

And the confusion matrix is not noise — it is a specification:

```
workday_my_information ↔ workday_questions ↔ workday_my_experience   (2 each way)
workday_voluntary_disclosures ↔ workday_self_identify                (2)
fb_create_listing_form ↔ fb_listing_condition_picker                 (2 each way)
```

**Vision cannot tell two Workday form phases apart — same chrome, different fields — and the DOM
tells them apart trivially, because it reads the field labels.** That is the complementary failure
mode the whole two-witness idea rests on, confirmed with our own numbers rather than asserted.

**So the visual observer is not a state classifier in v1, and designing it as one would waste it.**
Its three real jobs:

- **Platform / family witness** (93% — strong, and exactly the facet a brand-new tenant preserves).
- **Novelty detector** — distance to every known prototype, the one thing the DOM classifier
  structurally cannot do (NB can only be unsure *between known classes*; it can never be unsure of
  *everything*).
- **Effect witness** — "did the screen change the way the action said it would?" — a second net
  under the treadmill guard, on the same `StateDelta` seam.

**Two data findings that come with it:**

- **The screenshots were never missing — the pointers rotted.** A first pass read
  `screenshot_refs[].path`, found 101 of 174 absent, and concluded the June screenshots had been
  pruned. They had not: those rows carry an absolute path under `apps/mcp-mock/output/…`, the
  directory later renamed to `apps/mcp`. Resolving by filename under the current artifacts root
  finds **all 174**. Corrected 2026-07-22, before it cost us anything; the loader now resolves
  path-then-filename and the census counts how many rows needed the fallback, because a pointer
  that only resolves by fallback is provenance drift worth seeing. Same family as the 2026-07-16
  reckoning: nothing read this data, so nothing noticed it had gone stale.
- **0.836 AUROC with a same-state median cosine of 0.897 against a different-state median of 0.811
  is a narrow band.** FeaturePrint is trained on natural photographs, not UI. Good enough to build
  the seam on for free — and the first thing to re-bench against CLIP once the harness exists.

---

## 1b. S18 results — the bench, run (2026-07-22)

Leave-one-out over all 174 labeled captures / 59 states. Facets scored by **projection**: predict
the state, read the facet off the answer. `make perception-bench`; full JSON in
`<artifacts>/derived/perception_bench.json`.

| witness | state | platform | phase | novelty AUROC | cost |
|---|---|---|---|---|---|
| **dom:tfidf** | **66.9%** | **98.0%** | **75.5%** | **0.700** | free |
| dom:nb *(incumbent)* | 62.9% | 96.7% | 73.5% | 0.500 | free |
| **visual:apple** | 58.3% | 94.0% | 66.9% | 0.693 | free, native, no download |
| visual:clip | 63.6% | 91.4% | 70.9% | 0.685 | ~600 MB, wifi only |
| visual:pixel32 *(baseline)* | 49.0% | 86.8% | 57.0% | 0.683 | free |

**Adopted: `dom:tfidf` + `visual:apple`.** Four things the numbers settled:

1. **Predict the state, then project — never train on a facet.** Training directly on `phase`
   averages `workday_sign_in`, `indeed_login_email` and `login_wall` into one "sign_in" centroid,
   four vendors' chrome smeared together, and scored **62.8%**; projecting off the state scores
   **75.5%**. Facets are a lens on the answer, not a second model.
2. **The incumbent NB is beaten by a TF-IDF centroid on its own features** (66.9% vs 62.9%) — and
   the gap it cannot close is novelty: **0.500 vs 0.700**, i.e. chance. A posterior over known
   classes cannot represent "I have never been here", which is the entire reason for a second
   witness and the reason NB alone could never have been the observer.
3. **CLIP does not earn its download.** It is better at exact state (63.6% vs 58.3%) — which is
   *witness A's* job — and worse at platform (91.4% vs 94.0%) and novelty, which are witness B's.
   It stays in the registry as a one-flag comparison, not as the default.
4. **The falsifier did not fire.** Rows where the witnesses agree are right **77.9%**; rows where
   they split, **48.2%** — a 30-point gap, so disagreement genuinely predicts failure. On a split
   the DOM is right **48%** against vision's **25%**, so witness A leads and the belief is marked
   unsure rather than tie-broken. That is the whole combination policy, and it is measured.

**The one result that argues against this plan's own premise, recorded rather than buried:**
witness B's novelty AUROC (**0.693**) is *not better* than witness A's (**0.700**). The claim in §0
that vision is "the only cheap way to detect that we are somewhere genuinely new" is, on this
corpus, **not supported** — a TF-IDF centroid with class-conditional calibration detects an unseen
state just as well. What witness B has demonstrably earned is the **cross-check** (finding 4), not
novelty supremacy. Two consequences: the escalation trigger should read novelty from *both*
witnesses (it does — the observer takes the max), and §8's "visual novelty doesn't lead the DOM
witness" moves from a falsifier-to-watch to a **finding to re-test as the corpus grows**. If it
still holds at 400 captures, witness B's job shrinks to platform + cross-check and the plan should
say so.

**Novelty is honest at the operating point.** After the calibration fixes (LEARNINGS 2026-07-22 (2)),
in-distribution novelty is median **0.09**, and **3.4%** of known pages trip the 0.90 ceiling —
against a design target of ~10%. Cheap insurance rather than a nuisance alarm.

---

## 2. The embeddings question, answered directly

**Can the Naive Bayes take embeddings? No — and it should not be asked to.** `state_observer.py` is
a multinomial NB: it multiplies *count* likelihoods over sparse discrete tokens
(`route:…`, `role:…`, `tok:…`). A 768-dim dense float vector has no count semantics; feeding one in
either silently degrades to nonsense or requires discretizing the vector into bins, which throws
away the very geometry you wanted. Nothing about NB is worth preserving through that.

**What replaces it is smaller, not bigger.** Three rungs, in the order they should be tried:

1. **Nearest-prototype (centroid) cosine over frozen embeddings.** ~50 lines, no training loop, no
   sklearn, no download. Updates by averaging — as incrementally as NB's counts, so a human
   correction is still an instant update. Works at n=3 examples per class, which is exactly the
   regime we are in. **And it gives distance-based OOD for free**, which is the actual prize.
2. **A linear head (logistic regression) on the same frozen embeddings**, when prototypes plateau.
   Still tiny, still CPU, still interpretable enough.
3. **Never a fine-tuned vision model on this machine.** See Gemma.

**On "specify parameters, weight them, then add embeddings":** that describes a linear model over a
concatenated feature vector `[sparse tokens | dense embedding]` with per-block weights. Do **not**
build it that way. Early concatenation destroys the property this plan is paying for — two
*independent* witnesses with legible, separable failure modes. Use **late fusion**: each witness
emits its own distribution *and its own novelty score*; a single blend weight (tuned on held-out
data, not guessed) combines them, and disagreement is preserved as a first-class signal instead of
being averaged into a smooth lie. Averaging two uncalibrated confidences is how you get a system
that is confidently wrong at exactly the moments you needed it to raise its hand.

**Keep the NB.** It is the sparse-token witness and it is free. It stops being "the L3 model" and
becomes "witness A."

---

## 3. What gets built

### 3.1 Compositional state identity (the generalization lever — no ML at all)

Today: 59 flat strings in `page_state_registry`, with a *convention* of a platform prefix
(`workday_my_information`) that nothing parses. Every new tenant is a cold start.

v1: the flat id stays the primary key and is **never renamed** (same discipline as `Outcome` and
`FailureClass`), and gains **facets** alongside it:

```
domain    career_search
platform  workday            # the ATS/vendor — what a new tenant preserves
phase     personal_information
condition form_ready | validation_error | submitted | blocked
variant   generic | tenant:<slug>
```

Then: recipes and programs bind to **(platform, phase)**, tenant differences are *overrides* on that
binding, and the visual witness — which is 93% at platform and weak at exact state — reports at the
facet it is actually good at. A brand-new Workday tenant arrives as `workday/*/tenant:acme` and
inherits everything not explicitly overridden. **This is the single largest generalization win in
the plan and it costs zero model calls.**

### 3.2 The observer: two witnesses, separately calibrated

`perception/` — a new package alongside `interaction/`:

- **Witness A (DOM/AX)** — the existing NB plus page text and `ax_summary` identities. Sharp at
  phase and condition. Reports `P(state)` + its own margin.
- **Witness B (visual)** — frozen encoder (Apple Vision v0, CLIP if it earns the download) →
  prototype bank per state facet → `P(platform)`, `P(state)`, **and `novelty = distance to the
  nearest prototype`**. Encoder is behind an interface from day one; the prototype bank is a plain
  JSON artifact, same conventions as every other model artifact here.
- **Combination policy** (explicit, tested, not a heuristic buried in a caller):
  agree + both above threshold → proceed · one confident, one weak → proceed only on a **reversible**
  action, else re-observe · disagree → re-observe once, then escalate · either reports strong
  novelty → retrieve episodes, then escalate · **irreversible (Submit) → strict threshold on both,
  always** (this last one is the human's rail and does not move).

### 3.3 The belief state — five uncertainties, not one number

The current `Bundle` carries `state` as a single string and confidence as a single float. The system
is uncertain about **five different things** and collapsing them loses exactly the information the
recovery ladder needs to pick a rung:

| Axis | "I don't know…" | Who answers it |
|---|---|---|
| `state` | …where I am | two witnesses + transition prior |
| `element` | …which control to touch | AX resolve / grounding |
| `answer` | …what value is correct | `resolve_answer` rungs → human |
| `effect` | …whether the last action worked | `StateDelta` + supervisor |
| `novelty` | …whether I have *ever* been here | visual distance + fingerprint miss |

`BeliefState` is a frozen dataclass carrying those five plus the transition prior from the recipe
edge (`expected_next` already **is** the prior — it just is not currently treated as one). It is an
added field on `Bundle`, backwards-compatible, journaled on the same row. The controller's `decide()`
reads belief instead of a bare `state`.

### 3.4 Episodic retrieval

"Have we been somewhere like this before, and what worked?" Retrieve by (platform, phase) facets +
route template + fingerprint + visual distance, over the decision/intent journals we already write.
Feeds two consumers: the Bundle (cheap prior — most turns), and the escalation package (the
expensive one). This is the rung between "the local stack is confident" and "pay Claude."

### 3.5 The lesson contract — what an escalation must return

An escalation that returns a click is paid for once. An escalation must return a **typed `Lesson`**:

```
kind    state_label | visual_prototype | field_alias | recipe_edge | recovery_rule | tenant_patch
scope   universal | platform:workday | tenant:acme
```

Accepted **only after its prediction verifies** (the supervisor already decides that), then written
to the artifact its `scope` names. Cached by question meaning + state facets + fingerprint, so ten
Workday tenants teach the page **once**. This is PRINCIPLES §10 (the Open Brain) given a delivery
address: §10 says the teacher reasons on the record; the lesson contract says *where the record
lands so it is reused instead of re-derived*.

---

## 4. Already built — do NOT rebuild (the audit, so nobody re-invents)

Most of the incoming design is already in this repo. What is genuinely new is §3.1–§3.5 above and
nothing else.

| Incoming ask | Already exists | Verdict |
|---|---|---|
| "recipes as state graphs; edges with pre/post-conditions" | `apply_recipe.py` (states + `expect` edges + lessons), `programs/*.json`, `state_transition.py` | **built** — add facets (§3.1), not a rewrite |
| "semantic actions, not selectors" | `interaction/contract.py` (`Intent` + `INTENT_PARAMS` + `check_intent_params`), the journaled endpoints | **built and enforced** (PRINCIPLES §8) |
| "observe → act → verify loop" | `controller/loop.py` + `select_stage/verifier.py` + tier-2 outcomes | **built** |
| "name what went wrong; recovery ladder" | `interaction/supervision.py` (10 classes, 7 plays), `controller/recovery.py`, `controller/unexpected.py` | **built 2026-07-20** — this plan feeds it better inputs |
| "belief carries expected-next" | `Bundle.expected_next` from recipe `expect` edges | **built** — reframe as a prior (§3.3) |
| "escalate only high-value cases" | `DECISION_CONFIDENCE_THRESHOLD`, `unexpected.respond`, `handoff.emit_escalation` | **built** — add the novelty trigger |
| "structured teacher response, both sides kept" | `Decision.rationale`/`evidence`, `DecisionRecord.proposed_*`, `teach.py` golden rows | **built** (§10) — add `Lesson.scope` (§3.5) |
| "shadow mode, staged promotion" | `controller/shadow.py`, `metrics.py`, `CONTROLLER_PROMOTION.md`, per-class gates | **built** — reuse verbatim |
| "an event bus / a second corpus" | the append-only journals | **do not build** — see the 2026-07-16 reckoning |

---

## 5. Sessions

Numbering continues the supervisor's (S11–S16). **S22 is not last** — reps interleave: run it once
after S18 and again after S20. The muscles are the point; everything else exists to make one rep
worth more.

| # | Session | Cost | Live? |
|---|---|---|---|
| **S17** | **Compositional state identity.** ✅ **derivation built 2026-07-22** — `perception/facets.py`: the closed vocabulary (domain / platform / phase / condition / variant), derived not stored, with `platform` taken from the live host before the state-id prefix and `variant` (tenant) only ever from the host. **Owed:** the registry columns + backfill, and recipes/programs resolving by `(platform, phase)` with tenant overrides. | $0, offline | no |
| **S18** | **The perception bench.** ✅ **done 2026-07-22** — `perception/bench.py` + `make perception-bench`: leave-one-out at state level with facet **projection**, leave-one-CLASS-out novelty AUROC, and the agreement/disagreement split, over any encoder and either DOM model family. Results and the encoder decision in §1b. | $0, offline (CLIP download wifi-only) | no |
| **S19** | **The observer + `BeliefState`.** 🔨 **core built 2026-07-22** — `interaction/belief.py` (five axes, the novelty ceiling, `belief_to_prompt`), `perception/observer.py` (the combination policy), `perception/train.py` (fit → artifact → `load_observer()`). **Owed:** the `Bundle` field, the journal columns, and running it in shadow inside `run_controller`. | $0, offline | no |
| **S20** | **Episodic retrieval + the `Lesson` contract.** Retrieval into Bundle and escalation packages; typed lessons with scope; the teach-once cache. | ~$0 | no |
| **S21** | **The scoreboard** (§6) into `make controller-evals` + the cockpit. | $0 | no |
| **S22** | **Reps.** Operator-present drives on the apply backlog under PRINCIPLES §11 — controller leads, teacher rides. Capture + label every state (that IS the work), observer in shadow, per-class promotion gates start filling. | live | **yes** |

---

## 6. The scoreboard — what "the muscles are getting stronger" means numerically

Application completion rate is a lagging vanity metric. Track these, per drive:

- **% of turns resolved deterministically** (rung 0) — should rise toward the power law's ceiling.
- **% resolved locally** (rung 0 + witnesses agreeing) — the muscle.
- **Teacher calls per submitted application** — the cost curve that has to bend. This is the number.
- **Recovery success rate, per failure class** — feeds per-class promotion; `AUTONOMOUS_CLASSES` is
  still empty and only reps can fill it.
- **Novelty detection accuracy** — of the pages we flagged novel, how many *were*; of the pages that
  surprised us, how many did we flag. Both directions, or it is not a detector.
- **Success on a previously unseen tenant** — the generalization test that matters. A Workday tenant
  we have never driven, with zero new code.

## 7. Deliberately NOT in v1

- **No local reasoning model.** Settled by measurement (LEARNINGS 2026-07-20 (5)). The student's
  seat (`controller/local_reasoner.py`) stays wired and empty.
- **No fine-tuned vision model.** Frozen encoder + prototypes only.
- **No new corpus, no event bus.** The journals are the spine.
- **No exact-state visual classifier.** The 55% number says do not.
- **No new domain.** Career Search until a scenario family graduates.
- **No renaming of existing state ids, Intents, Outcomes, or FailureClasses.** Facets are added
  beside them.

## 8. Falsifying conditions

- **Facets don't transfer** — a new Workday tenant's states don't land on existing (platform, phase)
  pairs → the facet set is wrong; re-derive it from the states we actually meet, don't add facets by
  intuition.
- **Visual novelty doesn't lead the DOM witness** — if every page the visual witness flags novel was
  already flagged by a fingerprint miss, witness B is redundant; keep the platform witness, drop the
  rest.
- **Disagreement doesn't predict failure** — if turns where the witnesses disagree fail at the same
  rate as turns where they agree, the second witness is decoration. This is the single cleanest test
  in the plan and it is measurable in shadow, before anything acts.
- **Teacher calls per application flat across drives** — lessons are not being reused; suspect the
  `scope` field or the cache key, not the models.
- **Rung-0 share falls as facets are added** — the abstraction is leaking; a more general recipe
  should resolve *more* turns deterministically, never fewer.

## 9. Doc + principle changes this plan owes (pending operator sign-off)

1. **PRINCIPLES §9 amended** (not deleted): the student is the **perception and policy-on-rails**
   cog — witnesses, prototypes, the intent policy — and **not** an eventual reasoner or an eventual
   teacher. "The student becomes its own teacher" is retired: measured, not guessed. Claude is the
   novel reasoner permanently; the ladder's teacher rung never closes **and is no longer framed as
   scaffolding**.
2. **`PROJECT_STATUS.md` endgame paragraph rewritten** to match; priorities reordered with this plan
   beside the supervisor (they are one activity: the supervisor names the failure, perception
   supplies the evidence it names it from).
3. **`LEARNINGS.md`** — the 2026-07-22 entry: the FeaturePrint numbers, the 93%/55% split, the
   screenshot-linkage loss, and why NB does not take embeddings.
4. **Memory** — `feedback_reasoning_roles` and `project_north_star_dashboards_and_errands` both
   still encode the retired framing.

---

*One-line summary: stop trying to grow a brain we cannot host, give the one we have a second pair of
eyes and a vocabulary that generalizes across tenants, make every escalation return a scoped lesson
so it is paid for once — and then do reps.*
