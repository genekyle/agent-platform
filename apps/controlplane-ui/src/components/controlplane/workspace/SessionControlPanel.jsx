import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, postJSON, fmtTime } from "./api";
import { AppIcon } from "../../../ui/Icon";

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
  operator_login: "Not signed in. Pick a way in below, or sign in directly in the window — we never type passwords or clear 2FA.",
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
};

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
  const [picks, setPicks] = useState([]);
  const [note, setNote] = useState("");
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

  const toggle = (jobId) =>
    setPicks((prev) => (prev.includes(jobId) ? prev.filter((x) => x !== jobId) : [...prev, jobId]));

  const doChoose = async (advance) => {
    const d = await call("/choose", { picks, note, advance, initiator: "operator" });
    if (d) { setPicks([]); setNote(""); }
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

          {/* --- the page's results, and the choice ------------------------------- */}
          <div className="live-detail__body">
            {results.length === 0 ? (
              <div className="empty-hint">
                {atLine
                  ? "Step to read this page's results."
                  : "Results appear once the session reaches the start line."}
              </div>
            ) : (
              <>
                <table className="sc-results">
                  <thead>
                    <tr><th /><th>Role</th><th>Company</th><th>Where</th><th>Pay</th></tr>
                  </thead>
                  <tbody>
                    {results.map((r) => (
                      <tr key={r.job_id} className={picks.includes(r.job_id) ? "is-picked" : ""}>
                        <td>
                          <input
                            type="checkbox"
                            checked={picks.includes(r.job_id)}
                            onChange={() => toggle(r.job_id)}
                          />
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
                    <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => doChoose(true)}>
                      {busy ? "…" : `Take ${picks.length} · next page`}
                    </button>
                    <button className="btn btn-sm" disabled={busy} onClick={() => doChoose(false)}>
                      Take {picks.length} · stay
                    </button>
                  </div>
                  <p className="cv-blocked">
                    Picking a job is approval to enter its application. Nothing is submitted without
                    a separate confirmation.
                  </p>
                </div>
              </>
            )}
          </div>

          {p.picks?.length ? (
            <p className="mode-hint">Approved so far this session: {p.picks.length}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
