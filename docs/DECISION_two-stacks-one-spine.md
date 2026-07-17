# Decision — two stacks, one spine

**Status: decided 2026-07-16** (operator-directed, following the 07-16 corpus findings). This doc
exists so the convergence target is written down and new code stops straddling two architectures.
Disagree with it the architecture.md way: name an invariant that's wrong or a falsifying condition
that's met.

---

## 1. The finding that forced this

The repo grew **two execution stacks**, and the one that creates value and the one that creates
training data were not the same stack:

- **The runtime loop** (`runtime/loop.py` + `select_stage/`) — the flywheel machine. Sole writer of
  `loop_steps.jsonl` and `selection_telemetry.jsonl`. Has never driven real work (record-only /
  `run_batch` replays; 43 + 101 rows, essentially all synthetic replay).
- **The live-drive path** (Claude the teacher driving MCP/API endpoints) — produced every real
  accomplishment (Indeed submit, Wellington Workday submit, Greenhouse to the last field) and, until
  the journal landed on 07-16, contributed **zero rows to any corpus**. 350+ real actions on
  07-15 alone, invisible to the flywheel.

The event log looked like it bridged them. It doesn't and never can: 1000-line ring buffer, raced by
two processes, no fingerprint/session/outcome, no trainer reads it (LEARNINGS 2026-07-16). The
project's premise — teacher → distill — was structurally broken, not merely under-instrumented.

## 2. The decision

1. **The intent journal is THE corpus spine.** Anything that acts, journals — mechanically
   (`intent_api.journaled` is a route decorator; responses are derived from the journaled record).
   Architecture invariant #9. The June corpora become *joinable satellites* of the journal, never
   rivals: **the state fingerprint is the join key** across journal ↔ captures ↔ loop_steps ↔
   selection_telemetry. A corpus row that can't join to a fingerprint is the event-log mistake again.
2. **One action surface.** The teacher and the autonomous loop call the **same Interaction API**
   (intents → tier-2 protocols → tier-1 primitives). Claude gets no private path: even discovery is
   `/probe`, journaled, and must end in an endpoint + recipe entry + labeled states (PRINCIPLES §8).
   A teacher drive and an autonomous drive differ in *who emits the intent*, nothing else — which is
   exactly what makes the teacher's work distillable.
3. **The runtime loop's future is "policy engine that emits intents."** Phase 4 of
   `PLAN_interaction_api.md`, adopted as the target: the loop's select stage produces an *intent*
   (via L4 once trained; via Haiku/teacher until then), the Interaction API executes it, the outcome
   taxonomy is the loop's verify signal. This is the decided direction — **not current work**; the
   loop is not rewritten until the wheel has turned once (see PLAN_flywheel_first_revolution.md).
4. **The event log is the operator's wall display, forever, and never a corpus.** It's good at that
   job. Different jobs, different files.

## 3. The escalation ladder — the endgame, encoded

Operator-stated end state: learned/cached scenarios (the recipes Claude taught) eventually run
**without Claude**; Claude teaches novel work **indefinitely**; the inner system generalizes across
similar scenarios but escalates when it's stuck or an intent doesn't land on the expected state.

| Rung | Who acts | Cost | When |
|---|---|---|---|
| R0 | **Recipe + cached protocol** (deterministic) | $0 | known state, known field, known widget — the graduated path |
| R1 | **L3 perceives state · L4 emits intent** (local models) | ~$0 | learned scenario families; generalizes to *similar* states |
| R2 | **Protocol-level retry/verify** (tier-2 endpoints) | $0 | intent didn't land — outcome ∈ {not_opened, not_staged, not_committed, committed_unconfirmed}; bounded retry per the widget's own contract |
| R3 | **Haiku, budget-gated** | ~$0.002/call | bounded decisions: select catchall, resolve_answer rung 4, low-confidence L3/L4 |
| R4 | **Claude, the teacher** | $$ | novel site / novel widget / recipe stale / R2–R3 exhausted. Output is never just "the task got done": discovery → endpoint + recipe entry + captured, labeled states. **This rung never closes.** |
| R5 | **Human** | attention | stop-states (captcha/2FA/checkpoint), credentials, irreversible submits per policy, `resolve_answer` = NONE. **This rung never closes either.** |

