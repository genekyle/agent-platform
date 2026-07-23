// The domain catalog — the single source of truth for which workspaces exist and how each
// one is framed. Every domain answers the same questions (What is it responsible for? Is it
// healthy? What's queued? What needs me? What happened?), so they all share one workspace
// shell; only the `tabs` (the domain-specific data views) differ.
//
// `kind` drives the shared behaviour:
//   selling      — has a persistent channel browser (connect + supervised login)
//   jobs         — runs inside a training session (auth is session-scoped)
//   coming_soon  — scaffolded in the UI, not wired to a backend yet (honest placeholder)

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
    children: ["indeed_jobs", "linkedin", "workday", "greenhouse"],
    responsibility: "Search + apply across job engines (Indeed, LinkedIn, ZipRecruiter) and their ATS (Workday, iCIMS, Taleo, …); the shared application accounts live here.",
    blurb: "Job engines + ATS — search, apply, accounts.",
    tabs: [
      { id: "overview", label: "Sub-domains" },
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
    responsibility: "Search Indeed, shortlist matching roles, apply to approved jobs, and track application status.",
    blurb: "Find and apply to jobs on Indeed.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "control", label: "Session control" },
      { id: "live", label: "Live drive" },
      { id: "jobs", label: "Jobs" },
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
    responsibility: "Complete Greenhouse applications — embedded job_app forms, no account required.",
    blurb: "Cross-site apply target — no account wall.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "accounts", label: "Accounts" },
    ],
  },
  {
    id: "linkedin",
    label: "LinkedIn",
    short: "LinkedIn",
    kind: "coming_soon",
    parent: "career_search",
    host: "linkedin",
    tabUrl: "linkedin.com",
    responsibility: "Search LinkedIn and apply to roles (Easy Apply + off-site ATS hand-offs).",
    blurb: "Find and apply to jobs on LinkedIn — coming soon.",
    tabs: [{ id: "overview", label: "Overview" }],
  },
  {
    id: "gmail",
    label: "Gmail",
    short: "Gmail",
    // A real training domain (its registry entry owns the shared Google SSO page-states — the home
    // for single-sign-on training data — and the fetch_login_code errand goal). The operator
    // WORKSPACE isn't built yet, so the tile stays non-clickable; training capture is live.
    kind: "coming_soon",
    provider: "google",
    host: "gmail",
    responsibility: "Home for Google single-sign-on training data + the target of cross-domain 'fetch the login code' errands.",
    blurb: "Google login + email errands — training live, workspace soon.",
    tabs: [{ id: "overview", label: "Overview" }],
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
