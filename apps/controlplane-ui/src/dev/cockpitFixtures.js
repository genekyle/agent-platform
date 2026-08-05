// Captured panel payloads for the session cockpit, so its states can be looked at without a live
// session, a browser, or a byte of bandwidth.
//
// `atStartLineWithApplication` is the state from the operator's 2026-08-05 screenshots, kept
// deliberately: it is the one the old panel rendered as an unsaved pick for one job, an in-flight
// application for a DIFFERENT job, and a rung claiming picks were made — three simultaneous answers
// to "what are we doing". Any redesign has to be checked against the state that broke the last one.
//
// These are FIXTURES, not mocks: every field is shaped the way `_view()` in
// `routers/session_control.py` actually emits it. A fixture that drifts from the read model tests
// the fixture.

const RESULTS = [
  ["j1", "Azure Data Engineer", "Stellar IT Solutions LLC", "Hybrid work in Wilmington, MA", "$43 - $48 an hour"],
  ["j2", "Senior Data Engineer", "MFS Investment Management", "Hybrid work in Boston, MA", "$100,505.66 - $130,598.22 a year"],
  ["j3", "Senior Data Engineer - Analytics", "RevSpring Inc", "Boston, MA", ""],
  ["j4", "Salesforce Development Engineer", "The CCS Companies", "Salem, NH 03079", "$120,000 - $130,000 a year"],
  ["j5", "Process Engineer - FACs/Artiva", "The CCS Companies", "Norwood, MA", "$50,000 - $70,000 a year"],
  ["j6", "Senior SAP Reporting Configuration Engineer", "Liberty Mutual", "Hybrid work in Portsmouth, NH", "$106,000 - $197,000 a year"],
  ["j7", "Senior SAP Reporting Configuration Engineer", "Liberty Mutual", "Hybrid work in Boston, MA", "$106,000 - $197,000 a year"],
  ["j8", "Principal Front-Office Engineer, Investments Strategy & Capital Management", "Liberty Mutual", "Hybrid work in Boston, MA", "$128,000 - $225,000 a year"],
  ["j9", "Senior Data Engineer, Investments Technology", "Liberty Mutual", "Boston, MA", "$114,000 - $197,000 a year"],
  ["j10", "Supervisor, IT Service Delivery & Customer Experience", "High Liner Foods", "Hybrid work in Portsmouth, NH", ""],
  ["j11", "Principal Analyst, CGT Agentic Solutions", "Vertex Pharmaceuticals", "Hybrid work in Boston, MA", "$142,800 - $214,200 a year"],
  ["j12", "Secure Data/Voice Solutions Architect", "General Dynamics Mission Systems", "Hybrid work in Dedham, MA", "$139,548 - $147,180 a year"],
  ["j13", "Data Platform Engineer", "Wayfair", "Boston, MA", "$120,000 - $160,000 a year"],
  ["j14", "Analytics Engineer", "Klaviyo", "Boston, MA", ""],
  ["j15", "Staff Data Engineer", "Toast", "Remote", "$150,000 - $190,000 a year"],
  ["j16", "ETL Developer", "Point32Health", "Canton, MA", "$95,000 - $125,000 a year"],
  ["j17", "Data Warehouse Engineer", "Bright Horizons", "Newton, MA", ""],
  ["j18", "Senior Analytics Engineer", "CarGurus", "Boston, MA", "$130,000 - $165,000 a year"],
  ["j19", "Data Engineer II", "Cabot Corporation", "Billerica, MA", "$95,000 - $120,000 a year"],
  ["j20", "Business Intelligence Engineer", "Beth Israel Lahey Health", "Charlestown, MA", ""],
  ["j21", "Reporting Analyst, Finance Systems", "Dell Technologies", "Hopkinton, MA", "$88,000 - $110,000 a year"],
].map(([job_id, title, company, location, salary]) => ({ job_id, title, company, location, salary }));

