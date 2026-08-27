# PLAN — Generalization v1: the reliable core

**Status: adopted 2026-08-26 (operator-directed). This supersedes `PLAN_controller_v1.md` as the
build priority.** The controller stays built and live beneath it — what changes is what we
optimize. **The full flywheel is demoted from goal to instrument**: train-on-label stays on, the
shadow keeps riding every crank, the two-bar promotion gate stays enforced — but no session is
measured by gate progress, and no work queues behind "graduate a scenario family."
`PLAN_reliability_v1.md` recorded this exact recommendation on 2026-08-14 (*"freeze the student
stack, keep collecting rows"* — filed then as on the table, not adopted). It is adopted now.

**The operator's direction, verbatim in intent:** an extremely reliable **planner, observer,
validator, and interaction-profile selection**; the **execution layer stays bucketed and
ongoing**; and the system heads toward **generalization** — knowing it is on a login page, knowing
which ATS it is dealing with, pulling the recipe and generalizing with it (we know the general
flow of a Workday application; steps may reorder, but we know those steps will come and we know
how to deal with each).

---

## 0. Why, measured — the ten-session retrospective (2026-08-18 → 2026-08-26)

**What the window produced.** 8 applications submitted across 6 ATS platforms, with first-ever
end-to-end drives on Paylocity (6-step wizard), iCIMS, and TAM/`applicantmanager` — **TAM with no
recipe, no registry entry, and no prior sighting, posting→submitted in one pass.** Cornerstone
driven to mid-form, PeopleAdmin farmed to its wall, the Gmail errand and inbox sweep fired live,
$0 metered API spend since 08-03. The generic ATS cadence + the AX layer + the answer store
**already generalize** — TAM is the existence proof.

**What it cost, counted from the log itself** (each class is cited in LEARNINGS with dates):

1. **"The fact existed and nothing asked" — counted to EIGHT explicit instances.** The registry
   note predicting Paylocity's upload modal, unread at the moment it mattered (08-19); Cornerstone's
   double Apply Now, on file since 08-11, rediscovered by screenshot on 08-24; credentials in the
   vault while the flow opened a second account row (08-24); `tab_claims` unconsulted while
   classify read the wrong tab (08-24); `_RUNG_INTENT` in the code the whole time the shadow
   missed 106 pairs for lack of it (08-22). The producers exist; the deciding seam never asks.
2. **Silent false success.** JS `.click()` is untrusted — 11 failed attempts on one Workday date
   before one trusted pointer gesture (08-25); radio misfires 3/10 all reporting ok (08-23);
   uploads staging `files=0` reporting ok (08-24); a consent checkbox "filled" as text (08-21).
   `ok` still means *dispatched*, not *committed* — the oldest class in the log (25+ instances
   before August) and still the most expensive.
3. **The observer describes a page it cannot see.** The census enumerated form fields while the
   page's dominant feature was an upload modal + a consent dialog (08-19); invented 35
   requirements and missed the 4 that blocked (08-19); "all answered" on a step whose only content
   was a required textarea (08-19); empty-AX-name widget families sighted six times. **Silence
   reads as absence.** The observation-profiles gap, named by the operator on 08-19, is still
   unbuilt.
4. **Four resolvers, four notions of control identity** (the 08-25 table): prompt field-names,
   census label derivation, proximity selectors, `.value` writes — each failed on a control the
   AX layer resolved correctly *every time it was used directly*. Two names for one control
   deadlocked Paylocity; one name for three controls misclicked PeopleAdmin.
5. **State names: borrowed and unnamed.** Witnesses with no vocabulary for a platform borrow
   another ATS's names (5 measured borrowings, incl. a false `workday_already_applied` — the
   direction that silently skips jobs). Unnamed states are discovered only by collision (the
   preferences landing, 08-26).
6. **World-facts rot.** A `blocked_on` stale for twelve days planned a session; the virtualisation
   claim was falsified with no line of code changing; **tests were asserting the stale prose**
   (08-26 — PRINCIPLES §14 exists because of this).
7. **Expensive diagnosis downstream of an unasked cheap question.** Two wrong scroll-direction
   diagnoses (~2 hours) before one screenshot showed a filter pill (08-26); eleven typing
   mechanisms before checking whether the click was trusted (08-25). The recognizers now exist —
   captcha-first, untrusted-click, staged-not-committed, result-set-identity, refresh-first —
   **as prose rules, not as a dispatch.**

**And the numbers that changed the goal.** Flywheel revolutions by its own six-item DoD: **0**.
Trained checkpoints doing real work: **0**. Promotion-eligible scenarios: **none**. Meanwhile the
shadow's largest disagreement axis (106 of 119) was a **missing Bundle feature** (phase), not a
model gap — the reliability fix WAS the flywheel fix, cheaper. The window's conclusion is not
"train harder"; it is that the loop's ceiling is set by **orientation, validation, and
addressing** — and that generalization is already happening wherever those three hold.

## 1. The stance

