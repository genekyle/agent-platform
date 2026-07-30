// The domain catalog — the single source of truth for which workspaces exist and how each
// one is framed. Every domain answers the same questions (What is it responsible for? Is it
// healthy? What's queued? What needs me? What happened?), so they all share one workspace
// shell; only the `tabs` (the domain-specific data views) differ.
//
// `kind` drives the shared behaviour:
//   selling      — has a persistent channel browser (connect + supervised login)
//   jobs         — a career-search AGGREGATOR (Indeed, LinkedIn): search + triage + hand off to an ATS
//   errands      — exists to be CALLED by other domains (Gmail): a bounded favour that RETURNS,
//                  measured by what it served and what it had to escalate, not by work of its own
//   coming_soon  — scaffolded in the UI, not wired to a backend yet (honest placeholder)
//
// Two more fields matter for the `jobs` kind:
//   accounts: "domain" | "ats" — WHICH accounts panel the Accounts tab shows. An aggregator has one
//     login of its OWN ("domain"); an ATS sub-domain has a login PER EMPLOYER ("ats").
//   sweep — does this engine have a driven multi-page sweep? Both aggregators do: the CADENCE is
//     shared (floor the radius, one page at a time, click into what you shortlist, human pauses)
//     and only the readers differ, which the capture server picks off the live tab's host.

export const DOMAIN_CATALOG = [
  {
    // Career Search — the PARENT domain grouping the job engines (Indeed, LinkedIn, …) and the ATS
    // sub-domains (Workday, iCIMS, …) they hand off to. Its workspace lists the sub-domains and holds
    // the shared Accounts (application logins, company→ATS). `kind: "group"` = a container, not a
    // driven surface. Members declare `parent: "career_search"` so they nest here, not at top level.
    id: "career_search",
    label: "Career Search",
    short: "Career Search",
    kind: "group",
    children: ["indeed_jobs", "linkedin_jobs", "workday", "greenhouse", "icims"],
    responsibility: "Search + apply across job engines (Indeed, LinkedIn, ZipRecruiter) and their ATS (Workday, iCIMS, Taleo, …); the shared application accounts live here.",
    blurb: "Job engines + ATS — search, apply, accounts.",
    tabs: [
      { id: "overview", label: "Sub-domains" },
      // The canonical job database lives on the PARENT, not on an engine: it is one table across
      // every board, and hanging it off Indeed would imply Indeed owns it.
      { id: "database", label: "Job Database" },
      { id: "accounts", label: "Accounts" },
      { id: "activity", label: "Activity" },
    ],
  },
  {
    id: "facebook_marketplace",
    label: "Facebook Marketplace",
    short: "Marketplace",
    kind: "selling",
    channel: "facebook_marketplace",
    host: "facebook",
    tabUrl: "facebook.com",
    responsibility: "Post your inventory to Facebook Marketplace, monitor listings, and reply to buyers.",
    blurb: "Sell inventory on Facebook Marketplace.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "inventory", label: "Inventory" },
      { id: "queue", label: "Queue" },
      { id: "listings", label: "Listings" },
      { id: "messages", label: "Messages" },
      { id: "accounts", label: "Accounts" },
      { id: "training", label: "Training" },
    ],
  },
  {
    id: "indeed_jobs",
    label: "Indeed",
    short: "Indeed",
    kind: "jobs",
    parent: "career_search",
    channel: null,
    host: "indeed",
    tabUrl: "indeed.com",
    accounts: "domain",
    sweep: true,
    responsibility: "Search Indeed, shortlist matching roles, apply to approved jobs, and track application status.",
    blurb: "Find and apply to jobs on Indeed.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "control", label: "Session control" },
      { id: "live", label: "Live drive" },
      { id: "terminal", label: "Activity" },
      { id: "jobs", label: "Jobs" },
      { id: "database", label: "Job Database" },
      { id: "profile", label: "Application Profile" },
      { id: "apply-state", label: "Apply State" },
      { id: "accounts", label: "Accounts" },
      { id: "training", label: "Training" },
    ],
  },
  {
    id: "workday",
    label: "Workday",
    short: "Workday",
    kind: "coming_soon",
    parent: "career_search",
    host: "workday",
    accounts: "ats",
    responsibility: "Complete Workday applications (the ATS most Indeed applies route to).",
    blurb: "Cross-site apply target — coming soon.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "accounts", label: "Accounts" },
    ],
  },
  {
    // Greenhouse — the SECOND ATS we drive, and the first with NO account wall. Its own sub-domain
    // (not a KKR-specific thing) so every capture labels as greenhouse_* and generalizes across every
    // employer on Greenhouse. Reached both directly (job-boards.greenhouse.io) and via a branded
    // wrapper on the employer's own domain (KKR: kkr.com/careers?gh_jid=… embedding the form in an
    // iframe) — ats_registry.classify_ats sees through the wrapper via the gh_jid tell.
    id: "greenhouse",
    label: "Greenhouse",
    short: "Greenhouse",
    kind: "coming_soon",
    parent: "career_search",
    host: "greenhouse",
    tabUrl: "greenhouse.io",
    accounts: "ats",
    responsibility: "Complete Greenhouse applications — embedded job_app forms, no account required.",
    blurb: "Cross-site apply target — no account wall.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "accounts", label: "Accounts" },
    ],
  },
  {
    // iCIMS — met live 2026-07-26 on the first Joslin Diabetes Center application, reached from
    // Indeed's "Apply on company site". Its shape is the reason `apply_landing` exists: iCIMS
    // serves the employer's OWN branded page (jobs-<tenant>.icims.com) whose top document is that
    // employer's site chrome — nav, footer, copyright — and puts the job, the description and the
    // whole apply flow inside `#icims_content_iframe`. Any probe reading the top document sees a
    // corporate homepage and nothing else, which is exactly what stopped the first landing.
    id: "icims",
    label: "iCIMS",
    short: "iCIMS",
    kind: "coming_soon",
    parent: "career_search",
    host: "icims",
    tabUrl: "icims.com",
    accounts: "ats",
    responsibility: "Complete iCIMS applications — content lives in an embedded frame on the employer's branded page; account-gated at submit.",
    blurb: "Cross-site apply target — framed content, account wall.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "accounts", label: "Accounts" },
    ],
  },
  {
    // LinkedIn — the second career-search AGGREGATOR, a SIBLING of Indeed (both are `kind: "jobs"`
    // under the Career Search group), not an ATS. It gets Indeed's whole operating pattern because
    // the questions are identical: what did we search, what did we find, what did we apply to.
    // The one declared difference: `accounts: "domain"` — LinkedIn has ONE login of its own (the
    // `linkedin_default` account), typed in the Accounts tab and encrypted into the vault.
    // Everything else it shares with Indeed, including the sweep and the Session-control ladder:
    // the cadence is about how we behave, not whose markup we read, so it is the SAME code with
    // per-engine readers (capture server, chosen by the live tab's host) and a per-engine row in
    // session_control.ENGINES (front door, results-URL shape, query param, page size).
    id: "linkedin_jobs",
    label: "LinkedIn",
    short: "LinkedIn",
    kind: "jobs",
    parent: "career_search",
    channel: null,
    host: "linkedin",
    tabUrl: "linkedin.com",
    accounts: "domain",
    sweep: true,
    responsibility: "Search LinkedIn, shortlist matching roles, and apply (Easy Apply on-engine, or hand off to the employer's ATS).",
    blurb: "Find and apply to jobs on LinkedIn.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "control", label: "Session control" },
      { id: "live", label: "Live drive" },
      { id: "terminal", label: "Activity" },
      { id: "jobs", label: "Jobs" },
      { id: "database", label: "Job Database" },
      { id: "profile", label: "Application Profile" },
      { id: "apply-state", label: "Apply State" },
      { id: "accounts", label: "Accounts" },
      { id: "training", label: "Training" },
    ],
  },
  {
    // Gmail — the first member of the `google` PROVIDER group, and the first domain here whose
    // point is to be CALLED rather than driven for its own sake. Other domains detour into it for
    // a one-time login code and return; `kind: "errands"` is what gives it an Errands tab instead
    // of the jobs/selling data views, and what keeps the Command Center from reporting it as a
    // jobs domain (that tile branch was a binary, so a domain with no case of its own still got
    // an answer — Indeed's).
    //
    // Its registry entry also owns the shared `google_signin_*` page states on behalf of the WHOLE
    // provider: they are the one sign-in Docs, Sheets and Drive will reuse, homed here because
    // Gmail is the surface that triggers login today. Those members are deliberately NOT scaffolded
    // as tiles — they are declared in providers.py and nothing more, and a disabled card beside
    // live work is scaffolding pretending to be a product.
    id: "gmail",
    label: "Gmail",
    short: "Gmail",
    kind: "errands",
    provider: "google",
    host: "gmail",
    // The PROVIDER's shared profile, not a per-domain one: one supervised sign-in here
    // authenticates every Google surface, which is what makes an errand a tab hop and not a login.
    profile: "google",
    accounts: "domain",
    responsibility: "Serve cross-domain errands — read a one-time login code out of the inbox for another domain's 'sign in with a code' flow — and home the shared Google single-sign-on training data.",
    blurb: "Email errands for other domains + the shared Google sign-in.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "errands", label: "Errands" },
      { id: "accounts", label: "Accounts" },
      { id: "training", label: "Training" },
    ],
  },
  {
    id: "shopify",
    label: "Shopify / Storefront",
    short: "Shopify",
    kind: "coming_soon",
    host: "shopify",
    responsibility: "Publish the same inventory to a Shopify-style storefront channel.",
    blurb: "Storefront channel — coming soon.",
    tabs: [{ id: "overview", label: "Overview" }],
  },
];

