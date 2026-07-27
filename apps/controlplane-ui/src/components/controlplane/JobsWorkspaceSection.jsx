import { Fragment, useCallback, useEffect, useState } from "react";
import { AppIcon } from "../../ui/Icon";

const API = import.meta.env.VITE_API_BASE_URL;

const CATEGORIES = ["compensation", "demographics", "eligibility", "logistics", "experience", "custom"];
const INPUT_HINTS = ["text", "number", "select", "radio", "boolean", "textarea"];

const EMPTY_DRAFT = {
  answer_key: "", display_name: "", category: "custom", value: "",
  question_patterns: [], input_hint: "text", options: [], notes: "",
};

/** The job-seeking workspace for ONE career-search aggregator (Indeed, LinkedIn, …).
 *  - jobs-dashboard: the Notion-like database hub (click a database → its table)
 *  - application-answers: editable store of reusable application answers
 *  - apply-state: the live apply blackboard
 *
 *  Every panel is scoped by the `domain` it's given — the dashboard it reads, the sessions it
 *  offers, the tab it extracts from. The one panel that is deliberately NOT scoped is the
 *  Application Profile: those answers are the operator's, reused across every engine and ATS.
 */
export function JobsWorkspaceSection({ domain, section }) {
  if (section === "application-answers") return <ApplicationAnswers />;
  if (section === "apply-state") return <ApplyStatePanel domain={domain} />;
  return <JobsHub domain={domain} />;
}

const STATUS_COLOR = {
  done: "var(--success)", active: "var(--accent)", blocked: "var(--danger)", pending: "var(--text-subtle)",
};

/** The live apply blackboard. Polls /api/runtime/apply_state for the selected session and
 *  shows the four layers made visible: plan progress (the recipe spine + subtask status),
 *  per-field form_state (required/filled/valid), the code-enforced proceed/submit gate, and
 *  the blockers (human branches + empty required fields). This is the "where are we" readout
 *  so tab/step/field state lives on screen, not in someone's head. */
