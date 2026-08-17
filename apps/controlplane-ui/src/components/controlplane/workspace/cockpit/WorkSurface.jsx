import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { PickOrb } from "../OrderedPicks";
import FormSections from "../FormSections";
import FillPlan from "../FillPlan";
import FormCensus from "../FormCensus";
import NameThisPage from "./NameThisPage";
import { chooseFocus, newSearchFocus, TERMINAL_CHOICES } from "./lifecycle";

// THE WORK SURFACE — the one thing the operator can act on now.
//
// The rule this pane exists to enforce: EXACTLY ONE PRIMARY ACTION ON SCREEN. Not one per card,
// not one per stage — one. Everything a phase could theoretically offer is not the same as what
// this moment is asking, and rendering the former is how the old panel came to show "Work this
// step" twice (same label, same `/apply_step` endpoint, both styled primary, ~700px apart) while a
// results table for a decision already made sat above both of them.
//
// So: the focus comes from `lifecycle.focusFor`, and this component may not invent an action of its
// own. It renders `focus.primary` (or says the move it cannot perform, which is not a button),
// `focus.alternates` quietly beside it, and `focus.more` behind a disclosure. Sub-surfaces —
// the fill plan, the accordion, the results table — are the OBJECT being worked, not competing
// answers, and their own buttons are demoted accordingly.

function Actions({ focus, busy, call, onNewSearch }) {
  if (!focus.primary && !focus.say && !(focus.alternates || []).length) return null;
  return (
    <div className="work__actions">
      {focus.primary && !focus.primary.detour && (
        // STILL EXACTLY ONE PRIMARY — the modifier changes how it reads, never how many there are.
        // A consequential action that looked like the five reversible ones before it is how an
        // application gets sent by muscle memory.
        //
        // `aria-label` mirrors the visible label wherever a `title` rides a button (here and on
        // every action below): assistive readers that prefer `title` were announcing the WHY
        // instead of the pressable words (found driving the cockpit through an accessibility
        // tree, 2026-08-10) — and the label a user can see must be the name a user can say.
        <button className={`btn btn-primary${focus.primary.consequential ? " btn-consequential" : ""}`}
                disabled={busy} title={focus.primary.why} aria-label={focus.primary.label}
                onClick={() => call(focus.primary.endpoint, focus.primary.body)}>
          {busy ? "…" : focus.primary.label}
        </button>
      )}
      {/* A primary that is a DETOUR changes what this surface asks rather than driving anything —
          it has no endpoint, so it must not be routed through `call`. */}
      {focus.primary?.detour === "declare" && (
        <button className="btn btn-primary" disabled={busy} title={focus.primary.why}
                aria-label={focus.primary.label}
                onClick={onNewSearch}>{focus.primary.label}</button>
      )}
      {/* DRIVE UNTIL SOMETHING NEEDS YOU. An alternate, never the primary: the primary is still
          one deliberate rung, and this is the same rung repeated until the world asks for a
          human. It adds no authority — every iteration is the press beside it — and it stops at
          the gate, at any refusal, at a rung that wants a human, at a terminal flag, and at a
          rung that ran twice and moved nothing. Offered only where the primary is a rung to
          crank: there is nothing to repeat at a gate or a login. */}
      {focus.runnable && (
        <span className="work__alt">
          <button className="btn btn-sm" disabled={busy} onClick={() => call("/run", {})}
                  aria-label="Run until you are needed"
                  title="Cranks this application's rungs one after another and stops the moment anything wants you — the gate, a refusal, a screen it cannot read. It can reach nothing the button beside it cannot.">
            Run until you&apos;re needed
          </button>
        </span>
      )}
      {/* Abandoning a search mid-way: same session, same sign-in, only the query changes. An
          alternate rather than a primary, because the expected move here is still the page. */}
      {focus.searchAgain && (
        <span className="work__alt">
          <button className="btn btn-sm" disabled={busy} onClick={onNewSearch}
                  aria-label="Search something else"
                  title="Same session and the same sign-in — only the query changes.">
            Search something else
          </button>
        </span>
      )}
      {/* With no primary the alternates ARE the choice, so they are full size — shrinking them
          would imply a preselected answer that does not exist. */}
      {(focus.alternates || []).filter(Boolean).map((a, i) => (
        <span key={i} className="work__alt">
          <button className={focus.primary ? "btn btn-sm" : "btn"} disabled={busy} title={a.why}
                  aria-label={a.label}
                  onClick={() => call(a.endpoint, a.body)}>
            {a.label}
          </button>
          {a.demoted && <span className="work__alt-why"> — {a.demoted}</span>}
        </span>
      ))}
    </div>
  );
}

