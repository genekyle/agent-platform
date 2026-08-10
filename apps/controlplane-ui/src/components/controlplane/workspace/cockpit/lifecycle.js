// THE ONE AUTHORITATIVE WORKFLOW STATE.
//
// Everything the session cockpit renders is derived HERE, from the panel read model, in one pure
// function. Nothing downstream may decide for itself whether a control belongs on screen.
//
// This module exists because the old panel had no such place. Each capability was added as its own
// conditional card, so "where is this session" was answered independently by the ladder, the crank,
// the picker, the arbitration band, the observer card and the apply queue — six answers, all live
// at once, none of them labelled as the real one. On 2026-08-05 the operator's screenshot showed
// an unsaved pick for one job, an in-flight application for a DIFFERENT job, and a rung asserting
// picks were made — three claims about "what are we doing now", with nothing to say which was
// current. That is not a styling problem and no amount of hierarchy fixes it: it is what happens
// when a screen has no single source of truth about its own state.
//
// So: one derivation, one CURRENT group, exactly one FOCUS, and one PRIMARY action inside that
// focus. If a new capability does not fit, it becomes a new focus kind or a new inspector row —
// never a new top-level card. That rule is the whole point.
//
// THE GROUPING FOLLOWS THE LADDER, NOT A TEXTBOOK PIPELINE (operator-directed, 2026-08-05 second
// pass). The first cut imposed five fixed phases — Setup · Discover · Decide · Execute · Verify —
// which was a shape the work does not have: the checkpoint ladder is four preamble rungs walked
// ONCE, then a cycle PER RESULTS PAGE (read it → pick from it → work each pick to a terminal flag).
// Forcing every page through one shared "Decide" box meant page 2's choice would overwrite page 1's
// in the display, exactly the contextual clobbering the operator asked to prevent. So the rail now
// mirrors the ladder itself:
//
//   Session          — the browser and the sign-in. Held for the session's whole life.
//   Search N         — this query and its radius. A session holds SEVERAL, one after another:
//                      abandoning a query costs the query, never the signed-in browser.
//   Page 1, Page 2…  — one group per page rung, each carrying its own read + picks + applications.
//                      Past pages collapse to their record; the current page is the work.

//: The preamble rung ids, in ladder order — mirrors session_checkpoints.PREAMBLE.
export const PREAMBLE_IDS = ["provisioned", "authenticated", "query_entered", "radius_set"];

// THE PREAMBLE IS TWO SCOPES, NOT ONE (operator-corrected 2026-08-06). The browser and the
// sign-in belong to the SESSION and outlive every search in it; the query and the radius belong
// to ONE SEARCH. Rendering all four as a single "Session" group taught the operator the same
// wrong thing the backend used to enforce — that changing the query means starting over.
//
// Mirrors session_checkpoints.SESSION_RUNGS / SEARCH_SCOPED.
export const SESSION_IDS = ["provisioned", "authenticated"];
export const SEARCH_IDS = ["query_entered", "radius_set"];

// What the operator is being asked for, in their words rather than the API's, and WHICH GROUP the
// ask belongs to. `stage` is the load-bearing half: a blocker is the truest statement available
// about where a session is, so it outranks every other signal when the current group is resolved.
//   session — the preamble is not holding (sign-in, browser, query, radius)
//   page    — the current page's cycle is what is stuck
//   end     — the ladder cannot grow; closing out is the operator's call
export const BLOCKERS = {
  operator_login: { stage: "session", text: "Couldn't sign in with a saved login. Pick a way in below, or sign in directly in the window." },
  operator_2fa: { stage: "session", text: "A verification code is being asked for. That one is yours — enter it in the window, then step again." },
  operator_open_engine: { stage: "session", text: "No tab for this site is open and one couldn't be opened. Open its home page in the window, then step again." },
  operator_challenge: { stage: "session", text: "A challenge is up. Clear it yourself in the window — we never auto-solve." },
  operator_browser: { stage: "session", text: "The session's Chrome isn't answering. Start it, then step again." },
  operator_clean_start: { stage: "session", text: "This window still holds tabs from a previous session. Clear them before we begin." },
  operator_search_box: { stage: "session", text: "Couldn't find the search box. Open the job search, then step again." },
  operator_verify: { stage: "session", text: "The search was submitted but not confirmed. Check the window before stepping." },
  operator_filter: { stage: "session", text: "The distance filter wouldn't set. We don't gather below the radius floor." },
  operator_results: { stage: "page", text: "Couldn't read this page's results. Check the window, then step again." },
  recover: { stage: "page", text: "Get back to the results we already have — do not search again." },
  choose: { stage: "page", text: "Pick what to act on from this page." },
  operator_end: { stage: "end", text: "Nothing left to page into for this query. Searching for something else keeps this session and its sign-in — closing the session is a separate call." },
};

