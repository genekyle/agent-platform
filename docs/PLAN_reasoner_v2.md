# PLAN — Reasoner v2: the planner that grades itself against reality

**Status: design adopted 2026-07-17 (operator-approved). PARKED 2026-08-26 — S06–S09 entirely
unstarted (LEARNINGS:1924), and planner priority re-scoped by `PLAN_generalization_v1.md` §2 P4:
the live ladder/cadence gets reliable first (consultation as input, SESSION_17), and a taught
Plan contract waits until single-step + escalation measurably stalls. Originally queued behind
Controller v1** — v2's
offline pieces (contracts, assembler, grader) may be built in parallel any time, but its live
pieces gate on v1's journal flowing (Session 02) and reuse v1's teaching gate (Session 04) and
replay harness (Session 05) at a higher altitude. Session briefs: `docs/sessions/SESSION_06`–`09`.
Career Search only, same as v1 — no new domain until a scenario family has graduated.

**Relationship to v1 in one sentence:** v1's `decide()` answers *"what single intent now?"*;
v2's planner answers *"what sequence of states gets us to the goal, and why do we believe that?"*
— it emits the itinerary, and the v1 loop drives each leg. The planner never emits clicks, and it
never replaces `decide()`; it feeds it.

---

## 0. The shape: four pieces, not one model

