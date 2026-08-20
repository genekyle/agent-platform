# Do we have an ATS database? — no. Here is what we have, and what it already says.

_Written 2026-08-20, answering the operator's question directly. Numbers are from a real pass over
`apps/mcp/output/transitions/session_*.jsonl` (356 rows, sessions 25–32) and the live control-plane
API. Nobody had analysed this corpus before; this is the first pass._

## The short answer

**There is no ATS database.** There are three separate things that each hold a piece of it and do
not join:

| What | Where | Shape | Holds |
|---|---|---|---|
| Vendor catalogue | `apps/controlplane-api/ats_registry.py` | **hardcoded Python list**, 442 lines, 20 vendors | id, display name, hosts, auth, recipe pointer, prose notes, seed companies |
| One string per application | `models.Application.ats` | `String(40)`, denormalised | the vendor name, on 6 of 22 applied rows |
| The state traces | `apps/mcp/output/transitions/*.jsonl` | append-only JSONL, keyed by `session_id` | before/after URL, title, AX, belief, action, verdict, teacher correction |

So: a **catalogue in code**, a **column**, and a **corpus** — and no row anywhere represents *an ATS
instance*. There is no characteristics table, and nothing keys a state trace to the ATS it happened
on. The corpus keys on `session_id` + URL; the applications table keys on `job_key`. **They cannot
be joined today.** That is the actual gap.

## What the corpus already says (356 rows, 8 sessions, 20 distinct hosts)

### Tenancy is encoded differently by every vendor — an instance key cannot be the hostname

This is the finding that decides the schema, and it is invisible until you look:

    workday        cswg.wd1.myworkdayjobs.com          /CS_Careers/job/...      <- SUBDOMAIN
    peopleadmin    une.peopleadmin.com                 /postings/26341          <- SUBDOMAIN
    icims          careers-odysseyconsult.icims.com    /jobs/8308/...           <- SUBDOMAIN
    cornerstone    bc.csod.com                         /ux/ats/careersite/2/... <- SUBDOMAIN
    paylocity      recruiting.paylocity.com            /Recruiting/Jobs/Details/4382310/Isabella-Stewart-Gardner-Museum   <- PATH
    brassring      sjobs.brassring.com                 /TGnewUI/Search/Home     <- PATH
    linkedin       www.linkedin.com                    —                        <- NONE

Counting instances by hostname gives Workday **3 tenants** and Paylocity **1** — but we have driven
Paylocity for *two different employers* (Isabella Stewart Gardner Museum, Charles River Community
Health). The host axis silently undercounts every path-tenanted vendor. An `ats_instance` row needs
a **per-vendor tenant extractor**, not a hostname.

### Prediction difficulty already differs sharply by vendor — this is the trainable signal

Verdicts on rows where the system predicted and we observed (`confirmed` vs `mismatch`):

| vendor | observed | confirmed | mismatch | mismatch rate |
|---|---:|---:|---:|---:|
| brassring | 12 | 4 | 8 | **67%** |
| successfactors | 3 | 1 | 2 | 67% |
| paylocity | 3 | 1 | 2 | 67% |
| **company_site** (unmapped) | 19 | 10 | 9 | **47%** |
| workday | 44 | 31 | 13 | 30% |
| indeed_quick_apply | 113 | 85 | 28 | 25% |
| linkedin_easy_apply | 14 | 11 | 3 | 21% |
| cornerstone / icims / peopleadmin | 1 each | 1 | 0 | — (n=1) |

Corpus-wide: **146 confirmed, 65 mismatch, 99 read-only, 46 unobserved**, and **16 teacher
corrections (4.5%)**. The unmapped `company_site` bucket being the worst non-trivial performer is
exactly what you would predict and had never been measured. The small-n rows are flagged as small-n
on purpose — three observations is not a 67% failure rate, it is three observations.

### The state distribution names its own sore spot

    154  indeed_search_results          28  indeed_apply_questions
     74  indeed_apply_resume_selection  28  workday_create_account
     38  indeed_apply_review            25  workday_my_information
     36  workday_error_retry            25  linkedin_search_results
     30  workday_my_experience          21  indeed_home_logged_out
     30  workday_sign_in

`workday_error_retry` is the **fourth most common state in the entire corpus**. A state whose name
is an error outranking most real screens is a finding about Workday, not about the corpus.

## What an ATS database would need to be worth building

Not a table of vendors — `ats_registry` already is that, and moving it into rows buys nothing on its
own. The value is the three things that have nowhere to live today:

1. **`ats_instance`** — one row per *tenant*, keyed by `(ats_id, tenant)` where `tenant` comes from a
   **per-vendor extractor** (subdomain / path segment / none), plus the employer it belongs to. This
   is what makes "the next Paylocity employer costs nothing to meet" checkable rather than aspirational.
2. **`ats_characteristic`** — measured facts with provenance, replacing the prose in the registry's
   `notes` fields. Today "auth: account" and a paragraph of hard-won detail sit in a Python string
   that no model can read and no query can filter. Each row should carry *what was measured, when,
   on which instance, and by which drive* — the same evidence discipline as
   `submission_verifier`.
3. **`ats_flow`** — the join that does not exist: one row per attempted application, linking
   `job_key` → `ats_instance` → the transition rows that happened, with the terminal flag. This is
   the denominator `apply_requirements.summarise()` asks for and currently has to be handed by hand,
   and it is what turns 356 orphan traces into "we have driven Paylocity twice, and here is what
   both flows demanded".

The characteristics worth storing are already measurable from this corpus: tenancy style, auth
posture, mismatch rate, states seen, requirements declared (`apply_requirements`), and where the
résumé slot lands.

## Caveats, stated rather than buried

- **8 sessions is a small corpus.** Half the vendors have fewer than 15 observed rows. Nothing here
  supports a per-vendor rule; it supports a per-vendor *prior* with a visible denominator.
- **Zero rows carry a golden state label** — the 16 corrections are teacher corrections on
  transitions, not labelled states. The label gap this repo has logged since June is still open.
- **`Application.ats` is populated on 6 of 22 rows**, so any analysis joined through it today would
  be answering from a quarter of the data.
