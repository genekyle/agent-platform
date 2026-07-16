# Execution = API — the architecture for the Career Search domain

**Status:** design, 2026-07-15. Written after driving one Workday application to submission and one
Greenhouse application to its last field, on live sites, in one session.

**The rule:** *the model decides WHAT; the API owns HOW.* Every interaction with a known protocol is
an API call. Hand-rolled `Runtime.evaluate` is for DISCOVERY only, and its output is a new API.

---

## 1. Why — in order of how much it actually matters

### (a) An inline script is un-learnable. This is the real argument.

Today's event log:

```
type:137  clear:92  click:80  select:32  widget_select:12  capture:43   eval:0
```

Every API call is recorded — kind, target, outcome, domain — so it feeds the event console, the
capture/label loop, and eventually the `state_transition` model. **The ~25 `/eval` scripts I wrote
this session appear nowhere.** They are invisible to the flywheel. The knowledge in them (how a
react-select commits, how to scope options, how to read a month) existed only in my context window
and would have died with the session.

The entire premise of this project is teacher → distill. **An action the system can't see is an
action it can never learn.** That alone settles it.

### (b) The protocol's hard-won details live in ONE place

Bug ledger, 2026-07-15 — every single one was in a hand-rolled script, not in an endpoint:

| Bug | Where |
|---|---|
| `/Concord/` picked "Concordia, Entre Rios, Argentina" | my inline regex |
| Verified `.value` on a react-select → false success on an empty field | my inline verify |
| Read stray `[role=option]`s as "already open", skipped the click | my inline open-check |
| `driver:"direct"` silently no-op'd on a controlled input | my inline fill |
| Missed required checkbox groups entirely | my inline scan |

`/widget_select` and `/execute` did not break. The protocol was written once, tested once, and the
caller couldn't get it wrong because it wasn't the caller's business.

Note the pattern in that table: **five of five are "something reported success that didn't happen."**
An API can encode "verify at the layer that COMMITS." A prompt cannot reliably re-derive that every
time — I had the lesson written down and still fell for the substring trap.

### (c) Tokens — real, but a rounding error next to (a) and (b)

`/widget_select {opener_selector, option_label}` ≈ 30 tokens vs ~400 for the JS blob I re-derived
each time. Nice. Not the point.

---

## 2. The layering (where this sits)

```
  WHAT            L4 / planner / recipe   "answer 'Phone Device Type' with 'Mobile'"
  ─────────────────────────────────────────────────────────────────────────────────
  HOW (protocol)  EXECUTION API           /widget_select → open → stage → confirm → commit
  ─────────────────────────────────────────────────────────────────────────────────
  MECHANISM       driver / CDP            focus, native click, insertText, trusted keys
```

- **L3 (perception)** — *what state am I in?* Reads captures (AX + screenshot). Unchanged by this.
- **The Execution API** — *how do I operate this control?* Transition-level. This document.
- **The recipe** — the DATA: which selectors, which vocabulary, which quirks per ATS.

The API is what makes a local model viable. A distilled model will never express "verify the
singleValue, not `.value`". It will emit `widget_select(opener, option)` and inherit that for free.

---

## 3. Known protocols (proven live — these are the assets)

