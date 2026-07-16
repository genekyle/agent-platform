# The Interaction API — execution plan

**Status:** **Phase 1 SHIPPED 2026-07-16** (journal-first; see §8 and the deviations below).
Successor to `PLAN_execution_api.md` (the *why*); this is the *how*.

> ### What changed when Phase 1 met the code
>
> **The plan's founding argument was half wrong, and it was the half Phase 1 stood on.** §0 row 5
> ("Discovery is invisible — `eval:0` vs `type:137`") is a real measurement and a wrong inference: the
> event log is a 1000-line **ring buffer**, raced by two processes, with no fingerprint/session/
> outcome, read only by a React console. **No trainer reads it.** The real corpora
> (`loop_steps.jsonl`, `selection_telemetry.jsonl`) are written only by `runtime/loop.py`, which live
> drives never go through. Both `eval` and `widget_select` were at **zero**. Shipping §8's Phase 1 as
> written would have met its own DoD ("finish KKR using only these — zero `/eval`") and produced **no
> training data**. So **the journal landed first**, and the endpoints were built on top of it.
> (`docs/LEARNINGS.md` 2026-07-16; `PRINCIPLES.md` §8 corrected.)
>
> **Five more corrections, all found by implementing rather than designing:**
>
> 1. **The recipes were INERT, not just non-uniform** (§0 row 2, §4). No code path read *any* ATS
>    recipe's field entries — six addressing shapes across four sites, and nothing resolved against
>    any of them. The job was "make the recipe executable at all", so `resolve(ats, field)` moved from
>    Phase 2 into Phase 1 (`apply_fields.py`). Six shapes → two (`role_name`, `selector`).
> 2. **§6's taxonomy needs two more members.** `error` (a mechanism failure is not a protocol
>    outcome) and `committed_unconfirmed` (§6 assumes every endpoint *can* verify; the staged-commit
>    popup destroys its own observer, so `ok` is a silent success and `not_committed` is a false
>    negative that double-submits).
> 3. **§7's tiering makes §8's Phase-2 DoD impossible as written.** "Endpoints accept `field` and stop
>    taking selectors" can't be literal: tier-2 protocols are *"widget-shaped, site-agnostic"*, and a
>    site-agnostic endpoint cannot take a site-specific field name. Tier 2 takes **resolved
>    addressing**; the INTENT surface above it takes `field`. **"Zero selectors" = zero in the calls
>    the MODEL makes.**
> 4. **§2's `/describe_widget` must be READ-ONLY.** "options after open" contradicts `DESCRIBE` being a
>    read-only intent — opening dismisses other popups and fires fetches. Options are reported only
>    when readable without opening.
> 5. **§3's vocabulary needs `scroll`** (it's in the frozen `ActionId`, the loop emits it, and a verb
>    the system emits but the vocabulary can't express is a hole in the corpus) **and must not add
>    `clear`** (clearing is `set_text("")`). And INTENT is an altitude **above** the frozen
>    `ActionId`, not a rival to it — otherwise L4 trains on verbs the selector can't emit.

**Thesis:** the executor speaks **only** to the API. The model emits **intents from a closed
vocabulary**; it never writes JS, and it never sees a selector. Everything site-specific is DATA in a
recipe. Everything protocol-specific is CODE in an endpoint. Everything the model does is logged,
replayable and trainable — because it went through the API.

> **The model says WHAT. The recipe says WHERE. The API says HOW. The log says WHAT HAPPENED.**

---

## 0. What's actually broken today (measured, not asserted)

