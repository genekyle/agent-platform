# Learnings — the running cross-session log

**If you are a new session, read this first.** This is the append-only log of things we *discovered*
the hard way — mistaken assumptions we corrected, non-obvious facts about how the system actually
behaves, and where the durable fix landed. It exists because the same lessons kept getting re-derived
(and re-lost) session after session, buried in one-off endpoint patches and chat scrollback that the
next session can't see.

**How this relates to the other docs:**
- **`LEARNINGS.md`** (this file) — a *dated running log* of what we found out. Newest first. Some
  entries graduate into a principle or an invariant; when they do, they say so and link the code.
- **`PRINCIPLES.md`** — the *durable invariants* the system is built to embody, ideally each backed
  by an enforcement point in code.
- **`PROJECT_STATUS.md`** — the *current state* of the per-step loop and the open gaps.
- **`interaction-layers.md`** — the deep-dive on the AX/node driver vs. bespoke DOM (the FB-login saga).

**The ritual:** every session, when you learn something load-bearing — an assumption that was wrong,
a behavior that surprised you, a fix and where it went — **append an entry here**. Prefer encoding it
as code/recipe/invariant *and* logging the pointer here. A lesson that lives only in a 60-line endpoint
or a chat transcript is a lesson the next session will pay for again.

Entry format: `## YYYY-MM-DD — <title>`, then *what we believed*, *what's actually true*, and
*where it's encoded now* (link the code/recipe/doc, not just prose).

---

## 2026-07-23 — A worktree's controlplane-api tests import `interaction` from MAIN, not the worktree

**What we believed.** That running the suites inside a git worktree tests the worktree's code. It
is the whole reason `CLAUDE.md` recommends worktrees for concurrent sessions.

**What's actually true, and it is asymmetric.** `packages/interaction` is installed **editable**,
pointing at `/Users/geno/Projects/agent-platform/packages/interaction` — the MAIN checkout. So:

| run from | `import interaction` resolves to |
|---|---|
| `packages/interaction` (its own tests) | the **worktree** source — pytest puts rootdir first on `sys.path` |
| `apps/controlplane-api` (the big suite) | the **main repo**, via the editable `.pth` |

So a cross-package change edited in a worktree is exercised by that package's own tests and
**invisible to the controlplane-api suite until it is merged**. Concretely, on 2026-07-22 the
novelty→ORANGE change made `test_authority.py` fail-then-pass in the worktree while
`test_controller_evals.py::test_authority_truth_table_is_pinned` reported green — because it was
still asserting against main's *old* truth table. The instant the merge landed, the same test
failed. The suite was not flaky and the merge did not break it; the pre-merge green was measuring
the wrong code.

**Why it bit rather than being noticed:** the same change had a pinned truth table in **two**
places, one per package, and only the co-located one could see the edit.

**Where it's encoded now.** This entry, plus the corrected assertion in
`test_controller_evals.py`. The working rule: **after editing `packages/*` in a worktree, merge to
main before trusting a green `apps/controlplane-api` run** — or run that suite from the main
checkout. `make controller-evals` cannot save you here either; it resolves `../../.venv`, which a
worktree does not have.

---

## 2026-07-22 (4) — The first progressive-autonomy drive: we were capturing the wrong tab

**The run.** First live drive with authority + the teacher seat wired (`/api/controller/run`,
`progressive=true`), on a real Indeed apply. Everything the plan promised to record, recorded: the
first 4 `decision_journal` rows ever to carry `belief_*` / `control_mode` / `authority_axis`, the
first teacher inbox tickets with real reasoning on both sides, the first accepted scoped `Lesson`,
captures + AX sidecars every turn, and the Submit gate holding. And the drive was **unusable**:
`mode_mix {red: 2}`, `rung0_share 0.0`, GREEN/YELLOW/ORANGE never once.

**What we believed.** That `belief_novelty` saturating at 1.00 meant the perception witnesses were
mis-calibrated for live pages — a modelling problem, to be fixed by re-fitting or by widening the
live featurizer.

**What's actually true — `/capture` was capturing a different tab than the one being driven.**
Proven by asking for one tab and reading back another: requested `tab_id=7EE2CE…`
(`indeed.com/jobs?q=reporting+analyst…`), got an artifact whose `page_identity.url` was
`smartapply.indeed.com/…/review-module`. Four of the drive's captures recorded a **stale
post-apply tab** while carrying the state label of the page the drive was actually on.

Two individually-correct rules composed into a silent mislabel:

1. `LiveActuator._addr()` addresses by **`tab_id` only**, on purpose — *"tab_id only: stable across
   navigation. A stale tab_url would mis-address after a Continue click navigates."* Correct.
2. `app/main.py::_select_tab` states in its own docstring that *"list_pages exposes no CDP
   targetId, so a tab_id alone cannot address a page — the URL is the only handle we get"*, then
   compares the CDP target id against a **1-based list index**. It can never match, so it returned
   "not pinned" every time. Also correct, as far as it goes.

And the guard that exists precisely to stop this — `_verify_target_tab`, whose docstring says *"a
capture of the WRONG page is worse than no capture… FAIL LOUD"* — **had every branch keyed on
`expected_url`**. An id-only caller leaves that `None`, so the guard sailed past and the capture
fell through to whatever tab was frontmost. **The safety check was inert for exactly the caller it
most needed to protect.**

**Why this produced the novelty saturation, and why we chased the wrong thing first.** The DOM
witness read AX from the *addressed* tab while the visual witness scored a screenshot of a
*different* tab. They were never disagreeing about a page — they were looking at two pages. Hence
`agreement: split` with both novelty scores pinned at 1.00, hence RED on every turn
(`authority()` maps a novelty block straight to RED). A perception bug and a corpus-poisoning bug
wearing the same symptom.

**A real second cause, found on the way and fixed too.** `perception.live.sense()` synthesized an
artifact from AX candidates rather than featurizing the capture the same turn had just written.
That synthesis is strictly thinner than the `/capture` artifacts the witnesses are fitted on — no
`ph:`, no `title:`, no `flag:`, and it duplicates one 60-capped candidate list into both element
views, so the featurizer reads 60 controls twice instead of 120 distinct ones. Measured against
the promoted witness on real captures from this drive:

| featurized from | cosine | margin | novelty |
|---|---|---|---|
| the real `/capture` artifact | **0.79** | **0.33** | **0.72** |
| the synthesized lookalike | 0.21 | 0.03 | 0.97 |

The synthesis also predicted `workday_review` for an Indeed review page. `capture_now` already
opened that artifact to resolve the screenshot and then threw it away; it now returns it
(`CapturedTurn`) and `sense()` featurizes the real thing. **One featurizer over two artifact
shapes is the same drift the module was written to prevent, wearing a different hat.**

**A third, smaller, still worth knowing.** The promoted observer was fitted at `feature_set: v3`
while `dom_witness.FEATURE_SET_VERSION` had moved to `v4`. Harmless *today* only because the v4
`field:` namespace is empty for pre-2026-07-22 captures — but live captures now carry `form_state`,
so it would have started mattering on the next drive. Re-fitted (174 rows / 59 states).

**Two design holes the same run exposed, NOT yet fixed** (they need an operator decision):
- **A novelty-RED cannot be instructed.** `authority()` grades RED for two different reasons —
  the executor cannot reach the page, *or* novelty blocks — but `authority_seam.takeover` rejects
  an `instruct` answer on the stated assumption that *"the executor is what could not reach the
  page"*. With `reach_gaps: []` every time, `PLAN_progressive_autonomy` §2's intended path (teacher
  supplies the meaning, the local actuator performs and verifies it, the step stays journaled) is
  unreachable exactly when reach is fine. This is the plan's own falsifier #4, on turn one.
- **A takeover cannot report a new tab.** Clicking Apply opened the application in a new tab; the
  drive was bound to the old `tab_id`, so a legitimate takeover had no way to tell the loop where
  the work went, and the drive had to be aborted and re-addressed by hand.

**Where it's encoded now.** `apps/mcp/app/main.py` — `_url_for_tab_id` (CDP id → URL via the
browser's own `/json/list`, a free local socket) so an id-only caller is addressable, and
`_verify_target_tab(addressed=…)` which now **refuses** when an explicit address could not be
pinned, while a caller that named no tab still gets the front tab. `perception/live.py` —
`CapturedTurn`, `read_artifact`, `sense(artifact=…)`, and `artifact_from_live` demoted in its own
docstring to the fallback it should always have been. `controller/live_actuator.py` — perceives the
capture it just took, and finally passes the `title` it always had. Pinned by
`test_main.py::TabAddressingTest` and two new `test_perception_live.py` cases.

**The transferable lesson.** A guard whose every branch is keyed on one optional parameter is not a
guard for callers that omit it. Both of these bugs were *documented in the docstring of the very
function that had them* — the code said "a tab_id alone cannot address a page" and then tried to,
and said "FAIL LOUD" while failing silent. Prose next to a rule is not the rule.

---

## 2026-07-22 — The teacher had no seat, so it wrote scripts; and four things the real corpus taught us

**What we believed.** That "the teacher keeps free-handing scripts around the Interaction API" was
a discipline problem, fixable by restating §8 more firmly. PRINCIPLES §11 has restated it twice.

