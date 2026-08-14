# Reliability v1 — the four things that would change how it feels to use

**Adopted 2026-08-14**, out of an operator question after a full live drive: *"what are the
hardships, what do we need to add, and is this truly the best system to be building?"*

The answer that mattered: **the system never once lied about a failure** — every defect surfaced as
a refusal, not a false success — but roughly 85% of that session was repair and 15% was driving,
and it produced zero applications. The rails are the product; they are not yet boring.

Four steps, ranked by what they would have saved on the drive of 2026-08-14. Step 1 is built; 2–4
are near-future and deliberately unstarted.

---

## 1. Make "unmeasured" unrepresentable — **BUILT** (`interaction.measured`)

Six defects in one live application, and they were one defect wearing six masks: **a value in the
domain of the answer, where the truth was "I could not look" or "I looked at part of it."**

| What it claimed | What it had actually measured |
|---|---|
| "Apply" is *that* control | a name *contains* "search" / "apply" |
| two Apply links = ambiguous | two *rows*; never asked if same destination |
| Country isn't an option | 24 of ~250 options, cap unstated |
| the form is complete | required-and-empty only; optional-and-invalid unseen |
| this step's apply tab | *the session's* apply tab |
| 1 screen from Submit | over a tab that no longer existed |

Plus, from the log before that: `/auth_state` reporting `logged_in: false` on a signed-in session;
`/challenge_visibility` reporting `blocking: false` over two live hCaptchas it could not see;
the census reporting `unanswered: 0` with `url: ''`; `files.length == 0` meaning both "not staged"
and "ingested and reset".

The lesson is in `LEARNINGS.md` at least five times — *an address is a prediction*, *a probe that
found nothing has not found no*, *a scan that did not run is not a clean form*. **It was recorded
and not enforced**, so it was re-learned at each new call site at a cost of about one live
application per lesson. `packages/interaction/interaction/measured.py` is the enforcement point
PRINCIPLES asks every invariant to have.

* `Reading.measured(v, how=…)` / `.unmeasured(why)` / `.partial(v, shown=, total=, how=)`.
* **`bool(reading)` raises.** That is the mechanism, and it is deliberately violent: `if reading:`
  and `not reading` are the two lines that turned every incident above into a silent wrong answer,
  so they must not compile. You have to say which question you mean — `is_true`, `is_false`,
  `is_unmeasured`, `is_complete`, `value_or`.
* **`contains()` returns a Reading, not a bool** — the Country bug as a reusable primitive.
  Present is decisive; absent is only decisive on a *complete* reading.
* `all_measured` / `any_measured`, named for the question rather than the truth table. Each is
  decisive in exactly one direction, and **choosing the wrong one is itself the bug class** — a
  flaw the module's own first draft had, caught by its own test.
* Round-trips as JSON, because these readings cross mcp ↔ controlplane and that seam is precisely
  where the type would otherwise be erased back into the bare value that caused the incident.
  A payload with no envelope parses as UNMEASURED, never as a measured guess.

**Migrated so far:** `_unanswered_required` (was a hand-rolled `Optional[list]` tri-state whose
docstring told every caller not to merge the cases — one of those callers promotes a step to the
**Submit gate**); the census option cap surfaced in the cockpit.

### Migration backlog, highest risk first

1. **`/challenge_visibility`.** The rail descends same-origin frames and skips cross-origin ones
   with the comment *"cross-origin: fine, skip"* — sound for finding a captcha by the iframe
   element's `src` (which lives in the parent), but a captcha nested *inside* an unreadable frame
   is invisible and uncounted. Wants `any_measured`.
   **Open design question, and the reason this is not done yet:** naively, every cross-origin
   iframe (ads, embeds, the captcha's own frame) becomes a gap, and the rail would answer
   "cannot tell" on nearly every real page. An alarm that always fires gets ignored — which is the
   same failure as one that never fires. Needs a rule for which unreadable frames could plausibly
   host a form. Do not ship the naive version.
2. **`/auth_state`** — found-nothing reported as a negative. Unambiguous; straightforward.
3. **The census's `url` proof-of-life.** Measured 2026-08-14: `scan_required` returned `ok: True`
   with **`url: None`** *and* nine real fields, so the 08-12 "absence of url is an ERROR" rule
   would have raised a false alarm on a scan that plainly ran. Find where the url is dropped
   before gating anything on it.
4. `_apply_tab` — return whose tab it is, not just which one (partly addressed by the
   `tab_claims` guard in `_apply_cleanup`; the reader still answers a narrower question than
   callers ask).
5. `apply_flow` / `steps_to_submit` over a tab that no longer exists.

---

## 2. Bind refusal to exit, structurally

Named on 2026-08-13 after three instances; **four more appeared on 08-14** (the parked strip hid
the row the focus was not showing; a terminal flag can only be pressed on the *current* step; an
open step whose tab is gone still offers "Continue"; an ambiguous resolve has no way to say "I
mean this one"). It is not a bug that keeps getting fixed — it is a class that keeps regenerating,
because **a refusal is a string and its exit is hand-built somewhere else, with nothing binding
them.**

Make reach-parity a type rather than a discipline: a refusal carries its own actionable remedy
(endpoint + body + label), the cockpit renders refusals uniformly from that, and a refusal that
cannot name a pressable exit fails a test rather than shipping.

## 3. A "drive until you need me" loop

The single biggest change to how it feels to use, and **it needs no new safety.** On 08-14 one
application took ~15 button presses through gates that already exist — every advance runs the
census, the verify, and the operator-only Submit gate. The rails are built; the *composition* is
missing. Stop at: a real gate, a genuine ambiguity, an unmeasured reading, or a stop-state.

## 4. The gaps the drive found in the vocabulary

* **`already_applied` is a real outcome with no terminal flag.** Skipping the C&S duplicate had to
  borrow `abandoned:operator`, which means "you looked and do not want it" — a different fact, and
  one that will mislead the triage corpus.
* **Act on any step, not just the current one.** Flagging a queued duplicate needed the API.
* **"Fill the 6 ready field(s)" typed 5.** `summary.fillable` counts dropdowns; the bunch pass is
  text-only *by design* and says so in prose two lines above the button. The count and the promise
  disagree; the button should promise what this pass does, and the result should report what it
  did not attempt.

---

## The strategic note this plan sits under

Measured, and recorded here because it should be revisited rather than assumed: the local
perception/student stack has **0 trained checkpoints**, shadow at 61%, orienter at 59%, and on
2026-08-13 both local witnesses abstained at novelty 1.00 while the local belief was *wrong* at
0.99 reported uncertainty. Meanwhile every defect on the 08-14 drive was a **rails** defect, not a
reasoning defect.

At the current volume — single-figure applications a week — teacher tokens are not the binding
constraint; the operator's time and the defect rate are, and cheaper inference on a system that
closes the wrong tab only reaches the wrong outcome sooner. The unit-economics thesis may well be
right at product scale (the north star is task flows across domains, not job applications), but
the **sequencing is inverted**: the learning architecture is being paid for before the rails are
reliable enough to produce data worth learning from.

Recommendation on the table, not yet adopted: freeze the student stack — keep collecting rows, it
is free — and spend the next stretch making the rails boring. This is the second half of the move
already made on 2026-08-09, when the teacher was re-priced as the Claude session the operator pays
for anyway.
