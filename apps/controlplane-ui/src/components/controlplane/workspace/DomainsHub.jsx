import { useCallback, useEffect, useState } from "react";
import { getJSON } from "./api";
import { DOMAIN_CATALOG } from "./domains";

// The Domains hub — the landing that replaces "Selling" and "Indeed" sitting loose in the nav.
// One tile per domain, each answering the same glanceable questions: is it healthy, what's the
// headline number, and does it need me. Click a live tile to open its workspace.

const STATUS_TEXT = { ready: "Ready", attention: "Needs attention", idle: "Idle" };
const DOT = { ready: "dot--ready", attention: "dot--attention", idle: "dot--idle" };

function Tile({ domain, tile, onOpen }) {
  const soon = domain.kind === "coming_soon";
  const status = tile?.status || "idle";
  const attention = tile?.attention_count || 0;
  const primary = tile?.primary;

  return (
    <button
      className={`domain-tile ${soon ? "domain-tile--soon" : ""}`}
      onClick={() => !soon && onOpen(domain.id)}
      disabled={soon}
    >
      <div className="domain-tile__head">
        <span className="domain-tile__icon">{domain.icon}</span>
        <div style={{ minWidth: 0 }}>
          <div className="domain-tile__title">{domain.label}</div>
          <div className="domain-tile__blurb">{domain.blurb}</div>
        </div>
      </div>

      {soon ? (
        <div style={{ marginTop: 4 }}><span className="badge badge--muted">Coming soon</span></div>
      ) : (
        <>
          <div className="domain-tile__metrics">
            <div>
              <div className="domain-tile__metric-value">{primary?.value ?? 0}</div>
              <div className="domain-tile__metric-label">{primary?.label || "—"}</div>
            </div>
            <div className="domain-tile__chips">
              {(tile?.chips || []).map((c) => (
                <span key={c.label} className={`chip ${c.warn && c.value ? "warn" : "muted"}`}>{c.value} {c.label}</span>
              ))}
            </div>
          </div>

          {tile?.training && (
            <div className="domain-tile__chips" style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border, #d3dae4)" }}>
              <span className={`chip ${tile.training.to_label ? "warn" : "muted"}`}>🏷️ {tile.training.to_label} to label</span>
              <span className="chip muted">{tile.training.labeled} labeled</span>
            </div>
          )}

          <div className="domain-tile__foot">
            <span className="status-line"><span className={`dot ${DOT[status]}`} /> {STATUS_TEXT[status]}</span>
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              {attention > 0 && <span className="badge badge--warn">⚠ {attention}</span>}
              {tile?.automation_mode && <span className="badge badge--accent">{tile.automation_mode}</span>}
            </span>
          </div>
        </>
      )}
    </button>
  );
}

export function DomainsHub({ onOpenDomain, tilesById }) {
  const [fetched, setFetched] = useState({});

  const load = useCallback(() => {
    if (tilesById) return; // parent supplies the data (Command Center) — don't double-poll
    getJSON("/api/command-center/summary")
      .then((d) => setFetched(Object.fromEntries((d.domains || []).map((t) => [t.id, t]))))
      .catch(() => {});
  }, [tilesById]);
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);

  const byId = tilesById || fetched;

  return (
    <div className="section-body">
      <div className="domain-tiles">
        {DOMAIN_CATALOG.map((d) => (
          <Tile key={d.id} domain={d} tile={byId[d.id]} onOpen={onOpenDomain} />
        ))}
      </div>
    </div>
  );
}