function ApplyStatePanel({ domain }) {
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [state, setState] = useState(null);
  const [err, setErr] = useState("");
  const [live, setLive] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/training/sessions`).then((r) => r.json()).then((rows) => {
      const mine = (rows || []).filter((s) => (s.domain_id || "").startsWith(domain.host));
      setSessions(mine);
      setSessionId((cur) => cur ?? mine.find((s) => s.status === "active")?.id ?? mine[0]?.id ?? null);
    }).catch(() => {});
  }, [domain.host]);

  const load = useCallback(() => {
    if (!sessionId) return;
    fetch(`${API}/api/runtime/apply_state?training_session_id=${sessionId}`)
      .then(async (r) => {
        if (!r.ok) { setErr((await r.json().catch(() => ({})))?.detail || `HTTP ${r.status}`); setState(null); return; }
        setErr(""); setState(await r.json());
      })
      .catch((e) => setErr(String(e.message || e)));
  }, [sessionId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!live || !sessionId) return undefined;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [live, sessionId, load]);

  const bb = state?.blackboard;
  const decision = state?.proceed_decision;

  return (
    <div className="section-body">
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <select className="input" value={sessionId ?? ""} onChange={(e) => setSessionId(Number(e.target.value) || null)}>
          {sessions.length === 0 && <option value="">No {domain.label} sessions</option>}
          {sessions.map((s) => (
            <option key={s.id} value={s.id}>#{s.id} · {s.status} · {s.goal?.slice(0, 40) || "session"}</option>
          ))}
        </select>
        <button className="btn" onClick={load}>Refresh</button>
        <label className="muted" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live (4s)
        </label>
      </div>

      {err && (
        <div className="muted" style={{ marginTop: 10, color: "#f0883e" }}>
          {err.includes("not reachable") ? "Session Chrome isn't reachable — start the session and open the apply tab." : err}
        </div>
      )}

      {decision && (
        <div style={{
          marginTop: 12, padding: "10px 12px", borderRadius: 8,
          border: `1px solid ${decision.ok ? "#238636" : "#da3633"}`,
          background: decision.ok ? "rgba(46,160,67,0.12)" : "rgba(218,54,51,0.12)",
        }}>
          <strong>{decision.ok ? "✓ Clear to proceed" : "✕ Gate is holding"}</strong>
          <span className="muted" style={{ marginLeft: 8 }}>{decision.reason}</span>
          {!decision.ok && (decision.blockers || []).length > 0 && (
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {decision.blockers.map((b, i) => (
                <li key={i}>{b.label || b.note || b.field_id || b.source} {b.reason ? `(${b.reason})` : ""}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {bb && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 14 }}>
          <div>
            <h4 style={{ margin: "0 0 6px" }}>Plan</h4>
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              {bb.plan.map((s) => (
                <li key={s.id} style={{ color: STATUS_COLOR[s.status] || "#c9d1d9", fontWeight: s.id === bb.current_subtask_id ? 700 : 400 }}>
                  {s.label} <span className="muted" style={{ fontSize: 12 }}>· {s.status}{s.gate ? " · gated" : ""}</span>
                </li>
              ))}
            </ol>
          </div>
          <div>
            <h4 style={{ margin: "0 0 6px" }}>Form state {bb.gate_ok ? "✓" : "✕"}</h4>
            {(bb.form_state || []).length === 0
              ? <p className="muted" style={{ margin: 0 }}>No form fields scanned on the active tab.</p>
              : (
                <table style={{ width: "100%", fontSize: 13, borderCollapse: "collapse" }}>
                  <thead><tr style={{ textAlign: "left", color: "#8b949e" }}>
                    <th>Field</th><th>Kind</th><th>Req</th><th>Filled</th><th>Valid</th>
                  </tr></thead>
                  <tbody>
                    {bb.form_state.map((f) => (
                      <tr key={f.field_id} style={{ background: f.required && !(f.filled && f.valid) ? "rgba(248,81,73,0.12)" : "transparent" }}>
                        <td title={f.label}>{(f.label || f.field_id).slice(0, 26)}</td>
                        <td className="muted">{f.kind}</td>
                        <td>{f.required ? "●" : "—"}</td>
                        <td>{f.filled ? "✓" : "—"}</td>
                        <td>{f.valid ? "✓" : "✕"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
          </div>
          <div>
            <h4 style={{ margin: "0 0 6px" }}>Tabs ({bb.world?.tabs?.length || 0})</h4>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {(bb.world?.tabs || []).map((t, i) => (
                <li key={i} className="muted" style={{ fontWeight: i === bb.world?.active_tab_index ? 700 : 400, color: t.human_required ? "#f85149" : undefined }}>
                  [{t.role}] {t.state}{t.human_required ? " · human" : ""}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 style={{ margin: "0 0 6px" }}>Recent events</h4>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {(bb.events || []).slice(-6).reverse().map((e, i) => (
                <li key={i} className="muted">{e.kind}: {e.detail}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {!state && !err && <p className="muted" style={{ marginTop: 12 }}>Select a session to load its apply state.</p>}
    </div>
  );
}

/** Notion-like database hub for a jobs workspace. The landing is a gallery of "databases"
 *  (All Jobs, Searches, Job Descriptions, Applied); click one to open its full table, with a
 *  breadcrumb back to the gallery. The sweep/extract controls + headline stats sit on top.
 *  Everything is scoped to `domain` — the dashboard read, the session picked, the tab extracted. */
function JobsHub({ domain }) {
  const [data, setData] = useState(null);
  const [targets, setTargets] = useState([]);
  const [busy, setBusy] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [msg, setMsg] = useState("");
  const [sweep, setSweep] = useState(null);
  const [open, setOpen] = useState(null);   // null = gallery; else a database id

  const load = useCallback(() => {
    fetch(`${API}/api/dashboards/${domain.id}`).then((r) => r.json()).then(setData).catch(() => {});
    fetch(`${API}/api/search/targets`).then((r) => r.json()).then((d) => setTargets(d.targets || [])).catch(() => {});
  }, [domain.id]);
  useEffect(() => { load(); }, [load]);

  const activeSession = useCallback(async () => {
    const sessions = await fetch(`${API}/api/training/sessions`).then((r) => r.json());
    return sessions.find((x) => (x.domain_id || "").startsWith(domain.host) && x.status === "active");
  }, [domain.host]);

  const extract = useCallback(async () => {
    setBusy(true); setMsg("");
    try {
      const s = await activeSession();
      if (!s) { setMsg(`No active ${domain.label} session — start one and open a search.`); return; }
      const r = await fetch(`${API}/api/jobs/extract`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          training_session_id: s.id, tab_url: domain.tabUrl,
          platform: data?.platform || domain.host, search_query: "manual extract",
        }),
      }).then((x) => x.json());
      setMsg(r.ok ? `Scraped ${r.scraped} · ${r.new} new · ${r.duplicates} duplicates` : `Failed: ${r.detail || "?"}`);
      load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }, [activeSession, load, domain.label, domain.tabUrl, domain.host, data?.platform]);

  // The bounded auto-sweep: distance >=50mi → extract → click shortlisted cards → paginate.
  // `domain_id` names WHICH engine — it decides the tab the sweep aims at and the platform the
  // rows are tagged with. The readers themselves are chosen server-side off the live tab's host.
  const runSweep = useCallback(async () => {
    setSweeping(true); setMsg(""); setSweep(null);
    try {
      const s = await activeSession();
      if (!s) { setMsg(`No active ${domain.label} session — start one and open a search.`); return; }
      const r = await fetch(`${API}/api/search/sweep`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ training_session_id: s.id, domain_id: domain.id, min_miles: 50 }),
      }).then((x) => x.json());
      setSweep(r);
      load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setSweeping(false); }
  }, [activeSession, load, domain.label, domain.id]);

  const mark = useCallback(async (jobId, status) => {
    await fetch(`${API}/api/jobs/${encodeURIComponent(jobId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ application_status: status }),
    });
    load();
  }, [load]);

  if (!data) return <div className="section-body"><p className="muted">Loading…</p></div>;
  const t = data.totals;
  const STOP_OK = new Set(["max_pages", "no_next_page"]);

  // The databases shown as clickable cards (Notion-style). Each renders its own table when opened.
  const DATABASES = [
    { id: "jobs", icon: "briefcase", name: "All Jobs", count: data.jobs_seen.length,
      desc: "Every job seen across searches, deduped by identity." },
    { id: "searches", icon: "search", name: "Searches", count: targets.length || (data.by_query || []).length,
      desc: "Search targets (query · location · radius) and how each is doing." },
    { id: "descriptions", icon: "file", name: "Job Descriptions", count: (data.descriptions || []).length,
      desc: "Full descriptions captured by clicking into shortlisted listings." },
    { id: "applied", icon: "checkCircle", name: "Applied", count: data.jobs_applied.length,
      desc: "Jobs marked applied (incl. cross-platform identity matches)." },
  ];
  const current = DATABASES.find((d) => d.id === open);

  return (
    <div className="section-body">
      {/* --- action bar + headline stats (always visible) --- */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <p className="muted" style={{ margin: 0, flex: 1, minWidth: 240 }}>
          Your {domain.label} databases. The sweep forces ≥50 mi, walks each results page, clicks
          shortlisted cards for full descriptions, and pages forward — human-paced.
          {" "}Click a database to open it.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          {domain.sweep && (
            <button className="btn btn-primary" disabled={sweeping} onClick={runSweep}>
              {sweeping ? "Sweeping…" : "Run sweep (≥50 mi)"}
            </button>
          )}
          <button className="btn" disabled={busy} onClick={extract}>{busy ? "Extracting…" : "Extract page"}</button>
        </div>
      </div>
      {msg && <div className="muted" style={{ marginTop: 6 }}>{msg}</div>}

      {sweep && (
        <div style={{
          marginTop: 10, padding: "10px 12px", borderRadius: 8,
          border: `1px solid ${sweep.ok && STOP_OK.has(sweep.stopped_reason) ? "#238636" : "#da3633"}`,
          background: sweep.ok && STOP_OK.has(sweep.stopped_reason) ? "rgba(46,160,67,0.12)" : "rgba(218,54,51,0.12)",
        }}>
          {sweep.ok ? (
            <span>
              <strong>Sweep done</strong> · {sweep.pages_swept} pages · {sweep.jobs_found} found ·
              {" "}{sweep.new} new · {sweep.shortlisted} shortlisted · {sweep.descriptions_captured} descriptions ·
              {" "}{sweep.distance_selected ?? "?"} mi · stopped: {sweep.stopped_reason}
            </span>
          ) : (
            <span><strong>Sweep stopped:</strong> {sweep.stopped_reason}
              {sweep.stopped_reason === "not_authenticated" && " — log the session in first."}
              {sweep.stopped_reason === "captcha" && " — a challenge is showing; a human must clear it."}
              {sweep.stopped_reason === "distance_filter_failed" && " — couldn't set the distance filter."}
            </span>
          )}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: 10, marginTop: 12 }}>
        <Stat label="Jobs found" value={t.jobs_found} />
        <Stat label="Companies" value={t.distinct_companies} />
        <Stat label="Searches" value={t.searches_performed} />
        <Stat label="With description" value={t.with_description ?? 0} />
        <Stat label="Duplicates collapsed" value={t.duplicates_collapsed} />
        <Stat label="Applied" value={t.applied} />
      </div>

      {/* --- gallery vs. opened database --- */}
      {!open ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: 12, marginTop: 16 }}>
          {DATABASES.map((d) => (
            <DatabaseCard key={d.id} db={d} onOpen={() => setOpen(d.id)} />
          ))}
        </div>
      ) : (
        <div style={{ marginTop: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <button className="btn btn-sm" onClick={() => setOpen(null)}>← Databases</button>
            <span className="muted">/</span>
            <AppIcon name={current?.icon} size={16} /><strong>{current?.name}</strong>
            <span className="muted">({current?.count})</span>
          </div>
          {open === "jobs" && <JobTable title="All Jobs" jobs={data.jobs_seen} mark={mark} />}
          {open === "applied" && (
            data.jobs_applied.length
              ? <JobTable title="Applied" jobs={data.jobs_applied} mark={mark} />
              : <EmptyDb text="Nothing applied yet. Mark a job 'Applied' from the All Jobs table." />
          )}
          {open === "descriptions" && (
            (data.descriptions || []).length
              ? <DescriptionsTable jobs={data.descriptions} />
              : <EmptyDb text="No descriptions captured yet. Run a sweep to click into shortlisted listings." />
          )}
          {open === "searches" && <SearchesTable targets={targets} byQuery={data.by_query || []} />}
        </div>
      )}
    </div>
  );
}