const TABS = [
  { tab_id: "t1", url: "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection",
    title: "Upload or create a resume for this application | Indeed",
    role: "apply", is_search: false, is_apply: true },
  { tab_id: "t2", url: "https://www.indeed.com/jobs?q=data+engineer&l=Nashua%2C+NH&radius=50",
    title: "data engineer jobs in Nashua, NH", role: "search", is_search: true, is_apply: false },
];

const STALENESS = {
  level: "red", verdict: "refresh", rules_version: "v0-provisional",
  why: "The last recorded action was over 14 hours ago, though the session still answers and "
    + "reports signed in.",
  signals: [
    { name: "last_action_s", value: 52380, level: "red" },
    { name: "logged_in", value: true, level: "fresh" },
    { name: "responsive", value: true, level: "fresh" },
  ],
  unmeasured: ["last_nav_s"],
};

const WITNESSES = [
  { source: "url_host", claim: "indeed_quick_apply", detail: "host smartapply.indeed.com is Indeed's apply surface", weight: 1 },
  { source: "ax_title", claim: null, detail: "no title pattern matched", weight: 1 },
  { source: "dom_similarity:v3", claim: null, detail: "nearest labelled page scores 0.31 — below the 0.55 floor", weight: 0.8 },
  { source: "vision:apple-v1", claim: "indeed_quick_apply", detail: "0.71 to indeed_quick_apply_resume", weight: 0.6 },
  { source: "recipe_position", claim: null, detail: "past the known prefix — no rung to assert", weight: 1 },
];

const LADDER = [
  { id: "provisioned", label: "Browser ready", kind: "standing", status: "held",
    why: "A session is one focused Chrome instance; without a reachable one there is nothing to drive.",
    reached: { at: "2026-08-04T14:20:11", initiator: "operator", evidence: "clean window, 1 tab(s)" } },
  { id: "authenticated", label: "Signed in to Indeed", kind: "standing", status: "held",
    why: "Logged-out data is provenance-invalid, so this has to hold CONTINUOUSLY, not once.",
    reached: { at: "2026-08-04T14:20:44", initiator: "operator", evidence: "/auth_state reported logged_in" } },
  { id: "query_entered", label: "Query run", kind: "consuming", status: "held",
    why: "Submitting the query hits Indeed's search backend. Repeat it and Indeed collapses results.",
    recovery: "Return to the results we already have — never re-submit the query.",
    reached: { at: "2026-08-04T14:21:02", initiator: "operator", evidence: "results URL carries q='data engineer'" } },
  { id: "radius_set", label: "Distance filter applied", kind: "consuming", status: "held",
    why: "Clicking the distance pill re-queries the backend, same cost as the search itself.",
    recovery: "Trust the radius already applied to this result set.",
    reached: { at: "2026-08-04T14:21:30", initiator: "operator", evidence: "distance pill set to 50mi" } },
  { id: "page:1", label: "Page 1 reviewed", kind: "consuming", status: "next",
    why: "The page's cards are recorded and the operator has made their picks.",
    recovery: "Read the recorded results for this page instead of navigating back to it." },
  { id: "select:1", label: "Page 1 picks made", kind: "standing", status: "held",
    why: "Choosing what to apply to is a decision with a decider, and both belong on the record.",
    reached: { at: "2026-08-04T16:14:09", initiator: "operator",
      evidence: "1 of 21 picked by operator — one prospect to exercise the apply path with recording" } },
];

