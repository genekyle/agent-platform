import { useCallback, useEffect, useMemo, useState } from "react";
import { getJSON, postJSON, fmtTime } from "./workspace/api";

// The career-search job database — ONE table across every board.
//
// The older jobs hub is scoped to a single engine, which was fine while a job was whatever Indeed
// said it was. It stops being fine the moment the same posting is on two boards: "how many jobs
// have I found" then has two answers and neither is right. This view reads canonical jobs, so a
// posting seen on Indeed and LinkedIn is ONE row that names both, and `platform` is a filter
// rather than a partition.
//
// Four things the operator asked to be able to do, in four tabs:
//   Jobs        every posting, its description, and what happened to it
//   Applied     only the ones with an application, with the reply timeline
//   Duplicates  the review queue — nothing is ever folded away without a human saying so
//   Inbox       the Gmail sweep — outcomes read off the mailbox, ambiguous matches held for review

const TABS = [
  { id: "jobs", label: "Jobs" },
  { id: "applied", label: "Applied" },
  { id: "duplicates", label: "Duplicates" },
  { id: "inbox", label: "Inbox" },
];

// Event kinds worth one-click access on an application. The full vocabulary comes from the server
// (`/event_vocabulary`) so the dropdown can never drift from the validator; these are just the
// ones common enough to deserve a button.
const QUICK_EVENTS = [
  { kind: "rejection", label: "Rejected", tone: "#f85149" },
  { kind: "recruiter_contact", label: "Heard back", tone: "#3fb950" },
  { kind: "interview_invite", label: "Interview", tone: "#58a6ff" },
];

const STATUS_TONE = {
  applied: "#8b949e", acknowledged: "#8b949e", viewed: "#8b949e",
  responded: "#3fb950", screening: "#3fb950", interview: "#58a6ff",
  offer: "#d29922", rejected: "#f85149", withdrawn: "#6e7681",
};

export function JobDatabaseSection({ domain }) {
  // A per-engine workspace pins the filter to its own board; the Career Search parent shows all.
  // `host` is the board id ('indeed', 'linkedin') and matches the platform recorded on a
  // sighting. Only a job ENGINE pins it; the Career Search parent is a group with no host,
  // and an ATS sub-domain's host ('workday') is not a board anything was scraped from.
  const pinnedPlatform = domain?.kind === "jobs" ? domain.host || null : null;
  const [tab, setTab] = useState("jobs");
  const [overview, setOverview] = useState(null);
  const [vocab, setVocab] = useState(null);
  const [err, setErr] = useState("");

  const [inboxPending, setInboxPending] = useState(0);

  const loadOverview = useCallback(() => {
    getJSON("/api/career_search/overview").then(setOverview).catch((e) => setErr(e.message));
  }, []);
  const loadInboxPending = useCallback(() => {
    getJSON("/api/career_search/inbox?status=needs_review")
      .then((d) => setInboxPending(d.total || 0)).catch(() => {});
  }, []);

  useEffect(() => {
    loadOverview();
    loadInboxPending();
    getJSON("/api/career_search/event_vocabulary").then(setVocab).catch(() => {});
  }, [loadOverview, loadInboxPending]);

  const pending = overview?.totals?.pending_duplicates || 0;

  return (
    <div className="section-body">
      <div className="panel">
        <div className="panel-header">
          <div>
            Job Database
            {pinnedPlatform && <span className="muted"> · {pinnedPlatform}</span>}
          </div>
          <button className="btn btn-sm" onClick={() => {
            postJSON("/api/career_search/reindex", {}).then(loadOverview).catch((e) => setErr(e.message));
          }}>Reindex</button>
        </div>
        <p className="system-micro-copy" style={{ margin: "0 12px 10px" }}>
          One row per real posting, across every board. A job seen on Indeed and LinkedIn is one row
          that names both — {pinnedPlatform ? `filtered here to ${pinnedPlatform}.` : "no board owns its own copy."}
        </p>
        {err && <p className="mode-hint" style={{ color: "#f85149", margin: "0 12px 10px" }}>{err}</p>}
        <Stats overview={overview} />
      </div>

      <div style={{ display: "flex", gap: 6, margin: "14px 0 0" }}>
        {TABS.map((t) => (
          <button key={t.id}
                  className={t.id === tab ? "btn btn-sm btn-primary" : "btn btn-sm"}
                  onClick={() => setTab(t.id)}>
            {t.label}
            {t.id === "duplicates" && pending > 0 && ` (${pending})`}
            {t.id === "inbox" && inboxPending > 0 && ` (${inboxPending})`}
          </button>
        ))}
      </div>

      {tab === "duplicates"
        ? <Duplicates onChange={loadOverview} />
        : tab === "inbox"
        ? <InboxQueue vocab={vocab} onChange={() => { loadOverview(); loadInboxPending(); }} />
        : <JobList key={tab} pinnedPlatform={pinnedPlatform} appliedOnly={tab === "applied"}
                   vocab={vocab} onChange={loadOverview} />}
    </div>
  );
}

