# SESSION 20 — an act carries its evidence: ok means committed, not dispatched

_Written 2026-08-26. Pick this up cold; read `docs/PLAN_generalization_v1.md` §0 class 2 and §2
P2 first. Depends on SESSION 19 (evidence read-back needs the right node)._

## The problem in one paragraph

`ok` still means *dispatched*. This window alone: radio misfires 3 of ~10 teaches, every one
reporting ok, one of them landing the disqualifying answer on a sponsorship question (08-21,
08-23); an upload staging `files=0` reporting ok while a different upload was refused for a probe
that raised (08-24); a consent checkbox "filled" as text, reported as success (08-21); a State
select ok-and-blank because option text and value differ (08-24); a date widget rendering staged
segments while `answered=False` (08-25). The screenshots were the only witness, twice — and a
census can only ever confirm ANSWEREDNESS, never correctness. Meanwhile the cheap recognizers
that end these stalls exist as prose rules scattered through LEARNINGS: captcha-first,
untrusted-click-retry, staged-not-committed, overlay (grey-Cancel), refresh-first. **A rule in
prose fires only when someone remembers it.**

## The work

**1. The commit-evidence contract, per widget engine.** A mutating intent's success carries the
read-back from that engine's own read point, taken INSIDE the intent (never a second census
pass): checked state read back within `check_group` (the group, after the click); `files.length`
probed on the input actually fed (08-24's rule); the committed value at the engine's
`value_read_at` for selects/prompts (opener accessible name where `singleValue` lies — the 08-19
Paylocity lesson); hidden-node presence for Workday dates (the `aria-labelledby` tell). *"I wrote
it" and "the widget committed it" are different claims* (08-25) — the result now says which one
it is making. A read-back that cannot be taken is reported as `evidence: none`, loudly, never as
success.

**2. The recognizer chain as code at the act seam.** `ok` + no observable change auto-runs, in
order, before anything retries or grinds: `/challenge_visibility` (captcha-first, re-run at the
moment of the dead press — challenge state flips fast, 08-23) → trusted-click coordinate retry on
the same target (08-25's one-call recognizer) → overlay check (a control that should never be
disabled rendering grey — the ad-overlay tell, 08-21) → surface with a screenshot. This is the
two-tries-then-look rule promoted from feedback memory to dispatch, and it must journal which
recognizer resolved the stall.

**3. Verify's vocabulary, honest.** `verify()` gets its missing `expected_next` branch (rows
falling through to UNOBSERVED today, 08-22), and `mismatch` splits into `mismatch_kind` —
world-did-not-move vs supervisor-judged-non-nominal — so the label queue stops ranking two
different facts as one class (the open item from PROJECT_STATUS).

## Then drive, and let the drive prove it

A live form with radios, an upload, and a select (any smartapply screener + one ATS form step).
Every commit in the trail shows its evidence. Then deliberately address a select with a value its
options don't hold — the refusal must enumerate the real options (`no_option` with the
vocabulary, the good failure from 08-19). And one manufactured stall (a decorative click) to
watch the recognizer chain fire and journal its resolution.

## Definition of done

* Evidence read-backs on the three riskiest families — radio/checkbox groups, uploads,
  selects/prompts — each pinned by a test that fails when the read-back is faked.
* The recognizer chain fires at the act seam, journals which rung resolved a stall, and is
  covered by an output-observing test (the swallow-by-design standard, 08-23).
* `expected_next` branch + `mismatch_kind` landed; the label queue ranks by the split.
* One live drive whose trail shows evidence on every commit, zero silent false successes.
* `docs/LEARNINGS.md` entry.

## What NOT to do

* **Do not re-run a fill to re-verify.** A fill is not idempotent over a stateful control
  (08-19); evidence is read, never re-acted.
* **Do not gate read-only intents on evidence.** A look has no commit; forcing evidence there
  manufactures noise (the observe-rungs-stay-empty lesson, 08-23).
* **Do not let the chain retry more than once per recognizer.** Its whole point is to replace
  grinding (three failed tries on one control was already the surface-the-stall bar); a chain
  that loops is the grind wearing a uniform.