function DatabaseCard({ db, onOpen }) {
  return (
    <button className="panel" onClick={onOpen}
      style={{ textAlign: "left", cursor: "pointer", padding: "14px 16px", border: "1px solid var(--border, #30363d)",
               background: "transparent", display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <AppIcon name={db.icon} size={20} />
        <span style={{ fontSize: 20, fontWeight: 700 }}>{db.count}</span>
      </div>
      <div style={{ fontWeight: 600 }}>{db.name}</div>
      <div className="muted" style={{ fontSize: 12 }}>{db.desc}</div>
    </button>
  );
}

function EmptyDb({ text }) {
  return <div className="panel" style={{ marginTop: 4, padding: "18px 16px" }}><p className="muted" style={{ margin: 0 }}>{text}</p></div>;
}

function SearchesTable({ targets, byQuery }) {
  // Merge the configured targets (query · location · radius · status) with the live per-query
  // rollup (found / with-description / applied) so one table answers "what are we searching and
  // how is each doing". Queries seen in results but not configured are appended.
  const byQ = new Map(byQuery.map((r) => [r.query, r]));
  const rows = targets.map((t) => ({
    query: t.query, location: t.location || "—", radius: t.radius_miles ?? 50, status: t.status || "active",
    ...(byQ.get(t.query) || { found: 0, with_description: 0, applied: 0 }),
  }));
  for (const r of byQuery) {
    if (!targets.some((t) => t.query === r.query)) {
      rows.push({ query: r.query, location: "—", radius: "—", status: "ad-hoc", ...r });
    }
  }
  return (
    <div className="panel">
      <div className="panel-header"><div>Searches <span className="muted">({rows.length})</span></div></div>
      <div className="table-wrap">
        <table className="runs-table">
          <thead><tr><th>Query</th><th>Location</th><th>Radius</th><th>Status</th><th>Found</th><th>With desc</th><th>Applied</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.query}-${i}`}>
                <td>{r.query}</td>
                <td>{r.location}</td>
                <td style={{ textAlign: "center" }}>{r.radius === "—" ? "—" : `${r.radius} mi`}</td>
                <td>{r.status}</td>
                <td style={{ textAlign: "center" }}>{r.found}</td>
                <td style={{ textAlign: "center" }}>{r.with_description}</td>
                <td style={{ textAlign: "center" }}>{r.applied}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DescriptionsTable({ jobs }) {
  const [open, setOpen] = useState(null);
  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="panel-header"><div>Job Descriptions <span className="muted">({jobs.length})</span></div></div>
      <div className="table-wrap">
        <table className="runs-table">
          <thead><tr><th>Title</th><th>Company</th><th>Apply</th><th>Salary</th><th>Chars</th><th></th></tr></thead>
          <tbody>
            {jobs.map((j) => (
              <Fragment key={j.job_id}>
                <tr>
                  <td>{j.url ? <a href={j.url} target="_blank" rel="noreferrer">{j.title || j.external_id}</a> : (j.title || j.external_id)}</td>
                  <td>{j.company || <span className="muted">—</span>}</td>
                  <td className="muted">{j.apply_type || "—"}</td>
                  <td className="muted">{j.salary || "—"}</td>
                  <td style={{ textAlign: "center" }}>{j.desc_chars}</td>
                  <td><button className="btn btn-sm" onClick={() => setOpen(open === j.job_id ? null : j.job_id)}>{open === j.job_id ? "Hide" : "Read"}</button></td>
                </tr>
                {open === j.job_id && (
                  <tr><td colSpan={6}>
                    <div style={{ whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 300, overflow: "auto", padding: "6px 2px" }}>
                      {j.description || <span className="muted">No description captured.</span>}
                    </div>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobTable({ title, jobs, mark }) {
  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="panel-header"><div>{title} <span className="muted">({jobs.length})</span></div></div>
      <div className="table-wrap">
        <table className="runs-table">
          <thead><tr><th>Title</th><th>Company</th><th>Location</th><th>Seen</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.job_id}>
                <td>{j.url ? <a href={j.url} target="_blank" rel="noreferrer">{j.title || j.external_id}</a> : (j.title || j.external_id)}</td>
                <td>{j.company || <span className="muted">—</span>}</td>
                <td>{j.location || <span className="muted">—</span>}</td>
                <td style={{ textAlign: "center" }}>{j.seen_count > 1 ? <strong>{j.seen_count}×</strong> : j.seen_count}</td>
                <td>{j.application_status}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {j.application_status !== "applied" && <button className="btn btn-sm" onClick={() => mark(j.job_id, "applied")}>Applied</button>}{" "}
                  {j.application_status !== "skipped" && <button className="btn btn-sm" onClick={() => mark(j.job_id, "skipped")}>Skip</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="panel" style={{ padding: "12px 14px" }}>
      <div style={{ fontSize: 22, fontWeight: 600 }}>{value}</div>
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
    </div>
  );
}

function ApplicationAnswers() {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState(null); // null = not editing; object = editing/creating
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetch(`${API}/api/application-answers`).then((r) => r.json()).then(setData).catch(() => setError("Failed to load"));
  }, []);
  useEffect(() => { load(); }, [load]);

  const save = useCallback(async () => {
    if (!draft?.display_name?.trim()) { setError("Name is required"); return; }
    setBusy(true); setError("");
    try {
      const isEdit = Boolean(draft._existingKey);
      const url = isEdit
        ? `${API}/api/application-answers/${encodeURIComponent(draft._existingKey)}`
        : `${API}/api/application-answers`;
      const payload = {
        answer_key: draft.answer_key || undefined,
        display_name: draft.display_name, category: draft.category, value: draft.value,
        question_patterns: draft.question_patterns, input_hint: draft.input_hint,
        options: draft.options, notes: draft.notes,
      };
      const r = await fetch(url, {
        method: isEdit ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "save failed");
      setDraft(null); load();
    } catch (e) { setError(String(e.message || e)); } finally { setBusy(false); }
  }, [draft, load]);

  const remove = useCallback(async (key) => {
    if (!window.confirm(`Delete answer "${key}"?`)) return;
    await fetch(`${API}/api/application-answers/${encodeURIComponent(key)}`, { method: "DELETE" });
    load();
  }, [load]);

  const startEdit = (a) => setDraft({ ...EMPTY_DRAFT, ...a, _existingKey: a.answer_key });
  const startNew = () => setDraft({ ...EMPTY_DRAFT });

  if (!data) return <div className="section-body"><p className="muted">Loading…</p></div>;

  return (
    <div className="section-body">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <p className="muted" style={{ margin: 0 }}>
          Reusable answers to repeatable application questions. The form-fill executor will
          match an on-screen question to one of these; unmatched questions fall back to Haiku.
        </p>
        <button className="btn" onClick={startNew}>+ New answer</button>
      </div>
      {error && <div className="error-banner" style={{ marginTop: 8 }}>{error}</div>}

      {draft && (
        <div className="panel" style={{ marginTop: 12 }}>
          <div className="panel-header"><div>{draft._existingKey ? `Edit: ${draft._existingKey}` : "New answer"}</div></div>
          <div style={{ display: "grid", gap: 10, padding: "10px 4px" }}>
            <Field label="Display name">
              <input value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} placeholder="Salary expectation" />
            </Field>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <Field label="Category">
                <select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <Field label="Input type">
                <select value={draft.input_hint} onChange={(e) => setDraft({ ...draft, input_hint: e.target.value })}>
                  {INPUT_HINTS.map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </Field>
            </div>
            <Field label="Answer value">
              <input value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} placeholder="70000" />
            </Field>
            <Field label="Question patterns (one per line — how the question gets phrased)">
              <textarea rows={3} value={(draft.question_patterns || []).join("\n")}
                onChange={(e) => setDraft({ ...draft, question_patterns: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })}
                placeholder={"expected salary\ndesired compensation"} />
            </Field>
            <Field label="Options (for select/radio — one per line, optional)">
              <textarea rows={2} value={(draft.options || []).join("\n")}
                onChange={(e) => setDraft({ ...draft, options: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} />
            </Field>
            <Field label="Notes">
              <input value={draft.notes || ""} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
            </Field>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn btn-primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
              <button className="btn" onClick={() => { setDraft(null); setError(""); }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {Object.entries(data.by_category).map(([cat, items]) => (
        <div className="panel" key={cat} style={{ marginTop: 12 }}>
          <div className="panel-header"><div style={{ textTransform: "capitalize" }}>{cat}</div></div>
          <table className="runs-table">
            <thead><tr><th>Answer</th><th>Value</th><th>Type</th><th>Patterns</th><th></th></tr></thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.answer_key}>
                  <td><div style={{ fontWeight: 600 }}>{a.display_name}</div><div className="muted" style={{ fontSize: 11 }}>{a.answer_key}</div></td>
                  <td>{a.value}</td>
                  <td>{a.input_hint}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{(a.question_patterns || []).length} phrasings</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="btn btn-sm" onClick={() => startEdit(a)}>Edit</button>{" "}
                    <button className="btn btn-sm" onClick={() => remove(a.answer_key)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