/** The 2026-08-05 screenshot: at the start line, one application in flight, an unrecognised page. */
export const atStartLineWithApplication = {
  session_id: 21,
  goal: "apply_and_search",
  query: "data engineer",
  location: "Nashua, NH",
  radius_miles: 50,
  engine: "Indeed",
  page: 1,
  ladder: LADDER,
  next: { id: "page:1", label: "Read page 1", kind: "standing",
    reason: "The page's cards are recorded once it is read." },
  progress: { preamble_held: 4, preamble_total: 4, at_start_line: true, pages_reviewed: 0,
    page: 1, phase: "start_line" },
  observed: { authenticated: true },
  observer: {
    state: "indeed_quick_apply_unknown",
    headline: "Unrecognised page",
    confidence: "medium",
    kind: "unknown",
    url: "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection",
    mismatch: null,
    plan: [{ id: "screenshot", label: "Screenshot and hand to me", driveable: false,
      why: "nothing recognises this page, though the host reads as indeed_quick_apply — a human "
        + "look beats a guess, and that much is worth saying out loud" }],
    witnesses: WITNESSES,
  },
  next_action: {
    source: "rung", id: "", label: "Work this step", endpoint: "/apply_step", body: {},
    driveable: true,
    why: "Past the known prefix — the rungs from here depend on the platform (indeed), and those "
      + "are not built yet.",
    reason: "Unrecognised page — we read it and recognised nothing in it, so there is nothing here "
      + "that confirms the ladder or contradicts it. The rung stands because it is all there is, "
      + "not because the page agreed with it — read the page before working it.",
    observer_abstained: false,
    secondary: { source: "observer", id: "screenshot", label: "Read the page again",
      driveable: true, endpoint: "/orient_action", body: { action_id: "screenshot" },
      why: "a human look beats a guess",
      demoted_because: "the observer's way out, offered with no disagreement to resolve." },
  },
  block: null,
  tab_count: 2,
  tabs: TABS,
  results: RESULTS,
  picks: ["j2"],
  queue: {
    steps: [{
      job_id: "j2", title: "Senior Data Engineer", company: "MFS Investment Management",
      platform: "indeed", landing_state: "indeed_unknown", done: false, terminal: null,
      terminal_detail: "", next_rung: null, needs_operator: false,
      minis: [
        { rung: "reopened", outcome: "ok", detail: "search tab refocused" },
        { rung: "open_pane", outcome: "ok", detail: "card clicked, detail pane loaded" },
        { rung: "verify_identity", outcome: "ok", detail: "pane title matches the card clicked" },
        { rung: "enter_apply", outcome: "ok", detail: "Apply clicked; a new tab opened" },
        { rung: "classify", outcome: "ok", detail: "landed on smartapply.indeed.com" },
        { rung: "account", outcome: "skipped", detail: "indeed takes an application without an account of its own" },
      ],
    }],
  },
  queue_summary: { total: 1, done: 0, submitted: 0, blocks_page: true, remaining: 1 },
  proposal: null,
  account_handoff: null,
  account_state: null,
  open_pane: { apply_type: "indeed_apply" },
  tab_drift: { opened: ["https://smartapply.indeed.com/beta/indeedapply/form/resume-selection-module/resume-selection"] },
  applied_check: { found: false },
  awaiting: null,
  accordion_ats: ["successfactors"],
  last_step: { action: "select_page", ok: true, detail: "1 pick queued", pace: { style: "unhurried", why: "bot-safety cadence for a first apply on this platform" } },
  events: [],
  staleness: STALENESS,
};

/** The same page, before anything was picked — the Decide moment. */
export const decidingPage = {
  ...atStartLineWithApplication,
  ladder: LADDER.map((r) => (r.id === "select:1" ? { ...r, status: "pending", reached: null } : r)),
  observer: null,
  next_action: null,
  queue: { steps: [] },
  queue_summary: { total: 0, done: 0, submitted: 0, blocks_page: false, remaining: 0 },
  picks: [],
  awaiting: "choose",
  tabs: [TABS[1]],
  tab_drift: null,
  open_pane: null,
  last_step: { action: "review_page", ok: true, detail: "21 results read from page 1" },
};

/** A session that has not been told what it is for. */
export const freshSession = {
  session_id: 22, query: null, location: null, engine: "Indeed", page: 1,
  ladder: LADDER.slice(0, 4).map((r) => ({ ...r, status: "pending", reached: null })),
  next: { id: "provisioned", label: "Check the browser", reason: "Nothing has been declared yet." },
  progress: { preamble_held: 0, preamble_total: 4, at_start_line: false, pages_reviewed: 0, page: 1, phase: "climbing" },
  observed: {}, observer: null, next_action: null, tabs: [], tab_count: 0,
  results: [], picks: [], queue: { steps: [] },
  queue_summary: { total: 0, done: 0, submitted: 0, blocks_page: false, remaining: 0 },
  awaiting: null, accordion_ats: [], last_step: null, events: [], staleness: null,
};

