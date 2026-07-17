# PLAN — Controller v1: the `decide()` that can be taught

**Status: adopted 2026-07-17 (operator-directed). This is PRIORITY #1 — the most important work in
the repo, and it starts now.** Everything else queues behind it (PROJECT_STATUS.md carries the
reordered list). Prove it on **Career Search only**; expansion to any other domain waits until a
scenario family graduates. Per-session execution briefs live in `docs/sessions/` — a fresh working
session should read this doc, then pick up the lowest-numbered unfinished brief.

**What this is.** The most efficient route to a working v1 of the reasoner/controller — the missing
`decide()` in `observe() → decide() → act()` (contract.py:88). Every technique below is
tested-and-proven, either in the field at large or already inside this repo. Nothing here requires
inventing anything; it requires wiring five seams that already exist.

**The claim, up front:** we do not need a trained model, a planner, a memory system, or a loop
rewrite to get v1. We need (1) a frozen Decision contract, (2) a bundle builder that composes the
observation surfaces we already have, (3) a cost-ordered decide cascade shaped exactly like
`resolve_answer`, (4) a teaching mode that captures corrections on the states the controller
actually visits, and (5) one agreement metric. That is the whole v1.

---

## 0. The proven patterns this stands on (why this route and not another)

- **Closed-vocabulary policy + deterministic executor.** The controller emits one `Intent` from the
  frozen vocabulary; deterministic code executes it. This is how every production agent that works
  is built, and it is already our invariant #10. Rejected alternative: free-form action generation —
  unlearnable, unjournalable, already rejected in architecture.md alternative D.
- **Cost-ordered cascade with a confidence floor.** Proven *in this repo*: `resolve_answer`'s rungs
  (exact → normalised → alias → model → ask, threshold 0.75). The controller is the same shape one
  altitude up: recipe → cache → model → teacher → human. Below threshold: ask, don't guess.
- **Learning from intervention (DAgger), not pure behavior cloning.** Pure cloning from teacher
  logs fails on distribution shift — the student visits states the teacher never did, and errors
  compound. The proven fix (robotics, autonomous driving): the *student* drives, the teacher
  corrects at the point of disagreement, and corrections are collected on the student's own state
  distribution. This is why v1's teaching mode is propose-approve, not "watch Claude drive."
- **Skill/program caching (Voyager-style skill library).** A step solved once by the expensive rung
  is compiled into a replayable intent program, keyed by state, verified on replay. That is our R0
  and the endgame's "cached scenarios," made concrete.
- **Shadow deployment + gated per-scenario promotion.** Standard ML-ops. Already specified in
  PLAN_flywheel_first_revolution and DECISION_two-stacks-one-spine; v1 makes the metric computable.

---

## 1. The Decision contract (freeze it like Intent)

One new module: `packages/interaction/interaction/decision.py`. Sibling of `contract.py`, versioned
the same way (`DECISION_SCHEMA_VERSION = "v1"`). Two frozen shapes:

**`Bundle` — the controller's input. This is the OBSERVE payload, and later L4's feature set.**

```python
@dataclass(frozen=True)
class Bundle:
    # goal — "what are we trying to do"
    task: str                    # TaskSpec.name
    goal_text: str               # free-text goal
    done: bool                   # TaskSpec.is_complete(url, page_text)
    # state — "where are we"
    url: str
    state: str | None            # map_url_to_state / describe_tab
    fingerprint: str | None      # route_template join key
    is_branch: bool
    human_required: bool
    branch_note: str
    # recipe context — "what does the corpus say comes next"
    recipe_step: int | None
    next_action: str | None      # the recipe's prose action
    expected_next: tuple[str, ...]
    lessons: str                 # the ATS's LESSONS dict, serialized
    # form ground truth — "what is actually unanswered"
    unanswered: tuple[dict, ...] # scan_required output, verbatim
    # short history — "what just happened"
    recent: tuple[dict, ...]     # last k (decision, outcome) pairs, k=5
```

**`Decision` — the controller's output. One intent, one expectation, one confidence.**

```python
@dataclass(frozen=True)
class Decision:
    intent: str                  # a contract.Intent value — nothing else, ever
    params: dict                 # field/value refs only, no selectors (invariant #10)
    expected_next: tuple[str, ...]  # states this should land on; verify-fail = escalation trigger
    confidence: float
    rung: str                    # recipe | cache | model | teacher — who decided
    rationale: str               # one sentence; journaled, becomes training signal
    escalate: bool               # True → hand up the ladder instead of acting
```

