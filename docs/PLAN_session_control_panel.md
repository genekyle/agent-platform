# PLAN — the Session Control Panel: giving the local side a way to turn the loop

**Status: proposed 2026-07-23 (operator-directed). Design only — nothing built yet.** This doc is
the agreed output of a step-back: the operator described the loop they want (in their words, quoted
throughout), and this captures it before any code. Supersedes the small "Coaching pane" framing
(`LiveDrivePanel`), which was a keyhole into a loop only the teacher could turn.

---

## 0. The disconnect, stated as a fact about this repo

Every live drive this session was turned by **the teacher, from outside the system**: a human (or
Claude) called `POST /api/controller/run` by hand, decided when to survey the window, serviced the
teacher inbox, chose when to tidy. The muscles all exist — perception (two witnesses), the
controller's `decide()`, authority (GREEN/YELLOW/ORANGE/RED), the teacher inbox, the window
manager — but **there is no skeleton the local side owns**. The cadence ("when to perceive, when to
plan, when to act, when to escalate, when to coach") lives in the teacher's head and transcript,
nowhere the operator can start, see, or steer.

Operator, verbatim: *"we set up this beautiful handoff, teacher and student flags, but we never
gave our local side the way to initialize everything… all the logic in your head instead is just
out there not being used."*

The fix is one place that **controls a session** — starts it, shows it thinking, and lets the
operator steer it — with a real loop underneath that the local side turns. Not a coaching keyhole:
a control panel.

## 1. What a "session" is — the reframe (operator-corrected)

A session is **one focused Chrome instance trying to complete one task.** Not one-per-domain — the
operator's earlier assumption, corrected here:

> *"initially I thought each domain like marketplace selling or career search would have its own
> session, but it's more complicated since domain tasks also have cross-domain errands. So it's
> pretty much one focused chrome instance where it's trying to complete a task."*

So the identity is **task**, not domain. "Look for jobs" and "apply + search cadence" are both
Career-Search tasks, and a Career-Search task may take a **cross-domain errand** (a Gmail login-code
detour) *inside the same session* — which is exactly why domain can't be the session boundary.

- **One active session at a time, for now** (testing). *"making sure we have no other running
  sessions (for now during testing phase but eventually we want multiple sessions)."* The design
  must not foreclose multiple later, but enforces one now.
- **A session is "tasks waiting to happen"** — it exists before it runs; initializing it is
  provisioning, not driving.

## 2. The session lifecycle

```
  INITIALIZE ──▶ READY ──▶ RUNNING ⇄ PAUSED ──▶ ENDED (+ CLEANUP)
                             │                     ▲
                             └── gate/park ────────┘  (coach, then resume)
```

### 2.1 Initialize
*"initialize a session means it gets a chrome session ready and logged into indeed to start doing
the other tasks."* Initialization is a **precondition phase**, not the task loop:

1. **Guard**: refuse if another session is already running (the single-session rule; today's
   `drive_lock` is the keyboard-ownership half of this).
2. **Provision**: a fresh Chrome instance ready to work (Chrome-per-session already exists via
   training sessions — a dedicated profile + debug port).
3. **Reach the start line**: signed in to the target site (e.g. Indeed). Login primitives exist
   (`/auth_state` probe, login endpoints, `login_reasoner`); initialize *orchestrates* them and
   **stops for the human at any credential/2FA wall** (the hard boundary — the agent never types
   passwords).
4. **Declare the goal**: what task this session is for. *"see like, oh what are your goals for this
   session — is it just to look for jobs, or an apply-and-search cadence where we look at jobs per
   page and apply."* The panel offers the choice; the controller shows what it will do.

### 2.2 Run
The loop turns (see §4). The controller *"would let us choose, would show us what we're doing,
would allow us to pause."*

### 2.3 Pause / End
*"The session only ends after we pause-exit, or if it completes a full task."* Completion detection
("end flags") does **not exist yet** and is explicitly TBD (§11). Until it does, the two real
stopping points are:
- **Pause → exit**, with a safe cleanup.
- **Full task completion** — deferred to end-flags.

*"the pause-exit and cleanups should be there while we wait for real end flags."* So: build
pause/exit/cleanup now; leave a seam for completion later.

## 3. Who turns the crank — and provenance

*"who turns the crank can be either… the thing that actually turns it on should be an API call and
our system will be able to know who it came from, manually or automatically, and that should be
written in the API call itself."*

- The crank is an **API call** either way — a manual step-press and an automatic tick hit the
  **same endpoint**.
- The call **carries its initiator**: `initiator: "operator" | "auto" | "teacher"`. The system
  records who turned each step, so the journal shows provenance — this is the concrete form of
  *"the system knows everything comes from you."* It composes with the existing `rung`
  (recipe / model / teacher — who *decided*) to answer both "who advanced the loop" and "who made
  the call."

## 4. What one "step" is — and micro-stepping

*"One whole step includes the 4 steps you mentioned"* — **perceive → plan → act → verify** is one
beat. But:

> *"if we have problems from an input between steps or something novel we want to pause at every
> step at some point to help diagnose or push it in the right direction, that way we can control
> all parts and see the problem first-hand step-by-step (or micro-step)."*

So a step has two granularities, selectable per session (or per moment):

- **Whole-step** (default): perceive → plan → act → verify runs as one beat; the panel shows all
  four afterward.
- **Micro-step** (diagnosis / novel): the loop **halts between stages** so the operator sees what
  it *perceived* and *plans* **before** it acts, and green-lights the act separately. This is the
  "control all parts, see the problem first-hand" mode.

Micro-step is not a different loop — it is the same loop with a **stage barrier** the operator can
raise, to inspect and steer at the seams.

## 5. Pause — safe, checked, the basis for both coaching and exit

The operator asked for pause to be a **first-class, safe mechanism**, distinct from coaching:

> *"Coaching should only run during uncertain/novel, and we should instead do a 'pause' where the
> system checks for pauses… sent in or queued in (whatever the safest way to do this would be),
> where then we can instill coaching in the middle if we want to, but pausing to also be safe, so
> we can coach or drop the session entirely — and it requires to be paused and safely cleaned up."*

Design consequences:

- **Pause is requested, not seized.** A pause is **queued/flagged**; the loop **checks for a pause
  at a safe point** (a stage boundary, never mid-action) and honors it there. Safest form: a pause
  flag on the session that the loop reads between stages — the same "check at a safe seam" shape as
  micro-stepping, so the two share a mechanism.
- **A pause is where you can coach OR drop.** From paused you may: inject a coaching action
  (mid-flow, by choice), resume, or **exit the session entirely** — and *dropping requires the
  paused state and a safe cleanup* (close/park tabs, release the drive lock, mark the session
  ended). You cannot yank a running session out from under a live action.

**Coaching vs pause, kept distinct:**
- **Coaching happens automatically only at uncertain/novel gates** (the existing
  YELLOW/ORANGE/RED authority + teacher inbox). That is unchanged.
- **Pause is operator-initiated at any time**, honored at the next safe seam, and is what makes
  mid-flow coaching *and* safe exit possible. Coaching-at-a-gate is the system asking you; pause is
  you asking the system.

## 6. The Control Panel — the one place

Replaces the small coaching pane. Its own full tab, **intimate to one session** — all of that
session's context in one place — while journal/collected data still flow to the shared corpus.
*"one place to control everything on our side."*

Layout, top to bottom:

1. **Session header / initialize.** Which Chrome instance + login status; the goal; the mode
   (manual step / auto / micro-step). **Initialize · Start · Step · Pause · Exit.** Status:
   ready / running / paused / ended.
2. **Goals.** What this session is for (look-for-jobs vs apply-and-search cadence), chosen here,
   shown as what it will do.
3. **The live loop — the four stages, made visible** (all data already exists except the cadence):
   - **Perceive** — window health (tabs/dupes), belief (witnesses, uncertainty), recipe-vs-observer,
     what the page needs.
   - **Plan** — the intended action + why, confidence, which rung decided, expected next.
   - **Gate** — who owns this turn (GREEN/YELLOW/ORANGE/RED) and why.
   - **Act / Verify** — what it did, the outcome, whether the page actually moved.
4. **Coach.** At a gate: Go / Correct / Instruct / Take over / Stop, with the note that rides into
   the journal. From a pause: inject a coaching action or exit.
5. **Session memory (scoped).** This session's decisions, handoffs, lessons, and **provenance** —
   who decided and who advanced each step.

## 7. Exists vs missing (honest map)

| Piece | State |
|---|---|
| Chrome-per-session provisioning | ✅ training sessions (dedicated profile + debug port) |
| Single-active guard | ⚠️ partial — `drive_lock` is the keyboard-ownership latch, not a full session-running guard |
| Login primitives | ✅ `/auth_state`, login endpoints, `login_reasoner`; ⚠️ initialize must *orchestrate* them + stop at credential walls |
| Perceive stage (data) | ✅ belief, window, scan, recipe-vs-observer |
| Plan stage (data) | ✅ the `decide()` decision |
| Gate (authority) | ✅ GREEN/YELLOW/ORANGE/RED |
| Act / Verify | ✅ `LiveActuator` + loop verify |
| Coaching at gates | ✅ teacher inbox + the current pane |
| Read-only "watch it think" | ✅ `POST /api/controller/observe` (perceive→plan, no act) |
| Stepwise teacher act | ✅ `/teach/observe` + `/teach/commit` |
| Autonomous loop | ✅ `/api/controller/run` (fire-and-forget with parks) |
| **Session lifecycle object** (initialize/ready/running/paused/ended) | ❌ **missing** |
| **`session/step` — one crank, all four stages returned** | ❌ **missing** (the skeleton) |
| **Initiator provenance in the call** | ❌ missing (compose with existing `rung`) |
| **Micro-step / stage barrier** | ❌ missing |
| **Pause (queued, checked at a safe seam)** | ❌ missing |
| **Safe exit + cleanup** | ❌ missing |
| **The Control Panel UI** | ❌ missing (LiveDrivePanel is the precursor to fold in) |
| **End-flags / completion detection** | ❌ missing — TBD (§11) |

The muscles exist; the **skeleton and the cockpit** do not. That is the whole build.

## 8. The `session/step` contract (sketch — to be firmed in the build plan)

One call = one crank. Carries provenance and granularity; returns the whole beat.

```
POST /api/controller/session/{id}/step
  { initiator: "operator" | "auto" | "teacher",
    granularity: "whole" | "micro",
    resume_from?: "perceive" | "plan" | "act" | "verify" }   # micro-step continuation

-> { stage_reached, perceive{…}, plan{…}, gate{mode,why}, act?{…}, verify?{…},
     paused: bool, awaiting: "coach" | "act_ok" | null, provenance{initiator, rung} }
```

- **Whole**: runs perceive→plan→gate→(act→verify if GREEN), returns everything; stops at a gate
  awaiting coaching, or after verify.
- **Micro**: runs to the next stage barrier and returns `awaiting: "act_ok"` so the operator can
  inspect perceive+plan before act.
- **Pause**: a separate `POST /session/{id}/pause` sets a flag; `step` checks it at the seam and
  returns `paused: true` instead of acting.
- Reuses `observe` (perceive+plan) and `LiveActuator` (act+verify) under the hood — this endpoint
  is the **cadence wrapper**, not new reasoning.

## 9. Build order (proposed — for the follow-up build plan, not this doc)

1. **Session lifecycle object** + single-active guard + initialize (provision + login-orchestrate,
   stop at credential walls).
2. **`session/step`** (whole-step first) with initiator provenance — the skeleton.
3. **Pause / exit / cleanup** (the safe seam).
4. **The Control Panel** shell wired to lifecycle + step (fold in the existing perceive/plan/gate/
   context views).
5. **Micro-step** (the stage barrier).
6. **Session-scoped memory** view.
7. *Deferred:* end-flags/completion; multi-session.

## 10. Open questions / TBD

- **End-flags.** No completion detection exists. What *is* "task complete" for look-for-jobs
  (a page swept? a count?) vs apply-and-search (N applications submitted?)? Until defined, pause→
  exit is the only clean end. *(Operator: "still no end flags created yet so this will be TBD.")*
- **Multi-session.** Single-active now; the lifecycle object should carry a session id from day one
  so multi-session is a guard change, not a rewrite.
- **Auto cadence pacing.** In auto mode, what turns the crank and how fast — a server-side ticker,
  or the panel on a timer? (Bot-safety pacing already lives in the humanized driver.)
- **Where the local Claude agent sits.** `initiator: "teacher"` implies the local agent can turn
  the crank too; is that a third mode alongside manual/auto, or the same as auto with a different
  initiator?

## 11. Falsifying conditions

- **The panel still needs the teacher to curl anything** → the skeleton didn't land; the cadence is
  still outside the system.
- **Pause seizes mid-action** (a half-typed field, an orphaned tab) → the safe-seam rule was
  skipped; pause must be checked between stages, never during one.
- **Exit leaves the window dirty** (open apply tabs, drive-lock held) → cleanup is incomplete; a
  drop must always reach a clean, single-session-free state.
- **The journal can't say who turned a step** → provenance wasn't threaded; "everything comes from
  you" stayed a slogan.
- **A session gets keyed to a domain again** → the "focused Chrome per task, cross-domain errands
  inside it" reframe was lost.
