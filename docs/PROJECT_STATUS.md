# Project Status — Supervised Browser Agent

_Last updated: 2026-08-26 — **the ten-session retrospective and the generalization refocus
(operator-directed): the build priority is now `PLAN_generalization_v1.md`** — the reliable core
(planner · observer · validator · interaction-profile selection · bucketed execution) and
generalization by page-KIND, consulted knowledge, and step-SET recipes. **The full flywheel is
demoted from goal to instrument** (train-on-label, the shadow, and the enforced two-bar gate all
keep running as byproducts; nobody builds FOR the gate — this adopts PLAN_reliability_v1's
2026-08-14 standing recommendation). A session is measured by **drives that finish clean**:
submissions plus three zeros — no silent false success, no rediscovered fact, no unnamed screen.
Briefs queue 14 → 20; S06–S09 (Reasoner v2) are PARKED._

_Previous (2026-08-23): the reflection audit and the four-lane tandem (see that section
below); priorities re-ordered: **drive, live** — the code is ahead of the world's evidence.
Previous framing (2026-08-09, **the refocus**, operator-directed after the burnout audit; LEARNINGS
2026-08-09) stands beneath it: The full flywheel revolution is re-sequenced, not abandoned: **the controller is the
middle step and the priority, and legibility is its deliverable.** The teacher rung is re-priced:
the teacher is literally the Claude session the operator already pays for — attended drives
escalate recipe → session-Claude → human, and the Haiku API rung is demoted to unattended runs
(`settings.haiku_attended_allowed`, default off). A revolution on these terms: drive → controller
decides or escalates to the teacher → everything journaled **with its screenshot** → teacher
corrections ARE the labels → training runs on label-write, per transition, as volume arrives.
The unit of success changed with it: **a session is measured by rows banked, not code written.**
The previous status (2026-07-22, perception-v1/supervisor priorities) is in git history; its
priority list no longer stands._

## What we're building (one paragraph)

A **supervised browser agent** that runs a per-step loop — classify → propose → select → act →
verify — where each decision is made by the **cheapest tool that's confident**, a human catches
anything that reaches the top, and every action is **journaled** as training data. The model emits
**intents from a closed vocabulary** through the Interaction API (the model says WHAT, the recipe
says WHERE, the API says HOW, the journal says WHAT HAPPENED). Claude is the **teacher and the
permanent novel reasoner** (PRINCIPLES §9): it drives novel ground and services escalations, and
its journaled work — decisions, corrections, and now the screenshots they were made from — trains
the local witnesses that take routine perception off the expensive models. Hard constraints:
resource-efficient (solo founder; **$5/week autonomous spend cap** enforced), and **attended
escalation spends the already-paid session before any metered API call** (2026-08-09).

## The corpus reality (measured 2026-08-09 — the audit that forced the refocus)

The honest numbers, so nobody re-derives them:

| Fact | Number |
|---|---|
| Commits since April / trained model checkpoints ever | 451 / **0** (JSON count-tables + a July NB fit only) |
| `training_captures` (Postgres, the legacy labeled corpus) | 271 rows, **0 new since 07-30**, 5 approved bboxes |
| Transition corpus (`apps/mcp/output/transitions/`) | 59 rows over 3 days, **117/117 screenshots present**, 10 teacher labels — **the one healthy organ** |
| Decision journal / intent journal / orientation corpus | 117 / 754 / 40 rows — **zero image refs between them** (fixed 2026-08-09) |
| Teacher inbox (`teacher_inbox.jsonl`) | last wrote **07-23** — the seat was built and sat empty |
| First real shadow-agreement numbers | 2026-08-06: shadow 47% over 15 pairs, orienter 25% (gate: ≥90% over ≥25) |
| Shadow agreement, measured 2026-08-22 | **0.5952 over 294 pairs**; 119 disagreements, **all `wrong_intent`**, **106 of them click↔observe** (70 shadow-clicked/teacher-observed, 36 reverse). Best with real n: `indeed:indeed_apply_questions` 0.80/15, `indeed_quick_apply:indeed_job_posting` 0.657/67, `workday:workday_job_posting` 0.590/61. **No scenario passes the gate.** Root cause is a missing Bundle feature, not a threshold — see LEARNINGS 2026-08-22 (later) |
| Teacher labels on the transition corpus | **84** (17 before 2026-08-22, +67 that day); label queue **373 → 306**, the `mismatch` head fully worked |
| Flywheel revolutions (`PLAN_flywheel_first_revolution.md` DoD, all six) | **0** |

