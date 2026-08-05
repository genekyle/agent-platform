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
// So: one derivation, five lifecycle phases, exactly one CURRENT phase, exactly one FOCUS, and one
// PRIMARY action inside that focus. If a new capability does not fit, it becomes a new focus kind
// or a new evidence row — never a new top-level card. That rule is the whole point.

//: The lifecycle, in order. Setup happens once; Discover → Decide → Execute → Verify then CYCLE,
//: once per results page (and Execute/Verify cycle again per application inside a page). The rail
//: says so rather than drawing a straight line the work does not walk.
export const PHASES = [
  { id: "setup", label: "Setup", blurb: "a reachable browser, signed in, nothing inherited" },
  { id: "discover", label: "Discover", blurb: "the query, the radius, and this page's results" },
  { id: "decide", label: "Decide", blurb: "which of these to apply to, and in what order" },
  { id: "execute", label: "Execute", blurb: "work each chosen application to a terminal flag" },
  { id: "verify", label: "Verify", blurb: "what actually landed, and what it landed as" },
];

export const PHASE_IDS = PHASES.map((p) => p.id);

// What the operator is being asked for, in their words rather than the API's, and WHICH PHASE the
// ask belongs to. The phase is the load-bearing half: a blocker is the truest statement available
// about where a session is, so it outranks every other signal when the current phase is resolved.
export const BLOCKERS = {
  operator_login: { phase: "setup", text: "Couldn't sign in with a saved login. Pick a way in below, or sign in directly in the window." },
  operator_2fa: { phase: "setup", text: "A verification code is being asked for. That one is yours — enter it in the window, then step again." },
  operator_open_engine: { phase: "setup", text: "No tab for this site is open and one couldn't be opened. Open its home page in the window, then step again." },
  operator_challenge: { phase: "setup", text: "A challenge is up. Clear it yourself in the window — we never auto-solve." },
  operator_browser: { phase: "setup", text: "The session's Chrome isn't answering. Start it, then step again." },
  operator_clean_start: { phase: "setup", text: "This window still holds tabs from a previous session. Clear them before we begin." },
  operator_search_box: { phase: "discover", text: "Couldn't find the search box. Open the job search, then step again." },
  operator_verify: { phase: "discover", text: "The search was submitted but not confirmed. Check the window before stepping." },
  operator_filter: { phase: "discover", text: "The distance filter wouldn't set. We don't gather below the radius floor." },
  operator_results: { phase: "discover", text: "Couldn't read this page's results. Check the window, then step again." },
  recover: { phase: "discover", text: "Get back to the results we already have — do not search again." },
  choose: { phase: "decide", text: "Pick what to act on from this page." },
  operator_end: { phase: "verify", text: "This query is walked out. Closing the session is your call." },
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

//: Checkpoint id -> lifecycle phase. The ids come from `session_checkpoints.py`; the page/select
//: prefixes are the rolling rungs that make the cycle.
export function phaseOfRung(id = "") {
  if (id === "provisioned" || id === "authenticated") return "setup";
  if (id.startsWith("select:")) return "decide";
  return "discover"; // query_entered, radius_set, page:N
}

//: Server rung status -> rail status. `regressed` and `lapsed` become `attention` because both mean
//: "this stopped being true", which the rail must show rather than average into pending.
const RUNG_STATUS = {
  held: "done", next: "current", pending: "pending", regressed: "attention", lapsed: "attention",
};

function rungSteps(ladder, phase) {
  return ladder
    .filter((r) => phaseOfRung(r.id) === phase)
    .map((r) => ({
      key: `rung:${r.id}`,
      label: r.label,
      status: RUNG_STATUS[r.status] || "pending",
      // "spent" means the cost was actually PAID — a consuming rung that is merely next has not
      // been spent, and labelling it so would misreport what this session has already cost.
      note: r.kind === "consuming" ? (r.reached ? "spent" : "once only") : "",
      meta: r.reached ? `${r.reached.initiator}${r.reached.evidence ? ` · ${r.reached.evidence}` : ""}` : "",
      at: r.reached?.at || null,
      select: { kind: "rung", id: r.id },
    }));
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
  return { label: opt.label, endpoint: opt.endpoint, body: opt.body || {}, why: opt.why, ...extra };
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

function discoverFocus(p, results) {
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
  const read = results.length === 0;
  return {
    kind: "climb",
    title: read ? `Read page ${p.page ?? 1}` : `Page ${p.page ?? 1}`,
    subtitle: p.next?.label || "",
    why: p.next?.reason || "",
    primary: { label: read ? "Read this page" : `Step · ${p.next?.label || ""}`,
      endpoint: "/step", body: {}, why: p.next?.reason || "Turn the crank once." },
    alternates: [],
  };
}

function decideFocus(p, results, picks) {
  return {
    kind: "choose",
    title: `Page ${p.page ?? 1} · ${results.length} result${results.length === 1 ? "" : "s"}`,
    subtitle: picks.length ? `${picks.length} picked, not saved yet` : "nothing picked yet",
    why: "Picking a job is approval to enter its application. Nothing is submitted without a "
      + "separate confirmation.",
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

  // THE ARBITRATED ACTION, and it is the ONLY place it renders. The old panel drew `next_action`
  // in a band AND the same rung again as the queue step's own button — same label, same
  // `/apply_step` endpoint, both styled primary, ~700px apart. One authority, one button.
  return { ...base, kind: "application",
    why: nextAction?.why || "",
    say: nextAction && !nextAction.driveable ? nextAction.label : "",
    primary: actionFrom(nextAction),
    alternates: nextAction?.secondary ? [actionFrom(nextAction.secondary,
      { demoted: nextAction.secondary.demoted_because })].filter(Boolean) : [] };
}

function verifyFocus(p, qs, blocker) {
  if (blocker?.phase === "verify") {
    return {
      kind: "walked_out",
      title: "This query is walked out",
      subtitle: `${qs.submitted} submitted · ${qs.done}/${qs.total} accounted for`,
      why: BLOCKERS.operator_end.text,
      primary: null, alternates: [],
    };
  }
  return {
    kind: "landed",
    title: "What landed",
    subtitle: `${qs.submitted} submitted · ${qs.done}/${qs.total} accounted for`,
    why: "An application counts as done only when it reaches a terminal flag. Nothing is skipped.",
    primary: null, alternates: [],
  };
}

/**
 * The focus for ONE phase — normally the current one.
 *
 * Exported because the operator may look back at a phase the session has moved past (re-open the
 * picker and add to a page's picks, which the STANDING select rung allows by design). That is a
 * DETOUR, and the work surface labels it as one; what it must never become is a second live
 * question, which is why there is still only ever one focus rendered at a time.
 */
export function focusFor(panel, phase, { picks = [] } = {}) {
  const p = panel || {};
  const results = p.results || [];
  const steps = p.queue?.steps || [];
  const qs = p.queue_summary || { total: 0, done: 0, submitted: 0 };
  const currentStep = steps.find((s) => !s.done) || null;
  const blocker = p.awaiting && BLOCKERS[p.awaiting]
    ? { ...BLOCKERS[p.awaiting], awaiting: p.awaiting } : null;

  switch (phase) {
    case "setup": return { phase, ...setupFocus(p, p.last_step) };
    case "discover": return { phase, ...discoverFocus(p, results) };
    case "decide": return { phase, ...decideFocus(p, results, picks) };
    case "execute": return currentStep
      ? { phase, ...executeFocus(p, currentStep, p.next_action) }
      : { phase, kind: "idle", title: "Nothing queued",
          subtitle: qs.total ? `${qs.done}/${qs.total} accounted for` : "no applications yet",
          why: "Applications appear here once a page's picks are taken.",
          primary: null, alternates: [] };
    case "verify": return { phase, ...verifyFocus(p, qs, blocker) };
    default: return { phase, kind: "idle", title: "", primary: null, alternates: [] };
  }
}

/**
 * Derive the whole cockpit from the panel read model.
 *
 * @param {object} panel  the `/api/session_control/{id}` read model
 * @param {object} opts   `picks` — the LOCAL, unsaved pick order (it changes what Decide is asking)
 * @returns {{current: string, phases: object[], focus: object, blocker: object|null, cycle: object}}
 */
export function deriveCockpit(panel, { picks = [] } = {}) {
  const p = panel || {};
  const ladder = p.ladder || [];
  const results = p.results || [];
  const steps = p.queue?.steps || [];
  const qs = p.queue_summary || { total: 0, done: 0, submitted: 0, blocks_page: false };
  const currentStep = steps.find((s) => !s.done) || null;
  const atLine = !!p.progress?.at_start_line;

  const blocker = p.awaiting && BLOCKERS[p.awaiting]
    ? { ...BLOCKERS[p.awaiting], awaiting: p.awaiting }
    : null;

  // WHERE THE SESSION ACTUALLY IS. One resolution, in priority order, and every branch is a fact
  // about the world rather than a preference about layout:
  //   1. a blocker — the truest thing available: the session is stopped, and this is where.
  //   2. an application in flight — it holds the page open, so it IS the work. This is the branch
  //      that resolves the screenshot: the ladder said "page 1 reviewed, next" while an
  //      application was mid-flight, and both were true. Only one of them was the work.
  //   3. results on screen with the decision still open.
  //   4. at the start line with nothing read yet.
  //   5. still climbing — wherever the next rung lives.
  let current;
  if (blocker) current = blocker.phase;
  else if (currentStep) current = "execute";
  else if (qs.total > 0 && qs.done === qs.total && results.length === 0) current = "verify";
  else if (results.length > 0) current = "decide";
  else if (atLine) current = "discover";
  else current = phaseOfRung(p.next?.id || (ladder.find((r) => r.status === "next")?.id) || "provisioned");

  const focus = focusFor(p, current, { picks });

  const doneSteps = steps.filter((s) => s.done);
  const openSteps = steps.filter((s) => !s.done);

  const phases = PHASES.map((ph) => {
    const rungs = rungSteps(ladder, ph.id);
    const stepRows = ph.id === "execute" ? applicationSteps(openSteps, currentStep?.job_id)
      : ph.id === "verify" ? applicationSteps(doneSteps, null)
        : rungs;

    const blocked = blocker?.phase === ph.id;
    const isCurrent = ph.id === current;
    // "done" has to MEAN done. A phase behind the current one whose own steps have not all landed
    // is `open`, never ticked — this is the "4/4" defect the audit found, where the header counted
    // only the four preamble rungs and read as complete beside a list of six with one unfinished.
    // It is exactly the live case in the 2026-08-05 screenshot: an application holds the page open
    // while Discover's `page:1` rung is still unreached. Both are true, and a tick would say
    // otherwise.
    const idx = PHASE_IDS.indexOf(ph.id);
    const curIdx = PHASE_IDS.indexOf(current);
    const settled = stepRows.length > 0 && stepRows.every((s) => s.status === "done");
    const status = blocked ? "blocked"
      : isCurrent ? "current"
        : stepRows.some((s) => s.status === "attention") ? "attention"
          : idx > curIdx ? "pending"
            : settled ? "done"
              : stepRows.length ? "open" : "pending";

    return {
      ...ph,
      status,
      steps: stepRows,
      // The one-line summary a COLLAPSED phase shows. A completed phase should say what it
      // achieved, not merely that it is shut.
      summary: summaryFor(ph.id, { p, qs, results, picks, rungs, doneSteps, openSteps }),
      select: { kind: "phase", id: ph.id },
    };
  });

  return {
    current,
    phases,
    focus,
    blocker,
    cycle: {
      page: p.page ?? 1,
      pages_reviewed: p.progress?.pages_reviewed ?? 0,
      application: currentStep
        ? { index: doneSteps.length + 1, total: qs.total, job_id: currentStep.job_id }
        : null,
    },
  };
}

function summaryFor(phaseId, { p, qs, results, picks, rungs, doneSteps, openSteps }) {
  const held = rungs.filter((r) => r.status === "done").length;
  switch (phaseId) {
    case "setup":
      return held === rungs.length && rungs.length
        ? `browser ready · signed in to ${p.engine || "the board"}`
        : `${held}/${rungs.length} ready`;
    case "discover":
      return p.query
        ? `"${p.query}"${p.location ? ` · ${p.location}` : ""} · page ${p.page ?? 1}`
        : "no query yet";
    case "decide":
      return results.length
        ? `${picks.length || p.picks?.length || 0} of ${results.length} picked`
        : "nothing to decide yet";
    case "execute":
      return qs.total ? `${openSteps.length} open of ${qs.total}` : "nothing queued";
    case "verify":
      return qs.total ? `${qs.submitted} submitted · ${doneSteps.length} accounted for`
        : "nothing to verify yet";
    default:
      return "";
  }
}
