"""ATS registry — the organized structure for third-party application portals.

Applying to a job is CROSS-SITE: you find the posting on a *career-search engine* (Indeed,
LinkedIn, ZipRecruiter, …) and then, for most postings, you leave it and land in the employer's
own *Applicant Tracking System* (ATS) — Workday, iCIMS, Oracle Taleo, Greenhouse, Lever, … This
module names that structure so the apply task and its training data are ORGANIZED instead of a
per-company pile.

The two groupings (defined live with the operator 2026-07-12):

  * CAREER_SEARCH — the domain CATEGORY for job engines. Its members (indeed_jobs, linkedin_jobs,
    ziprecruiter, …) are where we SEARCH + triage. "Indeed" isn't really the domain; the domain is
    "career-search engine", and Indeed/LinkedIn/ZipRecruiter are members of it.
  * ATS_PLATFORMS — the third-party apply portals you hand off TO. Each ATS is treated like its
    OWN domain: it gets its own recipe (apply_recipe.WORKDAY_APPLY_RECIPE, …) and its own
    training-data bucket (captures tagged domain_id=<ats_id>, so TrainingCapture rollups accrue
    per-ATS, not per-company).

Why per-ATS and not per-company: an ATS renders the SAME component library across every tenant
(Workday's data-automation-ids are identical for State Street, Takeda, Point32Health). So training
GENERALIZES across companies that share an ATS. `company_ats` records which ATS each employer uses;
the first time we drive Company X's Workday we can already reuse everything learned on every other
Workday. That is the whole point of organizing by ATS.

Shape mirrors providers.py: a backend-owned CONSTANT (config, not training data) plus a small
JSON-persisted learning store for the company→ATS mapping (the one part that grows at runtime),
persisted next to the other operator-owned state (domain_settings.json, job_search_targets.json).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import re
from urllib.parse import parse_qs, urlparse

from settings import settings

_lock = threading.Lock()


# --- CAREER_SEARCH: the domain category for job engines ------------------------------------------
# member_domains are domain_ids in command_center.DOMAINS (live) or planned (not seeded yet). The
# ATS platforms below are the downstream apply targets these engines hand off to.
CAREER_SEARCH: dict[str, Any] = {
    "group_id": "career_search",
    "display_name": "Career Search",
    "icon": "🧭",
    "what": "Job engines / career-search engines — where we search + triage postings.",
    "member_domains": ["indeed_jobs", "linkedin_jobs", "ziprecruiter", "glassdoor", "dice", "monster"],
    "apply_targets": "ats",  # postings hand off to the ATS group for the actual application
}


# --- ATS_PLATFORMS: each is domain-like (own recipe + own training bucket) -----------------------
# hosts: substrings matched against the apply-destination URL host (classify_ats).
# recipe: pointer to the driving recipe in apply_recipe.py, or "seed"/"none" when not built yet.
# auth: "account" = needs a per-employer candidate account (the wall — a persistent pre-authed
#       profile OR the operator; NEVER auto-create); "none" = no sign-in to apply.
# seed_companies: employers we already know use this ATS (grows via record_company_ats()).
ATS_PLATFORMS: list[dict[str, Any]] = [
    {"ats_id": "workday", "display_name": "Workday", "icon": "🟠",
     "hosts": ["myworkdayjobs.com", "myworkday.com", "wd1.", "wd3.", "wd5."],
     "recipe": "apply_recipe.WORKDAY_APPLY_RECIPE", "auth": "account",
     "notes": "data-automation-id selectors are stable across tenants (see WORKDAY_LESSONS). "
              "'Use My Last Application' prefills from the candidate account.",
     "seed_companies": ["State Street", "Takeda", "Point32Health"]},
    {"ats_id": "icims", "display_name": "iCIMS", "icon": "🔵",
     "hosts": ["icims.com"], "recipe": "seed", "auth": "account",
     "notes": "careers-<tenant>.icims.com; account-gated.", "seed_companies": []},
    {"ats_id": "taleo", "display_name": "Oracle Taleo", "icon": "🔴",
     "hosts": ["taleo.net"], "recipe": "seed", "auth": "account",
     "notes": "legacy Oracle ATS; heavy multi-step, account-gated.", "seed_companies": []},
    {"ats_id": "greenhouse", "display_name": "Greenhouse", "icon": "🌱",
     "hosts": ["greenhouse.io", "boards.greenhouse.io"], "recipe": "seed", "auth": "none",
     "notes": "boards.greenhouse.io/<tenant>; usually no account to apply (resume + fields).",
     "seed_companies": []},
    {"ats_id": "lever", "display_name": "Lever", "icon": "🎚️",
     "hosts": ["lever.co", "jobs.lever.co"], "recipe": "seed", "auth": "none",
     "notes": "jobs.lever.co/<tenant>; single-page apply, usually no account.", "seed_companies": []},
    {"ats_id": "successfactors", "display_name": "SAP SuccessFactors", "icon": "🟦",
     # `rmkcdn`/`successfactors.eu` are SAP's own hosts, but MOST tenants serve the career site
     # from their OWN domain (jobs.teradyne.com) with no SAP string anywhere in the url — which is
     # why Teradyne classified as `company_site` on the first encounter (2026-07-27). The path
     # shape and the page's own furniture are the tells; see `_SUCCESSFACTORS_PATH_TELLS`.
     "hosts": ["successfactors.com", "successfactors.eu", "rmkcdn.successfactors.com"],
     "recipe": "apply_recipe.SUCCESSFACTORS_APPLY_RECIPE", "auth": "account",
     "notes": "SAP ATS, usually on the employer's OWN domain. RAISES A BLOCKING alert() on the job "
              "page ('Join our talent community…') that freezes the tab's renderer and cannot be "
              "dismissed once open — start /dialog_guard BEFORE driving. Apply is a STAGED MENU "
              "('Apply Now' vs 'Start applying with LinkedIn'), not a direct link.",
     "seed_companies": ["Teradyne"]},
    {"ats_id": "smartrecruiters", "display_name": "SmartRecruiters", "icon": "🟢",
     "hosts": ["smartrecruiters.com"], "recipe": "seed", "auth": "none", "notes": "",
     "seed_companies": []},
    {"ats_id": "ashby", "display_name": "Ashby", "icon": "⬛",
     "hosts": ["ashbyhq.com", "jobs.ashbyhq.com"], "recipe": "seed", "auth": "none", "notes": "",
     "seed_companies": []},
    {"ats_id": "workable", "display_name": "Workable", "icon": "🟣",
     "hosts": ["workable.com"], "recipe": "seed", "auth": "none", "notes": "", "seed_companies": []},
    {"ats_id": "brassring", "display_name": "IBM Kenexa BrassRing", "icon": "🟫",
     "hosts": ["brassring.com", "kenexa.com"], "recipe": "seed", "auth": "account",
     "notes": "legacy Kenexa; account-gated.", "seed_companies": []},
    {"ats_id": "jobvite", "display_name": "Jobvite", "icon": "🟨",
     "hosts": ["jobvite.com"], "recipe": "seed", "auth": "none", "notes": "", "seed_companies": []},
    {"ats_id": "applicantmanager", "display_name": "The Applicant Manager (TAM)", "icon": "🟩",
     "hosts": ["theapplicantmanager.com"], "recipe": "seed", "auth": "none",
     "notes": "DRIVEN END TO END 2026-08-24 (CEDENT, Tableau Dashboard Developer) — no account "
              "required, which is why it finished in one pass. The posting and the application "
              "are the SAME page: /jobs?pos=<id>&src=Indeed renders the JD with the form inline "
              "below it, so there is no separate apply control to hunt — the fields are already "
              "on screen and carry proper accessible names (a relief on a night of empty ones). "
              "Hidden file inputs behind Attach/Dropbox/Paste links (app_pers_post[14] = resume, "
              "[16] = cover letter) stage cleanly when addressed by exact name. State is a select "
              "whose OPTION TEXT is the full name ('New Hampshire') while its value is the "
              "abbreviation. Confirms in two places: the confirmation body says 'You applied with "
              "this email: <address>', then the tab settles at /applied?co=..&app=<id> — both are "
              "measured hints in submission_verifier.",
     "seed_companies": ["CEDENT"]},
    {"ats_id": "cornerstone", "display_name": "Cornerstone OnDemand", "icon": "🟤",
     "hosts": ["csod.com"], "recipe": "seed", "auth": "unknown",
     "notes": "<tenant>.csod.com, reached straight from an Indeed 'Apply on company site' "
              "(source[0]=IndeedATSSync in the hand-off URL). MEASURED live 2026-08-11 on Boston "
              "College (bc.csod.com): the requisition page is "
              "/ux/ats/careersite/<n>/home/requisition/<req_id>, its apply control is a plain "
              "button named 'Apply Now' (rendered twice — masthead and footer — so drive the "
              "VISIBLE one), and the masthead carries both 'Sign In' and 'Create Profile'. Those "
              "links prove an account SYSTEM exists; whether Apply Now demands it before the form "
              "is UNMEASURED — auth stays 'unknown' until a drive meets the wall, and the generic "
              "cadence engages the account rung the moment an account_gate screen is SEEN. "
              "auth='account' here is a promise the ladder acts on; record it from a measurement, "
              "never an inference.",
     "seed_companies": ["Boston College"]},
    {"ats_id": "adp", "display_name": "ADP Workforce Now", "icon": "🔶",
     "hosts": ["workforcenow.adp.com", "myjobs.adp.com"], "recipe": "seed", "auth": "account",
     "notes": "", "seed_companies": []},
    {"ats_id": "paylocity", "display_name": "Paylocity Recruiting", "icon": "🟧",
     "hosts": ["recruiting.paylocity.com", "paylocity.com"], "recipe": "seed", "auth": "none",
     "notes": "MEASURED live 2026-08-14 (session #29) — the FIRST ATS met through LinkedIn rather "
              "than Indeed, which is the whole point of the registry being engine-agnostic: it "
              "arrives already organised, and the next Paylocity employer reuses everything learned "
              "here. Reached from a LinkedIn posting's Apply, which opens a NEW TAB at "
              "recruiting.paylocity.com/Recruiting/Jobs/Apply/<req_id>/<Employer-Slug>. "
              "The apply form is ONE long page (Referred by, 'Have you worked with us before?' "
              "[required select], Upload Cover Letter, Upload Additional Files, Work History), and "
              "it opens behind an 'Apply with resume' MODAL offering to autofill from an upload — "
              "so the modal is the first thing any drive meets, not the form. Its footer is "
              "'Powered by paylocity'. Required fields observed beyond the modal: Desired Salary "
              "Type, Salary Range, plus 'How did you hear about us'. "
              "auth stays 'unknown' until a drive MEETS a wall — the form renders without a "
              "sign-in, but whether Submit demands one is unmeasured, and auth is a promise the "
              "ladder acts on (same rule the Cornerstone entry states). "
              "MEASURED AGAIN 2026-08-19 (session #32, Isabella Stewart Gardner Museum, reached "
              "from INDEED this time — the note above predicted the resume modal and the drive "
              "walked straight past it into the fields underneath, so this entry needs to be READ "
              "at classify, not just written). Four things this ATS does that a generic cadence "
              "gets wrong: (1) it is a SIX-STEP WIZARD — the page says 'Step 1 of 6' — so the "
              "shared spine's 'at most 1 screen from Submit' is badly optimistic. (2) NO ACCOUNT "
              "GATE was seen between the posting and the form; Apply goes straight through. "
              "(3) Uploading the resume AUTO-GENERATES a work-history entry per employer, each "
              "with its OWN required address block — required-field count went 7 -> 35 on one "
              "upload (1 personal + 5 employer blocks, every one of them named Country / Address "
              "Line 1 / City / County / State / Zip), so name-addressing is hopeless on this page "
              "by construction, not by accident. (4) THE TEACH SURFACE CANNOT REACH THE IDENTITY "
              "FIELDS AT ALL: the census names them by DOM id (`infoEmail`) while the widget "
              "resolver reads the human label by proximity ('Email Address (required)'). Teaching "
              "the census name is refused with TARGET MISMATCH; teaching the label is refused with "
              "not_found. Each name is rejected in favour of the other and neither works — only "
              "the cockpit's own census ROW (which addresses by selector) lands. Also present: a "
              "cookie/privacy alertdialog, and identity fields the page marks '(required)' that "
              "the census files as voluntary. "
              "auth FLIPPED unknown -> none 2026-08-19 ON A MEASUREMENT, not an inference: the "
              "application was driven end to end and SUBMITTED (Jobs/Success/4382310, 'Your "
              "application has been received!') without a sign-in ever being asked for. That is "
              "the standard this entry and the Cornerstone one both demand - a wall MET, or not "
              "claimed. The six steps in order: 1 Information (identity, address, resume, cover "
              "letter, work + education history), 2 Additional Questions (one required free text), "
              "3 References (TWO required, each needing name + email + phone + Personal/"
              "Professional), 4 EEO, 5 Optional Identity Questions, 6 review + a required "
              "acknowledgement checkbox and a work-authorisation select. Employer posts can gate "
              "on the cover letter - this one read 'Only applications with a cover letter will be "
              "considered'. The references step is the one that stalls a drive: it needs THIRD "
              "PARTY emails and phones, which are not inferable and must come from the operator.",
     "seed_companies": ["Charles River Community Health", "Isabella Stewart Gardner Museum"]},
    {"ats_id": "schoolspring", "display_name": "SchoolSpring (PowerSchool)", "icon": "🟩",
     "hosts": ["schoolspring.com", "auth.powerschool.com"], "recipe": "seed", "auth": "account",
     "notes": "K-12's job board and ATS, MEASURED live 2026-08-20 (session #32, Boston Public "
              "Schools, Data Analyst for Strategic Initiatives). Reached from Indeed's 'Apply on "
              "company site', which lands on schoolspring.com/jobdetail?jobId=<id> — one shared "
              "host, so the tenant is the EMPLOYER named on the posting, not the URL. "
              "AND IT IS THE SAME VENDOR AS PEOPLEADMIN: 'Apply for this job!' redirects to "
              "auth.powerschool.com/u/login/identifier (email, then Continue; a separate Sign up). "
              "PowerSchool owns both, which is why `auth.powerschool.com` is listed here — the "
              "login host is shared across the family and classifying it as SchoolSpring is only "
              "right while SchoolSpring is the only PowerSchool product we drive. Split the host "
              "out the moment a second one appears. "
              "auth = ACCOUNT, the wall MET: no application form is reachable before the login. "
              "OBSERVATION NOTE: the posting opens behind a `dialog` — 'Welcome to the new "
              "SchoolSpring experience' with a Close button — which the form census cannot see and "
              "which sits over the apply control. Close it BEFORE pressing Apply. "
              "SECTOR = EDUCATION (K-12), and that is a real characteristic rather than trivia: "
              "PowerSchool owns SchoolSpring AND PeopleAdmin, so the two ATS we have met through "
              "schools share a vendor and a login stack. The operator's hypothesis that education "
              "might run on a common platform was closer than the first answer this registry gave "
              "it — see the `education` note on peopleadmin.",
     "seed_companies": ["Boston Public Schools"]},
    {"ats_id": "peopleadmin", "display_name": "PeopleAdmin", "icon": "🟦",
     "hosts": ["peopleadmin.com"], "recipe": "seed", "auth": "account",
     "notes": "MEASURED live 2026-08-19 (session #32, University of New England, "
              "Systems Programmer/Analyst). Reached from INDEED via an apptrkr.com redirect — "
              "`apptrkr.com/get_redirect.php?id=...` is a HigherEdJobs-style hop, so the landing "
              "host is not visible in the Indeed link and only the post-redirect URL classifies. "
              "Tenanted per institution: <tenant>.peopleadmin.com/postings/<posting_id>. Higher-ed "
              "is its home ground, so expect it wherever a university posts. "
              "The posting page states its own requirements before any form is opened, in a "
              "'Documents Needed to Apply' block — here Required: COVER LETTER + RESUME, Optional: "
              "names and contact information for three professional references. Reading that block "
              "at classify time is free and tells a drive whether it can finish at all. It also "
              "declares a 'Supplemental Questions' section with the usual asterisk convention. "
              "The apply control is a LINK ('Apply for this Job'), not a button. "
              "auth = ACCOUNT, and the wall was MET, not inferred: 'Apply for this Job' goes "
              "straight to <tenant>.peopleadmin.com/login — username + password, 'Create an "
              "Account', 'Log In with Chronicle Vitae' (a higher-ed SSO worth knowing about), and "
              "a separate current-employee route. No apply form is reachable before it. "
              "SECTOR = EDUCATION (higher-ed), and the same vendor as SchoolSpring: PowerSchool "
              "owns both, and SchoolSpring's apply redirects to auth.powerschool.com. So 'is there "
              "an education-specific ATS' has a better answer than the first one this registry "
              "gave: not one product, but ONE VENDOR FAMILY across K-12 and higher-ed, with a "
              "shared identity provider. Worth carrying as a prior when a school or university "
              "posting routes off Indeed. "
              "ADDRESS THE APPLY LINK EXACTLY: the posting renders 'Bookmark this Posting', "
              "'Print Preview' and 'Apply for this Job' inside ONE heading, so a name-based "
              "resolver matches the heading and lands on /print_preview instead. Drive "
              "role=link name='Apply for this Job'.",
     "seed_companies": ["University of New England"]},
    {"ats_id": "phenom", "display_name": "Phenom", "icon": "⚫",
     "hosts": ["phenompeople.com", "phenom.com"], "recipe": "seed", "auth": "account",
     "notes": "career-site CMS, not a uniform host: employers run it on their OWN careers subdomain "
              "(careers.<co>.com; the Indeed feed carries utm_medium=phenom-feeds). classify_ats "
              "can't catch it by host — record the company→phenom mapping from the applystart feed. "
              "'Apply now' is typically account-gated. Observed: careers.usbank.com (U.S. Bank).",
     "seed_companies": ["U.S. Bank"]},
    {"ats_id": "appvault", "display_name": "AppVault", "icon": "🟩",
     "hosts": ["appvault.com"], "recipe": "apply_recipe.APPVAULT_APPLY_RECIPE", "auth": "account",
     "notes": "Reached via a company careers FRONT (e.g. careerswithus.com → 'APPLY NOW' link) that "
              "hands off to <employer>apply.appvault.com. Account-gated: Material-UI LOGIN (Email + "
              "Password) with a 'Create an Account' form (Email, Password[8-18, upper+lower+non-alpha], "
              "confirm, First/Last Name, Country, Profile Visibility, accept-Terms link → Continue). "
              "MUI inputs carry NO stable name/id EXCEPT the password fields "
              "(#outlined-adornment-password / -re-password) — match the rest by floating-label text / "
              "DOM order. Observed: Ahold Delhaize USA (aholddelhaizeapply.appvault.com).",
     "seed_companies": ["Ahold Delhaize USA"]},
    # Catch-all so an employer's own careers portal (not yet mapped to a known ATS) is a recordable
    # platform. classify_ats returns 'company_site' for unknown external hosts; this makes it a
    # first-class registry entry (so record_company_ats accepts it) until the real backend is known.
    {"ats_id": "company_site", "display_name": "Company careers site (unmapped)", "icon": "⬜",
     "hosts": [], "recipe": "none", "auth": "unknown",
     "notes": "employer's own careers portal not yet mapped to a known ATS backend.",
     "seed_companies": []},
    # The Indeed-native quick-apply flow (smartapply) is NOT an external ATS but is where an apply
    # can also complete without leaving the engine — kept here so classify_ats can name it.
    {"ats_id": "indeed_quick_apply", "display_name": "Indeed Quick Apply (smartapply)", "icon": "🔷",
     "hosts": ["smartapply.indeed.com", "indeed.com"], "recipe": "apply_recipe.INDEED_APPLY_RECIPE",
     "auth": "none", "notes": "On-engine Indeed apply; no external ATS. Not account-gated.",
     "seed_companies": []},
    # LinkedIn's on-engine "Easy Apply" — the same shape as Indeed's smartapply: an apply that
    # completes WITHOUT leaving the engine, so it needs a name here even though it is not a
    # third-party ATS. Its auth is the ENGINE's login (you are already signed in to LinkedIn to
    # see the button at all), which is why auth="none" — there is no separate per-employer account.
    # A LinkedIn posting that is not Easy Apply hands off to a real ATS on that ATS's own host, so
    # the host-matching loop above catches it exactly as it does for an Indeed hand-off.
    {"ats_id": "linkedin_easy_apply", "display_name": "LinkedIn Easy Apply", "icon": "🔗",
     "hosts": ["linkedin.com"], "recipe": "seed", "auth": "none",
     "notes": "On-engine LinkedIn apply; no external ATS. Signed in as the LinkedIn account.",
     "seed_companies": []},
]

# ATS ids that are the ENGINE's own on-page apply rather than a third-party portal. classify_ats
# must not let their broad engine hosts (indeed.com, linkedin.com) shadow a real ATS, so they are
# skipped in the host loop and answered explicitly at the end.
_ON_ENGINE_APPLY = ("indeed_quick_apply", "linkedin_easy_apply")

_BY_ID = {a["ats_id"]: a for a in ATS_PLATFORMS}


def off_engine_apply_hosts() -> tuple[str, ...]:
    """Every host that means "an application lives here", for callers that need only the hosts.

    The window's tab classifier used to keep its own copy of this list and it drifted nine
    platforms behind (2026-08-11). A registry that names a platform should be the only place that
    knows where that platform lives, so anything asking "is this tab an application?" asks here.

    On-engine applies are excluded: their hosts are the ENGINE's own (indeed.com, linkedin.com),
    and an engine host names the SEARCH tab. Callers that need the on-engine apply surface match
    it explicitly (`smartapply.indeed.com`), exactly as classify_ats does.
    """
    hosts: list[str] = []
    for ats in ATS_PLATFORMS:
        if ats["ats_id"] in _ON_ENGINE_APPLY:
            continue
        hosts.extend(h.lower() for h in ats.get("hosts") or ())
    return tuple(dict.fromkeys(hosts))


# A BRANDED WRAPPER hosts the ATS on the employer's own domain, so the host alone lies: KKR serves a
# Greenhouse job at www.kkr.com/careers/...?gh_jid=<id> (the real form is an embedded
# job-boards.greenhouse.io iframe). Host-matching called that 'company_site' and we'd have grown a
# bespoke KKR path for what is just Greenhouse. These QUERY-PARAM tells are the ATS leaking its own
# identity through the wrapper — cheap, and they generalize across every employer on that ATS.
# (Same lesson as Workday's branded wrappers, which are caught by the APPLY-NOW href instead.)
#: SuccessFactors on a tenant's own domain leaves no host or query tell, so it is recognised by the
#: SHAPE OF ITS PATH: SAP renders `/<Tenant>/job/<Location>-<Title>-<id>/`. Checked only after the
#: host, query and embed passes fail, so a real ATS host always wins.
#:
#: NARROW ON PURPOSE, and narrower than the first attempt. `/<Tenant>/search/` looked like an
#: equally good tell until it claimed `linkedin.com/jobs/search` — a confident wrong answer for a
#: platform we DO know, which is the same trap facebook.com fell into in the facet vocabulary. The
#: job-page shape is the one worth having anyway: the apply flow needs the posting, not the search.
#: The REAL shape, taken from the live url rather than from memory: SAP appends a NUMERIC job id as
#: its own segment. The first version of this pattern required exactly `/<Tenant>/job/<slug>/` and
#: was "verified" against a url I typed myself — so it passed the test and missed the page that was
#: open at the time. The numeric id is also the more distinctive half, which is why the corrected
#: pattern is both righter and narrower.
_SUCCESSFACTORS_PATH_TELLS = (
    re.compile(r"^/[A-Za-z0-9_%-]+/job/[^/]+/\d+/?$"),   # /Teradyne/job/North-Reading-.../1385295400/
)

_QUERY_PARAM_TELLS = {
    "gh_jid": "greenhouse",       # Greenhouse embed / board id
    "gh_src": "greenhouse",
    "lever-origin": "lever",
    "jvi": "jobvite",
}


def classify_ats(url: str, page_hints: Optional[dict[str, Any]] = None) -> str:
    """Map an apply-destination URL to its ATS id. Unknown external host = 'company_site' (still
    handled — an employer's own careers page that isn't a recognized ATS yet). Empty = 'unknown'.
    This is the single source of truth; search_cadence.classify_apply_platform delegates here.

    Checks, in order: the HOST, then the ATS's own QUERY-PARAM tells (which see through a branded
    wrapper on the employer's domain), then optional page_hints — {"embed_hosts": [...]} from a live
    page (e.g. an embedded job-boards.greenhouse.io iframe), for wrappers that hide even the param.
    """
    host = (urlparse(url or "").hostname or "").lower()
    if not host:
        return "unknown"
    for ats in ATS_PLATFORMS:
        if ats["ats_id"] in _ON_ENGINE_APPLY:
            continue  # handled explicitly below so a broad engine host doesn't shadow real ATSs
        if any(needle in host for needle in ats["hosts"]):
            return ats["ats_id"]

    # Branded wrapper: the ATS betrays itself in the query string.
    params = parse_qs(urlparse(url or "").query)
    for param, ats_id in _QUERY_PARAM_TELLS.items():
        if param in params and ats_id in _BY_ID:
            return ats_id

    # Branded wrapper with no param tell — ask where the page POINTS rather than where it IS.
    # Two sources, same idea: an embedded ATS iframe (`embed_hosts`), and the destination of the
    # page's own apply control (`apply_hrefs`).
    #
    # THE APPLY HREF IS OFTEN THE ONLY TELL, and this one cost a halt to find. Ahold Delhaize's
    # careers front is `aholddelhaizeusa.careerswithus.com` — no known host, no param, no iframe —
    # and its "APPLY NOW" points at `aholddelhaizeapply.appvault.com` (measured live 2026-07-30, via
    # a LinkedIn apply). The registry already DESCRIBED that hop in AppVault's notes, in prose,
    # from an Indeed drive; prose does not classify. A careers front is a signpost, and reading the
    # signpost is the whole trick.
    hosts_to_try = list((page_hints or {}).get("embed_hosts") or [])
    hosts_to_try += list((page_hints or {}).get("apply_hrefs") or [])
    for candidate in hosts_to_try:
        cand_host = (urlparse(candidate).hostname or candidate or "").lower()
        for ats in ATS_PLATFORMS:
            if ats["ats_id"] in _ON_ENGINE_APPLY:
                continue
            if any(needle in cand_host for needle in ats["hosts"]):
                return ats["ats_id"]

    # Branded SuccessFactors: no host tell, no param tell — read the path shape.
    path = urlparse(url or "").path or ""
    if any(pat.match(path) for pat in _SUCCESSFACTORS_PATH_TELLS):
        return "successfactors"

    if "smartapply.indeed.com" in host or "indeed.com" in host:
        return "indeed_quick_apply"
    if "linkedin.com" in host:
        return "linkedin_easy_apply"
    return "company_site"


def get_ats(ats_id: str) -> Optional[dict[str, Any]]:
    return _BY_ID.get(ats_id)


# --- company → ATS learning store (the part that grows at runtime) -------------------------------
def _store_path() -> Path:
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "company_ats.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_company_ats() -> dict[str, Any]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_company_ats(doc: dict[str, Any]) -> None:
    _store_path().write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _norm_company(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def record_company_ats(company: str, ats_id: str, url: str = "") -> dict[str, Any]:
    """Learn that <company> applies through <ats_id> (observed live at <url>). This is the
    generalization hook: once recorded, ats_for_company() reuses the ATS recipe + all training
    accumulated on OTHER companies that share it. Idempotent; bumps last_seen_at."""
    key = _norm_company(company)
    if not key or ats_id not in _BY_ID:
        return {"ok": False, "detail": f"bad company/ats ({company!r}, {ats_id!r})"}
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        doc = _load_company_ats()
        rec = doc.get(key) or {"company": company.strip(), "ats_id": ats_id, "first_seen_at": now}
        rec.update({"company": company.strip(), "ats_id": ats_id, "last_seen_at": now})
        if url:
            rec["last_url"] = url[:200]
        doc[key] = rec
        _save_company_ats(doc)
    return {"ok": True, **rec}


def ats_for_company(company: str) -> Optional[str]:
    """The ATS a company is known to use — from the learned store first, then the seed lists. None
    if unknown (drive it fresh, then record_company_ats to generalize next time)."""
    key = _norm_company(company)
    if not key:
        return None
    learned = _load_company_ats().get(key)
    if learned:
        return learned.get("ats_id")
    for ats in ATS_PLATFORMS:
        if any(_norm_company(c) == key for c in ats.get("seed_companies", [])):
            return ats["ats_id"]
    return None


def companies_on_ats(ats_id: str) -> list[str]:
    """Every company known to use this ATS (seed + learned) — the set training generalizes over."""
    out = {c for c in (_BY_ID.get(ats_id, {}).get("seed_companies") or [])}
    for rec in _load_company_ats().values():
        if rec.get("ats_id") == ats_id and rec.get("company"):
            out.add(rec["company"])
    return sorted(out)


def ats_spec() -> dict[str, Any]:
    """The full registry — what GET /api/career_search/ats returns. Merges the constant with the
    learned company→ATS mapping so each ATS shows every company (seed + observed) that uses it."""
    platforms = []
    for a in ATS_PLATFORMS:
        platforms.append({**a, "companies": companies_on_ats(a["ats_id"])})
    return {
        "career_search": CAREER_SEARCH,
        "ats_platforms": platforms,
        "company_ats": _load_company_ats(),
        "note": "Each ATS is domain-like: own recipe + own training bucket (capture domain_id=ats_id). "
                "Training generalizes across companies sharing an ATS via the company→ATS map. "
                "NEVER auto-create an account on an auth='account' ATS — escalate to the operator.",
    }
