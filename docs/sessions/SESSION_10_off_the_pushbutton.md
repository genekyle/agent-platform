# Session 10 — off the push-button: give `run_controller` a live call site

**Gates: none new — everything this needs is built.** The blocker is that it has never been wired.
**Read first:** `CLAUDE.md`, `docs/PLAN_controller_v1.md` (§2 the cascade, §5 the harness, §8 non-goals),
`controller/loop.py`, `controller/live_actuator.py`, `controller/teach_session.py`,
`controller/unexpected.py` + `runtime/handoff.py` (the escalation/alert seam added 2026-07-19),
LEARNINGS 2026-07-19.

## Why this session exists (operator-stated, 2026-07-19)

> "I don't want to be this weird push button to begin next step — I need the reasoner, planner and
> observer to do most of the work."

That is currently a *wiring* gap, not a capability gap. **`run_controller` has no production call
site** — grep it and every hit is a test. The only live controller surface is the step-wise
`POST /api/controller/teach/observe` → `/teach/commit` pair, which is *by construction* one operator
press per step. The loop that would replay known steps at $0 without asking exists, is tested, and
has never been pointed at a real tab.

## The three things that actually shrink the button

1. **Rung 0 replays known steps with no button at all.** `decide()` returns a `recipe`-rung decision
   from a compiled intent program; `PROPOSE_RUNGS` means only non-recipe decisions go to the reviewer.
   So every state that has a compiled program is already silent — the button shrinks in proportion to
   the program library, which grows from the teacher drives themselves.
2. **Only *uncertain* steps surface.** Rung 1 (Haiku today, the trained L4 later) proposes; below the
   0.75 confidence floor it escalates instead of acting. The operator sees the residue, not the flow.
3. **The gates that must NEVER become silent stay loud.** `CONSEQUENTIAL_INTENTS` (Submit) is held
   for the operator always; `human_required` states (Workday sign-in / create-account) are structurally
   undriveable; BLOCKED goes straight to a human. Getting off the push-button means removing the
   *routine* presses, never these.

## Scope — in

1. **A live `run_controller` call site.** One endpoint (e.g. `POST /api/controller/run`) that composes
   the pieces already built: `LiveActuator` as the actuator, `programs.ProgramStore()`, the Haiku
   reasoner as `model=`, and — new as of 2026-07-19 — `on_escalate=handoff.escalation_callback(...)`
   so every stop raises a real operator alert (banner + `handoffs.jsonl` + the Session Activity
   timeline) instead of a silent journal row. Bounded: `max_steps`, budget-gated, one task.
2. **A non-blocking reviewer.** `run_controller`'s `cli_reviewer` blocks on stdin, which is exactly why
   the drive is push-button today. Replace with a reviewer that (a) auto-approves rung-0, and
   (b) for anything else either escalates (autonomous mode) or parks the step for the existing
   `/teach/commit` surface (supervised mode). The DAgger correction path must still journal
   `{proposed, corrected}` golden rows — that is the training signal, not a UI nicety.
3. **Prove the $0 path.** One drive on a state family that already has a compiled program, showing
   **zero model calls** on the happy path (this is M2's original DoD, still unmet) and the button
   pressed only at the consequential gate.
4. **Measure it.** Record steps-per-operator-press and the rung mix from `decision_journal.summarize()`
   before/after. "Less push-button" has to be a number or it is a feeling.

## Scope — out

Training L3/L4 (data-gated — the journal must fatten first); the v2 planner (`PLAN_reasoner_v2.md`
owns the itinerary altitude); rewriting `runtime/loop.py` (explicit non-goal); new domains.

## Where the local models actually land (recorded so the question stops recurring)

**Nothing local runs today.** Reasoning is Claude (teacher, novel work) + **Haiku as a hosted cheap
backstop** at rung 1, under the $5/week cap. The students — L3 (page-state perception), L4 (intent
policy), later planner + critic — are unbuilt *by design* because the journal must fatten first. Their
seat is already reserved: **rung 1 is served behind an HTTP endpoint (invariant #6)**, so a trained
local model drops in *above* the Haiku backstop as a deployment swap with **zero contract change** —
it leads the backstop, it does not replace it. "Locally" means a small model on a local inference
endpoint (this machine or a cheap GPU box), trained on the journaled corpus, promoted **per scenario**
only after shadow-agreement ≥ 90% over ≥ 25 steps + replay green. Claude never stops being the teacher
for novel terrain — that door stays open by design.

## Definition of done

- `run_controller` drives a real Career-Search tab end-to-end from an endpoint, bounded and budget-gated.
- One happy-path drive with **zero model calls** (rung 0 only), operator pressed only at Submit.
- Every escalation produced a handoff alert (verify in Session Activity + `handoffs.jsonl`).
- Steps-per-press and rung mix recorded in `docs/PROJECT_STATUS.md`; LEARNINGS appended.

## Non-negotiables

- Act **only** through the Interaction API — acting through anything else is a failed session.
- Submit stays the operator's; never auto-solve captcha/2FA; the agent never types a password or
  creates an account.
- Live driving costs data — check `make data-check` first if on a capped connection, and defer.
- Stage explicit paths; `git status` before commit; a concurrent session may own the UI — own every
  staged path. Commit to `main`.
