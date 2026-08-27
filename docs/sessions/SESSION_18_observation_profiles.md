# SESSION 18 — observation profiles: what to look at here, and what we could not see

_Written 2026-08-26. Pick this up cold; read `docs/PLAN_generalization_v1.md` §0 class 3 and §2
P1 first. This builds the operator's own 2026-08-19 design note ("pages need an OBSERVATION
profile — what to look at here — the way they already need an interaction profile")._

## The problem in one paragraph

The census enumerates form fields, so it confidently describes a page whose dominant features are
a dialog, a consent banner, an upload module, and a wizard step counter — and is silent about all
four (Paylocity, 08-19: *"7 required fields"* while the operator saw *"a giant module asking to
upload a resume"*). It invented 35 requirements and missed the 4 that blocked (08-19). It said
"all answered" on a step whose only content was one required textarea (08-19). It rendered "3
answer(s) — No, No, No" as question labels while the AX tree held every full radiogroup name
(08-23 — *"the census is worse than its input"*). Empty-AX-name widget families have been sighted
six times, each rescued ad-hoc by position mapping. **Silence reads as absence**, and a reader
that speaks first defines the page.

## The work

**1. A reading ORDER per page-kind, generic tier first.** A profile is a small dict, not a schema
language: for each `apply_landing` kind, the ordered witnesses —
`dialogs/alertdialogs/consent → wizard position (the site's own stepper) → validation/complaint
text (the press-Next harvest) → required census → the target control`. Platform overrides are
ADDITIVE (they sharpen the order or add a witness; they can never be required) — the exact shape
`submission_verifier.ATS_HINTS` already proved.

**2. The observer states what it could not see.** The orientation report carries a
`could_not_see` list: frames it could not enter, option lists truncated (with the true count),
zero-size/hidden inputs, empty-AX-name regions. "We did not look" must never render as "there is
nothing" — the `page_meta.has_next` tri-state rule, applied to perception itself. This is the
single highest-value line in the session: most of the window's observer failures were confident
silence.

**3. Position mapping becomes a named route.** The empty-AX-name rescue (bbox against visible
labels, proven on Cornerstone 08-24) graduates from ad-hoc to a listed fallback the profile can
name for a widget family — journaled as `addressed_by: position`, so its uses are countable and
its risk visible.

## Then drive, and let the drive prove it

Drive a modal-first page live — SchoolSpring/PowerSchool and Paylocity are both stored shapes,
and the BPS/MACOM parks are resumable. The profile must name the dialog BEFORE the census speaks;
the cockpit Lens shows the reading order and the `could_not_see` list. Then one ordinary form
step, to prove the profile does not slow or distort the common case.

## Definition of done

* Profiles exist for the generic kinds (dialog-bearing entry, form step, review, confirmation,
  wall) with the reading order pinned by tests.
* The observer's report renders `looked_at` (in order) and `could_not_see` in the cockpit.
* One live modal-first drive where the dialog is named first, and one clean form step.
* The six empty-AX-name sightings' families are covered by the named position route.
* `docs/LEARNINGS.md` entry.

## What NOT to do

* **Do not reach for a vision model.** The AX tree held the truth in almost every counted failure;
  the fix is reading order and honesty about gaps, not new eyes. (Pixels stay the witness of last
  resort for radio state and overlays — that is P2's territory.)
* **Do not write per-tenant profiles.** Platform tier at most; the generic tier must carry an
  unknown ATS (the TAM precedent).
* **Do not let the profile suppress a witness.** It orders them and annotates gaps; every witness
  still speaks. A profile that silences the census reintroduces the bug it fixes, inverted.
