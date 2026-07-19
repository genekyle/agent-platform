import { useEffect, useMemo, useState } from "react";
import { getJSON, fmtTime } from "./workspace/api";

// One live timeline of what the system is doing and WHY — the operator's window into OUR system,
// the way the chat window is the window into Claude's. Merges reasoning (the open brain), actions,
// events, escalations, errors, and API touches (GET /api/activity). Career Search scoped.
const KIND = {
  reasoning: { color: "#a855f7", label: "reasoning" },
  action: { color: "#2f6feb", label: "action" },
  event: { color: "#64748b", label: "event" },
  escalation: { color: "#f59e0b", label: "escalate" },
  error: { color: "#dc2626", label: "error" },
  api: { color: "#0891b2", label: "api" },
};
const FEED_KINDS = ["reasoning", "action", "escalation", "error", "event"];

function Chip({ k, active, onToggle }) {
  const s = KIND[k];
  return (
    <button
      onClick={() => onToggle(k)}
      className="mono"
      style={{
        fontSize: 11, padding: "3px 9px", borderRadius: 999, cursor: "pointer",
        border: `1px solid ${active ? s.color : "#e2e8f0"}`,
        background: active ? `${s.color}1a` : "transparent", color: active ? s.color : "#94a3b8",
      }}
    >
      {s.label}
    </button>
  );
}

function Entry({ e }) {
  const s = KIND[e.kind] || KIND.event;
  const meta = e.meta || {};
  const chips = [];
  if (meta.rung) chips.push(`rung:${meta.rung}`);
  if (meta.state) chips.push(meta.state);
  if (meta.ats) chips.push(meta.ats);
  if (meta.outcome && meta.outcome !== "ok") chips.push(meta.outcome);
  if (Array.isArray(meta.evidence) && meta.evidence.length) chips.push(`cites ${meta.evidence.length}`);
  if (e.source && !["reasoning", "action"].includes(e.source)) chips.push(e.source);
  if (e.session) chips.push(`sess:${e.session}`);
  return (
    <div style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: "1px solid #f1f5f9" }}>
      <div style={{ width: 4, borderRadius: 2, background: s.color, flex: "0 0 auto" }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
          <span className="mono" style={{ fontSize: 10, color: s.color, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.4 }}>
            {s.label}
          </span>
          <strong className="mono" style={{ fontSize: 13 }}>{e.title}</strong>
          <span className="system-micro-copy" style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>{fmtTime(e.ts)}</span>
        </div>
        {e.detail ? (
          <div style={{ fontSize: 12.5, color: "#475569", marginTop: 2, fontStyle: e.kind === "reasoning" ? "italic" : "normal" }}>
            {e.detail}
          </div>
        ) : null}
        {chips.length ? (
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 4 }}>
            {chips.map((c, i) => (
              <span key={i} className="mono" style={{ fontSize: 10, background: "#0f172a08", color: "#64748b", borderRadius: 4, padding: "1px 5px" }}>{c}</span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function SessionActivitySection() {
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState(null);
  const [paused, setPaused] = useState(false);
  const [active, setActive] = useState(new Set(FEED_KINDS));
  const [includeApi, setIncludeApi] = useState(false);
  const [q, setQ] = useState("");

  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await getJSON(`/api/activity?domain=career_search&limit=250${includeApi ? "&include_api=true" : ""}`);
        if (alive) {
          setEntries(d.entries || []);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const t = paused ? null : setInterval(tick, 5000);
    return () => {
      alive = false;
      if (t) clearInterval(t);
    };
  }, [includeApi, paused, reloadKey]);

  const toggle = (k) =>
    setActive((prev) => {
      const n = new Set(prev);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  const shown = useMemo(
    () => entries.filter((e) => active.has(e.kind) && (!q || `${e.title} ${e.detail || ""}`.toLowerCase().includes(q.toLowerCase()))),
    [entries, active, q],
  );

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>🩺 Session Activity <span className="system-micro-copy">Career Search</span></h2>
            <p>
              One live timeline of what the system is doing and <strong>why</strong> — reasoning (the open
              brain), actions, escalations, errors, and the API points being touched. Your window into our
              system, the way the chat is the window into mine.
            </p>
          </div>
          <div className="controller-actions">
            <button className="ghost-btn small-btn" onClick={() => setPaused((p) => !p)}>{paused ? "▶ Resume" : "⏸ Pause"}</button>
            <button className="ghost-btn small-btn" onClick={() => setReloadKey((k) => k + 1)}>Refresh</button>
          </div>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 4 }}>
          {FEED_KINDS.map((k) => <Chip key={k} k={k} active={active.has(k)} onToggle={toggle} />)}
          <label className="system-micro-copy" style={{ display: "flex", gap: 5, alignItems: "center", marginLeft: 6 }}>
            <input
              type="checkbox"
              checked={includeApi}
              onChange={(e) => {
                setIncludeApi(e.target.checked);
                setActive((prev) => {
                  const n = new Set(prev);
                  if (e.target.checked) n.add("api");
                  else n.delete("api");
                  return n;
                });
              }}
            />
            API touches
          </label>
          <input
            className="mono"
            placeholder="filter…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ marginLeft: "auto", flex: "0 1 220px", padding: "5px 8px" }}
          />
        </div>
      </section>

      <section className="panel">
        <div className="system-micro-copy" style={{ marginBottom: 4 }}>
          {shown.length} of {entries.length} entries {paused ? "· paused" : "· live (5s)"}
        </div>
        {shown.length ? (
          shown.map((e, i) => <Entry key={i} e={e} />)
        ) : (
          <div className="empty-state">
            No activity in this view yet. Drive a login/apply, or toggle more kinds on (and “API touches” to
            watch the endpoints being hit).
          </div>
        )}
      </section>
    </div>
  );
}
