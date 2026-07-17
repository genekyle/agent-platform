# Session 03 — rung 1 (Haiku) + the escalation ladder wired (Controller M3)

**Read first, in order:** `CLAUDE.md`, `docs/PLAN_controller_v1.md` (§2, §6),
`docs/DECISION_two-stacks-one-spine.md` §3 (the ladder — this session implements its triggers),
`apps/controlplane-api/resolve_answer.py` (the cascade + `Reasoner` seam being copied),
`apps/controlplane-api/anthropic_usage.py` (budget enforcement), Session 02's LEARNINGS entries.

## Objective

Give the controller its first non-deterministic rung: Haiku, budget-gated, prompted with the
serialized Bundle, parsed into a `Decision`, floor-gated at 0.75 — plus the full set of
outcome-triggered escalation rules, so an intent that doesn't land where it expected climbs the
ladder instead of thrashing. Live target: a Career Search state with **no compiled program** —
resume the Greenhouse KKR application (currently at its last field) or a fresh Greenhouse posting.

## Scope — in

1. **The model seam.** `controller/decide.py` gains `model: DecisionReasoner | None` — a
   `Protocol`: `__call__(bundle: Bundle) -> Decision | None`, mirroring `resolve_answer.Reasoner`.
   `decide()` calls it only when rung 0 has no program; `None`, malformed, or
   `confidence < DECISION_CONFIDENCE_THRESHOLD (0.75)` → `escalate=True`. `decide()` stays pure —
   no HTTP inside it.
2. **The Haiku implementation, served per invariant #6.** A control-plane route (e.g.
   `POST /controller/decide_model`: Bundle in, Decision out) wrapping Haiku behind the same
   HTTP-endpoint contract every model gets — swapping in local L4 later must be a deployment
   change. Prompt = `bundle_to_prompt(bundle)` + the closed Intent vocabulary + "emit one intent,
   JSON, confidence, rationale; escalate if unsure." Budget-gated via
   `anthropic_usage.enforce_budget`; over-budget → escalate, never bypass.
3. **Strict parsing.** Reject any intent not in `contract.Intent`, any param key that looks like a
   selector, any missing confidence. A rejected parse is journaled (`rung="model"`,
   `escalate=True`, rationale = parse error) — malformed model output is training signal.
4. **Escalation triggers in the loop** (from the ladder, verbatim): landed state ∉ `expected_next`
   → verify-fail; `STALE_STATE_OUTCOMES` → re-observe once then escalate; `NO_OPTION` →
   `resolve_answer` path; `BLOCKED` → human, always; two consecutive verify-fails at rung 1 →
   rung 2 (teacher); program step failed → `mark_stale` + rung 1 on next visit.
5. **Live drive.** Run the loop on the Greenhouse target: rung 0 covers nothing (no programs yet),
   so every step exercises rung 1. You are standing by as rung 2: when it escalates, decide as the
   teacher (same contract, same endpoints), and let `compile_from_journal` turn the verified
   sequence into the first Greenhouse programs.
6. **Tests.** Parser unit tests (good/malformed/selector-smuggling fixtures); trigger unit tests
   (each Outcome → expected ladder move); a forced-low-confidence test proving the floor escalates
   instead of acting.

## Scope — out

Teach UI (Session 04), shadow metric (Session 05), Workday programs, prompt tuning beyond one
honest iteration (log the prompt version in the journal `detail` instead).

## Definition of done

- ≥1 journaled `rung="model"` Decision that was **acted and verified OK** live.
- ≥1 journaled escalation for each implemented trigger class (force with fixtures where the live
  drive doesn't produce one naturally).
- Cost for the whole session's model calls visible via `anthropic_usage` and under budget.
- First Greenhouse intent programs committed (PII-grepped). LEARNINGS.md appended: what the Bundle
  was missing when Haiku got it wrong — that list is the v1.1 Bundle backlog.

## Non-negotiables

- $5/week cap is enforced code, not vibes — if `enforce_budget` blocks, the session escalates, it
  does not retry around it.
- Never auto-solve captcha/2FA; final Submit held for the operator; `make data-check` before the
  live drive.
- Stage explicit paths; `git status` before commit; commit to `main`.