// What the crank just did, in words. The raw action ids are dispatch keys, not labels.
export const ACTION_COPY = {
  probe_browser: "checked the browser",
  auth_probe: "checked sign-in",
  run_query: "ran the query",
  set_distance: "set the radius",
  review_page: "read the page",
  recover: "needs recovery",
  choose: "recorded your picks",
  pre_gate: "stopped at a challenge",
  select_page: "recorded your picks",
};

// The ways an application can END other than being sent. `submitted` is deliberately not in this
// list — it is its own control, because it is the only one that means success and the only one
// that claims a real application went out.
//
// Parked and abandoned stay visibly distinct: "not now" versus "not ever". Collapsing them either
// resurrects dead requisitions forever or quietly drops applications you meant to come back to.
export const TERMINAL_CHOICES = [
  { flag: "parked:account_wall", label: "Account wall",
    why: "This ATS needs an account only you may create. Parked, not lost." },
  { flag: "parked:unknown_ats", label: "Unknown ATS",
    why: "Nobody has driven this platform yet — park it for a teaching session." },
  { flag: "parked:ai_recruiter", label: "AI recruiter",
    why: "A video/audio interview gate. Yours to complete, not ours." },
  { flag: "parked:assessment", label: "Assessment",
    why: "A survey or skills test stands between here and submit." },
  { flag: "parked:operator", label: "Park",
    why: "Your call — not now, come back to it." },
  { flag: "abandoned:ats_unavailable", label: "Job gone",
    why: "The requisition outlived the listing. Not ever, rather than not now." },
  { flag: "abandoned:operator", label: "Not a fit",
    why: "You looked and do not want it. Closed for good." },
];

//: Server rung status -> rail status. `regressed` and `lapsed` become `attention` because both mean
//: "this stopped being true", which the rail must show rather than average into pending.
const RUNG_STATUS = {
  held: "done", next: "current", pending: "pending", regressed: "attention", lapsed: "attention",
};

//: The page number in a `page:N` / `select:N` rung id, or null.
function pageOf(id, prefix) {
  if (!id?.startsWith(prefix)) return null;
  const n = parseInt(id.slice(prefix.length), 10);
  return Number.isNaN(n) ? null : n;
}

function rungStep(r) {
  return {
    key: `rung:${r.id}`,
    label: r.label,
    status: RUNG_STATUS[r.status] || "pending",
    // "spent" means the cost was actually PAID — a consuming rung that is merely next has not
    // been spent, and labelling it so would misreport what this session has already cost.
    note: r.kind === "consuming" ? (r.reached ? "spent" : "once only") : "",
    meta: r.reached ? `${r.reached.initiator}${r.reached.evidence ? ` · ${r.reached.evidence}` : ""}` : "",
    at: r.reached?.at || null,
    select: { kind: "rung", id: r.id },
  };
}

function applicationSteps(steps, currentJobId) {
  return steps.map((s) => ({
    key: `app:${s.job_id}`,
    label: s.title || s.job_id,
    status: s.done ? (s.terminal === "submitted" ? "done" : "attention")
      : s.job_id === currentJobId ? "current" : "pending",
    note: s.done ? s.terminal : s.job_id === currentJobId ? "now" : "queued",
    meta: [s.company, s.platform].filter(Boolean).join(" · "),
    select: { kind: "application", id: s.job_id },
  }));
}

// --- the focus: the ONE thing the operator can act on -----------------------------------------
//
// A focus carries at most one PRIMARY. Alternates are real choices kept beside it; `more` is the
// rarely-right tail (the terminal flags) that lives behind a disclosure. A step we cannot perform
// is still the right next move to SAY — it becomes `primary: null` with a `say`, because a button
// that cannot act is how a panel lies.

