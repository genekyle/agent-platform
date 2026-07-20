# Session 12 — the `SupervisorVerdict` contract, rung 0, and taxonomy v1

**Gates: S11 only, and S11 is done** (`interaction/delta.py` landed 2026-07-20). Everything here is
**offline and buildable today** — no browser, no live drive, no model call.
**Read first:** `CLAUDE.md`, `docs/PLAN_supervisor.md` (all of it — §0 is the audit this session
implements, §1 the contract, §2 the cascade), `interaction/delta.py`, `interaction/decision.py`
(the `DecisionRecord` column discipline), `controller/loop.py`, `controller/unexpected.py`,
LEARNINGS 2026-07-20 and 2026-07-19.

## Why this session exists

The loop's only post-action judgment today is `_verify()` returning a **boolean**. A drive that
gets stuck therefore produces `verified: false` and nothing else — no name for what went wrong, so
no playbook to select from, no per-class promotion, and no legible commentary. S11 gave the loop
eyes (`StateDelta`); this session gives it **vocabulary**.

The load-bearing insight, from `PLAN_supervisor.md` §0c: **the taxonomy is already in our logs and
it is a power law.** Eight classes cover every machine-readable stuck moment we have (13
`verified=False` decisions, 23 non-`ok` intents, 34 handoffs) and every incident in LEARNINGS since
07-12. So the supervisor's core skill is *classification + playbook selection*, which a
deterministic table does for free on most turns — not open-ended reasoning.

## Scope — in

1. **`packages/interaction/interaction/supervision.py`** — the frozen contract from
   `PLAN_supervisor.md` §1: `SUPERVISION_SCHEMA_VERSION`, `FailureClass`, `RecoveryPlay`,
   `SupervisorVerdict`. Both enums are **closed vocabularies**, extended the way `Outcome` members
   were extended (earned from an observed incident, never renamed). `recovery_params` passes
   through `looks_like_selector` exactly like `Decision.params`; `rationale` is held to
   `is_real_rationale` (§10).
2. **Rung 0 — the deterministic supervisor.** A pure function
   `(StateDelta, outcome, verified, expected_next, landed_state) -> SupervisorVerdict`, by table.
   The seeds, each traceable to a real incident:
   - `outcome ∈ STALE_STATE_OUTCOMES` → `CONTROL_NOT_FOUND` (24 occurrences — the largest bucket)
   - `verified and delta.moved` → `NONE`
   - `verified and not delta.moved` → `NO_PROGRESS` (the 07-19 treadmill, finally *named* rather
     than merely counted) → play `SETTLE_AND_RETRY`
   - `ok` + an empty scan/identity set → `STALE_TAB` → play `RE_RESOLVE_TAB`
   - `bundle.state is None` → `UNRECOGNIZED_STATE` (joins `page_state_candidates`)
   - `human_required` + a sign-in route → `AUTH_WALL` → play `ESCALATE`
   - anything else → `UNKNOWN`, honestly, with `stuck_signal` from the delta. **`UNKNOWN` is never
     a guess dressed as a class** and never graduates (§6).
   Rung 0 must emit a **full readable verdict** on every turn, including the nominal ones —
   legibility never required a model, and that is what makes the commentary pane (S14) free.
3. **Journal columns, not a new file.** Add optional `supervisor_*` fields to `DecisionRecord`
   (`decision.py` already declares appended optional fields backwards-compatible). **Do not build
   an event bus** — see `PLAN_supervisor.md` §0b and the 2026-07-16 corpus reckoning.
4. **Wire into `run_controller`** at the seam after `actuator.act()`. Three rules from §3:
   `unexpected.respond` keeps the final say (the verdict is its *input*, not its replacement); the
   verdict feeds the **next** `decide()` as one compact line in `Bundle.recent[-1]`; and in shadow
   mode it **influences nothing** — it journals and renders.
5. **A replay of the mined incidents as test cases.** Take the 13 `verified=False` rows out of
   `decision_journal.jsonl` and assert rung 0 classifies each into the class the LEARNINGS entry
   says it was. This is the session's real acceptance test: the taxonomy is only worth having if it
   fits the data it was mined from.

## Scope — out

- **No model rung.** Haiku behind `/api/controller/supervise` is S13, and it exists only to handle
  what rung 0 returns `UNKNOWN` for.
- **No screenshot path.** `diagnostic_request` is a field on the contract this session; only rung 1+
  may ever *emit* `"screenshot"` (S13). Rung 0 may not.
- **No UI.** The commentary pane is S14.
- **No postconditions.** `expect_present` / `expect_absent` on the top-3 states is S15.
- **No autonomous recovery.** Nothing the supervisor proposes may fire. Stage 1 is shadow (§6).

## Definition of done

- `supervision.py` frozen, versioned, fully unit-tested; both enums closed and documented with the
  incident that earned each member.
- Rung 0 classifies **all 13** journalled `verified=False` rows, and its `UNKNOWN` share on that
  replay is reported as a number in the session's LEARNINGS entry (it is the S13 falsifier).
- `run_controller` journals a verdict on every acting step; existing loop tests still green.
- The full suite green (controlplane-api, mcp, interaction) and `make controller-evals` green.

## Falsifiers to watch (from §8)

- **Rung-0 coverage below ~60% of turns** → the table is too timid or `StateDelta` too coarse. Fix
  the delta before reaching for a model.
- **`UNKNOWN` share not falling as drives accumulate** → the taxonomy is *wrong*, not incomplete.
  Re-mine the logs; do not add classes by intuition.
- **`no_progress` not falling once `SETTLE_AND_RETRY` / `COMMIT_WIDGET` are live** → classes 1, 3
  and 4 are probably one class wearing three hats.

## Note for whoever takes this

`LiveActuator.observe()` still runs no AX scan, so `Bundle.ax_identities` is empty and
`Bundle.fingerprint` has been `None` on every live row ever journalled (LEARNINGS 2026-07-20). Rung
0 must therefore degrade honestly on a scan-only delta rather than assuming identities exist —
`test_a_same_route_step_advance_is_INVISIBLE_without_ax_or_a_scan_change` pins the exact blind spot.
Wiring the AX scan into `observe()` is small, is the single highest-value fix for the supervisor's
accuracy, and belongs to whichever of S12/S16 reaches it first.
