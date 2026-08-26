# Operating principles & invariants

The mental models this system is built to embody. They live here (not just in someone's head or a
chat) so we apply them **systematically** and don't re-derive or forget them. When a principle is
enforced by code, the enforcement point is named — a principle backed by an invariant beats a
principle backed by discipline.

---

## 1. State is context-bound — provenance travels with the data
A value in the blackboard (or anywhere) is **not self-validating**. It only means something relative
to the context that produced it: *which cadence run, whether the session was authenticated when it was
gathered, and when.* The same session is **not** the same cadence run, which is **not** an authorized
context. Data gathered out of context (e.g. a shortlist extracted while logged-out, or from a prior
run) can **look valid but isn't** — acting on it is a "thought-bubble" error.

- **Enforced by:** `SearchState` provenance fields (`cadence_run_id`, `run_started_at`,
  `gathered_authenticated`, `stale`), `start_cadence_run`, and `search_data_actionable` in
  `apps/controlplane-api/apply_state_store.py`. Triage/approve are valid **only** within a current,
  authenticated run; logging in does **not** retroactively bless logged-out data (it goes `stale`).

- **Live UI/session state goes stale too — refresh before you operate.** Beyond blackboard data, the
  browser itself decays with time: CDP `backend_node_id`s churn, forms/CSRF tokens and ATS sessions
  time out, "Did you apply?" prompts appear, and a tab you left minutes ago may have navigated away
  from where you left it. **Before driving a tab — or pressing anything that scans it (the Account
  Manager's Create-Account / ▶ Login button, which AX-scans the live form) — reload the tab and
  re-verify the expected state.** Never assume the tab is where you left it. This is cheap insurance
  against the "button did nothing / it clicked the wrong element / it closed the wrong tab" surprises
  (all seen live 2026-07-12). Treat stale live state as a first-class hazard, like stale data. This
  freshness gate binds **every** reasoner — the trained student (L3/L4/planner) included, not just the
  teacher; when the student's rung leads, it re-verifies the drive exactly as Claude does, and the
  check is part of the startup/pre-flight ritual, not only a per-action step (operator-directed
  2026-07-18).

## 2. Authenticate before you automate
The agent must not run searches or task automation on a logged-out session. Login first, automate
second.
- **Enforced by:** the login gate — `auth_required` blocker + `proceed_decision` (`apply_state_store.py`),
  fed by the `/auth_state` probe each `session_state` cycle.

## 3. Reach states by clicking, like a human — URL-forcing is last-ditch
Default navigation is **clicking the on-page link/button**. Typing/forcing a URL is a fallback only,
because URL-jumping is flag-raising (bot-detection) behavior on a real account. Generalizes the older
"never URL-jump to job-detail pages / don't churn tabs" rule to **all** navigation.
- **Status:** policy now; to be baked into the driver/planner action selection (today `/navigate` is
  URL-based — use it only when there's no clickable path).

## 4. Capture per *state*, not per keystroke — and never capture secrets
The unit of training capture is a meaningful page **state** (the thing the L3 classifier / state-graph
learn from), not each button press. In credential flows we record **state identity only** (url path /
title / auth signals) and **never** screenshot or store passwords, codes, or field values.

## 5. Heuristics must be validated against real pages
Deterministic detectors (captcha active/passive, auth logged-in/out) are cheap but easy to get subtly
wrong; validate them against the actual live pages before trusting them. Burned twice already: a
reCAPTCHA `bframe` *present* ≠ *shown* (visibility probe), and `secure.indeed.com` host ≠ "in the auth
flow" (path-based check, since it also serves logged-in `/settings`).

## 6. Drive through the AX/node interaction layer — not bespoke DOM workarounds
There is one resilient way to act on a page: read the **accessibility tree**, find the element by
**role / accessible-name**, and drive it **by `backend_node_id`** (`DOM.resolveNode` →
`callFunctionOn`). It survives DOM reshuffles (`<button>`→`<div role=button>`, class churn, layout
shifts). A hardcoded-`querySelector` + coordinate-click fast-path feels simpler for a small known
form and is exactly why Facebook login has broken three times in a week — each FB DOM change is a
reactive patch to one brittle endpoint. When a flow breaks, **first ask which layer it's on**; don't
diagnose fields or build a one-off CDP script until you've confirmed it's even using the robust path.
Domain quirks (button→div, React-controlled inputs, the human gates) belong in the distilled recipe
(`facebook_recipe.py`) via the teacher→distill loop, **not** re-litigated in an imperative endpoint
the next session can't see.
- **Enforced by:** `apps/mcp/app/executor/driver.py` (Layer A) as the one drive path; FB login was
  routed through it on 2026-07-08 (commit `6775499`) — the bespoke `/facebook_login` endpoint and
  `channel_browser.py`'s `login_path` are **deleted**, and `facebook_recipe.match_login_fields` holds
  the domain quirks. See `docs/interaction-layers.md` and `docs/LEARNINGS.md`.

## 7. No single golden path — the golden path is the least-steps route through the UI in front of you
Accounts (and site variants) render **different UIs**, so a goal like "open Marketplace" has
different routes per account. Don't assume one fixed recipe spine works everywhere. Observe the
actual UI (CDP-AX `/ax_scan`), then take the **shortest available verified path** to the goal.
Recipes hold alternative routes; the state-transition graph accumulates them across accounts, and the
planner graph-searches it for the fewest-steps path. "Golden path" ≡ fewest steps for *this* UI —
verify each edge, never assume URL implies state.
- **Status:** policy now (discovered driving FB Marketplace across accounts); recipes/planner to hold
  per-variant routes + shortest-path selection over the observed state graph. See
  memory `project_per_account_ui_paths`.

---

*Add a principle here whenever a mental model proves load-bearing. Prefer encoding it as an invariant
in code and linking the enforcement point.*

## §8 — Execution = API. Discovery is a probe; its output is an endpoint.

The model decides WHAT; the API owns HOW. Any interaction whose protocol we have proven is an API
call, not a hand-rolled `Runtime.evaluate`.

**Why it's a principle and not a preference:** an inline script is invisible to the flywheel. **An
action the system can't see is one it can never learn** — which is the whole premise of
teacher→distill. Correctness follows the same way: every bug on 2026-07-15 lived in a hand-rolled
script (substring match, `.value` verify, stray-option open-check), never in an endpoint, because an
endpoint encodes the protocol once and the caller can't get it wrong.

> **Corrected 2026-07-16 — this section used to say "Every API action is recorded, replayable and
> trainable," citing `type:137 … eval:0` in the event log. The counts are real; the claim was false,
> and it was the claim Phase 1 was about to be built on.** `event_log.jsonl` is a 1000-line **ring
> buffer** raced by two processes, carrying no fingerprint, no session and no outcome (it's a
> `detail` substring), read by exactly one consumer — a React console polling every 5s. **No trainer
> reads it.** Meanwhile the real corpora (`loop_steps.jsonl`, `selection_telemetry.jsonl`) are written
> only by `runtime/loop.py`, which the live drives never go through: the session that drove a Workday
> application to submission added **zero rows to either**. So the scoreboard was never `eval:0` vs
> `type:137` — **both were zero**, and `/widget_select` was only marginally more visible than `/eval`.
> Being *an API call* was never what made an action learnable. Being *journaled* is.
> See `docs/LEARNINGS.md` 2026-07-16.

- **Enforced by:** `packages/interaction/interaction/journal.py` — the append-only, fingerprint-joined
  intent corpus, and `apps/mcp/app/intent_api.py::journaled`, a route decorator (not a helper) so no
  endpoint can forget and no early return can skip it. The response is *derived* from the journaled
  record, so the corpus and the HTTP response cannot disagree. `ok` means `outcome == OK`, which an
  endpoint cannot override — the anti-silent-success contract made mechanical rather than remembered.
  The event log remains the operator's wall display: a good one, and a bad corpus.

**Promotion rule — promote the MECHANISM eagerly, generalize the ABSTRACTION late, never freeze.**
Not "when it's perfected": `/select_prompt` was promoted from ONE observation and its imperfection
cost a single parameter, fixed once, forever — while a "perfect" inline regex was wrong on first
contact with a real option list and had nowhere for the fix to live. An API's job is not to be right;
it's to be **the single place the fix lands**. Generality is earned at the SECOND site (the popup
protocol went Indeed → Workday's `aria-controls` scoping → Greenhouse's keystroke-open).

**Discovery stays inline forever** — it meets novel contracts by definition. But its output is always
a new/extended endpoint + a recipe entry + captured, labelled states. Discovery that ends in a working
script and nothing else is a session paid for twice.

Enforcement: `docs/PLAN_execution_api.md` (protocol inventory, backlog, best practices). Corollary of
§6 (drive the AX/node layer) and of [[project_widget_protocol_layer]]: AX finds elements, the API
models widgets.

## §9 — The student is the central cog; the teacher bootstraps it; Haiku is a backstop, not a student

> **Amended 2026-07-22 (operator-directed) — the student is a PERCEPTION-and-policy-on-rails cog,
> not an eventual reasoner and not an eventual teacher.** The sentences below that promise the
> student "becomes its own teacher" and that Claude is "scaffolding, never the destination" are
> **retired**, by measurement rather than by taste. On this machine a local model that *reasons* is
> not on the table (Gemma 4 E2B: 7.2 GB resident, 50 s to emit one word, swapfile to 14.3 GB;
> llama3.2:1b: fits, 0/4, invents application answers — LEARNINGS 2026-07-20 (5)). And it turned out
> not to be needed for the part that matters: **getting unstuck is already deterministic** — rung-0
> supervision names the failure from the 10-class taxonomy at $0 and `RecoveryPlay` prescribes the
> play, with no model in the loop at all.
>
> So the roles re-anchor: **Claude is the novel reasoner, permanently and by design** — the teacher
> rung is not scaffolding awaiting removal, it is a load-bearing part of the finished machine. The
> student's job is to **perceive accurately, act on rails, verify honestly, and know precisely when
> it does not know** — witnesses, prototypes, the intent policy, the deterministic plays. Everything
> below still holds about *ordering* and about Haiku never occupying the student's seat; what
> changes is the student's ceiling and, with it, what "does this grow the student?" means: it means
> *does this sharpen perception or widen the rails*, not *does this teach it to think*.
> Build plan: `PLAN_perception_v1.md`.

The decision cascade has FOUR sources, and they are **not** interchangeable "models." Naming the
middle rung "Model (Haiku)" (as `PLAN_controller_v1` §2 and `PLAN_reasoner_v2` §5 originally did) was
a drift: it let a cheap API backstop squat in the **student's** seat and read as *the reasoner*. It is
not. Every design choice should ask *"does this grow the student?"* — because the student is the whole
point of the machine.

- **The student is the most important cog** — the local trained models (`L3` perception, `L4` intent
  policy; in v2 the **planner** and the **critic**). This is the central model we are building and
  bringing to the forefront. It leads the routine work as scenarios graduate, and it becomes *its own
  teacher*: the critic learns the teacher's critique function and takes over the routine critiquing
  (`PLAN_reasoner_v2` Loop 4). Bringing the student to the front is the goal, not a side effect.
- **The teacher (Claude — Opus/Fable/Mythos-tier) bootstraps the student**: demonstrates, corrects,
  and critiques — on the states the student actually reaches (DAgger). It steps aside from the
  *routine* as the student graduates, and stays for *novel terrain* indefinitely (the ladder's teacher
  rung never closes). The teacher is scaffolding for the student, never the destination.
- **Haiku is the cheap deployed BACKSTOP — not a student.** We know what Haiku can do and we do **not**
  train it further. In the live product (where we deliberately don't pay for Opus-tier on
  straightforward calls) it is the cheap fallback for when there is no program and no confident
  student. During teaching it **shadows** — a free "does the cheap model already agree with the
  teacher?" baseline — it does not act as the brain, and it never occupies the student's seat as a
  training target.
- **The human owns the irreversible** — Submit, credentials, stop-states. Never closes.

**Ordering (cheapest-confident-first):** program/cache ($0) → **student** (leads) → **Haiku backstop**
(catches what the student ducks) → **teacher** (catches what Haiku ducks, and teaches) → human. A
trained student does **not replace** Haiku and throw the net away — it sits **above** it. Invariant #6
(the model behind an HTTP endpoint) is how the student drops into its seat: a deployment swap that adds
the student *above* the backstop, not a rename of the backstop.

- **Status:** documented here; plans amended (`PLAN_controller_v1` §2/§8, `PLAN_reasoner_v2` §5). The
  shadow-agreement metric already measures the backstop (Haiku) against the teacher. Code follow-up
  (a v1 completion item): the cascade's `rung` vocabulary distinguishes `student` from `backstop`
  rather than collapsing both into one `model` rung, and `run_live_apply` defaults to
  teacher-demonstrates / Haiku-shadows (not Haiku-proposes).

## §10 — The Open Brain: the teacher reasons ON THE RECORD, and both sides of a correction are kept

Every decision the teacher makes must carry its real reasoning — the *why*, plus the Bundle facts it
cites — as structured, journaled, trainable context; and when the teacher overrides the backstop, the
corpus keeps **both** whys. Reasoning that lives only in the teacher's context window (or in chat) is
the event-log mistake again (§8): vivid in the moment, invisible to the students, paid for once and
lost. §9 says the teacher bootstraps the student; **§10 is HOW the reasoning transfers** — an
always-on pipe from the teacher's head into the one corpus every student reads. This is one of the
system's load-bearing ideas (operator-directed 2026-07-18), not a nicety.

**Why it's a principle and not a preference:**
- **A policy cloned from actions alone copies moves, not rules.** Given only `(state → intent)`, the
  student imitates what the teacher did on the states the teacher saw and has nothing to generalise.
  Given `(state → intent + rationale + evidence)`, it learns the *rule* and the rule is checkable
  against its cited receipts. Chain-of-thought with citations is the trainable object (the same
  result `PLAN_reasoner_v2` §3 Loop 5 reaches at plan altitude — bring it down to `decide()`).
- **The contrast on a correction is the densest signal we have.** "The backstop said `set_text`
  because the field looked like text; the teacher said `select_option` because it's a dropdown
  `set_text` never commits" teaches the exact boundary. The code used to journal the teacher's move
  and the backstop's move but **drop the backstop's why** — discarding the half that makes a golden
  row a *lesson* instead of a *label*.
- **A "history of good habits" only exists if habits are recorded AS they form.** The teacher (Claude)
  is the source; if the reasoning isn't captured on the step, no later session — and no student — can
  ever recover it.

**Enforced by:**
- **The contract carries reasoning as data, not prose-in-passing.** `Decision.rationale` +
  `Decision.evidence` (the cited Bundle keys — the receipts, mirroring `PlanStep.evidence`);
  `DecisionRecord.evidence` / `proposed_rationale` / `proposed_evidence` keep both sides. `record_for`
  copies them at the **single** choke point, so no seam can forget
  (`packages/interaction/interaction/decision.py`, `decision_journal.py`).
- **The teaching seams SOLICIT the why.** `cli_reviewer` used to hardcode `rationale="operator
  correction"` — every human correction taught reasoning-blind. It now asks ("why? — this is the
  training signal") and journals what's given verbatim; the model rung's structured-output schema
  requests `evidence` so the backstop cites too (`controller/teach.py`, `controller/reason.py`).
- **A placeholder cannot masquerade as reasoning.** `is_real_rationale` / `PLACEHOLDER_RATIONALES`
  define what counts; `summarize()` reports `reasoned_rate` + `unreasoned_teach_count` over the
  teaching rows (teacher-rung + golden). **Falsifier:** if `reasoned_rate` on a scenario stays below
  1.0 while corrections accumulate, reasoning is being paid for and dropped — fix the seam, don't
  shrug. Promotion (`PLAN_controller_v1` §6) gates on it beside shadow-agreement.

- **Status:** contract + capture + metric + the `cli_reviewer` fix landed 2026-07-18 (tests green).
  Owed: the reasoning-feed cockpit surface (watch the teacher's *why* stream into the corpus — the
  literal open brain), and the first teacher-supervised live drive (Workday) that fills these columns
  with real reasoning. Corollary of §8 one altitude up (journaled *reasoning*, not just journaled
  action), and the operational core of §9.

## §11 — A "teacher-driven drive" means the SYSTEM drives and the teacher runs alongside

**Redefined 2026-07-20 (operator-directed).** The term had drifted: sessions where Claude drove the
browser in front — sometimes free-handing scripts around the Interaction API (the §8 violation, and
the operator's standing gripe) — were being called "teacher drives." That mode is **bootstrap**, not
teaching, and it is no longer what the words mean. From here on a teacher-driven drive is exactly
this shape, and only this shape:

- **The system leads.** `run_controller` (or the cadence above it) drives: rung-0 programs and the
  rung-1 backstop act; verified steps just run. The teacher is not in front.
- **The teacher rides alongside, on call.** The teacher is the **local Claude agent** (Claude
  Code / the Claude app on the operator's machine) — not Haiku, and not an API rung. It watches the
  drive, keeps its own notes, and is *invoked* at pauses: an escalation, a low-confidence
  `Decision`, a propose-approve gate, a supervisor verdict worth auditing. Between pauses it waits.
- **The teacher acts only through the system.** Corrections, further escalation, teaching, and
  labeling go through the Interaction API, the `Reviewer` seam (`controller/teach.py` /
  `teach_session.py`), and the label/candidate endpoints — same contract, same journal as every
  other rung (no private path, `DECISION_two-stacks-one-spine.md` §2.2). Discovery stays
  `/probe`-journaled per §8. A teacher that free-hands a script around an existing endpoint is off
  the record — and off this definition.
- **Every intervention is corpus.** Approve/correct → golden rows carrying both rationales (§10);
  named states → candidate promotions; labels → the queue. Teaching IS data collection; the student
  is pushed in exactly the direction of what the teacher does at the pauses.

**Why this is the definition and not a preference:** DAgger (`PLAN_controller_v1` §4) — corrections
must land on the states the *student* actually reaches, which requires the student in front; and §8
— an action the system can't see is one it can never learn, so a teacher who drives in front
produces demonstrations at best and invisible work at worst.

- **Enforced by:** the `Reviewer` seam + golden rows (`controller/teach.py`), teacher-rung rows
  carrying rationale + evidence (§10), `journaled` endpoints as the only action surface (§8).
- **Status:** the machinery exists (propose-approve M4, `teach_session`, `handoff.emit_escalation`).
  Owed: **a reviewer transport the local Claude agent can service** (today's `cli_reviewer` assumes
  a human at a TTY; the `Reviewer` seam is injectable, so an HTTP/file review inbox is a thin
  adapter — pending-review queue, poll, respond); the **default mode flip** (`run_live_apply`
  defaults to teacher-demonstrates — under this definition the default is controller-leads /
  teacher-reviews); an escalation that **parks and waits** for the teacher instead of only halting;
  and `/capture` at pause/correction moments so teaching feeds L3, not just L4.

## §12 — Local owns the mission; the teacher owns the uncertainty; control returns after every teacher action

**Operator-directed 2026-07-22.** §11 defined what a teacher-driven drive *is*. This defines how
authority **moves during one**, because §11 was unimplementable without it: an escalation ended the
drive, so there was no "during".

Authority is decided **per turn, per transition** — never per site, never globally. An ATS is never
uniformly built: a Workday application routinely contains ten proven sections and one questionnaire
nobody has ever seen, and grading the whole vendor forces a choice between driving the unknown part
blind and hand-holding the known nine. Four modes, from three inputs that all already existed and
had never been combined (`maturity` × `belief` × `reach`):

- **GREEN** — inner reasoner decides, local executes. Certified transition, witnesses content,
  tools reach the page. Runs unwatched.
- **YELLOW** — local proposes, teacher approves or corrects, local executes.
- **ORANGE** — teacher supplies **one bounded semantic action**; the **local executor performs and
  verifies it**. The teacher contributes *meaning*, not keystrokes.
- **RED** — teacher drives, bounded, **through the journaled endpoints and `/probe`**. If the API
  genuinely cannot work the page, the ticket closes as a **capability gap** and the deliverable is
  an endpoint (§8) — never a bespoke script that worked once.

**Three rules that are the whole principle:**

1. **The teacher never acts before the inner layers make a prediction.** Every hand-up carries a
   real proposal at an honest confidence and a typed escalation axis, so one escalation answers
   four separate questions — did we name the state, pick the right field, pick the right verb, or
   only fail to ground it? Without this the student is never scored on the turns that matter, and
   the teacher's apparent omnipotence is an artifact of the corpus rather than a fact about the
   system.
2. **A teacher handoff has an explicit exit condition.** A ticket without one means "finish the
   application", which is the teacher replacing the driver rather than borrowing the wheel.
3. **Re-evaluate local control after every teacher action** — not at the end of the application.
   This is the line that keeps a takeover a *construction detour*.

**Reach outranks belief**, and the ordering matters: knowing exactly where you are buys nothing if
the executor cannot touch the page. It is also what separates ORANGE from RED — *teacher
instruction* from *teacher control*.

**Nothing about the rails moves with mode.** Submit is held for the operator, `human_required`
states are structurally undriveable, BLOCKED hands straight over, a challenge is never auto-solved
— not even on a teacher instruction. Mode decides who **chooses**, never what is **allowed**.

- **Enforced by:** `packages/interaction/interaction/authority.py` (the pure truth table, with
  "an UNSEEN transition can never be GREEN" as an exhaustive test), `controller/maturity.py` (the
  ladder, derived from the journal — a view, never a second corpus), `controller/reach.py`,
  `controller/inbox.py` + `/api/controller/teacher/*` (the seat §11 listed as owed), and the four
  modes in `controller/loop.py`. Plan: `docs/PLAN_progressive_autonomy.md`.
- **Falsifier:** if the mode mix never shifts across drives, the ladder is not climbing — suspect
  the certification requirements before the thresholds. If `park_expired` dominates, that is an
  operator-availability problem, not a capability one, which is why it is a separate status.

---

## §13 — In a novel domain, a hypothesis is not a finding until a prediction survives a test

**The failure this exists to stop.** On 2026-07-28 the same LinkedIn search box got THREE diagnoses
in one day: "a typeahead steals focus and beats the React write", "the accessible name is the
placeholder and changes on focus", and finally the honest "we do not know yet". The first two were
written into the recipe and the log **as measured facts**. Neither survived the next measurement.

The measurements themselves were real every time. What was invented was the MECHANISM around them:
one observation, an inferred cause, and the cause recorded as though it too had been observed. A
plausible story that explains the symptom is the most dangerous artifact in a novel domain, because
it *feels* like knowledge and it stops the search.

**The rule.** In a domain with no skeleton — no recipe, no captures, nothing driven before — every
claim carries its evidence class, and only one class is allowed into the recipe:

* **MEASURED** — read back from the live surface. A value probed after a write; a rect; a URL; a
  screenshot. Quote the reading.
* **HYPOTHESIS** — anything that explains a measurement. Labelled as such, never in a docstring
  that reads like fact, and it must state **what would falsify it** before anything is built on it.
* **UNVERIFIED** — a scaffold copied from a sibling domain to be replaced by the first real drive.

**The loop, and the step that keeps getting skipped.** Plan → **predict** → act → **read back** →
record what worked, what did not, and *why* → improve. The skipped step is PREDICT: say, in advance,
what you expect to see if the hypothesis is right AND what you would see if it is wrong. A test that
cannot come back negative is not a test, and three of the four wrong turns above would have been
caught by one sentence written before the action.

**Change one thing at a time.** Two of the retractions were unfalsifiable because a role, a name and
a driver path had all moved between attempts, so nothing could be attributed. In a novel domain the
diff between attempts is the only instrument there is.

**A negative result is a result, and it is cheap.** "The click focuses but the type does not fill,
against both nodes" is worth more than a fix that might work, because it narrows. Record what did
NOT work and why — the next session pays for it again otherwise.

- **Enforced by:** the `MEASURED` / `UNVERIFIED` / `THE OPEN BLOCKER` headings in
  `linkedin_recipe.py`, `google_recipe.py`'s policy comments citing the drive that produced each
  one, and `SEARCH_SUBMIT_READY`-style capability flags that stay False until a drive has read the
  result back. `docs/LEARNINGS.md` keeps the retractions alongside the claims — both sides of a
  correction, the same rule §10 applies to the teacher's reasoning.
- **Falsifier:** if a recipe's comments cannot be traced to a drive that produced them, or a
  capability flag is True with no read-back behind it, this principle is not being followed.

## §14 — A world-fact rots; a code-fact does not. They must not be stored the same way

**The failure this exists to stop.** `linkedin_recipe.RESULTS_TRAVERSAL` held both of these in one
dict: `"scroll_endpoint": "/scroll_job_list"` and `"virtualised": True`. The first is a **code-fact**
— true until we change it, and if we change it the suite goes red. The second is a **world-fact** —
measured live on 2026-07-30, and falsified by LinkedIn on 2026-08-26 without a line of this repo
changing. Same file, same review, same tests, and **only one of them can become false while nobody
is looking.** On the same day, `spec()["blocked_on"]` was still claiming a sweep "starts with
`/set_distance`… and would stop a LinkedIn sweep before it reached the list" — twelve days after
that gate moved behind `has_distance_filter`. A session read it, believed it, and planned around it.

**And the suite was defending the rot.** `test_the_traversal_separates_what_was_driven_live_from_what
_was_not` asserted `"not been PRESSED" in still_unverified` — the *prose of a perishable claim*. So
the green suite's job had quietly become **keeping a false statement alive**. A test that pins the
wording of a world-fact converts staleness from a risk into an invariant.

**The rule.** Every recorded claim about the outside world carries, at minimum: **what was observed,
when, on which drive, and how to re-check it.** A world-fact with no date is a rumour. Tests assert
the **shape** of such a claim — that it is dated, attributed and separated from what was *not*
driven — and **never its content**. `blocked_on`, `still_unverified` and `verified_live` are claims
with expiry dates, not documentation.

**The corollary that keeps biting.** A world-fact contradicted by a later drive is **retracted in
place, with both sides kept** (§10, §13) — never silently overwritten, and never silently left. The
2026-08-14 radius retraction in `linkedin_recipe` is the shape to copy: the wrong claim stays,
labelled, because a stage that silently disappears is indistinguishable from one nobody got to.

- **Enforced by:** dated `verified_live` / `verified_live_2` entries in `linkedin_recipe.py`;
  `test_the_traversal_separates_what_was_driven_live_from_what_was_not`, which now pins the
  separation (each drive carries its own date and numbers) instead of one drive's wording; the
  `MEASURED` / `HYPOTHESIS` / `UNVERIFIED` classes of §13; `docs/LEARNINGS.md` for the reasoning.
- **Falsifier:** a claim about an external surface that carries no date or drive; or a test that
  asserts the prose of one. If a `blocked_on` can outlive its bug in a green suite, this principle
  is not being followed.

## §15 — One authority per identity question, and everybody asks it

**The failure this exists to stop.** On 2026-08-25 four layers each had their own notion of what
identifies a control, and every "the control is right there and the layer said no" moment came from
a different one: `apply_prompt_select` keyed on a prompt's `field_name`, `check_group` on its own
label derivation, `/execute` on nearest-label-by-proximity, and a value write on `.value`. Seven
attempts, four notions of identity, one control. On 2026-08-26 the same shape in the corpus:
`job_id` is `f"{platform}:{external_id}"`, and two of three write paths **asserted** the platform
while the extractor had already **observed** it off the live tab's host — so a wrong guess would not
mislabel a row, it would mint a different row that can never dedupe against the real one.

**The rule.** For each kind of thing, exactly **one** authority answers *"which one is this?"*, and
every other component asks it rather than deriving its own answer. New code cites which authority it
used. When two answers disagree, the caller **refuses** — it does not pick, and it does not silently
correct, because a silent correction hides the real fault (a call aimed at the wrong thing).

| Question | The authority | Never |
|---|---|---|
| Which element is this? | CDP-AX: role + accessible name → `backend_node_id` (§6) | a `querySelector`, a class, a proximity guess |
| Which job is the pane showing? | the pane's own id (`currentJobId` / `JobDetails_*_<id>`), via `pane_shows` | a text diff against the previous read |
| Which result set is on screen? | the engine's own URL — query terms + filters (`result_set_identity`) | `/results_signature`, which answers "different cards?" |
| Which search surfaced this job? | the `SearchSighting` join table | `ObservedJob.search_queries`, which is a display field |
| Which platform is this row? | the extractor's observed host | the caller's `platform=` argument |
| Did the widget accept it? | the widget's own commit signal (`describe_widget` / the hidden node) | that the write returned `ok` |

- **Enforced by:** `observed_jobs.check_provenance` (refuses a platform the page disagrees with),
  `pane_shows`, `search_cadence.result_set_identity`, the AX interaction layer (§6).
- **Falsifier:** any new code that answers one of these questions itself instead of asking the
  authority — or that resolves a disagreement by choosing, rather than by refusing.

## §16 — Store the observation; derive the conclusion. A conclusion without its evidence cannot be corrected

**The failure this exists to stop.** `ObservedJob.search_queries` (a JSON list of "queries that found
this job") and `SearchSighting` (a row per search × job) are **the same fact stored twice** — once as
a conclusion anybody could assert, once as an observation with a search behind it. On 2026-08-26 the
corpus held 14 rows claiming they were found by searching *"data analyst"* when the only thing that
ever surfaced them was Indeed's suggestion feed, and **20 more rows that can never be judged at all**:
they carry a query, they were also surfaced by real searches, and the write that added the query
created no link. The evidence needed to adjudicate them was never written, so no cleverness recovers
it. **That pile only grows.** The 14 were repairable *because* the join table could adjudicate them;
the 20 are permanent, and they are the whole argument for this principle.

**The rule.** Where a fact can be **derived from recorded observations, derive it** — do not also
store it as a claim. Where a derived value must be stored (for display or speed), it carries a
pointer to the observation that justifies it, and a write that cannot produce that pointer is
**refused at the door, not audited afterwards**. An audit run later can only sort damage into
*provable* and *unknowable*; the unknowable pile never shrinks.

**And name the three "no"s.** "I looked and there is none", "I did not look", and "I cannot tell" are
different facts and must not encode alike. This was violated twice on 2026-08-26 within hours —
`page_meta.has_next` carries a note about exactly this, and `Search.filters` was written to collapse
`{}` and "never recorded" anyway. The consequence was concrete: a search whose result set
demonstrably changed under it would have rendered as *confirmed unfiltered*.

- **Enforced by:** `observed_jobs.check_provenance` at the single shared write door; the
  `filters` / `filters_recorded` tri-state in `searches.summarize`; `GET
  /api/career_search/provenance` (audit) and `POST /api/career_search/provenance/repair` (dry by
  default), which repair only the class the join table can prove and **count** the rest.
- **Falsifier:** a stored claim with no path back to an observation; a repair that guesses; or an
  audit that reports a number without saying which part of it is unknowable.