export const DOMAINS_BY_ID = Object.fromEntries(DOMAIN_CATALOG.map((d) => [d.id, d]));

// Provider groups — the bucket ABOVE domains. A provider is one company whose surfaces we drive as
// separate domains but which share ONE identity/login (Google ▸ Gmail, Calendar, Docs, Sheets). The
// backend `GET /api/providers` is authoritative; this static mirror lets the hub render the bucket
// without an extra fetch (same pattern as DOMAIN_CATALOG). `planned` are declared-not-yet domains.
export const PROVIDER_GROUPS = [
  {
    id: "google",
    label: "Google",
    blurb: "One Google sign-in authenticates every Google surface; errands hand off to Gmail.",
    planned: ["Calendar", "Docs", "Sheets"],
  },
];

// Domain id -> provider id, derived from the catalog (a domain declares its `provider`).
export const PROVIDER_OF_DOMAIN = Object.fromEntries(
  DOMAIN_CATALOG.filter((d) => d.provider).map((d) => [d.id, d.provider]),
);

// Human copy for each automation mode — shown under the mode toggle so the operator knows
// exactly what they're switching on.
export const MODE_COPY = {
  manual: {
    label: "Manual",
    hint: "Nothing runs unless you click Run. Every action is a deliberate step.",
  },
  supervised: {
    label: "Supervised",
    hint: "Safe, read-only tasks run on their own. Publishing, applying, and messaging still ask for approval first.",
  },
  autopilot: {
    label: "Autopilot",
    hint: "Approved recipes run unattended; you're pinged only for blocks or unknown states. Scheduling isn't wired up yet — this records the posture.",
  },
};
