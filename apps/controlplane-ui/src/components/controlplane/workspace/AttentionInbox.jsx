import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, fmtTime } from "./api";
import { DOMAIN_CATALOG } from "./domains";

// The Attention layer — the operator's "what needs me" queue, built from durable handoffs
// (why the loop stopped + what it tried). Shared by the Command Center (all domains) and each
// domain workspace (filtered to one host). You interact with the system by clearing these,
// not by micromanaging buttons.

function attributeDomain(row) {
  const hay = `${row.url || ""} ${row.tab_url || ""} ${row.task_goal || ""}`.toLowerCase();
  return DOMAIN_CATALOG.find((d) => d.host && hay.includes(d.host)) || null;
}

export function AttentionInbox({ host = null, title = "Needs your attention", showDomainTag = false }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState("");

  const load = useCallback(() => {
    getJSON("/api/runtime/handoffs?open_only=true&limit=50")
      .then((d) => {
        let rows = d.handoffs || [];
        if (host) {
          rows = rows.filter((r) => {
            const hay = `${r.url || ""} ${r.tab_url || ""} ${r.task_goal || ""}`.toLowerCase();
            return hay.includes(host);
          });
        }
        setItems(rows);
      })
      .catch(() => {});
  }, [host]);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  const resolve = async (id) => {
    setBusy(id);
    try {
      await postJSON(`/api/runtime/handoffs/${id}/resolve`);
      load();
    } catch {
      /* best-effort */
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title">🔔 {title}</div>
        <span className="layer__count">{items.length ? `${items.length} open` : ""}</span>
      </div>

      {items.length === 0 ? (
        <div className="attention-empty">✓ Nothing needs you right now.</div>
      ) : (
        items.map((h) => {
          const dom = showDomainTag ? attributeDomain(h) : null;
          return (
            <div key={h.id} className="attention-item">
              <div style={{ fontSize: 18, lineHeight: 1.2 }}>⚠️</div>
              <div className="attention-item__body">
                <div className="attention-item__why">
                  {dom && <span className="badge badge--muted" style={{ marginRight: 8 }}>{dom.icon} {dom.short}</span>}
                  {h.why || "The agent stopped and needs a human."}
                </div>
                {h.suggestion && <div className="attention-item__hint">{h.suggestion}</div>}
                <div className="attention-item__meta">
                  {h.task_goal ? `${h.task_goal} · ` : ""}{fmtTime(h.ts)}
                  {Array.isArray(h.tried) && h.tried.length ? ` · tried ${h.tried.length} step${h.tried.length === 1 ? "" : "s"}` : ""}
                </div>
              </div>
              <button className="btn btn-sm" disabled={busy === h.id} onClick={() => resolve(h.id)}>
                {busy === h.id ? "…" : "Resolve"}
              </button>
            </div>
          );
        })
      )}
    </div>
  );
}
