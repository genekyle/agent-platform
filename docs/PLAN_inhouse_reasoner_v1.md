# PLAN — The In-House Reasoner v1

_Adopted 2026-08-31, **operator-directed**. This plan is the build priority; it supersedes
`PLAN_generalization_v1.md` as priority (that plan's instruments keep running — see §7).
Companion principle change: PRINCIPLES §9 rewritten same day (the restoration)._

## §0 — The directive, recorded

The operator, 2026-08-31, verbatim in the parts that bind:

> "I want the decisions to come from a larger model of course so I think it's about time I
> introduce one or develop one in-house. Claude has become the teacher, creator, UI-direction,
> action-handling, etc. I need to start the transition of reinstating that these are closed loops
> and shouldn't require any human handling or submit gates, or transition handling from the human
> — ever. That was never the point."

> "Fully focus on getting to creating a reasoner, decision-maker model immediately… developing the
> vectors and the embeddings myself until we produce a teacher in-house that is specific to the
> cause."

> "Keep the current iteration where Claude runs some live searches but collects EVERYTHING —
> screenshots, semantics, states, expected states, errors, buttons, ax tree, position,
> interaction profile, etc. The biggest thing is to change our data to now be vector databases for
> this new in-house model and we start today on that. Everything else is now secondary."

Destination restated as invariants:

1. **Decisions come from the in-house system** — precedents, trained heads, and an in-house
   teacher model. Claude is the bootstrap collector/teacher, *scheduled for replacement*, never
   the destination. (This restores original §9; the 2026-07-22 "Claude reasons permanently"
   amendment is retired — see §1.)
2. **Closed loops.** No standing human press — no permanent submit gate, no transition handling by
   the operator. Human involvement is *graduation-gated per scenario*: a scenario that clears the
   measured bars is owned end-to-end by the in-house rung, including Submit.
3. **The data layer is vector-first for the perception/decision corpus, starting 2026-08-31.**
   Every banked row is embedded at write time; the whole historical corpus is backfilled.
4. **Collection is total.** A banked step carries screenshot, page semantics, state + expected
   state, error/outcome, the candidate controls, the AX tree, element geometry, and the
   interaction profile — or the bank is a loud bug.

## §1 — What this restores, what it reverses, what stands

**Restores.** Original §9's destination: *"the student becomes its own teacher; the teacher is
scaffolding for the student, never the destination."* The 2026-07-22 amendment retired those
sentences after measuring two small **general, freestyle-reasoning** models on this machine
(Gemma-2B: 7.2 GB resident, 50 s/word, swap to 14.3 GB; llama3.2:1b: 0/4, invented application
answers). Those measurements stand. The **ceiling inferred from them does not**: they tested
"small model freestyle-reasons about a page," which is not the design here. The design here is
(a) decisions by retrieval over our own journaled precedents — no generative model in the loop at
all — and (b) later, a model that chooses from a **closed intent vocabulary with constrained
decoding and retrieved precedents in context**, which is a categorically easier task than the one
that failed in July. (b) remains a HYPOTHESIS until measured (§8 P4); (a) is buildable today.

**Reverses.**
- "Claude is the novel reasoner, permanently and by design" → Claude is the bootstrap teacher,
  with a stated replacement criterion (§2 M1).
- "The gate is armed, not a target — never feed it" → **the two-bar gate is the eject button and
  we feed it deliberately.** Promotion is the product now.
- The submit gate as a standing human press → graduation-gated per scenario (§5).

**Stands, untouched.**
- **Stop-states forever:** captcha / 2FA / checkpoint / payment / identity-verification walls
  escalate or fail the flow. Nobody builds auto-solving — that is bot-detection bypass and Claude
  will not build it, for any rung, ever. An in-house model inherits the same stop-states. This is
  the one human touchpoint that is a hard line rather than a graduation candidate.
- Bot-safety live-drive rules (§3 clicking, pacing, no URL-forcing), provenance (§1), capture
  hygiene / no secrets (§4), execution-as-API (§8), Open Brain journaling (§10), one authority per
  question (§15), observation-vs-conclusion (§16). These are model-agnostic, and they are exactly
  what made the corpus trainable. The in-house model exists *because* these were followed.
- The $5/week external-spend cap (increasingly moot: the whole point of in-house is $0-marginal
  decisions; the cap now mostly guards the M1 cloud lane).

**Hardware truth (measured 2026-08-31, governs everything):** 8 GB RAM, Apple M3, 26 GB free
disk. A resident 7B model beside two live Chrome sessions swap-thrashes (the July measurement).
Consequences: M0 is retrieval + small heads (megabytes); M1 lane A is a ≤2B model loaded
on-demand; M1 lane B is our weights on rented compute (~$30–80/mo per the standing cloud
strategy). "In-house" means **our weights, our data, our control — not necessarily our silicon.**

## §2 — The three rungs of the in-house brain

### M0 — The Precedent Engine (this week; $0/decision; no new hardware)

Every journaled decision and transition half becomes a vector (§3). A decision is made by
retrieving the k nearest precedents for the current bundle and letting them vote:

- **Input:** the same `Bundle` `decide()` already receives (state, phase, platform, page text, AX
  names, screenshot artifact, censused controls).
- **Retrieve:** k-NN over the vector store, filtered by task family; distance-weighted vote over
  the closed intent vocabulary; target `ref` resolved by the same vote within the winning intent,
  then grounded through the **existing select stage** (the AX layer still finds the node — the
  precedent engine answers WHAT/WHERE-symbolically, never emits selectors; §8 Execution=API is
  unchanged).
- **Confidence = neighborhood agreement × proximity.** Below floor → next rung, exactly like
  every other rung. The engine ABSTAINS honestly; it never guesses on an empty neighborhood.
- **Seat:** the **student seat** in the cascade (above Haiku, below program/cache), the seat §9
  always reserved. Wire shadow-first (it logs what it *would* do beside what the teacher did) for
  the first drives, then it takes real decisions per scenario through the gate (§5).
- This is the selection cache generalized from exact-key to semantic neighborhood — the
  "practiced-recipe fast path" finally covering *near*-repeats, which is where the corpus says the
  volume is.

### M1 — The In-House Teacher (entry criteria below; replaces session-Claude as first escalation)

Two lanes, raced honestly:

- **Lane A (local, $0):** a ≤2B vision-language model (e.g. Qwen2-VL-2B / SmolVLM / Moondream
  class, 4-bit via MLX on the M3), **loaded on-demand**, answering the same `decide()` contract
  with (i) retrieved precedents in context and (ii) **constrained decoding over the closed intent
  vocabulary + censused refs** — it cannot invent an action that isn't on the page. HYPOTHESIS
  P4: this beats the Haiku shadow baseline on the same pairs. If it does not, lane A dies and
  nobody re-litigates it.
- **Lane B (ours-on-rented-GPU, ~$30–80/mo):** LoRA fine-tune of a 7B-class VLM on our journaled
  (bundle → decision + WHY) pairs — *our weights*, served from a rented GPU; capture and driving
  stay on the real local IP (the standing cloud-strategy split). Fine-tune becomes worthwhile at
  roughly ≥2,000 journaled decisions with outcomes (we hold 773 + 659 transition rows today, and
  every drive now banks more).

**Entry criteria for M1 (either lane):** the precedent engine carries ≥50% of decisions on driven
scenarios for a week AND ≥2k journaled decisions exist. **Exit criterion for Claude-as-teacher:**
M1 answers escalations with agreement ≥ the teacher's own replay-consistency on a ≥100-decision
window; Claude then drops to auditor (spot-checks, no seat in the loop).

### M2 — Own weights end-to-end (corpus-gated: ≥5k labeled pairs)

Distill teacher + precedents into small per-scenario-family trained heads (intent policy +
target ranker) — weights created here, from our data, fully ours. The M1 model remains the
fallback general rung. From-scratch *pretraining* of a large model stays out of scope on any
honest accounting of data and compute; nothing in the destination needs it.

## §3 — The vector data layer (starts 2026-08-31)

- **Store, today:** `sqlite-vec` (v0.1.9, verified loading in the venv) at
  `apps/mcp/output/vectors.db` — beside the corpus it indexes, readable by the API the cockpit
  uses. One file, SQL-queryable, zero new services.
- **Store, endgame:** the same schema in **pgvector inside the existing `agentos` Postgres** (one
  store, one server). Requires the one infra change: image `postgres:16` →
  `pgvector/pgvector:pg16` (data is volume-backed on `infra_pgdata`; a 5-minute swap). This is an
  **operator-approved restart**, scheduled deliberately like the 08-27 migration restart — not
  done incidentally.
- **Embedding recipe v0 (all on-device, $0, no downloads):** late fusion of L2-normalized blocks —
  - `vision`: Apple Vision FeaturePrint of the step screenshot (the perception witnesses' own
    embedder — same organ, wider job);
  - `text` (512-d): Apple `NLEmbedding` sentence vector over page title + heading + AX
    names/roles of censused controls + question text;
  - `facets`: hashed categoricals — platform, page-kind, state, phase, task, prior intent.
  - Block weights fit by leave-one-out on the teacher-labeled corpus (§8 P3 ablation says which
    blocks carry signal before anyone argues about it).
- **Backfill:** every existing transition half, decision record, and teacher label embedded
  tonight; counts reported in LEARNINGS.
- **Write-time rider:** banking a row writes its vector at the same choke points that ride
  screenshots (`record_for`, the transition writer) — the crank is the bank, not a batch job
  someone remembers.
- **Scope honesty:** *vector-first applies to the perception/decision corpus.* The ledger — jobs,
  applications, accounts, events — stays relational in Postgres; retrieval joins to it by key. If
  the operator meant more than this by "change our data to vector databases," say so and §3
  widens.

## §4 — Collect EVERYTHING — the completeness contract

The operator's list, made a checklist. A banked step MUST carry every row below; a bank missing
one is a loud bug (refuse-or-alarm, never silent):

| operator's word | field(s) on the row | status — AUDITED 2026-08-31 |
|---|---|---|
| screenshots | before/after screenshot refs | ✅ transitions 612/659 (93%), **0 broken paths**; ❌ decisions **58/773 (7.5%)** — the capture rider reaches `record_for` but most decide() paths pass no capture; close at the seam (`decision_journal.py:209`) |
| states / expected states | `before/after.belief.state`, `expected.kind`, verdict | ✅ 659/659 carry belief |
| errors | verdict, evidence, `mismatch_kind` | ✅ (S20) |
| semantics | title ✅ on row; page text excerpt only in artifact | ⚠️ acceptable via pointer; `changes.page_says` rides the after-half |
| buttons (candidate set) | `candidates` `[(role, name)]` on every observation | ✅ names/roles; see position row for what was stripped |
| ax tree | `artifact` basename → trace JSON | ✅ pointer — **but** `acquisition.accessibility_snapshot` inside artifacts has been failing (`get_accessibility_tree not found`); the real element data is `actionable_elements`. **Bug, now named: fix the AX snapshot stage or retire the dead field.** |
| **position** | bbox of acted control + candidates | ❌ on the ROW — but **present in every artifact** (`actionable_elements[].rect`, `ranked_candidates[].grounding.bbox` + `viewport_state` for px mapping). UN-DEFERRED: lift acted-control bbox into the row at `step_runner.py:107-112` (`Observation.as_row` currently strips to `(role,name)`) |
| interaction profile | `action.mode` (S19) partially | ⚠️ stamp executor profile/protocol on the act |
| phase | `rung` on transitions ✅; `bundle_snapshot.phase` mostly None on old decisions | ✅ going forward |
| vectors | embedding written at bank time | ✅ store BUILT + backfilled today (2,091); write-time rider next: embed from the **Bundle** (which carries `ax_identities`) at `record_for` (`decision_journal.py:110`) and from the Observation at `record_transition` (`step_runner.py:545`) — **not** from `bundle_snapshot`, which lacks the censused controls (measured cost: decisions' text block underperforms transitions' for exactly this reason) |

## §5 — Graduation and gates: the eject buttons

- **Per-scenario graduation is unchanged in mechanism, reversed in intent:** loose ≥0.90 AND
  exact ≥0.85, each over its own ≥25-row window — but now we **drive to feed it**. A scenario
  that clears both bars is owned by the in-house rung **end-to-end, Submit included, no human
  press.** The gate attaches where it already attaches (`interaction.authority`); nothing new to
  build, only a doctrine flip.
- **The scorecard's headline number changes:** **% of decisions made in-house** (by rung, trailing
  7 days) and **# scenarios graduated**. Today: 4.5% in-house-ish (recipe 2.8% + model 1.7%),
  teacher 95.5%, graduated 0. These are the numbers that must move.