- **The flywheel keeps turning as a byproduct.** Every drive still banks transition rows,
  teacher labels still refit witnesses and programs on write, the shadow still journals, the gate
  stays enforced at `authority()`. All of that is free. What stops: building FOR the gate,
  measuring sessions by rows banked, and deferring reliability work to protect a training
  schedule.
- **The new session measure.** A session is measured by **drives that finish clean**: applications
  submitted or walls met honestly, AND the absence-counts — **zero silent false successes, zero
  facts rediscovered that a table already held, zero screens driven without a name.** Those three
  zeros are the reliability bar, and each maps to a pillar below.
- **Claude remains the permanent novel reasoner** (PRINCIPLES §9) and the teacher-first economics
  stand (2026-08-09). Nothing here re-prices escalation.

## 2. The five pillars

Each pillar: what exists (measured), the gap, and the enforcement point that closes it.

### P1 — ORIENT (the observer): where am I, what kind of page, what should I look at

- **Exists:** two witnesses + facets; `apply_landing` page-KINDs; `classify_ats` off the landed
  page; state fingerprints; `ats_brief` in the session view; `Bundle.phase`.
- **Gap:** page-kind is not yet the PRIMARY axis (platform-keyed state names dominate, which is
  why witnesses borrow names on new platforms); no observation profile (what to look at, in what
  order, per kind); unnamed states surface only by collision.
- **Enforcement:** the observer's report must state **what it looked at and what it could not
  see** — "we did not look" must never render as "there is nothing" (the tri-state rule, now for
  perception). SESSION_15 (rank the unknowns) and SESSION_18 (observation profiles) build this.

### P2 — VALIDATE (the verifier): did that actually happen

- **Exists:** the submission verifier (evidence, not provenance; additive hints); StepRunner
  claims; the press-Next-and-read-the-errors strategy (the site's validator is the authority; the
  census is a hypothesis); the recognizers, in prose.
- **Gap:** act-layer `ok` means dispatched; `check_group` doesn't read back checked state; a
  census confirms answeredness, never correctness; `verify()` has no `expected_next` branch;
  `mismatch` means two different things; the recognizer chain is not code.
- **Enforcement:** **an act returns its evidence or its refusal** — the commit read-back travels
  inside the intent result (SESSION_20), and "ok with no observable change" auto-runs the
  recognizer chain before anything retries or grinds.

### P3 — ADDRESS (interaction-profile selection): which engine is this widget, how does it commit

- **Exists:** `describe_widget` tells in one order; `__kindOf` shared by census and classifier;
  the widget protocol layer (stage → commit); self-naming engines (`data-uxi-widget-type`);
  `/probe` for React props; the humanized driver.
- **Gap:** four addressing paths with four identities; JS-click untrusted by default; selector
  paths that never ran (`/select_prompt` 3-tuple); `/locate` matching visible text, not
  accessible names.
- **Enforcement:** **one resolver** — AX role + accessible name → `backend_node_id` is the only
  door; every other path is an explicit, listed exemption with a reason (SESSION_19). Trusted
  pointer gestures become the default for commit-bearing clicks.

### P4 — PLAN (the planner): what comes next, what will this demand, when to ask

- **Exists:** the checkpoint ladder (rungs that recover, never repeat), the search cadence and
  its traversal shapes, the blackboard + SearchState, `apply_requirements.blockers()`, the
  accounts vault, ask-now-beats-park (proven 08-23/24: five standing facts banked in chat).
  **Reasoner v2 (S06–S09, the taught Plan contract) is entirely unstarted** (LEARNINGS:1924) —
  the live planner IS the ladder, and that is fine.
- **Gap:** the planner does not consult what the system knows before spending the drive —
  `blockers()` wired to no decision point, the brief a side panel, the vault checked after the
  wall renders. And recipes are still step-SEQUENCES in prose where the world serves step-SETS.
- **Enforcement:** consultation becomes an input, not an archive (SESSION_17): the classify and
  entering rungs receive a composed orientation context, and their journaled rationale must cite
  what was consulted. Reasoner v2 stays parked until the ladder is reliable on these terms.

### P5 — EXECUTE (the bucketed, ongoing layer)

- **Exists:** the generic ATS cadence (fuzzy spine by `apply_landing` kinds — proven
  land-to-submitted on Cornerstone 08-11 and TAM 08-24); per-platform quirks in `ats_registry`
  notes and dialect rows; the interaction API.
- **The stance:** platform work is **permanently ongoing and always bucketed** — a new ATS enters
  through the generic spine, and earns a recipe only where the spine measurably stalls. Site
  truth lands as DATA (registry rows, dialect entries, world-facts with the §14/§16 shape), never
  as new endpoints. A second site is a table row (the readers-not-cadence rule).

## 3. Generalization, concretely

The operator's three examples, mapped to mechanisms — each is an extension of something proven,
not an invention:

