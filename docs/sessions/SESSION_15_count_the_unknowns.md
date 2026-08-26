# SESSION 15 — count the unknowns, so naming becomes triage instead of collision

_Written 2026-08-26. Pick this up cold. Depends on SESSION 14 being done (the column drop below
assumes one session of it being unread)._

## The problem in one paragraph

We keep discovering screens that have no name — `linkedin_blended_search` (08-14),
`indeed_apply_resume_highlights`, the Indeed home feed as a third traversal shape (08-25), the
LinkedIn **preferences landing** (08-26, still unnamed). Every one was found the same way: **a drive
tripped over it.** That is discovery-by-collision — it has no order, no priority, and no end in
sight, which is exactly why the naming backlog *feels* infinite. It isn't infinite; it is
**unmeasured**. An unnamed state that nothing counts is a liability. An unnamed state that is counted
and ranked is a backlog with a burn-down, and the top few almost certainly cover most encounters.

We already have the primitive: state **fingerprints** in the capture/transition corpus. It has never
been pointed at this question.

## The work

**1. Rank the unnamed.** A report — endpoint plus a cockpit panel, because if the teacher can curl it
the operator can click it — answering: *which state fingerprints have we encountered, how often, and
which of them classify as `unknown` (or fall back to a parent state that is not really them)?*
Ordered by encounters, with a screenshot per row from the capture the fingerprint came from. The
output is a work queue, not a metric.

Watch for the trap the preferences landing shows: it classifies as `linkedin_job_search` — **not**
`unknown` — because `map_url_to_state` matches `/jobs/search` and the landing shares that path. So
"classifies as unknown" is too narrow a filter. Include **states whose fingerprints cluster into
more than one visually distinct group under a single name**; that cluster split is the signal that
one name is covering two screens.

**2. Name the top of the list.** The known candidate is the LinkedIn preferences landing — measured
08-26: `origin=PREFERENCES_LANDING`, *"Jobs based on your preferences"*, 99+ results, a filter row
cut down to `Date posted` / `LinkedIn Apply`, a dismiss ✕ per card, paginated like a search but
unrequested like a feed. It is a real state by the same argument `BLENDED_SEARCH` won: *a screen the
operator can describe in one sentence is a screen the system should be able to name.* Naming it is
the operator's call — bring them the ranked list and the screenshots, do not name unilaterally.

Note the modelling question it raises, because it is genuinely new: **paginated** and **unrequested**
turn out to be independent axes, and this surface is the corner nothing occupied. Decide whether it
is a `kind` on the existing Search row (like `query|feed`) or a third value, and write the reasoning
down either way.

**3. Quarantine the unjudgeable.** The 20 rows from 08-26 carry a query nothing can support. Right
now they are merely *reported*. Give them a flag so they are **excluded from training and from
anything that learns a query→job association**, and so the number is visible rather than
re-discovered by each audit. They should not silently vote.

**4. Drop `search_queries`** if SESSION 14 left it unread and nothing has needed it.

## Then drive, and let the naming prove itself

**Drive the newly-named state live** and confirm the classifier emits it, the transition corpus banks
it under the new name, and the traversal chosen for it is the right one. A name that no drive has
exercised is a rename waiting to happen.

## Definition of done

* A ranked unnamed/ambiguous-state report exists, in the cockpit, with screenshots.
* The top entry is named (operator-approved), classified live, and banked under its own name.
* The unjudgeable rows are flagged and excluded, with a visible count.
* `search_queries` is gone.

## What NOT to do

* **Do not reach for a classifier or a model.** Count first. The counting may show five names cover
  it, which no model beats on cost.
* **Do not name a state the operator has not seen.** Every prior name in this repo came from a screen
  someone could describe in a sentence; that is the bar.
