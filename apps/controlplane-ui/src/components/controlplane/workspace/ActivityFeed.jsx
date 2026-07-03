import { fmtTime } from "./api";
import { DOMAINS_BY_ID } from "./domains";

// The Activity / memory layer — a readable trace of what the agents actually did. Presentational:
// the parent hands in an already-fetched, newest-first list of {ts|timestamp, message, status,
// kind, domain_id}. Used per-domain (inventory log) and cross-domain (Command Center feed).

const DOT = { ok: "#16a34a", error: "#dc2626", info: "#0ea5e9", warn: "#ea580c" };

export function ActivityFeed({ items = [], title = "Recent activity", showDomain = false, limit = 18 }) {
  const rows = items.slice(0, limit);
  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title">🧾 {title}</div>
      </div>
      {rows.length === 0 ? (
        <div className="empty-hint">No activity yet.</div>
      ) : (
        rows.map((e, i) => {
          const dom = showDomain && e.domain_id ? DOMAINS_BY_ID[e.domain_id] : null;
          return (
            <div key={e.id || i} className="activity-row">
              <span className="dot" style={{ background: DOT[e.status] || "#94a3b8", marginTop: 6 }} />
              <span className="activity-row__time">{fmtTime(e.ts || e.timestamp)}</span>
              <span className="activity-row__msg">
                {dom && <span className="activity-row__domain" style={{ marginRight: 6 }}>{dom.short}</span>}
                {e.message || e.kind || "—"}
              </span>
            </div>
          );
        })
      )}
    </div>
  );
}