/** Stopped at a sign-in wall — the blocker case, where the rail must not look calm. */
export const blockedOnLogin = {
  ...freshSession,
  session_id: 23, query: "data engineer", location: "Nashua, NH",
  ladder: LADDER.slice(0, 4).map((r, i) => (i === 0
    ? { ...r, status: "held", reached: { at: "2026-08-05T09:02:00", initiator: "operator", evidence: "clean window" } }
    : { ...r, status: i === 1 ? "next" : "pending", reached: null })),
  awaiting: "operator_login",
  observed: { authenticated: false },
  tabs: [{ tab_id: "t9", url: "https://secure.indeed.com/auth", title: "Sign in | Indeed",
    role: "unknown", is_search: false, is_apply: false }],
  tab_count: 1,
  last_step: {
    action: "auth_probe", ok: false, detail: "no stored session cookie was accepted",
    login: {
      state: "signed_out", seen: 14, detail: "",
      options: [
        { name: "Continue with Google", role: "button", why: "SSO — opens a Google popup you complete" },
        { name: "Sign in with a code", role: "link", why: "emails a one-time code to the account address" },
      ],
    },
  },
  staleness: { level: "fresh", verdict: "continue", rules_version: "v0-provisional",
    why: "acted within the minute", signals: [{ name: "last_action_s", value: 12, level: "fresh" }],
    unmeasured: [] },
};

/** Page 2 in progress, page 1 fully walked — the grouping case: a past page must collapse to its
 *  RECORD ("1 of 21 picked by operator"), never to a bare "done", and page 2's decision must not
 *  overwrite it. */
export const secondPage = {
  ...atStartLineWithApplication,
  session_id: 24,
  page: 2,
  ladder: [
    ...LADDER.map((r) => (r.id === "page:1" ? { ...r, status: "held",
      reached: { at: "2026-08-04T16:13:40", initiator: "operator", evidence: "21 results recorded" } } : r)),
    { id: "page:2", label: "Page 2 reviewed", kind: "consuming", status: "next",
      why: "The page's cards are recorded and the operator has made their picks.",
      recovery: "Read the recorded results for this page instead of navigating back to it." },
  ],
  observer: null,
  next_action: null,
  queue: { steps: [] },
  queue_summary: { total: 0, done: 0, submitted: 0, blocks_page: false, remaining: 0 },
  picks: ["j2"],
  results: RESULTS.slice(10, 16).map((r) => ({ ...r, job_id: `p2-${r.job_id}` })),
  awaiting: "choose",
  tabs: [TABS[1]],
  tab_drift: null,
  open_pane: null,
  progress: { preamble_held: 4, preamble_total: 4, at_start_line: true, pages_reviewed: 1,
    page: 2, phase: "start_line" },
  last_step: { action: "review_page", ok: true, detail: "6 results read from page 2" },
  staleness: null,
};

export const FIXTURES = [
  { id: "screenshot", label: "The 2026-08-05 screenshot", panel: atStartLineWithApplication,
    note: "One application in flight on an unrecognised page, while a 21-row results table and a "
      + "held picks rung both claim the screen. The state the old panel could not narrate." },
  { id: "decide", label: "Deciding page 1", panel: decidingPage,
    note: "21 results, nothing picked. The table is the object of the decision, not a permanent fixture." },
  { id: "login", label: "Blocked on sign-in", panel: blockedOnLogin,
    note: "A stop-state. The rail must show Setup blocked rather than reading as progress." },
  { id: "fresh", label: "Nothing declared yet", panel: freshSession,
    note: "A provisioned session with no query. The only question is what it is for." },
  { id: "page2", label: "Page 2, page 1 walked", panel: secondPage,
    note: "A past page collapses to its record — what was picked there — and never to a bare "
      + "'done'. Page 2's decision is its own group, so nothing overwrites page 1's history." },
];