function actionFrom(opt, extra = {}) {
  if (!opt) return null;
  if (!opt.driveable) return null;
  return { label: opt.label, endpoint: opt.endpoint, body: opt.body || {}, why: opt.why,
    // CARRIED, NOT RE-DERIVED. The API says which action is the irreversible one; a UI that
    // decided that for itself by matching on a label would send an application the day somebody
    // reworded a button.
    consequential: !!opt.consequential, operatorOnly: !!opt.operator_only, ...extra };
}

function setupFocus(p, last) {
  if (last?.fresh_start?.to_close?.length > 0) {
    const holds = last.fresh_start.holds_work?.length || 0;
    return {
      kind: "clean_start",
      title: "This window is not empty",
      subtitle: `${last.fresh_start.to_close.length} tab(s) inherited from a previous session`,
      why: "A persistent profile restores tabs, and one of them can be somebody's half-finished "
        + "application. Nothing here is closed silently.",
      primary: {
        label: holds > 0 ? `Clean start — discard ${holds} in-progress` : "Clean start",
        endpoint: "/clean_start", body: { confirm_discards_work: holds > 0 },
        why: "Close the inherited tabs and land on a clean window.",
      },
      alternates: [],
    };
  }
  if (last?.login) {
    const ways = (last.login.options || []).map((o) => ({
      label: o.name, endpoint: "/login_action", body: { control_name: o.name, role: o.role },
      why: o.why,
    }));
    const recheck = { label: "I've signed in — re-check", endpoint: "/step", body: {},
      why: "Re-probe the sign-in state after you have taken over." };
    // NO PRIMARY WHEN THE WAYS IN ARE PEERS. Google SSO and an emailed code are not ranked — which
    // one is right depends on the account, and that is the operator's knowledge, not ours. Styling
    // one of them as the obvious press would be a preference we do not have. An empty option list
    // is a real answer too, and then re-checking IS the only move.
    return {
      kind: "login",
      title: ways.length ? "Pick a way in" : "Signing in",
      subtitle: last.login.state,
      why: "The clicks toward a login screen are ours; the credential itself never is."
        + (ways.length ? " These are peers — nothing here is preselected." : ""),
      primary: ways.length ? null : recheck,
      alternates: ways.length ? [...ways, recheck] : [],
    };
  }
  if (!p.query) {
    return {
      kind: "declare",
      title: "What is this session for?",
      subtitle: "one focused browser, one query",
      why: "A session runs ONE query — re-searching is what makes the board collapse results — so "
        + "this is declared once and then spent.",
      primary: null, // the Initialize button lives in the form; it is the form's own submit
      alternates: [],
    };
  }
  return {
    kind: "climb",
    title: "Reaching the start line",
    subtitle: p.next?.label || "",
    why: p.next?.reason || "",
    primary: { label: `Step · ${p.next?.label || ""}`, endpoint: "/step", body: {},
      why: p.next?.reason || "Turn the crank once." },
    alternates: [],
  };
}

function readFocus(p) {
  if (p.next?.kind === "recover") {
    return {
      kind: "recover",
      title: "Recover — do not repeat",
      subtitle: p.next.label || "",
      why: p.next.recovery,
      primary: { label: `Recover · ${p.next.label || ""}`, endpoint: "/step", body: {},
        why: p.next.recovery },
      alternates: [],
    };
  }
  return {
    kind: "read",
    title: `Read page ${p.page ?? 1}`,
    subtitle: p.next?.label || "",
    why: p.next?.reason || "",
    primary: { label: "Read this page", endpoint: "/step", body: {},
      why: p.next?.reason || "Read this page's results so there is something to decide." },
    // Abandoning a query mid-way costs the query and nothing else — offered here as an alternate
    // rather than a primary, because reading the page is still the expected move.
    searchAgain: true,
    alternates: [],
  };
}