| Finding | Evidence |
|---|---|
| `/execute` has **five** ways to name a target | `target_bbox`, `backend_node_id`, `target_role`+`target_name`, `selector` — the caller picks, so the caller can pick wrong |
| **The recipe can't resolve anything** — schema isn't uniform | Workday: `{"role":"textbox","name":"Email Address"}` · Greenhouse: `"#first_name"`. Two different shapes for the same idea |
| **Tier violation**: domain skills sit flat beside universal primitives | `/set_distance`, `/open_job_card`, `/extract_jobs`, `/next_page`, `/fetch_job_description` live next to `/execute` and `/ax_scan` |
| **Widget classification is done by hand, every time** | I hand-wrote "inspect this widget" JS **~11 times in one session** (country, location, school, degree, discipline, 3× month, attestation, veteran, gender). Same shape each time |
| Discovery is invisible | `eval:0` in the event log vs `type:137 clear:92 click:80` |
| `/scan_form` is actively misleading | On Workday every field returns the whole fieldset's text as its label — First/Middle/Last indistinguishable. I abandoned it and hand-rolled |

**Read the 4th row again.** That's the spine of this plan: I *manually classified every widget* by
writing the same probe eleven times. That's not a missing endpoint — that's a **missing primitive**.

---

## 1. The four layers

```
  INTENT      closed verb vocabulary        select_option(field="Phone Device Type", value="Mobile")
              ↓ the model emits ONLY this. Trains L4. No selectors, no JS.
  ─────────────────────────────────────────────────────────────────────────────────────────────
  PROTOCOL    the API endpoints             open → stage → confirm staged → commit → confirm outside
              ↓ owns multi-step widget contracts + verification-at-commit
  ─────────────────────────────────────────────────────────────────────────────────────────────
  MECHANISM   driver / CDP                  focus, native click, insertText, trusted keys, OOPIF
  ─────────────────────────────────────────────────────────────────────────────────────────────
  DATA        the recipe                    field → {selector, widget_type, vocabulary, quirks}
```

Cross-cutting: **SEMANTIC** (`/resolve_answer`) turns a canonical answer into *this widget's*
vocabulary, and **PERCEPTION** (`/describe_widget`, `/scan_required`) tells the protocol layer what it
is looking at.

Rule: **a lesson lands in exactly one layer.** Selector changed → recipe. Widget behaves differently →
protocol. New CDP trick → mechanism. If a fix lands in the wrong layer it stops generalizing (today:
the `direct`-vs-`humanized` lesson lived in AppVault's *code* and never reached Workday's leg).

---

## 2. The missing primitive: `/describe_widget`

Everything else falls out of this. One structured probe replaces the eleven I hand-wrote.

```
POST /describe_widget  { ats, field }            # or { selector } during discovery
→ {
    widget_type: "aria_listbox" | "react_select" | "text" | "number" | "checkbox_group"
               | "segmented_date" | "month_year" | "file" | "prompt_hierarchical" | "unknown",
    opener:        {selector, role, aria_expanded},
    popup:         {ref, source: "aria-controls"|"aria-owns"|"document", option_count},
    options:       ["Yes","No"],                 # after open, if enumerable
    commit:        {kind: "on_select"|"footer_button"|"navigates", label: "Update"|null},
    value_read_at: ".value" | "[class*=singleValue]" | "aria-selected" | "opener_label",
    opens_on:      "click" | "keystrokes",
    required:      true, answered: false
  }
```

Why this is the spine:

- **`value_read_at` alone kills a whole bug class.** `.value` on a react-select reports transient text
  and clears on blur — I "verified" an empty field **twice**. Workday dates *display* while the model
  stays empty. The widget itself tells you where the truth is; nothing else has to know.
- **`opens_on` encodes the keystroke rule** (react-select fetches on real per-char input; a value-set
  leaves `aria-expanded=false`).
- **`popup.source`** encodes `aria-controls` scoping (Workday: 63 stray options → 5 scoped).
- **`commit.kind`** distinguishes Indeed's footer `Update` from Workday's apply-on-select.
- **Discovery gets a target**: a new site = a new `widget_type` in one classifier, not a new script.

Then `/select_option`, `/set_date`, `/check_group` **dispatch on `widget_type`** instead of the caller
knowing. That is what makes the intent vocabulary small enough for a local model to speak.

---

