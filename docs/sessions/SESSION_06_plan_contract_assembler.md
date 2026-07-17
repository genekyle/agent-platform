# Session 06 — Plan contract, ContextPack assembler, divergence grader (Reasoner v2, offline)

**Read first, in order:** `CLAUDE.md`, `docs/PLAN_reasoner_v2.md` (§1–§4 are this session's
spec), `packages/interaction/interaction/decision.py` (the contract style being copied),
`apps/controlplane-api/state_transition.py` (`predict` — the grounding substrate),
`apps/controlplane-api/training.py` (readiness gates), recent `docs/LEARNINGS.md`.

**This session is fully offline** — pure contracts, pure functions, fixtures. It can run in
parallel with controller v1 work and in low-data mode. It must NOT touch any file the v1 sessions
own (`controller/decide.py`, `controller/loop.py`, `controller/programs.py`) — if a seam is
needed there, write the seam's spec into your LEARNINGS entry instead of editing the file.

## Objective

Land v2's data layer: the frozen Plan/ContextPack/verdict contracts, the tier-0 context
assembler, the grounding validator, the divergence grader, and the plan journal — all tested
against fixtures built from real journaled Career Search sessions.

## Scope — in

1. **`packages/interaction/interaction/plan.py`** — `PLAN_SCHEMA_VERSION = "v1"`; frozen shapes
   exactly per `PLAN_reasoner_v2.md` §1: `ContextItem`, `ContextPack`, `PlanStep`, `Plan`,
   `StepVerdict`, `PlanRecord`; `pack_to_prompt(pack) -> str` with a snapshot test freezing the
   serialization; divergence taxonomy constants (§3 loop 1) and `PostMortem` + `Critique` shapes
   (records only this session — nothing consumes them yet).
2. **`controller/plan_context.py`** — `assemble(task, bundle, *, transition_model, k=5) ->
   ContextPack`, tier 0 ONLY: goal, state, recipe spine + `expect` edges, unanswered set, last-k
   decision rows, ranked transition edges from the current state. Provenance tag on every item.
   Pure function; all inputs passed in.
3. **`controller/plan_validate.py`** — the grounding validator: walk consecutive hops against
   `state_transition.predict` output ∪ recipe `expect` edges; below the transition readiness
   gates, recipe edges alone are authoritative (flag which authority grounded each hop).
   Non-exploration ungrounded hop → reject with a machine-readable reason.
4. **`controller/plan_grader.py`** — `grade(plan, leg_records) -> tuple[StepVerdict, ...]` — a
   pure fold over the loop's per-leg results (landed state, outcomes) into verdicts + divergence
   tags. Build fixtures from REAL rows in `cache/intent_journal.jsonl` / the decision journal
   (redacted) so the grader is proven against actual drive shapes, not invented ones.
5. **`cache/plan_journal.jsonl`** writer, journal.py rules: append-only, best-effort, redacted,
   fingerprint-joined, never raises into the hot path.
6. **Tests** for every module; plus one end-to-end offline test: assemble a pack from a journaled
   Indeed session → hand-write a 3-hop plan → validate → grade against the session's real
   outcomes → journal → read back.

## Scope — out

Any live driving, any model call, any planner rung, plan caching, critic, ablation. No edits to
shared orientation docs (another session may own them right now).

## Definition of done

- All new tests green; existing suite untouched and green.
- The end-to-end offline test's `PlanRecord` pasted (redacted) into the session log.
- Snapshot test locks `pack_to_prompt` v1 format.
- LEARNINGS.md appended: every place the existing journals lacked a field the grader needed —
  that list is the v1-loop seam spec for Session 07.

## Non-negotiables

- Frozen-vocabulary discipline: extend, never rename; version bumps are deliberate.
- No selectors/JS/PII in any contract field; `redact()` at every value boundary.
- Stage explicit paths; `git status` before commit — **a concurrent session is active on main**;
  you own every staged path, unstage anything you didn't touch. Commit to `main`.
