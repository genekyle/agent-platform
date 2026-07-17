# Session 08 — the model planner rung + plan-level propose-approve (Reasoner v2)

**Gates: Session 07 complete; controller Session 04 (propose-approve gate) exists.**
**Read first:** `CLAUDE.md`, `docs/PLAN_reasoner_v2.md` (§3 loop 3, §4, §5),
Session 07's baseline metrics + citation ledger in the log/LEARNINGS,
`controller/teach.py` (the gate being reused at plan altitude),
the controller's model-rung route (Session 03's `decide_model` pattern — copy its serving shape).

## Objective

Give the planner its model rung and its dense teaching loop: Haiku (Sonnet only if Haiku's plans
won't ground) authors plans from the tier-0 pack; every model plan is proposed to the teacher
BEFORE execution; approvals execute, edits/rejections are journaled as structured `Critique`
records — the training rows the critic (Session 09) will learn the teacher's job from. Tier-1
context (post-mortems + lessons) turns on this session, through the front door.

## Scope — in

1. **`POST /planner/plan_model`** — invariant #6 serving: `ContextPack` in, `Plan` out. Prompt =
   `pack_to_prompt` + the state vocabulary for this task + the plan rules (state granularity,
   3–5 hops, success tests, rationale + evidence keys, exploration flags). Budget-gated via
   `anthropic_usage.enforce_budget`. Strict parse: unknown state, click-level step, missing
   success test, or unflagged ungrounded hop → reject; rejected parses are journaled with the
   parse reason (they are training signal, same as v1 Session 03).
2. **Plan-level propose-approve.** Reuse the Session 04 gate one altitude up: render the full
   plan (steps, rationales, cited evidence, grounding authority per hop); teacher verdict
   `approve | edit | reject`. Edits are full replacement Plans; journal the `Critique` with BOTH
   halves (proposed + edited) — never overwrite the proposed half. Approved/edited plans execute
   through the Session 07 pipeline (validate → drive → grade → post-mortem).
3. **Tier-1 context ON:** `assemble(..., tiers=(0,1))` now retrieves this state-family's
   post-mortems + lessons by exact key. Log pack sizes; if tier 1 is systematically uncited by
   the model's rationales, say so in LEARNINGS — that's ablation evidence for Session 09, not a
   reason to silently keep or cut it.
4. **Plan caching (rung "cache"):** an all-ok-verdict plan for `(task, origin_state)` whose hops
   still ground replays without a model call; any failed verdict on replay → stale, back to the
   model rung. Same lifecycle as intent programs, same PII rule (value refs only).
5. **Live drives:** two backlog applications with the model planning, teacher gating every plan.
   Track per-plan: verdict, edit distance (hops changed), divergence after execution.
6. **Tests:** parser rejection fixtures (click-smuggling, unknown states, ungrounded-unflagged);
   critique round-trip; cache staleness; gate policy (which rungs propose — cache does not).

## Scope — out

Critic, ablation harness, context tiers 2+, training any model, autonomous (ungated) model plans
— every model plan is gated this session, no exceptions.

## Definition of done

- ≥2 drives planned by the model rung end-to-end under the gate; ≥1 real edit captured with both
  halves journaled (force a scripted one if you genuinely never disagreed — and say so).
- First plan served from cache on a repeat state, zero model calls, journaled as `rung="cache"`.
- Cost visible via `anthropic_usage`, under budget.
- LEARNINGS.md appended: the edit taxonomy you observed (wrong ordering? bad grounding? missed
  lesson?) — Session 09's critic labels come from exactly this.

## Non-negotiables

- The teacher's edits go through the SAME contract — no hand-fixing the plan in prose and driving
  ad hoc; an unjournaled correction teaches nothing.
- Budget blocks → escalate, never bypass. `make data-check` first; consequential gates hold.
- Stage explicit paths; `git status` before commit; concurrent session active. Commit to `main`.