The proven planner architecture is a separation of concerns. The model is deliberately the least
interesting part — it starts as a prompted frontier call behind the same HTTP-endpoint contract as
everything else (invariant #6) and gets distilled only when the prompt provably ceilings.

1. **The context assembler** — builds a `ContextPack`: tiered, provenance-tagged, stable
   serialization (the prompt surface today, the training feature set tomorrow — the
   `SemanticQuestion`/`Bundle` trick, third use).
2. **The Plan contract** — a frozen data artifact. Plans are journaled records first, model
   outputs second. Every field exists because something downstream trains on it.
3. **The divergence grader** — a pure function that grades every executed plan step against what
   actually happened, using signals the v1 loop already produces. This is where "learning from
   journal entries" stops being an aspiration and becomes a mechanical process.
4. **The critique loop** — teacher critique before execution, self-critique after, both journaled
   in a structured form so the *critiquing function itself* is learnable. This is how the system
   eventually learns to be its own teacher.

## 1. Contracts (`packages/interaction/interaction/plan.py`)

`PLAN_SCHEMA_VERSION = "v1"`. Frozen dataclasses, versioned like `contract.py`:

```python
@dataclass(frozen=True)
class ContextItem:
    source: str        # bundle | recipe | lessons | transition | plan_memory | postmortem | probe
    key: str           # e.g. "workday_lessons", "edges:workday_my_information", "plan:workday_apply/3"
    content: str       # serialized, redacted
    tier: int          # 0 = minimal core, 1+ = earned layers (see §4)

@dataclass(frozen=True)
class ContextPack:
    task: str
    goal_text: str
    state: str | None
    fingerprint: str | None
    items: tuple[ContextItem, ...]     # ordered, tier-0 first
    # serialization is stable and versioned: pack_to_prompt(pack) -> str

@dataclass(frozen=True)
class PlanStep:
    index: int
    subgoal_state: str                 # a page_state_registry id — plans move state to state
    means: str                         # "program:<task>/<state>" | "decide" | "exploration"
    precondition: tuple[str, ...]      # states we must be in to attempt this hop
    success_test: tuple[str, ...]      # states that count as this hop landing (feeds loop verify)
    rationale: str                     # one sentence
    evidence: tuple[str, ...]          # ContextItem keys the rationale cites — the teachable part

@dataclass(frozen=True)
class Plan:
    task: str
    origin_state: str
    goal_state_tests: tuple[str, ...]  # TaskSpec terminal conditions restated
    steps: tuple[PlanStep, ...]        # horizon 3–5; longer plans are split, not allowed
    rung: str                          # cache | model | teacher
    confidence: float
    grounded: bool                     # every non-exploration hop exists in the transition model

@dataclass(frozen=True)
class StepVerdict:
    index: int
    executed: bool
    landed_state: str | None
    ok: bool                           # landed ∈ success_test AND leg outcomes verified
    divergence: str | None             # taxonomy tag (§3) when not ok

# PlanRecord = Plan + ContextPack digest + tuple[StepVerdict] + critique/postmortem refs,
# appended fingerprint-joined to cache/plan_journal.jsonl (journal.py rules: append-only,
# best-effort, redacted, no row without a fingerprint).
```

Two hard rules baked into the contract. **State granularity:** `subgoal_state` must be a
registry id — the planner plans hops through the state graph; the v1 loop (programs + `decide()`)
drives within each hop. **Evidence or it didn't happen:** a rationale with no `evidence` keys is
valid but scored as uncited — §4 uses citation rates to prune context, and the distillation
target is `(ContextPack → Plan + rationale + evidence)`, chain-of-thought with receipts.

## 2. Grounding: plans are checked against the world model before they fire

`state_transition.predict` is the substrate the plan validator walks. For each consecutive hop
`(state_a → state_b)`: if the transition model (or the recipe's `expect` edges) contains it, the
hop is grounded; if not, the step's `means` MUST be `"exploration"` — and exploration steps are
exactly what triggers teacher mode (the ladder's R4), never silent autonomous attempts.
`Plan.grounded` is the AND over non-exploration hops. An ungrounded plan that isn't flagged
exploration is rejected before execution — this is the check that kills hallucinated plans at
$0, and it is why the planner needs no tree search in v2: the transition table does one-hop
feasibility; full search waits until single-hop grounding + replanning measurably stalls.

Replanning rule (mirrors v1's escalation-by-outcome): a `StepVerdict` with `ok=False` →
re-observe once, then **replan from the landed state** (not resume — the old plan's preconditions
are void). Two consecutive failed plans on the same task → escalate a rung, same as v1.

## 3. What makes this a learner — the five feedback loops

This section is the point of v2. "Understanding" is the eventual goal; what we build now is its
efficient operational proxy: **predict → justify with citations → get graded by reality → record
why you were wrong → retrieve that record next time**. Each loop below is mechanical — none
requires a human labeler in steady state.

**Loop 1 — reality grades every plan, step by step, for free.** The v1 loop already verifies
landed-state and protocol outcomes. The divergence grader (`controller/plan_grader.py`, pure
function) folds those signals into `StepVerdict`s. Every executed plan therefore becomes a
labeled training row — plan vs. what-actually-happened — at zero labeling cost. Divergence rows
are the densest data in the system: they carry the exact context, the exact prediction, and the
exact miss. Divergence taxonomy v1 (extend the way `Outcome` members were earned, never rename):
`wrong_next_state` (edge existed, went elsewhere), `stale_program` (means failed), `unmet_precondition`,
`overlong_horizon` (world changed mid-plan), `ungrounded_hop` (validator miss), `goal_mistest`
(plan "done" but TaskSpec disagrees).

**Loop 2 — post-mortems compound into the lessons corpus.** On every divergence, whoever is the
top active rung (teacher first; later the critic) writes ONE structured `PostMortem`:
`{divergence_tag, state, sentence, fix_kind ∈ {context_missing, plan_wrong, world_changed,
program_stale}}` — journaled AND appended to the state-family's lessons (the mechanical version
of how the LESSONS dicts were hand-written). Retrieval is exact-key (`(task, state_family)`), so
the next plan over the same terrain sees its predecessors' mistakes as tier-1 context. Falsifier
built in: **if divergence rate on a state-family does not fall as its post-mortems accumulate,
retrieval keying is wrong** — first suspect the key, not the model.

**Loop 3 — plan-level corrections are dense DAgger.** v1's propose-approve teaches one intent per
correction. v2 proposes the *whole plan* before execution; a teacher correction re-orders or
re-grounds an entire trajectory — one correction, many hops of signal, collected on the states
the planner actually reaches (the same distribution-shift argument as v1 §4, one altitude up).
Structured `Critique` record: `{verdict ∈ {approve, edit, reject}, edited_plan?, per_step_notes,
cited_evidence}` — journaled next to the plan it critiques.

**Loop 4 — the critic is trained from loops 1–3, and it is how the system becomes its own
teacher.** The critic's job: `(ContextPack, Plan) → predicted verdicts + divergence risks`,
i.e. *exactly the teacher's critique function*. Its training rows are exactly what loops 1–3
journal: plans with graded verdicts (loop 1), post-mortems (loop 2), teacher critiques (loop 3).
Ladder of teacher graduation: teacher demonstrates plans → teacher critiques the student's plans
before execution → teacher on escalation only, self-critique via the grader → **local critic does
pre-execution critique**, teacher audits samples. Claude never stops teaching novel terrain (R4
never closes); the critic takes over the *routine* critiquing, which is most of it. Promotion of
the critic follows the same shadow-agreement discipline as everything else: it shadows the
teacher's critiques until agreement gates pass.

**Loop 5 — the context itself learns.** Because every rationale cites `evidence` keys, we get a
citation ledger for free: which context items real decisions actually used. Uncited layers are
pruning candidates; cited-but-absent requests (the planner asked for something via a read-only
intent that a tier could have supplied) are promotion candidates. The M5 replay harness runs the
ablation: replay journaled sessions with a context tier toggled, measure plan agreement +
divergence rate. A tier that doesn't move the numbers is cut. "What is the best context" is a
weekly number, not a design debate.

## 4. Context policy — start minimal, earn every layer

**Tier 0 (the minimal core — ships first, already proven to carry `decide()`):** task + goal,
current state + fingerprint, this task's recipe spine (states + `expect` edges), `scan_required`
unanswered set, last-k decision/outcome rows, and **the transition model's ranked edges from the
current state** (the one genuinely new item — it is the world model, and grounding needs it
inline).

**Earned tiers, added ONE at a time through the ablation gate:** tier 1 — post-mortems + lessons
for this state-family; tier 2 — plan memory (this task/state-family's past plans WITH their
verdict summaries — never plans without grades, ungraded memory teaches confidence without
competence); tier 3 — cross-ATS analogies (Workday's lesson offered to AppVault) — cheap to try,
suspect until cited.

**What never goes inline: raw CDP/AX dumps.** The planner reads the world through the same closed
vocabulary as everything else — if its rationale needs page detail, it spends a read-only intent
(`OBSERVE`/`DESCRIBE`/`SCAN_REQUIRED`) and the result enters the pack as a provenance-tagged item
(`source="probe"`). Perception stays behind intents; the pack stays small, stable, and trainable.
This is the agentic-retrieval-over-context-stuffing result, and it is also what keeps the
serialized pack within a distillable size.

## 5. The model rungs (same cascade shape, plan altitude)

- **Cache:** a graded plan for `(task, origin_state)` whose verdicts were all-ok and whose hops
  still ground → replay it. Plans cache exactly like intent programs, and go stale the same way.
- **Student (planner), backstopped by Haiku:** this rung is the **student planner's seat** — the
  central model we are building (PRINCIPLES §9), which leads once trained. Until then it is occupied
  by **Haiku as the cheap backstop** (Sonnet only if Haiku's plans won't ground), prompted with
  `pack_to_prompt`, output parsed strictly into `Plan` (reject: unknown states, click-level steps,
  missing success tests, ungrounded non-exploration hops). Malformed or `confidence < 0.75` →
  escalate. Served behind an endpoint; the distilled planner drops into this seat *above* the Haiku
  backstop (invariant #6) — Haiku is not a student and is not trained further, it is the live
  product's cheap fallback. The **critic** (Loop 4) is the student that becomes the routine *teacher*;
  Haiku never does.
- **Teacher:** Claude — for exploration plans, repeated divergence, and everything novel. Teacher
  plans flow through the same contract, journal, and grader: no private path, which is precisely
  what makes the teaching distillable (spine decision §2.2, unchanged).
- **Human:** consequential gates, credentials, stop-states — inherited from v1 untouched.

## 6. Prerequisites and gates (what must be true before each piece goes live)

| Piece | Gated on |
|---|---|
| Contracts + assembler + grader (S06) | nothing — offline, buildable today |
| Teacher-demonstrated plans, live (S07) | v1 Session 02 done (journal flowing; programs exist) |
| Grounding validator | transition-model readiness gates already in `training.py` (`_TRANSITION_MIN_PAIRS=10`, `_TRANSITION_MIN_REPEATED=5`) — until met, hops ground against recipe `expect` edges only |
| Plan-level propose-approve (S08) | v1 Session 04's gate exists (reused at plan altitude) |
| Critic v0 + ablation (S09) | v1 Session 05's replay harness; ≥ ~30 graded plans in the journal |
| Training the planner/critic models | prompt ceiling demonstrated via flat shadow agreement — not before |

## 7. Deliberately NOT in v2

- **No tree search / MCTS.** One-hop grounding + replan-on-divergence first; search only if that
  measurably stalls (same "cheapest confident tool" principle as everywhere else).
- **No trained planner or critic until the prompt ceilings.** The contracts are the work; the
  models drop in behind invariant #6.
- **No embeddings/vector retrieval.** Exact keys (`task`, `state_family`) until the citation
  ledger shows exact-match systematically missing relevant lessons.
- **No new domains, no FB port, no runtime/loop rewrite** — all still queued behind graduation.

## 8. Falsifying conditions (architecture.md discipline)

- Divergence rate per state-family flat while its post-mortems grow → retrieval keying wrong
  (first suspect), then pack serialization.
- Critic shadow-agreement with teacher critiques flat while graded plans accumulate → divergence
  taxonomy too coarse; extend it the way `Outcome` was extended.
- Planner rationales systematically cite nothing → the pack's serialization isn't legible to the
  model; fix `pack_to_prompt` before adding any context tier.
- Exploration share of steps not falling on driven state-families → teaching isn't converting
  into grounded edges; suspect the grader's `success_test` semantics.

---

*One-line summary: freeze plans as graded, evidence-citing data artifacts; ground every hop in
the transition model before firing; let reality grade each step for free; turn every miss into a
retrievable post-mortem; teach at plan altitude where corrections are dense; train the critic on
the accumulated grades until it can do the teacher's routine critiquing — and let the citation
ledger, not taste, decide what context the reasoner gets next.*