| Protocol | Proven on | Status |
|---|---|---|
| **Staged-commit popup** — precondition → open → stage → confirm staged → commit → confirm outside | Indeed distance pill (footer `Update`) | `/widget_select` ✅ |
| **Apply-on-select listbox** — same, no footer | Workday State / Phone Device Type / veteran / gender | `/widget_select` ✅ |
| **aria-controls scoping** — the opener names its own popup | Workday (63 stray options → 5 scoped) | in `/widget_select` ✅ |
| **Hierarchical prompt** — native open + trusted per-char search + native option click | Workday "How Did You Hear" (U.S. Bank) | `/select_prompt` ✅ |
| **Month/year calendar picker** — tile aria-label carries "March 2026"; year nav self-verifies | Workday start date, signature date | ⚠️ inline only |
| **react-select** — per-char keystrokes to open; `aria-controls` appears only when expanded; `.value` empties after pick → verify at `singleValue` | Greenhouse country/location/all Yes-No/months | ⚠️ inline only |
| **Checkbox group** — group by id prefix before `[]`; exact label match; 0-checked = unanswered | Greenhouse restrictions/languages; Workday ethnicity | ⚠️ inline only |
| **Required-field scan** — `disabled` beats the label `*` and stale `aria-required` | Greenhouse End date (disabled) vs "If yes" details (still required) | ⚠️ inline only |
| **File upload** — `DOM.setFileInputFiles` (a click opens an OS dialog CDP can't drive) | Workday + Greenhouse resume | `/execute action_id=upload` ✅ |
| **Tab hygiene + record** | Wellington req R94007 | `/api/career_search/apply/epilogue` ✅ |
| **Branded-wrapper ATS detection** — query-param tells (`gh_jid`), then embed hosts | KKR → greenhouse | `classify_ats` ✅ |
| **Cross-origin iframe driving** — OOPIFs are their own CDP targets | Greenhouse embed | `_discover_target` ✅ |

**The ⚠️ rows are the backlog.** Each one is a protocol we have *proven* and are still re-deriving
inline every time — i.e. still paying for, and still un-learnable.

---

## 4. What else belongs in the API

### Execution primitives (the ⚠️ list, promoted)

- **`/select_option`** — generalize `/widget_select` to cover react-select (per-char open) as well as
  ARIA listboxes. One `strategy` field, or better: detect it (`aria-autocomplete=list` ⇒ keystrokes).
  Owns: scoping, exact-match, staged-confirm, `singleValue` verify.
- **`/set_date`** — polymorphic by widget, because we now know three shapes: Workday segmented
  spinbutton (calendar-picker only), Greenhouse month react-select + year number input, plain text.
  Caller says `{field, month:3, year:2026}`; the API figures out the shape and **verifies at commit**.
- **`/check_group`** — checkbox groups by id-prefix, exact label match, returns what's checked.
- **`/scan_required`** — "what is required AND unanswered", with the `disabled`-beats-asterisk rule and
  checkbox-group awareness baked in. Replaces the scan that missed two required groups today.
- **`/fill_form`** — batch: `{answers}` → form, atomic, returns per-field verified results. The
  natural home for the answer-store → widget mapping below.

### `/scan_form` is broken and should be fixed or retired

On Workday it returns the whole fieldset's text as every field's label, so First/Middle/Last are
indistinguishable. It's actively misleading — I stopped using it and hand-rolled instead. Either it
learns per-widget labelling or it goes.

### The vocabulary mapper — the interesting one

The answer store is **canonical**; every widget has its own **vocabulary**. Today:

| store | widget wanted |
|---|---|
| `education_start_date = "08/2015"` | month **"August"** (typing "08" → zero options) |
| `education_discipline = "Sports Science"` | **"Kinesiology"** (the offered near-match) |
| `education_degree = "Bachelor of Science"` | **"Bachelor's Degree"** |
| `education_school = "University of Santo Tomas"` | **"Other"** (genuinely absent from the list) |
| `ai_use_attestation = "I confirm"` | **"Yes"** (and the polarity can INVERT per employer) |

This is a real component, and it is **exactly the cheapest-first cascade** the project already
believes in:

1. **exact match** (free)
2. **normalised match** — case/punctuation/`Bachelor's` vs `Bachelors` (free)
3. **known alias table** in the recipe — `Sports Science → Kinesiology` (free, and it's the thing the
   operator can correct once)
4. **Haiku**, bounded: *"canonical answer X; offered options [...]; pick one or say NONE"* — ~$0.002,
   only on a miss, and the result gets written back to the alias table so it's free next time.
5. **NONE → ask the operator.** Never invent.

That last rung is where "Sports Science → Kinesiology" would have come from without asking — and note
the operator *volunteered* that mapping unprompted, which is the alias table wanting to exist.

**Polarity is a hard case:** the AI-attestation can be "confirm you did NOT use AI" (Yes = no AI) or
"did you use AI?" (Yes = used AI). A vocabulary mapper that ignores question polarity will silently
invert a real answer. `is_ai_use_attestation()` detects the field; the mapper must read the question,
not just the options.

---

## 5. When does discovery become API?

**Not "when it's perfected." Promote at the FIRST verified success.**

The intuition that it should be perfect first is backwards, and the evidence is right here:

- `/select_prompt` was promoted from **one** observation (U.S. Bank's Workday prompt). It was
  imperfect — `field_role` defaulted to `textbox` and Wellington's prompt is a `button`. That
  imperfection cost **one parameter**, fixed in one place, forever.
- My inline `/Concord/` regex was "perfect" the day I wrote it and wrong the first time it met a real
  option list. It would have been wrong again next session, because there was nowhere for the fix to
  live.

**An API's job is not to be right. It's to be the single place the fix lands.** An imperfect endpoint
is strictly better than a perfect script, because the endpoint accumulates and the script evaporates.

### The promotion rule

1. **Discover inline** (`/eval`) — you're allowed to be messy; you're learning a contract.
2. **The moment it works once, verified → promote it to an endpoint.** Even ugly. Even site-specific.
   Log it, so it's in the flywheel.
3. **Generalize the ABSTRACTION at the second site, not the first.** This is the part that needs
   patience — the *mechanism* is promoted eagerly, the *shape* earns generality through use:
   `_POPUP_SELECT_JS` was Indeed-only → Workday forced `aria-controls` scoping → Greenhouse forced
   keystroke-opening. Frozen after Indeed it would be wrong; delayed until "perfect" it'd be three
   scripts.
4. **Never freeze.** Sites change. The endpoint is versioned; the recipe holds the per-site data.

> **Promote the mechanism eagerly. Generalize the abstraction late. Never freeze.**

### What must NEVER become an API

- **Discovery itself.** By definition it meets something novel. `/eval` stays, forever, as the probe.
- But its **output is always**: a new/extended endpoint + a recipe entry + captured+labelled states.
  Discovery that ends in a working script and nothing else is a session we paid for twice.

---

## 6. Best practices (each one bought with a bug today)

1. **Verify at the layer that COMMITS, not the layer you typed into.** `.value` on a react-select
   reports the transient text and clears on blur — I "verified" an empty field twice. Workday dates
   *display* while the model stays empty. The API owns this; callers must not have to know.
2. **No silent fallbacks.** Four bugs today were a fallback quietly substituting a plausible answer:
   `set_distance`'s url rewrite, capture's wrong-tab, "already open" from strays, `_discover_target`'s
   first-page. **Test to apply: if the primary silently fails, what does the caller see? If the answer
   is "success", it's the wrong fallback.** Prefer `applied:false` + a reason.
3. **Every endpoint returns a per-step `log`.** `/widget_select`'s
   `precheck → open → select → commit` told me *which* step broke, instantly, every time. This is
   also exactly the label vocabulary L3 needs for intermediate states.
4. **Fail loud on ambiguity.** `job-boards.greenhouse.io` matched two targets; refusing beat guessing.
5. **Exact-match by default**, prefix as an explicit opt-in. "State" matched "United **State**s";
   "Concord" matched "Concord**ia**"; "No" would match "Yes, **no**n-compete".
6. **The recipe holds DATA, the API holds MECHANISM.** Selectors, vocabulary and quirks are per-ATS
   data. When a lesson lands in an endpoint's code instead of a recipe, it stops generalizing.
7. **Propagate a lesson to its siblings the day you learn it.** Three times in two days a fix landed
   in one call-site and not its twin (`/select_prompt`'s open never reached `set_distance`; AppVault's
   `direct` never reached the Workday leg; AppVault's verify-before-mark never reached it either).
   **Grep for the sibling in the same file before you close the fix.**
8. **Never invent application content.** Education stayed blank on Wellington rather than fabricated;
   UST → "Other" because the school genuinely isn't listed. The API should return `NONE`/ask, never a
   plausible guess.

---

## 7. Roadmap

**Now (unblocks the current backlog of 13 jobs)**
- [ ] `/select_option` — absorb react-select into `/widget_select` (detect `aria-autocomplete=list`)
- [ ] `/check_group` — required checkbox groups
- [ ] `/scan_required` — `disabled` beats the asterisk; groups included
- [ ] `/set_date` — the three known date shapes, verified at commit
- [ ] Fix or retire `/scan_form`

**Next**
- [ ] `/fill_form` batch + the vocabulary mapper (exact → normalised → alias table → Haiku → ask)
- [ ] Alias table in the recipe, written back on every operator correction
- [ ] Make `/eval` log an event (`kind:"probe"`) — discovery should at least leave a trace of what we
      probed, so the gaps are visible in the console
- [ ] Per-ATS recipe conformance: every ATS declares which protocols it uses

**Later**
- [ ] Distill: the loop emits intents (`select_option`, `set_date`); a local model picks them
- [ ] The per-step `log` becomes L3's intermediate-state labels (`popup_open`, `option_staged`) —
      currently the loop cannot verify its own progress through a multi-step widget

---

## 8. The one-line version

**Discovery is a probe; execution is an API; the recipe is the data; the log is the training signal.**
Anything the model does through `/eval` is invisible, unversioned and unlearnable — so it happens
once, on purpose, and its output is an endpoint.