function decideFocus(p, results, picks, qs) {
  // Choosing AGAIN after every queued application landed is the same moment as choosing the first
  // time — the STANDING select rung allows adding picks — but the subtitle carries the record
  // forward so "pick more or advance" doesn't read like nothing happened.
  const allDone = qs.total > 0 && qs.done === qs.total;
  return {
    kind: "choose",
    title: `Page ${p.page ?? 1} · ${results.length} result${results.length === 1 ? "" : "s"}`,
    subtitle: allDone
      ? `${qs.submitted} submitted · all ${qs.total} accounted for — pick more, or advance`
      : picks.length ? `${picks.length} picked, not saved yet` : "nothing picked yet",
    why: "Picking a job is approval to enter its application. Nothing is submitted without a "
      + "separate confirmation.",
    // Same move as on the read focus: a query that is not yielding is abandoned here, and it
    // costs the query rather than the signed-in session.
    searchAgain: true,
    // Neither is disabled at 0 picks: "nothing on this page" is a real answer and the page still
    // counts as reviewed. Taking that away strands a page of nothing-for-me behind a refusal.
    primary: { label: picks.length ? `Take ${picks.length} · apply here` : "Take none · stay",
      endpoint: "/choose", body: { advance: false },
      why: "Queue these applications and stay on this page to work them." },
    alternates: [{ label: picks.length ? `Take ${picks.length} · next page` : "Nothing here · next page",
      endpoint: "/choose", body: { advance: true },
      why: "Record the picks and turn to the next page of results." }],
  };
}

function executeFocus(p, step, nextAction) {
  const proposal = p.proposal && p.proposal.job_id === step.job_id ? p.proposal : null;
  const handoff = p.account_handoff && p.account_handoff.job_id === step.job_id ? p.account_handoff : null;
  const account = !handoff && p.account_state && p.account_state.job_id === step.job_id ? p.account_state : null;

  // WHICH QUESTION IS ON TOP. Only one of these can be the thing to answer, and the order is the
  // order the world imposes: a teacher pause is a question already asked; an account wall is a wall
  // you cannot walk through; a sign-in leg is the wall's other half; otherwise the arbitrated next
  // action stands.
  const base = {
    title: step.title || step.job_id,
    subtitle: [step.company, step.platform, step.landing_state?.replace(/_/g, " ")]
      .filter(Boolean).join(" · "),
    more: TERMINAL_CHOICES,
  };

  // PARKED: the application is mid-flight and waiting on the operator — the truth of the step,
  // so it outranks any stale proposal riding on it. One primary: step back in. Reopen archives
  // the walked rungs and re-walks from the top of the page (apply_steps.reopen's own contract).
  if ((step.terminal || "").startsWith("parked")) {
    return { ...base, kind: "application", parked: step.terminal,
      flow: p.apply_flow || null,
      why: step.terminal_detail
        || "This application parked for you. Stepping back in resumes it where the page really is.",
      primary: { label: "Step back in", endpoint: "/apply_reopen",
        body: { job_id: step.job_id, reason: "operator stepped back in from the cockpit" },
        why: "Reopen the parked application and re-walk this page's ladder from the top." },
      alternates: [] };
  }

  if (proposal) {
    return { ...base, kind: "proposal", proposal,
      why: proposal.rationale,
      // Correct is a PEER of Go, never quieter: the golden training rows come from disagreement,
      // and a surface whose easy path is always "yes" produces agreement and no signal.
      primary: null, alternates: [] };
  }
  if (handoff) {
    return { ...base, kind: "account_handoff", handoff,
      why: "Your account, your call. The system can create it for you — a captcha or an email "
        + "verification code still stops for you, and the honeypot is never touched.",
      primary: { label: "Create it automatically", endpoint: "/apply_account", body: { mode: "auto" },
        why: "Fills the form with these credentials and clicks Create Account." },
      alternates: [
        { label: "Fill, I'll submit", endpoint: "/apply_account", body: { mode: "fill" },
          why: "Fill the form but leave the Create Account click to you." },
        { label: "I created it", endpoint: "/apply_account", body: { mark_created: true },
          why: "You typed it yourself — mark done and continue." },
      ] };
  }
  if (account) {
    const signInDue = account.leg === "sign_in" && account.has_creds;
    return { ...base, kind: "account", account, signInDue,
      why: signInDue
        ? "The account exists and its login is stored. Nothing to type — a captcha or a 2FA code "
          + "still stops for you."
        : `No account here yet (${account.status}). The credential is derived on demand and stored `
          + "the moment it works.",
      primary: { label: signInDue ? "Sign in automatically" : `${account.button} automatically`,
        endpoint: "/apply_account", body: { mode: "auto" },
        why: signInDue ? "Fills the sign-in form from the stored credential and clicks Sign In."
          : "Fills the whole create-account form and submits it." },
      alternates: [
        { label: "Fill, I'll click", endpoint: "/apply_account", body: { mode: "fill" },
          why: "Fill the form but leave the click to you." },
        { label: "Show details", endpoint: "/apply_account", body: { mode: "handoff" },
          why: "Show the credential and the exact steps, and re-read the live form." },
        ...(signInDue ? [{ label: "It was never created", endpoint: "/apply_account", body: { reset: true },
          why: "This account was marked created but does not exist — back to pending." }] : []),
      ] };
  }

  // LOST IS ITS OWN MOMENT (operator, 2026-08-10). The arbitration says the screen is
  // unrecognised mid-application: the primary is a LOOK — orient, with the witnesses' scored
  // reads on the surface — and the ladder's presumed rung waits demoted until the page is
  // recognised. Forcing "Work this · Open the posting" here was the wrong-menu bug.
  if (nextAction?.lost) {
    return { ...base, kind: "orient", flow: p.apply_flow || null,
      whereabouts: p.observer || null,
      why: nextAction.why || "The screen isn't one the recipe or the observer recognises.",
      primary: actionFrom(nextAction),
      alternates: nextAction.secondary ? [actionFrom(nextAction.secondary,
        { demoted: nextAction.secondary.demoted_because })].filter(Boolean) : [] };
  }

  // THE ARBITRATED ACTION, and it is the ONLY place it renders. The old panel drew `next_action`
  // in a band AND the same rung again as the queue step's own button — same label, same
  // `/apply_step` endpoint, both styled primary, ~700px apart. One authority, one button.
  const primary = actionFrom(nextAction);

  // THE GATE IS ITS OWN MOMENT. Every rung before it is reversible and reads "work this step";
  // this one sends an application to a real employer under the operator's name. A button that
  // looks like the five before it gets pressed by muscle memory, so the gate gets its own focus
  // kind, its own copy, and a surface that states exactly what is about to leave.
  //
  // It is still ONE primary action — the rule the whole pane exists to enforce — just an
  // unmistakable one.
  if (primary?.consequential) {
    return { ...base, kind: "gate", flow: p.apply_flow || null,
      why: "This is the irreversible one. Everything before it on this ladder can be walked back; "
        + "this cannot. Nothing is sent until you press it.",
      sending: { title: step.title || step.job_id, company: step.company || "",
        platform: step.platform || "", state: step.landing_state || "" },
      primary, alternates: [] };
  }

  return { ...base, kind: "application", flow: p.apply_flow || null,
    why: nextAction?.why || "",
    say: nextAction && !nextAction.driveable ? nextAction.label : "",
    primary,
    alternates: nextAction?.secondary ? [actionFrom(nextAction.secondary,
      { demoted: nextAction.secondary.demoted_because })].filter(Boolean) : [] };
}

