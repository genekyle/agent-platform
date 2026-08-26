# SESSION 14 — one fact, one place: kill the class that made 20 rows unjudgeable

_Written 2026-08-26. Pick this up cold; everything needed is here. Read `docs/PRINCIPLES.md` §16
first — this session is that principle applied to the one place we know it is violated._

## The problem in one paragraph

`ObservedJob.search_queries` (a JSON list: "queries that found this job") and `SearchSighting` (one
row per search × job) are **the same fact stored twice** — once as a claim anyone can assert, once
as an observation with a search behind it. When they disagree, only the second can be checked. On
2026-08-26 the corpus held **14 rows** claiming a query that never surfaced them (repaired, because
the join table could prove it) and **20 rows that can never be judged** — they carry a query, they
were also surfaced by real searches, and the write that added the query created no link. Those 20
are permanent. The door added on 08-26 (`observed_jobs.check_provenance`) stops *new* ones. This
session removes the possibility.

## The work

**1. `search_queries` becomes derived, not stored.** It is a display convenience; the join table is
the record. Find every reader first — `grep -rn "search_queries"` across `apps/` including the UI —
and decide per reader whether it wants *the queries that surfaced this job* (derive from
`SearchSighting` → `Search.query`, excluding feeds, which have none) or something else.

Sequence, so nothing breaks mid-flight:
  a. Add the derivation (a helper in `observed_jobs.py`; one query for a page of rows, not N+1).
  b. Point every reader at it, including `apps/controlplane-ui`.
  c. Stop writing the column — `upsert_observed_jobs` no longer takes `search_query` for the *row*;
     it still needs the query to validate against the `Search` (that check is §16's door and stays).
  d. Leave the column in place, unread, for one session. Then drop it in SESSION 15 once nothing has
     needed it. A migration that drops a column the same day it stops being read has no evidence
     behind it.

**2. The cheap one, while you are in the test suite.** `test_route_inventory` already pins every
route. Extend it: **every mutating route (POST/PATCH/DELETE) that drives the browser carries
`@journaled`, or names itself in an explicit exemption list with a reason.** This single assertion
would have caught `/open_job_card`, whose absence from the journal is the only reason the 08-26
half-failed sweep left no trace anywhere. Expect the exemption list to be non-trivial — that list IS
the finding, so write down what is on it and why.

## Then drive, and let the drive prove it

Do not end this session on green tests. **Run a live sweep on a real target** — session 34 is the
LinkedIn browser (port 9323), and `Reporting Analyst` / `Greater Boston` is an active target that
matches the operator's actual preferences (unlike the SWE-heavy preferences landing that session 34
was parked on). Two things to watch for, both of which are the point:

* the rows land with **no** `search_queries` column write and the derived value still reads correctly
  from the join table — the fact survived losing its duplicate;
* the 08-26 result-set guard **does not fire on a healthy sweep**. It has only ever been proven to
  fire correctly on drift; a guard that stops a good run is worse than the bug it prevents, and this
  is the first run that can falsify that.

## Definition of done

* Nothing writes `search_queries`; every reader derives it; the column is unread and still present.
* The route inventory asserts journaling, with a written exemption list.
* A live sweep banked rows, the guard stayed quiet, and `GET /api/career_search/provenance` reports
  **0 repairable and no new unadjudicable**.
* `docs/LEARNINGS.md` has the entry, including whatever the exemption list turned out to say.

## What NOT to do

* **Do not retro-fix the 20.** They are unknowable; the honest treatment is the count. Touching them
  is a second caller asserting things (§16).
* **Do not build a provenance framework.** Every win on 08-26 was one function long.
* **Do not drop the column this session.** One session of it being unread is the evidence.