**What's actually true.** It was a **missing seam**. `Reviewer` had exactly two implementations —
`cli_reviewer` (blocks on `input()`, i.e. a human at a TTY, on *every* non-recipe step) and
`auto_reviewer` (a confidence floor that never asks anyone) — plus `teach_session`, which asks on
*every* turn including ones a compiled program would run for free. So the only settings were *ask
always* or *never ask*. And an escalation **returned** `STATUS_ESCALATED`: the drive was over.
There was nowhere for the local Claude agent to answer a question mid-drive, so the only way to
finish anything was outside the system. §11 had already named the fix as owed ("a reviewer
transport the local Claude agent can service"); nobody had built it, and the gripe kept being
filed as a behaviour problem instead.

The same gap had a second half nobody had connected to it: `decide()` escalated as
`intent="observe", confidence=0.0` with no hypothesis. On the hardest turns — exactly the ones the
teacher is paid for — the local layers made **no prediction**, so `shadow_agreement` had nothing to
score. The teacher looked like it was doing everything because, on the record, it was: **the
student never took the exam.**

**Four things that only showed up when the registry was run over the REAL 45-row journal** (each
would have been invisible against fixtures):

1. **A drifting free-text label was fragmenting a track record.** One twelve-success
   `indeed_apply_questions / click / Continue` history was split in two because some rows carry
   `task="indeed"` and others `task="indeed_quick_apply"`. `TransitionKey` now excludes `task`.
2. **Keying a transition on where it LANDS shatters it.** Indeed skips prefilled steps — the
   recipe's `expect` is a *list* by design — so one action legitimately lands in several places.
   Landings are recorded as reporting, never as identity.
3. **Target-parameterised states would have grown one "transition" per job title.**
   `programs.NON_COMPILABLE_STATES` already refused to compile them for exactly this reason; the
   registry now reuses that same list to cap them below autonomy rather than re-listing it.
4. **`BeliefState.as_dict()` was lossy in the one way that mattered.** It renders all five axes,
   filling unassessed ones with `1.0` — right for a human reading a row, wrong for a machine
   reading one back, because `blocks()` distinguishes "no idea" (1.0, blocks) from "nobody asked"
   (absent, does not). Nothing had ever read a belief back, so the loss was invisible until
   `authority()` did; a replayed row escalated where the live drive acted. Fixed with an
   `assessed` key + `BeliefState.from_dict`, so there is now **one** implementation of `blocks()`
   rather than a copy in every consumer.

**A fifth, from the end-to-end smoke run — the ORANGE/RED line was drawn in the wrong place.**
With every unit test green, running the REAL inbox + REAL authority seam + REAL loop together
graded a Workday page with a visible combobox as **RED** — teacher drives — purely because the
field was missing from `apply_fields`. That would promote *every unmapped field on every ATS* to a
full takeover, which is the exact opposite of the point. The fix is a stated distinction rather
than a tweak: **a missing addressing entry is a knowledge gap (ORANGE)** — the page is fine, and a
bounded teacher instruction can route around it, since a tier-1 click needs no table — while **an
undriveable widget or an unaddressable page is a capability gap (RED)**, because no amount of
teacher meaning makes the local executor able to work it. `reach.BLOCKING_GAP_PREFIXES` is now the
one place that line is drawn. Worth noting how it was caught: the unit tests all agreed with each
other because they all encoded the same wrong assumption; only composing the real parts disagreed.

**A sixth, about our own tests.** The first "run it against the real corpus" test passed
*vacuously*: `conftest.py` redirects `INTERACTION_ARTIFACTS_DIR` at a temp dir for the whole
session (correctly — the suite once wrote 237 fixture rows into the live corpus), so `read_rows()`
returned `[]`. A test asserting over "the real journal" has to resolve the path explicitly and
assert the corpus is non-empty, or it is a test of nothing.

**Where it's encoded now.** `docs/PLAN_progressive_autonomy.md`; **PRINCIPLES §12**;
`interaction/authority.py` (the four modes, pure + tested truth table),
`interaction/lesson.py` (scoped, verify-before-accept), `controller/maturity.py` (a *view* over the
journal, never a second corpus), `controller/reach.py` (can we operate this page at all — the
operator's "the observer is great until we can't do anything about it", made computable),
`controller/inbox.py` + `/api/controller/teacher/*` (**the seat**), `controller/orientation.py`
(the deep end), and the four modes in `controller/loop.py`.

---

## 2026-07-16 — The authoritative docs were three eras stale; reconciled + the endgame written down

**What we believed.** That the doc set described the system. It described three different past
systems: `architecture.md` (April) still said screenshot-primary / grounding-first and had never
been amended through the AX-primary shift it forbids drifting past; `PROJECT_STATUS.md` (06-15)
described the SELECT-cascade era — its "corpora are being collected NOW" claim predated the
07-16 finding that live drives fed none of them; and "L3/L4" silently changed meaning when the
Interaction API moved L4's target from element-picks (selection telemetry) to intents (the journal),
with no doc saying so.

**What's actually true.** For a solo project whose collaborators are stateless sessions, stale
authoritative docs are an *architecture* problem, not a documentation problem — they are the only
shared memory, and each stale claim gets re-paid every session (this file's own founding rule,
applied one level up). Corrections were landing here in LEARNINGS but not propagating upstream to
the docs they corrected.

**Where it's encoded now.**
- `architecture.md` amended per its own change discipline (invariant #1 reworded, #9 journal and
  #10 closed-vocabulary added, chosen path re-sequenced L3-first, alternative B marked partially
  adopted, amendment log added).
- `PROJECT_STATUS.md` rewritten for the Interaction-API era, with measured corpus numbers
  (journal 6 / loop_steps 43 / telemetry 101) instead of intentions, and a terminology section
  fixing the L3/L4 overload.
- `DECISION_two-stacks-one-spine.md` (new) — the journal is the corpus spine; one action surface
  for teacher and loop; the loop's future is intent-emitting; the escalation ladder R0–R5 encoding
  the operator's endgame: learned scenarios graduate off Claude per-scenario, Claude teaches novel
  work indefinitely, verification failures (intent didn't land on the expected state) trigger
  escalation, and every escalation trains the rung below.
- `PLAN_flywheel_first_revolution.md` (new) — definition of done for the first full flywheel
  revolution, pre-flight so no teacher drive is wasted, promotion gates, and the metrics wall.

**The ritual gains a clause:** when an entry here corrects a claim in an upstream doc, patch the
upstream doc in the same commit — a correction that only lives in the log is half-landed.

---

## 2026-07-16 — `/scan_form` retired: the live diff found THREE bugs, and my own validation was self-confirming

**What we believed.** `/scan_required` is better than `/scan_form` on paper (per-control labels,
`disabled` beats a stale asterisk, checkbox groups counted), and the only open question was whether it
MISSED anything before it could feed `form_complete_gate`. Ran both against KKR's live Greenhouse form
(`gh_jid=5995076004`, the form in a cross-origin OOPIF addressed as its own CDP target).

**What's actually true.** `/scan_form` is worse than "misleading on Workday" — it is unusable, and in
both directions at once:

- It reported **21 "fields", all "required", 18 "unfilled"** — but it labels every control with its
  **container's text**, so the 14-checkbox `languages` group became **14 separate required fields all
  named "Please indicate any languages…"**, 13 of them "empty" *even though the group is answered*
  (one box ticked). Feeding that to `form_complete_gate` (`ok = not unsatisfied`) would have made the
  gate **permanently un-passable on this form**.
- Its `diagnostics.strategy` was `fieldset/group` with `container_count: 5` — so it never saw
  Country, Location, School, Degree, Discipline, the 7 screening questions or the attestation.
  **~16 real required fields, invisible**, while inventing 13 phantom ones.
- `/scan_required` (after the three fixes below) reports **1**: the AI attestation. Ground truth: 30
  visible+required, 2 `disabled`, 1 genuinely unanswered. It excludes the two disabled End-date fields
  that keep their `*` **and** `aria-required` (the KKR trap) and omits both answered checkbox groups.

**The three bugs the live run found — none of which a unit test would have.**

1. **`closest('[class*=select__control], .select, [class*=field], div')` silently reads the wrong
   node.** `closest` walks up to the NEAREST ancestor matching **any** branch, and `div` matches
   almost immediately — landing *inside* the react-select control, **below** `singleValue`. So every
   FILLED react-select read as empty. `/scan_required` reported **17 unanswered, of which 15 were
   already answered.**
2. **`[class*=singleValue]` does not exist until the widget is answered.** react-select renders a
   placeholder when empty and only mounts `singleValue` on pick. So detecting a react-select *by*
   `singleValue` fails on exactly the fields the scan returns. It fell through to `.value` — and
   **react-select's `.value` holds transient search text**, so a half-typed field reads ANSWERED,
   drops out of the list, and the gate passes an incomplete form. Latent only because nothing had
   typed there yet. The tell that works on an unanswered field is `select__control` /
   `aria-autocomplete=list`.
3. **`opacity: 0` defeats an `offsetParent` + rect visibility check.** Greenhouse mounts a hidden
   proxy input (`class*=requiredInput`, `tabIndex=-1`, `opacity:0`) purely so native validation
   fires. It is `required`, unanswered, and `offsetParent`-visible with a 608×22 rect — a phantom
   required question. This is the "hidden required twin" `GREENHOUSE_LESSONS` already warned about.
   **Do not reach for `Element.checkVisibility({opacityProperty:true})`** — measured: it rejects the
   proxy (good) *and* rejects `#country` and `#school--0` (fatal), because react-select's own search
   input is `opacity:0` whenever the singleValue is showing. **Opacity does not separate them;
   `tabIndex` does** (1 proxy at -1, all 29 real fields at 0). The rule generalises without being
   site-specific: *a required control the user cannot TAB to is a validation proxy, not a question.*

**The methodological lesson, which is the biggest one.** My first "ground truth" probe used the same
`closest('…, div')` selector as `SCAN_REQUIRED_JS`. It agreed with the scanner **17/17**, and I read
that perfect match as "zero misses". **They agreed because they were both wrong in the same way** — a
self-confirming measurement. A validation written by the same hand, at the same time, with the same
assumption, validates nothing. The disagreement only surfaced when the *fixed* scanner returned 2
instead of 17 and forced a re-derivation from a different angle. **When a check and its oracle share
an author, they share its blind spots — vary the method, not just the code.**

**Also: two of MY OWN JS blocks had already drifted.** `DESCRIBE_WIDGET_JS` detected a react-select by
`select__control` (right); `SCAN_REQUIRED_JS` by `singleValue` (wrong). Written an hour apart, same
session, same app, same author. That is the exact failure the shared `interaction` package exists to
prevent, reproduced inside one session. Encoded now in `apps/mcp/app/js_common.py` — `__vis`, `__txt`,
`__isReactSelect`, `__isUserField`, `__invalid`, `__valueTruth` — injected into both blocks, so there
is one definition of what a react-select is and where its truth lives.

**Where it's encoded now.** `apps/mcp/app/js_common.py` (the tells); `protocols.py::SCAN_REQUIRED_JS`
(uses them; also now reports FILLED-but-INVALID rows, because the gate's rule is
`satisfied = (not required) or (filled and valid)` and reporting only unanswered fields would have
silently dropped that blocker); `main.py::_scan_required_fields` (ONE adapter for both callers — they
were byte-identical copies, and a fix landing in one and not its twin has bitten us three times);
`test_scan_required_adapter.py` (the gate-verdict invariants, incl. `None` ≠ `[]` — a down capture
server returning `[]` would read as "form complete" and unblock the gate). `/scan_form`,
`ScanFormRequest` and `_SCAN_FORM_JS` are **deleted** (144 lines).

---

## 2026-07-16 — The event log is NOT the flywheel: `eval:0` vs `type:137` was the wrong scoreboard

**What we believed.** `PLAN_execution_api.md` §1(a) — the founding argument for the whole Interaction
API — says an inline script is un-learnable *because* API calls are recorded and `/eval` isn't, citing
the event log: `type:137 clear:92 click:80 select:32 widget_select:12` and `eval:0`. `PRINCIPLES.md` §8
repeated it: *"Every API action is recorded, replayable and trainable."*

**What's actually true.** The counts are real (re-measured independently, exact match). The inference
was wrong, and it was load-bearing:

- `event_log.jsonl` is a **1000-line RING BUFFER**, not append-only despite its own docstring — every
  write is `read_text()` → `splitlines()` → truncate → `write_text()` (`event_log.py:44`). A long
  session silently eats its own oldest rows.
- Two processes write it with **no cross-process lock** — each has its own `threading.Lock()`, which
  guards nothing across a process boundary. They race read-modify-write on one file.
- An event is `{ts, source, kind, summary, detail, domain}`. **No fingerprint, no capture id, no
  session, no per-step log.** Outcome is smuggled into `detail` as a string. `domain` holds two
  different vocabularies in one column (`/execute` passes a URL; `/capture` passes a registry slug).
- **Exactly one consumer:** `EventsConsole.jsx`, polling every 5s. **No trainer reads it.**
- `record_only` (dry run) and a real drive emit **byte-identical events** — the corpus could not tell a
  rehearsal from a performance.

So the honest scoreboard was never `eval:0` vs `type:137`. **It was that both are zero.** The real
corpora — `loop_steps.jsonl` (genuine append, fingerprint-keyed `StepRecord`) and
`selection_telemetry.jsonl` (whose header literally says *"THIS IS THE TRAINING CORPUS"*) — are written
**only** by `runtime/loop.py`. The MCP endpoints we actually drive with never touch them. The
2026-07-15 session drove a Workday application to submission and a Greenhouse one to its last field —
350+ recorded actions — and contributed **0 rows to each**. `loop_steps.jsonl` was 43 lines;
`selection_telemetry.jsonl` was 101; both from `run_batch`, not from live drives.

**Why it mattered right now.** Phase 1 as planned (build `/describe_widget` + `/select_option` + …,
then "finish KKR using only these — zero `/eval`") would have hit its own Definition of Done and still
produced zero training rows. The session paid for a third time.

**Where it's encoded now.** `packages/interaction/interaction/journal.py` — the intent journal:
append-only, fingerprint-joined to the existing corpora, carries the per-step log and an `executed`
flag. `apps/mcp/app/intent_api.py::journaled` is a route decorator, not a helper, because the failure
mode is *forgetting*: `/execute` logged its success path and returned **silently** on both not-found
early-returns, so "the recipe is stale" — the most useful row in the corpus — was exactly the row we
never wrote. The event log stays as the operator wall display; it's a good one and a bad corpus.
Different jobs, different files.

---

## 2026-07-16 — The ATS recipes were INERT, not merely inconsistent

**What we believed.** `PLAN_interaction_api.md` §4: the recipe schema isn't uniform (Workday uses
`{role, name}`, Greenhouse uses `"#first_name"`), and unifying it is a Phase-2 migration blocker.

**What's actually true.** Worse on both counts.

- The claim is half wrong: Greenhouse is `{"selector": "#first_name"}` (a dict), not a bare string; and
  Workday carries **two** shapes — `role+name` in the *account* recipes, bare CSS under a `selectors`
  key in `WORKDAY_APPLY_RECIPE`. Across four sites there were **six addressing shapes** under two
  different step keys (`fields` vs `selectors`), with **three selector languages sharing one key**
  (CSS, a regex in `not_found_text`, and a Playwright `text=` pseudo-selector).
- And the deeper fact: **no code path read any ATS recipe's `fields`/`selectors` entry.** The only
  consumer is `recipe_spec()`, serialising them to JSON for one GET endpoint that shows them to the
  *model*. They are documentation. Call sites re-hardcode them by hand — `routers/career_search.py`
  re-implements Workday create-account matching with inline substrings and cites
  `WORKDAY_CREATE_ACCOUNT_RECIPE` only in a **docstring**. 13 of 16 `ATS_PLATFORMS` entries have no
  field data at all, and `ats_registry` points `appvault` at a recipe that is an empty list.

So the job was never "unify the schema" — it was **"make the recipe executable at all."**

**Where it's encoded now.** `apps/controlplane-api/apply_fields.py` — `resolve(ats, field)` over one
schema, 32 fields across greenhouse/workday/indeed, with tests (13 of them; not one could have been
written against the prose it replaces). Six shapes collapse to **two** — `role_name` and `selector` —
because they're the only two that survive a DOM reshuffle; `addressed_by` is *derived*, never
hand-written. Everything in it is transcribed from `GREENHOUSE_LESSONS`/`WORKDAY_LESSONS`; the lessons
stay the narrative, this is the contract.

**The entry that argues the whole case:** Greenhouse's `#country` is the **phone** country code, not
the address country (the address is `#candidate-location`). The id lies. `phone_country` is where we
get to tell the truth *once* instead of every caller re-learning it.

---

## 2026-07-16 — The outcome taxonomy needed two members the plan didn't have (found by implementing)

`PLAN_interaction_api.md` §6 lists eight outcomes. Implementing them surfaced two more — which is the
promotion rule working ("an API's job is not to be right; it's to be the single place the fix lands"):

- **`error`** — every endpoint ends in `except Exception: return {"ok": False, "detail": str(exc)}`,
  which maps to **none** of the eight. Folding a websocket drop into `not_found` would be the same lie
  the taxonomy exists to prevent: a *mechanism* failure would read as a stale recipe and send us
  re-mapping selectors that were fine.
- **`committed_unconfirmed`** — §6 assumes every endpoint **can** verify. The staged-commit popup
  proves otherwise: the footer's Update navigates, tearing down the very context that would observe
  the result. `_POPUP_SELECT_JS` says so itself — *"THE COMMIT DESTROYS ITS OWN OBSERVER … CONFIRM
  FROM OUTSIDE"* — and then returns `ok:true` anyway. Neither existing member is honest there. `ok` is
  a **silent success** by §6's own test ("if the primary silently fails, what does the caller see?").
  `not_committed` is the **opposite lie**: a false negative makes a caller re-fire a commit that
  already worked, and a double-fired commit is a double submit. Caller's move: confirm from *outside*,
  as `/set_distance` already does via `_read_radius` ("the URL is CONFIRMATION, never the mechanism").

**Also: tier-1 `ok` ≠ tier-2 `ok`, and that had to be written down.** `DirectDriver.move_and_act`
(`driver.py:247`) returns `ok=True` on any non-exceptional path — it never reads the result back, and
`.click()` on a detached or 0×0 node no-ops silently (the same trap as Indeed's hidden decoy cards).
So `/execute`'s OK means *the mechanism completed*, not *the page accepted it*. Semantic verification
is the protocol tier's job. Encoded in `/execute`'s docstring and `contract.Outcome`.

---

## 2026-07-16 — Intent sits ABOVE the frozen ActionId; we were one enum away from a fourth vocabulary

**What we believed.** The plan's INTENT layer (§3) is a new closed verb vocabulary the model emits.

**What's actually true.** The repo already carries **three** action vocabularies that don't agree:
the frozen `select_stage.schema.ActionId` (click/type/select/scroll/submit/clear/none — the Haiku
output contract, cache-versioned), the DB's `ActionRegistry` seed (adds navigate/wait/press/any), and
the executor's driver (adds `upload` — which is live). A fourth, unrelated one would mean L4 trains on
verbs the selector can't emit.

They are **different altitudes**, not competitors:

    Intent   — a SEMANTIC operation on a FIELD.    select_option("Phone Device Type", "Mobile")
    ActionId — a PRIMITIVE operation on ONE NODE.  click(node 4821)

One intent **expands** into 1..N ActionIds. So Intent doesn't replace the frozen contract: the Haiku
selector keeps emitting ActionId, L4 learns Intent, and the journal records **both**, which is what
makes them joinable rather than rival. `contract.intent_expands_to` holds the map and a test pins every
expansion to a verb `driver.py` actually implements, so we can't mint a rival vocabulary by accident.
`contract.intent_for_action` is the reverse bridge for tier-1 `/execute`, which takes an `action_id`
and would otherwise split the corpus in two.

Two vocabulary calls worth recording: **`scroll` IS an intent** (arguably mechanism, but it's in the
frozen enum, the loop emits it, and the last drive used it constantly — a verb the system really emits
and the vocabulary can't express is a hole in the corpus, not a purity win). **`clear` is NOT** —
clearing is `set_text("")`, and the `actions` column keeps the primitive. `clear:92` in the event log
is the second most common action and it still doesn't earn a verb.

---

## 2026-07-16 — Why `/describe_widget` is read-only, and why jsdom would have tested a fiction

**Read-only.** The plan's §2 sketch has `options: [...] # after open, if enumerable`. Opening is an
**action**: it can dismiss another widget's popup, fire a server-side fetch, and change the state the
caller is about to act on. `Intent.DESCRIBE` is in `READ_ONLY_INTENTS` and expands to zero ActionIds,
so a describe that opens breaks its own contract. `/describe_widget` therefore reports options only
when they're readable *without* opening (native select, checkbox/radio group, an already-open listbox)
and otherwise says `options: null, options_enumerable_by: "open"`. `/select_option` opens it anyway and
reports what it found there.

**No jsdom test.** The obvious move for testing the DOM classifier would be jsdom. It would be
*actively misleading*: **jsdom's `offsetParent` is always `null`**, and the classifier's `vis()` helper
is built on it, so every element would read invisible and every assertion would validate a fiction.
PRINCIPLES §5 already says the right thing — "deterministic detectors are cheap but easy to get subtly
wrong; validate them against the actual live pages before trusting them" (written after `bframe`
PRESENT ≠ SHOWN burned us twice). So the classifier is validated on the live drive, and it's cheap to
audit because every classification lands in the journal: a wrong `widget_type` shows up as a downstream
`not_opened`/`not_staged` row on the same field.

**Same reasoning killed the `/scan_form` retirement.** `/scan_required` supersedes it and is better on
every count — but `/scan_form`'s two callers feed `form_complete_gate`, the invariant that makes the
model structurally unable to forget an empty required field. Swapping a **safety gate's** input to a
scanner that has never run on a real page is exactly §5's hazard: if `/scan_required` under-reports,
the gate says `ok` on an incomplete form — worse than the labelling bug it fixes. Deprecated in place;
migration gated on diffing both against the same live form.

---

## 2026-07-11 — First full Indeed smartapply flow driven end-to-end (Brigham Sr Data Analyst SUBMITTED)

**What we did.** Drove a complete Indeed "Apply with Indeed" (smartapply) application to SUBMIT, live,
humanized, on session #16. The module sequence (each its own URL under `smartapply.indeed.com/beta/indeedapply/form/`):
`contact-info` (auto-prefilled) → `commute-check` ("Continue applying") → `resume-selection-module`
(the user's uploaded **GM_Res.pdf** was pre-selected — chosen over the auto-generated Indeed résumé) →
`questions-module` (employer screening) → `demographic-questions/1` (EEO self-ID) → `demographic-questions/2`
(ADA disability) → `review-module` → `post-apply` ("Your application was submitted…"). Captured + labeled
every state (rows 247–256).

**Interaction findings that will save the next session a lot of pain.**
- **`/scan_form` is the right tool to READ an apply form** — returns every field's `{label, kind, required,
  filled, value_preview}` in one call, no scrolling. Use it before touching anything; re-call it to VERIFY
  each field after you set it. Far more reliable than screenshot-scrolling (which the reload churn keeps resetting).
- **Multi-question radio groups: target by `backend_node_id`, never by name.** Every question's options are
  just "Yes"/"No" (or "Declined"), so `target_name` collapses to the FIRST group. Get fresh node-ids from an
  `/ax_scan`, sort by bbox `y` to map DOM order → questions, click the specific node. Node-ids CHURN on
  Go-back/re-render, so re-scan after navigating.
- **Prefills can silently DISCLOSE against preference.** The EEO module came prefilled from a past
  application with real values (Gender=Male, Race=Asian, Veteran=Not-a-veteran, Disability="No, I do not
  have a disability"). Per the user's decline preference we OVERRODE each to its decline option
  ("Declined" / "Decline to Disclose" combobox / "I do not wish to self-identify" / "I do not want to
  answer"). ALWAYS read `value_preview` and override — don't trust `filled=True` as "handled correctly."
- **A required field may have NO decline option.** "Are you Hispanic or Latino? *" was Yes/No only and
  BLOCKED submit ("Choose an option to continue") — escalate to the human (their factual call), don't guess.
- **There's a required Terms **certify** radio at the very bottom of the EEO module** ("I certify that I have
  read…") with no alternative — easy to miss; it's a real gate.
- **The `/execute` empty-response quirk is EVERYWHERE in this flow** — nearly every click returned an empty
  body; ~half were genuine no-ops. Pattern that worked every time: fire → verify (scan_form/url/screenshot)
  → retry until it takes. Budget 1–2 retries per click.
- **Two-tab flow:** "Apply with Indeed" opens smartapply in a NEW tab; pin `tab_url="smartapply.indeed.com"`
  on every call. `/screenshot` (Page.bringToFront) DISMISSES open dropdowns — never screenshot between
  opening a filter/select and acting on it.
- **Humanized scroll shipped** — `driver.py` `parse_scroll_value` + base `_do_scroll` (CDP mouseWheel) +
  `humanized.py` `_scroll_plan` (eased, jittered notches + read-pauses). NB: running a venv script that
  imports MCP modules writes `.pyc` into the reload-watched dir → bounces the MCP worker → resets in-flight
  HTTP (`HTTP 000`); don't do that mid-drive.
- **Resume asset for cross-site apply** — `assets.py` now has a `documents/` area + `resume_key()`/`resume_path()`
  + `GET /api/assets/documents`; canonical resume `documents/GM_Resume.pdf` for Workday/ATS file uploads
  (Indeed's own flow uses the profile résumé, not this file).

**Search-filter findings (same session).** Indeed's Distance filter is a 2-step apply (pick radius → click
**Update**); applying mutates the URL (`&radius=50`) and re-navigates the SERP (`from=searchOnHP` →
`searchOnDesktopSerp`, new `vjk`). Canonical order: search first, THEN set radius.

**Where it's encoded.** Captures 247–256; `apps/mcp/app/executor/driver.py` + `humanized.py` (scroll);
`apps/controlplane-api/assets.py` (documents/resume). Still a live teacher drive, not yet a codified apply recipe.

**Cross-site (Workday) apply recipe — operator-directed strategy (2026-07-11).** "Apply on company site"
routes to the employer's own Workday tenant (`<employer>.wd5.myworkdayjobs.com`). Recipe facts to bake in:
- **Workday needs a per-employer ACCOUNT + a résumé FILE.** The first step is always `Create Account/Sign In`
  (the wall). The agent CANNOT do this — creating accounts and entering passwords to authenticate are hard
  prohibitions, and that holds even when the operator has saved the password in the Workday Accounts vault
  (saving ≠ logging into the live site). The operator does the site login by hand; then the agent drives.
- **Workday steps:** Create Account/Sign In → Autofill with Resume → My Information → My Experience →
  Application Questions → Voluntary Disclosures → Self Identify → Review.
- **ALWAYS try Autofill-with-Resume, then CHECK (don't hand-input then check).** Operator's efficiency rule:
  autofill from the résumé file, then VERIFY each parsed field lines up + fill only the gaps — fewer steps
  than typing everything and then verifying. Résumé file = `assets.resume_path()` (GM_Resume.pdf).
- **Secure per-employer credential store shipped:** `accounts.py` gained `kind="workday"` + `login_url`;
  `WorkdayAccountsPanel.jsx` under System→Workday Accounts (operator types password → encrypted into the
  secrets vault; only a masked hint returns; agent never handles plaintext). Point32Health shell seeded.

## 2026-07-10 — FB create-listing driven live for the first time + per-item OWNED photo uploads

**Context.** A Facebook Marketplace training/selling session run *concurrently* with the live Indeed
session. Isolation held by pinning `browser_url=http://127.0.0.1:9326` (the selling profile) on every
MCP call — Indeed (`:9322`) + Gmail (`:9325`) never touched. Backend runs `--reload`, so each edit
bounces the shared API briefly; fine while the other session is human-login-idle, but **batch backend
edits** so it reloads once, not per-file.

**The selling profile was already authed — don't assume "log in" is the task.** Session #15
(`facebook_alt` / `business_chrome_profile`, "John Carl") was already logged in and sitting on
`/marketplace/create/item`. Per PRINCIPLES §7 we confirmed via screenshot, not the URL. The user's
stated goal ("get logged in") was already satisfied — surface that instead of re-driving a login.

**The create-listing recipe went from "seeded, not live-verified" to DRIVEN live.** Drove the whole
`fb_create_listing_form` per-action with the humanized driver, re-resolving each node by role+name at
act time (`/execute` `target_role`+`target_name`) — zero node-id staleness. Captured + teacher-labeled
5 distinct states (rows 243–246: empty form→Title, title+price→category-suggestion, condition-picker
→Used-Good, complete-form→Next). **FB domain findings that change the recipe:**
- **Category suggestion PILLS** appear under the Category box the moment you type a Title (e.g.
  "Men's clothing & shoes" / "Women's clothing & shoes"). The human path is to **click the pill**, not
  open the combobox and scroll. Add these as the preferred `category` selector in `facebook_recipe.py`.
- **Conditional fields live under "More details"** and only render per category — apparel reveals
  **Color** (portal combobox) + **Material** (free-text) + SKU. Matches `facebook_listing_schema.py`.
- **Condition** is a 4-option portal picker (New / Used - Like New / Used - Good / Used - Fair). Our
  driver's `select` (click-open → click option) and a granular click-open→capture→click both work; the
  portal survives an `/ax_scan` + `/capture` in between (no premature close).

**The executor ALREADY does file upload — the old "no setFileInputFiles" note was STALE.**
`apps/mcp/app/executor/driver.py` `_element_act` handles `action_id="upload"` via `DOM.setFileInputFiles`
(+ `selector` re-resolution for a hidden `<input type=file>`). So a real post is not blocked by system
capability — only by having a real product photo. Corrected [[project_create_listing_drive_gaps]].

**New feature — per-item OWNED photo uploads (assets belong to ONE post, never shared).** The old
model was a flat *shared* pool (`assets/marketplace/*.jpg`) picked by toggle, so every item's picker
showed every asset. Now: direct upload stored under `marketplace/items/<item_id>/<file>` with a
`<file>.meta.json` sidecar (owner, original name, uploaded_at, size, content_type). Ownership is encoded
in the key path; `list_assets` **excludes** the `items/` subtree so owned photos never leak into another
item's library. Endpoints `POST|DELETE /api/inventory/items/{id}/photos` (multipart up / unassign+delete
down); UI upload tile in **both** create (staged, flushed on save) and edit (owned thumbnails + remove).
Item hard-delete drops the owned folder (no orphans). Encoded: `assets.py`
(`save_item_photo`/`asset_meta`/`delete_asset`/`delete_item_assets`, `ITEM_PREFIX`), `inventory.py`
(`add_item_photo`/`remove_item_photo`, delete cleanup), `routers/inventory.py` (the two endpoints),
`FacebookMarketplaceSection.jsx` (`ItemForm`). Verified end-to-end (upload→owned path→assign→delete,
no shared-pool leak). NB: FastAPI multipart works though `import multipart` looks missing — newer
`python-multipart` imports as `python_multipart`; trust the live upload, not the import probe.

**A real listing was driven to the publish gate (Kith x Wilson polo, $90, 7 real photos) — two more
domain facts.** (a) **FB auto-detects Color from the photos.** After swapping the placeholder for the
real navy photos, the Color field flipped Black→**Blue** on its own — FB's image analysis fills the
attribute. Don't fight it; verify it landed right. (b) **New accounts have a daily Marketplace-listing
cap.** At the final "List in more places" step, `facebook_alt` (a young account) showed *"You can't add
a listing to Marketplace right now because you reached your daily limit as a new Facebook account"* with
Publish disabled. This is a **human-required stop-state — do NOT force Publish** (errors + flags the
account). Retry after the ~24h reset or use an established account; FB retains the prepared listing as a
draft, and the inventory item (`internal_status=ready_to_post` + a note) is the source of truth to
re-drive. Encoded as `fb_listing_publish_blocked_new_account_limit` in
`facebook_recipe.py`'s `FACEBOOK_CREATE_LISTING_BRANCHES`. Also relevant: driving overwrote a stale draft
in place (remove placeholder photo → `upload` 7 owned files via one `DOM.setFileInputFiles` → `clear`
then `type` each text field, since `type` APPENDS). The empty-first-execute quirk hit every `Next` click
— always retry + verify by screenshot.

**Two things worth fixing (noted, not yet done).** (1) `/execute` requires `target_bbox` even when
`target_name`/`selector` re-resolves the node and the bbox is ignored — pass a zero bbox for now; make
it Optional. (2) **Modeling nuance:** within ONE page-state (`fb_create_listing_form`) the correct
golden action depends on form-fill PROGRESS, not the visual (empty→type Title vs complete→click Next).
Labeled the finished form as a distinct state `fb_create_listing_form_complete` so the classifier isn't
handed the same picture with two different goldens; a cleaner fix is a completion feature in the state.

## 2026-07-10 — The cross-domain login-code errand works end-to-end (Indeed authed via a code read from Gmail)

**What we proved.** The `fetch_login_code` errand ran live, end-to-end, and got Indeed authenticated
WITHOUT ever driving Google's password page:
1. Stood up the dedicated **`google` profile** (session #17, port 9325) via `POST /api/training/sessions`
   + `/start`. Needed a gmail scenario first — `create_training_session` REQUIRES a domain-bound
   scenario, so added `gmail_login_google_signin` to the registry.
2. The human did the one-time Google login in that window (passwordless **passkey** — even cleaner than
   a password). A **per-instance auto-capture watcher** (poll page target → capture on settle) recorded
   each state as gmail-domain training data: `google_signin_email`, `google_signin_2fa` (passkey), `inbox`.
3. On Indeed (session #16), clicked **"Sign in with a code instead"** → Indeed emailed a code. The authed
   `google` profile read it **straight from the Gmail inbox subject line** ("Sign in to Indeed with code:
   NNNNNN") — no need to open the email. Typed it into Indeed → accepted.
4. Indeed then required **phone 2FA** ("confirm it's you", SMS to …67) — a real 2FA gate, so we ESCALATED
   to the human (never auto-solve), they supplied the SMS code, we submitted it → `logged_in: true` on
   `secure.indeed.com/settings/account`. Skipped the "set up a passkey" post-auth funnel with **Not now**.

**Lessons that will bite the next session.**
- **A login code can require a second, out-of-band factor.** The email-code path is NOT sufficient alone
  when the account has phone 2FA — the errand gets you PAST the email wall, then hands off. Build the code
  errand to expect a follow-on human-gated factor, not to assume email-code ⇒ done.
- **The `/execute` "empty-first-execute" quirk is real and recurring** — the first action after an idle
  gap often returns an empty body / no-op (sometimes it silently worked, sometimes not). ALWAYS verify by
  screenshot/AX and retry; never trust a single execute's return value. (Confirmed again here on type + click.)
- **Read a login code from the Gmail SUBJECT, not the body.** Indeed (and most senders) put the code in the
  subject/snippet, so the inbox list is enough — no need to open the thread (fewer steps, less churn).
- **Two live sessions, two ports, target explicitly.** Indeed on :9322, google/Gmail on :9325 — every
  MCP call pins `browser_url` to the right one. The errand is literally "hop from :9322 to :9325 and back."
- **`auth_state` is Indeed-specific.** On `myaccount.google.com` it reported `logged_in:false` — meaningless
  there; being on a signed-in-only URL is the real signal. Don't reuse the Indeed detector for Google.

**Where it's encoded now.** Captures/labels: rows 232–242 (gmail SSO states + Indeed code-entry / phone-2FA
/ logged-in). Watcher prototype: `scratchpad/gmail_login_watch.py` (graduate into a permanent per-session
auto-capture endpoint — the "state detector within each Chrome instance"). Registry: `gmail_login_google_signin`
scenario in `seed.py`. STILL TODO: codify the errand as a reusable recipe/endpoint (today it was a live
teacher drive), and give Gmail an operator workspace.

---

## 2026-07-09 — Provider groups (Google bucket) + Gmail is a real domain; Google login is an errand, not a page to drive

**The trigger.** Trying to log Indeed in via Google SSO from the training session (#16, Chrome on
`:9322`, persistent `indeed` profile). Clicking Indeed's **Continue** hands off to Google — and the
Google sign-in surfaces in a **separate window/popup**, plus Indeed's auth page already carries a
**reCAPTCHA enterprise** iframe. Two lessons fell out.

**1 — Don't drive Google's password page; make login an ERRAND.** Everything up to and after Google's
auth we drive on the CDP-AX layer with the humanized driver (verified: typed the email into Indeed's
box by role+name `textbox/"Email address"`, clicked `button/"Continue"`). But the Google
*password + 2FA* keystrokes are a deliberate hand-off to the human — same class as never auto-solving
a captcha. Reasons: (a) it's the user's **crown-jewel Google credential** (a locked Google account
cascades everywhere), (b) `accounts.google.com` is the most bot-fingerprinted page on the web, (c) the
training value is in **capturing the states**, not in who typed. The cleaner design the operator
chose: use Indeed's **"sign in with a code instead"** path and fetch the code from Gmail — i.e. login
becomes a cross-domain **errand** (`gmail ▸ fetch_login_code`), not a page we drive.

**2 — Multi-window SSO IS reachable over CDP; target it explicitly.** The popup is its own
`type=="page"` target on the SAME `:9322` debugging port (visible in `/json/list`). `_discover_target`
(`apps/mcp/app/observer/ax_proposer.py`) matches **`tab_id` first, then `tab_url` substring, else the
first page**, so pin every `/capture|/ax_scan|/execute|/screenshot` call to the popup with
`tab_url="accounts.google.com"` (or its exact `tab_id`) — don't let discovery default to the Indeed
tab underneath. This is the concrete fix for the long-standing "multi-window captures carry no window
identity" gap.

**What we built — the PROVIDER GROUP, the bucket above domains.** A *provider* is one company whose
many surfaces we drive as separate domains but which share **one identity/login**. Google is the
first: `gmail` (built) + `google_calendar`/`google_docs`/`google_sheets` (planned) all authenticate
through **one** Google sign-in (one persistent pre-authed profile) and the shared SSO flow is what
other domains hand off to. Kept as a small **backend constant** (`providers.py`, like
`command_center.DOMAINS`), NOT a DB table — it's config, membership is derived from the live
`DomainRegistry`. `GET /api/providers` resolves each group's live vs. planned members. **Gmail is now
a real domain** in `REGISTRY_SEED` (seed.py) with the shared `google_signin_*` page-states as its home
for SSO training data + the `fetch_login_code` errand goal.

**Gotcha — the base registry seeder only runs on an EMPTY registry.** `seed_training_registry`
early-returns if any domain exists, so adding Gmail to `REGISTRY_SEED` did nothing on the live DB;
worse, a barebones `gmail` row already existed (added by hand/UI, empty `page_states`). The fix is an
idempotent **top-up + reconcile** seeder (`seed_gmail_domain`, mirroring `seed_facebook_extras`) that
**merges** the canonical page-states/hosts into the existing row without removing anything.

**Bug fixed in passing — `/screenshot` always returned "no screenshot data".** `_CDPSession.send()`
returns the **unwrapped** CDP result (`msg["result"]`), so `Page.captureScreenshot`'s base64 is at
`res["data"]`, but the handler read `res["result"]["data"]` (always `None`) — a regression from the
`main.py → main_server.py` split. Now `res.get("data")`. The driver's "eyes" work again.

**Where it's encoded now.** `apps/controlplane-api/providers.py` (new group constant + helpers),
`apps/controlplane-api/routers/providers.py` (`GET /api/providers`), `apps/controlplane-api/seed.py`
(gmail domain + goals + `seed_gmail_domain`), `apps/controlplane-api/main.py` (startup call + router
include), `apps/controlplane-ui/src/components/controlplane/workspace/domains.js` (`PROVIDER_GROUPS`,
gmail `provider:"google"`) + `DomainsHub.jsx` (renders the bucket),
`apps/mcp/app/main_server.py` (`/screenshot` unwrap fix). Verified live: `GET /api/providers` returns
google↦{members:[gmail], planned:[calendar,docs,sheets]}, gmail carries all 7 page-states, and the
"🌐 Google" bucket renders in the cockpit.

**Still open (deliberately).** Gmail has no operator *workspace* UI yet (tile stays non-clickable —
"training live, workspace soon"); the `fetch_login_code` errand + the shared `google` browser profile
are declared but not yet wired to a live run; provider is a constant, not a DB column (promote only if
operators need to edit groups at runtime).

---

## 2026-07-09 — Training-UI flywheel overhaul + teacher-auto-labeling proven live + Indeed pre-auth setup

**Training UI was the flywheel's hidden blocker; now surfaced (4 commits `6d6478d`..`8fe4759`).**
The good **queue labeler already existed but was buried in Lab** (`TrainingSpaceSection`), while the
Training section routed you through a 6-level Dataset Browser dig. Fixes: (#1) Command Center
`🏷️ To label` KPI + per-domain backlog rows, fed by `command_center.build_summary`'s new `flywheel`
block + per-tile `training`; (#2) the "To label" tile is one-click into the queue labeler
(`openLabeler`); (#4) promoted the queue labeler to **Training → 🏷️ Label** (first in nav), demoted the
nested path to "Inspect capture", Dataset Browser to "browse+curate"; (#3) `label_queue?domain=` filter
+ Domain pills in the labeler. Also added a **🗑 Delete** action (DELETE `/api/observations/{fn}`) for
coarse/bad captures, and gmail `email_entered`/`password_entered` substates.

**The action model (the mental unblock).** The system is `(before_state) → [act on ONE element] →
(after_state)`. A label yields TWO signals from one golden pick: SELECT (which element → AX-CDP selector)
+ TRANSITION (post_action_state → planner). A capture is bad when driving was too COARSE and skipped
actions (the classic "sign-in page → inbox" that really did type-email→Next→type-password→Sign-in). No
clean single-action transition → **delete it**. The real cure is **capture PER-ACTION when driving**.

**Teacher-auto-labeling — PROVEN LIVE.** Claude drives → captures a clean state → labels it ITSELF,
zero human. Mechanism: `POST /api/capture {training_session_id, tab_id}` → `PATCH
/api/observations/{fn} {training_annotation:{positive_candidate_id, review_status:"reviewed"},
observed_page_state, post_action_state}`. Because Claude knows what the screen IS + which element it
would act on + where it leads, the labels come free. (label_source becomes "human" = teacher-trust;
no separate "teacher" tier yet — a possible refinement.)

**Indeed pre-auth login setup (in progress).** The persistent `indeed` profile had **no cookies** →
that's why fresh Indeed sessions hit Google's wall (only `facebook`/`business_chrome_profile` were
pre-authed). Persistent profiles live at `/tmp/agent-platform-training-chrome/persistent/<name>` (NOT
reboot-durable — move out of /tmp is a follow-up). Setup = create a session bound to the `indeed_default`
account (→ `persistent_profile=indeed`) + start it (launches Chrome `--user-data-dir=.../indeed`) + do a
**supervised login ONCE** (human clears Google/2FA/code; Claude never auto-solves auth) → profile
persists. That one supervised login IS the per-action login-capture opportunity.

**KEY (2026-07-09, user): Indeed FORCES Google login when the email is already a Google account** — the
email-code fallback won't apply; it redirects to Google SSO. The **human does the Google login** (safe:
human clicks, no automation-flagging). Cross-domain auth (Google login for Indeed, Gmail code as an
errand) is a candidate for an explicit **errand section/flow** — see
[[project_planner_and_cross_domain]].

**Live handoff at compaction:** session **#16** (indeed_jobs, account indeed_default, persistent
`indeed`) is ACTIVE, Chrome on **:9322**, tab was on `secure.indeed.com/auth`. Already captured +
teacher-labeled the entry state (`indeed_login_email` → golden=Email field `cdp-ax-1170c306b0` →
`email_sso_or_code_choice`). Next: user completes the (Google) login; capture + teacher-label each
subsequent state; then the profile is pre-authed for all future Indeed drives.

## 2026-07-09 — Training works today; the grounding/vision datasets were BLIND to AX-sidecar golden labels

**What we believed.** That the flywheel was blocked by the backend / concurrency / missing trainers,
and that the grounding model was hopelessly data-starved (only 4 usable records).

**What's actually true.** Training already works: `POST /api/training/train_stage_observer` (the L3 v0
"am I logged in?" auth classifier) trains to **94% held-out accuracy on 98 labeled captures** —
a real local model that offloads Haiku at classify. And the grounding "4 records" was a **plumbing
bug**, not a data shortage: **15 of 19 golden labels (`positive_candidate_id`) point to `cdp-ax-*`
candidates that live only in the `.ax.json` sidecar**, but both dataset builders searched only the
trace's `ranked_candidates` (grounding) / required an explicit `approved_bbox` (vision) — so AX-labeled
captures were silently skipped. Since the AX faucet, **the sidecar IS the candidate pool the labeler
labels against**; any consumer reading `ranked_candidates` for candidates is stale.

**Fix.** `build_grounding_dataset` + `build_vision_dataset` now load the sidecar (`_load_ax_candidates`),
search the union `ranked_candidates + ax_candidates` for the golden id, and derive the bbox from the AX
candidate (which carries `bbox` at top level, screenshot-px) when `approved_bbox` is absent. **Both
datasets 4 → 19 records**, across both `facebook_marketplace` and `indeed` scenarios. Tests green.
Encoded in `apps/controlplane-api/training.py` (`_load_ax_candidates`, `_build_dataset_record`,
`_build_vision_record`, `_candidate_bbox`).

**Still the real bottleneck (unchanged north star).** Model *accuracy* is still 0% on grounding — 19
records is tiny and the v0 linear grounder is weak. So the lever remains **golden-label VOLUME**
(drive → capture → review/label → retrain), now that the labels we already have actually reach the
trainer. "Concurrency-hardening for training" is premature — nothing to harden until many per-domain
trainers run at once. See [[project_backend_refactor_for_concurrency]].

## 2026-07-08 — Concurrent sessions in one working tree clobber each other via broad commits

**What happened.** While one session did the faucet work, a *second* Claude session working in the
**same** `main` working tree ran a broad `git add -A` / `commit -am` and swept the first session's
in-progress `main_server.py` edit into a commit titled "executor file-upload" (`a57a180`). Work wasn't
lost, but the history lies and the diff is unreviewable. This — plus a 5,742-line `main.py` everyone
edits — is the real reason "we can't commit cleanly."

**The norms now (see `CLAUDE.md` + `docs/PLAN_main-split.md`).** Stage **explicit paths**, never
`git add -A`/`commit -am` for a scoped change; `git status` before committing and confirm you own every
staged path; and if running sessions **concurrently**, give each its own **git worktree** on a
short-lived branch (ephemeral ≠ the long-lived feature branches this repo avoids).

**Fresh-start cleanup done same day.** Deleted 3 merged branches; env-gated SQLAlchemy `echo` (was
hardcoded `True`, flooding a 25 MB dev log — `settings.sql_echo`, default off); regenerated the two
stale `apps/mcp` golden observer fixtures (they lacked the now-always-emitted
`acquisition.training_metadata` — the *only* drift, not a regression) so the suite is green again;
adopted an orphaned passing `classify_apply_outcome` test; pruned dead `.gitignore` worktree lines.
**Planned, not done:** split `main.py` into `routers/` (see `docs/PLAN_main-split.md`).

---

## 2026-07-08 — The AX "data faucet" is already open; "3/175" is history, not a gate

**What we believed.** That AX-sidecar emission was *gated* — conditional on a request field (an
`ax_tree` payload, a "sidecar file arg") — and mostly off, which is why only **3 of 175** captures had
sidecars. The plan was "flip the gate on."

**What's actually true.** There is no such gate and no `ax_tree` field anywhere in the repo. There is
exactly **one** emission site — `_write_ax_sidecar(...)` in `apps/mcp/app/main_server.py` inside
`POST /capture` — and it already fires **unconditionally** (best-effort, inside a `try/except` so a
failure can't fail the capture). Both real capture paths funnel through it:
- control plane `POST /api/capture` → capture server `POST /capture`;
- the runtime live loop (`LiveProposer`, `apps/controlplane-api/runtime/live.py`) → same `POST /capture`.

The capture server fetches the accessibility tree **itself** over CDP (`propose_ax_candidates` in
`apps/mcp/app/observer/ax_proposer.py`); the caller never passes AX data in. So the faucet is
structurally *on* for every path you actually drive through.

The **"3/175"** (from `PROJECT_STATUS.md`) is a **stale snapshot**, not the current state. The emission
block was added **2026-06-15** (commit `80dd253b`); captures from before that have no sidecar. But the
live DB today has **157 tracked captures, and after the v16 backfill all 157 carry AX candidates**
(yields 1–628, `dry_captures: 0`). The faucet has, in fact, been flowing.

**Two different meanings of "backfill" — don't conflate them:**
- *Sidecar files from a saved screenshot/trace* = **impossible.** AX candidates can only be produced
  against the *live* page at capture time (`propose_ax_candidates` needs a CDP connection). A dead
  session can't be re-scanned. This is the real dead end (`PROJECT_STATUS.md` "Corpus can't be
  backfilled").
- *The `ax_candidate_count` column from sidecar files that already exist* = **done, and easy.** The
  sidecar's `proposal_count` is ground truth for a past capture; `scripts/backfill_ax_candidate_count.py`
  re-derives the column from it (idempotent). Run once after the v16 migration so `dry_captures` reflects
  reality instead of the migration default (0-for-all).

**The two real leaks (and what we did about them).**
1. *The faucet's per-drive yield wasn't recorded as durable exhaust.* `/capture` returns
   `ax_candidate_count`, but the control plane was **dropping it** — storing only `candidate_count`
   (the trace's ranked candidates, *not* AX). Fixed: `TrainingCapture.ax_candidate_count` column (v16
   migration) populated straight from the `/capture` response in `trigger_capture`, surfaced in
   `GET /api/observations`, and aggregated as `total_captures` / `dry_captures` in
   `GET /api/training/coverage`. Now "did this drive teach us anything?" is queryable without statting
   `.ax.json` files.
2. *An empty sidecar was silent.* When the tab is unreachable / node-ids are stale,
   `propose_ax_candidates` returns `[]` (it doesn't raise), so a sidecar with `proposal_count: 0` is
   still written — it **passes** the downstream `only_with_sidecar` existence check yet carries zero
   Select-training data (~15 of the 216 on-disk trace sidecars were like this — those are mostly
   runtime-loop artifacts, not DB rows). Fixed: emission now logs a **WARNING** (not INFO) on a
   0-candidate capture, and `dry_captures` counts them so the operator sees the real yield.

**Where it's encoded now.** `apps/controlplane-api/models.py` (`ax_candidate_count`),
`apps/controlplane-api/main.py` (`trigger_capture`, `training_coverage`, `list_observations`, v16
migration), `apps/mcp/app/main_server.py` (`POST /capture` empty-yield WARNING),
`apps/controlplane-api/scripts/backfill_ax_candidate_count.py` (one-time column backfill).

**Still open (deliberately not done).** The autonomous `run_live` loop writes on-disk artifacts +
sidecars but **no DB rows** — only `/api/capture` (with an active `TrainingSession`) creates queryable
`TrainingCapture` rows. So "every supervised task produces telemetry rows as exhaust" is only true for
the training-capture path today, not the autonomous loop. Wiring the runtime loop to auto-emit rows is
a real feature, deferred on purpose. Two dev/CLI paths (`debug_runner.py`, `run_observer`) also bypass
`/capture` and emit no sidecar — they're offline debug tools, left as-is.

---

## 2026-07-08 — Facebook login is fixed and lives on the AX layer; do not re-patch an endpoint

**What we believed / kept doing.** FB login broke ~weekly and each session reactively patched a bespoke
`/facebook_login` endpoint (hardcoded `querySelector` + coordinate click). `button[name=login]` broke
when FB shipped Log In as a `<div role=button>`; React-controlled inputs silently reset because a
per-char `dispatchKeyEvent` + native `.value` set didn't update React state. Each patch bought one more
week.

**What's actually true / what we did.** The bespoke endpoint was **deleted** (commit `6775499`,
2026-07-08). FB login now runs on the resilient **CDP-AX interaction layer** like everything else:
`/ax_scan` → `facebook_recipe.match_login_fields` (finds email/password/submit by **role +
accessible-name**, immune to `<div role=button>` because the AX tree normalises it to `button`) →
drive each node by `backend_node_id` via the humanized driver. The hard-won domain quirks
(button-is-a-div, React inputs need `Input.insertText`) are now **comments + logic in
`apps/controlplane-api/facebook_recipe.py`**, where the next session can see them — not re-litigated in
an endpoint.

**The meta-lesson (this is the important one).** Cross-session memory lives in **recipes and `docs/`**,
not in imperative endpoints. When a flow breaks, **first ask which interaction layer it's on** before
diagnosing fields or writing a one-off CDP script. See `PRINCIPLES.md` §6 and `interaction-layers.md`.

**Verified.** Live on `facebook_alt`: creds accepted → real 2FA gate; Marketplace reached via the
recorded `run_live` loop.

**Where it's encoded now.** `apps/controlplane-api/facebook_recipe.py` (`match_login_fields` + the
login-controls comment block), `apps/controlplane-api/channel_browser.py` (no more `login_path`),
`PRINCIPLES.md` §6, `interaction-layers.md`.

---

## 2026-07-12 — The apply cadence has an EPILOGUE: close the finished apply tab, refocus search

**What was missing.** The `targeted_search_and_apply` / `apply_triage` cadences drove a pick through
the apply flow and then jumped straight to "click pagination to the next page" — leaving the
newly-opened apply tab (smartapply for quick-apply, or the ATS host for cross-site) open. Over a
session that orphans a stack of apply tabs, and the loop never cleanly "returns to the search." There
was also no capability to close a tab at all; the bounds only said "never churn tabs."

**What's true / what we did.** Indeed opens the apply in a NEW tab. The human-natural epilogue —
finish (submit) OR abandon at a human-required wall (e.g. a Workday **account-creation gate** we
cannot create), record the outcome, then CLOSE that one apply tab and return to the search tab — is
now a first-class step:
- New MCP capability **`POST /close_tab`** (`apps/mcp/app/main_server.py`): closes a tab by id/url via
  the CDP HTTP endpoint (`/json/close/<id>`), optionally activates `focus_tab_url` (the search).
  SAFETY: refuses to close the control panel (`localhost:5173`) or the last remaining page tab.
- `search_cadence.py`: `BOUNDS.tab_hygiene` carves the single intentional close OUT of the "no tab
  churn" rule; the epilogue step added to both apply modes.
- `apply_recipe.py`: terminal `indeed_apply_submitted` action + new `APPLY_EPILOGUE` + the
  `account_creation` branch note now say "record → close apply tab → refocus search."

**The distinction that matters.** "No tab churn" forbids scraper-like opening/closing of many tabs to
browse. Closing the ONE finished apply tab to return to search is expected cleanup, not churn — a
human does exactly that. The bounds now say so explicitly.

**Verified.** Live on the Indeed session (port 9322): closed a completed smartapply `post-apply`
confirmation tab AND a Point32Health Workday `userHome` (account-wall, prospect #32) tab via
`/close_tab`, each refocusing `indeed.com/jobs` — ended on the single search tab, focused, where
triage left off.

**Where it's encoded now.** `apps/mcp/app/main_server.py` (`/close_tab`),
`apps/controlplane-api/search_cadence.py` (`BOUNDS.tab_hygiene` + both apply modes),
`apps/controlplane-api/apply_recipe.py` (`APPLY_EPILOGUE`, terminal step, `account_creation` note).

---

## 2026-07-12 — Applying is organized as Career-Search domain → ATS group (each ATS domain-like)

**The structure (defined live with the operator).** Applying is cross-site and was an unorganized
pile. It's now a taxonomy:
- **Career Search** = the domain CATEGORY for job engines (Indeed, LinkedIn, ZipRecruiter, …). "Indeed"
  isn't the domain; "career-search engine" is, and Indeed/LinkedIn/… are members. Where we SEARCH.
- **ATS group** = the third-party apply portals you hand off TO (Workday, iCIMS, Taleo, Greenhouse,
  Lever, SuccessFactors, …). Each ATS is treated like its OWN domain: its own recipe AND its own
  training-data bucket (captures tagged `domain_id=<ats_id>`, so rollups accrue per-ATS not per-company).

**Why per-ATS.** An ATS renders the same component library across every tenant (Workday's
`data-automation-id`s are identical for State Street / Takeda / Point32Health). So training
GENERALIZES across companies sharing an ATS. The **company→ATS map** (`ats_for_company` /
`record_company_ats`, persisted `cache/company_ats.json`) is the hook: the first time we drive
Company X's Workday we already reuse everything learned on every other Workday.

**Never auto-create an account.** ATSs with `auth: "account"` (Workday, iCIMS, Taleo, …) gate the
apply behind a per-employer candidate account — escalate to the operator (persistent pre-authed
profile), never sign up. Point32Health's Workday `userHome` account-wall (prospect #32) is the case
that motivated this; recorded as `Point32Health → workday`.

**Application preferences** are operator-owned notes attached to the career-search domain
(`application_preferences.py`, `cache/application_preferences.json`): a `structured` block (comp
target $130k, no sponsorship, 1–2 onsite days, decline demographics) + append-only `notes` (why a
role was skipped). The apply shortlister/filler reads these.

**Where it's encoded now.** `ats_registry.py` (CAREER_SEARCH + ATS_PLATFORMS + company→ATS store +
`classify_ats`), `application_preferences.py`, `routers/career_search.py`
(GET `/api/career_search/ats`, GET/POST `/application_preferences`, POST `/ats/company`),
`search_cadence.classify_apply_platform` now delegates to `ats_registry.classify_ats` (one source of
truth). Verified live: endpoints return the registry; Point32Health shows under Workday; both
session exclusions (Knipper Sr BI, Fidelity Alt-Investments) recorded as preference notes.

---

## 2026-07-12 — Account-walled ATS jobs: build the accounts system, pause at CREATION (don't skip)

**What was wrong.** Account-gated ATS applications (Workday/Phenom/iCIMS/… candidate-account walls)
were being SKIPPED as "can't, unsafe." The operator was right that this is wrong — it drops jobs they
want. The safety rule only forbids a narrow act (the agent typing a password into a site or submitting
an account creation/login), not organizing accounts or generating credentials.

**The workaround (built + verified end-to-end).** Company-first ATS accounts:
- `ats_accounts.py` on top of the existing `accounts.py` vault. `derive_password("U.S. Bank National
  Association")` → INITIALS "USBNA" (first letter of each token, splits on spaces AND punctuation) +
  a shared suffix in gitignored `.env` (`ATS_ACCOUNT_PW_SUFFIX`); username `ATS_ACCOUNT_USERNAME`
  (genomags@gmail.com). `ensure_account(company, ats_id)` registers a company↔ATS login as `pending`.
- Endpoints: `/api/career_search/accounts{,/ensure,/credentials}`. New top-level **Accounts** UI tab
  (`AccountsSection.jsx`), company→ATS, reveal generated login, Save login (→vault), operator ▶ Login.
- `accounts.py`: `_STATUSES` += "pending"; `_EDITABLE_KEYS` += company/ats_id/username_hint.
- New ATS registered from live intake: **Phenom** (careers.<co>.com; U.S. Bank → careers.usbank.com).

**The boundary (unchanged, load-bearing).** The agent GENERATES + ORGANIZES credentials and drives up
to the signup/login form. The agent does NOT type a password into a site or submit account
creation/login — the OPERATOR does that one step (the "pause at the creation point"), then automation
resumes. This is the honest line: build everything, pause at the keystroke, never refuse-and-skip.

**Where it's encoded now.** `apps/controlplane-api/ats_accounts.py`, `accounts.py` (pending status +
keys), `routers/career_search.py`, `apps/controlplane-ui/.../AccountsSection.jsx` + `navigation.js` +
`App.jsx`, `.env` (ATS_ACCOUNT_USERNAME / ATS_ACCOUNT_PW_SUFFIX, gitignored).

---

## 2026-07-12 — Workday account lifecycle: create-account recipe + sign-in leg = one loop

**What.** A per-employer Workday login is CREATED before it can sign in, so the account has a
lifecycle STATE and the button differs by state: `needs_creation`/`pending` → "Create Account";
`active` → "Sign In". Built both legs as DATA recipes (by accessible name, churn-immune AX layer),
verified against U.S. Bank's live Workday tenant:
- `WORKDAY_CREATE_ACCOUNT_RECIPE` — fields Email Address / Password / Verify New Password /
  acknowledge-checkbox → "Create Account"; honeypot ("Enter website… for robots only") NEVER filled.
- `WORKDAY_SIGN_IN_RECIPE` — Email + Password → "Sign In".
- `WORKDAY_ACCOUNT_LOOP` + `ats_accounts.next_account_action()` pick the leg from the account status;
  then hand to `WORKDAY_APPLY_RECIPE`. Endpoints: `/api/career_search/accounts/next-action`,
  `/mark-created`; recipes on `/api/runtime/apply_recipe`.

**The point.** create-account → sign-in → apply is ONE loop the (future, operator-run) **Account
Manager** executes so the operator doesn't manage it. BOUNDARY unchanged: these recipes are DATA;
they're run by the operator-triggered Account Manager / the operator, NEVER the agent's own loop —
the agent never types passwords into a site or submits account creation/sign-in.

**Also fixed:** `close_tab` now refuses to close a different tab when a specific tab_id/tab_url was
given but doesn't match (a truncated id fell through to closing the wrong tab live).

---

## 2026-07-12 — Career Search parent domain + Accounts moved in + operator "Create account"

**UI/domain restructure.** Domains is now hierarchical: **Career Search** (`kind: "group"`) is the
parent domain; the job engines + ATS (Indeed, LinkedIn, Workday) are its `children` and declare
`parent: "career_search"` so they nest (hidden from the top-level hub, shown inside Career Search's
"Sub-domains" tab). The top-level **Accounts** nav was REMOVED — the company-first `AccountsSection`
now lives in Career Search's **Accounts** tab (`GroupWorkspace` in DomainWorkspace.jsx renders the
group: no Status/Automation shell, just Sub-domains + Accounts). Files: `workspace/domains.js`,
`DomainWorkspace.jsx`, `DomainsHub.jsx` (filter out `parent`), `App.jsx` (pass onOpenDomain, drop
accounts route), `navigation.js`.

**Operator "Create account" executor.** `POST /api/career_search/accounts/create-account` — the
create leg of the account loop, built on the SAME operator-triggered pattern as the existing
`/api/accounts/{id}/login`: resolves the GENERATED credential server-side (never returned), scans the
live Workday Create-Account form, fills Email/Password/Verify + the acknowledge checkbox (SKIPS the
bot honeypot), clicks Create Account, stores creds in the vault + marks the account active. UI: a
"+ Create account" button on pending accounts. BOUNDARY: runs ONLY on the operator's button press —
the AGENT must never call it from its own tool-loop (never creates accounts / enters creds itself).
Also: deleted the stale U.S. Bank→Phenom account (Workday is the real apply backend).

**Rail nesting refinement.** The Domains SIDEBAR rail (not just the hub) is now hierarchical: only
top-level domains show at the "All Domains" level; Career Search always expands to its nested
Indeed / LinkedIn / Workday + a 🔐 Accounts item (`App.jsx` flatMap over `!d.parent`, `openDomainTab`).
Each sub-domain's own Accounts tab shows the company-first accounts filtered to THAT ATS
(`AccountsSection atsFilter=domain.id`); Workday now has an Overview + Accounts tab.

---

## 2026-07-12 — Event console: a cross-process feed of what the system did

**Gap.** Direct MCP drives (my bash curls to :8082) left no visible trace — the operator couldn't
tell at a glance that the system worked or was being used/trained. Nothing tied the two processes
together.

**Built.** A shared append-only JSONL event log in the dir both processes already share
(`observer_artifacts_dir` == `../mcp/output`): `apps/mcp/output/cache/event_log.jsonl`. Both write it
best-effort (never raises into a caller); the control plane serves it.
- `controlplane-api/event_log.py` + `mcp/app/event_log.py` (parallel writers, same file),
  `routers/events.py` (GET/POST `/api/events`).
- Instrumented: MCP `/capture` `/execute` `/close_tab` `/navigate`; control-plane account
  create/ensure/mark-created. Events: `{ts, source, kind, summary, detail, domain}`.
- UI: `EventsConsole.jsx` — live feed (source/kind badges, relative time, filter, pause) added as the
  **Activity** tab in the Career Search domain. Verified: MCP captures + control-plane apply/account
  events show together, auto-refreshing.

---

## 2026-07-13 — Driving a Workday PII form + the capture/label loop (live, U.S. Bank)

Drove Workday "My Information" for a FRESH account (not prefilled). What worked / bit us:

- **Capture→label loop:** control-plane `POST /api/capture {training_session_id, tab_id, tab_url}` makes
  a labelable `TrainingCapture` (MCP-direct `/capture` does NOT — its artifact 404s the label PATCH).
  Then `PATCH /api/observations/{filename}` `{observed_page_state}`. GOTCHA: the ISO-timestamp filename
  contains `+`; it MUST be URL-encoded (`%2B`) in the PATCH path or you get 404 / silent no-op.
  Labeled 2× `workday_my_information` (empty + filled) via session 16.
- **Use the `direct` driver for ATS form fills, not `humanized`:** humanized (wiggly mouse + cadence
  typing) is ~15-20s/field → times out at ~5 fields, and it TRUNCATED a value ("46 Canterbury Rd" →
  "46 Cante"). direct (Input.insertText) is ~1-2s and reliable. Bot-safety matters far less on a
  signed-in ATS than on the engine.
- **CLEAR before (re)typing** — a re-fill without clearing DOUBLED the postal code ("0330103301").
  Always verify text fields by value after filling (screenshot / `/locate` returns the value).
- **Act-by-name substring pitfall:** `target_name="State"` matched the **Country** field
  ("United **State**s…"); use the fuller name ("State Select One"). Single-select listbox dropdowns
  work (click field → click the option by exact name, e.g. "New Hampshire").
- **`How Did You Hear About Us?` nested prompt = confirmed gap** — clicking it surfaces no CDP-visible
  options; route to the operator (Online Source → Indeed), as WORKDAY_LESSONS said.

---

## 2026-07-13 — Reusable action for Workday prompts (`/select_prompt`) + stale-session validation

The nested-prompt gap ("How Did You Hear About Us?") is now a reusable atomic action, the prompt
analogue of human_click/human_type. `POST /select_prompt {field_name, value}` (apps/mcp):
1. **Open** the field with a NATIVE node-click (same path /execute uses) — a trusted-mouse-at-box-
   center did NOT reliably open the popup.
2. If the popup has a `input[data-automation-id=searchBox]`, type the value with **TRUSTED per-char
   key events** — Workday fetches prompt results SERVER-SIDE on real keystrokes; a react-safe
   value-set or `Input.insertText` does NOT trigger the fetch (confirmed via the new `/eval` debug
   endpoint: value became "Indeed" but zero options loaded).
3. **NATIVE-click** the matched option by accessible name — coordinate/`_trusted_click` on the
   option mis-fires on long/virtualized lists (picked "American Samoa" for "New Hampshire").

**Validated live on the State field** (State → New Hampshire, verified "State New Hampshire Required").
Caveats baked into WORKDAY_LESSONS.prompt_action: pass a PRECISE field_name (the accessible name
embeds the current value, and short names collide — "State" matches "United States"); a STALE session
silently returns NO options (the whole reason this looked broken for an hour — the Workday tab had
logged out; reload + re-auth first, per PRINCIPLES §1).

Also: added `POST /eval` (run JS in the tab, return value) as a dev tool for building/tuning actions.

## 2026-07-14 — Drove the U.S. Bank Workday apply through Application Questions; date-widget + transient-error + relogin lessons

Continued the live U.S. Bank (`usbank.wd1.myworkdayjobs.com`) Trust Reporting Analyst application on
session 16 (port 9322). Signed in (operator ▶ Login), then drove **My Information → My Experience →
Application Questions** to completion. What bit us / what to bake in:

- **Workday DATE fields (`dateSectionMonth-input`/`dateSectionYear-input`, role=spinbutton) do NOT accept
  typed input.** Both drivers failed: DirectDriver's `Input.insertText` and HumanizedDriver's per-char
  `char` events + react-safe set BOTH left the field *displaying* the value ("03/2026") while Workday's
  validation model stayed empty → "The field From is required and must have a value" on Save. Same class
  as the `/select_prompt` finding: Workday commits only on **trusted events**. **The reliable path is the
  CALENDAR PICKER**: click the field's "Calendar" button → it opens a **month grid** (year nav `< 2026 >`,
  Jan–Dec buttons) → click the month (trusted click registers). Verified: picking "Mar" set 03/2026 and
  cleared the required-error. Bake into the Workday recipe: dates = calendar-picker clicks, never typed.
  (Plain text inputs — Job Title/Company/Location, and free-text `textarea` like the "N/A" discharge box —
  DO accept DirectDriver `type`; only the segmented date/prompt widgets need trusted events.)
- **Workday throws a transient "Something went wrong — Please refresh the page and then try again."** at
  step transitions (hit it after sign-in→My Information AND after Application-Questions-save→Voluntary-
  Disclosures). It's a real, recurring page STATE with a deterministic recovery: **refresh**. Labeled it
  `workday_error_retry` (2 examples; post_action = whatever step was pending). Recognize→refresh, don't
  treat as a dead end.
- **A hard-navigate refresh can DROP the Workday session** (log you out). The `/navigate` (Page.navigate to
  the same URL) recovery worked the first time but the SECOND refresh returned us to "Start Your
  Application" **logged out** (top-right flipped account-email → "Sign In"). So Workday sessions here are
  short-lived / fragile: **completed steps persist on the candidate account** (My Info/Experience/Questions
  stayed ✓ and "Use My Last Application" is offered), but you must **re-auth** to continue. Next time try a
  SOFT reload (`location.reload()` via `/eval`) instead of a hard `Page.navigate` for the "Something went
  wrong" recovery — it may preserve the SPA session. Two logouts in one session also hints the rapid
  automated activity may be shortening the session; pace it.
- **Application Questions is a long compliance questionnaire (~16 listbox dropdowns + 2 textareas), all
  dropdowns share accessible name " Select One Required"** (can't target by name — target by
  `backend_node_id` from a fresh `ax_scan`, in DOM/y order = question order). Node ids were STABLE across
  single selects here (the earlier "reset" scare was a bad `textContent` verify read — **verify dropdown
  state by SCREENSHOT, not `button.textContent`**, which doesn't reflect the selection). DirectDriver
  `action_id:"select"` with an exact-substring `value` ("Yes"/"No"/"$75,000-$89,999") is reliable
  one-call-per-dropdown. A batch loop over ~14 selects TIMED OUT the bash tool — do them in batches of ~5.
- **L3 states captured+labeled this session** (all via control-plane `/api/capture` → PATCH, filename `+`→
  `%2B`): `workday_sign_in` (new — recipe-referenced but 0 examples), `company_careers_job_posting` (new —
  the Phenom careers.usbank.com posting w/ AI chatbot, the funnel step before the ATS), `workday_error_retry`
  (×2, new), `workday_my_information` (signed-in prefilled variant), `workday_my_experience` (empty + filled),
  `workday_questions` (empty + filled). Answers were operator-confirmed: work-auth Yes / sponsorship No /
  background-check + bonding acks Yes / willing-to-work-location Yes / desired comp **$75k placeholder**
  (operator will clarify the real number; their standing target is $130k) / all other screening = No / the
  "ever discharged/terminated" required free-text = "N/A".

## 2026-07-15 — Indeed's hidden decoy job cards + a job count that was really a filter badge

Ran a fresh `reporting analyst` / Nashua NH / 50mi search on session 16 and found two bugs in
`extract_jobs` (`apps/mcp/app/main_server.py`) — both fixed + verified live (18 rows → 17 real ones).

- **Indeed plants HIDDEN 0x0 decoy job cards.** A `[data-jk]` anchor (`id=job_fedcba9876543210`,
  `offsetParent === null`, 0x0 rect) sits alongside the real cards and extracted as a phantom job with
  an empty company/location. The hex jk looks like fixture data but is **Indeed's own** — it shows up in
  a real 07-14 capture trace. This is the SAME trap as smartapply's width-0 duplicate Continue button:
  **only ever trust the VISIBLE node**, in extraction as well as in driving. The extractor now filters
  on `offsetParent !== null && rect.width/height > 0`. A phantom in the shortlist is not cosmetic — it
  could burn an apply on a job that doesn't exist.
- **`meta.total_results` was reporting `1` for a full page.** Indeed no longer renders a job-count
  element at all (every count selector returns null), so the regex fallback ran against
  `body.innerText` — and `\s` spans newlines, so the filter chips "Distance\n**1**" + "**Job** Type"
  matched `/[\d,]+\+?\s+jobs?\b/i` as "1 Job". The count was the Distance badge. Fallback now uses
  `[^\S\n]` (no newline crossing) and requires plural "jobs"; **null is the honest answer** when the
  page shows no count — don't scrape a number off a badge. Anything recording `total_results` per query
  (targeted_search_and_apply) was recording a 1.

Also: **re-running a search via the Search button DROPS `radius` from the URL** — the distance filter
does not survive a re-query, so re-apply it after every search. `/set_distance` reported
`method: "url_fallback"`, i.e. the human widget path (trusted-mouse open of the distance pill) did NOT
open the menu and it fell back to the same-tab `radius=` rewrite. The cascade did its job, but the
preferred human path is silently degrading and is worth a look before it's the only thing left.

## 2026-07-15 — The distance pill: a STAGED-COMMIT widget, and why "the human path" kept losing

`/set_distance` had been silently reporting `method: "url_fallback"` — the human widget path never
worked. Root-caused and rebuilt on the live Indeed session; it now reports `method: "widget"`,
verified across 25 / 35 / 15 / 100 / 50. **Five rules, each of which cost a failed attempt, and all
of which should generalize to Workday + the unknown ATS popups:**

1. **A popup will NOT render in a hidden tab.** `document.visibilityState` must be `visible`
   (`Page.bringToFront`) or the opener click no-ops. This alone explains why the same code "worked"
   from a probe (which fronted the tab) and failed from `/eval` (which doesn't). A human's tab is
   visible when they click — foregrounding IS the humane path, not a trick.
2. **`.click()` does not FOCUS.** A real mousedown focuses; the synthetic one doesn't. Without focus
   the widget's keyboard protocol is dead — `activeElement` stayed `BODY` and arrows did nothing.
   `focus()` THEN `click()`, and the listbox takes focus + `aria-activedescendant` moves properly.
3. **The popup dismisses on BLUR → it cannot survive HTTP round-trips.** Every separate `/eval` call
   lost the menu. open→select→commit must run page-side in ONE evaluation.
4. **Selecting only STAGES the value — the footer's `Update` button commits it.** This was the actual
   bug, and nothing in the DOM/AX said so: the popup has Reset/Update buttons that only a SCREENSHOT
   revealed. It's why the old fiber-prop hack looked "invoked" yet nothing ever applied.
   (Cf. [[feedback_confirm_state_with_screenshot]] — the DOM lied by omission; the picture didn't.)
5. **The commit DESTROYS ITS OWN OBSERVER.** `Update` triggers a full navigation, so page-side code
   can't see its own result — `"Inspected target navigated"` IS the success signal. Confirm from
   OUTSIDE (read radius off `/json/list`).

**What was wrong before, and the general lesson.** The old path did a trusted-mouse click at the
pill's box centre (coordinates go stale the instant the menu re-renders — it landed outside and
*dismissed* the popup) and then invoked the option's React fiber props (Indeed's internals moved on).
Both are the two failure modes we already reject: coordinates, and reaching into a framework's guts.
Neither is needed. **Identify by ARIA/CSS semantics, drive natively, confirm every step** — that IS
the in-betweener layer, and it's now `_POPUP_SELECT_JS` + `_popup_select()`, config-driven
(`opener_selector` / `option_selector` / `option_label` / `commit_names`) so Workday and unknown-ATS
popups can reuse it instead of growing another bespoke path.

**The url_fallback is now OFF by default** (`allow_url_fallback=false`). A silent fallback is exactly
how a fully broken widget path went unnoticed for weeks: every caller still got its radius, so nobody
learned. A widget break must be LOUD. The URL is CONFIRMATION, never the mechanism.

Also worth knowing: `set_distance` is a FLOOR (`min_miles`), so it short-circuits `already` when the
current radius is larger — to exercise the widget you must start below the target.

## 2026-07-15 — Multi-tab capture was silently capturing the WRONG page (corpus-poisoning)

Captured the Wellington Workday create-account wall (tab 2) while Indeed (tab 1) was `[selected]`,
and got back a perfectly healthy-looking artifact **of the Indeed page** — which I then labelled
`workday_create_account`. A confidently mislabelled example is worse than no example: it teaches L3
that the Indeed SERP *is* a Workday account wall. Deleted it (`DELETE /api/observations/{filename}`),
root-caused, fixed, verified. **`tab_id` had never worked.**

**Root cause.** `list_pages` (chrome-devtools-mcp) returns a **dict**, not a list:
`{"raw_text": "## Pages\n1: <url> [selected]\n2: <url>"}` — a 1-based INDEX + URL, and **no CDP
targetId at all**. `_select_tab` did `pages = payload if isinstance(payload, list) else []`, so it
always parsed to `[]`, hit `if not pages: return`, and silently captured whatever page was
`[selected]`. Then `_verify_target_tab` saw the URL mismatch and — by design — "warned but didn't
block". Two silent failures in a row produced confident garbage. (This is the *same shape* as the
`set_distance` url_fallback: a fallback that always yields a plausible answer hides a dead path
forever. See the staged-commit entry above.)

**Fixed** in `apps/mcp/app/main.py`:
- `_parse_pages()` parses the real `raw_text` format (still tolerates a list payload).
- `_select_tab(session, tab_id, tab_url)` pins by **URL** — the only handle list_pages gives us —
  and returns whether it actually pinned. Ambiguous (>1 URL match) → refuse, don't guess.
- `_verify_target_tab(..., tab_pinned)` now **RAISES** on a URL mismatch when the tab wasn't pinned.
  A mismatch is only tolerable when we positively pinned by id/URL (then it's just a redirect).
- Verified: capturing tab 2 while tab 1 is `[selected]` now yields
  `page_identity.title = "Financial Reporting Analyst, US Funds"` @ wellington.wd5.myworkdayjobs.com.

**Consequences.** This closes the standing "multi-window/tab captures carry no window identity"
gap — but note **`tab_id` is decorative for capture**: pass a `tab_url` distinctive enough to match
exactly one page (a bare `indeed.com` will match several once an ATS tab is open). Any capture taken
of an ATS/second tab BEFORE this fix should be treated as suspect — it is probably a picture of
whichever tab was selected, not the one requested.

**The rule this keeps re-teaching:** *never let a fallback quietly substitute a plausible result for
the real one.* Fail loud, or the flywheel eats the lie.

## 2026-07-15 — The Workday create-account leg timed out mid-fill (a lesson that never propagated)

Operator pressed "+ Create account" for Wellington: the creds went in, then the UI said the driver
was unreachable and **Create Account was never clicked**. Event log tells it exactly:

```
17:58:40 type node 578  ok      <- email
17:58:42 type node 579  ok      <- password   (2s)
17:59:53 type node 580  ok      <- verify     (71s!)
(nothing — no click on submit)
```

**Root cause: `"driver": "humanized"` in `create_account_on_site`.** Humanized cadence-types at
~15-20s/field on Workday, so the third field blew the `httpx.AsyncClient(timeout=60.0)` → `HTTPError`
→ 502 "Create-account driver unreachable" — *after* filling, *before* submitting. The
**2026-07-13 entry already said this** ("use the `direct` driver for ATS form fills, not humanized —
~15-20s/field, times out at ~5 fields, and it TRUNCATED a value"), and the AppVault leg written the
next day used `direct` *with a comment explaining why*. Nobody went back and fixed the Workday leg.

**This is the third time the same shape has bitten us in two days:**
- `/select_prompt` learned "open with a NATIVE node-click, not trusted-mouse" → never reached
  `set_distance`, whose widget path was dead for weeks.
- AppVault learned "direct, not humanized" → never reached the Workday leg.
- AppVault learned "VERIFY it advanced before marking created" → never reached the Workday leg, which
  called `mark_created()` purely because the click returned. A stalled form (bad password, unticked
  ack) would have left a phantom `active` account with no login behind it.

**A lesson recorded in ONE call-site is not a lesson learned.** When you fix a leg, grep for its
siblings *in the same file* and fix them together, or the next session pays for it again.

**Fixed** (`routers/career_search.py`): Workday leg now uses `direct`, CLEARs before typing (`type`
appends — it once doubled a postal code to "0330103301"), and **verifies the form actually advanced
before `mark_created`**, returning `not_advanced` + leaving the account PENDING otherwise.

Also: **the signed-in signal on Workday is the account email in the header** ("Settings
genomags@gmail.com" / "Candidate Home"), NOT a "Sign Out" button — a `sign out` text probe returns
false while signed in.

UI: the accounts table rendered a separate `<table>` per company, each auto-sizing its own columns,
so no two company cards lined up, and five buttons on one `white-space: nowrap` row pushed the
actions clean off the right edge (the operator couldn't reach "+ Create account" without scrolling
sideways). Now `table-layout: fixed` + a shared `<colgroup>`, and the actions stack
primary → utilities → destructive. Verified: both tables measure identically, no horizontal scroll.

## 2026-07-15 — Workday widget layer generalizes; the SESSION is the real enemy (Wellington)

Drove Wellington's Workday My Information end-to-end (all 7 text fields + 4 widgets, verified by
value), hit Save and Continue → **4× opaque `Error - Page Error / VPS|<uuid>`** and a DISABLED Save
button. `/challenge_visibility` first (per the rule): no captcha. Then the recovery bit us.

- **A SOFT reload does NOT preserve the Workday session — this DISPROVES the hypothesis in the
  2026-07-14 entry** ("try `location.reload()` instead of Page.navigate — it may preserve the SPA
  session"). It doesn't. Both hard and soft refresh drop you to logged-out. **Refreshing to "recover"
  made things strictly worse**: we lost the whole fill AND the session. Do NOT reflexively refresh a
  Workday error — re-auth is the recovery, and the fill should be re-done after.
- **The 4 page errors were almost certainly an EXPIRED SESSION, not field validation.** No field-level
  errors, all 4 generic + server-side, and the reload revealed we were already logged out. Read
  `Page Error VPS|…` on save as "session is gone", not "your data is bad".
- **The step-count tell WORKS and caught it**: `current step 1 of 7` = the account step is back = you
  are LOGGED OUT; `1 of 6` = signed in, apply spine only. Cheap, deterministic, no screenshot needed.
  (Signed-in signal is the account email in the header — NOT a "Sign Out" button.)
- **Workday sessions are short under rapid automated activity — PACE IT.** Third logout in two
  sessions. Fill fewer fields per unit time and Save EARLY (each saved step persists on the candidate
  account, so a drop costs one step, not the whole application).

**The widget layer generalized — one protocol, three widget kinds** (`/widget_select`, new):
- Indeed distance pill: staged-commit (footer `Update` commits) — `commit_names:["Update"]`.
- Workday listbox (State, Phone Device Type): applies on select, no footer → `commit: found:false`.
- **Scope options via `aria-controls`/`aria-owns` on the opener.** Workday pages carry stray
  `[role=option]`s from OTHER fields (63 document-wide vs 5 scoped) — matching option text globally
  can click the wrong widget's option. `aria-controls` is the widget telling you which popup it owns.
- **BUG found + fixed: never infer "already open" from an option count.** Pre-open there is no
  `aria-controls`, so the scope falls back to document, counts another widget's strays, concludes the
  popup is open, and SKIPS THE CLICK. Open-state must come from the opener's `aria-expanded`.
- **Confirm staged via EITHER `aria-selected` OR the opener's label changing** — neither is universal
  (Indeed uses the first, Workday's dropdowns the second; and per 2026-07-14 some Workday dropdowns'
  textContent lies, so keep both signals).
- Workday's real field handle is its own `data-automation-id` (`formField-legalName--firstName`,
  `formField-addressLine1`, …) via `/execute`'s `selector` — stable, semantic, Workday's own contract.
  `scan_form` is NOT usable here: every field returns the whole fieldset's text as its label, so
  First/Middle/Last are indistinguishable.

**Per-TENANT variation is real — do not hardcode a prompt's options:**
- "How Did You Hear About Us?" on Wellington is a FLAT list with **no Indeed and no "Online Source"**
  (Career Site - eFinancial/Glassdoor/Wellington, Diversity Association, Other, Previous Employee or
  Consultant, Recruiting Agency). U.S. Bank had the hierarchical Online Source → Indeed. Operator
  chose **Other** (the only truthful option — we came from Indeed, which isn't offered).
- Its options are plain `div`s with **no `role=option`**, and `/select_prompt` found no `searchBox`
  here, so the searchbox-typing path is tenant-specific too. `/select_prompt`'s `field_role` defaults
  to `textbox`, but Wellington's prompt field is a **button** — pass `field_role` explicitly.

## 2026-07-15 — FIRST FULL WELLINGTON WORKDAY APPLICATION SUBMITTED (end-to-end, agent-driven)

**Financial Reporting Analyst, US Funds · Req R94007 · "Under Review" · submitted July 15 2026** —
confirmed in Workday's own My Applications (Active 1). Operator created the account + pressed Login;
the agent drove every step after. All 6 steps captured + labelled (`workday_my_information`,
`workday_my_experience`, `workday_questions`, `workday_voluntary_disclosures`, `workday_self_identify`,
`workday_review`, plus `workday_error_retry`).

**The re-fill took 1.5s for 11 fields** (7 text + 4 widgets) vs the 71s/field that blew the timeout
before. Identical data, saved clean on the first try — which retroactively **confirms the 4×
`Page Error VPS|…` was a DEAD SESSION, not bad data**. Read it that way next time: re-auth, don't
refresh, don't re-diagnose the fields.

**Workday widget protocols that now work (all via `/widget_select` or the same shape):**
- **Text fields** → `[data-automation-id="formField-<name>"] input` + `/execute`'s `selector`, CLEAR
  then `direct` type. Workday's `data-automation-id` is its own stable contract — use it, not labels.
  (`scan_form` is useless here: every field returns the whole fieldset's text, so First/Middle/Last
  are indistinguishable.)
- **Listbox dropdowns** (State, Phone Device Type, veteran, gender) → `/widget_select`, scoped by
  `aria-controls`. Applies on select, no footer commit.
- **MONTH picker** (`formField-startDate`, MM/YYYY) → typed input NEVER commits. Open `dateIcon`
  (aria=Calendar) → tiles are `monthPickerTileLabel` whose **aria-label carries "March 2026"**
  (month AND year — so year nav is self-verifying via `monthPicker{Left,Right}Spinner`) → click the
  tile → confirm from `dateSection{Month,Year}-display`. **Proof it committed: Save produced no
  "field is required" error**, which is exactly how typed input fails.
- **DAY picker** (`formField-dateSignedOn`, MM/DD/YYYY) → same opener; today's tile carries
  `data-automation-id*=datePickerSelectedToday` and aria-label `"Selected Today Wednesday 15 July 2026"`.
- **Checkbox groups** (ethnicity, disability, consent) → click the LABEL by its exact text, then
  verify `input.checked`.
- **Resume upload** → `/execute` `action_id:"upload"` + `selector:"input[type=file]"` +
  absolute path (DOM.setFileInputFiles; a click would open an OS dialog CDP can't drive). Confirmed
  by the page's own "GM_Resume.pdf successfully uploaded".
- **Conditional fields are real**: ticking "I currently work here" REMOVES the End Date field.

**Per-TENANT variation is the rule, not the exception** — the same logical field differs across
Workday tenants, so recipes must describe SHAPE, not fixed options/ids:
- Application Questions' `data-automation-id`s are **opaque hashes** (`formField-c6b3456dfd0a…`) —
  no semantic handle at all. Target by QUESTION TEXT.
- "How Did You Hear About Us?" — U.S. Bank: hierarchical, searchBox, Online Source → Indeed.
  Wellington: FLAT, plain `div` options (no `role=option`), **no searchBox, and no Indeed at all**.
  `/select_prompt`'s `field_role` defaults to `textbox`; Wellington's is a **button**.
- Gender offered **only Female/Male — no decline option**, while veteran ("I do not wish to
  self-identify") and ethnicity ("Choose Not to Disclose") both had one. A blanket "decline
  demographics" preference CANNOT be honoured everywhere — surface the constraint and ask.
- Ethnicity granularity: tenants split Asian into Central/East/South/Southeast/West/Other, so a
  stored `race_ethnicity=Asian` is too coarse to map. Saved `ethnicity_detail` to stop re-asking.

**Answers reused from the store** (full_name, street_address, city, state, postal_code, phone,
phone_device_type, resume_job_1_*) — the store IS the reuse mechanism; four new answers were written
back (primary_language, additional_languages, ethnicity_detail, current_employer). NOTE `todays_date`
in the store was stale (06/28) — a signature date must be computed, never read from the store.

## 2026-07-15 — The apply EPILOGUE is now a real step, not prose (tab hygiene + recording)

`APPLY_EPILOGUE` described "record the outcome, close the apply tab, refocus search" and MCP
`/close_tab` was the primitive — but **nothing ever wired them**. So every finished apply left an
orphan ATS tab and an unrecorded outcome: the loop couldn't distinguish "applied" from "still open",
and tab cleanup was manual. Now **one call**: `POST /api/career_search/apply/epilogue`.

- **RECORD before CLOSE.** A failed close still leaves the outcome known; a closed tab with no record
  is unrecoverable. The endpoint commits the prospect first, then does tab hygiene, and reports both.
- **Upserts the prospect**, so an epilogue works even for a job that was never extracted into
  `observed_jobs` (our Wellington apply was driven straight off a card click).
- Runs on EVERY terminal, same shape: `applied` (CONFIRMED submitted — the only status that stamps
  `applied_at`) / `abandoned` (stopped at a human-required wall; stays resumable) / `skipped`.
- Records the ATS provenance the corpus needs: `application_platform=workday`,
  `apply_type=company_site`, `tenant_id=<req id>` — so "which ATS did this actually go through" is
  answerable later, per company.
- **Verified live**: Wellington · req R94007 → recorded `applied` @ 19:39Z, closed the Workday tab,
  refocused the Indeed search, `remaining_tab_count: 1`.

Standing rule this encodes: **the loop must end each prospect on a clean single-tab search.** Tab
hygiene isn't tidiness — orphan ATS tabs are what made `tab_url` matching ambiguous for capture
(a bare `indeed.com` matches several pages once an ATS tab is open; see the capture entry above).

## 2026-07-15 — Cross-origin ATS iframes are driveable targets; branded wrappers lie about the ATS

Job #2 (KKR · Analyst - Actuarial Financial Reporting) routed to
`www.kkr.com/careers/...?gh_jid=5995076004` — **Greenhouse behind a branded wrapper**, with the real
form in an embedded `job-boards.greenhouse.io` iframe. Three findings:

- **`classify_ats` said `company_site`** because it only matched the HOST. We'd have grown a bespoke
  KKR path for what is plainly Greenhouse, and the company→ATS map would have learned the wrong
  thing. Fixed: host → **query-param tells** (`gh_jid`/`gh_src`→greenhouse, `lever-origin`, `jvi`) →
  optional `page_hints={"embed_hosts":[…]}`. The param is the ATS leaking its identity through the
  wrapper — cheap and it generalizes to every employer on that ATS. (Workday's wrappers are caught by
  the APPLY-NOW href; same shape.)
- **`_discover_target` filtered to `type=='page'`, so OOPIF iframes were undriveable.** A cross-origin
  embedded form is its own attachable CDP target (`type=iframe`, has a webSocketDebuggerUrl) — the
  main frame's Runtime cannot see inside it. Now included, addressable by explicit id/url only (never
  a default pick). Greenhouse/Lever embeds are common, so this unlocks a whole class of applies.
- **It silently fell back to the FIRST page when `tab_url` matched nothing** — so I evaluated against
  kkr.com believing I was in the Greenhouse iframe, and it looked fine. **Same disease as the capture
  bug and the set_distance url_fallback.** Now: no match → RAISE (listing the open targets);
  ambiguous → prefer a real page, else refuse. Immediately proved its worth —
  `job-boards.greenhouse.io` matched 2 targets (the form + a googleapis proxy iframe with
  "greenhouse" buried in a query param), and it refused to guess instead of driving the wrong one.

**That's now FOUR bugs of one shape in a day** (silent url_fallback, silent wrong-tab capture, silent
"already open" from stray options, silent wrong-target discovery). The pattern: *a fallback that
always produces a plausible answer hides a dead path forever.* When adding a fallback, ask what a
caller sees when the primary silently fails — if the answer is "success", it's the wrong fallback.

Also: Greenhouse embeds need **NO account** (`needs_account:false`) — no wall, unlike Workday. Its
form has clean semantic ids (`first_name`, `email`, `resume`, `candidate-location`, `company-name-0`,
`start-date-month-0`). reCAPTCHA Enterprise IS present in the iframe's frame tree (invisible /
score-based, not blocking) — note `/challenge_visibility` run against the PAGE reports
`anchor_count: 0` because the captcha lives in the iframe; check the iframe target for it. Humanized
input matters here to keep the score healthy.

## 2026-07-15 — Greenhouse STUBBED as a sub-domain (and 11 orphan labels finally registered)

Before driving KKR's Greenhouse form we stubbed the ATS out, so its captures label as `greenhouse_*`
and generalize across EVERY Greenhouse employer instead of teaching us something KKR-shaped.

**Found while stubbing: the `workday_*` labels were ORPHANS.** 62 page states were registered and
**not one of them was Workday**, yet `workday_my_information` etc. had been applied to ~20 captures.
`observed_page_state` is free text — the PATCH accepts anything — so the labels trained fine but the
registry (which drives the coverage view + label queue) had never heard of them. **A label that isn't
registered is invisible to the thing that tells you what to go capture.** Registered all of them
(create_account, sign_in, my_information, my_experience, questions, voluntary_disclosures,
self_identify, review, error_retry, application_submitted) + `indeed_did_you_apply`.
Still orphaned and left alone (another session's): the `fb_*` listing states, `appvault_login`,
`company_careers_job_posting`.

**Greenhouse stub** — domain `greenhouse` (hosts greenhouse.io / job-boards / boards) + 4 states:
`greenhouse_apply_form`, `greenhouse_apply_submitted`, `greenhouse_apply_error`, `greenhouse_captcha`;
UI sub-domain under Career Search (🌱, sibling of Workday); `GREENHOUSE_APPLY_RECIPE` +
`GREENHOUSE_ACCOUNT_LOOP` + `GREENHOUSE_LESSONS` wired into `recipe_spec().cross_site`.

**What generalizes vs. what doesn't** — the distinction the recipe encodes:
- GENERALIZES: the standard form (`#first_name`, `#last_name`, `#email`, `#phone`, `#country`,
  `#candidate-location`, `#resume`, `#cover_letter`, `#company-name-0`, `#title-0`,
  `#start-date-month-0`/`-year-0`), the APPLY button, no-account, the iframe embed.
- DOES NOT: the per-employer CUSTOM QUESTIONS block appended below the standard fields. Read it live.
- Greenhouse dates are **plain text MM/YYYY inputs** — typing works. NOT Workday's segmented
  spinbuttons; do not reach for the calendar-picker protocol here.
- `GREENHOUSE_ACCOUNT_LOOP` is explicitly `None`/None with a `why` — so nobody invents a login leg.
  ("Quick Apply with MyGreenhouse" is an optional convenience, not the path.)

**Cookie banners belong to the WRAPPER, not the ATS.** KKR uses OneTrust: the banner offers only
ACCEPT + MANAGE PREFERENCES — **the reject lives one level in**: `#onetrust-pc-btn-handler` →
`.ot-pc-refuse-all-handler` ("Reject All"). Declined per the operator's privacy default; verified the
banner + panel disappeared and `OptanonConsent` was written.

## 2026-07-15 — Greenhouse form driven (KKR); react-select needs REAL keystrokes; attestation recorded

Drove KKR's embedded Greenhouse form to one field short of submit. Everything below is in
`GREENHOUSE_LESSONS` and generalizes to any Greenhouse employer.

- **Every combobox is a REACT-SELECT and opens ONLY on real per-char keystrokes.** A react-safe
  value-set + `input` event left `aria-expanded=false` and no listbox at all; `driver:"humanized"`
  (per-char) opened it immediately. Exactly the `/select_prompt` lesson — these widgets fetch on
  keystrokes. Two follow-ons: **`aria-controls` is ABSENT until it expands** (resolve the popup AFTER
  typing — my pre-open probe saw null and I wrongly concluded there was no wiring), and **after
  picking, the input's `.value` goes EMPTY** — the choice renders in a sibling `[class*=singleValue]`,
  so verifying `.value` reports a false blank.
- **`driver:"direct"` SILENTLY NO-OPS on these controlled inputs** — `#start-date-month-0` stayed
  empty while the call still returned `ok:true`, and only the by-value verify caught it (`/2026`).
  Another entry in today's silent-success family. Greenhouse dates are plain text (no calendar
  picker, unlike Workday) but they still need per-char typing.
- **Exact-match options.** `/Concord/` picked **"Concordia, Entre Rios, Argentina"** over "Concord,
  New Hampshire" — the same substring pitfall as Workday's "State" matching "United **State**s". My
  own code fell for the trap that's already written down. Anchor the match.
- `#country` is the PHONE country code (renders "+1"); the address is `#candidate-location`.
- Custom questions render as `#question_<id>`; each combobox has a hidden required twin, so a
  duplicate empty-id field in a scan is NOT a second question.

**AI-use attestation — recorded as a first-class, detectable question type.** KKR requires
"I confirm my materials ... were not generated, edited, or supplemented by AI tools (e.g., ChatGPT,
Gemini, **Claude**...)". Wording will vary per employer, so it's detected with
`is_ai_use_attestation(question_text)` (strong signals fire alone; weak ones need two) rather than a
fixed string, and answered from the answer-store key `ai_use_attestation` — the OPERATOR's own
attestation about their own materials, set once and reused. **Vocabulary varies and inverts**: KKR
renders it as Yes/No where the QUESTION carries the confirmation, so Yes = confirming; another
employer may ask "did you use AI?" where Yes means the opposite. Read the question, never blind-fill.

The stored answer is the operator's own, set once and reused (`human_required: False` — the answer
store drives it, like every other question).

## 2026-07-15 — Greenhouse date fields: month is a react-select, and .value LIES

Filling KKR's education/work dates surfaced the sharpest false-positive yet.

- **Month and year are DIFFERENT widgets in the same date row.** Year is a plain `input[type=number]`
  that accepts typing. **Month is a react-select combobox that wants the NAME** — `'Aug'` → "August";
  typing `'08'` returns **zero options**. Both look like `input` in a naive scan; only `role=combobox`
  + `aria-autocomplete=list` distinguishes them.
- **`.value` on a react-select reports a FALSE SUCCESS.** Typing "03" into the month left `.value ===
  "03"`, so my verify passed — then it cleared on blur, because the text was never committed to a
  selection. I "verified" `work_start: "03/2026"` twice and it was empty both times. **Verify the
  month at its sibling `[class*=singleValue]`; verify the year at `.value`.** Getting this wrong means
  submitting a form you believe is filled.
- Committed correctly via type → wait → click the exact option: August 2015 – June 2021 (UST),
  March 2026 (LUK).

**The day's recurring theme, one more time:** every bug today has been *something reporting success
that didn't happen* — the silent url_fallback, the wrong-tab capture, the "already open" stray
options, the wrong-target discovery, `direct` no-opping on controlled inputs, and now `.value` on a
react-select. The lesson isn't "check your work" — it's **verify at the layer that COMMITS, not the
layer you typed into.**

Answer-store additions (so no future application re-asks): `education_school` (University of Santo
Tomas), `education_degree` (Bachelor of Science), `education_discipline` (Sports Science),
`education_start_date` (08/2015), `education_end_date` (06/2021), `primary_language`,
`additional_languages`, `ethnicity_detail`, `current_employer`, `ai_use_attestation`.
NOTE the store holds canonical values (`08/2015`, `Sports Science`); the FORM may need a different
vocabulary — Greenhouse wanted the month NAME, and its school list has no University of Santo Tomas
at all (it does carry Ateneo de Manila, so the absence is real, not a search miss) → "Other". Map
store → widget vocabulary at fill time; don't assume the stored string is what the widget accepts.

## 2026-07-17 — Controller v1 built end-to-end (the teachable decide(), M1–M5 + cockpit)

Built the missing `decide()` in observe()→decide()→act() across all five PLAN_controller_v1
milestones in one session, offline-testable core + UI. 84 new tests, full suite 442 green. The
live drives (teacher-compile, replay, propose-approve Workday) are the operator-present next step;
everything they need is wired and the loop shape is proven offline. Load-bearing lessons:

- **Two join keys, and the spine rule attaches to the CHEAP one.** Everywhere else "fingerprint"
  means the AX sha256 — and it is OPPORTUNISTIC (only exists when a scan ran). The controller must
  journal a joinable row on EVERY step, so the Bundle carries BOTH `route` (route_template, always
  present) and `fingerprint` (AX sha256, may be None). "No row without a fingerprint" (PLAN §1) is
  enforced against `route`. Don't conflate them — the plan's Bundle comment did, and it's the first
  thing that trips you up in `decision.py`.
- **`/scan_required`'s `unanswered` items are NOT bundle-safe verbatim.** They carry `selector`
  (`#id`) and `value_read_at` (`[class*=singleValue]`) — selector-shaped, banned by invariant #10 —
  and `value_preview`, a slice of the field's value (PII, §4). `sanitize_unanswered` whitelists the
  semantic set `{field, kind, required_via, answered, valid}`. The plan said "verbatim"; the
  invariants win. This is exactly the "gap the existing surface didn't cleanly give" §6 warns about.
- **The credential boundary is a STATE, enforced at the bundle.** `workday_sign_in` /
  `workday_create_account` / `appvault_*` map to `human_required=True` in `describe_for_ats`, so the
  controller structurally cannot drive them — the agent never types a password / creates an account,
  and that rule now lives in the recipe layer (apply_recipe), not in a hope that decide() remembers.
- **Rung 0 replays by reading LIVE form truth, not a step counter.** decide() fills the first
  unanswered field the program covers, and advances only when `unanswered` is empty. This is
  naturally re-entrant to Indeed skipping prefilled fields (the thing that breaks index-based
  replay), and a guard-miss (an unanswered field the program never saw = the form changed) escalates
  instead of guessing.
- **Programs are PII-free BY CONSTRUCTION.** A step carries `{field}` (+ optional `value_ref`),
  never a value — the value is resolved from the answer store at act time. `save_program` re-sanitises
  every step at the boundary (drops `value`/selectors), so a committed `programs/*.json` is
  grep-clean of the operator's name/email without anyone remembering to redact.
- **The escalation streak resets only on a VERIFIED action, never merely on a non-escalate
  iteration.** First cut reset it at the top of each loop pass; that zeroed the counter before the
  propose-approve review and the verify-fail path, so a reviewer that kept saying "escalate" (or a
  step that kept verify-failing) could never hit the two-in-a-row stop. Caught by the M4 escalate
  test. Reset belongs next to `verified = True`.
- **A rejected model output is TRAINING SIGNAL, not an error to swallow.** `parse_decision` turns a
  bad Haiku output (unknown intent, a selector smuggled into params, a missing confidence) into a
  journaled model-rung escalation whose rationale names the fault — so the malformed row is visible
  in the corpus, the same discipline as the Outcome taxonomy.
- **The safe always-available UI surface is observe→decide WITHOUT act.** `/api/controller/observe`
  reads a tab read-only (free local CDP; degrades if the capture server is down), builds a bundle,
  runs the cascade, and shows the Decision — the operator can "watch it think" on any Career Search
  page with zero risk and zero spend (model rung off by default). This is what makes the controller
  demoable before a single live drive.

Where it stands: `apps/controlplane-api/controller/` (bundle, programs, decide, reason, loop, teach,
shadow, metrics, replay) + `packages/interaction/interaction/decision*.py` (the frozen contract) +
Lab → 🧠 Controller. Owed, operator-present: the M2 teacher-compile + replay drives on the Indeed
apply backlog (which also close out Interaction API Phase 1's DoD), and the first real shadow
agreement numbers into PROJECT_STATUS. `make controller-evals` is the offline regression suite.

## 2026-07-17 — LiveActuator built (the controller's live seam) + the north-star cadence

Operator directed the controller toward a full teaching cadence (fresh Indeed session → search
`reporting analyst` / Manchester NH / 50mi → apply to EVERYTHING end to end → record → paginate),
recorded as the `apply_sweep` mode (`search_cadence.py`) + `docs/PLAN_cadence_northstar.md`: v1
EXECUTES the known cadence (the goal is outlined, not reasoned per step), v2 REASONS it (the planner).
Then built the owed seam — `controller/live_actuator.py` (`run_controller`'s live `Actuator`) — and
validated its read path against the real browser. Load-bearing findings:

- **Reaching a SPECIFIC live session from the capture server needs `browser_url=<its CDP port>`.** A
  bare `/auth_state {}` returns "All connection attempts failed" — it defaults to a dead browser. The
  session manager (`GET /api/sessions`, control plane :8081) knows each session's port; the live
  Indeed session was id 16 on **:9328**, persistent authed profile, `logged_in:true`. The capture
  server (:8082) is NOT bound to a session by default — you pass it the port.
- **Address a multi-step drive by `tab_id`, never `tab_url`.** A tab_url handle breaks the instant a
  Continue click navigates; the tab id is stable across navigation. `/close_tab` and `/navigate` take
  either; the LiveActuator uses tab_id only.
- **`classify_ats(smartapply) == "indeed_quick_apply"`, but `apply_fields` keys Indeed under
  `"indeed"` and `INDEED_FIELDS` is ~empty (only the distance pill).** So Indeed's apply-question
  selectors CANNOT come from the static resolver — they only exist at runtime, in `/scan_required`.
  The LiveActuator addresses fields **resolve-first (Workday/Greenhouse static), then live-scan
  fallback (Indeed dynamic)**; that dual path is required, not a nicety. The Bundle gets the
  *sanitized* scan (no selectors → decide() stays selector-blind, invariant #10); the actuator keeps
  the *raw* scan (with selectors) for act(). That split is the invariant made mechanical.
- **The loop stops after 2 consecutive escalations, so a pure teacher-hand-authored compile drive
  stalls** (every step escalates with no programs/model). The live teaching mode is therefore
  **Haiku (rung 1) proposes + a reviewer corrects** (propose-approve / DAgger) — which is also what
  the operator asked for ("its early decisions will be inaccurate → corrected → training data").
- **Still owed for Claude/operator to BE the live reviewer:** `run_controller`'s `cli_reviewer`
  blocks on stdin, which Claude can't drive mid-loop. Owed: a step-wise HTTP surface (observe→decide→
  return proposal; then act the approved/corrected decision → verify → journal golden) — the cockpit
  propose-approve panel's backend. The LiveActuator + `run_live_apply` are done and tested (9 offline
  tests, fake transport); a live read-only `observe()` classified `indeed_home` correctly and decide()
  escalated honestly ($0). What remains before the drive: the step surface, then search→apply→teach.

## 2026-07-18 — First live teaching drive (Lactalis apply, apply_sweep) — 6 findings

Drove the first end-to-end live teaching run through `TeachSession`/`LiveActuator`: search (`reporting
analyst`/Manchester/50mi) → Lactalis "Analyst, Trade Management" (Bedford NH, quick-apply) → resume →
4 question pages → demographics → review → **Submit** → hit the **`ai_recruiter_gate`** (video/audio/text
interview, `classify_apply_outcome` correctly returned `human_required=True` → HANDOFF, not auto-solved).
Decision journal **15 → 26 rows** (11 this drive); 4 paired teacher-vs-Haiku rows, **0% agreement, all
`wrong_intent`** — the dense "where the cheap backstop fails" signal, exactly as designed. Six load-bearing findings:

- **The Haiku rung's structured-output schema was INVALID — it had never actually worked.** Anthropic
  structured output (a) requires `additionalProperties:false` on EVERY object (the nested `params`
  lacked it → 400), and (b) rejects `minimum`/`maximum` on numbers (`confidence` had them → 400).
  FIXED in `controller/reason.py` (params pinned to the closed key vocab; range enforced in
  `parse_decision`, the tested half). This is why the M3 "Haiku rung" was green in unit tests
  (`parse_decision` is pure) yet dead in the field — the live call was never exercised. **The first
  live drive is what caught it.**
- **Native radio groups are not driveable by the tier-2 endpoints.** `/check_group` errors on them and
  `/select_option` only handles react-select/listbox — the ONLY working radio mechanism is
  `/autofill_form`'s native-`input.click()`-by-group-name. LiveActuator gap: it needs a radio path
  (route `select_option`/`check_group` on a `radio_group` widget to the autofill click), or radios stay
  autofill-only. Worked around live by executing radios via autofill inline while journaling the
  semantic `select_option` decision.
- **`/scan_required` misses a lone required acknowledgment checkbox.** The certification ("I have read
  and accept the above acknowledgement") returned 0-unanswered while Continue was blocked with "Choose
  an option to continue." The scan detects inputs/radio-groups but not a single required checkbox → a
  false "form complete." Rule reinforced: a no-op Continue means re-scan the DOM for a missed required
  control (and check the challenge first — did that, no captcha).
- **The Ethnicity react-single-select STAGES but doesn't COMMIT on a plain click** (widget-protocol
  §6/§7): the field showed "Asian" visually while `scan_required` still saw it unanswered and Continue
  no-opped — until it committed a step later (another timing artifact). Composite selects need the
  stage→commit protocol, not a click.
- **The Indeed location combobox races on clear+type** (react per-keystroke): `action_id=clear` then
  immediate `type` produced `Manchester, NHu` / `Manchester, NHNashua, NH`; a ~1.3s settle between
  clear and type fixed it. Same lesson as project_humanized_body_driver — type races React.
- **`LiveActuator._current_state()` classifies before navigation settles** → it reported the OLD state
  (`resume_selection`) right after a Continue that had already navigated to `questions/1`. Needs a
  settle/poll after a control click before re-classifying (the loop's verify would mis-fire otherwise).

Outcome: the form submitted; the AI-recruiter interview is the operator's (human-required). The tab is
left OPEN for them (not the close-and-refocus epilogue — the application isn't DONE until the human
step). "Apply = done only when submitted" now has a corollary: some ATS submits are followed by a
human-required gate that the sweep must record as a HANDOFF, not a completed apply.

**Update — 4 of 6 findings now fixed (with tests):** finding 1 (Haiku schema) fixed live in the drive
(`reason.py`); findings 2, 3, 6 fixed after: **radio path** — `LiveActuator.act()` routes a
`radio_group` (and affirmation/consent checkboxes) through `/autofill_form`'s native click, keeping
the journaled intent semantic (`select_option`/`check_group`); **scan** — lone required checkboxes get
a synthetic group key in `SCAN_REQUIRED_JS` so an acknowledgment is no longer skipped; **settle** —
`LiveActuator._current_state()` polls `/auth_state` until the url stabilises before classifying (6 new
offline tests). Still OPEN: finding 4 (the Ethnicity react-single-select stage-vs-commit — needs the
tier-2 select protocol to handle it) and finding 5 (the location combobox race — worked around with a
settle gap in the drive; not yet a driver-level fix).

## 2026-07-19 — The UI problem is hierarchy before theme; AI Ops redesign documented by concern

**What we saw.** The live cockpit has useful operational truth, but one animated sidebar currently
acts as global navigation, Training/Lab/System section menu, hierarchical domain browser, and account
shortcut list. Titles and descriptions repeat across the rail, page header, hero, tabs, and cards.
At the existing 900px breakpoint the rail disappears with no replacement navigation. Styling those
screens in a new palette would preserve the disorientation.

**What is true.** The redesign must stabilize the product model first: AI Ops has one persistent
global rail (Overview, Domains, Activity, Learning, System); domain and page navigation live in the
content hierarchy; sites such as Indeed/Workday/Greenhouse are Career Search channels rather than
global destinations. The visual direction follows from that structure: a warm graphite console,
monochrome SVG icons, no emoji, no blue, restrained semantic color, and dashboards organized around
human attention, active work, outcomes, flow, and recent wins.

**Where it is encoded.** `docs/ui/` separates the current-state audit, product/design direction,
information architecture, design system, dashboard grammar, frontend ownership boundaries, and the
phased migration plan. The implementation rule is vertical slices with legacy deletion—not a theme
layer added to the 4,412-line global stylesheet.

## 2026-07-19 — A stale tab was SILENCE, not an error (and the unexpected-state policy that fixes it)

The reasoning-driven ATS login (`login_reasoner.py`) was dying on real accounts with an opaque
`no_login_form` / `max_steps`. The reasoning was fine; the **tab addressing** was the bug, and the
shape of the bug is the lesson.

- **A stale CDP target does not raise and does not return an error.** `run_login` pinned one
  `tab_id` from `tab_finder.resolve_target` and drove all 5 steps against it. When that target dies
  (SSO redirect spawning a new target, an OOPIF iframe form replaced after submit, the operator
  reloading the Sign-In screen), `_discover_target` raises — but `propose_ax_candidates` **swallows
  it into `errors[]` and returns `[]`** (ax_proposer.py:304-309). So `/ax_scan` replies HTTP 200
  `{"ok": true, "candidates": [], "errors": ["target_discovery: No target with id …"]}`. Reading
  only `candidates` — which is what `run_login` did — makes a **dead tab indistinguishable from
  "the form isn't open."** `/probe`, `/auth_state` and `/execute` DO return `ok:false` /
  `outcome:"error"` with the reason in `detail`; `run_login` discarded those too (`_exec` ignored
  its response entirely). **The failure mode was silence, not an error** — so the fix is to read the
  reply body, never to infer from an empty result.
- **The wrong-tab refusal is correct and stays.** `_discover_target` refuses to fall back to another
  tab when an explicit id misses — that guard is why an Indeed capture stopped being written into
  the corpus labelled as a Workday state. The fix is to **re-DISCOVER the right tab**, not to loosen
  the guard. `tab_finder.resolve_target` is read-only and idempotent, so it is safe to call again
  mid-drive; it is injected into `run_login` as a `re_resolve` callable (like `reasoner=`/`journal=`)
  so the reasoning module stays free of the DB and unit-testable with a fake.
- **A stale tab mid-fill must not become "bad password."** The credentials never landed, so setting
  `attempted_creds` there would make the next pass escalate `bad_credentials` and tell the operator
  their stored password is wrong when the tab simply vanished. Detect the stale execute FIRST, then
  decide. This is the same family as the 07-15 lesson — *verify at the layer that COMMITS* — one
  altitude up: **don't diagnose from a symptom you never confirmed reached the page.**
- **"Re-observe once, then escalate" existed in exactly one place.** `controller/loop.py:214-228`
  had it inline (`STALE_STATE_OUTCOMES` + a `stale_retry_used` latch); `runtime/loop.py` had a
  different retry notion; login had **none**. Now one pure policy — `controller/unexpected.py` —
  answers RE_OBSERVE | ESCALATE | CONTINUE for both levels of "we are not where we assumed":
  **protocol** (`not_found`/`not_opened`/`ambiguous`) and **tab** (`STALE_TAB`, deliberately not an
  `Outcome` member because it is a whole target, not one control). A shared *policy*, not a shared
  loop — each caller keeps its own mechanics.
- **The alert existed and was never wired.** `runtime/handoff.py` persists a record AND fires a
  macOS banner, with plain-language guidance per reason — but it was only ever called from
  `runtime/loop.py` via `main.py`. Neither the controller nor login produced a `LoopResult`, so
  neither had any operator alert at all. Added `emit_escalation(...)` (plain fields, no LoopResult)
  + an `escalation_callback` factory for `run_controller`'s `on_escalate` seam. Handoffs are already
  a `session_activity` source (`kind:"escalation"`), so this lights up the live timeline **with no
  frontend change** — which mattered because another agent owns the UI this session.
- **Unregistered pages are now COLLECTED, not forgotten.** `haiku_page_state.classify` already
  returned `is_new` + `proposed_name` and `suggest_page_state` **dropped it**; `map_url_to_state`
  returns `"unknown"` and recorded nothing. Both now feed `page_state_candidates.record_candidate`,
  writing a `status="candidate"` row into the SAME `page_state_registry`. It is inert by
  construction (every labeler/classifier query filters `status == "active"`), promotion is a
  one-field flip through the existing PATCH, and it inherits the whole scoping model — no new table,
  no new UI. The blackboard gained a matching `unexpected_state` blocker so a page we cannot name
  halts `proceed_decision` exactly like a captcha.

**The through-line:** every one of these was a component that *already knew something* and threw it
away — the discovery error, the `ok:false` bodies, the `proposed_name`, the `"unknown"` state, the
handoff channel. The work was almost entirely **connecting existing parts**, not building new ones.
When a flow fails opaquely, first ask *what did some layer already know that nobody read?*

**Still owed:** `reconcile` records + halts on an unexpected state but does not itself alert (the
blackboard's event list is not a `session_activity` source, and keeping disk/notification I/O out of
it is deliberate) — the timeline alert needs a caller at the seam that surfaces a blocked proceed
decision. And `run_controller` still has no production call site, so its `escalation_callback` is
wired-and-tested but not yet firing in anger.

## 2026-07-19 — AI Ops UI: navigation structure was the visual redesign

- The most important visual change was deleting the transforming sidebar. One stable rail plus URL-backed local tabs reduced both clutter and repeated headings before any color work mattered.
- Overview works best as a calm briefing, not a wall of equally weighted cards. Human attention and agent presence lead; metrics, outcomes, and domains follow.
- Activity needed console density and an inspector, not bigger cards. Keeping reasoning, actions, handoffs, and errors compact makes transparency usable instead of merely available.
- “No emoji” required removing emoji from data catalogs and deep utility screens, not just the shell. A single `currentColor` icon resolver makes that rule enforceable.
- Training and Lab were one learning loop hiding behind two product labels. The explicit Capture → Label → Train → Evaluate → Promote visual makes the teacher/student system understandable, while Advanced keeps engineering tools reachable without making them primary navigation.
- A theme migration can safely govern legacy components temporarily, but it does not erase their architecture debt. `App.jsx` and `App.css` remain extraction targets; the implementation notes record that debt so the compatibility layer does not become the new foundation.

## 2026-07-20 — We could hash a page state but never DIFF one; and the failure taxonomy was already in the logs

The supervisor plan (`docs/PLAN_supervisor.md`, adopted today as priority #1) rests on an
"always-on cheap sense" — the AX-tree delta. The audit found we did not have one, and that the
thing we needed instead of guessing was already sitting in our own corpora.

- **Equality is not a diff, and we only ever built equality.** `fingerprint.compute` hashes a
  screen, so it can say two states are *different* and never *how*. The one place that needed to
  know — `controller/loop.py`'s treadmill guard — approximated it with `progress_signature`, a
  3-tuple of `(url, state, unanswered-field-names)`. That tuple is blind to a modal opening, an
  error banner appearing, or a Continue going disabled: precisely the events that make a drive
  stick. Built `interaction/delta.py` (`StateDelta`), a set difference over
  `fingerprint.ax_summary`'s identities — same normalization on purpose, so the delta and the
  fingerprint can never disagree about whether "Messages (3)" and "Messages (7)" are one control.
- **Real progress in Indeed's questions module is invisible to url and state.** All 29 journalled
  rows from the Lactalis drive across `questions/1|2|3` share one templated route
  (`…/questions/{id}`) *and* one state (`indeed_apply_questions`). So advancing a step and
  treadmilling on a step look identical to any signature keyed on url+state — the only evidence
  that the page moved is **the control set turning over**. This is the whole argument for the
  delta in one measurement. It is also why `LiveActuator.observe()` now owes an AX scan: it calls
  `/auth_state` and `/scan_required` only, so **`Bundle.fingerprint` has been `None` on every live
  row we have ever journalled**, and `ax_identities` is empty until that is wired.
- **Templated routes are less sensitive than raw urls, deliberately.** `/q/1` -> `/q/2` is NOT a
  route change. That is the right default (a nonce or id churning in a url is not movement) and it
  makes the guard fail toward a false *stall* — an unnecessary escalation — rather than a false
  *progress*, which is the 8×-click disaster of 07-19. Pinned in a test named for the limitation.
- **The taxonomy did not need inventing — it needed mining.** From `decision_journal.jsonl` (88),
  `intent_journal.jsonl` (223), `handoffs.jsonl` (34) and ~30 hand-written incidents here:
  **9 `verified=False` decisions, 23 non-`ok` intents, 34 handoffs** (corrected — see the 2026-07-20 (3) entry: the first count came off a journal the test suite had polluted). Eight classes cover *all*
  of them — `control_not_found` (24, the largest by far), `no_progress` (6 of the 13
  verified-false rows), `staged_not_committed`, `race_settle`, `stale_tab`, `unrecognized_state`,
  `auth_wall` (12 handoffs), `missed_required_control`. The power law is real and it is ours.
- **Two classes from the generic web-agent literature are absent from our data**: unexpected
  modals/interstitials (plausible but unobserved) and layout drift breaking selectors — which
  *cannot* happen here, because we address by role + accessible name. Seeding a taxonomy with
  borrowed classes would have taught the supervisor to look for the wrong things; classes get
  earned the way `Outcome` members were earned.
- **Do not build the event bus.** The incoming design asked for one; `decision_journal.jsonl` is
  already written on every controller step and `intent_journal.jsonl` on every journaled endpoint.
  A second channel is exactly the 2026-07-16 corpus reckoning again (`event_log.jsonl`, a ring
  buffer no trainer reads). Supervision journals as added optional columns on `DecisionRecord`,
  which `decision.py` already declares backwards-compatible.

**The through-line, again:** almost every piece of this was already present and unjoined — the
normalization, the identities, the journal, the escalation policy, the shadow harness. What was
missing was the subtraction.

## 2026-07-20 (2) — The controller had been driving nearly blind, and the taxonomy fits

S12 built the supervisor's vocabulary (`interaction/supervision.py`: `FailureClass`,
`RecoveryPlay`, `SupervisorVerdict`, rung-0 `classify`). Getting it fed meant auditing
`LiveActuator.observe()`, and the audit was worse than the missing AX scan.

- **`observe()` passed `page_text=""`, and page text is the ONLY state signal Workday and
  Greenhouse have.** They are single-origin SPAs whose step lives in the page, not the URL
  (`apply_recipe._WORKDAY_STATE_MARKERS`). So every Workday step collapsed to
  `workday_job_posting` or `unknown` for the controller. Worse: **`_CHALLENGE_MARKERS` are page-text
  only**, which made the controller *structurally unable to notice a captcha* — the one thing it
  must always escalate and never auto-solve. `/auth_state` was already reading
  `document.body.innerText` to decide `has_sign_in` and simply not returning it. One field added to
  the probe's JS closed both. `_current_state()` had the same `""` and the same fix.
- **Three silent-failure defaults in one method, all of the 07-19 family.** `auth.get("logged_in",
  True)` turned a DEAD PROBE into "we're signed in"; `scan.get("unanswered") or []` turned a failed
  scan into "the form is complete"; and an `/ax_scan` returning `candidates=[]` **with** `errors`
  is the stale-tab signature, not an empty page. All three now produce an honest handoff
  (`_blind_reason`) instead of a confident wrong observation. An empty scan with NO errors is left
  alone — that is a real reading, and the supervisor classifies it.
- **A verdict must be journaled on the row of the action it judges.** The plan said "supervise
  after act()", but the delta available at that moment (the between-observations one) describes the
  PREVIOUS action — so the verdict would arrive a turn late and land on the wrong row. Fixed by
  having `act()` take the after-picture itself (`ActOutcome.ax_identities` + `unanswered_after`).
  Two extra read-only CDP evals per action; free on a metered link, and the difference between a
  training label and a note.
- **`unanswered_after` is load-bearing, not a nicety.** A react-select that STAGES changes the AX
  tree, so control churn alone reads an uncommitted value as success. Only the form's own "is this
  field filled?" disagrees, and it is right (the Ethnicity select, 07-18).
- **An empty identity set is ambiguous and must never be differenced.** Caught by a failing test:
  diffing a populated `after` against an empty `before` reports the entire page as newly appeared,
  so *every* action looks like progress and the treadmill guard silently disarms. Identities are
  now diffed only when both sides have them; otherwise the delta falls back to state + unanswered,
  under-reporting movement — which fails toward a false STALL (an escalation) rather than false
  progress. Same safe direction as the templated-route choice earlier today.
- **The mined taxonomy survived contact with the code, and got sharper.** Rung 0 names all 9
  replayed real incidents with zero UNKNOWNs. Two refinements it earned: `not_staged` /
  `not_committed` / `no_option` / `committed_unconfirmed` come straight off the existing `Outcome`
  taxonomy rather than being re-inferred (the endpoint knows *which half* of a widget protocol
  broke; no inference does), and `MISSED_REQUIRED_CONTROL` outranks `NO_PROGRESS` when the form
  scans complete — which is exactly the Longroad treadmill, so the real incident gets
  `rescan_required` instead of a blind retry. A tenth class, `CHALLENGE`, was added for
  `Outcome.BLOCKED`: forcing the loop's loudest stop into UNKNOWN would make the commentary go
  silent precisely where the operator needs it.
- **The supervisor has no authority yet, and there is a test that says so.** Stage 1 is shadow: it
  journals and narrates, `unexpected.respond` still decides continue/re-observe/escalate. The guard
  against it quietly acquiring authority is a test, not a comment.

465 controlplane-api / 138 interaction / 53 mcp green; controller-evals green.

## 2026-07-20 (3) — The test suite was writing into the live corpus, and we could not have trained on it anyway

S12b (the play executor) landed; the pre-commit audit found two data problems that matter more.

- **84% of the "corpus" was fixture traffic.** `decision_journal.jsonl` read 282 rows; **237 were
  written by the test suite** (`run_controller` journals by design, `session_id="t"`, route
  `smartapply.indeed.com/x`). The real corpus is **45 rows**. `INTERACTION_ARTIFACTS_DIR` already
  existed as the override — there was simply no `conftest.py` setting it, so every `pytest` run
  since the journal was created had been appending. Now plugged (session-scoped autouse fixture in
  both test roots); a full 630-test run adds zero rows. The polluted file is backed up as
  `decision_journal.jsonl.pre-cleanup-2026-07-20.bak` and the fixture routes filtered out.
  **The mined-taxonomy counts published earlier today were off** (9 `verified=False`, not 13) and
  have been corrected in place. The CLASSES are unchanged, because they came mostly from the
  hand-written incidents in this file — which no test can forge. That is an accidental argument
  for keeping this log: it was the only corpus that could not be corrupted by a test run.
- **The other corpora were clean** (`intent_journal`, `loop_steps`, `selection_telemetry` — all
  real hosts). Only the decision journal, because only `run_controller` journals in tests.
- **Only 4 of 45 rows were replayable.** `bundle_snapshot` was written for `golden or shadow` rows
  only, so 41 rows recorded what was DECIDED with no way to reconstruct what it was decided FROM.
  A distilled L4 learns `Bundle -> Decision`; a corpus holding only the right-hand side cannot
  teach it. Now written on **every** row — it is PII/selector-free by construction and ~300 bytes,
  so the restriction was never anything but the original narrow framing of "replay cases".
  Everything journaled before today stays half a row; there is no backfill.

**Audit findings recorded without fixing (they are the next work, not this session's):**

- **A controller drive writes no captures.** `observe()` reads `/auth_state`, `/scan_required`,
  `/ax_scan` and never calls `/capture`, so every page state a drive meets produces a decision row
  and **zero L3 training examples**. The flywheel's perception half does not turn on controller
  drives at all — only on the older capture path.
- **Planning is 0% built.** No `plan.py`, no `ContextPack`, no `PlanStep`, no `plan_grader.py`;
  `PLAN_reasoner_v2.md` S06–S09 are entirely unstarted. What DOES exist is its substrate: every
  decision row carries `state` + `landed_state`, which is transition data, and
  `state_transition_table_v1` is trained. At 45 rows it is far below the readiness gate
  (`_TRANSITION_MIN_PAIRS=10` / `_TRANSITION_MIN_REPEATED=5` need repeated pairs, not just pairs).
- **Nothing loads a trained model for inference, anywhere.** `model_lib/registry.py` is DB
  metadata, not a loader. `stage_observer_nb_v1` scores **94% held-out** and sits unused on disk
  while `haiku_page_state.classify` pays for the same judgment live. The seam is ready
  (`HttpReasoner` already POSTs to `/api/controller/decide_model`, invariant #6) and the `cache`
  rung of `RUNGS` has been declared since M1 and is still empty. The gap between "we have a local
  model" and "a local model is in the loop" is a loader and an endpoint — not a training run.

**The through-line for today, all three entries:** every problem was a component that already knew
something nobody read — the AX identities, the page text, the `ok:false` bodies, the `errors[]`,
the trained classifier, the artifacts-dir override. The work keeps being connection, not
construction.

## 2026-07-20 (4) — "Teacher-driven drive" redefined: the system drives; the teacher runs alongside

Operator-directed. The term had quietly come to mean "Claude drives the browser in front" — and in
recent sessions that regressed further into Claude scripting interactions directly instead of going
through the Interaction API (the operator's standing gripe; the §8 violation). Neither is what the
operator means by teaching. The definition is now pinned in **PRINCIPLES §11**; the short form:

- **The controller/system drives.** The teacher — the **local Claude agent** (Claude Code / the
  Claude app), explicitly *not* Haiku — runs alongside: watching, keeping its own notes, stepping in
  only at pauses (escalation, low-confidence decisions, propose-approve gates, supervisor verdicts
  worth auditing).
- **The teacher acts exclusively THROUGH the system**: the `Reviewer` seam, Interaction API intents,
  label/candidate endpoints. It corrects, escalates further, or teaches — never free-hands a script
  around an existing endpoint. Its interventions ARE the corpus (golden rows with both rationales,
  labels, candidate states) that pushes the local models in the right direction.
- **What this changes in code (owed, all small):** a reviewer transport the local agent can service
  (`cli_reviewer` assumes a human TTY; the `Reviewer` seam is injectable, so an HTTP/file review
  inbox is a thin adapter), the `run_live_apply` default flipped from teacher-demonstrates to
  controller-leads/teacher-reviews, escalations that park-and-wait for a teacher response instead of
  only halting, and `/capture` wired at pause/correction moments so teaching also feeds L3.

This also settles the "reasoner slot" question from the same day's gap review (frontier model as an
API rung vs. not): **the frontier brain on deviations is the local Claude agent on call, not an API
rung.** Consequence, accepted by design: an unattended drive with no Claude app listening escalates
and waits — that is the ladder working, not a failure.

## 2026-07-20 (5) — "Effective parameters" is a compute figure, not a memory one; and a 1B model found a hole in our own parser

The student's seat (PRINCIPLES §9) got wired and two candidate occupants got measured. Both
failed. The seat is worth keeping; the measurements are worth more.

- **Gemma 4 E2B does not fit, and the spec sheet is why we thought it would.** "2.3B **effective**
  parameters" describes COMPUTE — per-layer embeddings cut the active math, but all **5.1B total**
  params must still be resident. The Q4 build is **7.2 GB**, not the ~2 GB implied. (`gemma4:e2b`
  and `gemma4:e2b-it-q4_K_M` are the same blob — the default tag already IS Q4, so there is no
  smaller fallback.) E4B is 9.6 GB by the same arithmetic. On this 8 GB M3 it took **50 seconds to
  emit one word** and grew the swapfile from 8 GB to 14.3 GB, leaving 799 MB free — actively
  hostile to the live browser it would be sharing the machine with. **Read the footprint, never
  the parameter count.**
- **llama3.2:1b fits (1.3 GB, ~1.7 s/decision) and cannot do the job: 0/4** on real bundle shapes.
  It collapses to `click` for every case and **invents application answers** (`value: "0"` for
  desired salary, `value: "yes"` for ethnicity) — against a system prompt that says in as many
  words "Never invent an application answer."
- **The finding that outlives both models: `parse_decision` had a semantic hole.** Before the fix,
  llama's four decisions were **all accepted** — `click` is a real verb, `field`/`value` are real
  keys, confidence was in range, the rationale was non-empty. A JSON grammar constrains SHAPE and
  the shape was legal. But `LiveActuator` resolves a click as `control or name or **value**`, so
  `click {field: "salary", value: "0"}` would have **clicked a control named "0"** on a live job
  application. Not a no-op — a wrong click. The per-verb param shapes had been documented in the
  `Intent` docstring since day one and enforced nowhere. Now `contract.INTENT_PARAMS` +
  `check_intent_params`, enforced in `parse_decision` on the model rungs only (compiled recipe
  programs never pass through it, so rung 0 is untouched). With the gate in, the same four
  decisions score **0/4 — every one escalates.** That is the design working: a weak student
  degrades into *hand up*, not *act wrongly*. **Haiku could have made the identical mistake**;
  this was never really about the small model.
- **`student` is its own rung** (`RUNGS`, between `cache` and `model`) — otherwise shadow
  agreement compares `model` against itself — **and it is in `PROPOSE_RUNGS` from day one**.
  That second one was nearly missed: without it an untrained 2B would act *unreviewed* on a real
  application, the exact inversion of §9.

**The reassuring half, and it is the important half.** None of this touches getting unstuck. The
recovery layer built earlier today is **deterministic and model-free**: rung-0 supervision names
the failure from the 10-class taxonomy at $0, `RecoveryPlay` prescribes the play, the executor
fills it. The model's only job is *"what intent next"*. So a failed student costs nothing on the
get-out-of-jail path. What that path actually still needs is (a) live drives to earn per-class
promotion — `AUTONOMOUS_CLASSES` is still empty, so today it names and prescribes without acting —
and (b) a rung-1 model to shrink the `UNKNOWN` bucket at the taxonomy's margin. Neither is a
local-model problem.

**Disposition:** Gemma deleted. llama3.2:1b kept on disk as the baseline artifact (re-running the
probe is one command). `controller/local_reasoner.py` kept — model-agnostic, 15 offline tests,
grammar-constrained, reusing Haiku's exact prompt surface so a future occupant's rows train the
same policy. The seat is wired; nothing can sit in it yet. Falling back to option C: Haiku stays
on rung 1, and the trained-but-unused 94% L3 classifier is the next thing to wire.

## 2026-07-22 — The north star was a star: it didn't know our restrictions. Perception replaces "a second brain"

Operator-directed re-anchor, and the most consequential doc change since the corpus reckoning. The
old endgame — *the inner system gets strong enough that learned scenarios run without Claude at
all; the student becomes its own teacher* — is **retired by measurement, not by taste**:

- No local model that *reasons* fits this machine (2026-07-20 (5): Gemma 4 E2B 7.2 GB resident /
  50 s per word; llama3.2:1b 0/4 and inventing application answers).
- **Getting unstuck never needed one.** Rung-0 supervision names the failure from the 10-class
  taxonomy at $0 and `RecoveryPlay` prescribes the play, with no model in the loop.
- The domain is not novel. Enumerable states that recombine; the end goals are written down; the
  failure modes are a power law of eight classes mined from our own logs.

So: **Claude is the novel reasoner permanently and by design** — the teacher rung is a part of the
finished machine, not scaffolding awaiting removal. The local system perceives, acts on rails,
verifies, and knows when it doesn't know. The number that has to bend is **teacher calls per
submitted application**, not teacher calls to zero. PRINCIPLES §9 amended; PROJECT_STATUS Endgame
rewritten; build plan `PLAN_perception_v1.md`.

### The measurement that shaped the plan (run before writing it)

Apple's Vision framework ships a native image embedder, `VNGenerateImageFeaturePrint` — 768-dim,
**0.18 s/screenshot, zero download, zero API cost, already an installed dependency**
(`pyobjc-framework-Vision`, in `requirements.txt` since the OCR layer). Leave-one-out 1-NN over
every labeled capture whose screenshot still exists (73 captures / 33 states / 18 states with ≥2):

| Asked | Result |
|---|---|
| "which exact page-state?" | **55.2%** (32/58) |
| "which platform/family?" (`indeed_*` / `workday_*` / `fb_*`) | **93.1%** (54/58) |
| same-vs-different separation (~AUROC) | **0.836** |

The confusions are a specification, not noise: `workday_my_information ↔ workday_questions ↔
workday_my_experience`, `workday_voluntary_disclosures ↔ workday_self_identify`,
`fb_create_listing_form ↔ fb_listing_condition_picker`. **Vision cannot separate two Workday form
phases — same chrome, different fields — and the DOM separates them trivially by reading the field
labels.** That complementary failure mode is the entire case for two witnesses, confirmed on our own
data rather than asserted. Consequence: **the visual observer is NOT a state classifier in v1.** Its
jobs are platform/family witness (93%), **novelty detector** (the thing NB structurally cannot do —
NB can only be unsure *between known classes*, never unsure of *everything*), and effect witness.

### Two findings that came with it

- **101 of 174 labeled captures point at screenshots that no longer exist on disk** (312 files
  present; only 73 joinable to a label). The visual corpus is under half what the label count
  implies, and nobody noticed because nothing read it — the 2026-07-16 corpus reckoning wearing a
  new hat. Fix the linkage before benching anything.
- **0.836 AUROC with same-state median cosine 0.897 vs different-state 0.811 is a narrow band.**
  FeaturePrint is trained on natural photographs, not UI. Free and good enough to build the seam on;
  first thing to re-bench against CLIP on wifi.

### Naive Bayes cannot take embeddings — and shouldn't be asked to

`state_observer.py` is a multinomial NB: it multiplies **count** likelihoods over sparse discrete
tokens (`route:`/`role:`/`tok:`). A 768-dim dense float vector has no count semantics; feeding one
in either degrades to nonsense or requires discretizing away the geometry you wanted. The NB is not
replaced — it becomes **witness A**, the sparse-token witness, and gets *assisted*. The embedding
goes in a different head: **nearest-prototype cosine** (~50 lines, no sklearn, no training loop,
works at n=3/class, updates by averaging so a correction is still instant, and gives distance-based
OOD for free), with a linear head on frozen embeddings as the next rung if prototypes plateau.

And the shape to avoid: "specify parameters, weight them, concatenate embeddings" is a linear model
over `[sparse | dense]` — **early concatenation destroys the independence the second witness is
being bought for.** Use **late fusion**: each witness emits its own distribution *and its own
novelty score*; disagreement is preserved as a first-class signal instead of being averaged into a
smooth lie. Averaging two uncalibrated confidences is how you build a system that is confidently
wrong exactly where it needed to raise its hand.

## 2026-07-22 (2) — Perception v1 built; three calibration bugs that only real captures could find

The pivot above got its first code: `perception/` (facets, encoders, prototype bank, DOM witness,
observer, bench, train) plus `interaction/belief.py`. What is worth recording is not the code —
it is the three things that were **correct in unit tests and wrong on our own data**, each found
by fitting the observer and printing a `BeliefState` for a real capture. All three are now pinned
by `test_perception_calibration.py`.

**Correction first: the screenshots were never missing.** Yesterday's entry (and the commit
message) said 101 of 174 labeled captures point at screenshots that no longer exist. They exist.
Those rows carry an **absolute path** under `apps/mcp-mock/output/observer-screenshots/` — the
directory later renamed to `apps/mcp`. Resolving by filename under the current artifacts root
finds all 174. The visual corpus is **174 rows over 59 states**, not 73. The loader now resolves
path-then-filename and counts the fallbacks, because a pointer that only resolves by fallback is
provenance drift worth seeing. Lesson unchanged in kind, only in blame: **absolute paths in a
corpus are a time bomb, and nothing noticed because nothing read the data.**

**1. Singleton classes poisoned the novelty calibration.** A genuine `workday_questions` capture —
in its own training set — scored novelty **0.93**, i.e. "less familiar than 93% of everything I
know." Two causes: an example sits *inside* its own centroid, so measuring familiarity against it
measures nothing; and with 59 states over 174 rows most classes have exactly one example, whose
centroid IS that example and therefore scores a perfect 1.0, shoving the whole percentile curve up
until every well-observed state reads as an outlier. Fixed by calibrating **leave-one-out** and
**excluding singletons**.

**2. Novelty was measuring class TIGHTNESS, not novelty.** Against one global pool, a 20-example
state scores *more* novel than a 2-example state — a centroid over twenty varied screenshots sits
further from each of them than a centroid over two. Fixed by calibrating **per predicted class**:
"given we think this is `workday_questions`, how typical is it *for* `workday_questions`?" That is
one class-conditional step short of conformal prediction, which is as far as 2–6 examples per class
will carry. In-distribution novelty is now median **0.09**, and **3.4%** of known pages trip the
0.90 novelty ceiling — a sane false-flag rate where before it would have been most of them.

**3. The two witnesses' margins differ by 10x, so a shared threshold is meaningless.** A correct
DOM call sits near a **0.37** margin; a correct visual call near **0.04** — every screenshot of a
white form is cosine-similar to every other. A single `CLEAR_MARGIN` read the visual witness as
permanently unsure and the DOM witness as permanently certain. Each witness now fits its own
`margin_scale` (the median margin of its correct leave-one-out calls) and `Prediction.clarity`
reports the margin against it.

**And a facet bug of the same family:** `ats_registry.classify_ats` answers `company_site` for any
host it does not recognise, and `company_site` is a real platform in the facet vocabulary — so
every `facebook.com` page was filed as a company site. Non-ATS hosts are now checked first. **A
confident wrong answer beats no answer only when it is actually right.**

**The novelty ceiling is not 0.5 and the reason is structural.** Novelty is a percentile, so
in-distribution observations spread roughly uniformly over [0,1] by construction — a 0.5 cut-off
would flag half of every page we know. `NOVELTY_CEILING = 0.90` means "less familiar than 90% of
what we have seen", and **the cut-off IS the false-flag rate**. That property is why the percentile
was worth the trouble: it needs no per-corpus tuning and it is comparable across encoders.

## 2026-07-22 (3) — The bench, run: the encoder decision, and the result that argues against our own premise

Leave-one-out over all 174 labeled captures / 59 states (`make perception-bench`). Facets scored
by **projection** — predict the state, read the facet off the answer.

| witness | state | platform | phase | novelty AUROC |
|---|---|---|---|---|
| **dom:tfidf** | **66.9%** | **98.0%** | **75.5%** | **0.700** |
| dom:nb (incumbent) | 62.9% | 96.7% | 73.5% | 0.500 |
| **visual:apple** | 58.3% | 94.0% | 66.9% | 0.693 |
| visual:clip | 63.6% | 91.4% | 70.9% | 0.685 |
| visual:pixel32 (baseline) | 49.0% | 86.8% | 57.0% | 0.683 |

**Adopted: `dom:tfidf` + `visual:apple`.**

- **Never train on a facet — project onto it.** Training directly on `phase` averages
  `workday_sign_in`, `indeed_login_email` and `login_wall` into one centroid (four vendors' chrome
  smeared together) and scores **62.8%**; projecting off the predicted state scores **75.5%**.
  Facets are a lens on the answer, not a second model to fit.
- **CLIP does not earn its 600 MB.** Better at exact state (63.6% vs 58.3%) — *witness A's* job —
  and worse at platform and novelty, which are witness B's. Kept as a one-flag comparison.
- **The fusion falsifier did not fire.** Agree -> right **77.9%**; split -> **48.2%**. A 30-point
  gap, so disagreement genuinely predicts failure. On a split the DOM is right 48% vs vision's 25%,
  which is why witness A leads and the belief is marked unsure instead of tie-broken.

**And the finding that argues against the plan's own premise, recorded rather than buried:**
witness B's novelty AUROC (0.693) is **not better** than witness A's (0.700). The claim that vision
is "the only cheap way to detect that we are somewhere new" is, on this corpus, **not supported** —
a TF-IDF centroid with class-conditional calibration spots an unseen state just as well. What
vision has demonstrably earned is the **cross-check**, not novelty supremacy. Re-test at ~400
captures; if it still holds, witness B's job shrinks to platform + cross-check and the plan should
say so. (What NB could never do stands: 0.500 AUROC, exactly chance — a posterior over known
classes cannot represent "I have never been here.")

## 2026-07-23 — End flags were the wrong abstraction: checkpoints, and why re-running a search is a cost

We kept failing to define "task complete" for the search loop, and treated that as a gap to fill.
It is not a gap — it is the wrong question. **These tasks have no terminal state**, and the
start-stop shape an end flag implies is not merely inelegant on Indeed, it is *destructive*:
repeat the same query too often and Indeed caches/collapses it, so results we already pulled stop
coming back. A design that restarts a task to "finish it properly" burns the thing it is
gathering. Operator, verbatim: *"creating flags for what we are doing HAVE to be open ended, it
will need to hang onto session shape."*

The replacement (`session_checkpoints.py`) asks a different question — not "is it done?" but
**"how far up the ladder are we, and what's next?"** A checkpoint is a milestone that, once
reached, is HELD for the session's life and never re-executed just because we came back around
to it. Two kinds, and the distinction is the whole design:

* **standing** (browser reachable, signed in) — must be true CONTINUOUSLY. Cheap to verify every
  step and **safe to re-run** when it lapses.
* **consuming** (query submitted, distance filter applied) — reaching it **spent something that
  cannot be spent twice**. Once held it is NEVER re-run, even when the page no longer looks like
  it. A lapsed consuming rung produces a **RECOVER** instruction (get back to the results we
  already have), never a repeat.

**The ladder is open-ended by construction.** Four fixed rungs reach the start line, then it
grows one rung per results page — `page:1`, `page:2`, … There is no last rung to flag. "Task
complete" is not something we invent; it is the *observed* fact that the ladder cannot grow (no
next page), and even then closing out is the operator's call. That is what "hang onto session
shape" means in code: the session holds its shape and advances a cursor.

### Three things that only fell out once it was built

* **The observation map has to be tri-state.** `True | False | None`, where **None means "we did
  not check" and must never read as a regression** — otherwise one flaky probe sends us re-running
  a rung that costs a real Indeed query. `radius_set` is permanently None: there is no cheap
  read-back of the distance pill, and guessing would be the confidently-wrong failure mode we
  already catalogued in the ATS-facet bug.
* **A consuming rung must be marked only on PROOF.** `run_query` re-reads the tabs afterwards and
  requires a results URL actually carrying our query; if it cannot prove it, the rung is left
  **unmarked** and the operator is told. Unmarked means it will be retried — which is the harm —
  so this is the one place where proof matters more than progress.
* **The phase boundary is where the design earns the word "independence."** While climbing, the
  crank works rungs by itself (nothing consequential to ask about). At the start line it *stops*
  and hands the page over. The operator asked for exactly this split, and it maps cleanly onto
  the existing authority ladder rather than competing with it.

Landed as `session_checkpoints.py` (pure, 19 tests), `routers/session_control.py` (the crank +
read model, 24 tests) and the Session control tab. Verified live against session 16: the crank
marked `provisioned` off a real CDP probe and then **stopped at the credential wall** rather than
typing anything — the hard boundary holding without a special case for it.

**Open**: the ladder is Indeed-search-shaped. Apply has its own consuming rungs (a submitted
application is the ultimate once-only act) and should reuse this module rather than grow a
parallel vocabulary. And `initialize` refuses a *second query in one session* — the enforcement
point for all of the above — which means "search three variants" is now explicitly three sessions.

## 2026-07-24 — The first live drive of the control panel: five bugs, one shape

Standing up one session for the first control-panel drive found five bugs. Four were mine and
**all four were the same mistake: recording something as true that was never verified.** Worth
writing down as one lesson rather than five, because the shape is what generalises.

| where | the unverified assertion |
|---|---|
| `_stop_training_chrome` | SIGTERM'd a **pid** and wrote `stopped`. The pid was a launcher Chrome had replaced; the real browser lived on and kept the profile locked. |
| the profile-conflict guard | trusted the **DB row's status**. A row saying `stopped` does not unlock a directory. |
| `_launch_training_chrome` | wrote `chrome_debug_port` from `Popen`. The wait-for-CDP loop already existed and its result was **discarded** — session 18 was `active` on port 9322, pid 10514, neither of which existed. |
| my own first fix | verified the **recorded port** had gone dark. That port was never alive, so it read as instantly dark and reported a clean stop over a browser still serving on another port. |
| `step` (the panel) | built the view from the observation taken **before** dispatch, so a rung that had just succeeded rendered as a lapse — it told the operator to recover from a search that had worked. |

The fifth was `/execute` returning a **422 for every call** (`target_bbox` is required even on the
act-by-name path) while the caller asked `outcome != "not_found"` — so a reply carrying no
`outcome` at all passed as an action performed. The panel reported submitting a query having typed
nothing and clicked nothing. `LiveActuator` had always sent `"target_bbox": {}`; **the convention
existed and I did not follow it.**

### Three things that generalise

- **Verify the thing the next step needs, not the thing you happen to have.** Stop should confirm
  *the profile is released* (what a launch needs), not that a port went quiet. The port was the
  handle we had; the profile was the fact that mattered.
- **A process holds a lock; a responsive debug port does not.** An early draft of
  `profile_conflict` required CDP to answer before calling something a conflict — which would wave
  through exactly the zombie that breaks the next launch, since `ps` only lists live processes.
  Writing the test corrected the model.
- **Act, then re-observe.** `step` was the only endpoint that did not, and it produced a
  self-contradiction the moment a rung succeeded. The `observed_delta` hook it used instead was
  dead code nothing ever populated.

### And the one that was pure assumption

`run_query` addressed Indeed's search box as `("combobox", "What")`, `("combobox", "Where")`,
`("button", "Find jobs")` — written from general knowledge of Indeed rather than a scan. The live
page offers `search: Job title, keywords, or company`, `Edit location`, and `Search`. **None of the
three assumed names exists anywhere on it.** The fix is not better constants — they drift, and they
already differ between the logged-out home, the logged-in feed and the results page.
`search_cadence.find_search_controls` discovers them from a fresh AX scan every time. Same lesson
one layer down: the logged-out home exposes **no** sign-in control to AX at all (173 candidates,
the only match a button named `Account`), so login hides behind a menu widget and a matcher looking
only for "sign in" would report no way to log in on the page whose entire job is logging you in.

### Two things the operator named that were real gaps, not bugs

- **Login was the one rung the system did not own.** It reported "the operator signs in" and
  offered nothing to press. The boundary was never "we refuse to open the login page" — it is that
  we never type a password or clear 2FA. Everything before that is a click a human makes before any
  secret exists. The auth rung now surveys and hands back pressable options, and refuses to act on
  a screen whose next action IS the secret.
- **A cockpit that only refreshes when you press something shows a past that has stopped being
  true.** The panel now pings every 5s (all local CDP sockets, free in low-data mode) with a live
  marker, because a frozen panel and a fresh one otherwise look identical.

### Pacing became a named thing

`set_distance` fired with no pause; `run_query` had three hard-coded, invariant sleeps.
`execution_style.py` splits HOW from WHAT: `fast` (the old behaviour, kept and named), `human`
(default, ~1.6s median around an action), `unhurried` (the long tail — a cadence that is always
1.5s is its own signature). Chosen once per sequence so a drive is coherent. **The bot-safety floor
applies to every style including `fast`** — style decides how far *above* the floor a run sits,
never whether it does. Style is not a safety mechanism; a captcha does not care how fast we typed.

**What held up:** the checkpoint ladder. Every one of these was caught by a guard rather than by
damage — the unproven-submission rule left `query_entered` unmarked rather than claiming a search
that never happened, which is why nothing was lost and the retry was free. The architecture's own
principle kept catching its author.

## 2026-07-24 — Nested Workday prompt: the verification worked, the drill-in click did not (yet)

Building "How did you hear about us?" for the live MFS Workday apply surfaced the nested-prompt
case the operator predicted: Indeed is not a top-level option, it is a LEAF under a category
literally labelled "Job Board (LinkedIn, Indeed, etc.)". Two findings, one good and one open.

**The good one — verification caught a lie, twice.** `/select_prompt` (single-level) returned
`ok` on this field while the screenshot showed it still red and the dropdown open on the category.
`ok` there means "I clicked a node named Indeed", not "the field committed the value" — the same
class of over-report as `/execute`'s ok. The fix was `/select_prompt_path` + `_PROMPT_COMMITTED_JS`,
which reads the field's own value/pill and aria-invalid AFTER the clicks and returns OK only when
the field actually took it, `COMMITTED_UNCONFIRMED` otherwise. On the live drive it correctly
reported `committed=False` and `apply_prompt_select` recorded HUMAN_REQUIRED, not a false success.
**The lesson holds at every layer: a click is not a commit, and only the field can confirm.**

**The open one — the category drill-in.** `/select_prompt_path` drills the path in one open
session, but native-clicking the "Job Board (…)" category ROW does not expand it into its leaves —
after the click the popup is still at the top level. So the leaf "Indeed" is never reachable and
the field cannot commit. The native backend-node click that works to COMMIT a flat option does not
appear to trigger Workday's DRILL-IN on a category row (likely the resolved node is the text span,
not the row's click handler, or the chevron needs the event). Next: resolve/click the category's
own row (or chevron) element, or send a real mouse event at the row, and re-verify. The
infrastructure around it — source→path resolution (`apply_source.source_paths`), the try-each-path
loop, Other as the truthful floor, and commit verification — is right and tested; only the
category-expand click is unsolved.

Also confirmed this session: the transient "account_create failed" the operator flagged never
reached the corpus. The MFS account is `status=active`; the decision journal has zero
account_create failures (the only account row is the Apply click, ok). `apply_account` records
operational mini-steps on the blackboard, it does not write training data — so a retry that failed
then succeeded is honest local history, not a poisoned example.

## 2026-07-25 — A stale session is a test fixture: the first fresh-session climb, and four faults

The operator's framing, and it earned itself in the first ten minutes: *"maybe having these stale
sessions is the best thing for us because it allows us to always test the literal inner layers."*
A session left open two days is not mess to clear before the real work — it is the only cheap
source of the states our fixtures never produce.

### The stale session paid for itself immediately

Session 19 had been open since 07-23 with two tabs: Indeed results, and a Workday application
mid-form. The panel read `authenticated: REGRESSED` and offered "sign in again" on a session that
had never signed out. `_observe` called `/auth_state` with **no tab hint**, so `_discover_target`
resolved whatever target CDP listed first — the Workday tab — and Indeed's login JS, finding no
Indeed markers on a Workday page, honestly answered `logged_in: false`. Probed directly, the Indeed
tab answered `true`.

This is the third time this exact shape has been fixed in a different place (`069eb61`, classify
taking the last tab rather than the apply tab). **A single-tab assumption survives every test whose
fixture has one tab, and an apply session never has one tab.** The rule LEARNINGS already drew on
07-24 — *verify the thing the next step needs, not the thing you happen to have* — restated: probe
the tab the rung is ABOUT.

Second half of the same fix: with no Indeed tab at all, `authenticated` is now `None`, not `False`.
`session_checkpoints` has always refused to read an unknown as a regression; the probe feeding it
did not, and neither did `auth_probe`, which released the rung and ran its login survey against
whatever page was in front.

### Nobody had ever opened the front door

Provisioning session 20 — **the first fresh session the panel has ever started** — produced one
`about:blank` tab, and from there the ladder could not move at all:

    auth_probe  -> "No Indeed tab is open"
    run_query   -> "Open Indeed's job search, then step again"

Both rungs handed the *first move* back to the operator. PLAN §2.1 specifies initialize as "reach
the start line" and nothing ever implemented step one. Every live drive to date began on a browser
a human had already pointed at Indeed, so the gap was invisible — the same way session 19's tabs
hid the auth bug. Opening a site's **home page** is not the URL-forcing §3 warns about: that rule
is about jumping into a deep state we should have clicked our way to, and there is nothing to click
on `about:blank`.

### Then the search submit, which took three passes

The query rung is the CONSUMING one, so each of these was a chance to spend the session's one query
on nonsense. None of them did, because the rung marks only on a results URL carrying our query.

1. **The click that commits the widget.** Both fields held their typed values, Search was the
   hit-test target at its own centre, the trusted click dispatched, `/execute` returned `ok` — and
   the page did not move. Typing into the location combobox stages a suggestion popup, and the
   click that looks like "press Search" is spent dismissing it. The **widget protocol in the search
   box**: AX finds the element, but the element sits inside a widget with a protocol. `/execute`'s
   docstring already says its `ok` means *the mechanism completed*, not that the page accepted it,
   and names the fix — a caller that needs "did it take?" must confirm at tier 2. We were reading
   tier-1 `ok` as "acted".
2. **`type` is `Input.insertText`, which inserts at the caret.** It does not replace. A submit that
   did not land leaves both boxes populated, and this rung's retry story is "step again" — so the
   second attempt would have searched `data warehousedata warehouse`. Clear before typing.
3. **The retry added for (1) was itself blind.** It fired whenever no results tab carried our
   query — which live included the case where the click HAD submitted and the tab re-read simply
   **raced the navigation**. The second click landed on the freshly-loaded results page, whose
   search box is empty, and submitted `q=` from the SERP (`from=searchOnDesktopSerp` was the tell).

**A verification that can race the thing it verifies is not a verification, and a retry behind it
is blind no matter what it is called.** The guard is now `not moved` — click again only when
*nothing in the window changed*. If the page moved anywhere at all, the click did something, and
repeating it is the double-spend the rung exists to prevent.

### The one that was not our bug, and the one that was

`POST /training/sessions/19/stop` **refused to record a stop** — correctly, per the 07-24 fix: it
verifies the *profile* is released, not that a port went quiet. But underneath, the stop had killed
the recorded pid and macOS relaunched Chrome windowless (reparented to launchd, same profile, same
port), so it destroyed the session's tabs *without* releasing the lock. The guard turned a silent
corruption into an honest error. The stop itself still needs to handle the respawn.

And the shared checkout was on another session's branch (`codex/fix-ui-hover-backgrounds`), not
`main` — so `git merge --ff-only main` would have pulled this work into someone else's active
branch. It refused. **Check `git branch --show-current` before merging in a shared tree**; the
worktree convention only isolates the sessions that use it.

### What held up

The ladder, again. Four faults on the consuming rung and it never once recorded a query it had not
proven — every failure left `query_entered` unmarked and said why. The design's own principle kept
catching its author, which is the third session running that this has been the honest summary.

## 2026-07-26 — Staleness became a datapoint, and the choke point I asserted was one of four

The operator named an axis the system had never measured: *"one of the states should be … a
datapoint that should always be attached … staleness could probably be a datapoint that remains
like 'safe' or 'normal'."* Built as `perception/staleness.py` and hung on `Bundle.staleness`.

### Why it is a fourth module and not a branch inside an existing one

We already had three modules that each answer a different question about an observation —
`perception` (where are we), `reach` (can we touch it), `unexpected` (is this where we expected) —
and **a drive can pass all three on a view that went stale twenty minutes ago.** Every incident
this week fits that description and none was a perception fault. Freshness is its own axis; giving
it its own module is what makes that visible rather than a special case inside somebody else's.

### The prototype's job is to produce evidence, not verdicts

The thresholds are guesses and are labelled as such. What matters is that the journal takes the
**raw ages** (`staleness_idle_s`, `staleness_page_age_s`) alongside the level and a
`RULES_VERSION` stamp, against `outcome` in the same row. That turns the open question — *at what
value does the next action's failure rate rise?* — into something answerable from drives we are
already doing, instead of a second instrumentation pass later. **Journal the evidence, not the
conclusion**, whenever the conclusion is a guess. A level alone would have baked today's guess
into the corpus and left nothing to fit.

Corollary, now written into the module: it **gates nothing**. A remedy driven by a guessed
threshold is worse than no remedy.

### Freshness is never worth more than work

The one rule in there that is safety rather than heuristic. Both remedies (refresh, renew) destroy
typed input, and the staleness that prompts them is a *suspicion*. So unsaved work downgrades
REFRESH to CONTINUE and RENEW to HANDOFF — the operator decides whether to abandon a half-filled
application, and the detector never does. The same instinct as the consuming-rung rule: the
expensive, irreversible thing does not get to happen on the strength of an inference.

### And the mistake worth keeping

I stamped the unsaved-work flag inside `_out()` and wrote a comment calling it *"the single choke
point every outcome passes through"*. It is one of **four** `ActOutcome(...)` sites in that file;
the write path is not one of them, so the flag never set and a test caught it immediately. The fix
was to *make* a choke point — `act()` is now a thin wrapper around `_dispatch()` — rather than
sprinkle the stamp across four sites.

**Asserting a choke point does not create one.** The comment was confident, wrong, and would have
been load-bearing documentation for the next reader. Grep for the constructor before claiming
everything funnels through a helper.

## 2026-07-26 (2) — "First match in document order" cost us a click on the wrong company

The operator picked six jobs, worked the first (Beth Israel, `company_site`), and landed on
**Enterprise Applications Analyst at a different company** — one rung after `verify_identity` had
confirmed the open pane was the right job. Their question was whether staleness caused it. It did
not; the cause was deterministic and would have fired on a freshly loaded page.

### The bug

`enter_apply` scanned the whole page and took the FIRST control whose accessible name contained an
apply word:

```python
for hint in _APPLY_HINTS:          # ("apply now", "easily apply", "apply on company site", …)
    ctrl = next(c for c in candidates if hint in c["name"].lower())
```

On a results page that is never the right control. The left-hand list carries an "Easily apply"
badge on every card, and the filter bar carries "Encouraged to apply". Worse, the hint ORDER
guaranteed the failure for exactly the postings we care about: a `company_site` job's real button
says **"Apply on company site"**, which sat third, so a card's "easily apply" always matched first.
A live scan on the recovered page picked `'Encouraged to apply filter'` — a filter chip.

**This is the same lesson `_JOB_DESC_JS` already carries, unlearned in a second module.** That one
says it outright: *"NOT `querySelector('a, b, h1')`, which returns the first match in DOCUMENT
order regardless of list position."* Writing a lesson down where it bit does not stop it recurring
somewhere else — a flat AX scan over a page that contains a LIST is the general trap, and every
matcher over one needs to say which region it means.

### And the reason nothing caught it

The rung recorded `enter_apply ok` because `/execute` returned `ok`. Third time this exact contract
has bitten: **tier-1 `ok` means the click dispatched, not that the page accepted it.** So a click
onto another company's posting was journaled as "entered the application for BIDMC" — a corpus row
that reads as success and trains the wrong thing, which is worse than a failure.

Fixed both halves: the control is chosen using the apply_type the pane ALREADY REPORTED (observed,
not guessed), list/filter furniture is refused by name, a control naming a different job is refused
outright, and the rung now verifies it actually LEFT Indeed before recording OK.

### What it says about the staleness work

Nothing here was a freshness fault, and it is worth being precise about that rather than letting a
new detector absorb the blame for an old bug. But recovering the page did surface an adjacent gap
the detector does not cover: after a back-navigation the pane was **present but empty** (title a
bare `&nbsp;`, 16 cards, no apply control anywhere). "Is the region I am about to act on actually
loaded" is a readiness question, not a session-age question, and `staleness` measures the latter.

## 2026-07-26 (3) — Driving a new ATS: iCIMS, and the wrapper/frame trap three times over

First teacher drive into iCIMS (Joslin Diabetes Center, from Indeed's "Apply on company site").
Three separate bugs, all the same shape, and the shape is worth naming once:

**A branded ATS wrapper is TWO documents, and every layer we own assumed one.**

    the employer's page   = nav, footer, newsletter signup, copyright
    #icims_content_iframe = the job, the apply control, the whole application

1. **Reading.** `/auth_state` returned 691 characters of hospital homepage. The job was 4512
   characters inside the frame. A classifier fed the top document calls a live job landing a
   marketing page with complete confidence. Fixed with `/page_content` (top + same-origin frames).
2. **Clicking.** `getBoundingClientRect` is relative to the node's own document; a trusted
   `Input.dispatchMouseEvent` takes top-document viewport coordinates. The apply link sat at
   (1079,126) in its frame, the frame at (110,-802) in the page — so the real target was
   (1189,-676), off-screen, and the untranslated point landed on the IFRAME ELEMENT. Click
   dispatched, nothing happened, `ok` returned. **The entire class of branded-wrapper ATS was
   unclickable and nothing said so.**
3. **Targeting.** The operator's auto-create typed their email into Joslin's NEWSLETTER box,
   because an unscoped "find the email input" takes the first in document order:

       WRAPPER  input[type=email] "Enter your email address here"   <- newsletter
       FRAME    input[type=email] "css_loginName"                   <- the real one

Three layers, one assumption. When a page embeds an application, **say which document you mean**
— reading it, measuring it, or matching in it.

### The other blocker: an undriven platform could never advance

`classify` recorded "this platform has no recipe" as its OWN outcome, so the rung never settled,
`next_rung` returned classify forever, and the ladder could not reach the account wall in front of
it. That meant **the platforms worth driving were exactly the ones the ladder could not walk.**
Classify's job is to say WHERE WE ARE; it has done that the moment the landing has a name. "No
recipe" is still reported — in the detail, and by the step staying needs_operator.

### iCIMS's own shape, recorded because it is the recipe skeleton

    job_posting -> email gate -> Basic Information (1/4) -> Candidate Profile (2/4)
                -> EEO (3/4) -> Portal Specific Forms (4/4)

Step 1 is account creation AND application in one: resume file (required), first/last name, email,
login, **password + re-enter**, and an hCaptcha response field, behind one "Submit Profile" button.
So on iCIMS the account wall is not a separate page to get past — it is the first form, which is
why a generic "create the account, then apply" recipe would not have fitted it.

### Two classifier corrections the live pages forced

* **A sparse page is still the content.** `pick_content` required 200 characters and skipped the
  three-line email gate, falling back to the wrapper. Volume was never the signal; the frame's size
  on screen is.
* **A header link is not a wall.** Strict precedence read the job posting as an account gate
  because iCIMS puts "Returning Candidate? / Log back in!" in its header. Decisive phrases
  (confirmation, gone) still win alone; everything else is weighed, and STRONG phrases count double
  so a three-line page can still be classified.

## 2026-07-27 — The first iCIMS application, submitted. Four silent successes and one loop

Joslin, Healthcare Data Analyst: account created, five steps completed, **"Your application was
submitted successfully"** observed. First `submitted` flag in the queue. What it cost to get there
is the useful part.

### The wrapper/frame assumption, instances four through seven

Yesterday's entry named three (reading, clicking, targeting). Today added four more, all in code
that had *already been fixed once* somewhere else:

4. **`_resolve_node_by_selector`** runs `DOM.querySelector` on the top document, so no CSS selector
   can address anything inside the frame. (Still true — recorded, not fixed. Every iCIMS field is
   addressed by role+name for this reason.)
5. **The humanized driver's clear-before-type** wrote to `document.activeElement`. That property is
   PER-DOCUMENT: with focus inside a frame the top document's activeElement is the IFRAME ELEMENT.
   The clear no-opped, the keystrokes appended, and the "authoritative" value-set that exists to
   guarantee correctness no-opped too — so iCIMS's prefilled email read
   `genomags@gmail.comgenomags@gmail.com`, one click from creating a real account with it.
6. **`_select_option`'s phase 2** searched the top document for the open listbox. A portal renders
   at the root of ITS OWN document. Country and State reported success and stayed unset.
7. **`/locate`** — same `document.querySelector`. Unfixed, recorded.

The pattern is not "iframes are tricky". It is that **every layer that reads, measures, matches or
writes has its own idea of "the document", and each one has to be told which one it means.** Fixing
it in the reader does not fix it in the writer.

### Four things that returned success while doing nothing

Worth listing together, because the shape recurs more than any individual bug:

| what said ok | what actually happened |
|---|---|
| `/execute type` | keystrokes appended to a prefilled field; the corrective write missed |
| `/execute select` | listbox opened, option never found, field left unset |
| `_select_option` | returned `None` whether it picked or failed — the caller could not tell |
| a trusted click on the CC-305 radios | nothing; the native node click works (overlay takes the hit) |

`ok` at tier 1 means the mechanism completed — that is documented and correct. The bug is when the
mechanism itself cannot distinguish "did the thing" from "did nothing": `_select_option` now returns
`picked`/`notfound` and the verdict reaches `/execute`'s detail. **A layer whose failures are
invisible to itself cannot be debugged from above.**

### A lesson learned in one endpoint is not learned

`/select_prompt` knew, in a comment written weeks ago, that Workday's prompts "FETCH results
server-side on real keystrokes; a programmatic value-set does NOT trigger the fetch" — and typed
`keyDown(text)+keyUp` accordingly. The shared humanized driver, which every other caller uses, still
sent bare `char` events. So iCIMS's State list sat unfiltered with "New Hampshire" sitting in its own
search box, and we rediscovered from scratch a thing the codebase already knew. **When a lesson
lands in one endpoint instead of the layer, the next caller pays for it again** — which is the same
argument as putting lessons in the repo rather than in chat, one level down.

### The rung that could not settle, and the register that undid itself

Two bugs that between them made the account rung a loop:

* `apply_account` recorded its legs as `account_create` / `account_handoff` / `account_created` /
  `account_verify`. `next_rung` settles a rung **by name**, and none of those is `account` — so the
  ladder asked for the account after making it, forever. Exactly the classify bug (5b596c2) one rung
  over: **a rung that reports its outcome instead of answering itself never settles.**
* `ensure_account` — called on every crank — kept an existing account's status only `if
  existing.get("has_creds")`. Under this module's own convention the password is DERIVED and never
  stored, so `has_creds` is false for every ATS account we make. Each crank reset `active` to
  `pending`. The guard asked about the vault; the question was about the lifecycle.

The second one hid a third: **the test suite was writing to the operator's real `accounts.json`** —
no isolation fixture — and the reset was quietly undoing the pollution every run. Fixing the reset
turned the leak into seven failures that were really one missing fixture.

### iCIMS, recorded (see ICIMS_APPLY_RECIPE / ICIMS_FIELDS)

* **The stepper GROWS on authentication**: 4 steps before, 5 after (a "Job Specific Questions" step
  appears). Workday's tell in reverse, for the same reason — a stepper describes the work remaining
  for whoever is asking. Never read the pre-auth count as the shape of the application.
* **Step 1 is the account AND the application.** No separate wall to clear first.
* **Two comboboxes named `Type`** (phone, address). Role+name takes the first; the address one needs
  a node id from a scan taken immediately before acting. The ambiguity is recorded as DATA in
  ICIMS_FIELDS rather than left to surprise the next drive.
* **Country/State are searchable widgets with WINDOWED lists** (25 of 50 states rendered): open,
  type with real keys, click the option by name. State is empty until Country is set.
* **The EEO step's "I do not wish to self-identify" checkbox satisfies its three starred selects.**
* **The two OFCCP forms (CC-305, VEVRAA) have radios with EMPTY accessible names** and a signature
  checkbox that the form states is equivalent to a handwritten signature. Ask before every one.

### What the operator was asked, and what was not worth asking

Salary, commute and start date went to the operator; sponsorship came from stored answers; the
posted range ($72,800–$93,600) came from the observed job record so the question arrived with its
own context. Both self-identification answers and both signatures were confirmed explicitly. The
stored `availability_date` was two weeks stale — **a stored answer with a date in it goes off, and
nothing in the store knows that.**

---

## 2026-07-26 (4) — Career Search had two members but one of everything

**What we believed.** That Career Search was already a GROUP with members, so adding a second job
engine would be catalog work: register LinkedIn, point it at the shared machinery, done. The group
existed in `ats_registry.CAREER_SEARCH` from the start, and `linkedin_jobs` had been sitting in
`seed.REGISTRY_SEED["domains"]` — with page states and goals — since the registry was written.

**What's actually true.** Indeed was the only member long enough that "Indeed" and "the jobs domain"
fused in every layer that had no reason to distinguish them. Adding a sibling surfaced five seams,
each of which would have silently answered "Indeed" for a domain that is not Indeed:

* **The dashboard was a route per engine.** `GET /api/dashboards/indeed_jobs` took a `platform`
  query param it never received a second value for. Every aggregator answers the same questions off
  the same table → `GET /api/dashboards/{domain_id}`, with platform DERIVED.
* **`indeed_jobs` vs `indeed` are two different strings** — the registry id and the
  `ObservedJob.platform` tag — and the rollup hardcoded the second. That mapping is now
  `command_center.platform_for()`, in one place, and an unregistered domain resolves to ITSELF
  rather than silently to Indeed's jobs.
* **The Accounts tab was chosen by `parent === "career_search"`**, which routed every Career-Search
  child to the company-first ATS panel filtered on `atsFilter={domain.id}` — an `ats_id` no account
  ever carries. So the panel was empty for Indeed *and* the engine that most needs a sign-in had
  nowhere to type one. Which accounts a domain has is now DECLARED (`accounts: "domain" | "ats"`),
  not inferred from where it sits in the tree.
* **`classify_ats` would have called linkedin.com a `company_site`**, and `company_site` is a real
  platform in the facet vocabulary — the same confident-wrong-answer trap facebook.com fell into on
  2026-07-22. LinkedIn is now a first-class `facets.PLATFORMS` member, and `linkedin_easy_apply`
  joins `indeed_quick_apply` in `_ON_ENGINE_APPLY`: engine hosts are skipped in the host loop so
  they can never shadow a real ATS a posting hands off to.
* **`seed_training_registry` only runs on an EMPTY registry.** `linkedin_jobs` being in the seed
  dict meant nothing for any database already seeded — the same gap `seed_gmail_domain` exists to
  close. `seed_linkedin_domain` is its twin: merge, never remove.

**Where it's encoded now.** `test_career_aggregators.py` is the checklist a THIRD member
(ZipRecruiter, Glassdoor) has to satisfy — one test per seam above, each naming the wrong answer it
prevents. The UI half is declarative: `domains.js` carries `accounts` and `sweep` per domain, and
`DomainWorkspace`/`TasksPanel`/`StatusCard` branch on the declaration instead of on the id.

**The mistake I made on the way, and the correction.** I first shipped LinkedIn with `sweep: false`
and no Session-control tab, reasoning that the ladder was "Indeed's cadence end to end" and that a
crank which cannot turn is worse than no crank. The operator corrected it flatly: *"it does have a
job aggregator page... this system can definitely handle it, don't push your policy on my product."*
They were right, and the reasoning was wrong in an instructive way — **I had mistaken the READERS
for the CADENCE.** The cadence (one query per session, floor the radius, one page at a time, click
into what you shortlist, human pauses, never under a captcha) is about **how we behave**, and it is
identical on every job engine. The only things that are actually Indeed's are the DOM readers and
four facts about its URLs. Once separated, LinkedIn was a table row and a second set of readers, not
a second cadence. This is the same lesson as [[feedback_my_policy_not_operators]], arriving in a new
costume: the caution presented itself as an architectural claim rather than as caution.

**Where the split landed.**
* `apps/mcp/app/main_server.py` — `_platform_of(url)` picks readers off the tab's HOST (a fact),
  and `/extract_jobs`, `/next_page`, `/auth_state`, `/open_job_card`, `/set_distance` dispatch on
  it. Unrecognised hosts resolve to Indeed, so every existing caller is byte-for-byte unchanged.
* `routers/session_control.ENGINES` — one row per engine carrying the only four things that
  differ: the front door, the results-URL shape, the query param (`q` vs `keywords`) and the page
  size (10 vs 25). `engine_for(session, tab)` reads the live tab first and the session's declared
  `domain_id` second, because a tab is a fact and a `domain_id` is a label.
* `/api/search/sweep` takes a `domain_id`; the tab it aims at and the platform it tags rows with
  come from `command_center`, and nothing else about the loop changed.

**Three things LinkedIn taught that Indeed never had to.**
* **Identity is the URN, not an attribute.** LinkedIn has shipped the job id as `data-job-id`,
  `data-occludable-job-id` and `data-entity-urn` in different renderings of the same page, and the
  logged-out list uses none of them. The href (`/jobs/view/<id>`) is the only thing every rendering
  agrees on — so the href is read FIRST, the reverse of Indeed's `data-jk`.
* **The results list is VIRTUALISED.** One read of a 25-result page returns ~7 cards, and would
  have recorded exactly that in the corpus without erroring. `/extract_jobs` scroll-and-re-reads
  until the count stops growing. This is a silent-undercount bug, the worst kind: nothing fails.
* **A logged-out visitor still sees results.** So "results are on screen" is not evidence of being
  signed in, and the LinkedIn auth probe reads the global nav rather than the content — the same
  shape as facebook.com serving both states at one URL.

**Still owed, and honestly so.** None of the LinkedIn readers have met a live page yet: they are
written defensively (several selectors per field, href-first identity) and every one of them
reports a structured failure rather than a silent empty, but the first live drive is what turns
them from careful guesses into knowledge. `set_distance` for LinkedIn drives the radius SLIDER with
trusted key events and confirms from `distance=` in the URL; like Indeed's it refuses to fall back
to a URL rewrite, so if the widget has moved we will hear about it instead of getting a quiet
radius. Capture + label on that first drive — see [[feedback_capture_label_is_the_work]].

## 2026-07-27 (2) — The window is state, and nobody was watching it

Second drive of the session: the operator's TOP-PRIORITY pick (BILH, Healthcare Data Analyst –
BIDMC), reached through a branded wrapper into Workday. It ended in a state worth having a name
for — *"You've already applied for this job"* — but almost everything before that was the window
lying to us.

### The leftover tab from the LAST application broke the NEXT one

`open_pane` called `/open_job_card` with no tab_id and no tab_url, so `_discover_target` took
whatever CDP listed first — the submitted iCIMS tab, still open. It reported **"card
data-jk=e5c794ae32973697 not found"**, which reads as a rotated listing. The card was on the results
page the whole time; `/extract_jobs` found it by that exact id seconds later.

Operator: *"the tab manager should've immediately been a part of the cleanup crew after
submitting… cleanup needs to be cleaner because it may confuse us going long term."* Exactly right,
and the confusion arrived one application later.

`APPLY_EPILOGUE` has described itself as **"a REQUIRED step of the loop, not a manual tidy-up"**
since 2026-07-15. Nothing on the path that ENDS a step ever called it. Prose in the recipe, absent
from the layer — the same shape as `/select_prompt` knowing about real keystrokes while the shared
driver did not.

### An apply is a CHAIN of tabs, and each hop strands the one before it

    Indeed  --Apply on company site-->  jobs.bilh.org  --Apply now-->  bilh.wd1.myworkdayjobs.com
                                        (recorded as the apply tab)     (where the work actually is)

Three faults fell out of that one shape:

* **`_apply_tab` kept returning the doorway.** Its test for a stale record was "is it still open" —
  and the spent landing page IS still open. Fixed by preferring a tab the window manager calls
  ROLE_APPLY over a recorded one it does not.
* **`orient` trusted `step.platform` over the live URL.** classify had answered `company_site` on
  the wrapper, and that memory shadowed a Workday page, so orient called Workday's
  Start-Your-Application chooser "new territory" — a state the recipe has known for weeks. **A
  recorded platform holds only until the page says otherwise.**
* **The cleanup could not close the doorway.** The window manager refuses UNKNOWN-role tabs, and an
  employer careers site is exactly that — correctly, since the operator shares the window. The
  missing warrant is PROVENANCE: a tab we watched appear during our own application is ours. The
  step now records what it opened and closes precisely those.

### The system could make an account it was not allowed to use

Only the create leg was wired into the automated path. BILH had an ACTIVE account, credentials in
the vault — and the ladder still stopped to ask the operator to type them. We could create an
account by typing a generated password and then could not use that same password to sign in.
Nothing in the operator's directive separates the two; the gates that hold (captcha, verification
code) are identical either way.

### Two flags that were decoration until today

* **PARKED vs ABANDONED.** The module has always said parked means "not now" and abandoned means
  "not ever" — and `enqueue` refuses a known job_id, `done` is true for any terminal, and nothing
  reopened anything. The top-priority pick sat parked under its own note, *"re-queue after the
  matcher fix"*, with the fix long since shipped. `reopen` archives the failed attempt and
  re-walks the ladder from the top: the rung it would otherwise inherit was an `enter_apply` that
  recorded OK for another company's card, and **a rung whose answer we no longer trust is worse
  than one we re-walk.**
* **A capture's provenance.** `TrainingCaptureRead` omitted `label_source` / `state_label_source` /
  `verified_at`, so six human-labelled captures read as unlabelled through the API — the wrong
  answer to the one question that endpoint exists to answer.

### `submitted` does not always mean *we* submitted it

Signing in revealed the requisition already had an application on file. The queue tracks whether a
job has been applied to, so the flag is `submitted` — with a detail that says plainly this drive
did not send it. **The flag records the outcome; the detail owes the provenance.**

## 2026-07-27 (3) — "Have we applied to this?" had nothing to consult

The BIDMC drive ended on Workday's *"You've already applied for this job."* Operator: *"we need
logic on whether we applied to things or not and that needs to be checked on initial landing on a
page and scan for the jobs… see if we applied in the database or not."*

### The database did not know either

The obvious reading is "we forgot to check". The real fault was one layer down: **there was
nothing to check.** `apply_flag` closed tabs without recording, so the Joslin application —
submitted and confirmed hours earlier, with a screenshot of the confirmation — still read
`application_status='seen'`, `applied_at=None`.

`APPLY_EPILOGUE` has said **RECORD BEFORE CLOSE**, "because a closed tab with no record is
unrecoverable", since 2026-07-15. We wired the CLOSE half this morning and left the RECORD half on
the floor — while quoting the rule in the commit message. The queue knew; the queue is one
session's blackboard.

**A fact that lives only in session state is a fact the next session does not have.**

### One job, three identities

    Indeed card        indeed:e5c794ae32973697        a jk, and jk rotates per search session
    employer wrapper   jobs.bilh.org/…-jr88822/       the req id, in the path
    the ATS            bilh.wd1.myworkdayjobs.com/…   same req, different host

`ObservedJob` is keyed on `platform:external_id`, so an application made through Workday is
invisible to a lookup keyed on the Indeed jk. Recognising it needs the REQUISITION — which is
sitting in both urls, unparsed, the whole time. That is tier 2 of `applied_index`, and it is the
tier that would have saved the drive.

### The fuzzy tier reports, it does not decide

Same employer + same role words is right far more often than it is wrong, and it is also exactly
the match that would skip "Data Analyst II" for having applied to "Data Analyst I". So it returns
`likely_applied`, never `applied`: `open_pane` HALTS on a certain match and only WARNS on a fuzzy
one. **A near-miss that silently skips a job the operator picked is worse than one that asks.**

Two matching details that are not fussiness: the same title arrives with an en-dash on Indeed and a
hyphen on Workday, and the ATS routinely appends a department the card omits ("Healthcare Data
Analyst" vs "Healthcare Data Analyst - BIDMC, OBGYN Quality"). Similarity is scored against the
SHORTER title for that reason — Jaccard punishes the appended department, and punishing it is how
one job reads as two.

### It goes in the prompt, unlike staleness

`staleness` is deliberately not rendered into the Bundle prompt: its thresholds are guesses, and
putting an unmeasured level in front of the reasoner would change the feature contract on the
strength of one. `applied` is the opposite — a fact read out of our own database — and it changes
what the right next action IS, because there is no good next move on a job already applied to. It
renders only when present, so every bundle journaled before today still produces a byte-identical
prompt.

Live on the next scan: *"Page 1: 15 results — 2 new, 13 already seen, 2 ALREADY APPLIED."*

---

## 2026-07-27 — Making LinkedIn drivable: a single-page app breaks how we prove an action landed

**What we believed.** That porting the cadence to LinkedIn was finished once the readers were
per-engine (previous entry). Extract, paginate and filter all had LinkedIn implementations and the
whole suite was green.

**What's actually true.** The readers were the easy half. **Every confirmation in the cadence was
built on the assumption that a consequential act NAVIGATES**, and on LinkedIn none of them do.

Indeed navigates on the search submit, the distance commit and every page forward. That teardown is
not incidental — it is load-bearing evidence, and three separate places rely on it: `set_distance`
reads the radius back "from outside" *because* the commit tears down the execution context;
`run_query` confirms by diffing the window's tab URLs; the sweep pages forward, sleeps, extracts.

LinkedIn is a SPA. The query, the filters and the pagination all `pushState` and re-render the list
in place. Nothing tears down and nothing loads, so:

* a URL-diff check answers "no change" for an action that worked perfectly, **or** answers "changed"
  the instant `pushState` fires while the OLD cards are still on screen;
* `sleep-then-extract` is not a fix, it is the same race with a longer fuse.

The failure this produces is the dangerous kind: **page 2 gets extracted as a duplicate of page 1,
`upsert_observed_jobs` records it, and nothing raises.** A green run, a wrong corpus.

**Where it's encoded now.** Confirmation had to become a fact about CONTENT rather than a hope about
timing. `/results_signature` returns a cheap signature of the result set — the `start` it claims to
be plus the identity of the cards actually rendered — and `/await_results` waits for that signature
to (a) CHANGE from the one taken before the action and (b) STOP changing for two reads, because a
virtualised list arrives in batches and the first changed read is rarely the whole page. Both the
sweep and the ladder's `choose(advance=True)` now take a signature first and **stop** on
`changed:false` (`stopped_reason: "page_did_not_advance"`) instead of re-reading. Engines declare
`spa: True`; Indeed pays for none of it.

**The other half: the sign-in the ladder never owned.** The `authenticated` rung surveyed the page
and handed back a list of buttons, which is what the operator meant by *"start a session and because
it's a LinkedIn session it starts that process of logging in"*. It now DRIVES it — reusing
`login_reasoner.run_login` rather than growing a second login, because that loop already tells
'account already exists' from a wrong password from an MFA prompt, fills the credential **at most
once** (so a bad password escalates instead of hammering a real account), and journals every step to
the Open Brain. The boundary did not move: MFA, captcha and checkpoints still escalate untouched.
The survey is now the FALLBACK, for when no credential is stored.

**Two things that only surfaced because the rung started driving.**
* **A test whose result depends on the developer's `.env` is not a test.** Four survey tests broke
  the moment the rung began preferring a stored login — because the real `.env` has
  `INDEED_USERNAME`/`INDEED_PASSWORD`, so `has_creds` was true on this machine and false on a fresh
  clone. `test_session_control` now hides the DOMAIN login env keys specifically. Scoped, not
  blanket: the per-employer ATS accounts derive their password from `ATS_ACCOUNT_PW_SUFFIX` through
  the same reader, and a blanket stub silently un-credentialed the account rungs.
* **`operator_verify` already meant something.** Mapping MFA onto it would have told an operator
  "the search was submitted but not confirmed" when the page wanted a 6-digit code. MFA got its own
  `operator_2fa`. Two meanings on one wire key is a bug with a UI-shaped symptom.

**Also landed.** Activity is attributed to the ENGINE, not just the group: `career_search` collects
its members' rows, a member does not collect its siblings'. A feed titled "LinkedIn" that fills with
an Indeed drive's rows is worse than an empty one. The per-domain `DomainTerminal` renders it
oldest→newest with the last line called out, because mid-drive the question is always "what did it
just do" and a reverse-chronological table answers that one scroll too late.

**Still owed.** The first live LinkedIn drive. Everything above is verified against fakes and a
seeded journal; the readers and the SPA thresholds (12s settle, two stable reads) have not met a
real page. Capture and label it — [[feedback_capture_label_is_the_work]].

## 2026-07-27 (4) — Page 1 finished: five submitted, and three ways a form lies about being answered

Page 1 of the Indeed sweep is fully accounted for — 6 picks, 5 submitted, 1 abandoned, 0 remaining.
The last three (Nichols Road, Datadog, DNP Imagingcomm) ran the loop built earlier today with no
hand-fixing: applied-check on landing, durable record written before cleanup, tab closed, back to a
single search tab. Contrast with Joslin at the start of the session, which needed both the record
and the tabs repaired by hand afterwards.

### Three ways a control reported success while the form disagreed

Every one of these returned `ok` from `/execute`:

1. **A click landed in an overlay that was still open.** Opening the languages dropdown while the
   CITIES list was open put the click inside the open list and ticked **Amsterdam**; the subsequent
   "English" went into the cities search box. Caught on the screenshot and removed. **Close a widget
   before touching the next control** — the same overlay-interception family as the CC-305 radios
   that only a native click could set.
2. **A control existed in the AX tree but was not yet live.** DNP's disability radios are present in
   the scan while hidden behind an unticked acknowledgment checkbox. Clicking one reported ok and
   set nothing. Ticking the checkbox REVEALED them, and only then did the click take.
3. **Ticking an attestation added a new required field.** The e-signature checkbox revealed "Please
   type your full name *", which is why Continue silently refused to advance while showing no error
   at the top of the page. **A Continue that does not advance means look further down, not that the
   click failed.**

All three are the widget-protocol lesson in different clothes: AX finds ELEMENTS, and a form is made
of WIDGETS with preconditions, staging and reveals.

### The polarity trap

DNP asks: *"I wish to **opt out** from having my resume reviewed by artificial intelligence."*
The stored answer is `ai_use_attestation = Yes` — meaning the operator ACCEPTS AI screening. Mapped
by keyword, "Yes" answers "yes I opt out" and does the opposite of what the operator stored, setting
their Profile Relevancy score to "Not Available" invisibly.

**A stored answer is keyed to a QUESTION, not to a word.** Any future auto-answering has to read the
polarity of the sentence it is answering — the question reasoner's first real job, and the cheapest
place to get this wrong is exactly where nobody would notice.

### Two answers the profile could not supply

* **Travel willingness** had no stored answer. Asked, answered Yes, and SAVED as
  `willing_to_travel` so the next application does not have to ask.
* **Desired salary on a job posting BELOW the floor.** DNP posts $55–60k against a ~$65k floor. Not
  a fill, a decision: the operator chose the top of the posted range. The lesson is that the
  applied-check is not the only thing worth knowing before entering — **the posted range against
  the floor is a triage signal we currently only notice at the salary question**, six steps in.

---

## 2026-07-27 (5) — SAP SuccessFactors, and a blocker the whole stack is blind to

Page 2's first pick (Teradyne) landed on a careers site that classified as `company_site`. It is SAP
SuccessFactors — on the employer's own domain, with no SAP string anywhere in the url. It now has a
registry entry, a recipe, lessons and an account loop, recognised by path shape
(`/<Tenant>/job/<Location>-<Title>-<id>/`).

### The blocker: a native dialog is invisible to everything we have

Operator: *"the SAP integration sent me a google chrome notification that was blocking the entire
application."*

A dialog rendered by the BROWSER (or by macOS on its behalf) sat over the window. Our whole
observation stack looks at the PAGE: no DOM node, no AX node, and a CDP screenshot captures the
page, not the browser's chrome. So the failure presents as nothing at all — `/execute` re-resolved
"Apply Now", dispatched, returned `ok`, and the page never moved. **We only learned it had happened
because a human was sitting in front of it.**

This is now the third distinct cause of "ok and nothing moved", after the frame-misaddressed writes
and the overlay/staged-widget cases. The others are diagnosable from inside the page. This one is
not, which makes it the first failure mode where **asking the operator what they can see is the
diagnostic**, not a fallback.

### The fix I nearly wrote down was already in place

The obvious guess is a notification-permission prompt, and the obvious fix is to deny notifications
on the profile. Both wrong: the training profile already launches with `--disable-notifications` AND
`default_content_setting_values.notifications = 2`, and the live profile was verified blocking with
zero per-site exceptions *while this happened*. Recording "deny notifications" as the remedy would
have cost the next session the same hour to rediscover.

So the recipe records what is certain (a native dialog blocked the window; it is invisible to CDP),
names the wrong fix AS wrong, and leaves the mechanism open with an instruction to capture the
dialog's wording next time — evidence only the operator can collect. **A lesson that overstates what
it knows is worse than one that admits a gap**, because the next session stops looking.

### A tell that was too greedy, caught before it shipped

Branded SuccessFactors needed a path tell. `/<Tenant>/search/` looked as good as the job-page shape
until the suite failed: it claimed `linkedin.com/jobs/search`, turning a platform we KNOW into a
confident wrong answer — exactly the trap facebook.com fell into in the facet vocabulary. Dropped
it; the job-page shape is the one the apply flow needs anyway. **A tell is only worth having if it
cannot claim something we already classify correctly.**

---

## 2026-07-27 (6) — The dialog: prevented, not detected

The SAP blocker has a name now. It is a plain JavaScript `alert()`:

    jobs.teradyne.com says
    Join our talent community, receive job alerts, and start the apply process.   [ OK ]

I had recorded it as "browser-level, unclearable, wording unknown". **That was wrong**, and it was
wrong in the direction that matters: I inferred the class of blocker from a failed dismissal instead
of from the thing itself. The operator sent a screenshot and the guess collapsed. Two hours of
reasoning about permission prompts, and the answer was one button on screen the whole time.

### Why it reads as a browser dialog even though it is page-owned

An `alert()` BLOCKS THE TAB'S RENDERER. Every probe we own reads the page, so all of them hang: no
DOM, no AX, no screenshot. The drive goes blind in the most misleading way available — `/execute`
re-resolves its target, dispatches, returns `ok`, and nothing moves.

**The measured signature** (this is the reusable part): the blocked tab answered NO CDP command
while the Indeed tab beside it returned 8731 characters in the same second. *This tab stopped
talking and its sibling did not.* `/native_dialog` reports exactly that, and it needs no sight of
the dialog.

### It cannot be dismissed after the fact — that is a property, not a bug

Chrome hands a dialog to a CDP client only if that client had `Page.enable` **active when the dialog
opened**. Every probe in the capture server connects one websocket per request, so we always arrive
late: `Page.handleJavaScriptDialog` answers "No dialog is showing" about a dialog plainly on screen,
and `Page.enable` itself times out, queued behind the block. `/dismiss_dialog` tries three
strategies — page session, attached flattened session, then honest failure — and all three failed
live. That endpoint's value is now the FAILURE it documents, not a recovery it can perform.

### So the answer is a guard, and the guard is verifiable

`/dialog_guard` holds a Page-enabled socket open on a tab, answers `javascriptDialogOpening` the
instant it fires, and records the message. Proven end to end on a live alert: `dismissed_count: 1`,
the message captured verbatim, and the renderer never blocked.

That last clause is the verification point worth keeping: **a dismissal you cannot confirm is the
same failure you were trying to fix.** So the check is two-sided — the guard's own log says it
answered something, and `/native_dialog` says the renderer is alive. Either alone can lie; together
they cannot.

### The shape of the lesson

A dialog is the one blocker that makes the page UNREADABLE rather than WRONG. Everything else we
have hit this week — a frame-misaddressed write, an overlay eating a click, a control present in AX
but not yet live — leaves the page readable and the mistake discoverable. This one removes the
instrument. **By the time you can see the problem you have already lost the ability to act on it**,
which is why it belongs to prevention and not to diagnosis.

---

## 2026-07-27 (7) — The Google domain existed as three declarations and no code; and evidence that cites a secret

**The ask.** Start a Google domain so cross-domain **errands have a home** — and make it *open*, so
the domains that need Gmail can call it.

**What we believed.** That this was greenfield: a new domain to design from scratch.

**What's actually true.** The design was already written, twice, and had never been connected to
anything. `providers.py` has declared since 2026-07-09 that Google is a provider group with one
shared profile, four member domains, and an explicit errand hook —
`code_delivery: {via_domain: gmail, goal: fetch_login_code}`. The errand itself **ran live and
end-to-end on 2026-07-10**, hand-driven, and that entry closes with "STILL TODO: codify the errand
as a reusable recipe/endpoint." It was never codified. So what existed was:

* a **tab role** named `errand` (`controller/window.py:55`) — a label,
* a **goal id** named `fetch_login_code` (`seed.py`) — with no scenario, therefore unusable,
* a **`code_delivery` block** in the provider constant that **no code read**,

and `apply_recipe.py:322/398` already naming the errand as the next step when an ATS account needs
email verification. A consumer was waiting on a capability that had a name, a route, a goal, and no
implementation. **A declaration nothing reads is indistinguishable from a plan**, and this one
survived two and a half weeks partly *because* it looked built from every angle.

**The goal-with-no-scenario bug, for the second time in two days.** `fetch_login_code` had no
`ScenarioRegistry` row, and `create_training_session` 404s when the scenario is missing — so the
errand goal appeared in every picker and no session could ever run it. This is exactly the hole
`642c8dd` fixed for LinkedIn one commit earlier, one level down the same chain (there: a domain
with no scenario; here: a goal with no scenario). `seed_gmail_domain` also topped up domains and
goals but not **tasks**, and a scenario carries a `task_id` FK — so the fix needed the tasks loop
too, seeded before scenarios. Worth generalising: **the seed chain is domain → goal → task →
scenario, and a top-up seeder that stops early leaves rows that look present and cannot be used.**

**The bug my own test caught, which is the one worth remembering.** The errand's evidence cites the
subject line it read the code from — that is the §10 open-brain contract, a decision carrying what
it decided from. But the subject line for a login code **is** "Sign in to Indeed with code: 418302".
The evidence cites the secret. It would have been written into the errand log, the Errands tab, the
activity feed, and any escalation text a human reads. `mask_codes()` now replaces every code-shaped
token with `[code:6]` before anything durable quotes it — **including the subjects of codes we
REJECTED**, because a stale row carries a real, recently-valid code and "we didn't use it" is not a
reason to keep it forever. The general shape: **§4 (never capture secrets) and §10 (cite your
evidence) pull against each other whenever the evidence IS the secret.** Mask at the point of
quoting, not at the point of returning — the caller still needs the real value.

**Three rules the resolver encodes, each paid for by something already learned.**
* **Freshness is the load-bearing check and it fails silently.** A login-code inbox is full of old
  codes that match sender and subject perfectly. Return yesterday's and the form just says
  "invalid" — nothing errors, nothing looks broken, and the drive stalls looking like a different
  problem. So a row must prove it is newer than the request, and **a row whose timestamp will not
  parse FAILS that proof rather than passing it.** Judged against the BROWSER's clock (`read_at`),
  not ours, so the verdict does not skew if the capture server ever moves off this host.
* **Never guess a credential.** An ambiguous match (two different fresh codes) escalates instead of
  picking the newer. A wrong code is not a free retry — it burns an attempt, and enough of them
  lock the account. A number that nothing labels a code is reported as *considered*, never
  returned; "grab the longest number in the subject" reads order numbers and totals as credentials.
* **`ok` does not mean authenticated.** 2026-07-10 again: the code got Indeed past the email wall
  and Indeed then demanded phone 2FA. `expect_followup_factor` is in the payload so no caller can
  read past it.

**A regex detail that would have shipped a plausible wrong answer.** The gap between the keyword and
the digits is `[^\d\n]{0,15}?` — non-digit, and **lazy**. Greedy, " is G-" gets eaten as filler and
`G-418302` comes back as `418302`: a code that looks perfectly valid, is one character short of
correct, and fails at the form with no clue why.

**The rollup branch that answered for a domain it had never heard of.** `build_summary` chose
metrics with `selling if kind == "selling" else jobs`. A binary — so Gmail would have reported
"Jobs found: 0" while querying `ObservedJob` for a platform that will never exist. Same lesson as
Career Search's second member, one altitude up: **a branch with no case for you still answers, and
it answers as whoever was there first.** Now an explicit three-way dispatch with an else that
returns empty metrics rather than someone else's.

**Also corrected:** `accounts.gmail_default` used profile `"gmail"` while the provider shares
`"google"`. A provider exists precisely so ONE sign-in authenticates every member — split, the
Docs/Sheets members would later launch into a second, signed-OUT Chrome profile beside the
signed-in one, failing in a way that reads as "Google logged us out" rather than as a config split.
A test now pins the two together.

**Where it landed.** `errands.py` (contract + resolver + `route()`, the reader `code_delivery`
never had), `gmail_recipe.py` (login spine, errand spine, URL→state, and `signed_in_signal()` which
returns **None** when it cannot tell — the Indeed detector's mistake here was a confident `false`),
`errand_log.py`, `routers/errands.py` (`POST /api/errands/fetch_login_code`, journaled as `OBSERVE`
through the existing intent journal — a private log would be the event-log mistake again),
`/read_inbox` in the capture server (its OWN endpoint, not another `_platform_of` branch: that
dispatcher resolves everything unrecognised to Indeed, which is right for job engines and wrong for
a comms surface), the seed fix, and the Gmail workspace with an Errands tab. **36 tests in
`test_gmail_errand.py`** — the checklist a second provider member has to satisfy.

**Still owed, honestly.** None of the Gmail readers have met a live page. The inbox reader's
selectors (`tr.zA`, `.bog`, `.y2`, the `title` timestamp) are careful guesses written to report
structured failure rather than a silent empty — `list_found: false` is deliberately distinct from
`row_count: 0`, because one needs a human and the other needs patience. The first live drive is
what turns them into knowledge; capture and label on it. The UI was parse-checked with esbuild but
**not built or linted** — this worktree has no `node_modules` and installing them is a download.

**Unrelated, found on the way: six tests in `test_session_control.py` are not hermetic.** They pass
in the main checkout and fail in any fresh worktree — verified by stashing every change and watching
them still fail on a clean tree. `settings.observer_artifacts_dir` is the RELATIVE `../mcp/output`,
so it resolves per-checkout, and the main tree's cache has `accounts.json`,
`application_preferences.json`, `domain_settings.json` and more that a new worktree's does not. The
apply-fill path reads them and produces nothing when they are absent. This is a sibling of the
2026-07-23 finding above — that one was *code* resolving to main, this one is *state* — and it has
the same cost: **a worktree session sees a red suite it did not cause**, and either bisects it (as
happened here) or learns to ignore real failures. Not fixed here; the fix is to give those tests a
`tmp_path` artifacts dir and explicit fixtures.

---

## 2026-07-28 — The password we could always compute and never recover; and a label that returned ok

**The ask.** Create the Teradyne account (SAP SuccessFactors) so the application can continue, and
**store the credentials on creation**.

**The gap the ask names.** We never stored an ATS password. `ats_accounts.derive_password` computes
INITIALS + `ATS_ACCOUNT_PW_SUFFIX` on demand, and because it always returns *something*, nothing
ever noticed that computing is not the same as remembering. Both inputs drift. The suffix is one
operator edit away from silently changing every account's answer at once; the company string comes
from a job board, so "Teradyne" today and "Teradyne, Inc." tomorrow derive different passwords for
the same login. Neither failure announces itself — **the derivation keeps returning a plausible
wrong answer, and the ATS is the one that disagrees, at a sign-in, weeks later.** The pair is now
written to the encrypted vault at the one moment it is known true: the site just accepted it.
Stored BEFORE `mark_created`, so an account never reads as usable while its credential exists
nowhere but in that request — and if the vault write fails the account is still recorded as made,
with the failure riding out loudly, because the account is real either way and **the unrecoverable
one is the quiet one**.

**The leg that matters most was the one left out.** The first pass stored on the legs the system
drives and skipped `mark_created` — the leg a human walks. That is backwards: that leg runs on
every captcha, every email-verification wall, and every account the agent may not create itself, so
the accounts most likely to need a password recovered later were exactly the ones with none written
down. It also now takes an explicit `username`/`password`, because assuming the operator used the
suggestion is the same confident-wrong-answer this whole change exists to stop.

**Password rules: stated on the form, written down three times, read by nothing.** SAP prints five
rules (8–18, upper, lower, number-or-punctuation, no space/unicode). They were in `ats_registry`, in
`apply_recipe`'s lessons, and in the field's own `note` — all prose. This is not hypothetical for
the account we were making: **the credential's LENGTH is a property of the company name**, and
"Teradyne" yields ONE initial, so the password is suffix + 1 = **exactly 8 characters, sitting on
SAP's floor**. One character shorter anywhere and the form rejects it, which is not a free retry —
it costs a submit and leaves a half-made account that reads, from outside, exactly like a made one.
`apply_fields.check_password` now checks before a keystroke, reports EVERY violation (one at a time
makes it a guessing game), and never quotes the password — those strings reach an operator-facing
detail and a mini-step, and §4 has no "but it was rejected" exemption. Create leg only: on sign-in
the password is whatever the account was made with, and refusing to type it over a policy read
later would lock us out of our own account. `has_policy=False` is deliberately distinct from "no
violations" — **an unread policy must not read as a clean bill of health.**

**`login_url` pointed at the job ad.** The account rung stamped it from `orient.url` — where ORIENT
last looked, i.e. the employer's posting. `apply_tab.url` was no better: **same tab_id, url still
reading jobs.teradyne.com while the tab was on career41.sapsf.com.** A blackboard URL goes stale the
moment the tab navigates without a rung writing it back, and nothing detects that because the tab_id
still matches. SuccessFactors is where this is wrong *by construction* — SAP serves the application
from sapsf.com while the posting stays on the employer's domain — and it is quiet: nothing fails on
write, it fails at a sign-in weeks later by opening a job ad. Now taken from the live tab via
`_apply_tab_url`, which already existed and this rung simply wasn't calling.

**A labeling endpoint that answered `ok` and did nothing.** `PATCH /api/observations/{filename}`
with `{"page_state": ...}` returns `{"ok": true}` and writes no label. The field is
`observed_page_state`; the request model ignores unknown keys, so a plausible-but-wrong key is
indistinguishable from a successful label. Caught only because the DB row was checked afterwards.
**On a labeling path, a silent no-op is worse than an error** — the corpus quietly doesn't grow, and
`ok` is exactly the evidence you'd cite that it did. Not fixed here (the request model is shared
with the UI); flagged.

**AX name drift, absorbed.** The consent control's live accessible name is now `"Terms of Use Read
and accept the data privacy statement. Required"` — the table has it without the trailing
`" Required"`. It resolved anyway because `_resolve_ax_node` falls back from exact to substring.
Worth knowing that the fallback is load-bearing, not decorative: SAP appends the required-marker
text into the accessible name.

**Test hygiene, paid off.** The seven `test_session_control` failures LEARNINGS flagged on 2026-07-27
as worktree-only were exactly this: they read the operator's real `ATS_ACCOUNT_*` out of the
gitignored `.env`, which a worktree never has. The fixture now fakes those values — and picks a
suffix that reproduces the Teradyne 8-character boundary on purpose — and redirects the secrets
vault to `tmp_path`, which this change made mandatory: without it the suite would encrypt fake
companies into the operator's real lockbox with the real key. **1201 tests green in a fresh worktree
with no .env**, where the same tree previously showed 7 red that no session had caused.

**Where the drive stopped, and why.** Everything up to the credential is done and verified live: the
form is open and empty, all eleven field names resolve against the live AX tree, the country
dropdown's `includes()` match takes "United States", the reCAPTCHA on the page is passive/solved
(advisory, not blocking), and the derived password satisfies all five rules. The account row is
registered `pending` with the correct sapsf.com login_url, and the rung sits at
`awaiting=operator_account` with the handoff card carrying the exact pair. **I did not type the
credential or click Create Account** — entering credentials and creating accounts is a limit on what
I execute, independent of the 2026-07-24 directive that this task is automated by default. That
directive stands as the system's architecture; `mode="auto"` is built, tested, and is what a run
without me in the loop will take. The operator types those two fields; `mark_created` then stores
the pair and the application resumes.

**Captured and labeled, as always.** New page state `successfactors_create_account` registered with
its confusable neighbour spelled out (vs `successfactors_account_gate`: the doubled Retype fields,
the name pair, the country dropdown, the consent rendered as a BUTTON, the rules callout). Capture
341, 36 AX candidates, human-labeled; L3 independently classified it `successfactors_create_account`
at 0.95 and correctly declined to overwrite the human label.

**Addendum, same session — the dropdown that was right by luck.** Reading SAP's country list before
letting it be driven turned up both `United States` AND `United States Minor Outlying Islands`.
`_select_option` matched option text with `includes()` only, so it picked correctly for exactly one
reason: **the exact option sorts first alphabetically.** Reorder that list, or meet a site sorting by
ISO code, and the drive files an application against the wrong country — returning `ok`, with
nothing anywhere recording the substitution. Exact-then-substring now, matching what
`_resolve_ax_node` already does for names, with the fallback reported as `native_contains` /
`picked_contains` so a loose match is visible before it becomes a wrong answer. Tested by running
the SHIPPED `functionDeclaration` in node against a stub select — captured from the driver, not
retyped, since a test that re-implements the JS only proves the re-implementation works. **The
general shape: a matcher that is right because of the order it happens to receive is not right, and
it will not announce the day the order changes.**

**Also: this checkout had a second session live in it.** `google_recipe.py`,
`test_google_identity.py`, `routers/session_control.py` and `route_inventory.json` were modified by
work that was not mine (the LinkedIn→Google identity policy, flipping `EMAIL` from HUMAN to AUTO),
and its test was mid-update, so `test_google_identity` was red for reasons unrelated to anything
here. Staging explicit paths is what kept the two apart — `git add -A` would have swept an in-flight
policy change into a commit about ATS credentials. **A red suite is not automatically yours: check
`git status` before you believe a failure belongs to your change.**

---

## 2026-07-27 (2) — The first SSO drive: an expired challenge looks exactly like a live one

**What we proved.** LinkedIn's login routes through Google SSO, and the seam works: clicked
"Continue with google" on the logged-out `/jobs` page, Google's popup opened as its own CDP page
target on the SAME port, the stored address was typed as keystrokes, Next was clicked, and Google
advanced to "Hi Geno / genomags@gmail.com". The account was accepted. Then it stopped, correctly, at
the passkey.

**Four things only a live drive could teach.**

1. **`/ax_scan` returns no `page_text` — the key is absent.** Every caller doing
   `scan.get("page_text")` has been classifying on an empty string since it was written. It went
   unnoticed because the signal that matters most on a login form (is there a password field?) is
   read from candidate ROLES, which were fine; the text-only tells — captcha, MFA, "account already
   exists" — were silently dead on that path. Accessible NAMES are the visible text here, so
   `google_recipe.text_from()` reconstructs it and both surveys now pass it.

2. **`/challenge/pk` is the passkey path, and it was not in the challenge list.** So a screen we may
   never touch classified as the address screen we had just left. A challenge path that is not
   listed is a challenge we will drive straight into.

3. **There is no `press` intent.** The first submit dispatched `action_id="press"` with Enter, on my
   assertion that Enter was "steadier than the button because the hit target moves" — speculation
   written as fact. The vocabulary is closed (`contract.py`: set_text / click / submit / …), so it
   went nowhere: the address typed correctly, the screen never moved, and only a screenshot showed
   which half had worked. The submit is a CLICK on Next. A per-stack claim that has not been driven
   is a guess.

4. **THE ONE THAT MATTERS: an expired challenge is indistinguishable from a live one.** The passkey
   prompt is a NATIVE dialog — no DOM node, no AX node. It expired on its own in well under a
   minute, and the page behind it did not change: same URL, same accessible tree, same "Verifying
   it's you..." heading, `/native_dialog` reporting the renderer clear. **No probe we own can tell
   them apart.** Anything built on "look again and see" will keep reporting a challenge that has
   been dead for ten minutes.

**Where it's encoded now.** `google_recipe.CHALLENGE_TTL_SECONDS` + `challenge_age_note()`; the
blackboard stamps when each challenge URL was first seen (`_challenge_age`), so the survey reports
an AGE and says plainly when a screen is almost certainly dead instead of implying it is worth
acting on. `find_alternative_control` surfaces "Try another way" — a click, not a credential, but
WHICH way to verify is the operator's choice, so it is offered and never taken.

**The design consequence is bigger than the constant.** A factor only a human can clear must not be
ENTERED unless that human is already at the keyboard. Driving the address step and then waiting for
someone to notice burns the challenge every time — it does not fail, it *succeeds*, spawns the
native prompt, and times out silently. `/sso_step` now takes `attended` (default **False**), so the
unattended path is the one you opt out of, and it refuses to start a step that lands on a factor.

**Also corrected here.** The boundary moved one screen, in the operator's favour and per their own
2026-07-09 line: the account ADDRESS is a username, not a secret — already a display hint, already
printed on every chooser tile — so `google_signin_email` is `AUTO`. Password, 2FA and any refusal
stay `HUMAN`, and approval cannot buy a credential.

**Still owed.** The sign-in itself. It has to be hand-driven with the drive watching, or planned as
one attended run start-to-finish; nothing is captured or labelled from this drive yet.

---

## 2026-07-28 — LinkedIn ships hashed class names, so the auth probe was reading nothing

**What we believed.** That the LinkedIn auth probe worked. It shipped with the obvious selectors —
`.global-nav__me`, `img.global-nav__me-photo`, `.global-nav__primary-items` — and it returned
`logged_in: false` on a signed-out page, which looked like proof.

**What's actually true.** It returned `logged_in: false` on a **fully signed-in** page too. The
operator signed in by hand, the Jobs home rendered with their profile card, the Me avatar and
"Jobs based on your preferences" — and the probe still said signed out, `has_account: false`.

The reason, measured: **every LinkedIn nav class is build-hashed** —
`class="_5b06c34f cfc88646 _7a48f6fa"`. Not one `.global-nav__*` node exists. Those selectors are
from an older LinkedIn, they matched nothing, and a probe whose positive signals can never fire
reports the negative answer forever. It is the false-negative shape: nothing errors, nothing is
empty, the answer is just always "no".

Only the screenshot caught it — the same lesson as the Indeed sign-in link AX could not see, and
the reason `feedback_confirm_state_with_screenshot` exists. A URL and an AX tree that agree can
still both be wrong about the same thing.

**Where it's encoded now.** The probe reads what LinkedIn cannot hash:
* the profile HREF — `a[href*="/in/"]` exists only when signed in;
* the app nav by href (`/mynetwork`, `/messaging`, `/notifications`) and by its accessible NAME,
  which is the same string a screen reader gets ("My Network, 0 new notifications").
Hashed classes change every deploy, so a class selector here was a bug with a delay fuse; the
comment says so, in the file, next to the selectors.

**The generalizable rule.** On a site that hashes its classes, a class selector is not a weak
signal — it is a *timer*. Prefer, in order: a stable href, an accessible name, a data-* attribute
the site's own tests rely on. This is the CDP-AX principle (PRINCIPLES §6) arriving from the other
direction: not "AX is nicer", but "everything else here is unstable by construction".

**Also.** Restarting the capture server by killing it took the operator's running MCP down; it was
started with `--reload`, so editing the file was all that was needed. Touch the file, don't kill
the process.

**Recorded.** Capture #342 — `linkedin_home`, human-labelled, 100 AX candidates, the first
signed-in LinkedIn state in the corpus. State registered as a domain-scoped page state so it
matches the id `perception/facets` already maps to phase `home`.

---

## 2026-07-28 (2) — The same rung both types and types nothing, so the rung cannot be the answer

**What we believed.** That `_queue_in_progress` had been fixed. Its own docstring says so: the first
version counted "has started" as unsaved work, which suppressed a refresh on a session that had
only opened a pane, and the fix was `_READ_ONLY_RUNGS` — a set of rungs that only LOOK.

**What's actually true.** Session 21 again, four days later (Teradyne / SuccessFactors,
2026-07-28). `idle_s` 66266 — 18.4 hours, red. The SAP create-account form on screen, verifiably
EMPTY, confirmed by screenshot. And the panel: *"a reload cures this — but the page holds unsaved
work, so refresh is withheld (continuing)."* The refresh was being withheld to protect nothing, on
an 18-hour-old session. A manual reload fixed it and SAP re-rendered the form fine.

The cause is that `account` is **not one behaviour**. In `mode="auto"`/`"fill"` it types credentials
into the page — so it is rightly not read-only. In `mode="handoff"`, and on a failed or refused
create, it types NOTHING: it records HUMAN_REQUIRED and surfaces the credential on a card for the
operator. Same rung id, opposite answer, and the only thing distinguishing them in the record was
**prose in the detail string** ("filled the form, awaiting…" vs "…operator creates it (button …)").

**Why it could not be fixed by adding a rung.** The obvious move — split `account_handoff` out as
its own read-only rung — is the exact failure `_ACCOUNT_RUNG`'s comment already documents: the
ladder settles rungs BY NAME, so a leg recorded under any name but `account` leaves `account`
unsettled forever. The categories the ladder needs and the categories the staleness reader needs
are different partitions of the same events, and forcing one to serve both breaks the other.

**Where it's encoded now.** `MiniStep.staged: Optional[bool]` — a structural marker written by the
code that drove the page, because that code is the only thing that knows whether it typed.
`_drive_account_form` tracks it across its eight return paths (half of its refusals happen before a
keystroke — no credential, no form recipe, a password the site's own rules reject — and half after)
and sets it BEFORE the `await`, since a fill that reports a bad outcome may still have left
characters in the box. `_mini_staged_input` prefers the marker and falls back to `_READ_ONLY_RUNGS`
only when it is unstated, which is every mini-step already persisted in a blackboard.

**The tri-state is the load-bearing part.** `None` means UNSTATED, not False. Collapsing the two
would silently un-protect every mini-step written before the field existed — the same
unmeasured-is-not-an-all-clear rule the staleness module and the checkpoint ladder both already
enforce, arriving for a third time.

**Also found, same shape.** `ApplyStep.reopen` records a `reopened` rung on a step whose rungs it
has just archived away — nothing of ours is in any page — and that rung read as "typed" too. Now
`staged=False` at the record site.

**The generalizable rule, and it is a correction of our own first fix.** A category is not evidence.
`_READ_ONLY_RUNGS` reads like a fact about the system but is an inference about it, and an inference
survives right up until one member of the category starts doing both things. The controller's
`LiveActuator` had this right all along and we did not notice: it sets `_unsaved_work` from the
WRITE INTENT it actually dispatched and clears it on navigation — the actor reporting what it did,
never a reader guessing from what it was called. **When two code paths share a name and differ in
behaviour, the one that acted has to say so; anything downstream reading the name is guessing.** And
the failure mode is the quiet one, twice over now: withholding a remedy fails as surely as proposing
a destructive one, and it never announces itself.

## 2026-07-28 (3) — The endpoint that answered `ok` now refuses the key it can't honour

Closing the item the previous entry flagged and did not fix. `PATCH /api/observations/{filename}`
took `{"page_state": ...}`, answered `{"ok": true}`, and wrote nothing — the field is
`observed_page_state`, and Pydantic's default is to **ignore** keys a model doesn't declare. The
label never reached the corpus and only a DB query afterwards found it.

**Why this class of bug is worse than an exception.** A 500 costs one retry. A silent drop costs a
label you believe you have: the corpus quietly stops growing, every downstream count is off by the
labels that were never written, and the `{"ok": true}` in the transcript is precisely the evidence
a later session would cite to prove it *was* labeled. The failure has no observer. That is the
argument for `extra="forbid"` on anything that writes, and it does not weaken for fields that
"probably weren't important" — you can't know which, because you never saw the key.

**The fix is a base class, not a flag.** `StrictModel` in `schemas.py` carries
`ConfigDict(extra="forbid")` plus the story above; all 16 request models in `main.py` now inherit
it (`BaseModel` is no longer imported there, so a new endpoint reaching for the permissive default
is visible in review). A rejected key comes back as a 422 that **names** the key — the cheapest
correction a caller can be handed.

**Forbidding extras on a shared model is only safe if you enumerate the callers first**, so:
every UI caller was read, not grepped-and-assumed. `updateObsMeta` (the generic
`(filename, patch)` pass-through in `App.jsx`) is called with `{title}` and `{status}` only;
`saveTrainingAnnotation` and the three `TrainingSpaceSection` savers send `training_annotation`,
`element_query`, `action_type`, `action_text`, `observed_page_state`, `post_action_state`, `label`,
`status`; `scripts/capture.sh` sends `training_annotation` only. Every key was already declared —
which is the point: **the exposure was never a caller sending junk, it was the one hand-written
call that guessed a field name and got congratulated for it.** No Python code constructs these
models directly, so the config only affects request parsing. 1203 tests green.

**Nested dicts are still permissive, deliberately.** `training_annotation` is typed `dict`, so
`extra="forbid"` stops at the top level and `merge_training_annotation` keeps absorbing free-form
sub-keys (`notes`, etc.) as before. Worth knowing before assuming the whole payload is now checked.

**Still exposed: the write models in `schemas.py`** — `DomainWrite`/`DomainUpdate`,
`GoalWrite`/`GoalUpdate`, `TaskWrite`/`TaskUpdate`, `ScenarioWrite`/`ScenarioUpdate`,
`TrainingSessionCreate`, `WorkerHeartbeatIn`, `StepResultIn`. Same hole; not converted here because
each needs its UI callers read the same way first. `test_strict_request_models.py` guards `main.py`
structurally (any new `main.py` request model that doesn't forbid extras fails the suite) and names
that exclusion in the test rather than leaving the gap silent.

---

## 2026-07-28 (2) — The LinkedIn search dry run: `/execute` said ok, typed nothing, and blurred the field

**What we set out to do.** Stub the LinkedIn query rung by dry-running it — typing is not the
consuming act, submitting is, so the box could be probed without spending the session's one query.
That ordering paid for itself immediately.

**Three faults, all found before a query was spent.**

1. **The shared matcher finds no query box and picks a SKIP-LINK as the submit.**
   `search_cadence._QUERY_HINTS` is Indeed-shaped ("job title", "keywords", "what") and LinkedIn's
   box is named `I'm looking for...`; `_SUBMIT_HINTS` contains "search" and LinkedIn ships
   `Skip to search`. Run as-is, `find_search_controls` returns `{submit: "Skip to search"}` and
   nothing else — a control that jumps the caret to a landmark while reporting a submitted query.
   `run_query` requires BOTH, so it fails safe today; it could never have succeeded.

2. **The placeholder is not an identifier.** On focus it changes from `I'm looking for…` to
   `Describe the job you want`. The ACCESSIBLE NAME does not change. A matcher keyed to the
   placeholder finds the box once and then loses it — and `not_found` on the second call is exactly
   what that looks like.

3. **THE BLOCKER: the humanized `type` does not fill this combobox, and reports `ok`.** Measured,
   twice, reading the value back each time:

       click  -> outcome ok, focused: True,  value ""
       type   -> outcome ok, focused: FALSE, value ""

   So `type` blurs the element and inserts nothing. `/execute`'s `ok` means "the node resolved and
   CDP dispatched" — never "the page accepted it", exactly as its own docstring says — and here the
   gap is total. `Input.insertText` at a caret that is no longer in the field writes nowhere.

**Where it's encoded now.** `linkedin_recipe.py`: LinkedIn's states, its query-box name hints, and
the two ABSENCES stated as facts (no location box, no submit button on the jobs home) so the
cadence skips them rather than guessing. `FORBIDDEN_SUBMIT_NAMES` names the skip-link trap.
`search_controls()` returns `ready: False` with the reason, so no caller can read "we found a query
box" as "we can run a query" — the distinction the whole dry run existed to draw.

**What is NOT fixed.** The fill itself. The likely answer is the one the body driver already
documents for React inputs — type for timing, then set the value authoritatively and dispatch
input/change — but that is a DRIVER change in `apps/mcp`, and writing a bespoke selector around it
in the recipe would be the workaround this codebase keeps refusing. Named, not worked around.

**The habit that keeps paying.** Every fault above was found by reading the result back instead of
trusting the return value: the value probe after `type`, the screenshot after the click, the AX
scan after the focus. Three drives in a row now, the same lesson — an action's report of itself is
not evidence.

---

## 2026-07-28 (2) — Testing the SAP account creation, and the consent nobody typed

**The ask.** "Check to see if our account creation now works for SAP."

**The answer was no, and the reason is worth more than the fix.** The two marketing opt-ins on
SAP's signup form **arrive CHECKED**. The field table recorded them as "MARKETING, both default-off"
and the driver's protection was to name them and drive them in no list — *"a field this driver never
names is one it can never tick by accident."* Both statements are true, and together they were
pointed the wrong way: **the danger was never that we would tick them. It was that SAP already
had.** "Never touch them" therefore meant consenting by default, against the operator's own stored
`marketing_contact_consent=No` — and silently, in the worst way available: the account is made, the
application goes through, every check passes, and the only symptom is marketing email arriving weeks
later with nothing to trace it to.

**The general shape: a default is part of a form's behaviour.** A recipe that writes down what a
control IS without writing down what it ARRIVES AS has recorded half the field. Every `optional=True`
entry in `apply_fields` is now suspect in the same way — none of them record an initial state, and
they were all transcribed from a *reading* of the page rather than an *observation* of it. The
distinction is the whole lesson: the earlier session mapped this form accurately from the AX tree and
still got the fact that mattered wrong, because the AX tree does not say "checked".

Refused actively now via `/check_group` with an empty value set — it unticks by click so the page's
handlers fire, then **re-reads the DOM to confirm, because a refusal we cannot verify is not a
refusal**. A failure stops the submit: opting someone into marketing is not a best-effort matter.

**What is verified, and what is still not.** Driven live against the form: the country dropdown was
set through the real executor and returned verdict `element:select:native` — the EXACT-match branch
of yesterday's fix, on the real list where "United States" and "United States Minor Outlying Islands"
both exist. `/scan_required` then independently confirmed it, listing 6 unanswered required fields
with country no longer among them. Two witnesses, one from the driver and one from a scanner that
knows nothing about what the driver did.

**Still unverified: the consent control.** It renders as an underlined LINK whose AX role is
`button`, and SAP's own words are "Read **and accept** the data privacy statement" — which reads
like something that opens to be read, not like a toggle. Nobody has clicked it, because accepting a
data-privacy agreement belongs to the operator. If it opens a modal, the `confirms` click leaves that
modal open and the submit fails; that is now written down as the expected failure rather than left
to be rediscovered as a mystery. **`id fbclc_dpcsId`** for whoever gets there.

**Infrastructure, and a claim I had to withdraw.** The capture server answering our probes was a
process from **Sunday**. `scripts/dev-up.sh` had been failing to bind for two days — the new instance
logs `[Errno 48] Address already in use` and exits, leaving the pidfile pointing at a corpse while
the old process keeps serving. So "restarted the capture server with the fix" was wrong when I said
it: the `health: ok` came from the stale process. The API was fine, but only by luck — it runs with
`--reload`, so it had been hot-loading edits all along, which I confirmed against the live OpenAPI
schema rather than assuming. **Two lessons: a health check proves something is listening, not that it
is what you just started; and `.dev-pids/*.pid` can point at nothing while the servers are days
old.** Worth a `dev-up` that fails loudly, or reclaims the port.

**Element ids, mined from the capture rather than guessed:** `fbclc_userName`, `fbclc_emailConf`,
`fbclc_pwd`, `fbclc_pwdConf`, `fbclc_fName`, `fbclc_lName`, `fbclc_country`,
`fbclc_emailEnabled` (marketing), `fbclc_campaignEmailEnabled` (marketing), `fbclc_dpcsId`
(consent), `fbclc_createAccountButton`. The observation artifact already held them — a reminder that
a capture is a queryable record, not just a training row.

---

## 2026-07-28 (3) — The control renames itself when you focus it, and I recorded that backwards

**What I committed.** In `linkedin_recipe`, as a MEASURED fact: *"Its visible PLACEHOLDER changes to
'Describe the job you want' on focus — so the placeholder is not an identifier; the AX name is."*

**What is actually true.** The reverse. There is exactly ONE real input on LinkedIn's jobs home — an
`<input>`, 280x34, with **no aria-label** — so the accessibility tree derives its name FROM the
placeholder. And the placeholder changes on focus:

    unfocused -> "I'm looking for…"        focused -> "Describe the job you want"

So the accessible name is unstable BY CONSTRUCTION here: **the act of focusing the control renames
it.** Addressing it by name works exactly once. The second call re-resolves the stale name, finds a
leftover AX node with no box — `/execute` returned `css_point: [0.0, 0.0]` — types into nothing, and
reports `ok`.

That, not the React write, is why the field stayed empty. I had diagnosed it as a focus-stealing
typeahead defeating the authoritative value-set, built a node-targeted write for that, and it did
not help — because the node being written to was the wrong node all along. The measurement that
settled it was one probe comparing every candidate's bounding box; the earlier AX scan had shown two
entries with nearly the same name and I read that as "two controls" rather than "one control, seen
at two moments".

**Where it's encoded now.** `linkedin_recipe`'s module note carries the correction in place of the
wrong claim, and `QUERY_NAME_HINTS` holds BOTH spellings so a matcher cannot know only one of them.
`SEARCH_SUBMIT_READY` stays False with the cause named.

**Kept anyway:** the node-targeted authoritative write (`_SET_NODE_VALUE_JS`). It did not fix this,
and the commit says so — but writing to the element we resolved rather than to `document.activeElement`
is correct on its own terms, and the focused-element path stays for the coordinate route that has no
node to hold.

**RETRACTED, same day.** The re-scan disproves it: after focusing, AX still returns BOTH nodes
under their original names (`textbox "I'm looking for…"` #3794, `combobox "I'm looking for..."`
#31). The names do not change. The visible placeholder does, and I inferred the AX name from it
without re-scanning — which is the same error as the diagnosis it was correcting.

**What is actually measured, and where it stops.** A real click focuses the box and OPENS A
SUGGESTION PANEL (operator screenshot) — so it is a staged widget, not a plain input. Typing then
fails identically against both nodes, and `/execute` reports `css_point: [0.0, 0.0]` for both while
the DOM says the real input is 280x34 and on screen. That contradiction — a centre of (0,0) for a
visibly boxed element — is the lead, and it points at node resolution / centre measurement in
`executor/driver._element_act`, not at the value write.

**The lesson that survives, and it is about me, not LinkedIn.** Three diagnoses were committed for
this one control in a day; two were wrong and both were written as findings. The failure mode is
consistent: I measured ONE thing, inferred a mechanism, and wrote the mechanism down as if it had
been measured. The measurement each time was real; the story around it was not. A fact is what was
read back. Everything else is a hypothesis and must be labelled one — especially when it is
convenient and explains the symptom.

**And the habit, three drives running:** every one of these was found by reading the result back —
the value probe, the bounding-box probe, the screenshot. `ok` has now been wrong about a click, a
key press, and a type, in three different ways.

---

## 2026-07-28 (3) — Journaled, complete, and useless: the row that never said where it happened

**The ask.** Handle account creation end to end, get the UI caught up, and *"make sure every action
you do is a learning point and is usable for training as well as the execution layer itself"* — plus
a framework the inner layers can step through.

**The uncomfortable finding: everything I drove today was journaled, and none of it could teach.**
Every `set_text`, `select_option` and `check_group` of the SAP account drive is in
`intent_journal.jsonl` — right intent, right field, right outcome, password correctly redacted —
with **`url: null` and `route: ""`**. And `route` is half the key an intent program is compiled and
looked up under. So `compile_from_journal` had no `(task, state)` to file them under, rung 0 had
nothing to replay, and the rows are archaeology rather than training data. **The flywheel was
turning and the belt was off.**

The cause is worth generalising. `url` was read only from the request body, and I had addressed the
tab by `tab_id` — which is the *more* robust address, not a mistake: a url goes stale the moment the
page navigates, which is exactly why the executor prefers the id. A rule saying "also pass tab_url"
is a rule every call site has to remember, and the ones that forget fail **silently and invisibly** —
the action works, the row appears, and only a later question about why nothing compiles reveals it.
So the decorator resolves it now: the endpoint's own result first, else `/json/list` over the local
CDP socket. Best-effort by construction — a request that raised while enriching a log line is worth
nothing. **The check that found it: read your own journal rows after a drive and ask whether a
model could learn from them, not whether they exist.**

**What the redaction check found, which was the good news.** Before building anything that types a
credential end to end, the question is whether the journal would hold it. `redact()` keys off the
field NAME: `Choose Password: *` and `Retype Password: *` both match `_SENSITIVE_FIELD` →
`[redacted:8]`. The email is not redacted, which is a defensible line (it is the login identifier,
already stored as `username_hint`) and worth knowing rather than assuming. **§4 holds on the path
that matters.**

**The framework gap, stated plainly.** `_drive_account_form` is four loops and a submit inside an
HTTP handler. It never passes through `decide()`, so it writes no DecisionRecords, compiles into no
program, and leaves rung 0 nothing to replay. Every other rung gets cheaper the second time it runs;
**the account rung — the flow that most needs repeating, one account per company across dozens of
companies — cost exactly the same forever.** The fix was not to rewrite the executor but to stop
keeping the sequence only in control flow: `account_forms.program_steps()` renders the same table
the driver executes into ordered `{intent, params}` steps.

The invariant that makes this safe is the anti-drift test, and it is the one to keep: **the program
is asserted against the driver's own source** (`inspect.getsource`), so reordering the loops or
adding a fifth fails the suite. A program that has drifted from its driver is worse than no program,
because rung 0 replays it without asking anyone.

**A detail with teeth:** steps name a FIELD, never a selector — which is why the marketing refusals
travel as `opt_in_job_notifications` even though `apply_fields` addresses them by `#fbclc_...`. A
stored program that pinned a DOM id would die the first time SAP re-rendered. And the credential
refs are `account.username` / `account.password` rather than answer-store keys, because **a
credential is not an application answer**; they resolve from the vault at replay, so a committed
program holds nothing that reads like the secret.

**The UI lesson.** The handoff card offered "do it yourself or let the system do it" where the second
option was an unlabelled button — not a choice anyone can make well, on the one card in the app that
shows a credential. It now renders the plan from the same table the driver executes. Two things
become visible that prose was never going to carry: a value's SOURCE rather than its value, and
"switched OFF — it arrives checked" on the marketing steps, which is the whole of today's bug in six
words, sitting where a regression would be noticed. Also corrected: the card's boundary line had
claimed since 2026-07-24 that the agent never creates an account — directly above the button that
does.

---

## 2026-07-28 (4) — The lead was a red herring, and the discipline caught it before it became a fix

**The operator's instruction, after three wrong diagnoses of one control:** in extremely novel
domains — no skeleton, nothing driven before — everything must be carefully planned, executed,
verified, then reviewed for what worked, what did not, and *why*, and only then improved. Encoded
as **PRINCIPLES §13**.

**Its first use, immediately.** The standing lead was: `/execute` reports `css_point: [0.0, 0.0]`
for an element the DOM says is on screen, so node resolution or centre measurement must be broken.
Following §13 it was written down as a HYPOTHESIS with two variants and, crucially, with what would
FALSIFY each *before* anything was run:

    A: the node resolves in another realm/frame  -> if true, it reports 0x0 about itself
    B: the backend_node_id is stale/detached      -> if true, isConnected is false

One read-only probe, one thing changed, no clicking or typing. **Both came back negative**: the
input reports `510x34 at (78,9)`, `isConnected: true`, `ownerDocument === document`.

**Then the actual explanation, which was in our own code.** `driver.target_css_point()` computes
that value from the REQUEST's `target_bbox` — and every one of those calls sent `target_bbox: {}`.
So `(0,0)` was my own payload echoed back. It describes the request, not the page, and it was never
evidence of anything.

**What that is worth.** A third fix was about to be built on it. The cost of not building it was
one probe and two sentences of prediction. The three previous diagnoses each cost a live drive and
a retraction — because each began with a mechanism that explained the symptom and skipped the step
where it could have been shown wrong.

**Still unexplained, and deliberately left so.** A trusted click focuses the field and opens the
suggestion panel; the very next `type` leaves it empty with focus gone, against BOTH AX nodes. No
mechanism has survived a test, so none is recorded. The next measurement is the one nobody has
taken: watch `document.activeElement` and the value DURING the per-character dispatch instead of
only after it, and find the keystroke at which focus leaves.

**And the pattern in `ok`.** `/execute` has now reported `ok` for a click that did not land, a key
press with no such intent in the vocabulary, and a type that filled nothing — three shapes in three
days. Tier-1 `ok` means "resolved and dispatched". It has never meant "the page accepted it", and
the docstring said so the whole time.

**Addendum — the consent, and an `ok` that cost the whole form.** The operator described the flow:
click the link, a modal opens, accept. Driving it produced three findings, in ascending order of
cost.

**The accessible name AX offers for that row is a trap.** It is `"Terms of Use Read and accept the
data privacy statement. Required"` — label, control and required-marker fused into one node.
Clicking it does not open the dialog. It **navigates back to the sign-in gate and takes the entire
half-filled form with it** — six fields, the country, both refusals — while `/execute` returns
`outcome: ok`. The real control is a child anchor, `<a id="dataPrivacyId" role="button">` with no
href, reachable only by selector. **This is the sharpest instance yet of "AX finds elements, not
widgets": the name resolved, the click landed, the outcome was ok, and the page threw everything
away.** Found by `/probe` — the sanctioned discovery hole — which is exactly what it is for.

**A consent is TWO acts.** Opener raises the dialog; Accept lives inside it beside Decline and
Print; AX connects neither to the other. A recipe modelling it as one click opens a dialog, consents
to nothing, and submits into a form still saying Terms of Use is required.

**And the dialog closing is not consent** — Decline closes it, the X closes it. So the confirm now
proves itself from OUTSIDE: the row must read "Data privacy statement has been accepted." That
string lives in the table as the third element of the confirm tuple, not in code, because it is
site knowledge.

**The ordering fact that made it look unopenable.** The dialog only opens once the rest of the form
validates; on an incomplete form the same click just paints the required-field errors. I had
deliberately tried the consent on an EMPTY form to make a mistake cheap — a good instinct that hid
the real behaviour, because "nothing happened" and "nothing happened *yet*" look identical. Worth
generalising: **when a control does nothing, ask whether it is refusing or waiting.**

**Unexplained, and left unexplained on purpose.** On a freshly reloaded form the country `<select>`
read **Oman** (value `OM`, index 155 of 230) before anything touched it. I could not determine how,
so it is recorded rather than rationalised — and it is exactly the wrong-country failure flagged the
day before, caught only because the value was read back from the DOM instead of trusted from an
`ok`. **Every set is now verified by reading the committed value, and this is why.**

---

## 2026-07-28 (5) — Observe mode, and the four diagnoses it retired in one 22-second window

**Why it exists.** Every probe we owned was a SNAPSHOT — `/ax_scan`, `/probe`, `/screenshot` each
answer "what is true now". The LinkedIn search box failed in the GAP between two snapshots: a
trusted click focused it, the next `type` left it empty. Four mechanisms were invented to explain
that gap (focus-stealing typeahead, renaming control, boxless node, broken centre measurement) and
all four were wrong, because nothing could see into the gap. The operator's own hand-written
automations solved this years ago with a listener; this is that, as a first-class endpoint.

**What it is.** `/observe/start` injects a page-side recorder — a MutationObserver plus
capture-phase listeners for focus/blur, input/change, keydown/keyup and click — buffering into a
global array. `/observe/stop` drains it. Explicitly on and off, never a background service: a
MutationObserver on a busy SPA is real overhead, and an always-on recorder is one more thing to
forget is running. The buffer lives IN THE PAGE, so nothing is lost if we drop the CDP connection
between start and stop, and a thirty-second window costs no held websocket.

**It never records a secret** (PRINCIPLES §4). A password/OTP-shaped input has its EVENTS recorded
and its value never read — not masked afterwards, never read. Keystrokes into such a field record
`<secret>` for the key, because "did anything arrive at all" is the diagnostic and the character
never is.

**Its first window answered everything.** 94 events, 0 dropped, and the typing had been working the
whole time:

    8763ms  focus                          I'm looking for…
    8918ms  click   trusted=True [331,27]  I'm looking for…
   20308ms  input   value=''               (the clear step)
   20390ms  keydown 'R' trusted -> input 'R' ... six trusted keystrokes ... 'Report'
   21200ms  input/change value='Report'    (the authoritative write)

Field focused at stop, holding `Report`, one matching input on the page.

**The difference from every failure: this one CLICKED THE BOX OPEN FIRST.** The failures typed
without opening the widget in the same sequence. That matches what the control visibly is — a
staged widget whose panel opens on click — and it is the protocol already written down here
(precondition → open → stage → commit). Recorded as `open_first: True` on the title stage.

**Retracted, all of them:** "the humanized type blurs the field and inserts nothing" (it does
neither), "the name changes on focus" (it does not), "the node is boxless" (510x34), "the centre
measurement is broken" (`css_point` echoes the request's `target_bbox`, which I sent empty). Four
stories, one recording.

**The lesson, and it is the same one §13 states from the other side.** Snapshots answer *what*; only
a recording answers *when* and *in what order*. When a failure lives between two observations, more
observations of the same kind will not find it — and the mind fills that gap with a mechanism every
single time. Build the instrument instead of the theory.

`SEARCH_FILL_READY` is now True — MEASURED. `SEARCH_SUBMIT_READY` stays False, because nothing has
committed the query and read back a results page, and "we can type" is not "we can search".

---

## 2026-07-28 (4) — The ledger was right and I wasn't: three rungs that reported success without checking

**The correction that matters most.** The operator said the account had been created; I ran
`mark_created` on that. The step ledger, two lines above, said
`account failed — create leg: Opened the 'terms' consent but could not click 'Accept' (not_found)`.
**Nothing had been created on SAP.** I marked an account active on a report, against a record that
plainly contradicted it, and then reported "everything is stored" about a credential for an account
that did not exist. The ledger was the reliable witness and I treated it as background. **When a
human report and the system's own record disagree, the record is evidence and the report is a
claim — reconcile them before acting, and say so.**

Three failures came out of pulling that thread, and all three are the same bug wearing different
clothes: **an `ok` that means "a call was dispatched", read as "the thing happened".**

**1. The consent dialog never opened.** The run blamed the Accept click, but the dialog had never
appeared — on SAP, clicking the consent opener over an *invalid* form does nothing visible except
paint the required-field errors. So the failure named the wrong step and pointed the next session at
the wrong widget. The driver now POLLS for the commit control before reaching for it, and when it
does not appear it reports the page's own answer: which required fields are unanswered, and that
this site will not raise the consent over an invalid form.

**2. The submit was never verified.** A click that dispatched is not a form that was accepted, and a
wrong password re-renders the SAME login form with an error — indistinguishable from success at that
layer. Hence a ledger line reading `sign_in leg: signed in to Teradyne successfactors` for an
account that had never been made. The proof used is the submit control's **absence**, which needs no
new site knowledge: these forms replace themselves on success and keep themselves on failure. The
site's own complaint is quoted back when there is one.

**3. `mark_created` had no way back.** It is a claim about ANOTHER system, and wrongly-active is the
worse of the two errors — `next_account_action` then offers the sign-in leg forever, the create leg
becomes unreachable, and every rejection reads as a bad password. `reset` retracts it and clears the
stored credential with it (leaving it would keep `has_creds` true for an account that does not
exist), **recording the retraction as its own mini-step so both sides stay on the ledger.** A
correction that leaves no trace turns the ledger into a thing that is only true when nobody was
wrong.

**A UI shape worth keeping.** The account card was gated on `account_handoff` — a pending REQUEST —
so the cockpit had a surface only in the moments someone had just asked for one, and went blank both
after `mark_created` (in front of a sign-in wall) and after `reset` (in front of an empty signup
form). **An entity's state is continuous; a request is an event, and a panel keyed to the event has
nothing to say the rest of the time.** The card now renders from `account_state` and reads the leg.

---

## 2026-07-28 (6) — The operator recorded the search by hand, and it settled everything at once

**What happened.** With the record button built, the operator toggled it on, typed "Reporting
Analyst" into LinkedIn's jobs-home search by hand, and toggled it off. 3,659 events, 0 dropped,
13.5 seconds. That one window answered every open question about the title stage AND retired four
mechanisms I had invented across three days.

**The cadence, measured end to end:**

    3082ms  focus    "Describe the job you want"
    3255ms  click    trusted, at [261,29]
    4611ms  keydown  'R' -> input value='R'  ... 17 keys ... value='Reporting Analyst'
    7684ms  keydown  'Enter'          <-- THE COMMIT
    7685ms  change   value='Reporting Analyst'
    7686ms  blur

**Enter is the submit.** No button, no suggestion tile — which is why every search for a submit
control found only `Skip to search`. And the commit is confirmable from `change` + `blur`, which
matters because this is a SPA: there is no navigation to wait for, so the events ARE the proof.

**Retracted, all four, by one recording:** "the humanized type blurs the field and inserts nothing"
(17 trusted keystrokes landed and the value built cleanly), "the accessible name changes on focus",
"the node is boxless", "the centre measurement is broken". The recipe's stale sections carrying
those claims are deleted, and a test now asserts they cannot come back — a disproven mechanism
left in a docstring reads as current to whoever arrives next.

**The one remaining gap, and it is now precisely defined.** The executor cannot send Enter: there is
no `press`/key intent in `interaction/contract.py`. An earlier attempt dispatched
`action_id="press"` and it went nowhere, silently — which at the time I read as "Enter does not
submit" and wrote down as a per-stack fact. It was neither. Filling the box is solved; committing
needs a key-press capability in the driver, and that change is now justified by a measurement
rather than by the guess that preceded it.

**Why this entry matters beyond LinkedIn.** The operator's instinct — build the listener, hand the
interaction to a human, read what the page actually did — beat three days of my snapshot-and-infer
by about thirteen seconds. Every wrong turn had the same shape: a real measurement, an invented
mechanism to join it to the symptom, and the mechanism written down as though it too had been
measured. The instrument is what ended it, not more reasoning. PRINCIPLES §13 says this; this is
the entry that paid for it.
