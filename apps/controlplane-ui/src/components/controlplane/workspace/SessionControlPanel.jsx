import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, postJSON, fmtTime } from "./api";
import { AppIcon } from "../../../ui/Icon";
import { PickOrb } from "./OrderedPicks";
import { useOrderedPicks } from "./useOrderedPicks";

// The Session Control Panel — the one place the local side turns the loop.
//
// The shape is a LADDER, not a task with an end flag. Four fixed rungs get the session to the
// start line, then it grows one rung per results page. There is deliberately no "Done" button:
// the ladder ends when the world says it does (no next page), and even then closing out is the
// operator's call.
//
// Load-bearing:
//   * The QUERY is a setup input, shown as spent once it is held. A session runs ONE query —
//     re-searching is what makes Indeed collapse results — so once spent, the field locks and
//     the server refuses a different one.
//   * A CONSUMING rung that has lapsed shows "recover", never "retry". If the panel ever offers
//     to re-run the query, the whole design has been lost.
//   * Step is unattended while climbing and STOPS at the start line. Everything past that point
//     waits for a human pick, because past that point the actions are consequential.

//: How often the panel re-reads the session. Every call is a local CDP socket (tabs + auth state),
//: so this costs no bandwidth — the number is about how quickly the operator should see the world
//: change, not about load.
const PING_MS = 5000;

const RUNG_TONE = { held: "ready", next: "accent", pending: "muted", regressed: "warn", lapsed: "warn" };
const RUNG_MARK = { held: "check", next: "play", pending: "circle", regressed: "refresh", lapsed: "alert" };

// What the operator is being asked for, in their words rather than the API's.
const AWAITING_COPY = {
  operator_login: "Couldn't sign in with a saved login. Pick a way in below, or sign in directly in the window.",
  operator_2fa: "A verification code is being asked for. That one is yours — enter it in the window, then step again.",
  operator_open_engine: "No tab for this site is open and one couldn't be opened. Open its home page in the window, then step again.",
  operator_challenge: "A challenge is up. Clear it yourself in the window — we never auto-solve.",
  operator_browser: "The session's Chrome isn't answering. Start it, then step again.",
  operator_clean_start: "This window still holds tabs from a previous session. Clear them before we begin.",
  operator_search_box: "Couldn't find Indeed's search box. Open the job search, then step again.",
  operator_verify: "The search was submitted but not confirmed. Check the window before stepping.",
  operator_filter: "The distance filter wouldn't set. We don't gather below the radius floor.",
  operator_results: "Couldn't read this page's results. Check the window, then step again.",
  recover: "Get back to the results we already have — do not search again.",
  choose: "Pick what to act on from this page.",
  operator_end: "This query is walked out. Closing the session is your call.",
};

// The ways an application can END other than being sent. `submitted` is deliberately not in this
// list — it is the primary button, because it is the only one that means success and the only one
// that claims a real application went out.
//
// Parked and abandoned stay visibly distinct: "not now" versus "not ever". Collapsing them either
// resurrects dead requisitions forever or quietly drops applications you meant to come back to.
const TERMINAL_CHOICES = [
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
    why: "The requisition outlived the Indeed listing. Not ever, rather than not now." },
  { flag: "abandoned:operator", label: "Not a fit",
    why: "You looked and do not want it. Closed for good." },
];

