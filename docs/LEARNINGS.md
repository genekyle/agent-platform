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
