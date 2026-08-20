# SESSION 13 — finish the data-analyst drive, and farm the education ATS family

_Written 2026-08-20 at the end of a long session. Pick this up cold; everything needed is here._

## Where things stand

Session **#32** is live on the Indeed persistent profile, signed in. Two searches have run in it:

* `report analyst` · Concord NH · 100mi — **walked out, 4 pages.** 2 submitted (HopeWell via Indeed
  quick-apply; Isabella Stewart Gardner Museum via Paylocity), 1 parked at a PeopleAdmin account
  wall (University of New England).
* `data analyst` · **Manchester NH · 50mi** — page 1 read, 22 results (13 new, 9 seen, 1 already
  applied). **Three picks queued and none finished:**

  1. **Data Analyst for Strategic Initiatives** — Boston Public Schools (up to $104,675) — on
     **SchoolSpring**, standing at the PowerSchool login wall
  2. **Enterprise Applications Analyst** — Beacon Communities LLC ($90–100k) — untouched
  3. **FP&A Analyst** — Safran Defense & Space, Bedford NH ($75–125k) — untouched

## The first thing to get right

**The credential boundary stops one ACTION; it does not end an application.** Pick 1 was parked at
its login wall on the agent's own judgement, twice in two days on two different ATS, and the
operator's correction is the standing rule now (`feedback_finish_the_thing_dont_switch`):

> keep going up to the boundary → take down every clue → **then ask** → never flag a terminal
> because the agent decided the work was over.

So: pick 1 is **reopened and live**. Farm the sign-up form's shape (fields, requirements, whether an
email code is involved) without typing anything, then ask the operator whether to log in.

## What was learned about the ATS, and what to do with it

**SchoolSpring and PeopleAdmin are the same vendor.** SchoolSpring's *"Apply for this job!"*
redirects to `auth.powerschool.com/u/login/identifier`, and PowerSchool owns PeopleAdmin too. The
operator asked days earlier whether colleges share an education-specific ATS and the first answer
was "no — they buy commercial ATS like everyone else". **That answer was too confident.** Better:
not one product, but **one vendor family across K-12 and higher-ed with a shared identity
provider**. Both registry entries now carry a `SECTOR = EDUCATION` note; carry it as a prior when a
school or university posting routes off Indeed.

Open question worth testing next: does `auth.powerschool.com` mean **one account works for both
SchoolSpring and PeopleAdmin tenants**? If so, the UNE wall and the BPS wall are the same wall and
one signup clears two applications. Cheap to check, and it changes the account strategy for every
education employer.

## Build state (all pushed to `main`)

Landed this session: `submission_verifier` + evidence-gated `submitted`; the ATS database
(`ats_instances` / `ats_characteristics` / `ats_flows`) with `ats_tenancy` per-vendor tenant
extraction, backfilled from 356 transitions **and** 1,093 observer traces → 39 instances;
`ats_brief` as the pre-flight, wired into the session view; `apply_requirements` (the what-it-asks-
for axis); the cover-letter base + generator; the cockpit's **Stop this application**, the
tab-existence guard on **Step back in**, and **No more pages · finish this search**; `DISLIKED` as
a third verdict.

**Known-open, in rough priority order**

1. **The dislike has an endpoint and no button.** `POST /api/job-decisions/dislike` works; the
   picker needs a per-row control or it collects nothing.
2. **`controlplane-ui` has no test runner at all.** Every UI change this session is verified by
   driving `deriveCockpit` under node and by the live cockpit — not by a committed test.
3. **`AtsFlow.job_key` is NULL on all 63 backfilled rows** — the transition corpus records states
   without job identity. New flows written live carry it; the old ones cannot be recovered.
4. **Screenshots (458 MB) are addressable only by filename** — "show me every confirmation screen"
   is still unaskable. Deferred on purpose: it pays off at training time, not on a drive.
5. `queue.page` vs `progress.page` is now handled in the UI, but the queue still carries the page
   its picks were made on rather than being cleared — the backend half is untouched.

## The habit that keeps paying, and the one that keeps costing

**Paying:** press the site's own *Next* and read the validation. Our census claimed 35 required
fields on Paylocity; the site named ~6 and included one (a cover letter) we did not have. The
validator is the authority.

**Costing:** four times this session the system already knew and nothing asked — the registry note
that predicted the Paylocity résumé modal, the URL buried at `.acquisition.page_identity.url`,
`tab_open` on parked steps, `has_next` on the results page. When something looks missing, **check
whether it is already recorded and merely unread** before building it.