// HOW FAR THIS APPLICATION IS FROM SUBMIT, drawn as the walk it is.
//
// The recipe has held an ordered spine for every Indeed application since it was written, and the
// cockpit's only word for progress was "Work this step" — pressed an unknown number of times,
// toward an end nobody could see. This is the object being worked, not a competing answer, so it
// carries no controls of its own.
//
// The count is an UPPER BOUND and says so: platforms skip screens whose answers the profile
// already holds, and skipping only ever shortens the path.
// WHAT "NEW" MEANS HERE, SAID PRECISELY.
//
// Every unplaced screen used to render as "New territory", a phrase this system reserves for
// something strong and rare: an ATS nobody has driven, go by hand. The operator read it on an
// ordinary job on ground we know well (2026-08-13) — "technically yes we've never opened this job
// before, but we've opened other job cards before". A warning that fires on the routine case
// cannot be read on the real one.
//
// So the platform question and the JOB question are separated. `flow.novelty` says which kind of
// unplaced we are looking at; `applied` answers the one the operator actually asked — have we
// been here before? That check already runs on landing (`applied_index`, three tiers: same id,
// same requisition through another door, same employer+role) and already halts an exact match.
// It was simply never shown next to the word "new".
const NOVELTY_TONE = {
  unread: "flowstrip--quiet",
  unclassified: "flowstrip--quiet",
  unplaced_screen: "flowstrip--quiet",
  new_platform: "flowstrip--unknown",
};

// The verdict's own vocabulary, not a guess at it: `applied` / `likely_applied` / `not_applied`
// (applied_index.STATUS_*). Matching on the POSITIVE values is the point — treating every status
// that was not the empty string as a hit turned `not_applied`, the answer meaning "nothing on
// file", into "Possibly applied before" on the very first job of the drive.
function AppliedNote({ applied }) {
  const status = applied?.status;
  if (status !== "applied" && status !== "likely_applied") {
    return <span className="flowstrip__note">Never applied to this one.</span>;
  }
  const when = applied.applied_at ? ` on ${applied.applied_at.slice(0, 10)}` : "";
  const how = applied.matched_on ? ` (${applied.matched_on})` : "";
  return (
    <span className={status === "applied" ? "flowstrip__note is-stop" : "flowstrip__note is-warn"}>
      {status === "applied"
        ? <><strong>Already applied</strong>{when}{how}.</>
        : <><strong>Possibly applied before</strong>{how} — {(applied.evidence || []).join("; ")}.</>}
    </span>
  );
}

function FlowStrip({ flow, applied }) {
  if (!flow) return null;
  if (!flow.recognised) {
    return (
      <div className={`flowstrip ${NOVELTY_TONE[flow.novelty] || "flowstrip--unknown"}`}>
        <AppIcon name="eye" size={12} />
        <span>
          <strong>{flow.headline || "New territory"}</strong> — {flow.why}.
        </span>
        <AppliedNote applied={applied} />
      </div>
    );
  }
  const left = flow.steps_to_submit;
  return (
    <div className="flowstrip">
      <div className="flowstrip__head">
        {flow.at_review_gate
          ? <span className="flowstrip__gate-note">At the Submit gate</span>
          : <span>{flow.bound} <strong>{left}</strong> screen{left === 1 ? "" : "s"} from Submit</span>}
      </div>
      <ol className="flowstrip__steps">
        {(flow.screens || []).map((s) => (
          <li key={s.state}
              className={"flowstrip__step"
                + (s.past ? " is-past" : "") + (s.current ? " is-current" : "")
                + (s.is_gate ? " is-gate" : "")}
              title={s.state}>
            {s.label}
          </li>
        ))}
      </ol>
    </div>
  );
}