// --------------------------------------------------------------------------------------
// Headline numbers
// --------------------------------------------------------------------------------------

function Stat({ label, value, sub, tone }) {
  return (
    <div className="stat-card" style={{ padding: "12px 14px", borderTop: `3px solid ${tone || "#cbd5e1"}` }}>
      <div className="stat-label" style={{ marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, lineHeight: 1 }}>{value}</div>
      {sub && <div className="stat-footnote" style={{ marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

function Stats({ overview }) {
  if (!overview) return <p className="muted" style={{ margin: "0 12px 12px" }}>Loading…</p>;
  const t = overview.totals;
  const pct = (n) => `${Math.round((n || 0) * 100)}%`;
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, padding: "0 12px 12px" }}>
        <Stat label="Jobs" value={t.jobs} sub={`${t.sightings} sightings · ${t.duplicates_collapsed} collapsed`} />
        <Stat label="Applied" value={t.applied} sub={`${t.observations} total observations`} tone="#58a6ff" />
        {/* Against applications, never against jobs found — dividing replies by postings-seen
            produces a number that FALLS every time a sweep runs well. */}
        <Stat label="Response rate" value={pct(t.response_rate)}
              sub={`${t.responded} replied · ${t.ghosted} ghosted`} tone="#3fb950" />
        <Stat label="Descriptions" value={pct(t.description_coverage)}
              sub={`${t.with_description} of ${t.jobs} jobs`} tone="#d29922" />
        <Stat label="To review" value={t.pending_duplicates} sub="possible duplicates" tone={t.pending_duplicates ? "#f85149" : "#cbd5e1"} />
      </div>
      {overview.description_by_platform?.length > 1 && (
        <p className="stat-footnote" style={{ margin: "0 12px 12px" }}>
          Description coverage by board:{" "}
          {overview.description_by_platform.map((r) => (
            <span key={r.platform} style={{ marginRight: 12 }}>
              <strong>{r.platform}</strong> {r.with_description}/{r.jobs} ({pct(r.coverage)})
            </span>
          ))}
        </p>
      )}
    </>
  );
}

// --------------------------------------------------------------------------------------
// The job table
// --------------------------------------------------------------------------------------

function JobList({ pinnedPlatform, appliedOnly, vocab, onChange }) {
  const [q, setQ] = useState("");
  const [platform, setPlatform] = useState(pinnedPlatform || "");
  const [hasDescription, setHasDescription] = useState("");
  const [sort, setSort] = useState(appliedOnly ? "applied" : "last_seen");
  const [data, setData] = useState(null);
  const [openKey, setOpenKey] = useState(null);
  const [err, setErr] = useState("");

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim());
    if (platform) p.set("platform", platform);
    if (hasDescription) p.set("has_description", hasDescription);
    if (appliedOnly) p.set("applied", "true");
    p.set("sort", sort);
    p.set("limit", "200");
    return p.toString();
  }, [q, platform, hasDescription, sort, appliedOnly]);

  const load = useCallback(() => {
    getJSON(`/api/career_search/jobs?${query}`).then(setData).catch((e) => setErr(e.message));
  }, [query]);

  // Debounced so typing in the search box doesn't fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(load, 220);
    return () => clearTimeout(t);
  }, [load]);

  const refresh = () => { load(); onChange?.(); };

  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="panel-header">
        <div>{appliedOnly ? "Applied" : "All jobs"} <span className="muted">({data?.total ?? "…"})</span></div>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "0 12px 10px" }}>
        <input className="form-input" style={{ flex: "1 1 220px" }} value={q}
               onChange={(e) => setQ(e.target.value)}
               placeholder="Search title, company, or description text…" />
        {!pinnedPlatform && (
          <select className="form-select" value={platform} onChange={(e) => setPlatform(e.target.value)}>
            <option value="">All boards</option>
            <option value="indeed">Indeed</option>
            <option value="linkedin">LinkedIn</option>
          </select>
        )}
        <select className="form-select" value={hasDescription} onChange={(e) => setHasDescription(e.target.value)}>
          <option value="">Any description</option>
          <option value="true">Has description</option>
          <option value="false">Missing description</option>
        </select>
        <select className="form-select" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="last_seen">Recently seen</option>
          <option value="seen">Most observed</option>
          <option value="first_seen">First found</option>
          <option value="applied">Applied date</option>
          <option value="company">Company</option>
        </select>
      </div>

      {err && <p className="mode-hint" style={{ color: "#f85149", margin: "0 12px 10px" }}>{err}</p>}

      {data && data.jobs.length === 0 && (
        <p className="empty-state" style={{ margin: "0 12px 14px" }}>
          {appliedOnly ? "Nothing applied yet." : "No jobs match these filters."}
        </p>
      )}

      {data && data.jobs.length > 0 && (
        <div className="table-wrap">
          <table className="runs-table">
            <thead>
              <tr>
                <th>Title</th><th>Company</th><th>Boards</th>
                <th style={{ textAlign: "center" }}>Seen</th>
                <th>JD</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((j) => (
                <JobRow key={j.job_key} job={j} open={openKey === j.job_key}
                        onToggle={() => setOpenKey(openKey === j.job_key ? null : j.job_key)}
                        vocab={vocab} onChange={refresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function JobRow({ job, open, onToggle, vocab, onChange }) {
  const app = job.application;
  return (
    <>
      <tr>
        <td>{job.url
          ? <a href={job.url} target="_blank" rel="noreferrer">{job.title || job.job_key}</a>
          : (job.title || job.job_key)}</td>
        <td>{job.company || <span className="muted">— no employer scraped —</span>}</td>
        <td className="muted">{(job.source_platforms || []).join(", ") || "—"}</td>
        {/* Two different numbers: how many rows resolve here, and how many times it has been
            observed. A posting still surfacing after six sweeps is still open. */}
        <td style={{ textAlign: "center" }} title={`${job.sighting_count} sighting row(s)`}>
          {job.seen_count > 1 ? <strong>{job.seen_count}×</strong> : job.seen_count}
          {job.sighting_count > 1 && <span className="muted"> ({job.sighting_count})</span>}
        </td>
        <td>{job.has_description
          ? <span title={job.description_source || ""}>✓</span>
          : <span className="muted">—</span>}</td>
        <td>
          {app
            ? <span style={{ color: STATUS_TONE[app.status] || undefined, fontWeight: 600 }}>{app.status}</span>
            : <span className="muted">{job.status}</span>}
          {app?.ghosted && <span className="muted" title="No employer reply in six weeks"> · ghosted</span>}
        </td>
        <td><button className="btn btn-sm" onClick={onToggle}>{open ? "Close" : "Open"}</button></td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} style={{ background: "rgba(127,127,127,0.05)" }}>
            <JobDetail jobKey={job.job_key} vocab={vocab} onChange={onChange} />
          </td>
        </tr>
      )}
    </>
  );
}

// --------------------------------------------------------------------------------------
// One job, in full
// --------------------------------------------------------------------------------------

function JobDetail({ jobKey, vocab, onChange }) {
  const [job, setJob] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getJSON(`/api/career_search/jobs/${encodeURIComponent(jobKey)}`)
      .then(setJob).catch((e) => setErr(e.message));
  }, [jobKey]);
  useEffect(load, [load]);

  const act = (path, body) => {
    setBusy(true);
    postJSON(path, body)
      .then(() => { load(); onChange?.(); })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  if (err) return <p className="mode-hint" style={{ color: "#f85149" }}>{err}</p>;
  if (!job) return <p className="muted">Loading…</p>;

  const app = job.application;

  return (
    <div style={{ display: "grid", gap: 14, padding: "10px 4px" }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {!app && (
          <button className="btn btn-sm btn-primary" disabled={busy}
                  onClick={() => act(`/api/career_search/jobs/${encodeURIComponent(jobKey)}/apply`, {})}>
            Mark applied
          </button>
        )}
        {app && QUICK_EVENTS.map((e) => (
          <button key={e.kind} className="btn btn-sm" disabled={busy}
                  style={{ borderColor: e.tone, color: e.tone }}
                  onClick={() => act(`/api/career_search/jobs/${encodeURIComponent(jobKey)}/events`,
                                     { kind: e.kind, source: "human", summary: e.label })}>
            {e.label}
          </button>
        ))}
        {(vocab?.job_statuses || []).map((s) => (
          s !== job.status && (
            <button key={s} className="btn btn-sm" disabled={busy}
                    onClick={() => act(`/api/career_search/jobs/${encodeURIComponent(jobKey)}/status`, { status: s })}>
              {s}
            </button>
          )
        ))}
      </div>

      {app && <Timeline app={app} vocab={vocab} busy={busy}
                        onAdd={(body) => act(`/api/career_search/jobs/${encodeURIComponent(jobKey)}/events`, body)} />}

      <div>
        <div className="field-label">Job description
          {job.description_source && <span className="muted"> · read from {job.description_source}</span>}
        </div>
        {job.description
          ? <div style={{ whiteSpace: "pre-wrap", fontSize: 12, maxHeight: 340, overflow: "auto",
                          padding: "8px 10px", border: "1px solid rgba(127,127,127,0.25)", borderRadius: 6 }}>
              {job.description}
            </div>
          : <p className="muted" style={{ margin: 0 }}>
              Not captured yet — the sweep reads descriptions by clicking into each card.
            </p>}
      </div>

      <div>
        <div className="field-label">Where this job was seen ({job.sightings.length})</div>
        <table className="runs-table">
          <thead><tr><th>Board</th><th>Searches that found it</th><th style={{ textAlign: "center" }}>Times</th><th>Last seen</th></tr></thead>
          <tbody>
            {job.sightings.map((s) => (
              <tr key={s.job_id}>
                <td>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.platform}</a> : s.platform}</td>
                <td className="muted">{(s.search_queries || []).join(" · ") || "—"}</td>
                <td style={{ textAlign: "center" }}>{s.seen_count}</td>
                <td className="muted">{fmtTime(s.last_seen_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {job.merged_jobs?.length > 0 && (
          <p className="stat-footnote" style={{ marginTop: 6 }}>
            Folded in: {job.merged_jobs.map((m) => `${m.title} (${m.company || "no employer"})`).join("; ")}
          </p>
        )}
      </div>
    </div>
  );
}

function Timeline({ app, vocab, busy, onAdd }) {
  const [kind, setKind] = useState("note");
  const [summary, setSummary] = useState("");

  return (
    <div>
      <div className="field-label">
        Application timeline · applied {fmtTime(app.applied_at)}
        {app.responded && ` · first reply after ${app.days_to_response} day(s)`}
      </div>
      <ul style={{ margin: "4px 0 10px", paddingLeft: 18, fontSize: 12 }}>
        {app.events.map((e) => (
          <li key={e.id} style={{ marginBottom: 3 }}>
            <strong style={{ color: e.is_response ? "#3fb950" : undefined }}>{e.kind}</strong>
            {" · "}<span className="muted">{fmtTime(e.occurred_at)}</span>
            {/* Provenance shown always: today everything is `human`, and the moment Gmail or an
                ATS poller starts writing here the operator can see which rows they wrote. */}
            {" · "}<span className="muted">{e.source}</span>
            {e.summary && <> — {e.summary}</>}
          </li>
        ))}
      </ul>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <select className="form-select" value={kind} onChange={(e) => setKind(e.target.value)}>
          {(vocab?.kinds || []).map((k) => (
            <option key={k.kind} value={k.kind}>{k.kind}{k.is_response ? " (reply)" : ""}</option>
          ))}
        </select>
        <input className="form-input" style={{ flex: "1 1 200px" }} value={summary}
               onChange={(e) => setSummary(e.target.value)} placeholder="What happened? (optional)" />
        <button className="btn btn-sm" disabled={busy}
                onClick={() => { onAdd({ kind, source: "human", summary }); setSummary(""); }}>
          Add
        </button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------
// The duplicate review queue
// --------------------------------------------------------------------------------------

function Duplicates({ onChange }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(null);

  const load = useCallback(() => {
    getJSON("/api/career_search/duplicates").then(setData).catch((e) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const decide = (id, action, body) => {
    setBusy(id);
    postJSON(`/api/career_search/duplicates/${id}/${action}`, body || {})
      .then(() => { load(); onChange?.(); })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(null));
  };

  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="panel-header"><div>Possible duplicates <span className="muted">({data?.total ?? "…"})</span></div></div>
      <p className="system-micro-copy" style={{ margin: "0 12px 10px" }}>
        Only a shared requisition id merges on its own — everything else asks, because a wrong merge
        silently hides a job you picked. Saying "different" is remembered, so the pair never comes back.
      </p>
      {err && <p className="mode-hint" style={{ color: "#f85149", margin: "0 12px 10px" }}>{err}</p>}
      {data?.total === 0 && <p className="empty-state" style={{ margin: "0 12px 14px" }}>Nothing waiting on you.</p>}

      <div style={{ display: "grid", gap: 10, padding: "0 12px 12px" }}>
        {(data?.duplicates || []).map((d) => (
          <div key={d.id} style={{ border: "1px solid rgba(127,127,127,0.25)", borderRadius: 6, padding: 10 }}>
            <div className="stat-footnote" style={{ marginBottom: 6 }}>
              <span className="badge badge--muted">{d.tier}</span>{" "}
              {(d.evidence || []).join(" · ")}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, fontSize: 12 }}>
              <SideBySide label="Keep" job={d.keep} />
              <SideBySide label="Fold in" job={d.fold} />
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
              <button className="btn btn-sm btn-primary" disabled={busy === d.id}
                      onClick={() => decide(d.id, "merge")}>Same job — merge</button>
              <button className="btn btn-sm" disabled={busy === d.id}
                      onClick={() => decide(d.id, "merge", { keep: d.fold.job_key })}>
                Merge, keep the right one
              </button>
              <button className="btn btn-sm" disabled={busy === d.id}
                      onClick={() => decide(d.id, "reject")}>Different jobs</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SideBySide({ label, job }) {
  return (
    <div>
      <div className="field-label">{label}</div>
      <div><strong>{job.title || "—"}</strong></div>
      <div className="muted">{job.company || "— no employer scraped —"}</div>
      <div className="muted">
        {(job.source_platforms || []).join(", ") || "—"} · seen {job.seen_count}×
        {job.has_description && " · has description"}
      </div>
      <div className="muted">{job.location || ""}</div>
    </div>
  );
}

// --------------------------------------------------------------------------------------
// The inbox sweep — outcomes read off Gmail, ambiguous matches held for a human
// --------------------------------------------------------------------------------------

function InboxQueue({ vocab, onChange }) {
  const [data, setData] = useState(null);
  const [sweep, setSweep] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(null);

  const load = useCallback(() => {
    getJSON("/api/career_search/inbox").then(setData).catch((e) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const runSweep = () => {
    setBusy("sweep");
    setSweep(null);
    postJSON("/api/career_search/inbox/sweep", {})
      .then((r) => { setSweep(r); load(); onChange?.(); })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(null));
  };

  const resolve = (id, body) => {
    setBusy(id);
    postJSON(`/api/career_search/inbox/${id}/resolve`, body)
      .then(() => { load(); onChange?.(); })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(null));
  };

  const queue = (data?.emails || []).filter((e) => e.status === "needs_review");
  const done = (data?.emails || []).filter((e) => e.status !== "needs_review");

  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="panel-header">
        <div>Gmail inbox <span className="muted">({queue.length} waiting)</span></div>
        <button className="btn btn-sm btn-primary" disabled={busy === "sweep"} onClick={runSweep}>
          {busy === "sweep" ? "Sweeping…" : "Sweep inbox"}
        </button>
      </div>
      <p className="system-micro-copy" style={{ margin: "0 12px 10px" }}>
        Reads the open Gmail tab's subject lines (never the mail itself) and writes what it is sure
        of to the application timelines — confirmations and clearly-worded rejections. Anything a
        human should glance at lands here instead. Re-sweeping an unchanged inbox writes nothing.
      </p>
      {err && <p className="mode-hint" style={{ color: "#f85149", margin: "0 12px 10px" }}>{err}</p>}
      {sweep && (
        <p className="stat-footnote" style={{ margin: "0 12px 10px" }}>
          {sweep.ok
            ? `Swept ${sweep.read} mails: ${sweep.recorded?.length || 0} recorded, `
              + `${sweep.needs_review?.length || 0} for review, ${sweep.ignored} not ours, `
              + `${sweep.skipped_known} already seen.`
            : `Blocked: ${sweep.blocked}`}
        </p>
      )}
      {data && queue.length === 0 && (
        <p className="empty-state" style={{ margin: "0 12px 14px" }}>Nothing waiting on you.</p>
      )}

      <div style={{ display: "grid", gap: 10, padding: "0 12px 12px" }}>
        {queue.map((row) => (
          <InboxReviewRow key={row.id} row={row} vocab={vocab}
                          busy={busy === row.id} onResolve={(body) => resolve(row.id, body)} />
        ))}
      </div>

      {done.length > 0 && (
        <div style={{ padding: "0 12px 12px" }}>
          <div className="field-label">Recently resolved</div>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 12 }}>
            {done.slice(0, 12).map((row) => (
              <li key={row.id} className="muted" style={{ marginBottom: 2 }}>
                <span className="badge badge--muted">{row.status}</span>{" "}
                {row.subject || "(no subject kept)"}
                {row.kind && <> — <strong>{row.kind}</strong></>}
                {row.decided_by && <> · {row.decided_by}</>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function InboxReviewRow({ row, vocab, busy, onResolve }) {
  // Prefills come from the matcher; the human may override either before confirming. Kinds are
  // rendered from the server vocabulary, never a hardcoded list — this queue introduces new kinds
  // and must be the first consumer that cannot drift from the validator.
  const [kind, setKind] = useState(row.kind || "");
  const [jobKey, setJobKey] = useState(row.job_key || "");
  const [note, setNote] = useState("");

  return (
    <div style={{ border: "1px solid rgba(127,127,127,0.25)", borderRadius: 6, padding: 10, fontSize: 12 }}>
      <div><strong>{row.subject || "—"}</strong></div>
      <div className="muted" style={{ marginBottom: 4 }}>
        {row.sender_name || row.from_address}
        {row.sender_name && row.from_address && <> · {row.from_address}</>}
        {row.ats_id && <> · <span className="badge badge--muted">{row.ats_id}</span></>}
        {row.received_at && <> · {fmtTime(row.received_at)}</>}
      </div>
      {row.snippet && <div className="muted" style={{ marginBottom: 6 }}>{row.snippet}</div>}
      <div className="stat-footnote" style={{ marginBottom: 6 }}>
        {(row.reasons || []).join(" · ")}
      </div>

      {(row.candidates || []).length > 0 ? (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
          {row.candidates.map((c) => (
            <button key={c.job_key}
                    className={c.job_key === jobKey ? "btn btn-sm btn-primary" : "btn btn-sm"}
                    onClick={() => setJobKey(c.job_key)}
                    title={(c.reasons || []).join(" · ")}>
              {c.company} — {c.title}
            </button>
          ))}
        </div>
      ) : (
        <input className="form-input" style={{ marginBottom: 6, width: "100%" }} value={jobKey}
               onChange={(e) => setJobKey(e.target.value)}
               placeholder="No application matched — paste a job key to file it, or dismiss" />
      )}

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <select className="form-select" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">what happened?</option>
          {(vocab?.kinds || []).map((k) => (
            <option key={k.kind} value={k.kind}>{k.kind}{k.is_response ? " (reply)" : ""}</option>
          ))}
        </select>
        <button className="btn btn-sm btn-primary" disabled={busy || !kind || !jobKey}
                onClick={() => onResolve({ action: "confirm", kind, job_key: jobKey })}>
          Record it
        </button>
        <input className="form-input" style={{ flex: "1 1 140px" }} value={note}
               onChange={(e) => setNote(e.target.value)} placeholder="why not? (optional)" />
        <button className="btn btn-sm" disabled={busy}
                onClick={() => onResolve({ action: "dismiss", note })}>
          Not ours
        </button>
      </div>
    </div>
  );
}