The pattern the log itself counted six times: built, documented, never wired. The refocus's answer
is structural — the work IS the drive, and everything below exists to make one drive pay four ways
(a real application + journal rows + teacher labels + refit witnesses).

## What landed 2026-08-09 (the refocus build)

1. **The teacher's eyes reach the corpus.** `Bundle.capture` (prompt- and digest-invisible) carries
   the turn's artifact + screenshot basenames from `perception_live.capture_now` through
   `build_bundle`; `record_for` flattens them to `DecisionRecord.capture_artifact/-_screenshot` at
   the one choke point, so **no seam can journal a decision without what it saw**. Controller
   transition rows now carry `before.artifact`/`before.screenshot` like StepRunner rows.
   `collect=False` credential flows journal `capture=None` by construction (PRINCIPLES §4).
2. **Attended mode — teacher-first escalation.** `RunBody.attended` (default true): the Haiku rung
   is not wired at the model-wiring line (the only enforcement point that runs before `decide()`
   spends), and YELLOW propose-approve turns go to the **teacher inbox** instead of an 0.85
   auto-approve; timeout still escalates, never approves. Parks announce when they OPEN
   (`ask → on_park → wait`), and the cockpit shows them. Known, documented side doors that remain
   explicit per-call opt-ins: `POST /api/controller/decide_model`, `observe?allow_model=true`,
   `decide_cascade` with a posted budget — all still under the weekly cap.
3. **Transitions are the training spine.** `perception.dataset.transition_label_rows()` turns each
   teacher-labeled transition into TWO witness training rows (before + after halves);
   `load_rows(include_transitions=True)` folds them beside the DB corpus, censused separately.
   **Train-on-label**: a state label lands → `BackgroundTasks` refits the transition table AND the
   perception witnesses (`settings.train_on_label`, default on) — the crank is the label write,
   not a button someone remembers. *Explicitly deferred, not dropped:* the element-level faucet
   (approved bboxes / `positive_candidate_id` → grounding + select-cache) has no transition-row
   equivalent; it stays on the dormant `training_captures` path until element labels matter again.
4. **The cockpit Trace is the visualizer.** Per step: before/after screenshots, the witnesses'
   own words, declared expectation, act, delta, verdict, teacher label if one exists, and a
   correction form that posts `/api/transitions/{key}/correct` — which now also queues the refit.
   Controller decisions render with their journaled screenshot; open teacher parks banner at the
   top. Screenshot serving is traversal-hardened and basename-only.

Tests: controlplane-api 1476 → **1485**, interaction 241 → **244**, mcp 84, controller-evals 7 —
all green from the worktree with import provenance verified.

## What landed 2026-08-20 (the audit, Tier 0, and the self-teaching plumbing)

