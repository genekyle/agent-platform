# SESSION 16 — facts with an expiry date, and a report that finds the rot before a drive does

_Written 2026-08-26. Pick this up cold. Read `docs/PRINCIPLES.md` §14 first — this session is that
principle given a mechanism._

## The problem in one paragraph

`linkedin_recipe.RESULTS_TRAVERSAL` held `"scroll_endpoint": "/scroll_job_list"` (a **code-fact**:
true until we change it, and the suite goes red if we do) beside `"virtualised": True` (a
**world-fact**: measured 2026-07-30, falsified by LinkedIn on 2026-08-26 with no line of this repo
changing). Same dict, same review, same tests — and only one of them can rot unwatched. Meanwhile
`spec()["blocked_on"]` claimed a sweep dies at `/set_distance` **twelve days** after that stopped
being true, a session read it and planned around it, and **the test suite was asserting the stale
prose** (`assert "not been PRESSED" in still_unverified`) — so green tests were keeping a false
statement alive. There is currently no mechanism by which a world-fact can be noticed to have rotted.

## The work

**1. Give a world-fact a shape.** Small and boring:
`{claim, observed_at, drive (session + date), evidence (capture / screenshot / quoted reading), recheck}`.
Structured data, not prose. Recipes cite entries instead of embedding sentences. The evidence classes
from §13 (`MEASURED` / `HYPOTHESIS` / `UNVERIFIED`) become a field rather than a heading convention.

**2. Migrate one recipe as the pilot — `linkedin_recipe.py`.** It is the one with the most claims and
the one that just proved they rot. Its `verified_live`, `verified_live_2`, `still_unverified`,
`blocked_on` and the 08-14 radius retraction are the corpus to convert. **Keep the retraction** —
both sides of a correction stay (§10, §13, §14).

**3. The report that makes it self-correcting.** *Which claims about a surface predate the last drive
on that surface?* Everything needed is already banked: claims carry a date, and the transition corpus
records when we last drove each state. The output is a list of claims to re-verify, ranked by how
much later the last drive was — which is precisely how the virtualisation claim would have surfaced
weeks before it cost a session.

**4. Fix the tests that defend rot.** Every assertion pinning the *wording* of a perishable claim
becomes an assertion on its *shape*: dated, attributed, and separated from what was not driven.
`test_the_traversal_separates_what_was_driven_live_from_what_was_not` was already re-pointed this way
on 08-26 — copy that pattern, and sweep the suite for its siblings.

## Then drive — and let the report choose the drive

This is the pairing that makes the session pay twice: **run the staleness report, take its top entry,
and re-verify it live.** Two candidates are known in advance and both are real:

* *"the list is virtualised, one read returns ~7 of 25"* (07-30) — contradicted on the preferences
  landing on 08-26 (25 of 25 on the first read, a card ~3000px above the fold still found). Is that a
  renderer change, or a per-surface difference? Nobody knows, and the recipe still asserts one answer.
* *"`origin` survives a page turn"* — the basis for excluding `origin` from `result_set_identity`,
  and it rests on **exactly one** observation.

## Definition of done

* World-facts have a shape; `linkedin_recipe` uses it; the retraction survived the migration.
* The staleness report exists and is reachable from the cockpit.
* No test asserts the prose of a perishable claim.
* At least one stale claim was re-verified **live** and either confirmed with a new date or retracted
  in place with both sides kept.

## What NOT to do

* **Do not migrate every recipe.** One pilot, then judge whether the shape earned its keep.
* **Do not turn `LEARNINGS.md` into the store.** It keeps the *reasoning* and it is very good at
  that; at 11k lines it cannot answer "is the LinkedIn list virtualised?" without a human reading it.
  The store is its queryable twin, not its replacement.
* **Do not add a TTL that auto-invalidates claims.** A claim does not become false on a timer; it
  becomes *worth re-checking* when the world has been touched since. Rank, do not expire.
