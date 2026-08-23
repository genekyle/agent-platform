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

// SCREENS THAT ARE INSIDE THE APPLICATION — past whatever identity wall the ATS put in front of
// it. Matched on the KIND half of `<platform>_<kind>`, so it holds for a scripted spine and the
// generic cadence alike rather than listing Workday's screens and going stale on the next ATS.
//
// It answers one question: has the wall already been walked through? An account's lifecycle
// cannot answer that — `active` means "a login exists", which makes the next leg `sign_in`
// forever — so after a signup that logged us straight in, the cockpit covered Workday's My
// Information with a "Sign in automatically" button that would have navigated away from it
// (live 2026-08-13). The screen is the thing that knows.
export const APPLICATION_SCREENS =
  /_(my_information|my_experience|questions|application_form|voluntary_disclosures|self_identify|review|confirmation|submitted)$/;

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
  // NOT the same key as above — the account seam used to reuse `operator_verify` for this and the
  // cockpit told the operator to check a SEARCH while the page wanted a 6-digit code. Same bug
  // the `operator_2fa` split fixed, retired at this seam by PLAN_verify_email_leg.
  account_verify_email: { stage: "page", text: "The new account wants email verification. The card on the work surface says which mechanism and what settles it." },
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
  // A DUPLICATE IS NOT A REJECTION. Indeed re-surfaced C&S the day after it was submitted, it was
  // picked again, and skipping it had to borrow "Not a fit" — which tells the decision ledger the
  // operator rejected a role they had in fact applied for. The ledger is the thing being trained.
  { flag: "abandoned:already_applied", label: "Already applied",
    why: "We have sent one for this requisition before. Not a judgement about the job — the work "
       + "is done, it just happened earlier." },
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
  // ONLY AN EXPLICIT `false` RETIRES THE NEXT-PAGE MOVE. `has_next: null` means the reader did not
  // report, and "we did not look" must not render as "there is nothing there" — the same tri-state
  // discipline `_parked_all` keeps for `tab_open`.
  const exhausted = p.page_meta?.has_next === false;
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
    exhausted,
    // Neither is disabled at 0 picks: "nothing on this page" is a real answer and the page still
    // counts as reviewed. Taking that away strands a page of nothing-for-me behind a refusal.
    primary: { label: picks.length ? `Take ${picks.length} · apply here` : "Take none · stay",
      endpoint: "/choose", body: { advance: false },
      why: "Queue these applications and stay on this page to work them." },
    // THE LAST PAGE HAS NO NEXT PAGE, AND THE BUTTON USED TO CLAIM OTHERWISE.
    //
    // `page_meta.has_next` is Indeed's own pagination-next link, read on every page and thrown
    // away until 2026-08-20. Without it the cockpit offered "Nothing here · next page" on the
    // final page of a search — an affordance whose only outcome is a refusal, which is the same
    // lie-shaped control the `realign` guard above exists to prevent.
    //
    // Tri-state on purpose: only an explicit `false` retires the button. `null` means the reader
    // did not say, and a page we did not look at must keep offering the move.
    alternates: [exhausted
      ? { label: picks.length ? `Take ${picks.length} · finish this search`
                              : "No more pages · finish this search",
          endpoint: "/choose", body: { advance: false, complete: true },
          why: "This was the last page of results — Indeed offers no next page. Recording the "
             + "picks closes the search out; every job it showed us stays on the ledger under "
             + "this query." }
      : { label: picks.length ? `Take ${picks.length} · next page` : "Nothing here · next page",
          endpoint: "/choose", body: { advance: true },
          why: "Record the picks and turn to the next page of results." }],
  };
}