The whole-system audit (`ANALYSIS_system_gaps.md`) and its first two build tiers. Six verified
bugs fixed with pinned tests (dead upload gate; stale-URL/wrong-key/self-destroying writes around
the day-old `ats_flows`; the cockpit's unreadable 422s and the inert Submitted press). And the
self-teaching consumers attached: confirmed transitions now label themselves beneath the teacher
(343 witness rows / 82 states, 115 self-supervised, from zero new drives), the edge table refuses
mismatched acts, stale programs are pardoned only by new journal evidence — automatically, at
label-write and drive-end — and `GET /api/transitions/label_queue` hands the session-Claude
teacher only the rows self-supervision cannot claim, mismatches first. The measure stands: rows
banked, labels written, parks answered — but each now feeds three organs instead of zero.

## What landed 2026-08-22/23 (the reflection audit and the four-lane tandem)

A step-back audit (LEARNINGS 2026-08-22, "the reflection audit") measured the flywheel's three
bottlenecks — shadow agreement 59.5%/294 with 106/119 disagreements on one click↔observe axis,
a 373-row label queue with no screen, and a ledger blind after submit (event kinds: `applied`
only; 63/68 flows unterminated) — and found the Gmail errand fully built with **zero callers**.
Four coordinated worktree sessions (lanes + merge order in `PLAN_verify_email_leg.md`, seam
rulings included) then closed all three at once:

1. **The `verify_email` leg** — the errand's first caller. The account seam measures the wall
   (code | link | second_factor | unknown), drives the CODE mechanism end to end (fetch → stage
   → submit → re-classify), escalates the rest honestly, and writes measured
   `verification_mechanism`/`verification_sender` characteristics. `gmail_senders.py` is the one
   sender table read both directions. Workday's verify-screen addressing is **HYPOTHESIS until a
   live scan** — the first drive through a real wall corrects it.
2. **The phase wire** — `Bundle.phase` + a phase-conditioned rail: the click↔observe axis was
   the Bundle not knowing whose turn it was. Verified live: one Workday page, two phases, two
   correct verbs. All historical rows carry `phase=None`; **the gate clock starts with the next
   drive.** Teacher labels 17 → **84** (the queue's mismatch head cleared; 306 remain, none
   mismatch); train-on-label recompiled 21 programs.
3. **The outcome loop** — inbox rows → `ApplicationEvent` (sender→ATS via the shared table,
   strong/weak phrase tiers, ≥0.75 company-token match; ambiguity always reviews, interviews
   never auto-write; personal mail keeps fingerprint only). Drive-end sweep rides `close_out`;
   the Inbox review tab lives in the career_search Database section.
4. **The flywheel's screen** — Learning → Queue (the real `transitions/label_queue`, correction
   form with states-as-placeholder, never prefilled values) and Learning → Scorecard (rows
   banked / labels written / parks answered, apps/week, outcomes vs the frozen 2026-08-22
   baseline, and the promotion gate).

**The promotion gate grew a second bar** (CONTROLLER_PROMOTION.md 2026-08-23): loose match is
intent-only for clicks by construction, so promotion now requires loose ≥0.90 AND exact
(control-identity, case/whitespace-folded) ≥0.85, **each over its own ≥25 window**
(`PROMOTION_MIN_EXACT_N`); teacher rows with no recorded control are `exact_unscoreable`, never
silently scored. Current (all pre-wire): loose 0.5952/294, exact 0.6012/163, eligible **none**.

**The two-bar gate is ENFORCED as of 2026-08-23**, which closes the 2026-08-20 open decision. It
attaches at `interaction.authority.authority()`, one branch before the only path to GREEN: a
scenario that has not cleared both bars caps at YELLOW, so the fall-through is intact — a blocked
scenario keeps working, reviewed. Absence of measurement BLOCKS (the `ActuationReach.unprobed()`
precedent), refusals name the failing bar with its number, and the standing is computed in the
maturity registry's existing cached refresh, off the same rows, so it cannot drift. Nothing
regressed: `derive()` grades **0 transitions CERTIFIED** today, so nothing reached that branch
before the change.