// What the crank just did, in words. The raw action ids are dispatch keys, not labels.
const ACTION_COPY = {
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

// --- staleness: how old is what we are looking at ------------------------------------------
// PROTOTYPE (perception/staleness.py). ADVISORY: it reports, the operator decides — nothing in
// the panel acts on it, because the thresholds behind the level are still guesses.
//
// The level and the remedy are shown as SEPARATE things on purpose. They came apart on the first
// live test: a session left 14.5 hours was red on age while still signed in and answering with
// 210 controls, so the honest reading is "very suspect, and a reload fixes it" — not "red,
// panic". Collapsing them is what made the detector propose destroying a working session.
const STALE_TONE = { fresh: "ready", yellow: "warn", orange: "warn", red: "danger" };
const STALE_VERDICT_COPY = {
  continue: "operable — carry on",
  refresh: "reload this page before acting",
  renew: "reloading will not fix this — needs a fresh state",
  handoff: "cannot see the page well enough to judge",
};

// Signals whose name ends in `_s` carry SECONDS; the rest carry a flag. Formatting everything as
// an age rendered `responsive: 1s`, which reads as "answered a second ago" and means "yes".
function _signalValue(name, value) {
  if (value === null || value === undefined) return "not measured";
  if (!String(name).endsWith("_s")) return value ? "yes" : "no";
  if (value < 90) return `${Math.round(value)}s`;
  if (value < 5400) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

function StalenessChip({ stale }) {
  if (!stale) return null;
  const tone = STALE_TONE[stale.level] || "muted";
  // The hover carries the EVIDENCE, not a restatement of the verdict — the raw ages are what
  // will eventually calibrate the thresholds, so they should be visible to the person who can
  // tell us the level was wrong.
  const detail = [
    stale.why,
    "",
    ...(stale.signals || []).map((s) => `${s.name}: ${_signalValue(s.name, s.value)} · ${s.level}`),
    ...(stale.unmeasured?.length ? ["", `not measured: ${stale.unmeasured.join(", ")}`] : []),
    "",
    `rules: ${stale.rules_version} (provisional — thresholds not yet measured)`,
  ].join("\n");

  return (
    <span className={`badge badge--${tone} sc-stale`} title={detail}>
      <AppIcon name={stale.level === "fresh" ? "check" : "alert"} size={12} />
      <span>{stale.level === "fresh" ? "fresh" : `${stale.level} · stale`}</span>
      {stale.verdict !== "continue" && (
        <em className="sc-stale__verdict">{STALE_VERDICT_COPY[stale.verdict] || stale.verdict}</em>
      )}
    </span>
  );
}

function Rung({ rung }) {
  // "spent" means the cost was actually PAID — i.e. the rung is reached. A consuming rung that
  // is merely next has not been spent, and labelling it so would misreport what this session
  // has already cost.
  const spent = rung.kind === "consuming" && !!rung.reached;
  return (
    <li className={`rung rung--${rung.status}`}>
      <span className={`rung__mark badge badge--${RUNG_TONE[rung.status] || "muted"}`}>
        <AppIcon name={RUNG_MARK[rung.status] || "circle"} size={13} />
      </span>
      <div className="rung__body">
        <div className="rung__line">
          <span className="rung__label">{rung.label}</span>
          {rung.kind === "consuming" && (
            <span className="badge badge--muted" title={rung.why}>
              {spent ? "spent" : "once only"}
            </span>
          )}
          {rung.status === "lapsed" && <span className="badge badge--warn">recover, don't repeat</span>}
          {rung.status === "regressed" && <span className="badge badge--warn">lapsed — safe to re-run</span>}
        </div>
        {rung.reached && (
          <div className="rung__meta">
            {fmtTime(rung.reached.at)} · {rung.reached.initiator}
            {rung.reached.evidence ? ` · ${rung.reached.evidence}` : ""}
          </div>
        )}
        {rung.status === "next" && <div className="rung__meta">next</div>}
      </div>
    </li>
  );
}

export function SessionControlPanel({ domain }) {
  const [sessionId, setSessionId] = useState(null);
  const [panel, setPanel] = useState(null);
  const [form, setForm] = useState({ query: "", location: "", radius_miles: 50 });
  // Picks carry an ORDER, not just a tick — that order is the order the applications run.
  // Scoped to (session, page): the draft survives clicking out of the picker, and turning the
  // page starts a fresh one rather than inheriting the last page's choices.
  const [pickerOpen, setPickerOpen] = useState(null);   // null = follow the default
  const { picks, armed, pick, clear: clearPicks, retain: retainPicks } =
    useOrderedPicks(sessionId ? `${sessionId}.${panel?.page ?? 1}` : null);
  const [note, setNote] = useState("");
  const [correcting, setCorrecting] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // A ref, not the state, because the interval closes over its first render.
  const busyRef = useRef(false);
  busyRef.current = busy;

  // Find this domain's live session. Everything below hangs off one session id.
  useEffect(() => {
    getJSON("/api/sessions")
      .then((d) => {
        const s = (d.sessions || []).find((x) => x.domain_id === domain.id && x.live)
          || (d.sessions || []).find((x) => x.domain_id === domain.id);
        setSessionId(s?.id ?? null);
      })
      .catch(() => setSessionId(null));
  }, [domain.id]);

  // The panel PINGS the session rather than only reading it when you press something. The world
  // moves underneath us — you sign in by hand, a challenge appears, a tab closes — and a cockpit
  // that only refreshes on click shows a past that has stopped being true. The read model is all
  // local CDP sockets, so this is free even in low-data mode.
  const load = useCallback(() => {
    if (!sessionId) return;
    getJSON(`/api/session_control/${sessionId}`)
      .then((d) => {
        // A poll carries no last_step; keep the one the operator is still reading rather than
        // blanking the result of the action they just took.
        setPanel((prev) => ({ ...d, last_step: d.last_step ?? prev?.last_step ?? null }));
        // Sync the form ONCE, when a declared query first arrives. Re-syncing on every ping would
        // overwrite what the operator is mid-way through typing.
        setForm((f) => (d.query && !f.query
          ? { query: d.query, location: d.location || "", radius_miles: d.radius_miles || 50 }
          : f));
        setError("");
      })
      .catch((e) => setError(e.message || "could not read the session"));
  }, [sessionId]);

  useEffect(() => {
    load();
    // Paused while an action is in flight: a ping landing mid-step would render a half-applied
    // world, and the action's own response is fresher than anything a poll could fetch.
    const t = setInterval(() => { if (!busyRef.current) load(); }, PING_MS);
    return () => clearInterval(t);
  }, [load]);

  // Drop picks for jobs that are no longer on the page. The results list is re-read every ping,
  // and a pick for a vanished job would ride into /choose and come back 422 ("Not on the page
  // under review") — an error the operator did nothing to cause, on a control that gives no sign
  // it has gone stale. Sits up here with the other hooks, ABOVE the no-session early return:
  // below it, the hook only runs on some renders, which is a Rules-of-Hooks violation React
  // catches at runtime rather than a subtle bug (met while building this, 2026-07-25).
  useEffect(() => {
    retainPicks((panel?.results || []).map((r) => r.job_id));
  }, [panel, retainPicks]);

  // A new page is a new decision: drop any collapse the operator set on the last one.
  const pageSeen = useRef(panel?.page);
  useEffect(() => {
    if (pageSeen.current !== panel?.page) { pageSeen.current = panel?.page; setPickerOpen(null); }
  }, [panel?.page]);

  const call = async (path, body) => {
    setBusy(true);
    setError("");
    try {
      const d = await postJSON(`/api/session_control/${sessionId}${path}`, body);
      setPanel(d);
      return d;
    } catch (e) {
      setError(e.message || "the call failed");
      return null;
    } finally {
      setBusy(false);
    }
  };

  if (!sessionId) {
    return (
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title layer__title--with-icon">
            <AppIcon name="sliders" size={17} /> Session control
          </div>
        </div>
        <p className="empty-hint">
          No {domain.label} session yet. Start one from Sessions, then come back — a session is one
          focused browser working one query.
        </p>
      </div>
    );
  }

  const p = panel || {};
  const spent = (p.ladder || []).some((r) => r.id === "query_entered" && r.status !== "pending");
  const atLine = p.progress?.at_start_line;
  const results = p.results || [];
  const last = p.last_step;
  const awaiting = p.awaiting;


  // WHEN THE PICKER SHOULD BE OPEN. It is a tool for one moment — deciding this page — and
  // afterwards it is a fifteen-row table sitting on top of the work it created. Operator,
  // 2026-07-26: "after its initial use the menu stays at the top getting in the way of what we
  // need to see."
  //
  // Default, not a rule: open while the choice is still to be made, collapsed once the page's
  // selection rung is held. `pickerOpen` overrides it either way, so the operator can always
  // reopen and add to the page's picks — which the STANDING select rung allows by design.
  const selectionMade = (p.ladder || []).some((r) => r.id?.startsWith("select:") && r.status === "held");
  // An UNSUBMITTED draft keeps it open, whatever the selection rung says. A closed picker sitting
  // on picks nobody has taken is the "click out and lose them" anxiety with a lid on it — the
  // draft survives now, but hiding a pending decision is still the wrong default.
  const pickerDefaultOpen = results.length > 0 && (!selectionMade || picks.length > 0);
  const showPicker = pickerOpen === null ? pickerDefaultOpen : pickerOpen;

  const queue = p.queue?.steps || [];
  const qs = p.queue_summary || { total: 0, done: 0, submitted: 0, blocks_page: false };
  const currentStep = queue.find((s) => !s.done) || null;

  const proposal = p.proposal || null;
  const accountHandoff = p.account_handoff || null;

  const flagStep = (jobId, flag, detail = "") =>
    call("/apply_flag", { job_id: jobId, flag, detail, initiator: "operator" });

  const decide = async (body) => {
    const d = await call("/apply_decide", body);
    if (d) setCorrecting(null);
  };

  const sendCorrection = () => {
    if (!correcting?.intent) { setError("a correction needs an intent"); return; }
    let params = {};
    try {
      params = correcting.params ? JSON.parse(correcting.params) : {};
    } catch {
      setError('params must be JSON, e.g. {"field": "Work authorization", "value": "Yes"}');
      return;
    }
    if (correcting.rationale.trim().length < 12) {
      setError("a correction needs a reason — that reasoning is the training signal");
      return;
    }
    decide({ action: "correct", intent: correcting.intent, params, rationale: correcting.rationale });
  };

  // `picks` goes over the wire IN ORDER — /choose enqueues in the order it receives, and the
  // queue is sequential, so element 0 is the application that runs first.
  const doChoose = async (advance) => {
    const d = await call("/choose", { picks, note, advance, initiator: "operator" });
    if (d) { clearPicks(); setNote(""); }
  };

  return (
    <div className="live">
      {/* --- 1. what this session is FOR ------------------------------------------------ */}
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title layer__title--with-icon">
            <AppIcon name="sliders" size={17} /> Session control
          </div>
          <span className="layer__sub">
            {atLine ? "At the start line — stop and go" : "Climbing to the start line"}
            <span className="sc-live" title={`Re-reading the session every ${PING_MS / 1000}s`}>
              <span className="sc-live__dot" /> live
            </span>
          </span>
        </div>

        <div className="sc-setup">
          <label className="sc-field">
            <span>Query</span>
            <input
              value={form.query}
              disabled={spent || busy}
              placeholder="reporting analyst"
              onChange={(e) => setForm((f) => ({ ...f, query: e.target.value }))}
            />
          </label>
          <label className="sc-field">
            <span>Location</span>
            <input
              value={form.location}
              disabled={spent || busy}
              placeholder="Nashua, NH"
              onChange={(e) => setForm((f) => ({ ...f, location: e.target.value }))}
            />
          </label>
          <label className="sc-field sc-field--narrow">
            <span>Radius</span>
            <input
              type="number"
              min={50}
              value={form.radius_miles}
              disabled={spent || busy}
              onChange={(e) => setForm((f) => ({ ...f, radius_miles: Number(e.target.value) }))}
            />
          </label>
          <button
            className="btn btn-sm"
            disabled={busy || spent || !form.query.trim()}
            onClick={() => call("/initialize", { ...form, initiator: "operator" })}
          >
            {busy ? "…" : "Initialize"}
          </button>
        </div>

        {spent && (
          <p className="mode-hint">
            This session has spent its query on <strong>{p.query}</strong>. It will not search
            again — re-running a query is what makes Indeed collapse results. A different query
            means a new session.
          </p>
        )}
      </div>

      <div className="live-split">
        {/* --- 2. the ladder --------------------------------------------------------- */}
        <div className="live-queue layer">
          <div className="layer__head">
            <div className="layer__title layer__title--with-icon">
              <AppIcon name="waypoints" size={17} /> Checkpoints
            </div>
            <span className="layer__count">
              {p.progress ? `${p.progress.preamble_held}/${p.progress.preamble_total}` : ""}
            </span>
          </div>
          <ul className="rungs">
            {(p.ladder || []).map((r) => <Rung key={r.id} rung={r} />)}
          </ul>
          <p className="mode-hint">
            No end flag: the ladder grows a rung per page. It stops when there is no next page.
          </p>
        </div>

        {/* --- 3. the crank + the page ------------------------------------------------ */}
        <div className="live-detail layer">
          <div className="live-detail__head">
            <span className="live-detail__state">
              {p.query ? `"${p.query}"` : "no query yet"}
              {p.location ? ` · ${p.location}` : ""}
            </span>
            <span className="badge badge--muted">page {p.page ?? 1}</span>
            {p.tab_count ? <span className="badge badge--muted">{p.tab_count} tabs</span> : null}
            <StalenessChip stale={p.staleness} />
          </div>

          <p className="live-detail__why">
            {p.next?.kind === "recover"
              ? p.next.recovery
              : p.next?.reason || "Initialize the session to give it something to be for."}
          </p>

          <div className="sc-crank">
            <button
              className="btn btn-sm btn-primary"
              disabled={busy || !p.query}
              onClick={() => call("/step", { initiator: "operator" })}
              title={p.next?.label ? `Next: ${p.next.label}` : "Turn the crank once"}
            >
              {busy ? "…" : atLine ? "Read this page" : `Step · ${p.next?.label || ""}`}
            </button>
            {last && (
              <span className={`badge badge--${last.ok ? "ready" : "warn"}`}>
                {ACTION_COPY[last.action] || last.action}
              </span>
            )}
            {/* The pace it actually ran at. Without this, "that felt too quick" is unfalsifiable. */}
            {last?.pace && (
              <span className="badge badge--muted" title={last.pace.why}>
                {last.pace.style} pace
              </span>
            )}
          </div>

          {error && <div className="coaching-error">{error}</div>}
          {last?.detail && <p className="mode-hint">{last.detail}</p>}
          {awaiting && AWAITING_COPY[awaiting] && (
            <p className="sc-awaiting">
              <AppIcon name="alert" size={14} /> {AWAITING_COPY[awaiting]}
            </p>
          )}

          {/* Login: what the system can SEE and DO from here. The clicks toward a login screen are
              ours; the credential itself never is. An empty option list is a real answer and says
              which — "this screen needs you" vs "I cannot see a way in on this page". */}
          {last?.login && (
            <div className="sc-login">
              <div className="sc-login__head">
                <AppIcon name="key" size={14} /> Signing in
                <span className="badge badge--muted">{last.login.state}</span>
                {last.login.seen ? (
                  <span className="rung__meta">{last.login.seen} elements seen</span>
                ) : null}
              </div>
              {last.login.options?.length > 0 ? (
                <>
                  <div className="rung__meta">I can click any of these for you:</div>
                  <div className="sc-login__opts">
                    {last.login.options.map((o) => (
                      <button
                        key={o.name}
                        className="btn btn-sm"
                        disabled={busy}
                        title={o.why}
                        onClick={() => call("/login_action", {
                          control_name: o.name, role: o.role, initiator: "operator",
                        })}
                      >
                        {o.name}
                      </button>
                    ))}
                  </div>
                  <p className="cv-blocked">
                    You take over at the password or code — we never type either.
                  </p>
                </>
              ) : (
                <p className="cv-blocked">{last.login.detail}</p>
              )}
              <div className="cv-actions" style={{ marginTop: 8 }}>
                <button className="btn btn-sm btn-primary" disabled={busy}
                        onClick={() => call("/step", { initiator: "operator" })}>
                  {busy ? "…" : "I've signed in — re-check"}
                </button>
              </div>
            </div>
          )}

          {/* The inherited window. Shown in full and never cleared silently — a persistent
              profile's restored tabs can include somebody's half-finished application. */}
          {last?.fresh_start?.to_close?.length > 0 && (
            <div className="sc-inherited">
              <div className="sc-inherited__head">
                Inherited from a previous session — {last.fresh_start.to_close.length} tab(s) to close
              </div>
              <ul className="sc-inherited__list">
                {last.fresh_start.to_close.map((t) => (
                  <li key={t.tab_id} className={t.role === "apply" || t.role === "errand" ? "is-work" : ""}>
                    <span className="badge badge--muted">{t.role}</span> {t.url}
                    {(t.role === "apply" || t.role === "errand") && <em> — may hold real work</em>}
                  </li>
                ))}
              </ul>
              {last.fresh_start.keeper && (
                <div className="rung__meta">
                  Keeping <code>{last.fresh_start.keeper.url}</code> to land on.
                </div>
              )}
              <div className="cv-actions" style={{ marginTop: 10 }}>
                <button
                  className="btn btn-sm btn-primary"
                  disabled={busy}
                  onClick={() => call("/clean_start", {
                    initiator: "operator",
                    confirm_discards_work: last.fresh_start.holds_work.length > 0,
                  })}
                >
                  {busy ? "…" : last.fresh_start.holds_work.length > 0
                    ? `Clean start — discard ${last.fresh_start.holds_work.length} in-progress`
                    : "Clean start"}
                </button>
              </div>
            </div>
          )}

          {/* --- THE CHOICE, and it goes FIRST -------------------------------------
              This used to sit at the very bottom, under the apply queue. At the start line the
              choice IS the work — everything below is the consequence of it — so the operator
              had to scroll past the outcome to reach the decision. Operator, 2026-07-25:
              "selecting jobs is top priority when we get to this point." */}
          <div className="live-detail__body">
            {results.length === 0 ? (
              <div className="empty-hint">
                {atLine
                  ? "Step to read this page's results."
                  : "Results appear once the session reaches the start line."}
              </div>
            ) : (
              <>
                <div className="sc-picks__head">
                  {/* THE HEADER IS THE TOGGLE. One obvious, always-present place to open and
                      close the picker, rather than a control that appears somewhere else — and
                      it keeps saying how many are picked while collapsed, so a closed picker
                      never hides a pending decision. */}
                  <button type="button" className="sc-picks__toggle"
                          aria-expanded={showPicker}
                          onClick={() => setPickerOpen(!showPicker)}
                          title={showPicker ? "Hide the picker" : "Open the picker"}>
                    <AppIcon name={showPicker ? "chevronDown" : "chevronRight"} size={14} />
                    <span className="sc-picks__title">
                      Pick in the order you want them applied
                    </span>
                    <span className="badge badge--muted">{results.length} on this page</span>
                    {picks.length > 0 && (
                      <span className="badge badge--accent">{picks.length} picked</span>
                    )}
                  </button>
                  {showPicker && <span className="sc-picks__hint">
                    {armed
                      ? "Now click another number to swap the two — or click the same one again to remove it."
                      : picks.length
                        ? `${picks.length} picked · click a number to pick it up and swap`
                        : "Click a circle to make it #1. They run in this order."}
                  </span>}
                  {/* PICKS ARE NOT SAVED UNTIL TAKEN. They live in this component until the
                      button below posts them, so a reload loses them — which is exactly what
                      happened: six selected, nothing on the server, and no sign of it. */}
                  {picks.length > 0 && (
                    <span className="sc-picks__unsaved" title="Nothing is recorded until you press Take">
                      <AppIcon name="alert" size={12} /> not saved yet
                    </span>
                  )}
                  {showPicker && picks.length > 0 && (
                    <button type="button" className="btn btn-sm" disabled={busy}
                            onClick={clearPicks} title="Clear every pick and its number">
                      Clear
                    </button>
                  )}
                </div>
                {showPicker && (<><table className="sc-results">
                  <thead>
                    <tr><th className="sc-results__ord">#</th><th>Role</th><th>Company</th><th>Where</th><th>Pay</th></tr>
                  </thead>
                  <tbody>
                    {results.map((r) => (
                      <tr key={r.job_id} className={picks.includes(r.job_id) ? "is-picked" : ""}>
                        <td className="sc-results__ord">
                          <PickOrb jobId={r.job_id} label={r.title}
                                   picks={picks} armed={armed} onPick={pick} />
                        </td>
                        <td>{r.title}</td>
                        <td>{r.company}</td>
                        <td>{r.location}</td>
                        <td>{r.salary || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <div className="live-detail__foot">
                  <textarea
                    className="cv-note-in"
                    rows={2}
                    placeholder="Why these (or why none) — rides into the page's record."
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <div className="cv-actions">
                    {/* Not disabled at 0 picks: "nothing on this page" is a real answer, and the
                        page still counts as reviewed. Taking that away would strand a page of
                        nothing-for-me behind a button that refuses to move. */}
                    <button className="btn btn-sm btn-primary" disabled={busy}
                            onClick={() => doChoose(true)}>
                      {busy ? "…" : picks.length
                        ? `Take ${picks.length} · next page`
                        : "Nothing here · next page"}
                    </button>
                    <button className="btn btn-sm" disabled={busy} onClick={() => doChoose(false)}>
                      Take {picks.length} · stay
                    </button>
                  </div>
                  <p className="cv-blocked">
                    Picking a job is approval to enter its application. Nothing is submitted without
                    a separate confirmation.
                  </p>
                </div></>)}
              </>
            )}
          </div>

          {/* --- the apply queue: N picks, N steps, the page waits ------------------ */}
          {queue.length > 0 && (
            <div className="sc-queue">
              <div className="sc-queue__head">
                <AppIcon name="listTree" size={14} />
                Applications from page {p.page}
                <span className="badge badge--muted">
                  {qs.done}/{qs.total} done · {qs.submitted} submitted
                </span>
              </div>
              <p className="rung__meta">
                {qs.blocks_page
                  ? "This page stays open until every one reaches a terminal flag — nothing is skipped."
                  : "Every application is accounted for. Choose again to advance the page."}
              </p>

              <ol className="sc-steps">
                {queue.map((s) => {
                  const isCurrent = !s.done && s.job_id === currentStep?.job_id;
                  return (
                    <li key={s.job_id}
                        className={`sc-step ${s.done ? "is-done" : ""} ${isCurrent ? "is-current" : ""}`}>
                      <div className="sc-step__line">
                        <span className={`badge badge--${s.done
                          ? (s.terminal === "submitted" ? "ready" : "muted")
                          : (isCurrent ? "accent" : "muted")}`}>
                          {s.done ? s.terminal : isCurrent ? "now" : "queued"}
                        </span>
                        <span className="sc-step__title">{s.title || s.job_id}</span>
                        {s.company && <span className="rung__meta">{s.company}</span>}
                        {s.platform && <span className="badge badge--muted">{s.platform}</span>}
                        {/* WHERE we landed, not just whose software it is. A re-orientation
                            after leaving Indeed is normal, and the state is what makes it
                            reportable rather than a shrug. */}
                        {s.landing_state && (
                          <span className="badge badge--accent" title="Where this application landed — platform + page kind, read from the page content">
                            {s.landing_state.replace(/_/g, " ")}
                          </span>
                        )}
                      </div>
                      {s.terminal_detail && <div className="rung__meta">{s.terminal_detail}</div>}

                      {/* The mini-step trail. Every flag shown, because an unrecognised screen
                          must be visible rather than inferred from a gap. */}
                      {s.minis?.length > 0 && (
                        <div className="sc-minis">
                          {s.minis.map((m, i) => (
                            <span key={i} className={`sc-mini sc-mini--${m.outcome}`} title={m.detail}>
                              {m.rung} <em>{m.outcome}</em>
                            </span>
                          ))}
                        </div>
                      )}

                      {isCurrent && (
                        <>
                          <div className="rung__meta" style={{ marginTop: 6 }}>
                            {s.next_rung
                              ? `Next: ${s.next_rung.replace(/_/g, " ")}`
                              : "Past the known prefix — the rest depends on where we landed."}
                          </div>
                          {s.needs_operator && (
                            <p className="sc-awaiting" style={{ marginTop: 6 }}>
                              <AppIcon name="alert" size={14} />
                              Stopped on something only you can resolve.
                            </p>
                          )}
                          {/* ACCOUNT HANDOFF. The one place credentials appear — and the boundary
                              is loud: the system prepares the account and hands YOU the details;
                              you type them and click Create Account. The agent never does. */}
                          {accountHandoff && accountHandoff.job_id === s.job_id && !s.done && (
                            <div className="sc-account">
                              <div className="sc-account__head">
                                <AppIcon name="key" size={14} />
                                {accountHandoff.button} — {accountHandoff.company} ({accountHandoff.ats})
                              </div>
                              <p className="sc-account__boundary">
                                <AppIcon name="shield" size={13} /> Your account, your call. The
                                system can create it for you — a captcha or an email verification
                                code still stops for you, and the honeypot is never touched.
                              </p>
                              {/* WHAT "AUTOMATICALLY" MEANS, before you press it. The steps come
                                  from the same table the driver executes, so this cannot describe
                                  a drive that doesn't happen — and choosing between doing it
                                  yourself and delegating it isn't a real choice while the second
                                  option is an unlabelled button. */}
                              {Array.isArray(accountHandoff.plan) && accountHandoff.plan.length > 0 && (
                                <details className="sc-account__plan">
                                  <summary>
                                    {accountHandoff.plan.length} steps if the system does it
                                    {accountHandoff.policy_checked && " · password checked against the site's rules first"}
                                  </summary>
                                  <ol>
                                    {accountHandoff.plan.map((st, i) => (
                                      <li key={i}>
                                        <code>{st.intent}</code> {st.params?.field}
                                        {/* The value's SOURCE, never the value. A credential ref
                                            reads account.* and resolves from the vault. */}
                                        {st.params?.value_ref && (
                                          <span className="rung__meta"> ← {st.params.value_ref}</span>
                                        )}
                                        {st.intent === "check_group" && (
                                          <span className="rung__meta"> (switched OFF — it arrives checked)</span>
                                        )}
                                      </li>
                                    ))}
                                  </ol>
                                </details>
                              )}
                              <dl className="sc-account__creds">
                                <dt>Username</dt>
                                <dd><code>{accountHandoff.username || "—"}</code></dd>
                                <dt>Password</dt>
                                <dd>
                                  {accountHandoff.suggested_password
                                    ? <code>{accountHandoff.suggested_password}</code>
                                    : <span className="rung__meta">
                                        {accountHandoff.suffix_configured
                                          ? "—"
                                          : "no suggestion configured — choose your own that meets the site's rules"}
                                      </span>}
                                </dd>
                              </dl>
                              <div className="cv-actions">
                                <button className="btn btn-sm btn-primary" disabled={busy}
                                        title="The system fills the form with these credentials and clicks Create Account. A captcha or email code still stops for you."
                                        onClick={() => call("/apply_account", { mode: "auto", initiator: "operator" })}>
                                  Create it automatically
                                </button>
                                <button className="btn btn-sm" disabled={busy}
                                        title="Fill the form but leave the Create Account click to you"
                                        onClick={() => call("/apply_account", { mode: "fill", initiator: "operator" })}>
                                  Fill, I'll submit
                                </button>
                                <button className="btn btn-sm" disabled={busy}
                                        title="You typed it yourself — mark done and continue"
                                        onClick={() => call("/apply_account", { mark_created: true, initiator: "operator" })}>
                                  I created it
                                </button>
                                <button className="btn btn-sm btn-ghost" disabled={busy}
                                        onClick={() => flagStep(s.job_id, "parked:account_wall", "operator chose not to create the account")}>
                                  Park it
                                </button>
                              </div>
                            </div>
                          )}

                          {/* THE TEACHER'S PROPOSAL. Teacher runs pause anyway, so the surface
                              here is not another row of buttons but what it intends and why —
                              sitting where you can read it and steer. Correct is a PEER of Go,
                              never quieter: the golden training rows come from disagreement, and
                              a surface whose easy path is always "yes" produces agreement and no
                              signal. */}
                          {proposal && proposal.job_id === s.job_id && (
                            <div className="sc-proposal">
                              <div className="sc-proposal__head">
                                <AppIcon name="sparkle" size={14} /> Teacher proposes
                                <code>{proposal.intent}</code>
                                {Object.keys(proposal.params || {}).length > 0 && (
                                  <span className="rung__meta">
                                    {Object.entries(proposal.params)
                                      .map(([k, v]) => `${k}: ${v}`).join(" · ")}
                                  </span>
                                )}
                              </div>
                              <p className="sc-proposal__why">{proposal.rationale}</p>
                              {proposal.note && (
                                <p className="sc-proposal__note">
                                  <AppIcon name="alert" size={12} /> {proposal.note}
                                </p>
                              )}
                              {proposal.expected_next?.length > 0 && (
                                <div className="rung__meta">
                                  expects → {proposal.expected_next.join(", ")}
                                </div>
                              )}

                              {correcting ? (
                                <div className="cv-correct" style={{ marginTop: 8 }}>
                                  <input placeholder="intent instead (click / set_text / select_option …)"
                                         value={correcting.intent}
                                         onChange={(e) => setCorrecting((c) => ({ ...c, intent: e.target.value }))} />
                                  <input placeholder='params, e.g. {"field": "Work authorization", "value": "Yes"}'
                                         value={correcting.params}
                                         onChange={(e) => setCorrecting((c) => ({ ...c, params: e.target.value }))} />
                                  <textarea className="cv-note-in" rows={2}
                                            placeholder="Why the teacher was wrong — this reasoning is the training signal. Required."
                                            value={correcting.rationale}
                                            onChange={(e) => setCorrecting((c) => ({ ...c, rationale: e.target.value }))} />
                                  <div className="cv-actions">
                                    <button className="btn btn-sm btn-primary" disabled={busy}
                                            onClick={sendCorrection}>Send correction</button>
                                    <button className="btn btn-sm" onClick={() => setCorrecting(null)}>Cancel</button>
                                  </div>
                                </div>
                              ) : (
                                <div className="sc-flags" style={{ borderTop: "none", paddingTop: 8 }}>
                                  <button className="btn btn-sm" disabled={busy}
                                          onClick={() => decide({ action: "go" })}>Go</button>
                                  <button className="btn btn-sm" disabled={busy}
                                          onClick={() => setCorrecting({ intent: proposal.intent,
                                            params: JSON.stringify(proposal.params || {}), rationale: "" })}>
                                    Correct
                                  </button>
                                  <button className="btn btn-sm btn-ghost" disabled={busy}
                                          onClick={() => decide({ action: "skip" })}>Skip</button>
                                </div>
                              )}
                            </div>
                          )}

                          {/* WORK THE STEP. This is the button the first cut was missing: the
                              queue shipped with only ways to END a step, so every application
                              looked like something to dismiss rather than something to do. */}
                          <div className="sc-flags">
                            <button className="btn btn-sm btn-primary" disabled={busy}
                                    onClick={() => call("/apply_step", { initiator: "operator" })}>
                              {busy ? "…" : s.next_rung
                                ? `Work this · ${s.next_rung.replace(/_/g, " ")}`
                                : "Work this step"}
                            </button>
                          </div>

                          <div className="sc-flags sc-flags--end">
                            <span className="rung__meta">or end it:</span>
                            <button className="btn btn-sm" disabled={busy}
                                    title="Only press this when the application is CONFIRMED sent"
                                    onClick={() => flagStep(s.job_id, "submitted")}>
                              Submitted
                            </button>
                            {TERMINAL_CHOICES.map((f) => (
                              <button key={f.flag} className="btn btn-sm" disabled={busy}
                                      title={f.why}
                                      onClick={() => flagStep(s.job_id, f.flag, f.why)}>
                                {f.label}
                              </button>
                            ))}
                          </div>
                        </>
                      )}
                    </li>
                  );
                })}
              </ol>
            </div>
          )}


          {p.picks?.length ? (
            <p className="mode-hint">Approved so far this session: {p.picks.length}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
