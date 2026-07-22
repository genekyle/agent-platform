# PLAN — Progressive autonomy: bounded teacher takeover

**Status: BUILT 2026-07-22** (M1–M8, branch `progressive-autonomy` off `main`). Operator-directed.
Owed: the operator-present live drives (§9) and a cockpit surface for the coverage map and the
teacher inbox.

> **Local owns the mission. The teacher only owns the uncertainty.**

---

## 0. The problem, stated as a fact about this repo

The teacher/student metaphor was broken in one specific, structural way: **there was no seat for
the teacher inside the loop.** `controller/loop.py` had exactly two teacher transports —

- `teach.cli_reviewer` — blocks on `input()`; a human at a TTY, on **every** non-recipe step;
- `teach.auto_reviewer` — a confidence floor that **never asks anyone**;

plus `teach_session.propose()/commit()`, which asks the teacher **every single turn**, including
turns a compiled program would have run for free. So the settings were *ask always* or *never ask*.
And when a drive hit something it could not do, `run_controller` **returned** — the drive was over.

There was nowhere for the local Claude agent to answer a question mid-drive. That is why sessions
kept falling back to hand-rolled scripts around the Interaction API: **a missing seam, not
indiscipline.** PRINCIPLES §11 had listed that transport as owed since 2026-07-20.

The second half of the same problem: `decide()`'s escalations were information-free
(`intent="observe"`, `confidence=0.0`, no hypothesis). On the hardest turns — precisely the ones
the teacher was paid for — the local layers made **no prediction**, so `shadow_agreement` had
nothing to compare. The teacher looked like it was doing everything because, on the record, it was:
**the student never took the exam.**

## 1. What was already here (and was NOT rebuilt)

| The incoming design asked for | It already existed as |
|---|---|
| the inner loop observe→estimate→act→compare→recover/escalate | `controller/loop.py::run_controller` |
| semantic actions, never selectors | `interaction/contract.py` + the journaled endpoints |
| four kinds of confidence + a risk rail | `interaction/belief.py::BeliefState` — five axes, `blocks(consequential=)` |
| name what went wrong, recover from a closed playbook | `interaction/supervision.py` + `controller/recovery.py` |
| local proposes / teacher approves | `controller/teach.py` propose-approve + golden rows |
| the lesson's content fields | `DecisionRecord` + `SupervisorVerdict` + `StateDelta` |
| "certified" | `docs/CONTROLLER_PROMOTION.md` — **documented, never machine-enforced** |

Genuinely new: `ControlMode`, per-transition `Maturity`, actuation `reach`,
prediction-before-escalation, park-and-resume, the teacher inbox, and the scoped `Lesson`.

## 2. Authority — who owns this turn (`packages/interaction/interaction/authority.py`)

Pure, frozen, versioned, journaled on every row. Three inputs that all existed and had never been
combined: **maturity** (what this transition earned), **belief** (how unsure, per axis), **reach**
(can the executor operate this page at all).

| Checked in order | Mode |
|---|---|
| the executor cannot operate the page | **RED** |
| `novelty` blocks — we have never been anywhere like this | **RED** |
| maturity is UNSEEN | **ORANGE** |
| belief blocks on state / element / answer | **ORANGE** |
| maturity below CERTIFIED | **YELLOW** |
| otherwise (capped at YELLOW if reach was never probed) | **GREEN** |

**Not every gap blocks** (`reach.BLOCKING_GAP_PREFIXES`), and this line is the ORANGE/RED split
itself. A missing `apply_fields` entry is a **knowledge** gap: the page is fine, we lack one
addressing entry, and a bounded teacher instruction can route around it (a tier-1 click needs no
table). An undriveable widget, an unaddressable page, or an unknown verb is a **capability** gap:
no amount of teacher meaning makes the executor able to work it. Caught by the end-to-end smoke
run — every unit test agreed with every other because they all encoded the same wrong assumption;
only composing the real parts disagreed.