Rules of the ladder:

- **Escalation is triggered by verification, not by vibes.** The outcome taxonomy
  (`contract.Outcome`) is the trigger vocabulary: `ok` means verified-at-commit; anything else names
  which rung handles it (`not_found` → recipe stale → R4 re-maps; `no_option` → R3 resolve;
  `blocked` → R5). "Intent doesn't match the state it lands on" is precisely a verify-fail, and the
  loop's verifier and the tier-2 outcomes are the same gate at two altitudes.
- **Every escalation feeds the rung below it.** R4's discoveries become R0 recipes and R1 training
  rows; R3's resolve answers write back to the alias table (rung 3 of resolve_answer); R5's
  corrections are golden labels. That IS the flywheel — escalation isn't failure, it's how the
  cheap rungs get built.
- **Graduation is per-scenario, never global.** A scenario family moves to R0/R1 when its models
  pass the promotion gates (PLAN_flywheel_first_revolution.md §Promote) — with R2–R5 always armed
  underneath. There is no "turn Claude off" switch to flip, only scenarios that stop needing R4.

## 4. Component dispositions (what this means for existing code)

| Component | Disposition |
|---|---|
| `packages/interaction` (contract, journal, fingerprint) | **The spine.** Version like a public contract (invariant #5-sibling). |
| Interaction API tiers 1–3 (`intent_api`, `protocols`, `widget_probe`) | **The one action surface.** Phase 2–3 per `PLAN_interaction_api.md`. |
| `apply_fields.resolve` + recipes | **The data layer.** All site truth lands here; selector churn is a data edit. |
| `runtime/loop.py` | **Keep; evolves into the intent-emitting policy engine (Phase 4).** Until then: record-only replay + the harness for shadow-mode evals. |
| `select_stage/` (cache, Haiku SoM, verifier) | **Keep — demoted from spine to component.** It is the element-grounding step *inside* act/protocol execution and the loop's select. Its frozen `ActionId` contract stands; intents expand into it (`contract.intent_expands_to`). |
| `selection_telemetry.jsonl`, `loop_steps.jsonl` | **Keep, as satellites.** Still real corpora for grounding/selection; no longer "THE training corpus." Verify fingerprint joins to the journal. |
| `event_log.py` + EventsConsole | **Keep as wall display.** Fix nothing about it for training purposes; never cite it as evidence again. |
| `state_observer` / `haiku_page_state` / `train_stage_observer` | **The L3 v0 track — the first thing the wheel trains** (PLAN_flywheel_first_revolution.md). |
| Grounding/vision track (`training.py`, model_lib, Florence notes) | **Keep, demoted from "first model."** Near-term earned work: protocol discovery + AX-blind pages + labeling support (amended architecture.md). |
| Movement Playground / diffusion input model | **Parked** until after the first revolution. |

## 5. Non-goals right now

- **No loop rewrite this month.** Phase 4 is a target, not a task; the wheel turns first.
- **No deleting the June corpora or select_stage.** Demotion ≠ removal; they're joinable satellites.
- **No porting FB Marketplace onto the Interaction API yet.** Career Search proves the surface;
  FB is the deliberate *second site* that earns each abstraction's generality (§8's promotion rule,
  applied at domain scale).
- **No new domains** until one scenario family has graduated to R1.

## 6. Falsifying conditions

- If, after two revolutions, journal-trained L4 shadow-agreement stays flat while label volume
  grows, the distillation target is mis-specified — first suspect is the journal record shape.
- If teacher drives systematically route around the API (journal rows per drive falls while work
  still gets done), the surface has an expressiveness gap — find it in the `/probe` log, which is
  exactly what `/probe` exists to reveal.
- If per-scenario graduation never sticks (graduated scenarios keep re-escalating to R4 on
  unchanged sites), R0/R1's verification gates are too weak, and the outcome taxonomy needs members
  we haven't met yet — extend it the way `error` and `committed_unconfirmed` were earned.