function endFocus(qs) {
  return {
    kind: "walked_out",
    title: "This search is walked out",
    subtitle: `${qs.submitted} submitted · ${qs.done}/${qs.total} accounted for`,
    why: BLOCKERS.operator_end.text,
    // A WALKED-OUT SEARCH IS NOT A WALKED-OUT SESSION. The browser is open and still signed in,
    // so the natural next move is another search in it — not closing anything down.
    primary: { label: "Search for something else", detour: "declare",
      why: "Same session, same sign-in — only the query changes." },
    alternates: [],
  };
}

/**
 * Declaring the NEXT search in this session — the second legitimate detour.
 *
 * Reuses the `declare` focus so there is one setup form, not two. What changes is the framing:
 * this is not provisioning a session, it is pointing an open, signed-in one at different work.
 * `/initialize` decides whether that is a new search or a refused repeat; the surface does not
 * pre-judge it.
 */
export function newSearchFocus(panel) {
  const p = panel || {};
  const spent = Object.values(p.search?.spent || {});
  return {
    kind: "declare",
    group: "search", groupLabel: p.search?.n > 1 ? `Search ${p.search.n}` : "Search",
    title: "Search for something else",
    subtitle: `Session #${p.session_id} stays open and signed in to ${p.engine || "the board"}`,
    why: "Only the query changes. The browser, the sign-in and everything already applied to are "
      + "untouched"
      + (spent.length
        ? ` — this session has already run ${spent.map((q) => `“${q}”`).join(", ")}, and running `
          + "one of those again is the repeat that gets results collapsed."
        : "."),
    primary: null, alternates: [],
  };
}

