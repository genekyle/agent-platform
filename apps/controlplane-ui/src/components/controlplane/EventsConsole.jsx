import { useCallback, useEffect, useState } from "react";
import { getJSON } from "./workspace/api";

// System events console — a live feed of what the agent/system actually did, across BOTH processes
// (control plane + MCP browser driver). Lets the operator confirm at a glance that the system worked
// and is being used + trained (captures, drives, account actions, tabs). Reads GET /api/events.

const KIND_BADGE = {
  capture: "badge--accent", drive: "badge--ok", account: "badge--warn",
  tab: "badge--muted", nav: "badge--muted", apply: "badge--accent",
  label: "badge--ok", search: "badge--accent", error: "badge--warn",
};

function ago(ts) {
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function EventsConsole({ domain = "", limit = 150 }) {
  const [events, setEvents] = useState([]);
  const [paused, setPaused] = useState(false);
  const [kindFilter, setKindFilter] = useState("");

  const load = useCallback(() => {
    getJSON(`/api/events?limit=${limit}`).then((d) => setEvents(d.events || [])).catch(() => {});
  }, [limit]);
  useEffect(() => {
    load();
    if (paused) return undefined;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load, paused]);

  const kinds = Array.from(new Set(events.map((e) => e.kind))).sort();
  const shown = events
    .filter((e) => !domain || !e.domain || String(e.domain).includes(domain))
    .filter((e) => !kindFilter || e.kind === kindFilter);

  return (
    <div className="section-body">
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title">🖥️ System events — what the agent did</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select className="input" style={{ padding: "4px 8px", width: "auto" }} value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
              <option value="">all kinds</option>
              {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
            <button className="btn btn-sm" onClick={() => setPaused((p) => !p)}>{paused ? "▶ Resume" : "⏸ Pause"}</button>
            <button className="btn btn-sm" onClick={load}>Refresh</button>
          </div>
        </div>
        <p className="mode-hint" style={{ marginTop: 0 }}>
          Live feed across the control plane + browser driver — captures, drives, account actions, tabs.
          Auto-refreshes every 5s{paused ? " (paused)" : ""}. {shown.length} shown.
        </p>
      </div>

      <div className="layer">
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>When</th><th>Source</th><th>Kind</th><th>What</th></tr></thead>
            <tbody>
              {shown.map((e, i) => (
                <tr key={`${e.ts}-${i}`}>
                  <td className="muted" style={{ whiteSpace: "nowrap" }} title={e.ts}>{ago(e.ts)}</td>
                  <td><span className="badge badge--muted">{e.source}</span></td>
                  <td><span className={`badge ${KIND_BADGE[e.kind] || "badge--muted"}`}>{e.kind}</span></td>
                  <td>
                    {e.summary}
                    {e.detail && <div className="muted" style={{ fontSize: 11 }}>{e.detail}</div>}
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr><td colSpan={4} className="empty-hint">No events yet — drive something and it'll show here.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
