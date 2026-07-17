# Session 02 — rung 0, intent programs, loop harness; Indeed replay (Controller M2)

**Read first, in order:** `CLAUDE.md`, `docs/PLAN_controller_v1.md` (§2, §3, §5),
`docs/sessions/SESSION_01_decision_contract.md` (its output is your input),
`apps/mcp/app/intent_api.py` + `apps/mcp/app/protocols.py` (the action surface),
`apps/controlplane-api/apply_recipe.py` (`INDEED_APPLY_RECIPE`, `APPLY_BRANCHES`),
`docs/PROJECT_STATUS.md` (Phase 1's unmet DoD — this session meets it), `docs/LOW_DATA_MODE.md`.

## Objective

**The keystone session.** Build the cheapest rung and the loop around it, then prove both on the
one recipe that is already live-verified: Indeed apply. Two live drives: a **teacher-compile
drive** (you decide step-by-step, journaled, through the Interaction API only) and a **replay
drive** (rung 0 replays the compiled programs with **zero model calls** on the happy path). This
simultaneously satisfies Interaction API Phase 1's Definition of Done (a live drive through the
new endpoints, zero `/eval` in model-made calls) and puts the first real rows in the journal.

## Scope — in

1. **`controller/programs.py`** — `IntentProgram` (frozen): `task, state, guard_fields,
   steps[(intent, params)], expected_exit, version, compiled_from (journal fingerprints),
   verified_at, stale`. Store: one JSON per `(task, state)` under
   `apps/controlplane-api/programs/` — committed recipe-layer DATA. **Programs never contain
   literal values**: params carry field names + value *references* resolved at replay via the
   answers/`apply_fields` layer. Functions: `load_program`, `save_program`, `mark_stale`,
   `compile_from_journal(rows) -> IntentProgram`.
2. **`controller/decide.py`** — `decide(bundle, *, programs, model=None) -> Decision`, rung 0 only
   this session: short-circuits (`done`, `human_required`, `is_branch` → escalate/stop), guard
   check (`scan_required` field-set ⊆ program guard), then emit the program's next intent as a
   Decision (`rung="recipe"`, confidence 1.0). No program / guard mismatch → `escalate=True`
   (rung 1 lands in Session 03; this session escalation surfaces to you, the teacher).
3. **`controller/loop.py`** — the harness from PLAN §5, verbatim shape. Acts **only** through the
   Interaction API endpoints (journaled by construction). Verify = landed state ∈
   `decision.expected_next` + tier-2 Outcome; mismatch → re-observe once (`STALE_STATE_OUTCOMES`),
   then escalate. Stop conditions: `done`, MAX_STEPS=40, `human_required`, budget cap, two
   consecutive escalations. Every step logs a `DecisionRecord`.
4. **Teacher-compile drive (live).** Pick a job from the apply backlog. You are rung 2: at each
   escalation, YOU choose the intent — but emit it as a `Decision` through the same contract and
   act through the same endpoints (no private path). Verified-OK sequences → `compile_from_journal`
   → committed programs for each Indeed state.
5. **Replay drive (live).** A second backlog job: run the loop; happy path must be pure rung 0 —
   **zero model calls, zero `/eval`**. Escalations allowed only at genuinely novel states (journal
   will show them).

## Scope — out

Haiku rung, teach UI, shadow metric, Workday/Greenhouse programs (they compile in later sessions),
`runtime/loop.py`, any non-Career-Search domain.

## Definition of done

- Both drives complete; **final Submit is held for the operator** (consequential gate) on each.
- Replay drive's journal shows every happy-path row with `rung="recipe"`, `cost_usd=0`.
- Phase 1 DoD explicitly checked off in `docs/PROJECT_STATUS.md` (edit it, cite the journal rows).
- Committed programs contain zero PII (grep them for the operator's name/email before staging).
- `journal.summarize()` output pasted into the session log; LEARNINGS.md appended (expect at least
  one: where the recipe's prose and the compiled program disagreed).

## Non-negotiables

- **Live driving costs data and touches a real account**: `make data-check` first; operator present;
  reach states by clicking like a human, never URL-forcing; never auto-solve captcha/2FA — classify
  → escalate, $0.
- Interaction API only — a bespoke `querySelector` anywhere in this session is a failed session.
- Stage explicit paths; `git status` before commit; commit to `main`.
