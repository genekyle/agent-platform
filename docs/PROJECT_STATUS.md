# Project Status — Supervised Browser Agent

_Last updated: 2026-07-22 — **the north star was re-anchored** (`PLAN_perception_v1.md`,
operator-directed). The old endgame ("the inner system gets strong enough to run without Claude;
the student becomes its own teacher") is **retired by measurement**: no local model that reasons
fits this machine, and getting unstuck turned out not to need one. Claude is the novel reasoner
**permanently and by design**; the local system's job is to perceive accurately, act on rails,
verify honestly, and know precisely when it does not know. What has to get strong is the **inner
loops — the muscles** (perception, verification, recovery), not a second brain. PRINCIPLES §9 is
amended accordingly and the Endgame section below is rewritten._

_Previously 2026-07-20 — **the supervisor became priority #1** (`PLAN_supervisor.md`): a
per-turn observer that names what went wrong from a taxonomy mined from our own logs, instead of a
boolean `verified`. **S11 + S12 landed** — the state delta we had assumed existed and did not, the
10-class taxonomy with a deterministic rung 0 that names every replayed real incident, and the
perception `LiveActuator` never had (an AX scan, page text, and three silent failures made honest).
99 new tests; controlplane-api 427 → 465, interaction 78 → 138, mcp 53, controller-evals green.
Previously (2026-07-19) **the unexpected-state pass**: the ATS-login stale-tab blocker is fixed,
and "we are not where we assumed" is now one shared policy (`controller/unexpected.py`) with an
operator alert, a candidate-state store, and a blackboard gate. 26 new tests; controlplane-api
401 → 427 green, mcp 53 green, controller-evals green. Previously (2026-07-17) **Controller v1
(`PLAN_controller_v1.md`) BUILT end-to-end (M1–M5) and surfaced in the cockpit** (Lab → 🧠
Controller); what still remains there is the operator-present live drive (M2 teacher-compile +
replay, which also closes Interaction API Phase 1's DoD). Session briefs live in `docs/sessions/`.
The 2026-07-16 full rewrite below otherwise stands; the previous version (2026-06-15, SELECT-cascade
era) is in git history._

## Progressive autonomy — BUILT 2026-07-22 (branch `progressive-autonomy`)

The teacher/student split had a structural hole: **there was no seat for the teacher inside a
running drive.** `Reviewer` had two implementations — a blocking TTY prompt on *every* non-recipe
step, and a confidence floor that never asks anyone — and an escalation *returned*, ending the
drive. So sessions finished work outside the system. That was the standing "ad-hoc scripts" gripe,
and it was a missing seam rather than indiscipline (PRINCIPLES §11 had listed the fix as owed).

Landed: `interaction/authority.py` (four control modes from maturity × belief × reach),
`controller/maturity.py` (the per-transition ladder, a **view** over the journal),
`controller/reach.py` (can the executor operate this page — the operator's "the observer is great
until we can't do anything about it"), `controller/inbox.py` + `/api/controller/teacher/*` (**the
seat**), `interaction/lesson.py` (scoped, verify-before-accept), `controller/orientation.py` (the
Indeed→ATS deep end), prediction-before-escalation in `decide()`, and park-and-resume in the loop.
**+120 tests; interaction 206 → 237, controlplane-api 465 → 693, mcp 53, controller-evals 3 → 7.**

Details, the honest day-one numbers, and the falsifiers: `docs/PLAN_progressive_autonomy.md`.
**Owed: the operator-present live drives** (one Indeed, one Workday-from-applystart) and a cockpit
surface for the coverage map and the pending-questions pane.

## ⚠️ In flight — a large operator-led change is landing (declared 2026-07-22)

The operator is building **new teacher endpoints + permission-based acting**: letting the inner
models attempt more, fail earlier, and have those failures caught and kept, so that no teacher
token is wasted and the ad-hoc scripts-around-the-system stop (PRINCIPLES §8/§11 — the standing
gripe, now being fixed structurally rather than by discipline).

**Collision surface.** Perception v1 landed the same day and touches three of the same files.
What it added is **additive-only**, so a rewrite above it should not need to unpick anything:

| File | What perception added | If you are rewriting this |
|---|---|---|
| `interaction/decision.py` | `Bundle.belief` (appended, defaulted, **not** in `bundle_to_prompt`); `DecisionRecord.belief_*` (4 scalars) | keep the fields; they are optional and prompt-invisible |
| `interaction/decision_journal.py` | `record_for` flattens belief at the one choke point | keep the copy — it is what stops a seam journaling a decision without who observed it |
| `controller/live_actuator.py` | `observe()` captures then senses; `collect=True` ctor flag | keep the capture — "always be collecting" is the point |
| `controller/bundle.py` | `build_bundle(belief=…)` passthrough | pure passthrough, no logic |
| **`controller/teach.py`** | **untouched, deliberately** | yours |

**The connective tissue worth knowing about**, because it is the primitive the permission system
wants and it already exists: `BeliefState.blocks(consequential=…)` answers *"which axis should
stop me?"* over five separate uncertainties (state / element / answer / effect / novelty), not one
collapsed float. "Let the inner model try" is not one permission — it is *may act while unsure
about `state` on a reversible intent* (yes), *may act while unsure about `answer`* (never — that
invents an application answer), *may act while `novelty` is high* (no, retrieve first). One
confidence number cannot express any of those.

**And the rule that makes early failure valuable rather than merely cheap:** an action is worth
permitting to a weak rung iff it is **(a) reversible and (b) journaled with a real rationale and
cited evidence** (§8 + §10). A failure the system cannot see is not a lesson — it is the event-log
mistake again. Gate the permission on the journal contract, not on the risk level alone.

## What we're building (one paragraph)

A **supervised browser agent** that runs a per-step loop — classify → propose → select → act →
verify — where each decision is made by the **cheapest tool that's confident**, a human catches
anything that reaches the top, and every action is **journaled** as training data. The model emits
**intents from a closed vocabulary** through the Interaction API (the model says WHAT, the recipe
says WHERE, the API says HOW, the journal says WHAT HAPPENED). Claude is the **teacher**: it drives
novel domains, and its journaled work distills into local models (L3 perception, L4 intent policy)
that graduate learned scenarios off the expensive models entirely. Hard constraint: resource-efficient
(solo founder) — a **$5/week autonomous spend cap** is enforced.

## Terminology (one overload, fixed here)

- **Cascade layers L1–L6** — the SELECT cascade rungs (rules / cache / tiny classifier / micro
  selector / Haiku / vision+human).
- **L3 and L4 as model names** — L3 = the **page-state (perception) model**; L4 = the **intent
  (policy) model**. Historically these named the cascade layers they were to occupy; since the
  Interaction API, **L4's job changed**: it emits *intents* (trained from the journal), not raw
  element picks (trained from selection telemetry). When a doc says "L3/L4" it means the models.
- **API tiers 1–3** — the Interaction API namespace: tier 1 primitives, tier 2 site-agnostic
  protocols, tier 3 domain skills (`PLAN_interaction_api.md` §7).
- **Rungs** — the resolve_answer cascade (exact → normalised → alias → Haiku → ask). The
  **controller** (`PLAN_controller_v1.md`) reuses this shape one altitude up: recipe/program →
  Haiku → teacher → human.
- **Teacher-driven drive** — redefined 2026-07-20 (operator-directed; **PRINCIPLES §11**): the
  **controller drives**; the **teacher = the local Claude agent** rides alongside and steps in only
  at pauses (escalation / low confidence / propose-approve), acting exclusively through the
  Interaction API + `Reviewer` seam and labeling as it goes. "Claude drives in front" is bootstrap
  mode, not a teacher drive; free-handed scripts around the API are a §8 violation, period.

## What changed since the last status (2026-06-15 → 2026-07-16)

1. **The agent did real work.** Indeed smartapply submitted end-to-end (07-11); FB create-listing
   driven live (07-10); cross-domain Gmail login-code errand (07-10); **first full Workday
   application submitted** (Wellington, 07-15); Greenhouse (KKR) driven to its last field (07-15).
   Career Search became a parent domain with an ATS taxonomy (Workday, Greenhouse, AppVault, iCIMS…),
   an accounts system, and an apply epilogue.
2. **The widget protocol layer was discovered** (the Indeed distance pill): AX finds *elements*, but
   widgets are elements + a *protocol* (open → stage → confirm → commit → confirm outside). See
   `interaction-layers.md`; PRINCIPLES gained §6–§8.
3. **The corpus reckoning (07-16).** The event log was never a training corpus (1000-line ring
   buffer, no fingerprint/session/outcome, no trainer reads it), and the real corpora are written
   only by `runtime/loop.py`, which live drives never touch — so three months of live work produced
   **zero** training rows. This is the finding that forced the journal-first Interaction API and the
   spine decision. See LEARNINGS 2026-07-16 and `DECISION_two-stacks-one-spine.md`.
4. **Interaction API Phase 1 shipped (07-16), journal-first**: `packages/interaction` (contract +
   fingerprint + append-only journal), `intent_api.journaled` route decorator,
   `resolve(ats, field)` over one recipe schema (`apply_fields.py`, 32 fields / 3 ATS),
   `/describe_widget` (12 widget types), `/select_option`, `/set_date`, `/check_group`,
   `/scan_required`, `/probe` (journaled discovery). 119 tests green.
   **Phase 1's Definition of Done is NOT met**: no live drive has run through the new endpoints;
   the page-side JS is unvalidated (PRINCIPLES §5). Meeting it is controller Session 02's job
   (`docs/sessions/SESSION_02_rung0_indeed_replay.md`).
5. **(07-17) The controller was adopted as priority #1** — the missing `decide()` in
   `observe() → decide() → act()`. Plan: `PLAN_controller_v1.md`; five sessioned milestones
   (contract → rung-0 Indeed replay → Haiku rung → propose-approve teaching → shadow metric).
   Career Search proves it before any expansion.
6. **(07-17) Controller v1 built end-to-end (M1–M5) + cockpit surface.** The frozen contract
   (`interaction/decision*.py`: Bundle/Decision/DecisionRecord + `bundle_to_prompt`), the pure
   `build_bundle`, the compile-on-first-drive intent programs, the `resolve_answer`-shaped cascade
   (rung 0 recipe → rung 1 Haiku behind an HTTP seam → escalate), the thin loop harness with the
   consequential gate + escalation ladder, the propose-approve DAgger gate (corrections → golden
   rows), and the measurement layer (`shadow_agreement`, `make controller-evals`, `CONTROLLER_
   PROMOTION.md`). Surfaced at **Lab → 🧠 Controller** (scoreboard, rung mix, a read-only
   observe→decide preview, the program library, the decision feed). **84 new tests; full suite 442
   green.** NOT yet done — the operator-present live drives: the M2 teacher-compile + replay on the
   Indeed apply backlog (which double as Interaction API Phase 1's unmet DoD), and the first real
   shadow-agreement numbers. Everything those drives need is wired; only the driving remains.

7. **(07-19) The unexpected-state pass — "act → observe → verify" given teeth.** The reasoning-driven
   ATS login was dying opaquely because **a stale CDP tab is silence, not an error**: the capture
   server swallows the discovery failure and returns a *successful* empty scan, so a dead tab read as
   "no form here" (LEARNINGS 2026-07-19). Fixed by detecting it, re-resolving the live tab once via an
   injected `re_resolve` seam, and escalating honestly otherwise. Generalized in the same pass:
   `controller/unexpected.py` is now the single policy for RE_OBSERVE | ESCALATE | CONTINUE across
   both levels of staleness (protocol `STALE_STATE_OUTCOMES` and tab `STALE_TAB`), replacing the
   copy that lived inline in `controller/loop.py`; `handoff.emit_escalation` gives the login drive and
   the controller the operator alert they never had (banner + `handoffs.jsonl` + the Session Activity
   timeline, no frontend change); `page_state_candidates` writes an unregistered page into the
   registry as an inert `status="candidate"` row so the state graph **grows from what we actually
   meet** (promotion = one `PATCH` flip); and the blackboard mints an `unexpected_state` blocker so a
   page we cannot name halts `proceed_decision` like a captcha. Owed: `reconcile` halts but does not
   itself alert, and `run_controller` still has no production call site.

## The per-step loop — status

| Stage | Status | Where | Note |
|---|---|---|---|
| classify | ✅ built | `escalation_rules.py` | verified on real reCAPTCHA / 2FA |
| propose | ✅ built | `mcp/app/observer/ax_proposer.py` | AX sidecars emitted unconditionally on every capture |
| select | ✅ built | `select_stage/` | cache + Haiku SoM live; L1/L3/L4 cascade slots still empty by design |
| **decide** | **✅ built (offline) — live drive owed** | `controller/` | the teachable reasoner; M1–M5 landed 2026-07-17 (`PLAN_controller_v1.md`). Cascade + loop + teach + shadow/replay all tested; the operator-present live drives remain |
| act | ✅ built, **fired live extensively** | `mcp/app/executor/` + tier-2 protocols | via teacher drives; the autonomous loop remains record-only/`run_live`-limited |
| verify | ✅ built (element-level) | `select_stage/verifier.py` | protocol-level verification now lives in tier-2 outcomes (`ok` = verified at commit) |
| **authority** | **✅ built 2026-07-22 — live drives owed** | `interaction/authority.py`, `controller/maturity.py`, `controller/reach.py`, `controller/inbox.py` | who owns each turn (GREEN/YELLOW/ORANGE/RED), per transition (`PLAN_progressive_autonomy.md`, PRINCIPLES §12). Gating on by default at `/api/controller/run`; day-one coverage is **18 transitions, 0 certified** |
| **supervise** | **🔨 in progress — S11–S12 of 6 done** | `interaction/delta.py`, `interaction/supervision.py`, `controller/loop.py` | the post-act diagnosis (`PLAN_supervisor.md`). Delta + taxonomy + rung 0 + the journal columns landed 2026-07-20, running in **shadow** (no authority). Owed: rung 1 + gated screenshot (S13), the commentary pane (S14), postconditions (S15), the live shadow drives (S16) |

Guardrails all live: $5/week cap (`anthropic_usage.enforce_budget`), human escalation on
stop-state / over-budget / low-confidence / no-match / verifier-fail, never auto-solve
captcha/2FA, secrets never captured.

## The two execution stacks — and the corpus reality (measured 2026-07-16)

The **runtime loop** (`runtime/loop.py` + `select_stage/`) is the flywheel machine and the only
writer of the June-era corpora; the **live-drive path** (teacher driving the MCP/API endpoints) is
where all real work happened. Until the journal, they didn't share a corpus. Numbers as of 07-16:

| Corpus | Rows | Writer | Read by a trainer? |
|---|---|---|---|
| `intent_journal.jsonl` | **6** | `journaled` endpoints (live drives) | will feed L4 — the spine going forward |
| `loop_steps.jsonl` | 43 | `runtime/loop.py` only (mostly `run_batch`) | L4/state-transition (legacy framing) |
| `selection_telemetry.jsonl` | 101 | `select_stage` via the loop | L3/L4 (legacy framing) |
| `event_log.jsonl` | 416 (ring) | everything | **NO — operator wall display only, never a corpus** |
| Captures + AX sidecars + labels | 157+ captures, all with AX candidates | `/capture` | grounding + L3 |

**Nothing in the loop is a trained model yet, and no model has ever been trained-and-promoted from
live-work data.** Baselines: L3 v0 stage-observer **94%** held-out on 98 labels (2026-07-09);
grounding **0%** on 19 records (data-starved). The flywheel has never completed one revolution —
that is the project's single most important open item, and the controller build is how it turns:
every controller step journals, and controller Session 02 starts the crank on the apply backlog.

## Priorities (ordered — everything else queues behind these)

_Reordered 2026-07-22 (operator-directed): **perception is #1**, with the supervisor immediately
behind it. They are one activity, not a queue: the supervisor NAMES what went wrong; perception
supplies the evidence it names it from — and today rung-0 supervision is reading a single state
string produced by one witness that cannot tell it when it is somewhere new. Previously reordered
2026-07-20: **the supervisor is #1.** The reasoning layer —
observer, reasoner, planner — is what gets the system unstuck and tells every other part what to
do, so it leads. It does **not** displace controller v1: the supervisor rides on the live drives
v1 already owed (M2), which is why the two are one activity and not a queue. Previously reordered
2026-07-17 (the controller ahead of Phase 1)._

1. **Perception v1 — two witnesses, compositional states, scoped lessons** (`PLAN_perception_v1.md`;
   sessions S17–S22). The NB layer does not get replaced, it gets **assisted**: a second witness
   with a different failure mode (a frozen image encoder + prototype bank), a state vocabulary with
   **facets** (`domain/platform/phase/condition/variant`) so a new Workday tenant inherits instead
   of cold-starting, a `BeliefState` carrying **five separate uncertainties** (state / element /
   answer / effect / novelty) instead of one collapsed float, episodic retrieval, and a typed
   `Lesson` with a `scope` so an escalation is paid for once. Measured before it was planned
   (2026-07-22, Apple Vision `VNGenerateImageFeaturePrint`, free + local): **93% at platform level,
   55% at exact state, ~0.836 same/different AUROC** — and the confusions are `workday_my_
   information ↔ workday_questions ↔ my_experience`, i.e. exactly the phases the DOM separates
   trivially. That split IS the design: vision witnesses the platform and the novelty, the DOM
   witnesses the phase. Also found: **101 of 174 labeled captures point at screenshots that no
   longer exist on disk** — fix the linkage before benching anything.

2. **The Supervisor — a per-turn observer that NAMES what went wrong**
   (`PLAN_supervisor.md`; session briefs `docs/sessions/SESSION_11`–`SESSION_16`). Replaces
   today's boolean `verified: true|false` with a cited diagnosis from a closed failure taxonomy
   **mined from our own logs** (8 classes cover every machine-readable stuck moment we have), and
   makes recovery a *selection* from a small playbook rather than open-ended reasoning. Its three
   products, in payoff order: **legibility** (a live commentary of why, every turn, without
   acting), **corpus** (every turn a pre-labeled example; every override a free correction), and
   **autonomy** (promotion per *failure class*, never globally). Cost is held under the $5/week cap
   by a cascade, same as everywhere: rung 0 deterministic ($0, every turn, emits a full readable
   verdict) → rung 1 Haiku only when rung 0 can't name the class → rung 2 teacher. **Vision stays
   gated** — a screenshot is a diagnostic the reasoner *requests*, never a firehose.
   **S11 + S12 landed 2026-07-20** (99 new tests): `interaction/delta.py` (`StateDelta` — the
   always-on cheap sense we had assumed existed and did not) with the treadmill guard rebuilt on
   it; `interaction/supervision.py` (the 10-class taxonomy, the 7-play playbook, rung-0 `classify`
   — which names all 9 replayed real incidents with **zero UNKNOWNs**); `supervisor_*`/`delta_*`
   columns on `DecisionRecord`; the `on_supervise` seam in `run_controller`. And the perception the
   controller never had: `LiveActuator.observe()` now runs an AX scan and passes **page text**
   (without which Workday/Greenhouse states — and the *captcha markers* — were unreadable), and
   three silent-failure defaults became honest handoffs.
   **The supervisor has no authority yet — stage 1 is shadow, guarded by a test.**
   **Next: S13** (rung 1 Haiku + the gated screenshot) and **S14** (the commentary pane); both
   offline, as is S15. The only operator-present work is S16.

3. **Controller v1 — the teachable `decide()`** (`PLAN_controller_v1.md`; session briefs
   `docs/sessions/SESSION_01`–`SESSION_05`). **Career Search only — prove it in this domain first,
   then expand.** M1 Decision contract + Bundle → M2 rung-0 Indeed replay through the Interaction
   API with zero model calls (doubles as Phase 1's DoD) → M3 Haiku rung + escalation ladder →
   M4 propose-approve teaching (corrections as golden rows) → M5 shadow agreement + replay evals +
   promotion gate. **North star (operator-directed 2026-07-17):** `decide()` must ultimately own the
   whole `apply_sweep` cadence — state-check → search (`reporting analyst` / Manchester NH / 50mi) →
   apply to *everything* end to end → record → paginate — executed as a known program in v1, reasoned
   by the planner in v2. See `PLAN_cadence_northstar.md`. **The live seam is BUILT + offline-tested:**
   `controller/live_actuator.py` (the `Actuator` — resolve→journaled endpoints, Submit held; 9 tests;
   live read-only `observe()` classified `indeed_home`) and `controller/teach_session.py` (the §9
   teaching surface — `propose()`/`commit()`, teacher decides + Haiku shadows; 6 tests). Reasoning
   roles re-anchored in PRINCIPLES §9 (student = central cog; Haiku = backstop). **Owed = the live
   teaching drive itself** (journal flows, programs compile) + search-phase/tab states in the Bundle.
4. **The first flywheel revolution** — drive → journal → label → train L3 v1 → shadow → promote →
   measure. Fed directly by controller drives. The full plan with gates and metrics:
   `PLAN_flywheel_first_revolution.md`.
5. **Spine convergence** — one corpus spine (the journal), one action surface for teacher and loop
   alike. Decision + component dispositions: `DECISION_two-stacks-one-spine.md`. (The loop emitting
   intents is Phase 4 of `PLAN_interaction_api.md` — the controller's `decide()` is exactly the
   piece the loop will adopt; still not current work to rewrite the loop itself.)
6. **Interaction API Phase 2** — the intent surface (`/api/interact/*`, `{ats, field, value}`),
   `/resolve_answer` rungs + alias-table writeback.
7. **Parked** (do not resume until the wheel turns once, unless one blocks a drive): `main.py`
   split resumption (5,061 lines, 170 routes — it's growing again; the route-inventory guardrail
   still holds), movement playground / diffusion input model, OmniParser removal, Account Manager
   build-out (`PLAN_account_manager_and_l3.md` — its capture/label directive is *absorbed into* #3),
   `/scan_form` retirement (gated on a live diff vs `/scan_required`), FB Marketplace expansion.

## Endgame (rewritten 2026-07-22 — the re-anchor; the 2026-07-16 version is in git history)

Operator-stated: **Claude is the novel reasoner, permanently.** The teacher rung is not scaffolding
awaiting removal — it is a load-bearing part of the finished machine, and every design choice should
stop asking "how do we get Claude out of the loop" and start asking "how do we make one Claude call
buy more." The inner system's job is **perception, rails, verification, and calibrated humility**:
recognize the state (two witnesses, DOM and pixels, with complementary failure modes), act through
the closed intent vocabulary, name what went wrong from the taxonomy, run the deterministic play —
and raise its hand *accurately* when it is somewhere genuinely new. Escalation ladder unchanged:
deterministic play → Haiku backstop (bounded) → Claude (teaching) → human (stop-states, credentials,
irreversibles — always). What changes is what an escalation must **return**: a typed, scoped
`Lesson` (universal / platform / tenant), accepted only after its prediction verifies, so ten
Workday tenants teach the same page **once**. "Claude-free" stays a per-scenario graduation for
*routine* scenarios and is no longer the destination; the number that has to bend is **teacher calls
per submitted application**, not teacher calls to zero. The ladder is specified in
`DECISION_two-stacks-one-spine.md`; the controller's cascade is its running implementation;
`PLAN_perception_v1.md` is how the bottom of it gets strong.

## Short term vs long term

**Short term:** controller sessions 01–02 — the Decision contract, then the rung-0 Indeed replay.
The replay doubles as Phase-1 live validation and starts filling the journal against the existing
13-job apply backlog (the crank is the work itself, not extra work).

**Long term:** L3 owns state recognition per ATS; L4 slots into the controller's rung 1 for learned
scenarios (shadow → gated promotion per `docs/CONTROLLER_PROMOTION.md` once Session 05 writes it);
the teacher's journaled drives keep expanding the recipe/protocol/program library; cost per
submitted application falls as scenarios graduate; the vision catchall earns its keep on protocol
discovery and AX-blind pages; continuous retraining on a cadence once the manual crank has proven
the loop.