**Reach outranks belief**, and that ordering is the operator's caveat encoded as a branch:

> "the observer is great until we can't do anything about it"

Knowing exactly where you are buys nothing if you cannot touch the page. It is also what separates
**teacher instruction** from **teacher control**: if the tools reach the page, the teacher supplies
the missing *meaning* and the **local actuator still performs and verifies the action** — so the
step is journaled like every other step (§8). Only when the tools do not reach does the teacher
drive, and then the `gaps` list is **the spec for an endpoint**, never a licence to free-hand.

## 3. Maturity — a view, never a corpus (`controller/maturity.py`)

`derive(rows, programs)` is a pure function over the journal. Nothing is stored; delete the cache
and it recomputes identically. That is the 2026-07-16 reckoning applied one level up — a registry
that *can* disagree with the corpus eventually does.

`UNSEEN → DEMONSTRATED → REPLAYABLE → TESTING → CERTIFIED`, plus `REGRESSED` as an overlay.
Certification requires **the supervisor's verdict**, not `verified`: the 2026-07-19 treadmill
scored 8/8 verified while the page never moved.

Three things running it over the **real 45-row corpus** immediately fixed:

1. **The key excludes `task`.** One twelve-success `indeed_apply_questions / click / Continue`
   history was split in two because some rows say `task="indeed"` and others
   `task="indeed_quick_apply"`. A free-text label drifting across sessions must not reset a track
   record.
2. **The key excludes the destination.** Indeed skips prefilled steps — the recipe's `expect` is a
   *list* — so keying on the landing shatters one transition into three thin ones and nothing ever
   accumulates enough to certify. Landings are recorded on the stat, as reporting.
3. **Target-parameterised states are capped below autonomy**, reusing
   `programs.NON_COMPILABLE_STATES`. Otherwise the corpus grows one "transition" per job title.
   The cap also lands the right policy on entering an ATS: that click stays reviewed.

**The honest day-one map** (`GET /api/controller/coverage`): 18 transitions — 3 replayable,
5 demonstrated, 4 regressed, 6 unseen, **0 certified**, one platform. Gating immediately therefore
means nearly every Workday turn is a teacher turn until reps fill the registry. That is intended,
and it is only safe because the seat landed in the same slice.

## 4. Prediction before escalation (`controller/decide.py`)

