# Controller promotion gate — when a scenario graduates off the expensive rungs

_Written for PLAN_controller_v1 M5 (SESSION_05). The controller earns autonomy **per scenario
family**, never as a global switch — "Claude-free" is a graduation, and the door back to the
teacher stays open by design (see PROJECT_STATUS "Endgame")._

## The unit is a scenario, never the whole controller

A scenario family = a `(task, state)` region the controller works — e.g. Indeed
`indeed_apply_questions`, Workday `workday_my_information`. Each graduates on **its own numbers**.
Averaging across scenarios hides the one that is still wrong, so the gate reads `by_scenario` from
`controller.metrics.shadow_agreement`, not the overall figure.

## The metric

**Shadow agreement** — on a teacher (or propose-approve) drive, the controller also decides
silently on every step; agreement = the fraction where the controller's proposal matches what the
teacher actually did. Two match strengths:

- **loose** — same intent AND same field. The primary gate metric.
- **exact** — same intent AND identical params after value-ref resolution. A tightening, later.

Collected free for rung-0 shadows; the model rung is budget-gated and may be sampled (log the
rate — no silent caps). Computed by `shadow_agreement(rows)`; surfaced in the cockpit's Controller
panel and in `GET /api/controller/summary`.

## The gate (per scenario family)

A scenario moves **from teacher-driven → propose-approve** when BOTH hold:

1. **agreement ≥ 90%** over **≥ 25 consecutive teacher steps** in that scenario (loose match), and
2. the **replay suite is green** — `make controller-evals` passes, and every golden row for that
   scenario reproduces.

A scenario moves **from propose-approve → rung-0/1 owns it (ladder armed underneath)** after:

3. **N = 3 clean propose-approve runs** — no correction needed — in that scenario.

At every stage the ladder stays armed: a verify-fail, a stale program, a BLOCKED outcome, or a
`human_required`/consequential state escalates exactly as before. Graduation removes the *default*
call to the teacher, not the *safety net*.

## The falsifier (carried verbatim from PLAN_controller_v1 §6)

> If agreement stays flat while corrections accumulate, the **Bundle is missing a feature the
> teacher is using** — the bundle shape is the first suspect, not the model.

Concretely: when `by_category` in the agreement report is dominated by `wrong_field` or
`wrong_intent` on a scenario whose agreement will not climb, do **not** tune the prompt first. Ask
what the teacher can see that `bundle_to_prompt` does not encode, and add that feature to the frozen
`Bundle` (bumping `DECISION_SCHEMA_VERSION`). The corrections are the spec for the missing feature.

## What resets the clock

- A `DECISION_SCHEMA_VERSION` bump (the feature set changed — old agreement is on a different input).
- A program marked stale for the scenario (the site changed under it).
- Any new disagreement category appearing in `by_category` that was not there before.

## Where the numbers live

- `make controller-evals` — the deterministic regression suite (offline, free, low-data-safe).
- `GET /api/controller/summary` → `agreement.by_scenario` — the live per-scenario figures.
- The Controller cockpit panel (Lab → 🧠 Controller) — the operator-facing scoreboard.
- `docs/PROJECT_STATUS.md` — the status doc states corpus **numbers**, not intentions; the first
  real per-scenario agreement figures belong there once a shadow drive has produced them.

## 2026-08-20 — the two gates do not agree, and only one is enforced (operator decision pending)

The audit measured what this document promises against what the code does:

* **This document's gate (shadow agreement ≥ 90% over ≥ 25 steps) is computed and displayed,
  enforced nowhere.** No code branches on it — promotion via this gate is a human reading
  `GET /api/controller/summary`.
* **The enforced gate is `controller/maturity.py`** (`authority()` grades every progressive
  turn): CERTIFIED needs 5 verified OKs + a 3-row supervisor-clean tail + 1 approved-uncorrected
  YELLOW review, and `GREEN_AT = CERTIFIED`. Different units, different evidence, different
  thresholds.
* **The two scenarios that clear this document's N ≥ 25 bar are both `*_job_posting` states**,
  which `TARGET_PARAMETERISED_STATES` hard-caps below autonomy regardless of agreement — under
  the code's gate they are permanently ineligible, correctly (the action depends on which job is
  being pursued).

Current measured agreement (loose, 239 paired rows): indeed_job_posting 0.67/60,
workday_job_posting 0.68/40, indeed_apply_questions 0.80/15. None passes this document's bar
either.

**The decision to make:** either this document's agreement gate becomes an enforced input to
`maturity.grade()` (e.g. a floor below TESTING), or this document is rewritten to describe the
maturity ladder as the single promotion mechanism and shadow agreement as its dashboard metric.
Until then, the ladder is the truth the loop acts on; nothing here should be quoted as an
enforcement claim.
