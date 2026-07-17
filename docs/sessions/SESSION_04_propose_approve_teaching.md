# Session 04 — propose-approve teaching mode (Controller M4)

**Read first, in order:** `CLAUDE.md`, `docs/PLAN_controller_v1.md` (§4 — this is the DAgger
argument; internalize *why* corrections must come from the controller's own states),
`docs/sessions/SESSION_03` LEARNINGS, `apps/controlplane-api/training.py`
(`merge_training_annotation` — the approve/reject labeling culture this extends),
`apps/controlplane-ui/` layout (only if building the cockpit panel).

## Objective

Make the controller *teachable* in the precise sense: it drives, and every decision it isn't
entitled to make alone is proposed to the teacher/operator before acting. Approve → act.
Correct → act on the correction AND journal `{proposed, corrected}` as a **golden row**. This is
the loop that generates the only training data that fixes distribution shift — corrections on
states the controller actually reaches. Live target: **one full Workday application** from the
backlog (the third ATS; its programs compile through this mode).

## Scope — in

1. **`controller/teach.py`** — the propose-approve gate as a loop policy:
   `rung="recipe"` acts freely; `rung="model"` and `rung="teacher"` decisions are **proposed**:
   render intent, params (values redacted on screen where sensitive), rationale, confidence,
   expected_next; wait for `approve | correct | escalate | abort`.
2. **Correction capture.** A correction is a full replacement `Decision` (teacher-authored, same
   contract). Journal one golden row carrying BOTH: `proposed_*` columns + the acted decision +
   the eventual outcome. Golden rows are the highest-value rows in the corpus — make them
   mechanically distinguishable (`golden=True`), and never overwrite the proposed half.
3. **Surface: CLI first.** A terminal prompt in the loop harness is sufficient and ships this
   session. Cockpit panel (controlplane-ui) only if the CLI is done and time remains — the
   contract, not the UI, is the deliverable.
4. **Approved sequences compile.** `compile_from_journal` ingests approved/corrected sequences
   exactly like teacher drives — a clean propose-approve run leaves Workday intent programs behind.
5. **Live drive.** One Workday application end-to-end in propose-approve mode. Expect heavy
   proposing early (no Workday programs exist yet) tapering as states repeat — paste the
   per-rung counts from `journal.summarize()` before/after into the session log.
6. **Tests.** Gate policy unit tests (which rungs propose); golden-row round-trip; a scripted
   correction fixture proving `{proposed, corrected, outcome}` all land in one row.

## Scope — out

Shadow metric (next session), auto-retraining on golden rows (that's the flywheel's job), cockpit
polish, any second Workday drive.

## Definition of done

- One full Workday drive through propose-approve; final Submit taken by the operator.
- ≥1 real correction captured (if you genuinely never disagreed, force one scripted correction so
  the pipeline is proven — and say so in the log).
- Workday intent programs committed (PII-grepped, value-refs only).
- LEARNINGS.md appended: the *categories* of corrections (wrong field? wrong intent? wrong
  expectation?) — this taxonomy seeds the shadow-agreement match rules next session.

## Non-negotiables

- Corrections are teacher-authored decisions through the SAME contract — no side-channel "just fix
  it by hand in the browser"; a hand-fix invisible to the journal is the event-log mistake again.
- Workday accounts: the agent never types passwords or submits account creation — Account Manager
  boundary holds (`WORKDAY_ACCOUNT_LOOP` runs as operator, never the tool-loop).
- `make data-check` first; never auto-solve captcha/2FA; stage explicit paths; commit to `main`.
