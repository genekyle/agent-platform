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

- **loose** — same intent AND same field. The headline number, and deliberately unchanged since
  2026-08-06 so every figure ever recorded stays comparable.
- **exact** — same intent AND the same params, compared case/whitespace-insensitively. **No longer
  "a tightening, later": since 2026-08-22 it is the SECOND REQUIRED BAR** (below).

Collected free for rung-0 shadows; the model rung is budget-gated and may be sampled (log the
rate — no silent caps). Computed by `shadow_agreement(rows)`; surfaced in the cockpit's Controller
panel and in `GET /api/controller/summary`.

## The gate (per scenario family)

A scenario moves **from teacher-driven → propose-approve** when ALL hold:

1. **loose agreement ≥ 90%** over **≥ 25 consecutive teacher steps** in that scenario, and
2. **exact agreement ≥ 85%** over **≥ 25 rows that can testify about control choice** (see "why
   two bars" below), and
3. the **replay suite is green** — `make controller-evals` passes, and every golden row for that
   scenario reproduces.

All four numbers are named, operator-tunable constants in `controller/metrics.py` —
`PROMOTION_LOOSE_BAR`, `PROMOTION_EXACT_BAR`, `PROMOTION_MIN_N`, `PROMOTION_MIN_EXACT_N` — not
literals buried in a comparison, so retuning is a one-line reviewable change. `is_promotable()` is
the single function that answers the question; `GET /api/controller/summary` carries both numbers
per scenario plus an `eligible` flag, so **no screen can show gate-passing on `loose` alone**.

### Why two bars (measured 2026-08-22, LEARNINGS "the backtest caveat, corrected")

**`loose` is INTENT-ONLY for every click decision, by construction.** `_matches` compares
`params.get("field")`, and a click's params carry `control`, not `field` — so `_field()` returns
`None` on both sides and a proposal to click `"A"` scores as agreement against a teacher who
clicked `"TOTALLY DIFFERENT"`. Verified directly and pinned in `test_controller_metrics.py`.

That is defensible as far as the definition goes (a click has no field), but it means **a scenario
can pass a loose-only gate while reaching for the wrong button every time** — and
`open_pane`/`enter_apply`, the phases the controller is most confident about, are exactly those
click-shaped turns. The first measurement made the point: `indeed_quick_apply:indeed_job_posting`,
the scenario closest to promotion on loose, had **15 genuinely wrong controls** in the corpus
including `Submit your application` proposed as `review your application` and `Apply Now` proposed
as `save job`.

*Rejected alternatives, recorded so they are not re-litigated:* redefining `loose` to fall back to
`params["control"]` (silently changes the metric and invalidates every number recorded before it),
and documenting the blind spot without gating on it (a documented blind spot in a gate is still a
blind spot the week someone promotes in a hurry).

### Two things the exact bar must NOT be, and how each is handled

Measured on the 294-pair corpus, a naive `exact` scored **0.088** — and almost none of that gap was
about control correctness. A bar built on it would have been **unpassable for reasons nobody could
act on**, which is worse than the blind spot it replaces.

- **It must not be a test of string formatting.** The controller reads normalised AX identities
  (lowercased); the teacher records the label as the page renders it. **72 of 87** value
  differences were case-only (`'Continue'` vs `'continue'`). `exact` therefore folds case and
  whitespace — two renderings of one name are one name. It does *not* fold anything else: a
  different control still fails, pinned.
- **It must not blame the controller for a journaling gap.** **131 of 294** pairs have empty
  teacher params, because the crank seam synthesises the teacher Decision from `_RUNG_INTENT` when
  the acted step carried none — there is no control claim to compare against. Those rows leave the
  `exact` denominator and are reported as `exact_unscoreable` rather than silently dropped (same
  principle as `step_runner.verify`'s "BLIND IS NOT WRONG"). **The remedy is upstream**: the crank
  should journal the control it clicked.

With both handled, `exact` reads **0.601 over 163 scoreable rows** — a real number, still far below
the bar, which is the honest state.

**And `exact` needs its own window, not a borrowed one.** `indeed_quick_apply:indeed_job_posting`
had 67 pairs and **`exact_n` = 1**: without `PROMOTION_MIN_EXACT_N`, one lucky row would read as
`exact = 1.000` and carry a scenario through a gate it has no evidence for. That this bar is
currently unreachable on our best scenario is a true statement about our evidence, and it names its
own fix.

A scenario moves **from propose-approve → rung-0/1 owns it (ladder armed underneath)** after:

4. **N = 3 clean propose-approve runs** — no correction needed — in that scenario.

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

## 2026-08-22 — the second bar lands; what changed and what pointedly did not

**Changed:** the gate is now *correctly computable*. `shadow_agreement` reports `loose` and `exact`
per scenario with their own windows and an `eligible` flag, `is_promotable()` is the one function
that answers the question, the four thresholds are named tunable constants, and
`GET /api/controller/summary` carries all of it so no screen can render gate-passing on `loose`
alone.

**NOT changed, and this is the same caveat the 08-20 audit raised:** *nothing branches on it.* No
code path consults `is_promotable()` to decide anything; `controller/maturity.py`'s `authority()`
is still the only enforced gate, with its own units and evidence. This document still describes a
**scoreboard**, and promotion via it is still a human reading the summary. The 08-20 decision —
fold this into `maturity.grade()` or rewrite this doc as the dashboard spec — remains open. Nothing
here should be quoted as an enforcement claim.

**Fresh numbers (294 pairs, superseding the 08-20 line above):** loose **0.5952**; exact **0.6012**
over 163 scoreable rows (131 unscoreable). Per scenario, the three with real volume:
`indeed_quick_apply:indeed_job_posting` loose 0.657/67 with **exact_n = 1**;
`workday:workday_job_posting` loose 0.590/61, exact 0.766/47; `company_site:?` loose 0.511/45,
exact 0.486/35. **Eligible scenarios: none.**

**Every one of those numbers is pre-wire.** They were journaled before `Bundle.phase` reached the
shadow seam, so they measure a controller that could not see whose turn it was. They are the
baseline the fresh rows will be read against, not a verdict on the rail.
