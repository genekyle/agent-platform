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
    id: "facebook_marketplace",
    label: "Facebook Marketplace",
    short: "Marketplace",
    icon: "🛍️",
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
      { id: "training", label: "Training" },
    ],
  },
  {
    id: "indeed_jobs",
    label: "Indeed",
    short: "Indeed",
    icon: "💼",
    kind: "jobs",
    channel: null,
    host: "indeed",
    tabUrl: "indeed.com",
    responsibility: "Search Indeed, shortlist matching roles, apply to approved jobs, and track application status.",
    blurb: "Find and apply to jobs on Indeed.",
    tabs: [
      { id: "overview", label: "Overview" },
      { id: "jobs", label: "Jobs" },
      { id: "profile", label: "Application Profile" },
      { id: "apply-state", label: "Apply State" },
      { id: "training", label: "Training" },
    ],
  },
  {
    id: "workday",
    label: "Workday",
    short: "Workday",
    icon: "🗂️",
    kind: "coming_soon",
    host: "workday",
    responsibility: "Complete Workday applications (the ATS most Indeed applies route to).",
    blurb: "Cross-site apply target — coming soon.",
    tabs: [{ id: "overview", label: "Overview" }],
  },
  {
    id: "gmail",
    label: "Gmail",
    short: "Gmail",
    icon: "📧",
    kind: "coming_soon",
    host: "gmail",
    responsibility: "Fetch verification codes and run cross-domain email errands.",
    blurb: "Email errands — coming soon.",
    tabs: [{ id: "overview", label: "Overview" }],
  },
  {
    id: "shopify",
    label: "Shopify / Storefront",
    short: "Shopify",
    icon: "🏬",
    kind: "coming_soon",
    host: "shopify",
    responsibility: "Publish the same inventory to a Shopify-style storefront channel.",
    blurb: "Storefront channel — coming soon.",
    tabs: [{ id: "overview", label: "Overview" }],
  },
];

export const DOMAINS_BY_ID = Object.fromEntries(DOMAIN_CATALOG.map((d) => [d.id, d]));

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