function executeFocus(p, step, nextAction) {
  const proposal = p.proposal && p.proposal.job_id === step.job_id ? p.proposal : null;
  const handoff = p.account_handoff && p.account_handoff.job_id === step.job_id ? p.account_handoff : null;
  // The email-verification wall, scoped to the step being worked like its three siblings. The
  // stored half is the wall's identity; `mechanism` and `leg` arrive re-derived from the read
  // model (`_account_verify`), so the card cannot describe last week's wall.
  const verify = p.account_verify && p.account_verify.job_id === step.job_id
    ? p.account_verify : null;
  // SCOPED LIKE ITS THREE SIBLINGS. `applied_check` is written on landing and survives on the
  // blackboard until the NEXT landing overwrites it, so a step reached without one — a resume, a
  // repick, any path that does not re-open the pane — would render the previous job's verdict as
  // if it were this one's. "Already applied" is precisely the sentence an operator acts on by NOT
  // applying, so showing it about the wrong job is the expensive direction to be wrong in.
  const applied = p.applied_check && p.applied_check.for_job_id === step.job_id
    ? p.applied_check : null;
  // THE WALL STANDS DOWN ONCE WE ARE PAST IT. `account_state` is derived from the ACCOUNT's
  // lifecycle — active means "a login exists", which makes the next leg `sign_in` forever — and it
  // knows nothing about where the browser is standing. So after a signup that logged us straight
  // in (Workday does), the cockpit sat on My Information and offered "Sign in automatically" as
  // the primary: a button that navigates AWAY from the form it is covering. The account rung
  // already refuses the mirror case ("still before workday's account wall — press Apply first");
  // this is the other side of the same fact, and the screen is the one that knows it.
  //
  // ...SO ASK THE SCREEN. This tested `step.landing_state` — the RECORD — which is the one thing
  // on the panel that does not know where the browser is standing. Live 2026-08-16: a refresh
  // silently signed the session out, the record still read `workday_my_information`, so this said
  // "past the wall" and suppressed the account controls WHILE THE WINDOW WAS SHOWING THE WALL.
  // The observer had it right and said so — high confidence, Recipe Mismatch, "Create account" on
  // the stepper — and the panel offered the operator no way to act on its own diagnosis.
  //
  // The observation decides when it actually read the page; an abstaining one (unknown /
  // unreadable) falls back to the record rather than inventing a position from a non-answer.
  const seen = p.observer || null;
  const readIt = !!(seen && seen.kind && seen.kind !== "unknown" && seen.kind !== "unreadable");
  const pastTheWall = readIt
    ? seen.kind !== "account_gate" && APPLICATION_SCREENS.test(seen.state || "")
    : APPLICATION_SCREENS.test(step.landing_state || "");
  const account = !handoff && !pastTheWall
    && p.account_state && p.account_state.job_id === step.job_id ? p.account_state : null;

  // WHICH QUESTION IS ON TOP. Only one of these can be the thing to answer, and the order is the
  // order the world imposes: a teacher pause is a question already asked; an account wall is a wall
  // you cannot walk through; a sign-in leg is the wall's other half; otherwise the arbitrated next
  // action stands.
  const base = {
    title: step.title || step.job_id,
    // The heading names the screen the OPERATOR is looking at. Reading it off the record left the
    // subtitle saying "workday my information" over a sign-in wall while every other part of the
    // panel had already followed the window — a header contradicting its own page.
    subtitle: [step.company, step.platform,
               (readIt ? seen.state : step.landing_state)?.replace(/_/g, " ")]
      .filter(Boolean).join(" · "),
    more: TERMINAL_CHOICES,
  };

  // THE RECORD CAN FALL BEHIND THE WINDOW, AND THE WAY OUT WAS CURL-ONLY.
  //
  // `/reconcile_step` has existed since the rebuilt-queue work — the apply-step half of "the
  // browser is truth, the record is memory" — and it was reachable by nothing the operator can
  // press. Which broke the reach-parity rule exactly where it costs most: found live 2026-08-11
  // with the ladder pinned on an `enter_apply` that had already succeeded (a stale hosts list
  // demoted it), the application tab plainly open on Cornerstone, and no button in the cockpit
  // that could say so. The operator's only offers were to redo work the world had done or to
  // flag an outcome that had not happened — one wastes a click, the other falsifies the record.
  //
  // Offered only when an application tab is actually open: reconcile refuses without one ("there
  // is nothing the window can prove"), and an affordance that can only refuse is a lie-shaped one.
  const applyTabOpen = (p.tabs || []).some((t) => t.role === "apply");
  const realign = applyTabOpen ? {
    label: "Catch up to the window",
    endpoint: "/reconcile_step", body: {},
    why: "Record only what the open application tab PROVES — the browser is truth and the record "
       + "is memory, so when they disagree, memory yields. It never invents progress: the "
       + "near-miss guard still refuses to rubber-stamp an identity it cannot match.",
  } : null;

  // PARKED: the application is mid-flight and waiting on the operator — the truth of the step,
  // so it outranks any stale proposal riding on it. One primary: step back in. Reopen archives
  // the walked rungs and re-walks from the top of the page (apply_steps.reopen's own contract).
  //
  // AND A TRUTHFUL WAY OUT (2026-08-10 audit): a parked application is attention, not arrest.
  // Without `searchAgain` the only exits from this focus were the terminal flags — every one
  // that frees the surface falsifies the outcome, and the truthful one (Park) re-imprisons.
  // Declaring the next search leaves the park exactly as it is; the backend harvests it into
  // the session-level parked list, where Step back in still reaches it.
  if ((step.terminal || "").startsWith("parked")) {
    // DOES THE TAB IT PROMISED TO COME BACK TO STILL EXIST?
    //
    // The backend has answered this since 2026-08-13 — `_parked_all` compares each parked step's
    // recorded `tab_url` against the LIVE window and publishes `tab_open`, tri-state, precisely
    // because "a shutdown closes the tab and anything typed into it goes with it". The focus never
    // asked. So the cockpit kept offering "Step back in" over a tab that no longer existed, which
    // is the fact existing and the seam that needs it not consulting it (operator, 2026-08-20:
    // "we actually have stale ui in our controller asking us if we want to step back in ... make
    // sure that never happens again like checking to see if the tab exists").
    //
    // It is NOT removed when the tab is gone — reopening still works, it just starts the page's
    // ladder over instead of resuming. The button tells the truth about which one you are getting,
    // because an affordance that silently means something else is worse than one that is absent.
    const parkedRow = (p.parked || []).find((r) => r.job_id === step.job_id) || null;
    const tabGone = parkedRow?.tab_open === false;
    return { ...base, kind: "application", parked: step.terminal,
      flow: p.apply_flow || null, applied,
      searchAgain: true,
      tabGone,
      why: tabGone
        ? "The tab this application parked on is gone — closed with the session or by a cleanup. "
          + "Anything typed into it that was never saved on the employer's side went with it, so "
          + "this starts the page's ladder over rather than resuming where it stopped."
        : (step.terminal_detail
           || "This application parked for you. Stepping back in resumes it where the page really is."),
      primary: { label: tabGone ? "Start this application over" : "Step back in",
        endpoint: "/apply_reopen",
        body: { job_id: step.job_id, reason: "operator stepped back in from the cockpit" },
        why: tabGone
          ? "Its tab is closed, so there is nothing to resume — this re-walks the page's ladder "
            + "from the top."
          : "Reopen the parked application and re-walk this page's ladder from the top." },
      alternates: [] };
  }

  if (proposal) {
    return { ...base, kind: "proposal", proposal,
      why: proposal.rationale,
      // Correct is a PEER of Go, never quieter: the golden training rows come from disagreement,
      // and a surface whose easy path is always "yes" produces agreement and no signal.
      primary: null, alternates: [] };
  }
  // THE VERIFICATION WALL, before the account cards: a code prompt on screen is a wall the leg
  // buttons cannot walk through — "Sign in automatically" over a verify screen is the same
  // wrong-form press the Workday toggle lesson was about. Every mechanism gets a truthful exit;
  // the CODE mechanism gets the one automation that exists; the code itself never renders.
  if (verify) {
    const code = verify.mechanism === "code";
    const link = verify.mechanism === "link";
    const done = { label: code ? "I entered the code" : "I finished the verification",
      endpoint: "/apply_account", body: { mark_created: true },
      why: "You settled the wall by hand — record the account as made and continue." };
    return { ...base, kind: "verify_email", verify,
      why: code
        ? `The site emailed a one-time code to ${verify.mailbox || "the shared inbox"}. The `
          + "errand reads it off the subject line — no mail is opened, and an ambiguous or "
          + "stale match stops for you rather than guessing."
        : link
          ? "The site sent a verification LINK, not a code. The errand never opens a mail (no "
            + "read receipt), so the click is yours — press it in Gmail, then continue."
          : (verify.detail
             || "The page asks for verification but its mechanism couldn't be measured — "
              + "finish it in the window, then continue."),
      primary: code
        ? { label: "Fetch code from Gmail & continue", endpoint: "/apply_account",
            body: { mode: "auto" },
            why: "Reads the code from the inbox and enters it on the wall. A missing mail, an "
               + "ambiguous match, or a second factor still stops for you." }
        : done,
      alternates: code ? [done] : [] };
  }
  if (handoff) {
    // AND THE VERB IS THE LEG'S, for the same reason the label is the ATS's. The handoff's `leg`
    // is re-read from the account record on every render (`_account_handoff`), so it can now say
    // `sign_in` about a card that was written for a create — which is precisely the case where a
    // button reading "Create it automatically" would send the operator at the wrong form. The
    // endpoint always drove whichever leg is due; only the wording assumed one.
    const signingIn = handoff.leg === "sign_in";
    const press = handoff.button || "the submit";
    return { ...base, kind: "account_handoff", handoff,
      why: signingIn
        ? "The account exists — this signs in with the stored credential. A captcha or an email "
          + "verification code still stops for you."
        : "Your account, your call. The system can create it for you — a captcha or an email "
          + "verification code still stops for you, and the honeypot is never touched.",
      // THE BUTTON IS THE ATS'S OWN WORD FOR IT, not ours. `handoff.button` has carried the real
      // label since the handoff was first written (iCIMS says "Submit Profile"), and this branch
      // spelled "Create Account" into the prose anyway while its `account` sibling one block below
      // interpolated it correctly. PowerSchool's identifier-first signup made the drift visible:
      // the screen the operator is sent to has a "Continue" and no "Create Account" anywhere on
      // it, so the sentence described a control that is not there.
      primary: { label: signingIn ? "Sign in automatically" : "Create it automatically",
        endpoint: "/apply_account", body: { mode: "auto" },
        why: `Fills the form with these credentials and clicks ${press}.` },
      alternates: [
        { label: "Fill, I'll submit", endpoint: "/apply_account", body: { mode: "fill" },
          why: `Fill the form but leave the ${press} click to you.` },
        // THE TRUTHFUL EXIT, on both legs. This settles the rung and stores the credential the
        // card showed, so an operator who did it by hand is not left pressing an automation
        // button to say they did not need one.
        { label: signingIn ? "I signed in" : "I created it",
          endpoint: "/apply_account", body: { mark_created: true },
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
  //
  // `searchAgain` USED TO BE WITHHELD FROM EVERY IN-FLIGHT FOCUS, and the reasoning was sound
  // against the backend of the time: `/initialize` refused a new search over any step without a
  // terminal flag, so the door led only to a refusal, and a button that can only refuse is a
  // lie-shaped affordance. The premise is gone (2026-08-13). `/initialize` now prices the step
  // back instead of forbidding it — an unopened pick is released, a DRIVEN application is parked
  // with the operator's stated reason and stays resumable — and the declare surface shows that
  // bill before anything is pressed. So the door is real from here, and withholding it was the
  // thing that left the operator with no menu but "work this application" when they wanted a
  // different search entirely. It stays an ALTERNATE: the application in flight is still the work.
  if (nextAction?.lost) {
    return { ...base, kind: "orient", flow: p.apply_flow || null, applied,
      whereabouts: p.observer || null,
      searchAgain: true,
      why: nextAction.why || "The screen isn't one the recipe or the observer recognises.",
      primary: actionFrom(nextAction),
      alternates: [
        ...(nextAction.secondary ? [actionFrom(nextAction.secondary,
          { demoted: nextAction.secondary.demoted_because })] : []),
        realign,
      ].filter(Boolean) };
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
  //
  // AND THE ONE IN-FLIGHT FOCUS THAT KEEPS NO `searchAgain`. Everywhere else the door out is a
  // fair offer; here it is a distraction one press from an irreversible act, on a surface built to
  // carry exactly one unmistakable choice. Stepping back from the gate is still reachable — Park,
  // in More — and it costs a deliberate press, which at this particular moment is the point.
  if (primary?.consequential) {
    return { ...base, kind: "gate", flow: p.apply_flow || null, applied,
      why: "This is the irreversible one. Everything before it on this ladder can be walked back; "
        + "this cannot. Nothing is sent until you press it.",
      sending: { title: step.title || step.job_id, company: step.company || "",
        platform: step.platform || "", state: step.landing_state || "" },
      primary, alternates: [] };
  }

  return { ...base, kind: "application", flow: p.apply_flow || null, applied,
    searchAgain: true,
    // ONLY WHERE THERE IS A RUNG TO REPEAT. The ordinary application focus is exactly that: a
    // driveable next action that is not the irreversible one. The gate, the account wall, the
    // teacher's proposal and the login are all moments where "do that again" means nothing, and
    // offering it there would be a button whose only outcome is an immediate hand-back.
    runnable: !!primary && !primary.consequential,
    why: nextAction?.why || "",
    say: nextAction && !nextAction.driveable ? nextAction.label : "",
    primary,
    alternates: [
      ...(nextAction?.secondary ? [actionFrom(nextAction.secondary,
        { demoted: nextAction.secondary.demoted_because })] : []),
      realign,
      stopThisApplication(step),
    ].filter(Boolean) };
}

/**
 * THE SAFE WAY OUT OF AN APPLICATION THAT IS ALREADY UNDER WAY.
 *
 * Every other exit in `TERMINAL_CHOICES` is a judgement about the JOB — not a fit, job gone,
 * account wall, assessment. None of them is a judgement about the ATTEMPT, and until now the only
 * way to stop a half-driven form was to pick one of those and lie to the decision ledger about
 * why. That ledger is the thing being trained, so a mis-flagged abort teaches the wrong lesson
 * twice: once about the employer, once about the platform.
 *
 * `parked:operator` is the honest flag for it — "your call, not now" — and the backend already
 * treats parked as resumable and leaves staged work alone (`leaves_work_open`). What was missing
 * was a PRESS: it sat eighth in a nine-item disclosure headed "End this application another way",
 * which is not where anyone looks when a drive has gone sideways and they want out.
 *
 * Operator, 2026-08-20: "you need to build a way in the cockpit if we want to abort an in-progress
 * application we can safely do that." So it stands beside the primary, it says what it costs, and
 * it keeps the application on the ledger.
 */
function stopThisApplication(step) {
  if (!step || step.done) return null;
  return {
    label: "Stop this application",
    endpoint: "/apply_flag",
    body: { job_id: step.job_id, flag: "parked:operator",
            detail: "stopped by the operator from the cockpit" },
    why: "Ends this attempt and hands the page back. It stays on the ledger and stays resumable — "
       + "nothing typed is discarded and nothing is sent. Use this rather than 'Not a fit', which "
       + "records a judgement about the JOB you may not mean.",
  };
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
 * THE BROWSER IS GONE, and that outranks everything the session still wants to do.
 *
 * A shut-down session keeps its whole ledger — the query stays SPENT, the page's results stay
 * cached, the queue keeps its picks in order. Only `provisioned` regresses, because only the
 * browser actually went away. But the focus resolution below reads "an application in flight" as
 * the truest fact available, so a stopped session rendered its apply step and offered "Work this ·
 * Open the posting" over a Chrome that did not exist.
 *
 * That is a lie-shaped affordance, and an expensive one: the only reachable alternative was
 * starting FRESH, which spends a second query against Indeed for a search already run and picked
 * from. Operator, after exactly that happened: "we wasted a good search and actual candidates."
 * The resume is what makes `close_out(keep_work)` honest — putting work down is only safe if
 * picking it back up is one press.
 */
function resumeFocus(p) {
  const steps = p.queue?.steps || [];
  const held = steps.filter((s) => !s.done || (s.terminal || "").startsWith("parked:"));
  const names = held.slice(0, 3).map((s) => s.title).filter(Boolean).join(" · ");
  return {
    kind: "resume",
    title: "This session is shut down",
    subtitle: p.query ? `“${p.query}”${p.location ? ` · ${p.location}` : ""}` : "",
    why: held.length
      ? `Its browser stopped, but nothing else did: ${held.length} application`
        + `${held.length === 1 ? "" : "s"} still on the ledger${names ? ` (${names}${held.length > 3 ? " …" : ""})` : ""}, `
        + `and the search is already spent for this session. Resuming relaunches the browser on the `
        + `same signed-in profile and picks the queue back up — starting fresh would run a second `
        + `query for a page you have already chosen from.`
      : "Its browser stopped. Resuming relaunches it on the same signed-in profile; the session's "
        + "ledger is untouched.",
    primary: { label: held.length ? `Resume · carry ${held.length} over` : "Resume this session",
      endpoint: "/resume", body: {},
      why: "Relaunches this session's Chrome on its own profile. The sign-in comes back with it "
         + "and the search is not re-run." },
    alternates: [],
  };
}

/**
 * THE RESULTS PAGE IS GONE, and the queue is worked ON that page.
 *
 * A consuming rung whose effect has lapsed (the results tab closed, the browser relaunched) is
 * reported by the ladder as RECOVER — "return to the results we already have, never re-submit".
 * `step` honours it and refuses to dispatch. But the focus below reads "an application in flight"
 * as the truest fact, so the cockpit offered "Work this · Open the posting" over a browser sitting
 * on about:blank: the next queued job is opened by CLICKING ITS CARD, and there is no card.
 *
 * Only when nothing is mid-flight on an ATS tab. A parked application whose own tab is open is
 * genuinely the work and does not need the results page — that step is worked where it stands.
 */
function recoverResultsFocus(p) {
  const held = (p.queue?.steps || []).filter((s) => !s.done).length;
  return {
    kind: "recover_results",
    title: "The results page is gone",
    subtitle: p.query ? `“${p.query}”${p.location ? ` · ${p.location}` : ""}` : "",
    why: (p.next?.recovery
      || "Return to the results we already have — never re-submit the same query.")
      + (held ? ` ${held} queued application${held === 1 ? " is" : "s are"} opened by clicking `
        + `${held === 1 ? "its card" : "their cards"} on that page.` : ""),
    primary: { label: "Reopen the results", endpoint: "/resume", body: {},
      why: "Reopens the page this search already reached, from the session's own query, location "
         + "and radius. The search is NOT re-run — re-submitting is what gets it collapsed." },
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
 *
 * IT NOW CARRIES THE BILL. Stepping back out of a search is a decision with a price, and the price
 * used to arrive as a 409 AFTER the operator had typed a new query — naming one job, in prose,
 * with no way to act on it. `search.step_back` is that price computed before anything is pressed:
 * how many picks nobody opened (released, free), which applications have real work in them
 * (parked with your reason, still resumable), what is already parked, and what has been submitted
 * and is therefore untouchable. `needsReason` is the one case the backend will refuse without it.
 */
export function newSearchFocus(panel) {
  const p = panel || {};
  const spent = Object.values(p.search?.spent || {});
  const cost = p.search?.step_back || { worked: [], unworked: [], parked: [], submitted: 0 };
  const worked = cost.worked || [];
  const unworked = cost.unworked || [];
  const parked = cost.parked || [];
  const bill = [
    unworked.length ? `${unworked.length} pick${unworked.length === 1 ? "" : "s"} nobody opened `
      + "— released, costs nothing" : null,
    worked.length ? `${worked.length} application${worked.length === 1 ? "" : "s"} with real work `
      + "— parked with your reason, still resumable" : null,
    parked.length ? `${parked.length} already parked — kept` : null,
    cost.submitted ? `${cost.submitted} already submitted — untouched` : null,
  ].filter(Boolean);
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
    // The picks do NOT carry over, and saying so here is the point: a fresh query means a fresh
    // result set, and a queue built from cards that are no longer on screen is the stale-context
    // fault this whole surface exists to avoid.
    stepBack: { ...cost, bill, needsReason: worked.length > 0 },
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
  const atLine = !!p.progress?.at_start_line;
  const page = p.page ?? 1;

  // A QUEUE BELONGS TO THE PAGE ITS PICKS WERE MADE ON, AND THE SESSION CAN LEAVE THAT PAGE.
  //
  // `queue.page` is stamped when the picks are made; `p.page` is where the search is NOW. When the
  // operator pages forward, the old queue is history — but every step in it is still readable, so
  // the parked-step rule above kept handing the focus to an application from a page nobody is on.
  // Measured 2026-08-20: after paging to 2, the cockpit rendered page 1's three finished steps
  // under a "Page 2 · 3/3 done" chip, held focus on the parked one, and offered NO route to read
  // page 2 at all. The operator had moved on and the screen had not.
  //
  // An UNFINISHED step still outranks the page — a live application holds the tab open wherever
  // the results went, which is the same exception `resultsGone` makes below. Only the parked
  // (already-terminal) case is demoted, because "waiting on you" stops being true once you have
  // walked to another page.
  const queuePage = p.queue?.page;
  const queueIsStale = queuePage != null && queuePage !== page;
  const attentionStep = currentStep || (queueIsStale ? null : parkedStep);

  const blocker = p.awaiting && BLOCKERS[p.awaiting]
    ? { ...BLOCKERS[p.awaiting], awaiting: p.awaiting }
    : null;

  // WHERE THE SESSION ACTUALLY IS — one resolution, in priority order, every branch a fact about
  // the world rather than a preference about layout:
  //   0. THE BROWSER IS GONE. Truer than any of the below, because every one of them describes
  //      work that needs a browser to do. A shut-down session keeps its ledger, so branch 2 used
  //      to win and offer "Work this" over a Chrome that did not exist (2026-08-13).
  //   1. a session/end blocker — the truest thing available: the session is stopped, and where.
  //   2. an application in flight — it holds the page open, so it IS the work. This is the branch
  //      that resolves the 2026-08-05 screenshot: the ladder said "page 1 reviewed, next" while an
  //      application was mid-flight, and both were true. Only one of them was the work.
  //   3. results on screen — the page's decision (first time or choosing again).
  //   4. at the start line with nothing read yet — read the page.
  //   5. still climbing — the preamble.
  //
  // `regressed` and not "anything but held": a rung that has never been walked is the ordinary
  // start of a session and belongs to the preamble at branch 5, which knows how to climb it.
  // Regressed means it WAS held and the world took it away — which is exactly a shutdown.
  const browserGone = ladder.some((r) => r.id === "provisioned" && r.status === "regressed");
  // 0b. The results page is gone and nothing is mid-flight elsewhere. Same shape as 0: the queue
  //     is worked ON that page, so offering its next step is offering a click with no card. An
  //     open ATS tab is the exception — that application is the work wherever the results went.
  const resultsGone = p.next?.kind === "recover"
    && !(p.tabs || []).some((t) => t.is_apply || t.role === "apply");

  let focus;
  if (browserGone) focus = resumeFocus(p);
  else if (resultsGone) focus = recoverResultsFocus(p);
  else if (blocker?.stage === "session") focus = setupFocus(p, p.last_step);
  else if (blocker?.stage === "end") focus = endFocus(qs);
  else if (attentionStep) focus = executeFocus(p, attentionStep, p.next_action);
  else if (results.length > 0) focus = decideFocus(p, results, picks, qs);
  else if (atLine) focus = readFocus(p);
  else focus = setupFocus(p, p.last_step);

  const pageMoments = new Set(["read", "recover", "choose", "proposal", "account_handoff",
    "account", "verify_email", "application", "gate", "orient"]);
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
      // The ATTENTION step, not just the in-flight one: a parked application is the surface's
      // subject too, and building this from `currentStep` alone meant every terminal-flag press
      // on a parked focus posted `job_id: undefined` and bounced off validation (found 2026-08-10
      // working the cockpit as the operator).
      application: attentionStep
        ? { index: steps.filter((s) => s.done).length + (currentStep ? 1 : 0),
            total: qs.total, job_id: attentionStep.job_id }
        : null,
    },
  };
}
