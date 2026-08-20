# Project Status — Supervised Browser Agent

_Last updated: 2026-08-09 — **the refocus** (operator-directed, after the burnout audit; LEARNINGS
2026-08-09). The full flywheel revolution is re-sequenced, not abandoned: **the controller is the
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

## Priorities (ordered — everything else queues behind these)

1. **Occupy the seat: attended live drives.** Run the apply cadence through
   `POST /api/controller/run` (attended, progressive) with the session-Claude teacher polling
   `GET /api/controller/teacher/pending` and answering with real rationales. Every park answered
   is a golden row **with its screenshot**; every drive advances the real backlog. The measure per
   session: rows banked, teacher labels written, parks answered — visible in the cockpit Trace.
2. **Label and let it train.** Work the Trace correction form (or the API) after every drive;
   train-on-label refits the table + witnesses each time. Watch the witness census
   (`from_transitions`) and shadow agreement climb toward the gate (≥90% over ≥25 steps) —
   promotion stays per-state, per-ATS, fall-through intact (`CONTROLLER_PROMOTION.md`).
3. **The revolution's remaining tail** — when (and only when) the numbers pass the gate: promote,
   and measure teacher tokens per submitted application against the corpus. This closes
   `PLAN_flywheel_first_revolution.md` §0 on the transition-spine terms.
4. **Parked, explicitly** (do not resume until the wheel turns once, unless one blocks a drive):
   element-level bbox faucet, L4 intent model (volume-gated: ≥500 journaled intents), Interaction
   API Phase 2, `main.py` split, movement/diffusion input model, OmniParser removal, Account
   Manager build-out, FB Marketplace expansion, cloud move.

## Endgame (unchanged from 2026-07-22, sharpened 2026-08-09)

Claude is the novel reasoner **permanently** — and now also the cheapest escalation while the
operator's session is open, which inverts the old cost logic: the number that has to bend is
**teacher tokens per submitted application on LEARNED ground**, while on novel ground a teacher
call is the product working as designed. The inner system's job is perception, rails,
verification, and calibrated humility; every escalation must return a typed, scoped lesson or a
labeled row, so the same page is paid for once. "Claude-free" remains a per-scenario graduation
for routine scenarios, never the destination.