- **Stop-states never graduate** (§1). The operator keeps a global pause switch. Everything else
  is the model's to earn.

## §6 — What Claude does until replaced

Runs live collection drives (every step now banks the §4 contract + vector), services
escalations (every one becomes a labeled row — DAgger as always), labels the queue tail, and
builds this plan's code. What Claude **stops** doing: adding human gates (none new, ever),
holding seats M0/M1 can take, and writing "permanently" next to its own name in doctrine.

## §7 — Demotions (explicit, so nothing creeps back silently)

- `PLAN_generalization_v1.md` briefs pause as *priority* (S14–S21 are built; their owed live
  measurements ride ordinary collection drives as byproducts, not as goals).
- Cockpit/UI work only where it feeds labeling or eval throughput (the label queue at 526 is
  training data for M1 — the queue screen earns its keep; new panels do not).
- Reasoner v2 (S06–S09) stays parked — **superseded by this plan** (its planner/critic ideas
  return at M2 as trained heads, not prompt chains).
- OmniParser removal gets scheduled (frees 1.3 GB of the 26 GB — model headroom).
- The Haiku rung stays as-is (backstop, unattended only) and is measured for retirement once M0
  outperforms its shadow agreement.

## §8 — Predictions and falsifiers (per §13 — stated before building)