`DecisionRecord` = Decision + Bundle digest + outcome, appended to the **same journal spine**
(a fingerprint-joined `decision_journal.jsonl` satellite, written `journal.py`-style: append-only,
best-effort, never raises into the hot path — spine rule: no row without a fingerprint).

Everything in `Bundle` already exists: `describe_tab()` is the state+recipe half,
`SCAN_REQUIRED_JS` is the form half, `TaskSpec.is_complete` is the goal half, the journal tail is
the history half. The bundle builder (`controller/bundle.py`) is composition, not construction.

## 2. The decide cascade (`controller/decide.py`)

`decide(bundle) -> Decision`. A pure function, no I/O, model calls injected as a `Reasoner`
callable exactly like `resolve_answer(model=...)` — the seam that is already built and tested.
Rungs, cheapest first:

**Rung 0 — recipe (deterministic, $0).** If `bundle.state` is known, not a branch, and a cached
**intent program** exists for `(task, state)` → replay it (see §3). Guard rails before replay:
`scan_required` must agree the program's fields are the unanswered ones; `human_required` and
`done` short-circuit everything. This rung is the graduated path (ladder R0).

**Rung 1 — model (Haiku, budget-gated, ~$0.002).** No cached program: prompt = the serialized
Bundle (which is why Bundle is frozen — the prompt surface IS the future feature set, same trick as
`SemanticQuestion`). Output parsed into `Decision`; malformed or `confidence < 0.75` → escalate.
The model is called behind an HTTP endpoint per invariant #6, so swapping Haiku → local L4 later is
a deployment change.

**Rung 2 — teacher (Claude).** Novel state, stale recipe, rung-1 low-confidence, or two
consecutive verify-fails. The teacher's decisions go through the *same* Decision contract and the
same journal — no private path (spine decision §2.2). Teacher output must end in: a decision that
worked + a cached program + labeled states. Escalation is production of training data, not failure.

**Rung 3 — human.** `human_required` branches, BLOCKED, credentials, consequential gates
(submit/publish), confidence floor breached twice. Never closes.

**Escalation triggers are outcomes, never vibes** — reuse the ladder rules verbatim: landed state
∉ `decision.expected_next` is a verify-fail; `STALE_STATE_OUTCOMES` → re-observe once, then
escalate; `BLOCKED` → straight to human.

## 3. Rung 0's fuel: compile prose steps into intent programs

The known blocker: recipe steps are **inert prose** (`"autofill (atomic) + Continue"` — no code
reads it). Do not hand-translate recipes into a new executable schema — that's a rewrite, and it
re-inertifies the moment a site changes. The proven move is **compile-on-first-drive**:

1. First time through a state, rung 1/2 decides step by step; each verified-OK decision is
   journaled.
2. A verified sequence of decisions within one `(task, state)` — entered at `state`, exited into an
   `expected_next` — is saved as an **intent program**: an ordered list of `(intent, params)` with
   its guard (the `unanswered` field-set it expects). Stored keyed by `(task, state)`, versioned,
   as recipe-layer DATA (site truth lands in the data layer).
3. Next visit, rung 0 replays it. Any step's outcome ≠ OK → program marked stale, escalate to
   rung 1, recompile on success.

**Programs never store literal values.** Params carry field names + **value references** into the
answers/`apply_fields` layer, resolved at replay time — a committed program file must contain zero
PII, the same discipline the journal's `redact()` already enforces.

This is the skill-library pattern with our own vocabulary, and it converts the teacher's expensive
work into the $0 path mechanically — the flywheel's first turn happens *inside the controller*
rather than waiting on model training.

## 4. Teaching mode (`controller/teach.py` + one cockpit surface)

Three modes, in order of build priority:

**Propose-approve (build first — this is the DAgger loop).** The controller runs the loop but every
non-rung-0 Decision is presented before acting: intent, params, rationale, confidence. Operator (or
Claude driving the cockpit) hits approve / correct / escalate. Approved → act. Corrected → act on
the correction AND journal `{proposed, corrected}` as a golden row. The corrections land exactly on
the states the controller actually reaches — the data pure cloning can never give you. A minimal
surface is fine: a CLI prompt or one cockpit panel; the contract is what matters.