/**
 * The choose moment for the CURRENT page, on demand — the one legitimate detour.
 *
 * The STANDING select rung allows re-opening the picker and adding to a page's picks after the
 * queue has started, so clicking that rung in the rail brings this moment back. It is a detour and
 * the work surface labels it as one; what it must never become is a second live question, which is
 * why there is still only ever one focus rendered at a time.
 */
export function chooseFocus(panel, picks = []) {
  const p = panel || {};
  const qs = p.queue_summary || { total: 0, done: 0, submitted: 0 };
  return { group: `page:${p.page ?? 1}`, groupLabel: `Page ${p.page ?? 1}`,
    ...decideFocus(p, p.results || [], picks, qs) };
}

/**
 * Derive the whole cockpit from the panel read model.
 *
 * @param {object} panel  the `/api/session_control/{id}` read model
 * @param {object} opts   `picks` — the LOCAL, unsaved pick order (it changes what choose is asking)
 * @returns {{current: string, groups: object[], focus: object, blocker: object|null, cycle: object}}
 */
export function deriveCockpit(panel, { picks = [] } = {}) {
  const p = panel || {};
  const ladder = p.ladder || [];
  const results = p.results || [];
  const steps = p.queue?.steps || [];
  const qs = p.queue_summary || { total: 0, done: 0, submitted: 0, blocks_page: false };
  const currentStep = steps.find((s) => !s.done) || null;
  // A PARKED application is attention, not history. `parked:*` is a terminal flag so it never
  // shows as the current step — and the cockpit fell back to the pick table while a half-finished
  // application held the tab open (2026-08-10). Waiting-on-you outranks "pick more".
  const parkedStep = currentStep ? null
    : [...steps].reverse().find((s) => (s.terminal || "").startsWith("parked")) || null;
  const attentionStep = currentStep || parkedStep;
  const atLine = !!p.progress?.at_start_line;
  const page = p.page ?? 1;

  const blocker = p.awaiting && BLOCKERS[p.awaiting]
    ? { ...BLOCKERS[p.awaiting], awaiting: p.awaiting }
    : null;

  // WHERE THE SESSION ACTUALLY IS — one resolution, in priority order, every branch a fact about
  // the world rather than a preference about layout:
  //   1. a session/end blocker — the truest thing available: the session is stopped, and where.
  //   2. an application in flight — it holds the page open, so it IS the work. This is the branch
  //      that resolves the 2026-08-05 screenshot: the ladder said "page 1 reviewed, next" while an
  //      application was mid-flight, and both were true. Only one of them was the work.
  //   3. results on screen — the page's decision (first time or choosing again).
  //   4. at the start line with nothing read yet — read the page.
  //   5. still climbing — the preamble.
  let focus;
  if (blocker?.stage === "session") focus = setupFocus(p, p.last_step);
  else if (blocker?.stage === "end") focus = endFocus(qs);
  else if (attentionStep) focus = executeFocus(p, attentionStep, p.next_action);
  else if (results.length > 0) focus = decideFocus(p, results, picks, qs);
  else if (atLine) focus = readFocus(p);
  else focus = setupFocus(p, p.last_step);

  const pageMoments = new Set(["read", "recover", "choose", "proposal", "account_handoff",
    "account", "application", "gate", "orient"]);
  const current = pageMoments.has(focus.kind) ? `page:${page}` : "session";
  focus = { ...focus, group: current,
    groupLabel: current === "session" ? "Session" : `Page ${page}` };

  // --- the groups: session, then this search, then one per page ----------------------------
  const byId = new Map(ladder.map((r) => [r.id, r]));
  const pages = [...new Set(ladder.map((r) => pageOf(r.id, "page:") ?? pageOf(r.id, "select:"))
    .filter((n) => n !== null))].sort((a, b) => a - b);
  if (!pages.includes(page) && p.query) pages.push(page);

  const groups = [];

  {
    // THE SESSION: the browser and the sign-in. These survive every search inside it, which is
    // the whole point of the 2026-08-06 correction — abandoning a query must not cost them.
    const sessionRungs = SESSION_IDS.map((id) => byId.get(id)).filter(Boolean);
    const stepsRows = sessionRungs.map(rungStep);
    const allHeld = stepsRows.length > 0 && stepsRows.every((s) => s.status === "done");
    const status = blocker?.stage === "session" ? "blocked"
      : current === "session" ? "current"
        : stepsRows.some((s) => s.status === "attention") ? "attention"
          : allHeld ? "done" : "open";
    groups.push({
      id: "session", label: "Session", status, steps: stepsRows,
      summary: allHeld
        ? `open · signed in to ${p.engine || "the board"}`
        : `${stepsRows.filter((s) => s.status === "done").length}/${stepsRows.length || 2} ready`,
      select: { kind: "group", id: "session" },
    });
  }

  {
    // THE SEARCH: this query and what it means. One session can hold several, one after another.
    const searchRungs = SEARCH_IDS.map((id) => byId.get(id)).filter(Boolean);
    const stepsRows = searchRungs.map(rungStep);
    const allHeld = stepsRows.length > 0 && stepsRows.every((s) => s.status === "done");
    const n = p.search?.n || 1;
    const spentCount = Object.keys(p.search?.spent || {}).length;
    groups.push({
      id: "search", label: n > 1 ? `Search ${n}` : "Search", status: allHeld ? "done" : "open",
      steps: stepsRows,
      summary: [
        p.query ? `“${p.query}”` : "not declared yet",
        allHeld ? "run" : "not run yet",
        // Says out loud that earlier searches are on the record — which is what makes re-running
        // one refusable and starting a different one free.
        spentCount > 1 ? `${spentCount} run this session` : null,
      ].filter(Boolean).join(" · "),
      select: { kind: "group", id: "search" },
    });
  }

  for (const n of pages) {
    const pageRung = byId.get(`page:${n}`);
    const selectRung = byId.get(`select:${n}`);
    const isCurrentPage = n === page;
    const stepsRows = [
      ...(pageRung ? [rungStep(pageRung)] : []),
      ...(selectRung ? [rungStep(selectRung)] : []),
      // Applications live under THEIR page. Only the current page's queue exists in the read
      // model — a past page's applications all reached a terminal flag before it could advance,
      // and their record is the select rung's evidence.
      ...(isCurrentPage ? applicationSteps(steps, currentStep?.job_id) : []),
    ];
    const blocked = isCurrentPage && blocker?.stage === "page";
    // "done" has to MEAN done. A past page with unfinished steps renders `attention`, and the
    // current page is `current` even when its rungs are held — this is the fix for the old `4/4`
    // header that read as complete beside an unfinished list.
    const status = blocked ? "blocked"
      : isCurrentPage && current !== "session" ? "current"
        : stepsRows.some((s) => s.status === "attention") ? "attention"
          : stepsRows.length && stepsRows.every((s) => s.status === "done") ? "done"
            : isCurrentPage ? "open" : "pending";
    // A past page's one line is its RECORD — what was decided there, from the select rung's own
    // evidence — never just "done".
    const picksEvidence = selectRung?.reached?.evidence?.split(" — ")[0];
    groups.push({
      id: `page:${n}`, label: `Page ${n}`, status, steps: stepsRows,
      // SCOPES DON'T MIX. The current page's count is the LOCAL draft (unsaved picks) or its own
      // select rung's evidence — never `p.picks`, which is the session-wide approved list and
      // would claim page 1's decisions as page 2's.
      summary: isCurrentPage
        ? (qs.total ? `${qs.done}/${qs.total} done · ${qs.submitted} submitted`
          : results.length ? (picks.length
            ? `${picks.length} of ${results.length} picked, not saved`
            : picksEvidence || `${results.length} to decide`)
            : pageRung?.status === "held" ? "read" : "not read yet")
        : (picksEvidence || (pageRung?.status === "held" ? "reviewed" : "not reached")),
      select: { kind: "group", id: `page:${n}` },
    });
  }

  return {
    current,
    groups,
    focus,
    blocker,
    cycle: {
      page,
      pages_reviewed: p.progress?.pages_reviewed ?? 0,
      application: currentStep
        ? { index: steps.filter((s) => s.done).length + 1, total: qs.total, job_id: currentStep.job_id }
        : null,
    },
  };
}
