# Where the data is, and which of it is siloed

_2026-08-20, after building the ATS database. Measured, not estimated: row counts from Postgres,
file counts and sizes from disk, join rates from actually running the extractor over the stores._

## The shape of the problem in one table

| Store | Size | Queryable? | Joined to anything? |
|---|---|---|---|
| Postgres — 25 tables | ~2,000 rows total | yes | yes |
| `observer-traces` | **2,455 files · 675 MB** | no | **no — until now** |
| `observer-screenshots` | **1,127 files · 458 MB** | no | by filename convention only |
| `derived` | 1,322 files · 15 MB | no | no |
| `models` | 89 files · 20 MB | no | n/a |
| `transitions` | 8 files · 4.5 MB | no | by `session_id` |
| `datasets` / `cache` / `training` | 80 files · 22 MB | no | no |

**Over 1.1 GB of captured observation lives outside the database. Postgres holds about two thousand
rows.** The largest asset in the system is the one nothing can query.

## The correction that matters

I first sampled **one** `step_runner` trace, found no `session_id`, no `url`, no job reference, and
concluded the traces carried no join key. That was true of that file and **wrong as a
generalisation** — the exact single-document-negative mistake this log recorded on 2026-08-18
("a negative result needs its search scope in the sentence"). Sampling every trace kind, then
searching *nested* rather than top-level, gives the opposite answer:

    primary traces        1,141
    joinable by URL       1,093   (96%)
    distinct ATS instances reached   38

The URL was never missing. It was **buried** — `.acquisition.page_identity.url` on captures, and a
different path per trace kind — so nothing had ever looked below the top level. One deep-search
function opens 675 MB.

## What opening it is worth

The transitions corpus knows **19** ATS instances. The traces reach **38** — twice as many, including
tenants the transitions corpus never saw at all (`workday:usbank`, `workday:wellington`). Every
denominator in the new `ats_characteristics` table is currently computed from the smaller half of
the evidence.

    612  indeed_quick_apply          25  workday:usbank
     98  company_site:facebook.com   25  cornerstone:bc
     49  linkedin_easy_apply         19  workday:cswg
     43  workday:eversource          15  company_site:jobs.mapfre.com
     37  company_site:bostonchildrens 14 brassring:368
     34  workday:solutionhealth      13  workday:wellington

## The gaps, ranked by what they cost

1. **The traces are unjoined, not unjoinable.** 96% carry a URL, and `ats_tenancy` turns a URL into
   an instance key. Backfilling them would roughly double every instance count and every
   denominator. *Cheapest high-value fix in the system right now.*
2. **The transition corpus does not record job identity.** No `job_key`, so 63 flows and 22
   applications cannot be joined — the reason `AtsFlow.job_key` is NULL on every backfilled row.
   Live flows should be written with the job attached; that is a one-line addition at the point the
   row is created, not a migration.
3. **458 MB of screenshots are addressed only by filename.** The transition rows reference them by
   basename, so they are reachable one at a time and not queryable as a set — "show me every
   confirmation screen we have ever seen" is not askable.
4. **`.ax` / `.meta` / `.vision` sidecars carry no key of their own** and rely entirely on the
   filename prefix matching their parent. That convention is undocumented and unenforced; a rename
   breaks a 675 MB relationship silently.
5. **Zero golden state labels** across 356 transition rows — 16 teacher corrections, no labelled
   states. Logged as open since June and still open.
6. **`Application.ats` is populated on 6 of 22 rows.** Any analysis routed through that column today
   answers from a quarter of the data; the new `ats_flows`/`ats_instances` tables should become the
   join instead.

## What is deliberately *not* a gap

`ats_registry`'s vendor catalogue staying in code is correct — it is small, curated, reviewed in
diffs, and 20 rows in a table would buy nothing. The split that matters is vendor-in-code,
**instance-and-measurement-in-database**, which is what shipped today.