**Shadow (build second — it's free once the contract exists).** Teacher drives as today; on every
step the controller *also* computes `decide(bundle)` without acting. Journal both. Agreement % is
the promotion metric and costs nothing extra to collect.

**Replay (build third — it's the regression suite).** Re-run journaled bundles through `decide()`
offline. Deterministic rung-0/cache evals run in CI; model-rung evals run budget-gated. Every
correction ever captured becomes a permanent test case.

## 5. The loop harness (`controller/loop.py`)

Thin and boring, on purpose:

```
while not bundle.done and steps < MAX:
    bundle   = build_bundle(task, tab)          # observe
    decision = decide(bundle)                   # decide (cascade)
    if decision.escalate: hand_up(decision); continue
    outcome  = act(decision)                    # Interaction API only — the one action surface
    verify(outcome, decision.expected_next)     # outcome taxonomy; mismatch → escalate next turn
    journal(decision, outcome)                  # spine
```

This is NOT a rewrite of `runtime/loop.py` (non-goal per the spine decision — the wheel turns
first). It's a standalone harness that imports the same modules; `runtime/loop.py` adopts
`decide()` in Phase 4 when that becomes current work. Stop conditions: `done`, MAX_STEPS,
`human_required`, budget cap, two consecutive escalations to the same rung.

## 6. The metric and the gate

One number: **per-scenario shadow agreement** — % of teacher steps where `decide()` produced the
same intent+field (params-loose match first; exact later). Secondary, already computed:
`verified_rate` and escalation-rate-by-rung from `journal.summarize()`.

Promotion gate (per scenario family, never global): agreement ≥ 90% over ≥ 25 consecutive teacher
steps AND replay suite green → scenario runs propose-approve; N clean propose-approve runs →
rung 0/1 owns it with the ladder armed underneath. Falsifier (inherited from the spine doc): if
agreement stays flat while corrections accumulate, the Bundle is missing a feature the teacher is
using — the bundle shape is the first suspect, not the model.

## 7. Build order — five milestones, five session briefs

| # | Milestone (brief) | Definition of done | Size |
|---|---|---|---|
| M1 | `decision.py` contract + `bundle.py` builder (`sessions/SESSION_01`) | Bundle builds live against an open tab; unit tests on both shapes | ~1 day |
| M2 | Rung 0 + program cache + loop harness (`sessions/SESSION_02`) | Indeed apply (the live-verified recipe) replays end-to-end through the Interaction API with **zero model calls** on the happy path | ~2–3 days |
| M3 | Rung 1 (Haiku) + escalation triggers (`sessions/SESSION_03`) | An unknown state gets a journaled model Decision; low confidence escalates instead of acting | ~1–2 days |
| M4 | Propose-approve teaching (`sessions/SESSION_04`) | One full drive where every model decision is approved/corrected; corrections journaled as golden rows | ~2 days |
| M5 | Shadow metric + replay evals (`sessions/SESSION_05`) | Agreement % computed from a real teacher drive; replay suite runs via a make target | ~1–2 days |

M2 is the keystone: it live-validates the Interaction API (Phase 1's unmet DoD), fills the journal,
and proves the loop shape — three open project priorities paid by one build.

## 8. Deliberately NOT in v1

- **No trained L4.** The journal must fatten first; the cascade's rung 1 slot is where L4 slots in
  later with zero contract change (invariant #6 makes it a deployment swap).
- **No lookahead/search.** `state_transition.predict` stays a verify-side sanity check; planning
  over it is v2, and only if single-step + escalation measurably stalls.
- **No embeddings / vector memory.** Retrieval in v1 is exact: `(task, state)` keys and the
  journal tail. Add similarity retrieval only when exact-match provably misses.
- **No loop rewrite, no new domains, no FB port** — all already declared non-goals; the controller
  proves itself on Career Search where the recipes are live-verified.

---

*One-line summary: freeze a Decision contract, build the bundle from surfaces that already exist,
decide through a resolve_answer-shaped cascade, teach via propose-approve corrections on the
controller's own states, compile every expensive success into a cached intent program, and gate
promotion on shadow agreement. Five milestones, ~1.5–2 weeks, nothing speculative.*
