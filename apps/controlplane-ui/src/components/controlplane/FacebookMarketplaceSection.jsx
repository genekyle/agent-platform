import { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

/** The Facebook Marketplace workspace.
 *  - fb-overview: the seeded login + create-listing recipes the runner will drive.
 *  - listings:    draft a listing from operator inputs, then run the loop to create it.
 *  - fb-handoffs: the loop's "needs help" queue — why it stopped + what it tried.
 */
export function FacebookMarketplaceSection({ section }) {
  if (section === "listings") return <ListingsPanel />;
  if (section === "fb-handoffs") return <HandoffsPanel />;
  return <OverviewPanel />;
}

// Facebook sessions the loop can be pointed at (needs a live Chrome debug port).
function useFacebookSessions() {
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  useEffect(() => {
    fetch(`${API}/api/training/sessions`).then((r) => r.json()).then((rows) => {
      const fb = (rows || []).filter((s) => (s.domain_id || "").startsWith("facebook"));
      setSessions(fb);
      setSessionId((cur) => cur ?? fb.find((s) => s.status === "active")?.id ?? fb[0]?.id ?? null);
    }).catch(() => {});
  }, []);
  return { sessions, sessionId, setSessionId };
}

function SessionPicker({ sessions, sessionId, setSessionId }) {
  return (
    <select className="input" value={sessionId ?? ""} onChange={(e) => setSessionId(Number(e.target.value) || null)}>
      {sessions.length === 0 && <option value="">No Facebook sessions</option>}
      {sessions.map((s) => (
        <option key={s.id} value={s.id}>#{s.id} · {s.status} · {s.goal?.slice(0, 40) || "session"}</option>
      ))}
    </select>
  );
}

/* -------------------------------------------------------------------------- */
/* Overview — the seeded recipes rendered as flows                            */
/* -------------------------------------------------------------------------- */
function OverviewPanel() {
  const [recipe, setRecipe] = useState(null);
  useEffect(() => {
    fetch(`${API}/api/runtime/facebook_recipe`).then((r) => r.json()).then(setRecipe).catch(() => {});
  }, []);
  if (!recipe) return <div className="section-body"><p className="muted">Loading…</p></div>;

  return (
    <div className="section-body">
      <div className="panel" style={{ padding: "10px 12px", borderLeft: "3px solid #f0883e" }}>
        <strong>Seeded, not yet live-verified.</strong>
        <span className="muted" style={{ marginLeft: 8 }}>{recipe.status}</span>
      </div>
      <RecipeFlow title="Login" spine={recipe.login} />
      <RecipeFlow title="Create listing" spine={recipe.create_listing} />
    </div>
  );
}

function RecipeFlow({ title, spine }) {
  if (!spine) return null;
  const branches = Object.entries(spine.branches || {});
  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="panel-header">
        <div>{title} <span className="muted">· terminal: {spine.terminal_state}</span></div>
      </div>
      <ol style={{ margin: "6px 0", paddingLeft: 20 }}>
        {(spine.recipe || []).map((s) => (
          <li key={s.step} style={{ marginBottom: 4 }}>
            <strong>{s.state}</strong>
            <span className="muted"> — {s.action}</span>
          </li>
        ))}
      </ol>
      {branches.length > 0 && (
        <div style={{ padding: "4px 4px 8px" }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>Human-required branches:</div>
          <ul style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
            {branches.map(([id, b]) => (
              <li key={id} style={{ color: b.human_required ? "#f85149" : "#8b949e" }}>
                <strong>{id}</strong> — {b.note}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Listings — draft the inputs, then run the loop to create the listing        */
/* -------------------------------------------------------------------------- */
const EMPTY_DRAFT = { title: "", price: "", category: "", condition: "", description: "", location: "", photos: [] };

function ListingsPanel() {
  const { sessions, sessionId, setSessionId } = useFacebookSessions();
  const [drafts, setDrafts] = useState([]);
  const [draft, setDraft] = useState({ ...EMPTY_DRAFT });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [run, setRun] = useState(null);

  const load = useCallback(() => {
    fetch(`${API}/api/facebook/listings`).then((r) => r.json()).then((d) => setDrafts(d.drafts || [])).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const missing = useMemo(() => {
    const m = [];
    if (!draft.title.trim()) m.push("title");
    if (!String(draft.price).trim()) m.push("price");
    return m;
  }, [draft]);

  const save = useCallback(async () => {
    setBusy(true); setMsg("");
    try {
      const r = await fetch(`${API}/api/facebook/listings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      }).then((x) => x.json());
      setDraft({ ...EMPTY_DRAFT, ...r.draft });
      setMsg(r.missing_required?.length ? `Saved · still need: ${r.missing_required.join(", ")}` : "Saved.");
      load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }, [draft, load]);

  // Fire the runner loop against the live FB tab. Execute-by-default; the consequential gate
  // holds the final Publish for approval, so a run stops at the review/publish step with a handoff.
  const runCreate = useCallback(async () => {
    if (!sessionId) { setMsg("Pick a Facebook session first (needs a live Chrome)."); return; }
    if (!draft.id) { setMsg("Save the draft first."); return; }
    setBusy(true); setMsg(""); setRun(null);
    try {
      const r = await fetch(`${API}/api/runtime/run_live`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          training_session_id: sessionId,
          task: "facebook_create_listing",
          task_goal: "Create a Facebook Marketplace listing",
          listing_draft_id: draft.id,
          tab_url: "facebook.com",
          max_steps: 14,
        }),
      }).then((x) => x.json());
      setRun(r);
      load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }, [sessionId, draft, load]);

  const edit = (d) => setDraft({ ...EMPTY_DRAFT, ...d });

  return (
    <div className="section-body">
      <AuthProfileCard sessionId={sessionId} setSessionId={setSessionId} />
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 12 }}>
        <SessionPicker sessions={sessions} sessionId={sessionId} setSessionId={setSessionId} />
        <button className="btn" onClick={() => setDraft({ ...EMPTY_DRAFT })}>+ New draft</button>
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <div className="panel-header"><div>{draft.id ? `Edit draft ${draft.id}` : "New listing"}</div></div>
        <div style={{ display: "grid", gap: 10, padding: "10px 4px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 10 }}>
            <Field label="Title *"><input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Trek hybrid bike" /></Field>
            <Field label="Price *"><input value={draft.price} onChange={(e) => setDraft({ ...draft, price: e.target.value })} placeholder="450" /></Field>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
            <Field label="Category"><input value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} placeholder="Bicycles" /></Field>
            <Field label="Condition"><input value={draft.condition} onChange={(e) => setDraft({ ...draft, condition: e.target.value })} placeholder="Used - Good" /></Field>
            <Field label="Location"><input value={draft.location} onChange={(e) => setDraft({ ...draft, location: e.target.value })} placeholder="Nashua, NH" /></Field>
          </div>
          <Field label="Description">
            <textarea rows={3} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Great commuter bike, barely used." />
          </Field>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "…" : "Save draft"}</button>
            <button className="btn" disabled={busy || !draft.id || missing.length > 0} onClick={runCreate}
              title={missing.length ? `Fill: ${missing.join(", ")}` : "Run the loop to create this listing"}>
              {busy ? "Running…" : "Run create-listing (test loop)"}
            </button>
            {missing.length > 0 && <span className="muted" style={{ fontSize: 12 }}>needs: {missing.join(", ")}</span>}
          </div>
          {msg && <div className="muted">{msg}</div>}
        </div>
      </div>

      {run && <RunResult run={run} />}

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="panel-header"><div>Drafts <span className="muted">({drafts.length})</span></div></div>
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>Title</th><th>Price</th><th>Condition</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {drafts.map((d) => (
                <tr key={d.id}>
                  <td>{d.title || <span className="muted">—</span>}</td>
                  <td>{d.price ? `$${d.price}` : "—"}</td>
                  <td className="muted">{d.condition || "—"}</td>
                  <td>{d.status}</td>
                  <td><button className="btn btn-sm" onClick={() => edit(d)}>Edit</button></td>
                </tr>
              ))}
              {drafts.length === 0 && <tr><td colSpan={5} className="muted">No drafts yet.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// The auth/profile step: launch a PERSISTENT Facebook browser (login survives), log in once,
// and see whether the session is signed in. A create-listing run's auth pre-flight refuses to
// drive a logged-out session, so this is the gate to a real run.
function AuthProfileCard({ sessionId, setSessionId }) {
  const [auth, setAuth] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const check = useCallback(async (sid) => {
    const id = sid ?? sessionId;
    if (!id) return;
    try {
      const r = await fetch(`${API}/api/runtime/auth_status?training_session_id=${id}&tab_url=facebook.com`).then((x) => x.json());
      setAuth(r);
    } catch (e) { setMsg(String(e.message || e)); }
  }, [sessionId]);

  const launch = useCallback(async () => {
    setBusy(true); setMsg(""); setAuth(null);
    try {
      const s = await fetch(`${API}/api/facebook/session`, { method: "POST" }).then((x) => x.json());
      if (s?.id) {
        setSessionId(s.id);
        setMsg("Chrome opened at facebook.com — log in there once (do any 2FA/checkpoint by hand). This profile stays signed in.");
        setTimeout(() => check(s.id), 2500);
      } else {
        setMsg(s?.detail || "Could not launch the session.");
      }
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }, [setSessionId, check]);

  const authed = auth?.authed;
  const badge = authed === true ? { t: "Signed in", c: "#3fb950" }
    : authed === false ? { t: "Not signed in", c: "#f85149" }
    : { t: "Unknown", c: "#8b949e" };

  return (
    <div className="panel" style={{ padding: "12px 14px", borderLeft: "3px solid #58a6ff" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div>
          <strong>Sign in once</strong>
          <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
            The loop can’t create a listing until this browser is logged in.
          </span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ color: badge.c, fontWeight: 600, fontSize: 13 }}>● {badge.t}</span>
          <button className="btn btn-primary" disabled={busy} onClick={launch}>
            {busy ? "Launching…" : "Launch persistent FB browser"}
          </button>
          <button className="btn" disabled={!sessionId} onClick={() => check()}>Check sign-in</button>
        </div>
      </div>
      {msg && <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>{msg}</div>}
      {authed === false && auth?.guidance && (
        <div style={{ marginTop: 6, color: "#f0883e", fontSize: 12 }}>{auth.guidance}</div>
      )}
    </div>
  );
}

function RunResult({ run }) {
  const ok = run.completed;
  const color = ok ? "#238636" : run.handoff ? "#f0883e" : "#da3633";
  return (
    <div className="panel" style={{ marginTop: 12, borderLeft: `3px solid ${color}` }}>
      <div style={{ padding: "10px 12px" }}>
        <strong>{ok ? "✓ Listing task completed" : `Stopped: ${run.status}`}</strong>
        <span className="muted" style={{ marginLeft: 8 }}>
          {run.executed_steps} step(s) executed{run.reason ? ` · ${run.reason}` : ""}
        </span>
        {run.handoff && (
          <div style={{ marginTop: 8 }}>
            <div style={{ color: "#f0883e", fontWeight: 600 }}>Needs you: {run.handoff.why}</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{run.handoff.suggestion}</div>
          </div>
        )}
        {run.detail && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{run.detail}</div>}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Handoffs — the loop's "needs help" queue                                    */
/* -------------------------------------------------------------------------- */
function HandoffsPanel() {
  const [rows, setRows] = useState([]);
  const [openOnly, setOpenOnly] = useState(true);

  const load = useCallback(() => {
    fetch(`${API}/api/runtime/handoffs?open_only=${openOnly}`).then((r) => r.json())
      .then((d) => setRows(d.handoffs || [])).catch(() => {});
  }, [openOnly]);
  useEffect(() => { load(); }, [load]);

  const resolve = useCallback(async (id) => {
    await fetch(`${API}/api/runtime/handoffs/${encodeURIComponent(id)}/resolve`, { method: "POST" });
    load();
  }, [load]);

  return (
    <div className="section-body">
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <label className="muted" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> Open only
        </label>
        <button className="btn" onClick={load}>Refresh</button>
      </div>
      {rows.length === 0 && <p className="muted" style={{ marginTop: 12 }}>Nothing waiting on you. 🎉</p>}
      {rows.map((h) => (
        <div key={h.id} className="panel" style={{ marginTop: 12, borderLeft: `3px solid ${h.status === "resolved" ? "#3fb950" : "#f0883e"}` }}>
          <div style={{ padding: "10px 12px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <strong>{h.why}</strong>
              {h.status !== "resolved"
                ? <button className="btn btn-sm" onClick={() => resolve(h.id)}>Mark resolved</button>
                : <span className="muted" style={{ fontSize: 12 }}>resolved</span>}
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {h.task_goal} · {h.escalation_reason || h.loop_status}
            </div>
            <div style={{ marginTop: 4 }}>{h.suggestion}</div>
            {h.tried?.length > 0 && (
              <details style={{ marginTop: 6 }}>
                <summary className="muted" style={{ fontSize: 12, cursor: "pointer" }}>What it tried ({h.tried.length})</summary>
                <ul style={{ margin: "4px 0", paddingLeft: 18, fontSize: 12 }}>
                  {h.tried.map((t, i) => (
                    <li key={i} className="muted">
                      #{t.step} {t.action} · {t.layer}
                      {t.confidence != null ? ` · conf ${Number(t.confidence).toFixed(2)}` : ""}
                      {" · "}{t.outcome}{t.verified === false ? " · unverified" : ""}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            {h.url && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{h.url}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: "block" }}>
      <div className="muted" style={{ fontSize: 12, marginBottom: 3 }}>{label}</div>
      {children}
    </label>
  );
}
