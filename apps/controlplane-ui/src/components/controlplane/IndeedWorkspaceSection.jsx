import { useCallback, useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

const CATEGORIES = ["compensation", "demographics", "eligibility", "logistics", "experience", "custom"];
const INPUT_HINTS = ["text", "number", "select", "radio", "boolean", "textarea"];

const EMPTY_DRAFT = {
  answer_key: "", display_name: "", category: "custom", value: "",
  question_patterns: [], input_hint: "text", options: [], notes: "",
};

/** The Indeed job-seeking workspace. Two sections:
 *  - indeed-overview: at-a-glance counts (the seed of the eventual jobs dashboard)
 *  - application-answers: editable store of reusable application answers
 */
export function IndeedWorkspaceSection({ section }) {
  if (section === "application-answers") return <ApplicationAnswers />;
  if (section === "jobs-dashboard") return <JobsDashboard />;
  return <IndeedOverview />;
}

function JobsDashboard() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    fetch(`${API}/api/dashboards/indeed_jobs`).then((r) => r.json()).then(setData).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);

  const extract = useCallback(async () => {
    setBusy(true); setMsg("");
    try {
      // find an active Indeed session with a results tab open
      const sessions = await fetch(`${API}/api/training/sessions`).then((r) => r.json());
      const s = sessions.find((x) => x.domain_id === "indeed" && x.status === "active");
      if (!s) { setMsg("No active Indeed session — start one and open a search."); return; }
      const r = await fetch(`${API}/api/jobs/extract`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ training_session_id: s.id, tab_url: "indeed.com/jobs", search_query: "manual extract" }),
      }).then((x) => x.json());
      setMsg(r.ok ? `Scraped ${r.scraped} · ${r.new} new · ${r.duplicates} duplicates` : `Failed: ${r.detail || "?"}`);
      load();
    } catch (e) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }, [load]);

  const mark = useCallback(async (jobId, status) => {
    await fetch(`${API}/api/jobs/${encodeURIComponent(jobId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ application_status: status }),
    });
    load();
  }, [load]);

  if (!data) return <div className="section-body"><p className="muted">Loading…</p></div>;
  const t = data.totals;

  return (
    <div className="section-body">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <p className="muted" style={{ margin: 0 }}>
          Jobs the agent has seen, deduped by identity. Re-seen jobs collapse into one row
          (seen count) so duplicates are easy to find and the corpus stays manageable.
        </p>
        <button className="btn" disabled={busy} onClick={extract}>{busy ? "Extracting…" : "Extract from active search"}</button>
      </div>
      {msg && <div className="muted" style={{ marginTop: 6 }}>{msg}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(130px,1fr))", gap: 10, marginTop: 12 }}>
        <Stat label="Jobs found" value={t.jobs_found} />
        <Stat label="Companies" value={t.distinct_companies} />
        <Stat label="Searches" value={t.searches_performed} />
        <Stat label="Duplicates collapsed" value={t.duplicates_collapsed} />
        <Stat label="Applied" value={t.applied} />
      </div>

      {data.jobs_applied.length > 0 && (
        <JobTable title="Jobs Applied" jobs={data.jobs_applied} mark={mark} />
      )}
      <JobTable title="Jobs Seen" jobs={data.jobs_seen} mark={mark} />
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

function IndeedOverview() {
  const [answers, setAnswers] = useState(null);
  const [coverage, setCoverage] = useState(null);

  useEffect(() => {
    fetch(`${API}/api/application-answers`).then((r) => r.json()).then(setAnswers).catch(() => {});
    fetch(`${API}/api/training/coverage?target_per_state=10`).then((r) => r.json()).then(setCoverage).catch(() => {});
  }, []);

  const indeedStates = useMemo(() => {
    if (!coverage?.states) return [];
    // The coverage feed isn't domain-tagged in the row; filter by the indeed_ prefix
    // we use for Indeed-scoped state ids (best-effort until coverage carries domain_id).
    return coverage.states.filter((s) => (s.state_id || "").startsWith("indeed"));
  }, [coverage]);

  const covered = indeedStates.filter((s) => s.status === "covered").length;

  return (
    <div className="section-body">
      <p className="muted" style={{ marginTop: 0 }}>
        The job-seeking workspace. Application answers power the (future) form-fill executor;
        the jobs dashboard (searches, jobs found, duplicates, applied) lands here next.
      </p>
      <div className="stat-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: "12px" }}>
        <Stat label="Stored answers" value={answers?.total ?? "—"} />
        <Stat label="Answer categories" value={answers ? Object.keys(answers.by_category).length : "—"} />
        <Stat label="Indeed states seen" value={indeedStates.length || "—"} />
        <Stat label="Indeed states covered (≥10)" value={indeedStates.length ? covered : "—"} />
      </div>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-header"><div>Coming soon — Jobs Dashboard</div></div>
        <ul className="muted" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
          <li>Searches performed · jobs found · duplicates · jobs applied</li>
          <li>Jobs Seen + Jobs Applied tables (from an observed_jobs store)</li>
          <li>Populated automatically once job-extraction + the task runner land</li>
        </ul>
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
