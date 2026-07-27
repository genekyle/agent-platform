import { useCallback, useEffect, useState } from "react";
import { getJSON, fmtTime } from "./api";
import { DOMAINS_BY_ID } from "./domains";
import { AppIcon } from "../../../ui/Icon";

// The Errands tab — where a cross-domain detour stops being invisible.
//
// An errand is the one kind of work whose whole point is to happen somewhere the operator isn't
// looking: a Career Search drive pauses, a Gmail tab gets read, the drive resumes. Without this
// panel the only trace is an unexplained gap in a DIFFERENT domain's timeline, which is exactly
// how "the agent just stopped for a while" gets diagnosed as the wrong problem.
//
// Two things it deliberately shows, because they are the two questions worth asking of an errand:
// WHO asked (an errand with no caller is a drive nobody is waiting on — a bug), and WHICH ONES
// ESCALATED (each of those is another domain parked until a human looks).
//
// It never shows a code. The API never sends one — `errand_log` records the shape of the answer
// and masks the code out of the subject line it quotes, including for codes it rejected.

const STATUS_TONE = {
  ok: "success",
  not_found: "neutral",   // retryable — the mail may not have landed yet, nobody is needed
  ambiguous: "warning",
  blocked: "danger",
};

const STATUS_LABEL = {
  ok: "Code found",
  not_found: "Nothing yet",
  ambiguous: "Refused — ambiguous",
  blocked: "Blocked",
};

function CallerName({ id }) {
  const domain = DOMAINS_BY_ID[id];
  return <span className="activity-row__domain">{domain?.short || id || "unknown"}</span>;
}

function Catalog({ errands }) {
  if (!errands.length) return null;
  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title layer__title--with-icon">
          <AppIcon name="sliders" size={17} /> What this domain offers
        </div>
        <span className="layer__sub">Callable by any domain</span>
      </div>
      {errands.map((e) => (
        <div key={e.errand_id} style={{ padding: "10px 0", borderTop: "1px solid var(--hairline)" }}>
          <div className="domain-tile__title">{e.errand_id}</div>
          <div className="domain-tile__blurb" style={{ marginBottom: 6 }}>
            Served by {e.route?.domain_id || "—"} on the shared{" "}
            <code>{e.route?.profile || "—"}</code> profile.
          </div>
          <ul className="mode-hint" style={{ margin: 0, paddingLeft: 18 }}>
            {(e.guarantees || []).map((g, i) => <li key={i}>{g}</li>)}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function ErrandsPanel({ domain }) {
  const [rows, setRows] = useState([]);
  const [stats, setStats] = useState({ served: 0, ok: 0, escalated: 0 });
  const [catalog, setCatalog] = useState([]);

  const load = useCallback(() => {
    getJSON("/api/errands?limit=40")
      .then((d) => { setRows(d.errands || []); setStats(d.stats || {}); })
      .catch(() => {});
  }, []);

  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);
  useEffect(() => {
    getJSON("/api/errands/catalog").then((d) => setCatalog(d.errands || [])).catch(() => {});
  }, []);

  return (
    <div className="cockpit">
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title layer__title--with-icon">
            <AppIcon name="activity" size={17} /> Errands served
          </div>
          <span className="layer__sub">Last 7 days</span>
        </div>
        <div className="chip-row" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <span className="badge">{stats.served || 0} requested</span>
          <span className="badge badge--success">{stats.ok || 0} answered</span>
          {/* The number that actually matters: every escalation is a drive in another domain
              waiting on a human, so it is styled as a warning even at zero. */}
          <span className={`badge ${stats.escalated ? "badge--danger" : "badge--muted"}`}>
            {stats.escalated || 0} escalated
          </span>
        </div>
      </div>

      <div className="layer">
        <div className="layer__head">
          <div className="layer__title layer__title--with-icon">
            <AppIcon name="activity" size={17} /> Recent errands
          </div>
          <span className="layer__sub">Who asked {domain?.short || "this domain"} for what</span>
        </div>
        {rows.length === 0 ? (
          <div className="empty-hint">
            No errands yet. Another domain calls one when it hits a wall only this one can see —
            e.g. Career Search choosing “sign in with a code instead”.
          </div>
        ) : (
          rows.map((r, i) => (
            <div key={i} className="activity-row" style={{ alignItems: "flex-start" }}>
              <span className={`dot activity-dot activity-dot--${STATUS_TONE[r.status] || "neutral"}`} />
              <span className="activity-row__time">{fmtTime(r.ts)}</span>
              <span className="activity-row__msg">
                <CallerName id={r.requested_by} />{" "}
                asked for <strong>{r.errand_id}</strong> — {STATUS_LABEL[r.status] || r.status}
                {r.reason ? <div className="mode-hint">“{r.reason}”</div> : null}
                {/* The escalation text IS the instruction to the operator — show it in full
                    rather than making them open a detail view to find out what to do. */}
                {r.escalated && r.escalation
                  ? <div className="status-card__reason" style={{ marginTop: 4 }}>{r.escalation}</div>
                  : null}
                {r.evidence?.subject
                  ? <div className="mode-hint" style={{ marginTop: 4 }}>
                      Read from: {r.evidence.subject}
                    </div>
                  : null}
              </span>
            </div>
          ))
        )}
      </div>

      <Catalog errands={catalog} />
    </div>
  );
}