## 3. The intent vocabulary (what the model may emit)

Closed, small, semantic. **No selectors. No JS. No `backend_node_id`.**

| Intent | Args | Replaces |
|---|---|---|
| `set_text` | field, value | `/execute type` + clear + driver choice |
| `select_option` | field, value | `/widget_select`, `/select_prompt`, react-select, all by hand |
| `set_date` | field, month, year, [day] | 3 hand-rolled date protocols |
| `check_group` | field, values[] | hand-rolled checkbox groups |
| `upload` | field, path | `/execute upload` |
| `click` | control (semantic name) | `/execute click` |
| `submit` | form | the Save/Continue/APPLY click + outcome classify |
| `describe` | field | the 11 hand-rolled probes |
| `scan_required` | form | "what's required and unanswered" |
| `resolve_answer` | question, options[], canonical | the vocabulary mapper |
| `probe` | js | **discovery only** — logged as `kind:"probe"`, output must become one of the above |

`target_bbox`/`backend_node_id`/`target_role` collapse into **`field`**, resolved by the recipe. Five
addressing modes → one.

---

## 4. Recipe as resolver (not documentation)

Today the recipe is prose + inconsistent hints. It must become **executable data** with one schema:

```python
FIELD = {
  "phone_device_type": {
     "ats": "workday",
     "selector": '[data-automation-id="formField-phoneType"] button',
     "widget_type": "aria_listbox",          # hint; /describe_widget still verifies
     "answer_key": "phone_device_type",      # → the answer store
     "vocabulary": {"Mobile": "Mobile"},     # canonical → this widget's word
  },
}
```

- `resolve(ats, field) → {selector, widget_type, answer_key, vocabulary}`
- **The model never sees a selector.** It says `select_option("Phone Device Type", "Mobile")`.
- Selector churn = a data edit, not a code change. That is the whole point of the recipe.
- **Migration blocker:** Workday's schema is `role+name`, Greenhouse's is a selector string. Unify to
  `{selector | role+name}` with an explicit `addressed_by`, or resolution can't be written.

---

## 5. The semantic layer — where AI reasoning is actually required

The store is **canonical**; every widget has its **own vocabulary**; sometimes the question's
**polarity inverts**. This is the part that needs thinking, not matching.

```
POST /resolve_answer { question_text, options[], canonical_answer, ats, field }
→ { value, confidence, method: "exact"|"normalised"|"alias"|"model"|"none", rationale }
```