`_escalate` became `_handup`, and it never emits a blank. It guesses from **form shape only**
(`kind` values taken from the scanner's own JS), proposes the advance control **as the page renders
it**, and carries a typed `escalation_axis` from a closed vocabulary — `no_program` on a page we
can name is a compile job; `unknown_state` is a labelling job; `low_confidence` is a teaching job.

Two rails on the guess, both tested: it is **always below the acting floor** (a prediction is a
thing to be scored, never a bid to act), and it **never invents an answer value** — that axis
belongs to `resolve_answer` and past it to the human. Stop-states hand up *empty*, deliberately: on
a sign-in wall a "best guess at the next action" is a suggestion to do something the agent must
never do.

## 5. The seat (`controller/inbox.py`, `controller/authority_seam.py`)

An append-only JSONL queue beside the journals — survives a process restart, no infrastructure,
readable on a tethered connection, request and response overlaid by id exactly like
`runtime/handoff.py`'s resolve markers.

- `GET /api/controller/teacher/pending` — the **full escalation package**: the frozen prompts, the
  local prediction, the reach gaps, and for a takeover its stop conditions. Answerable without
  reading the drive's memory.
- `POST /api/controller/teacher/{id}/respond` — approve | correct | instruct | escalate |
  takeover_done | abort. An instruction or correction **must** carry real reasoning (§10).
- `inbox_reviewer()` serves YELLOW over the identical transport — added here rather than by editing
  `controller/teach.py`, which is claimed by the operator's in-flight work.

**Two properties are non-negotiable and both are tested.** A park that expires lands *exactly*
where the drive landed before the inbox existed (`emit_escalation` + stop) — nobody listening must
never be worse than nobody asking. And a timeout **escalates, never approves**: failing open would
invert the gate this whole plan installs.

## 6. The loop (`controller/loop.py`)

GREEN acts · YELLOW proposes · ORANGE asks for one bounded action **and executes it locally** ·
RED hands the wheel over with explicit stop conditions and takes it back at the next checkpoint.

The load-bearing line is not "there are four modes" — it is **"re-evaluate local control after
every teacher action"**, and it has a test named after it. A takeover that runs to the end of the
application is the teacher replacing the driver; a takeover that returns the wheel at the next
checkpoint is a construction detour. `MAX_TAKEOVERS` (3) stops a drive that is being *done for*
rather than taught.

YELLOW is selected by what the **transition** earned, with the old `PROPOSE_RUNGS` kept as a floor
so nothing gets *less* review than before. That fixes something the rung-keyed gate could not
express: a compiled rung-0 program replaying a step nobody ever verified is exactly as unproven as
a model guess, and the old gate waved it straight through.

**The rails do not move with mode.** Submit is held, `human_required` is undriveable, BLOCKED hands
over, a challenge is never auto-solved — not even on a teacher instruction. Mode decides who
*chooses*, never what is *allowed*.

## 7. The scoped lesson (`packages/interaction/interaction/lesson.py`)

`kind` × `scope` (`universal` | `platform:x` | `tenant:y`), accepted **only after its prediction
verifies**, looked up most-specific-first so a new tenant inherits everything already known about
its platform. The cache key deliberately **excludes the scope**: "what is this Workday sponsorship
field?" is the same question at every tenant, and keying by scope would re-buy the identical
lesson ten times.

New kind: **`capability_gap`** — the one that turns *"the observer is great until we can't do
anything about it"* from a complaint into a work item whose resolution is code.

## 8. Route identification — the deep end (`controller/orientation.py`)

The one outer-loop step in scope. Two witnesses that are genuinely complementary here:

- **the URL** (`ats_registry.classify_ats`) is near-perfect on a known host and says
  `company_site` for everything else — including **branded wrappers**;
- **the pixels** were measured at **93–94% at PLATFORM level**, which is exactly the facet needed.

So the interesting signal is the *disagreement*: host says `company_site` while the screen looks
like Workday is a branded-wrapper tell (the KKR case, generalised). The URL still **leads** when it
recognises the host — a host match is a fact, and 93% is not enough to overrule a fact; the pixels
lead only where the URL has nothing, which is what they were brought in for. This is the first
place the visual witness is load-bearing rather than decorative.

## 9. What is owed

1. **The operator-present live drives** (PRINCIPLES §11 — controller leads, teacher rides):
   one Indeed drive exercising GREEN/YELLOW and confirming the registry promotes; one Workday drive
   from an Indeed `applystart` exercising §8's orientation, ORANGE (a tenant questionnaire) and RED
   (an unfamiliar widget), with the local Claude agent servicing
   `/api/controller/teacher/pending`. Capture + label every state — that IS the work.
   Success measure: **authority changes hands several times within one application.**
2. **The cockpit surface** — the coverage map and the pending-questions pane.
3. **Baseline `teacher calls per submitted application`** — the number this plan exists to bend.

## 10. Falsifying conditions

- **The mode mix never shifts across drives** → the ladder is not climbing. Suspect the
  certification requirements (supervisor-clean tail, clean reviewed run) before the thresholds.
- **ORANGE share stays flat while lessons accumulate** → lessons are not being reused; suspect the
  `scope` or the cache key, not the models.
- **RED never fires** → the reach probe is too permissive and we are attempting pages we cannot
  operate, which shows up as `CONTROL_NOT_FOUND` in the supervisor instead.
- **RED fires constantly** → too strict, and the teacher is being called for pages the executor
  could have worked.
- **`park_expired` dominates** → this is an operator-availability problem, not a capability one,
  and the status is separate from `escalated` precisely so the two never look alike.