**The 2026-08-23 in-flight items all landed same-day** (the crank journals its control; the
vault-overwrite fix; the swallow-by-design audit — LEARNINGS 08-23). **Open, now owned:** the
MISMATCH split (`mismatch_kind` + `verify()`'s missing `expected_next` branch) → SESSION_20;
witnesses borrowing other ATS's state names on out-of-vocabulary platforms → kind-first
classification (`PLAN_generalization_v1.md` §3.1). **Open, un-owned:** audit computations folded
into hot shared paths — moving work inside a shared cached refresh widens its blast radius from
"this number is wrong" to "every consumer is down" (measured: one malformed journal row crashed
the whole authority seam via `MaturityRegistry.refresh()` before the 2026-08-23 hardening;
LEARNINGS has the write-up).

## The per-step loop — status

| Stage | Status | Where | Note |
|---|---|---|---|
| classify | ✅ built | `escalation_rules.py` | verified on real reCAPTCHA / 2FA |
| propose | ✅ built | `mcp/app/observer/ax_proposer.py` | AX sidecars on every capture |
| select | ✅ built | `select_stage/` | cache + Haiku SoM; cascade slots empty by design |
| decide | ✅ built, live-driven to the Submit gate 2026-08-06 | `controller/` | attended teacher-first cascade as of 2026-08-09 |
| act | ✅ built, fired live extensively | `mcp/app/executor/` + tier-2 protocols | StepRunner wraps every Career-Search path (2026-08-03) |
| verify | ✅ built | `select_stage/verifier.py` + StepRunner claims | no rung marks itself complete |
| supervise | ✅ rung 0 shadow | `interaction/supervision.py` | named all replayed incidents; no authority yet |
| authority | ✅ built + live | `interaction/authority.py` + seat | parks now announce at open; seat serviced by session-Claude |
| **learn** | **✅ wired 2026-08-09 — needs drives** | `perception/dataset.py` + `routers/transitions.py` | label → refit, automatically; witnesses no longer frozen |

Guardrails unchanged and live: $5/week cap, never auto-solve captcha/2FA, Submit held for the
operator, credential flows collect nothing, secrets never captured.

## Priorities (re-ordered 2026-08-26 — everything else queues behind these)

The 2026-08-23 list is in git history. Much of its "blind to the world" debt has since been paid
live (first errand fire and its measured boundary, first real inbox sweeps, phase-carrying rows,
8 submissions across 6 platforms — LEARNINGS 08-23/24/25). The new list, per
`PLAN_generalization_v1.md`:

1. **Run the briefs in order, each ending on a live drive.** S14 (one fact one place — **done
   2026-08-26**, live-proven) → S15 (count the unknowns — **built 2026-08-27**: naming report +
   panel live against the corpus, 20 rows quarantined live, column drop staged for next restart,
   location door landed; the NAMING half is the operator's at the next drive) → S16 (facts with
   expiry — **built 2026-08-27**: world-fact shape + constructor gate, `linkedin_recipe` migrated
   with its retraction kept, staleness report + panel live, the prose-defending test re-pointed
   to shape; the RE-VERIFY drive is owed) → S17 (consultation as input — **built 2026-08-27**:
   `orientation_context` composed over brief + registry NOTE + account store + tab claims + stale
   claims, wired at classify/enter_apply/account with output-observing tests, `find_existing`
   ahead of the account mint, cockpit card; four of the eight counted instances now cite
   themselves live) → S18 (observation profiles — **built 2026-08-27**: reading order per
   page-kind with additive platform sharpening, `wizard_position` reading the "step 1 of 6" the
   regex had always parsed and discarded, a real dialog reader on the census, the 08-21
   truncation flags carried end to end after being dropped twice, and `could_not_see` on every
   report) → S19 (one resolver — **partly built 2026-08-27**: an untrusted click now names itself
   in `mode`, the VISIBLE twin wins a same-destination tie, `addressed_by` reaches the caller, and
   one contradiction is left standing with the experiment that settles it. **The four addressers
   are still four** — collapsing them is a per-endpoint migration behind a live ATS each, and the
   next step is to count the doors on real drives now that `addressed_by` is observable) → S20
   (act carries evidence — **partly built 2026-08-27**: `check_group` gained the
   `expect_question` guard `/execute` has had since 08-19 and reports the question it answered,
   `verify()` gained its missing `expected_next` branch, and `mismatch_kind` splits the one word
   that meant two facts. **The recognizer chain is still prose, not dispatch** — that is the
   remaining piece). Each kills a counted failure class from the ten-session retrospective; none
   is speculative.

   **S21 (session restore) is DONE and live-proven 2026-08-27** — both signed-in profiles
   captured warm over CDP, encrypted beside the vault, and the recovery loop run end to end on
   LinkedIn (jar cleared to 0, restored 79, reloaded, still signed in, screenshot-confirmed).
   `cookie_ttl_s` reads a real number after a month inert; the naive minimum it replaced measured
   **23 s on a healthy Indeed session**. The merge+restart it was written to survive was survived:
   main fast-forwarded S15→S21 and the armed `search_queries` drop fired on 718 live rows.

   **The queue's live-drive debt is PART-PAID, and the first drive found a real bug.** Attempted
   2026-08-27 on an operator-approved Staples posting. Four rungs ran clean (`provisioned` →
   `authenticated` → `radius_set`, the last recorded honestly as *not applicable* → `review_page`,
   one application queued). Then `open_job_card`'s click **landed on a filter** — `origin` became
   `JOB_SEARCH_PAGE_JOB_FILTER`, `f_SAL` appeared, `start=50` dropped — and the approved card was
   reported `not_found` after eight honest wheel batches over a set that no longer contained it.
   **S20 held**: the verifier returned `mismatch` with a structured `url_param` expectation and
   refused to bless it. The fix shipped the same session (`0b92f82`): `/open_job_card` reports the
   URL either side of its own click and the open_pane rung names the drift in the engine's
   vocabulary. The application is `parked:operator` and resumable via `apply_reopen`.

   **The cockpit's step-through-iness is settled (operator-directed 2026-08-27): `/run` is the
   default press, `step` is the inspection tool, and the pick boundary stays consent.** What made
   that safe to say: STOP_UNSURE wires `BeliefState.blocks()` into the run loop against the floor
   that already existed (0.75), the cockpit carries the same number as a "seeing N%" chip, the
   Overview counts sessions holding still for the operator (derived from the record, no CDP), and
   the header's location is served from the search row's page-backed fact instead of the declared
   intent. The view rule that fell out: **a view field serves its authority or says why it
   cannot** — three UI lies in one day were all true values read from the wrong field.

   **Still owed, and now more specific:** the operator names the preferences landing (S15), the
   virtualisation claim is re-verified (S16), the consultation citations appear in a real trail
   (S17), the census/dialog gaps meet a real form (S18), a duplicate-control page exercises the
   visible-twin tiebreak (S19), and a screener exercises the question guard (S20). **None of those
   were reached** — the drive stalled before entering an ATS, so every rung past `open_pane` is
   still unexercised. The next drive should start from a small, freshly-queried result set rather
   than page 3 of a long one, because eight wheel batches over a virtualised list is where the
   stray click came from.

   **Two carried items, both deliberate.** (a) The `observed_jobs.search_queries` DROP is armed in
   `migrations.py` and rehearsed on a scratch DB; it fires on the next restart of this code
   against Postgres, and **any process still running old code breaks the instant it does** — so
   restart the main API deliberately, not incidentally. (b) The next live drive owes three
   measurements: the operator names the preferences landing (S15), the virtualisation claim is
   re-verified on a keyword results page (S16), and the location door is watched on a healthy
   sweep (it now drops-and-logs rather than raising, but has never met a live URL).
2. **Keep driving live between briefs.** Applications are the product, and the drives are where
   the three zeros get measured. Rows, labels, sweeps, and scorecard movement ride along free.
3. **Label the tail as the rows arrive** (unchanged, byproduct — every label still refits
   table + witnesses + programs on write).
4. **Watch the two-bar gate; never feed it.** Promotion happens through the authority seam if a
   family clears both bars in passing — the gate is armed, not a target.
5. **Parked, explicitly**: Reasoner v2 / S06–S09 (planner re-scoped — the live ladder gets
   reliable first), element-level bbox faucet, L4 intent model (volume-gated: ≥500 journaled
   intents), Interaction API Phase 2, `main.py` split, movement/diffusion input model,
   OmniParser removal (the 1.3 GB dependency — still the biggest cleanup win, scheduled
   deliberately), Account Manager build-out, FB Marketplace expansion, cloud move,
   Gmail-as-domain (reader ≠ domain; actions stay out of scope).

## Endgame (unchanged from 2026-07-22, sharpened 2026-08-09)

Claude is the novel reasoner **permanently** — and now also the cheapest escalation while the
operator's session is open, which inverts the old cost logic: the number that has to bend is
**teacher tokens per submitted application on LEARNED ground**, while on novel ground a teacher
call is the product working as designed. The inner system's job is perception, rails,
verification, and calibrated humility; every escalation must return a typed, scoped lesson or a
labeled row, so the same page is paid for once. "Claude-free" remains a per-scenario graduation
for routine scenarios, never the destination.