**Cheapest-first cascade** (the project's existing doctrine, applied here):

| Rung | Cost | Example |
|---|---|---|
| 1. exact | free | `"Mobile"` → `Mobile` |
| 2. normalised (case/punct/plural) | free | `"Bachelor of Science"` → `Bachelor’s Degree` |
| 3. **alias table** (in the recipe, operator-correctable) | free | `Sports Science` → `Kinesiology` |
| 4. **Haiku, bounded** — question + options + canonical → pick one or NONE | ~$0.002, only on a miss | granularity + polarity cases below |
| 5. **NONE → ask the operator**, then write the answer back to the alias table | one-time | `University of Santo Tomas` → `Other` |

Rung 4 writes its result to rung 3, so **every hard question is paid for once**.

### The tricky-question taxonomy (all real, all from today)

1. **Polarity inversion** — *"confirm your materials were NOT generated by AI"* (Yes = no AI) vs
   *"did you use AI?"* (Yes = the opposite). **Matching options without reading the question silently
   inverts a real answer.** This is the sharpest case for reasoning over regex.
2. **Granularity mismatch** — store says `Asian`; Workday offers Central/East/South/Southeast/West/
   Other. Unanswerable from the store alone → ask once, store `ethnicity_detail`.
3. **Near-match** — `Sports Science` → `Kinesiology`. Requires domain knowledge; the operator
   volunteered this mapping *unprompted*, which is the alias table asking to exist.
4. **Absent option** — `University of Santo Tomas` isn't in Greenhouse's list at all (verified: the
   list *does* carry Ateneo de Manila, so the absence is real) → `Other`.
5. **Format** — `08/2015` → month **"August"** (typing `08` yields zero options).
6. **Compound/nested** — *"family members (inclusive of Global Atlantic) ... a family member includes
   (a) related by blood, adoption, foster care or marriage; or (b) spouse, fiancé(e), domestic
   partner..."*. The definition is inside the question.
7. **Conditional reveal** — *"If yes, please describe..."* is required **only** if the parent is Yes.
   `disabled` beats a stale asterisk (today's required-field trap).

**Guardrails:** confidence threshold → ask, don't guess. Never invent application content (education
stayed blank on Wellington rather than fabricated). Log `rationale` so a wrong alias is auditable.

---

## 6. Error taxonomy — the anti-silent-success contract

**Five of five bugs today were "reported success that didn't happen."** Every endpoint returns a
discriminated outcome; `ok:true` means *verified at the commit layer*, never "the call didn't throw".

| Code | Meaning | Caller's move |
|---|---|---|
| `ok` | verified at `value_read_at` | continue |
| `not_found` | field didn't resolve | recipe is stale → re-map |
| `ambiguous` | >1 match | refuse; needs a more specific field |
| `not_opened` | widget wouldn't open | wrong `opens_on`? hidden tab? |
| `not_staged` | clicked, never took | protocol mismatch |
| `not_committed` | staged, commit failed | footer? navigation? |
| `no_option` | vocabulary miss | → `/resolve_answer` |
| `blocked` | captcha/challenge/session | escalate |

Plus: **every call returns a per-step `log`** (`precheck → open → select → commit`). It tells you
*which* step broke — and it is exactly the intermediate-state vocabulary L3 currently lacks
(`popup_open`, `option_staged`), which is why the loop can't verify its own progress through a
multi-step widget today.

**Test for any new fallback:** *if the primary silently fails, what does the caller see?* If the
answer is "success", it's the wrong fallback. (`set_distance`'s url rewrite, capture's wrong-tab,
"already open" from strays, `_discover_target`'s first-page — four bugs, one shape.)

---

## 7. Tiering — stop the flat namespace

```
  tier 3  DOMAIN SKILLS     /indeed/extract_jobs  /indeed/open_job_card  /indeed/set_distance
          composed from tier 2 + recipe data. Site-specific by design.
  tier 2  PROTOCOLS         /select_option  /set_date  /check_group  /scan_required  /fill_form
          widget-shaped, site-agnostic, dispatch on widget_type.
  tier 1  PRIMITIVES        /execute  /ax_scan  /describe_widget  /screenshot  /navigate  /probe
```

`/set_distance` is a tier-3 skill that should be *composed* from tier-2 `/select_option` + the
recipe's selectors — it already shares `_popup_select`, so this is mostly renaming and namespacing.
The value: **a new ATS adds tier-3 + recipe data, and zero tier-1/2 code.** That's the generalization
test.

---

## 8. Migration — strangler, not rewrite

The 13 remaining jobs do **not** stop. Each phase is shippable and paid for by the work.

**Phase 1 — the spine (unblocks the current backlog)** — SHIPPED 2026-07-16
- [x] **The intent journal** — append-only, fingerprint-joined, outcome taxonomy, redaction.
      *Not in the original list; it goes first, because without it Phase 1's DoD is vacuous.*
      `packages/interaction/` + `apps/mcp/app/intent_api.py`
- [x] **`resolve(ats, field)`** — pulled forward from Phase 2; `/describe_widget{ats,field}` needs it.
      `apps/controlplane-api/apply_fields.py` (32 fields, 3 ATS)
- [x] `/describe_widget` (the classifier: **12** `widget_type`s — the plan's 9 plus `native_select`,
      `radio_group` and `number`, all already handled as distinct kinds by the code it replaces)
- [x] `/select_option` — absorbs `/widget_select` + `/select_prompt` + react-select, dispatching on type
- [x] `/set_date` — month_year verified at commit; **segmented_date refuses (BLOCKED → operator)**,
      because CDP typing scrambles across the linked spinbuttons and a protocol that "tried anyway"
      would emit `12//` and report success
- [x] `/check_group` — required checkbox groups
- [x] `/scan_required` — `disabled` beats asterisk; groups included
- [x] `/probe` replaces `/eval` (moved up from Phase 3 — it's ~20 lines and it's the thing that makes
      discovery visible, which is the point of the phase)
- [ ] ~~Fix or retire `/scan_form`~~ → **deprecated in place, migration gated on the live drive.** Its
      callers feed `form_complete_gate`; swapping a *safety gate's* input to a scanner that has never
      run on a real page is exactly what PRINCIPLES §5 forbids. Diff both on a live form, then rewire.
- Definition of done: **finish KKR using only these — zero `/eval`.** ⟵ **NOT YET MET.** All the code
  is in and 119 tests pass, but no live drive has run. The page-side JS is unvalidated (§5), and
  that is the next session's first job.

**Phase 2 — the resolver**
- [x] ~~Unify the recipe field schema~~ — done in Phase 1 (`apply_fields.py`); six shapes → two
- [x] ~~`resolve(ats, field)`~~ — done in Phase 1
- [ ] The **intent surface**: `/api/interact/*` on the control plane, taking `{ats, field, value}`,
      resolving, and proxying to tier 2. *This* is what "endpoints stop taking selectors" means —
      tier-2 protocols keep taking resolved addressing (see correction 3 above).
- [ ] `/resolve_answer` cascade + alias table with write-back. The vocabulary data already exists in
      `apply_fields` (`Bachelor of Science → Bachelor's Degree`, `Sports Science → Kinesiology`,
      `University of Santo Tomas → Other`); the cascade that reads it does not.
- Definition of done: **an application driven with zero selectors in any call the MODEL makes.**

**Phase 3 — the contract**
- [ ] Namespace tiers (`/indeed/*`)
- [ ] `/probe` replaces bare `/eval`, logs `kind:"probe"`
- [ ] Error taxonomy enforced across all endpoints
- [ ] Per-ATS conformance: each ATS declares which protocols it uses
- Definition of done: **the executor's whole surface is the intent vocabulary.**

**Phase 4 — distill**
- [ ] The loop emits intents; a local model picks them (the API *is* the action space)
- [ ] `log` steps become L3 intermediate-state labels

---

## 9. Why this is the right architecture (and the honest trade-offs)

**Right:**
- **Trainable** — the action space *is* the intent vocabulary. A distilled model will never express
  "verify the singleValue"; it emits `select_option` and inherits it for free.
- **Fixable** — one place per lesson. Today a fix landed in one call-site and missed its twin three
  separate times in two days.
- **Trackable** — `eval:0` vs `type:137`. What goes through the API is visible; what doesn't, isn't.
- **Cheap** — ~30 tokens per intent vs ~400 per re-derived JS blob. Real, but the least of it.

**Trade-offs, stated plainly:**
- A closed vocabulary **can't express the novel** — which is exactly why `/probe` stays forever. The
  discipline isn't "never script"; it's "**scripting is discovery, and discovery ends in an endpoint**".
- Over-abstracting early is a real risk: `_POPUP_SELECT_JS` was right to start Indeed-only and earn
  generality at Workday, then Greenhouse. **Promote the mechanism eagerly; generalize the abstraction
  at the second site.**
- `/describe_widget` will be wrong on unknown widgets. Fine — `unknown` routes to `/probe`, and the
  probe's output is a new `widget_type`. The classifier is the flywheel's memory.

---

## 10. One line

**Discovery is a probe; execution is an API; the recipe is the data; the log is the training signal.**
The model never writes JS and never sees a selector — it says what it wants, and the system already
knows how.
