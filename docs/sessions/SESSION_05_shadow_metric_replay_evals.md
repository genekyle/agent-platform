# Session 05 — shadow agreement + replay evals + the promotion gate (Controller M5)

**Read first, in order:** `CLAUDE.md`, `docs/PLAN_controller_v1.md` (§4 shadow/replay, §6 the
gate), `docs/PLAN_flywheel_first_revolution.md` (the promote/measure culture this plugs into),
Session 04's correction taxonomy in `docs/LEARNINGS.md`, `apps/controlplane-api/training.py`
(`_stable_split` — reuse it for any train/eval split of journaled bundles).

## Objective

Close v1 with its measurement layer: shadow mode (the controller silently decides alongside the
teacher; agreement is journaled for free), a replay harness (every journaled Bundle becomes a
permanent regression case), and the written per-scenario promotion gate. At the end of this
session the question "is the controller ready to own Indeed apply?" has a number, a command that
recomputes it, and a gate it must pass — no vibes.

## Scope — in

1. **`controller/shadow.py`** — wraps any teacher/propose-approve drive: on every step, compute
   `decide(bundle)` WITHOUT acting; journal `{shadow_decision, teacher_decision}` side by side
   (fingerprint-joined, `shadow=True`). Zero extra model spend for rung-0 shadows; rung-1 shadows
   are budget-gated and can be sampled (log the sampling rate — no silent caps).
2. **Agreement metric.** `controller/metrics.py::shadow_agreement(rows, *, match="loose") ->
   per-scenario report`. Loose match = same intent + same field (Session 04's correction taxonomy
   decides what else counts); exact match = params equal after value-ref resolution. Report:
   agreement %, N, disagreement breakdown by category, escalation rate by rung, `verified_rate`.
3. **`controller/replay.py`** — re-run journaled bundles through `decide()` offline:
   deterministic rungs (0 + cache) in CI; model-rung replays budget-gated behind an explicit flag.
   Every golden row from Session 04 is a required-pass case. Wire a make target:
   `make controller-evals` (deterministic only) — fast, offline, free, low-data-safe.
4. **The promotion gate, written down** — a short `docs/CONTROLLER_PROMOTION.md`: per scenario
   family, agreement ≥ 90% over ≥ 25 consecutive teacher steps AND replay suite green → scenario
   moves to propose-approve; N=3 clean propose-approve runs → rung 0/1 owns it, ladder armed.
   Include the falsifier verbatim: agreement flat while corrections grow ⇒ the Bundle is missing a
   feature — bundle shape is the first suspect, not the model.
5. **Measure for real.** Run shadow mode over one more backlog drive (any Career Search ATS) and
   compute the first real agreement numbers. **Update `docs/PROJECT_STATUS.md`** with them — the
   status doc states corpus numbers, not corpus intentions; controller numbers now belong there.

## Scope — out

Acting on the gate (promotion happens when the numbers say so, not this session), L4 training,
similarity retrieval, any new domain. Resist the urge to tune `decide()` mid-measurement — measure
first, tune next session, or the number means nothing.

## Definition of done

- `make controller-evals` green and documented in the Makefile.
- First real per-scenario agreement report pasted into the session log AND summarized in
  `docs/PROJECT_STATUS.md` (with row counts and date).
- `docs/CONTROLLER_PROMOTION.md` committed; every golden row passing in replay.
- LEARNINGS.md appended: the top disagreement category and whether it implicates the Bundle, the
  prompt, or the programs — that verdict is the v1.1 backlog's first line.

## Non-negotiables

- Scenario is the unit of split — no scenario in both any train and eval use of journaled bundles
  (architecture invariant #7; reuse `_stable_split`).
- Sampled or capped anything → log what was dropped (no silent caps).
- Stage explicit paths; `git status` before commit; commit to `main`; `make data-check` before any
  live shadow drive.