1. **"Understand it's a login page."** Page-KIND is the primary classification axis; platform is
   the modifier. The kinds are a closed, small vocabulary (posting, apply-entry, auth wall,
   account-create, verification wall, form step, review, confirmation, interstitial/upsell,
   error, consent dialog) — most already exist in `apply_landing` + the wall classifiers. A
   **known kind on an unknown platform must drive** (TAM precedent); an unknown kind is the real
   escalation. This also ends name-borrowing: a witness with no platform vocabulary falls back to
   the KIND, never to another platform's state.
2. **"Understand which ATS it's dealing with."** `classify_ats` off the LANDED page (post-redirect,
   the PeopleAdmin lesson), then the registry + brief + requirements axis consulted **at classify
   time** — "expect an account wall", "cover letter required", "two Apply buttons, drive the
   visible one" arrive before the approach, not after the stall (SESSION_17).
3. **"Know the general Workday flow; steps may reorder."** A recipe family becomes a **SET of
   expected step-kinds with per-kind handlers** — the spine walks whatever order the site
   presents, the site's own stepper ("Step 2 of 6") is read as data, and `expect` is honestly
   multimodal (the 08-25 draft-resume lesson). The generic cadence already walks this way;
   SESSION_21 formalizes per-family step vocabularies as data with world-fact freshness.

## 4. Build order

`SESSION_14 → 15 → 16` are this plan's data-honesty foundation (one fact one place; count the
unknowns; facts with expiry). **S14 was executed 2026-08-26, the same evening this plan was
written** (LEARNINGS has the entry; its live sweep also confirmed the untrusted-click recognizer
on a new widget and the `@journaled` contract catching five broken endpoints) — **the queue's
head is S15**, which also inherits the location-column lie S14's drive named. Then:

| # | Brief | Kills (from §0) | Pillar |
|---|---|---|---|
| 17 | `SESSION_17_ask_what_we_know.md` — consultation becomes an input | class 1 (counted to eight) | P4/P1 |
| 18 | `SESSION_18_observation_profiles.md` — what to look at here | class 3 (census blindness) | P1 |
| 19 | `SESSION_19_one_resolver.md` — one identity per control | class 4 (four resolvers) | P3 |
| 20 | `SESSION_20_act_carries_evidence.md` — ok means committed | class 2 (silent success) + the recognizer dispatch (class 7) | P2 |

| 21 | `SESSION_21_session_restore.md` — restore the session exactly | the stale-session incident, made an experiment | P2/P4 |

**S21 was inserted 2026-08-27 (operator-directed) ahead of the two below**, on a finding: the
signed-in Indeed and LinkedIn profiles live in `/tmp`, which a reboot clears, with no copy
anywhere. It is a learning feature rather than an ops one — it gives `PLAN_staleness.md`'s RENEW
verdict its first remedy, lights the permanently-inert `cookie_ttl_s` signal off the same CDP
read, and turns "the session went stale" from an incident into a repeatable bench experiment.

Defined here, briefed when reached (do not build ahead of the wheel): **S22** step-set recipe
families (P5/§3.3); **S23** planner pre-flight — `blockers()` + document requirements into the
pick/plan step so a drive knows *can this finish* before spending the approach (P4). Every
session keeps the standing rule: **end on a live drive that exercises the change**, because a
green suite has repeatedly certified wires that were not there.

## 5. What NOT to do

- **No trained model on the critical path, and no gate-chasing.** The gate stays armed; nobody
  feeds it deliberately. If a family clears it in passing, promote per CONTROLLER_PROMOTION.md.
- **No new domains.** Career Search until a family graduates (unchanged since 07-17).
- **No frameworks.** Every 08-26 win was one function long. The orientation context is a composed
  read, not a knowledge base; profiles are dicts, not a schema language.
- **No wholesale recipe rewrites.** S16 pilots the world-fact shape on ONE recipe; S21 extends it
  only if the pilot earned its keep.
- **No bespoke DOM layers.** §6 stands; S19 *finishes* it (one door), not replaces it.

## 6. Falsifiers (stated before the work, §13)

- **If after S17 a live drive still hits a "fact existed, nothing asked" instance**, the
  wire-each-seam approach has failed — the next move is ONE orientation payload every rung MUST
  receive (a choke point like `record_for`), not more per-seam wiring.
- **If the generic spine stalls short of Submit on 2 of the next 5 new platforms**, the bucketed
  execution layer needs promotion (per-family recipes ahead of spine work) — the "readers not
  cadence" bet would be measured wrong.
- **If silent false success recurs after S20 on a widget family that has an evidence contract**,
  the contract's read point for that family is wrong — fix the read point; do not add a retry.
- **If kind-first classification mislabels a wall as a form step on a live drive**, the kind
  vocabulary is missing a member — add the kind deliberately (S15's discipline), never widen an
  existing one.

---

*One-line summary: stop optimizing the flywheel and start optimizing the three zeros — no silent
false success (validate: evidence inside the act), no rediscovered fact (plan/orient: consultation
as input), no unnamed screen (observe: kind-first, profiles, ranked unknowns) — on one addressing
door, with execution staying a bucketed, data-shaped, permanently ongoing layer. Generalization
falls out of the kinds, the sets, and the consultation — as TAM already proved.*
