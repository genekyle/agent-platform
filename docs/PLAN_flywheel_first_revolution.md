# Plan — the first full flywheel revolution

**Status: plan, 2026-07-16.** The gameplan for what a successful flywheel looks like now that the
Interaction API exists, and for hitting the ground running the moment full drives work.
`FLYWHEEL_RUNBOOK.md` stays as the operational how-to for the label/train cranks; this doc is the
campaign: definition of done, phases, gates, and the numbers on the wall.

**The one-line thesis check:** the flywheel has never completed a single revolution. No model has
ever been trained from live-work data and promoted to make anything cheaper. Every priority in this
plan exists to make that happen once — because until it happens once, we don't actually know which
of the platform's organs are load-bearing.

---

## 0. What "the wheel has turned once" means — Definition of Done

One scenario family (target: **ATS page-states for Workday + Greenhouse applies**) where all six
hold:

1. **Acted through the API** — applications driven end-to-end with zero `/eval` in model-made calls;
   every action journaled with a fingerprint and an outcome.
2. **Labels accrued from the drives themselves** — auto-labels from journal outcomes + per-step
   protocol logs, human labels from the queue; not from bespoke labeling sessions.
3. **L3 v1 trained** on the ATS state taxonomy, evaluated on a **scenario split** (architecture
   invariant #7 — no scenario in both train and eval).
4. **Shadow-mode record** — L3 ran on every live capture during real drives; agreement vs teacher
   truth measured; disagreements harvested as training rows.
5. **Promoted behind a gate** — L3 answers first for the states it owns, with fall-through intact.
6. **A number moved** — % of state classifications answered locally went from 0 to real, and
   cost-per-submitted-application is measured against the Phase-0 baseline.

Anything short of all six is infrastructure, not a revolution.

---

## Phase 0 — pre-flight (now; while Claude still drives everything)

The point of Phase 0 is that **no teacher drive from here on is wasted**. Every item is small; all
are prerequisites for the data to be usable later, so they come before volume.

1. **Live-validate Phase 1** (its unmet DoD): finish KKR using only the new endpoints — zero
   `/eval` in model-made calls. The page-side JS has never met a real page (PRINCIPLES §5); this
   drive is both the validation and the first real journal fill.
2. **Verify the joins end-to-end, once, by hand.** Take one drive; confirm journal rows ↔
   fingerprints ↔ captures (.ax.json sidecars) ↔ per-step logs actually join. A corpus that doesn't
   join is the event-log mistake with better intentions. Write the join query down.
3. **Capture-at-protocol-step.** Each tier-2 endpoint's per-step log (`open → staged → committed`)
   names exactly the intermediate states L3 has never seen (`popup_open`, `option_staged`).
   Wire capture + auto-label at those steps. Two standing cautions from LEARNINGS: pin a `tab_url`
   that matches exactly one page, and a popup dismisses on blur — capture in-page against the live
   open widget, never after an HTTP round-trip.
4. **Auto-label writeback from outcomes.** `outcome=ok` on a journaled intent ⇒ positive label for
   the state transition it implies; `not_opened`/`not_staged`/`not_committed` ⇒ **negative
   examples** — which the operator explicitly asked to see accumulating
   (`PLAN_account_manager_and_l3.md`). Teacher-auto-labeling was proven live 2026-07-09; this
   extends it to protocol steps.
5. **Snapshot the baseline BEFORE spinning.** From recent teacher drives: teacher tokens + $ per
   submitted application, intents per application, escalations per application, current label
   counts per ATS state. Improvements are unmeasurable without this row zero.
6. **The dashboard queries** (Lab/Flywheel panel or plain SQL/jq to start): journal rows by
   outcome and by rung; label coverage per ATS state vs the 10–30-per-state target; shadow
   agreement over time. Cheap now, priceless in week 3.

## Phase 1 — spin (2–4 weeks of *normal work*, not extra work)

**The crank is the existing apply backlog (13 jobs), driven as usual.** The flywheel's input was
never a special activity — that was the whole point of fixing the corpus plumbing.

- Every application: teacher drives through the intent surface → journal + step-captures accrue
  automatically (Phase 0 items 3–4).
- **Weekly cadence, inside the $5/wk cap:** work the label queue (`FLYWHEEL_RUNBOOK.md` — human
  labels for what auto-labeling can't decide) → retrain L3 (`train_stage_observer` → v1 on the ATS
  taxonomy) → eval on the scenario split → read the dashboard.
- **Shadow mode from the first retrain:** L3 predicts on every live capture during drives;
  prediction vs teacher-labeled truth logged. Agreements are validation; **disagreements are the
  most valuable training rows in the system** and the confusable-neighbor states they reveal get
  labeled first.
- Coverage target per state (from PLAN_account_manager): **10–30 varied examples + labeled
  confusable neighbors.** The coverage tracker drives where to capture more, not vibes.

## Phase 2 — promote (the gates)

Promotion is **per-state, per-ATS** — never global. L3 answers first for a state when:

- **(a) Scenario-split eval** ≥ threshold on the states it will own (propose: ≥90% precision per
  owned state; a wrong state classification misroutes a whole protocol, so precision beats recall —
  fall-through covers the recall).
- **(b) Shadow agreement** with teacher truth over the last N live drives (propose N ≥ 3 drives and
  ≥ 50 classifications with zero silent disagreements on owned states).
- **(c) Fall-through intact and tested** — low-confidence or unowned state → next rung (Haiku /
  teacher), never a guess. The cascade's cheapest-first contract, unchanged.
- **(d) Budget unchanged** — the $5/wk cap enforcement is untouched by promotion; a promoted L3
  *reduces* spend or it isn't promoted.

Then watch the wall: % answered locally ↑ while escalations/application don't rise. If a promoted
state re-escalates on unchanged pages, demote it and label the gap — demotion is one flag, which is
the point of the cascade.

## Phase 3 — L4, the intent model (only after L3 is promoted AND the journal has volume)

L4 trains from the journal: `(state fingerprint, goal/field, describe_widget output) → intent from
the closed vocabulary`. Its action space **is** the intent vocabulary — that's what makes it small
enough to learn and impossible to emit an unexecutable verb (`contract.intent_expands_to` pins every
expansion to verbs the driver implements).

- **Shadow first, free labels forever:** on every teacher drive, L4 predicts the next intent; the
  teacher's actual journaled intent is the label. Agreement rate is the graduation meter — and it
  costs nothing, because the teacher was driving anyway.
- **Graduation to R1** (per DECISION_two-stacks-one-spine.md §3): L4 runs a *cached, learned*
  scenario end-to-end with the verifier gate live and the ladder armed — Haiku for bounded
  decisions it flags, teacher on anything novel, human on stop-states. First candidate: a
  repeat-ATS apply (a second Workday tenant on an already-learned flow), because ATS components are
  identical across tenants — that's the generalization the operator wants ("help generalize across
  similar scenarios") on the easiest honest terms.
- **Do not start L4 early.** A policy model trained on 6 journal rows is a party trick. Volume
  gate (propose): ≥ 500 journaled intents with outcomes across ≥ 10 applications and ≥ 2 ATS
  before the first training run. Until then, every drive is already building its corpus — patience
  costs nothing here.

## The numbers on the wall (the only dashboard that matters)

| Metric | What it proves |
|---|---|
| Journal rows per application (↑ then plateau at "everything") | actions are visible to the flywheel |
| % of live actions journaled vs total (→ 100%) | no un-journaled side doors (PRINCIPLES §8) |
| Label coverage per ATS state vs 10–30 target (↑) | the capture faucet feeds the right states |
| % state classifications answered by L3 (0 → real) | perception localizing |
| L4 shadow agreement with teacher (↑) | policy learning what the teacher does |
| Escalations per application by rung (R3/R4 ↓ on known ATS) | the ladder is working |
| Teacher tokens per application (↓ on learned ATS, **flat on novel** — that's correct, not failure) | graduation, with the door open |
| Cost per submitted application (↓) | **the thesis** |
| Grounding accuracy off 0% | the legacy metric — still real, still fed by the same labels |

## Standing rules (so we start running, not limping)

- **No un-journaled work.** A session that acts through anything but the API is a bug to fix that
  session, not a convenience to forgive (invariant #9).
- **Discovery ends in an endpoint + recipe entry + labeled states, every time** — a working script
  and nothing else is a session paid for twice (PRINCIPLES §8).
- **Every teacher drive is a training session whether or not the application succeeds.** Failures
  label the negative space; `not_*` outcomes are rows, not embarrassments.
- **Don't build ahead of the wheel.** Nothing parked (diffusion/movement, OmniParser removal,
  main-split resumption, new domains, Account Manager build-out) resumes until §0's six items hold
  — unless it concretely blocks a drive.
- **When the wheel HAS turned once:** run it again on the second domain family (FB Marketplace over
  the Interaction API — the deliberate second site that earns the abstractions' generality), then
  make the cadence continuous (scheduled retrain + gated auto-promote). That's v2's doorstep, and
  we'll know the organs are load-bearing because they'll have borne load.