- **P1 (today):** leave-one-out intent agreement of the precedent engine on the teacher corpus
  lands **55–75%** (the Haiku shadow read 59.5% on weaker features). Below 45% → the feature
  recipe is wrong (run P3 before blaming corpus size). Above ~85% → suspect leakage (P2).
- **P2 (methodology, binding):** evaluation splits **by session/drive**, never randomly — same-
  drive neighbors are near-duplicates and would inflate agreement. A random-split number may be
  reported only next to the split-by-drive number, never alone.
- **P3 (ablation):** score each block (vision / text / facets) alone. Expectation: text+facets
  carry most signal at this corpus size; vision differentiates same-text states (wizard steps).
  Whatever it says, the weights follow the measurement.
- **P4 (M1 lane A):** the ≤2B constrained-decode model with retrieved precedents beats the Haiku
  shadow baseline on identical pairs, at usable latency, without swap. Fails → lane A dies, lane
  B is the path, and the July measurement gets its second confirmation instead of a third retry.
- **P5 (the operator's own claim, testable):** "we have enough for a starter model making good
  educated guesses" — TRUE if P1 lands in range on split-by-drive; the number goes in LEARNINGS
  either way.

## §9 — Today's definition of done (2026-08-31) — CLOSED same day

- [x] Plan adopted; PRINCIPLES §9 rewritten (the restoration); PROJECT_STATUS + CLAUDE.md
      re-pointed; LEARNINGS entry with the day's measurements.
- [x] `vectors.db` exists: **2,091 precedents** (773 decisions + 659×2 transition halves),
      1,264 with vision, 0 broken screenshot refs, 88 s backfill, fully on-device.
- [x] P1 measured, split-by-SESSION (P2 honored; random split reported beside it):
      - **transitions: 0.814/183** vs 0.230 majority; selective@0.9 → **0.970 accuracy at
        0.366 coverage** — a deployable abstaining rung on day one. Ablation: facets 0.809,
        text 0.525, vision 0.504 — the ladder/state structure carries the signal (P3).
      - **decisions: 0.503/773** vs 0.472 majority; random split 0.683 → the leakage gap P2
        predicted is real. Sub-P1-range → per P1's own clause, the recipe is the suspect:
        `bundle_snapshot` lacks the censused controls (`ax_identities` is prompt-invisible and
        never serialized), and 92.5% of rows have no image. Scenario floor confirmed:
        `cornerstone:*` scores 0.000/39 because the scenario lives in ONE session — cross-drive
        transfer needs either more drives or kind-level features, exactly §3.1 of the old plan.
      - **P5 (the operator's claim): CONFIRMED at the transition level** — the corpus already
        supports in-house educated guesses at 97%-when-confident; fine-grained novel-page
        decisions are where collection (not model size) is the binding constraint.
- [x] §4 audit column resolved (table above carries the measured statuses + exact seams).
- [x] Write-time vector rider spec'd at seams (`decision_journal.py:110` `record_for` off the
      Bundle; `step_runner.py:545` `record_transition` off the Observation). Landing next
      session with tests; the backfill CLI covers the gap until then (idempotent, re-run cheap).
