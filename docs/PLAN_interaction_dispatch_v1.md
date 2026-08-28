# PLAN — Interaction dispatch v1: one door, one cycle, evidence that lands

**Status: adopted 2026-08-27 (operator-directed).** The operator's framing, verbatim in intent:
*"call for a new interaction profile … relate it to a particular ATS so we remove work from the
probe … our interaction layer needs the largest revamp because that's what's been slowing us
down."* Mapped under load the same night (LEARNINGS 2026-08-27 eleventh entry), the diagnosis is
sharper than "revamp": **the primitives are sound and half the profile system already exists; what
is broken is the DISPATCH between floors, and the evidence each floor produces that nothing
keeps.** This plan is that dispatch, made one thing.

Sits under `PLAN_generalization_v1.md` P3 (ADDRESS) and completes S19's direction. Not a rewrite:
every section names code that exists and the one seam that connects it.

---

## 0. The five floors, and where tonight's failures actually lived

| floor | what | where | verdict |
|---|---|---|---|
| 1 | **Intent vocabulary** — closed verbs, models never emit selectors | `packages/interaction/contract.py` | sound |
| 2 | **Addressing** — which node: AX role+name vs bespoke CSS | `_resolve_ax_node`, census selectors | **two doors, callers pick inconsistently** |
| 3 | **Widget classification** — what IS this control | `widget_probe.py`, `__kindOf` tells | sound |
| 4 | **Commit protocols + dialect cycle** — how does it commit, learned per (platform, family) | `protocols.py`, `/select_option`, `app/dialect.py` | **sound and UNREACHED by the ladder** |
| 5 | **The body** — trusted pointer/keys, humanized | `executor/driver.py`, `humanized.py` | sound |

Measured 2026-08-27, one Greenhouse form: the apply ladder's only select lever
(`apply_prompt_select`) routed to `/select_prompt` — the **Workday** hierarchical driver — for
every platform; `form_fill` computed `deferred_to_widget` and **no consumer read it**; six
required selects sat blocked while `/select_option`'s dialect cycle — built 2026-08-11 from the
operator's own words, exactly the "learned per-ATS profile" being asked for — sat one floor down,
never called. Inside the protocols, once reached: the option commit was a synthetic `el.click()`
(the untrusted-click class, sixth measured instance), `isSearchable=false` widgets were typed at
instead of looked at, and `select_option` spoke only CSS (the census's `#country` was the phone
dial-code widget). All fixed the same night; the dispatch is what this plan makes durable.

## 1. The verdict on per-ATS profiles, recorded

**Right instinct, right altitude already chosen by the dialect store: key by WIDGET ENGINE,
learn the ATS as a prior.** A platform renders one component library (Greenhouse = react-selects
everywhere; smartapply = portal listboxes; Cornerstone = native selects — `dialect.py`'s own
docstring, confirmed live repeatedly), so the profile that removes probe work is
`(platform, widget_family) → verified protocol`, learned on first success, offered first ever
after, demoted the moment it stops verifying. That exists. **Hardcoding profiles BY ATS would
repeat the name-borrowing mistake one layer down** — the witnesses borrowing `workday_review` for
a BambooHR page is the same disease as a hardcoded "Greenhouse protocol" applied to the one
Greenhouse tenant that customised its widgets. Data over code, learned over asserted (§15, §16).

## 2. The work

### W1 — the ladder routes through the cycle (the first commit of this plan)

* `apply_fill`'s execute pass drives `widget == "select"` rows through **`/select_option`** by
  accessible name (`target_name` = the field's question text — the AX door added 2026-08-27),
  after the text bunch. `deferred_to_widget` stops being a report and becomes the work list.
* `apply_prompt_select` keeps `/select_prompt` ONLY for multi-level paths (Workday's drill-downs,
  its specialty); every flat value goes to `/select_option`, whose internal dispatch still hands
  `prompt_hierarchical` back to `/select_prompt`. One entry, engine decided by classification +
  dialect, never by which endpoint a caller happened to know.

### W2 — one addressing door, selectors as adapter

By-name addressing is the default at every act seam; a CSS selector is either the derived output
of `_selector_for_node` (the adapter, 2026-08-27) or an explicit, commented exemption (census
anonymous inputs, where the "name" is a proximity label the AX tree never heard). `open_job_card`'s
bbox-and-coordinates path — measured losing to the AX door on a scrolled list — migrates behind
the same rule when next touched. This is S19 finished, not restated: count the doors on real
drives via `addressed_by`, which every act now reports.

### W3 — evidence lands on the element axis

Every floor already produces the evidence the 75% gate cannot see: protocol attempts verify at
the widget's own truth, dialect wins/losses are recorded, `addressed_by`/`mode` ride every act,
read-backs confirm or refuse. **None of it writes `BeliefState.uncertainty["element"]`.** The
wire: after each act bunch, the step's belief carries an element-axis reading derived from what
just happened — verified commits lower it, `not_staged`/ambiguity/untrusted-mode raise it — so
STOP_UNSURE covers "I can't reliably touch this page" and not just "I don't know where I am".
Same shape as `cookie_ttl_s`: the signal exists, the landing is the work. Journal raw components
per §13; thresholds stay the existing constants.

### W4 — the readers that lied this week, fixed at their read-point

Named debts from the same drive, each a read-point fix, not a retry: the dial-code chip verify
(`singleValue` reads "+1" for a correct "United States +1" pick — compare against the PICKED
option's text, not the caller's value, when the pick was exact); the privacy-select popup that
renders where no protocol looks; the two-"Attach" upload ambiguity (scope `within` to the
LABELLED section even when the input portals outside it); `/challenge_visibility` reporting a
solved checkbox as blocking (the BambooHR submit gate read stale state until a human re-ticked).

## 3. Falsifiers (§13, stated before the work)

* If after W1 a select-family field on a NEW platform still needs a bespoke endpoint to commit,
  the cycle's candidate list is missing an engine — add the ENGINE, never a platform branch.
* If the dialect store's hit-rate on second-and-later widgets of a platform is not measurably
  above its first-widget rate, the profile idea itself is wrong and this plan should be revisited
  rather than extended.
* If after W3 the unsure gate stops a drive on element uncertainty and the operator's look shows
  the page was actually drivable, the derivation over-weights a signal — fix the weights with the
  journaled components, never by widening the ceiling.

## 4. What NOT to do

* **No per-ATS interaction endpoints.** §6 stands; the FB-login case study is the tombstone.
* **No new resolver.** Two doors exist; the work is routing everything through one and deriving
  the other, not minting a third.
* **No confidence theater.** The element axis is written from measured act evidence or not at
  all — an axis filled with a guess is worse than an unassessed one (silence does not block;
  a wrong number does).

*One-line summary: the profile system is the dialect cycle and it already works — route the
ladder through it, keep one addressing door with selectors as a derived adapter, and land the
evidence every act already produces on the element axis so the unsure gate can see hands, not
just eyes.*
