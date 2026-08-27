# Session 09 — critic v0 (shadowing the teacher) + the context ablation harness (Reasoner v2)

_PARKED 2026-08-26 — Reasoner v2 unstarted and re-scoped (`PLAN_generalization_v1.md` §2 P4).
Not the "lowest unfinished brief": skip to SESSION_14+._

**Gates: Session 08 complete; controller Session 05 (replay harness) exists; ≥ ~30 graded plans
in `plan_journal.jsonl`** (count them first — if short, run more Session-08-style drives instead
of building on thin data; say so in the log rather than proceeding).
**Read first:** `CLAUDE.md`, `docs/PLAN_reasoner_v2.md` (§3 loops 4–5, §8),
Session 08's edit taxonomy in LEARNINGS, `controller/replay.py` + `controller/metrics.py`
(the harness being extended), `training.py::_stable_split` (reuse; scenario is the split unit —
invariant #7).

## Objective

Close v2's outer loops: a critic that shadows the teacher's plan critiques (the first step toward
the system doing its own routine teaching), and the ablation harness that makes "what context is
best" a computed number. Nothing is promoted this session — we build the instruments and take the
first honest readings.

## Scope — in

1. **Critic v0 — prompted, shadowing, never gating.** `POST /planner/critique_model`:
   `(ContextPack, Plan) → predicted verdict + per-step divergence risks + rationale`, prompted
   with the divergence taxonomy and this state-family's graded history. On every Session-08-style
   drive, the critic runs SILENTLY alongside the teacher's real critique; journal both, joined.
   Metric: critic/teacher agreement on verdict and on which steps were flagged. The critic gates
   nothing until agreement passes the same shadow discipline as everything else.
2. **Ablation harness.** Extend `controller/replay.py`: re-run journaled `(ContextPack, Plan)`
   pairs through the model planner with context tiers toggled (`tiers=(0,)` vs `(0,1)` vs cached
   variants); measure plan agreement vs the journaled accepted plan, grounding rate, and (where
   replayable) divergence. Deterministic parts in `make controller-evals`; model-rung replays
   behind an explicit budget-gated flag with sampling logged (no silent caps).
3. **The citation ledger, computed.** `controller/metrics.py` gains: citation rate per
   ContextItem key across the journal; uncited-tier report; probe-requested-but-absent report
   (§3 loop 5). Output: a ranked keep/cut/promote table for context tiers.
4. **First readings, written down.** Run the ablation + ledger over everything journaled so far;
   put the numbers and the keep/cut verdicts in `docs/PROJECT_STATUS.md` (coordinate with the
   concurrent session before editing shared docs — if contended, put them in the session log and
   LEARNINGS instead, and note the deferral). Update `docs/CONTROLLER_PROMOTION.md` with the
   planner/critic promotion gates (same shape: agreement ≥ threshold over N, replay green,
   per-scenario, never global).
5. **Tests:** agreement metric fixtures; ablation determinism for non-model parts; ledger counts
   against hand-checked fixtures.

## Scope — out

Training the critic or planner models (prompt ceiling not yet demonstrated); critic gating
anything; embeddings; context tier 3; new domains.

## Definition of done

- Critic shadow-agreement number computed from ≥2 real gated drives.
- Ablation table for tier 1 (in vs out) with plan-agreement and grounding deltas.
- Citation ledger with at least one earned keep/cut decision actually taken (context change
  committed through `plan_context.py`, cited to the numbers).
- Promotion gates for planner + critic written. LEARNINGS.md appended: whether §8's falsifiers
  are live (esp. "post-mortems accumulate but divergence flat" — check it explicitly).

## Non-negotiables

- Scenario is the unit of any split (invariant #7; `_stable_split`). Sampling/caps logged, never
  silent. Measure before tuning — a number taken mid-tune means nothing.
- Budget-gated model replays only; over budget → smaller sample, logged.
- Stage explicit paths; `git status` before commit; concurrent session active — own every staged
  path. Commit to `main`.