// THE GATE. One press, and it is the only irreversible one on the ladder — so it states exactly
// what is about to leave, in the operator's terms, before the button rather than in a tooltip.
// WHERE ARE WE — the lost-state's evidence, on the acting surface (operator, 2026-08-10:
// "show scores and confidence of where it thinks it's at"). Every witness's read with its own
// claim and detail, and the observer's fused verdict on top. This is what makes "Orient" an
// informed press instead of a shrug: you can see exactly who recognised what before looking again.
function WhereAmI({ whereabouts }) {
  if (!whereabouts) return null;
  const witnesses = whereabouts.witnesses || [];
  return (
    <div className="whereami">
      <div className="whereami__verdict">
        <strong>{whereabouts.headline || whereabouts.state || "Unrecognised"}</strong>
        {whereabouts.platform && <span className="badge badge--muted">{whereabouts.platform}</span>}
        {whereabouts.confidence && (
          <span className={`badge ${whereabouts.confidence === "high" ? "badge--ready" : "badge--warn"}`}>
            {whereabouts.confidence} confidence
          </span>
        )}
        {whereabouts.state && <code>{whereabouts.state}</code>}
      </div>
      {witnesses.length > 0 && (
        <ul className="whereami__witnesses">
          {witnesses.map((w, i) => (
            <li key={`${w.source}-${i}`}>
              <code>{w.source}</code>
              {w.claim
                ? <span className="badge badge--muted">{w.claim}</span>
                : <span className="badge badge--muted">abstains</span>}
              {w.weight !== undefined && <small>w {Number(w.weight).toFixed(2)}</small>}
              {w.detail && <p>{w.detail}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function GateBody({ focus }) {
  const s = focus.sending || {};
  return (
    <div className="work__section gatecard">
      <div className="work__section-head">
        <AppIcon name="alert" size={13} /> About to send
      </div>
      <dl className="gatecard__what">
        <div><dt>Role</dt><dd>{s.title || "—"}</dd></div>
        <div><dt>Employer</dt><dd>{s.company || "—"}</dd></div>
        <div><dt>Through</dt><dd>{s.platform || "—"}</dd></div>
      </dl>
      <p className="gatecard__note">
        Pressing this submits the application as it currently stands in the window. Look at the
        tab first if you have not — this is the last moment it can be changed.
      </p>
    </div>
  );
}

// A step we cannot perform is still the right next move to SAY. What it must never be is a button:
// a control that cannot act is how a panel lies about what it can do.
function Say({ focus }) {
  if (!focus.say) return null;
  return (
    <p className="work__say">
      {focus.say}
      <span className="work__say-note">
        Not something this system can perform — it is the move to make, not a button to press.
      </span>
    </p>
  );
}

function More({ focus, busy, onFlag }) {
  if (!(focus.more || []).length) return null;
  return (
    <details className="work__more">
      <summary>End this application another way — {TERMINAL_CHOICES.length + 1} outcomes</summary>
      <div className="work__more-grid">
        {/* Submitted is separated from the rest because it is the only one that means success and
            the only one that claims a real application went out. */}
        <button className="btn btn-sm" disabled={busy} aria-label="Submitted"
                title="Only press this when the application is CONFIRMED sent"
                onClick={() => onFlag("submitted", "")}>
          Submitted
        </button>
        {focus.more.map((f) => (
          <button key={f.flag} className="btn btn-sm btn-ghost" disabled={busy} title={f.why}
                  aria-label={f.label}
                  onClick={() => onFlag(f.flag, f.why)}>
            {f.label}
          </button>
        ))}
      </div>
    </details>
  );
}

// --- per-phase bodies: the OBJECT being worked, never a second question ----------------------

function SetupBody({ focus, panel, form, setForm, busy, call }) {
  if (focus.kind === "declare") {
    // STEPPING BACK, PRICED BEFORE THE PRESS. `focus.stepBack` is what leaving the current search
    // costs — nothing at all on a first declaration, and on a live one the honest bill. It renders
    // above the form because it changes what the operator is deciding: not "what shall I search
    // for" but "what am I putting down to search for it".
    const back = focus.stepBack;
    const stepping = !!back && (back.bill || []).length > 0;
    const reason = (form.release_open || "").trim();
    // SAME TERMS, ASKED AGAIN. Checked against every query this session has SPENT — not just the
    // one currently declared — because `search.spent` is the ledger's own record of what has
    // actually hit the board, and a query declared but never run costs nothing to re-point at.
    // The old check compared only against `panel.query`, which disabled the button outright; the
    // once-only rule is about the accidental repeat, so this asks for a reason instead of refusing.
    const typed = (form.query || "").trim().toLowerCase();
    const alreadySpent = !!typed && Object.values(panel.search?.spent || {})
      .some((q) => (q || "").trim().toLowerCase() === typed);
    const rerun = (form.rerun_spent || "").trim();
    const blocked = (stepping && back.needsReason && !reason) || (alreadySpent && !rerun);
    return (
      <>
        {stepping && (
          <div className="work__section">
            <div className="work__section-head">
              <AppIcon name="boxes" size={13} /> What stepping back costs
            </div>
            <ul className="rungs">
              {back.bill.map((line) => (
                <li key={line} className="rung rung--pending">
                  <div className="rung__body"><div className="rung__line">{line}</div></div>
                </li>
              ))}
            </ul>
            {/* The picks do not carry over, and this is the sentence that says so. A new query is
                a new result set; a queue built from cards that have left the screen is exactly the
                stale-context fault the cockpit rebuild exists to prevent. */}
            <p className="empty-hint">
              {alreadySpent
                ? `Running “${panel.query}” again means a fresh selection — the result set turns `
                  + "over, so the earlier picks were chosen off cards that may no longer be there."
                : `A new query means a fresh selection — nothing from “${panel.query}” carries over.`}
            </p>
          </div>
        )}
        <div className="work-setup">
          <label className="work-field">
            <span>Query</span>
            <input value={form.query} disabled={busy} placeholder="reporting analyst"
                   onChange={(e) => setForm((f) => ({ ...f, query: e.target.value }))} />
          </label>
          <label className="work-field">
            <span>Location</span>
            <input value={form.location} disabled={busy} placeholder="Nashua, NH"
                   onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))} />
          </label>
          <label className="work-field work-field--narrow">
            <span>Radius (mi)</span>
            <input type="number" min={50} value={form.radius_miles} disabled={busy}
                   onChange={(e) => setForm((f) => ({ ...f, radius_miles: Number(e.target.value) }))} />
          </label>
        </div>
        {/* A REASON, NOT A CONFIRMATION TICK. Only asked when an application has actually been
            driven — an unopened pick costs nothing and being made to justify releasing it is
            friction that teaches operators to type anything. What is typed here becomes the
            parked step's own note AND the journal's `why`, so the record says why the work was
            put down, not merely that somebody agreed to put it down. */}
        {stepping && back.needsReason && (
          <label className="work-field">
            <span>Why are you stepping back?</span>
            <input value={form.release_open || ""} disabled={busy}
                   placeholder="wrong candidates for this query"
                   onChange={(e) => setForm((f) => ({ ...f, release_open: e.target.value }))} />
          </label>
        )}
        {/* THE SAME TERMS, ASKED AGAIN. Not forbidden — repeating a query TOO OFTEN is what gets
            it collapsed, and the same search a day later, when the postings have turned over, is
            the most ordinary thing a job search does. Asked for rather than refused, so the
            once-only rule keeps the job it is good at: stopping the accidental repeat. */}
        {alreadySpent && (
          <label className="work-field">
            <span>This session already ran that — why run it again?</span>
            <input value={form.rerun_spent || ""} disabled={busy}
                   placeholder="a day on, the postings have turned over"
                   onChange={(e) => setForm((f) => ({ ...f, rerun_spent: e.target.value }))} />
          </label>
        )}
        <div className="work__actions">
          <button className="btn btn-primary" disabled={busy || !form.query.trim() || blocked}
                  title={blocked
                    ? "Say why, and this starts a new search with that reason on the record."
                    : "Start this search on the browser that is already open and signed in."}
                  onClick={() => call("/initialize", { ...form })}>
            {busy ? "…" : alreadySpent ? "Run it again · new search"
              : stepping ? "Step back · start this search" : "Initialize this session"}
          </button>
          {/* The refusal, said BEFORE the press rather than as a 409 after it. */}
          {blocked && (
            <span className="work__alt-why">
              {alreadySpent && !rerun
                ? " — the board collapses results for a query repeated too often, so a deliberate "
                  + "re-run goes on the record with its reason."
                : ` — ${back.worked.map((w) => w.title || w.job_id).join(", ")} has real work in `
                  + "it; say why and it parks, still resumable."}
            </span>
          )}
        </div>
      </>
    );
  }

  if (focus.kind === "clean_start") {
    return (
      <div className="work__section">
        <div className="work__section-head">
          <AppIcon name="boxes" size={13} /> Inherited tabs
        </div>
        <ul className="rungs">
          {(panel.last_step?.fresh_start?.to_close || []).map((t) => (
            <li key={t.tab_id} className="rung rung--pending">
              <div className="rung__body">
                <div className="rung__line">
                  <span className="badge badge--muted">{t.role}</span>
                  <span className="rung__label">{t.url}</span>
                  {(t.role === "apply" || t.role === "errand") && (
                    <span className="badge badge--warn">may hold real work</span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
        {panel.last_step?.fresh_start?.keeper && (
          <p className="rung__meta">
            Keeping <code>{panel.last_step.fresh_start.keeper.url}</code> to land on.
          </p>
        )}
      </div>
    );
  }

  if (focus.kind === "login") {
    // The credential boundary, stated where the credential would be typed.
    return (
      <p className="cv-blocked">
        You take over at the password or code — we never type either.
        {panel.last_step?.login?.seen ? ` ${panel.last_step.login.seen} elements seen on this page.` : ""}
        {!(panel.last_step?.login?.options || []).length && panel.last_step?.login?.detail
          ? ` ${panel.last_step.login.detail}` : ""}
      </p>
    );
  }
  return null;
}

// THE SAME VERDICT, ONE ROW WIDE. `AppliedNote` speaks in sentences because it sits under a
// single application in flight; a 25-row picker needs the same fact as a glance. Same vocabulary,
// same source (`applied_index.STATUS_*`), so the two surfaces can never drift into disagreeing —
// and `not_applied` stays SILENT here, because 22 rows each declaring "never applied" is noise
// that would bury the three that matter.
function AppliedCell({ applied }) {
  const status = applied?.status;
  if (status !== "applied" && status !== "likely_applied") return <span className="muted">—</span>;
  const when = applied.applied_at ? applied.applied_at.slice(0, 10) : "";
  const via = applied.platform ? ` via ${applied.platform}` : "";
  return (
    <span className={status === "applied" ? "badge badge--danger" : "badge badge--warn"}
          title={status === "applied"
            ? `Applied ${when}${via} — matched on ${applied.matched_on}.`
            : `${(applied.evidence || []).join("; ")}. Matched on ${applied.matched_on}.`}>
      {status === "applied" ? "Applied" : "Maybe applied"}
    </span>
  );
}

function DecideBody({ panel, picks, armed, onPick, onClear, note, setNote, busy }) {
  const results = panel.results || [];
  if (results.length === 0) return null;
  // COUNTED OVER THE WHOLE PAGE, AND OVER THE PICKS SEPARATELY. "3 already applied" is a fact
  // about the search; "2 of them are in your picks" is the one that costs a drive, and it is the
  // sentence the operator needed on 2026-08-17 and did not have.
  const hit = (r) => r.applied?.status === "applied" || r.applied?.status === "likely_applied";
  const seenBefore = results.filter(hit);
  const pickedSeen = seenBefore.filter((r) => picks.includes(r.job_id));
  return (
    <>
      <div className="work__section">
        <div className="work__section-head">
          <AppIcon name="listTree" size={13} />
          {picks.length
            ? (armed ? "Click another number to swap the two — or the same one again to remove it."
              : "Click a number to pick it up and swap.")
            : "Click a circle to make it #1. They run in this order."}
          {picks.length > 0 && (
            <button className="btn btn-sm btn-ghost" disabled={busy} onClick={onClear}>Clear</button>
          )}
        </div>
        {seenBefore.length > 0 && (
          <p className={pickedSeen.length ? "work__why is-warn" : "work__why"}>
            {seenBefore.length} of these {seenBefore.length === 1 ? "has" : "have"} an application
            on file{pickedSeen.length > 0 && <> — <strong>{pickedSeen.length} of them
              {pickedSeen.length === 1 ? " is" : " are"} in your picks</strong></>}.
            {" "}A certain match is refused when you choose; a maybe is queued with a warning.
          </p>
        )}
        {/* The table scrolls INSIDE the surface. A 21-row list is the object of this decision, not
            a reason for the page to become three screens tall. */}
        <div className="work-results__scroll">
          <table className="work-results">
            <thead>
              <tr>
                <th className="work-results__ord">#</th>
                <th>Role</th><th>Company</th><th>Where</th><th>Pay</th><th>Applied?</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.job_id} className={picks.includes(r.job_id) ? "is-picked" : ""}>
                  <td className="work-results__ord">
                    <PickOrb jobId={r.job_id} label={r.title} picks={picks} armed={armed}
                             onPick={onPick} />
                  </td>
                  <td>{r.title}</td>
                  <td>{r.company}</td>
                  <td>{r.location}</td>
                  <td>{r.salary || "—"}</td>
                  <td><AppliedCell applied={r.applied} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <textarea className="work-note" rows={2} value={note}
                placeholder="Why these (or why none) — rides into the page's record."
                onChange={(e) => setNote(e.target.value)} />
    </>
  );
}

// A STEP THAT REFUSED, SAID OUT LOUD.
//
// These endpoints are careful in exactly the way that matters: when a rung's effect cannot be
// CONFIRMED they mark nothing, refuse to retry, and return 200 with `ok:false` plus a detail
// naming what was done, what was left unmarked, and why repeating it would be worse than
// stopping. The cockpit rendered none of it — `call` only shows a message when the HTTP call
// THROWS, and a deliberate refusal is a success at the transport layer.
//
// Live 2026-08-13, session #28: `run_query` committed the query by Enter, then read the tab list
// before the results page had landed, so it could not confirm its own effect and left
// `query_entered` unmarked — correctly, since that rung is CONSUMING and a second submit spends
// the query twice. The operator saw an unchanged "Step · Query run" button and nothing else: the
// surface silently invited the exact double-spend the backend had just refused to risk. A
// refusal nobody can read is indistinguishable from a stall, and the recovery it points at
// (`adopt_from_window`, which records only what the window PROVES) had no control anywhere.
function Refusal({ panel, busy, call }) {
  const last = panel.last_step;
  if (!last || last.ok !== false || !last.detail) return null;
  return (
    <div className="work__refusal">
      <p className="cv-blocked">
        <AppIcon name="alert" size={13} />{" "}
        <strong>Nothing was marked{last.action ? ` — ${last.action}` : ""}.</strong> {last.detail}
      </p>
      {/* THE REFUSAL'S OWN WAY OUT, rendered from the refusal rather than hand-built here.
          This one branch replaces the per-site work that never kept up: the backend now says
          which press resolves what it declined (`interaction.refusal`), so a refusal added
          tomorrow arrives with its button already attached. A refusal that legitimately has NO
          exit — a captcha, a credential — says WHO acts instead, which is the honest version of
          the same answer and is rendered as words rather than as a control we may not offer. */}
      {last.refusal?.exit && (
        <button className={`btn btn-sm${last.refusal.exit.consequential ? " btn-consequential" : ""}`}
                disabled={busy} aria-label={last.refusal.exit.label}
                title={last.refusal.exit.why || last.refusal.exit.label}
                onClick={() => call(last.refusal.exit.endpoint, last.refusal.exit.body || {})}>
          {last.refusal.exit.label}
        </button>
      )}
      {last.refusal && !last.refusal.exit && last.refusal.no_exit_because && (
        <p className="rung__meta">Nothing here can do this for you — {last.refusal.no_exit_because}.</p>
      )}
      {/* The window may already show the effect this step could not confirm in time. Adopting is
          a RECOVERY, not a re-run: it records only what the page proves, and refuses the rest. */}
      {panel.awaiting === "operator_verify" && (
        <button className="btn btn-sm" disabled={busy}
                aria-label="Adopt what the window proves"
                title="Reads the live window and marks only what it can prove — never re-runs the step."
                onClick={() => call("/adopt_from_window", {})}>
          Check the browser — adopt what it proves
        </button>
      )}
    </div>
  );
}

// WHAT THE BUTTON ACTUALLY DID — the working log, in the place the button was pressed.
//
// Operator, after a "Create Account" press that filled the form and stopped: "the manual should
// also have a log as well because it needs to show what it did, tried and what it was thinking so
// we get full context of even what our buttons on the ui are doing behind the scenes."
//
// The record already existed and had no surface. Every rung attempt is written to the step as a
// mini — rung, outcome, detail, when, and who turned the crank — and the details are already
// written in exactly that explanatory voice ("Could not check 'acknowledge' … so submitting now
// would bounce — nothing was submitted"). The Trace tab carries the full journal, but a tab away
// is the wrong distance from the button: the operator read a filled form with an untouched submit
// and could only conclude the Create Account button had not been found, when the log said plainly
// it was a checkbox one field earlier.
//
// Newest first, every attempt kept — the repeats ARE the story (§10: both sides of a correction),
// which is how "clicked the employee Apply → mismatch → clicked APPLY NOW" reads as one sentence.
// Opens by itself when the latest attempt did not succeed, because that is when it is needed.
const OUTCOME_TONE = { ok: "ready", failed: "danger", mismatch: "warn", blocked: "warn",
                       human_required: "warn", skipped: "muted", unknown: "muted" };

function ActionLog({ panel }) {
  const steps = panel.queue?.steps || [];
  const step = steps.find((s) => !s.done)
    || [...steps].reverse().find((s) => (s.terminal || "").startsWith("parked"))
    || null;
  const minis = [...(step?.minis || [])].reverse();
  if (!minis.length) return null;
  const worst = minis[0]?.outcome;
  return (
    <details className="action-log" open={worst !== undefined && worst !== "ok"}>
      <summary>
        <AppIcon name="listTree" size={12} /> What these buttons did — {minis.length} attempt
        {minis.length === 1 ? "" : "s"} on {step.title || step.job_id}
      </summary>
      <ol className="action-log__rows">
        {minis.map((m, i) => (
          <li key={`${m.rung}-${m.at || i}`} className="action-log__row">
            <span className={`badge badge--${OUTCOME_TONE[m.outcome] || "muted"}`}>
              {m.outcome || "—"}
            </span>
            <code className="action-log__rung">{m.rung}</code>
            <span className="action-log__detail">{m.detail || "—"}</span>
            <span className="action-log__meta">
              {m.at ? new Date(m.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }) : ""}
              {m.initiator ? ` · ${m.initiator}` : ""}
              {m.staged ? " · typed into the page" : ""}
            </span>
          </li>
        ))}
      </ol>
    </details>
  );
}

function ExecuteBody({ focus, panel, busy, call, decide }) {
  const last = panel.last_step;
  const account = focus.account || focus.handoff;

  return (
    <>
      {/* THE TEACHER'S PROPOSAL. Correct is a PEER of Go, never quieter: the golden training rows
          come from disagreement, and a surface whose easy path is always "yes" produces agreement
          and no signal. */}
      {focus.kind === "proposal" && (
        <ProposalBody proposal={focus.proposal} busy={busy} decide={decide} />
      )}

      {/* The credential card. The one place credentials appear, and the boundary is loud. */}
      {account && (
        <div className="work__section">
          <div className="work__section-head">
            <AppIcon name="shield" size={13} />
            {account.company} ({account.ats})
          </div>
          {focus.handoff?.remaining?.checked && (
            <p className="sc-account__remaining">
              {focus.handoff.remaining.operator.length > 0
                ? <><b>Needs you:</b> {focus.handoff.remaining.operator.join(" · ")}</>
                : focus.handoff.remaining.system.length > 0
                  ? <>Nothing needed from you — the account system fills the {focus.handoff.remaining.system.length} blank
                    field(s) from the stored credential.</>
                  : <>The form on screen is complete — nothing left but the button.</>}
            </p>
          )}
          {focus.handoff && (
            <dl className="sc-account__creds">
              <dt>Username</dt><dd><code>{focus.handoff.username || "—"}</code></dd>
              <dt>Password</dt>
              <dd>
                {focus.handoff.suggested_password
                  ? <code>{focus.handoff.suggested_password}</code>
                  : <span className="rung__meta">
                      {focus.handoff.suffix_configured ? "—"
                        : "no suggestion configured — choose your own that meets the site's rules"}
                    </span>}
              </dd>
            </dl>
          )}
        </div>
      )}

      {/* THE FORM ON THIS PAGE — the object being filled, in its own register.
          Once anything has been READ (the census, a fill plan, the accordion's sections) the full
          panel renders: the form as it stands with its per-field verbs, then the bunch-fill plan.
          Before a read there is one quiet way in, not a wall of standing buttons — but that way in
          is ALWAYS here on an execute focus. The 2026-08-10 audit's finding was precisely a rung
          refusing over unanswered fields while the surface offered no way to see or answer them:
          the capability lived behind `/apply_fill` and `/apply_teach` with no control anywhere. */}
      {(last?.form_scan || last?.fill_plan || last?.sections) ? (
        <div className="work__section">
          <div className="work__section-head"><AppIcon name="listTree" size={13} /> The form on this page</div>
          <FormCensus census={last?.form_scan} busy={busy} taught={last?.taught}
                      // Every verb is /apply_teach: validated against the intent vocabulary,
                      // journaled with the rationale, recorded on the apply step. Cockpit work
                      // IS corpus work — that is the whole point of routing it here.
                      onTeach={(intent, params, rationale) =>
                        call("/apply_teach", { intent, params, rationale })}
                      // The canonical résumé, resolved server-side by `assets.resume_path()` so a
                      // file field can be answered with a press instead of a hand-typed path.
                      resumePath={panel?.resume_path || ""}
                      // AUTO-FILL, OFFERED RATHER THAN DESCRIBED. `will_type` is what the executor
                      // will actually type — not `fillable`, which counts rows the bunch pass
                      // defers to their own widget protocol and would promise more than it does.
                      fillable={last?.fill_summary?.will_type || 0}
                      onAutofill={() => call("/apply_fill", { execute: true })}
                      onReread={() => call("/apply_fill", { execute: false })} />
          <FormSections sections={last?.sections} busy={busy}
                        ats={panel.account_state?.ats} accordionAts={panel.accordion_ats}
                        // No ats sent: the backend resolves the OPEN application's platform. The
                        // old hardcoded "successfactors" fallback read SAP's bars against an
                        // Indeed tab and rendered a phantom profile (2026-08-10).
                        onExpand={(what) => call("/apply_sections",
                          { ...(what ? { expand: what } : {}) })} />
          {last?.fill_plan && (
            <FillPlan plan={last.fill_plan} summary={last.fill_summary} busy={busy}
                      onPlan={() => call("/apply_fill", { execute: false })}
                      onFill={() => call("/apply_fill", { execute: true })} />
          )}
        </div>
      ) : (
        <div className="work__section">
          <button className="btn btn-sm btn-ghost" disabled={busy}
                  aria-label="Read this page's form"
                  title="Read the open form as it stands — shows every required field and what would be typed; types nothing"
                  onClick={() => call("/apply_fill", { execute: false })}>
            Read this page&apos;s form
          </button>
        </div>
      )}
    </>
  );
}

function ProposalBody({ proposal, busy, decide }) {
  const [correcting, setCorrecting] = useState(null);
  const [err, setErr] = useState("");

  const send = () => {
    if (!correcting?.intent) { setErr("a correction needs an intent"); return; }
    let params = {};
    try { params = correcting.params ? JSON.parse(correcting.params) : {}; }
    catch { setErr('params must be JSON, e.g. {"field": "Work authorization", "value": "Yes"}'); return; }
    if (correcting.rationale.trim().length < 12) {
      setErr("a correction needs a reason — that reasoning is the training signal"); return;
    }
    setErr("");
    decide({ action: "correct", intent: correcting.intent, params, rationale: correcting.rationale });
    setCorrecting(null);
  };

  return (
    <div className="work__section">
      <div className="work__section-head">
        <AppIcon name="sparkle" size={13} /> Teacher proposes <code>{proposal.intent}</code>
        {Object.keys(proposal.params || {}).length > 0 && (
          <span className="rung__meta">
            {Object.entries(proposal.params).map(([k, v]) => `${k}: ${v}`).join(" · ")}
          </span>
        )}
      </div>
      {proposal.note && <p className="cv-blocked">{proposal.note}</p>}
      {proposal.expected_next?.length > 0 && (
        <p className="rung__meta">expects → {proposal.expected_next.join(", ")}</p>
      )}
      {err && <div className="coaching-error">{err}</div>}

      {correcting ? (
        <div className="cv-correct">
          <input placeholder="intent instead (click / set_text / select_option …)"
                 value={correcting.intent}
                 onChange={(e) => setCorrecting((c) => ({ ...c, intent: e.target.value }))} />
          <input placeholder='params, e.g. {"field": "Work authorization", "value": "Yes"}'
                 value={correcting.params}
                 onChange={(e) => setCorrecting((c) => ({ ...c, params: e.target.value }))} />
          <textarea className="work-note" rows={2}
                    placeholder="Why the teacher was wrong — this reasoning is the training signal. Required."
                    value={correcting.rationale}
                    onChange={(e) => setCorrecting((c) => ({ ...c, rationale: e.target.value }))} />
          <div className="work__actions">
            <button className="btn btn-primary" disabled={busy} onClick={send}>Send correction</button>
            <button className="btn btn-sm" onClick={() => setCorrecting(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        // Go and Correct are the same size on purpose.
        <div className="work__actions">
          <button className="btn btn-primary" disabled={busy}
                  onClick={() => decide({ action: "go" })}>Go</button>
          <button className="btn btn-primary" disabled={busy}
                  onClick={() => setCorrecting({ intent: proposal.intent,
                    params: JSON.stringify(proposal.params || {}), rationale: "" })}>
            Correct
          </button>
          <button className="btn btn-sm btn-ghost" disabled={busy}
                  onClick={() => decide({ action: "skip" })}>Skip</button>
        </div>
      )}
    </div>
  );
}

const SETUP_KINDS = new Set(["declare", "clean_start", "login"]);
const EXECUTE_KINDS = new Set(["proposal", "account_handoff", "account", "application", "gate",
  "orient"]);

export function WorkSurface({
  panel, cockpit, viewMoment, onExitDetour, onNewSearch, busy, error, call, decide, onFlag,
  picks, armed, onPick, onClear, note, setNote, form, setForm,
}) {
  // THE ONE LEGITIMATE DETOUR: re-opening the current page's picker while its queue is being
  // worked — the STANDING select rung allows adding picks. Anything else the operator clicks in
  // the rail only changes what the inspector explains, never what this surface asks.
  const detour = viewMoment === "choose" && cockpit.focus.kind !== "choose"
    && (panel.results || []).length > 0;
  // THE SECOND DETOUR: declaring the next search. Unlike the picker it is available whatever the
  // moment — abandoning a query is exactly the thing you do when the current one is not working.
  const declaring = viewMoment === "declare";
  const focus = declaring ? newSearchFocus(panel)
    : detour ? chooseFocus(panel, picks) : cockpit.focus;

  return (
    <div className="cockpit__pane">
      <div className="work">
        {declaring && (
          <div className="work__detour">
            <AppIcon name="eye" size={13} />
            Declaring the <strong>next search</strong> in this session — the browser stays open and
            you stay signed in. Only the query changes.
            <button className="btn btn-sm" onClick={onExitDetour}>Keep the current search</button>
          </div>
        )}
        {detour && (
          <div className="work__detour">
            <AppIcon name="eye" size={13} />
            You re-opened <strong>{focus.groupLabel}&apos;s picker</strong> — picks add to this
            page&apos;s queue. The session&apos;s work is
            {" "}<strong>{cockpit.focus.title}</strong>.
            <button className="btn btn-sm" onClick={onExitDetour}>Back to the work</button>
          </div>
        )}

        <div className="work__eyebrow">
          <AppIcon name="play" size={12} /> {focus.groupLabel}
          {cockpit.blocker && !detour && <span className="badge badge--warn">needs you</span>}
        </div>

        <h2 className="work__title">
          {focus.title}
          {focus.parked && (
            <span className="badge badge--warn work__parked"
                  title="This application parked mid-flight and is waiting on you — it is not closed.">
              {String(focus.parked).replace("parked:", "parked · for ")}
            </span>
          )}
        </h2>
        {focus.subtitle && <p className="work__subtitle">{focus.subtitle}</p>}
        {/* The walk BEFORE the reasoning: "where am I in this application" is the question the
            operator asks first, and it was the one the surface could not answer at all. */}
        {/* `focus.applied`, not `panel.applied_check`: the focus has already scoped the verdict to
            the step being worked, the same way it scopes the proposal and the account handoff. The
            raw panel field survives on the blackboard until the next landing overwrites it. */}
        <FlowStrip flow={focus.flow} applied={focus.applied} />
        {focus.why && <p className="work__why">{focus.why}</p>}

        {error && <div className="coaching-error">{error}</div>}

        {/* A refusal is not an error and not a success — it is the system declining to claim
            something it could not confirm, and the operator is the only one who can settle it. */}
        <Refusal panel={panel} busy={busy} call={call} />

        {/* What is about to leave, stated before the button that sends it. */}
        {focus.kind === "gate" && <GateBody focus={focus} />}

        {/* Lost: the witnesses' scored reads, before the button that looks again. */}
        {focus.kind === "orient" && <WhereAmI whereabouts={focus.whereabouts} />}
        {/* The teaching control rides UNRECOGNISED GROUND, not just the orient moment — the
            observer can blink (a poll with no watched tab) while the screen stays one nobody has
            named, and the human's knowledge is the same either way (2026-08-10 audit: labeling
            lived two tabs away in the Trace, farthest from where the knowledge strikes). */}
        {(focus.kind === "orient" || (focus.flow && !focus.flow.recognised)) && (
          <NameThisPage sessionId={panel.session_id} whereabouts={focus.whereabouts} />
        )}

        <Say focus={focus} />
        {focus.kind !== "proposal" && (
          <Actions focus={focus} busy={busy} call={call} onNewSearch={onNewSearch} />
        )}

        {SETUP_KINDS.has(focus.kind) && (
          <SetupBody focus={focus} panel={panel} form={form} setForm={setForm}
                     busy={busy} call={call} />
        )}
        {focus.kind === "choose" && (
          <DecideBody panel={panel} picks={picks} armed={armed} onPick={onPick} onClear={onClear}
                      note={note} setNote={setNote} busy={busy} />
        )}
        {EXECUTE_KINDS.has(focus.kind) && (
          <ExecuteBody focus={focus} panel={panel} busy={busy} call={call} decide={decide} />
        )}
        {focus.kind === "walked_out" && <VerifyBody panel={panel} />}

        {/* The working log, under the controls that wrote it. */}
        <ActionLog panel={panel} />

        {EXECUTE_KINDS.has(focus.kind) && <More focus={focus} busy={busy} onFlag={onFlag} />}
      </div>
    </div>
  );
}

function VerifyBody({ panel }) {
  const done = (panel.queue?.steps || []).filter((s) => s.done);
  if (!done.length) return null;
  return (
    <div className="work__section">
      <div className="work__section-head"><AppIcon name="checkCircle" size={13} /> Accounted for</div>
      <ul className="rungs">
        {done.map((s) => (
          <li key={s.job_id} className={`rung rung--${s.terminal === "submitted" ? "held" : "pending"}`}>
            <div className="rung__body">
              <div className="rung__line">
                <span className={`badge badge--${s.terminal === "submitted" ? "ready" : "muted"}`}>
                  {s.terminal}
                </span>
                <span className="rung__label">{s.title || s.job_id}</span>
                {s.company && <span className="rung__meta">{s.company}</span>}
              </div>
              {s.terminal_detail && <div className="rung__meta">{s.terminal_detail}</div>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
