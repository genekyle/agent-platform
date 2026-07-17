# PLAN — the north-star cadence: what `decide()` must ultimately own

**Status: planning note, recorded 2026-07-17 (operator-directed).** This captures the *ultimate goal*
of the controller's `decide()` — the full search→apply cadence, run end to end — so every session
aims the same direction. It is the outer-loop companion to `PLAN_controller_v1.md` (which specifies
the inner, per-step `decide()` contract). The cadence itself already lives, half-codified, in
`apps/controlplane-api/search_cadence.py`; this doc declares it as `decide()`'s scope and splits what
we build now (v1) from what we defer (v2).

## The cadence, as the operator stated it

> Check we're logged in / in the correct state → input the search query → then the location → then
> the radius → then apply to everything that pops up, end to end, each page found until the search is
> completed, noting what we found into the database and what we applied to. That is a **full cadence
> that must complete**, and `decide()` must know what to do at every step because the north star is
> outlined.

Concretely, this is the new **`apply_sweep`** cadence mode (`search_cadence.py`): breadth of
`extraction_sweep` (record everything) + depth of `apply_triage` (drive each apply end to end), minus
the per-job handpick. We deliberately do NOT triage here — *"you must do everything in order to
learn."* The whole point is that every step becomes training data.

This teaching run's parameters: **query `reporting analyst`, location `Manchester, NH`, radius
`50 mi`, fresh Indeed session, mode `apply_sweep`.**

## Why "apply to everything" and not our usual handpick

The default day-to-day loop is `targeted_search_and_apply` / `apply_triage`: shortlist → operator
handpicks → apply to picks. That is right for *getting jobs*, wrong for *teaching the reasoner*: a
handpicked subset never exercises the states we skip, and the states we skip are exactly where
`decide()` will later be wrong. Applying to everything walks `decide()` through the full state space
under supervision, and each wrong call is corrected at the point of disagreement — the golden
`{proposed, corrected}` rows (DAgger) that pure observation can never produce. The batch approval for
the sweep is the operator's explicit opt-in; it stands in for the per-prospect approval the default
modes require.

## The scope shift this forces on the controller

Controller v1's `decide()` / `Bundle` are **apply-inner-loop-scoped** today: `describe_for_ats`
classifies apply states, `scan_required` reads the current form, and `decide()` picks the next
field-fill or Continue. The north star extends `decide()` to own the **whole outer cadence**:

| Cadence phase | State ids | Action surface (exists) | `decide()` today |
|---|---|---|---|
| state check | logged-in? challenge? | `/auth_state`, `/challenge_visibility` | ❌ not modeled in the bundle |
| search | `indeed_home` → `indeed_search_results` | `/set_distance`, `/extract_jobs`, `/next_page` | ❌ bundle is apply-only |
| handoff | `indeed_job_posting` → apply | `/open_job_card`, click Apply | ❌ |
| **apply** | per-ATS apply states | `/autofill_form`, `/select_option`, `/set_date`, `/check_group` | ✅ **this is v1** |
| outcome branches | submitted / captcha / ai_recruiter / account_creation / … | `classify_apply_outcome` | ⚠️ classified, not yet `decide()`-driven |
| tab management | which tab am I on; close finished; refocus search | `/close_tab` | ❌ **new reasoning concern (below)** |
| record | observed_jobs, application_status | `ObservedJob` model, `/api/search/targets/outcome` | ⚠️ written by the sweep endpoint, not by `decide()` |

The pieces exist. What's missing is (a) the `Bundle` recognizing the **search-phase and
tab-management states**, and (b) the **`LiveActuator`** that lets `decide()` actually drive and
journal them (the one seam owed from `PLAN_controller_v1.md` M2).

## Tab management is a first-class reasoning concern (new)

The `BOUNDS.tab_hygiene` rule ("close the one finished apply tab, refocus the search tab — that single
close is not churn") is a *bound*; the north star makes it a thing `decide()` **reasons about**:
which tab is the search tab, which is the apply tab, is this apply finished (submitted or abandoned at
a wall), do I close-and-refocus now. This runs straight into the known **multi-window identity gap**
(`project_terminal_states_and_multiwindow`: captures carry no window identity). So: the `Bundle` needs
a tab-context surface (active tab, the search tab, any open apply tab), and the intent vocabulary
needs the close/refocus action journaled like any other. Until then, tab moves are operator-driven and
the sweep endpoint owns the epilogue.

## v1 vs v2 — the split the operator named

- **v1 (now): execute the KNOWN cadence.** The north star is *outlined*, not *reasoned per step*. So
  `decide()` follows the cadence like a program at the outer altitude — the same "compile the expensive
  path into a replayable program" trick as the intent programs, one altitude up. Haiku / the teacher
  make the granular calls; corrections train it. This is the fastest route to a **v2 skeleton reasoner**
  that *does the whole thing end to end* on this one fixed goal, and it generates the corpus. This is
  what we teach from starting now.
- **v2 (later): REASON the cadence.** `decide()` chooses queries, decides whether a given job is worth
  applying to, sequences cross-domain errands (login, account creation handoffs) on a call-stack — the
  high-level planner (`project_planner_and_cross_domain`: planning = memory + graph-search over the
  state-transition graph, not per-step reasoning). Deferred on purpose (`PLAN_controller_v1.md` §8: no
  lookahead in v1) and justified only once single-step + escalation is shown to stall. The
  state-transition graph substrate it will search already exists.

The operator's own framing: *"reasoning at that high level will take a lot longer than just getting to
a v2 skeleton reasoner, but for now know that `decide()` needs to just do everything since we know the
ultimate goal."*

## The hard gates do NOT relax under "apply to everything"

`apply_sweep` authorizes **entering** every apply; it never touches the invariants:
- **Final Submit stays a per-application operator confirm** (consequential gate — irreversible action
  on a real application). The operator is present; it is a quick yes.
- **Account walls** (Workday/AppVault sign-in or create) → operator. The agent never types a password
  or creates an account (`describe_for_ats` maps these to `human_required`).
- **Captcha / 2FA / AI-recruiter / survey branches** → escalate, never auto-solve
  (`classify_apply_outcome` `human_required=True`).
- **Navigation is human-like**: click cards and pagination, never URL-jump to job details; one apply
  tab at a time, closed and refocused before the next.

## What running this needs (the owed build)

1. **The `LiveActuator`** — `observe()` (a live `/scan_required` + tab context → `build_bundle`) and
   `act(Decision)` (dispatch the intent to the tier-2 semantic endpoints by `{ats, field, value,
   tab_url}`, report outcome + landed state). The single missing seam; reuses `runtime/live.py`
   patterns. Without it `run_controller` cannot touch the browser.
2. **Search-phase + tab-management states in the `Bundle`** — so `decide()` sees "I'm on the search
   results page, distance is 25mi, I need to set it to 50" as a decidable state, not just apply forms.
3. **Wire the recording** — `decide()`'s journal is the training corpus; the sweep separately writes
   `observed_jobs` / `application_status`. Keep both; the decision journal is the point.

Until 1–2 land, the sweep runs through the existing `/api/search/sweep` + the apply recipes with the
teacher (Claude) driving and journaling by hand — which is itself the first `apply_sweep` teaching run
and the data that justifies building the seam.

---

*One line: `decide()`'s north star is the whole `apply_sweep` cadence — state-check → search →
distance → per-page apply-everything end to end → record → paginate → done — run as a known program in
v1 (execute + correct + journal) and reasoned in v2 (the planner). The gates never relax; the
`LiveActuator` is the seam that lets `decide()` drive it.*
