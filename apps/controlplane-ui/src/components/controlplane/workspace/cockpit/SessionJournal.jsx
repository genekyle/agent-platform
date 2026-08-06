import { useCallback, useEffect, useState } from "react";
import { getJSON, fmtTime } from "../api";
import { AppIcon } from "../../../../ui/Icon";

// THE JOURNAL — what this session's window actually did, as a record you can read.
//
// The Live tab answers "what now"; this tab answers "what happened". Its substance is the window
// census (`/api/session_control/{id}/windows`): every tab that opened, navigated or closed, with
// its classified role and who did it. That record used to exist only as an API response — the
// multi-window story ("an apply opens a SECOND tab and navigates it three times") had no surface a
// person could scroll.
//
// This is a RECORD register, like the rail: it renders history and carries no actions. Reading it
// is a ledger read on the API side — no CDP, no bandwidth, safe on a stopped session.

const KIND_ICON = { opened: "play", navigated: "arrowRight", closed: "close" };
const ROLE_TONE = { apply: "accent", search: "ready", errand: "warn", blank: "muted", unknown: "muted" };

function splitUrl(url) {
  try {
    const u = new URL(url);
    return { host: u.host.replace(/^www\./, ""), path: (u.pathname + u.search).slice(0, 90) };
  } catch {
    return { host: url || "—", path: "" };
  }
}

export function SessionJournal({ sessionId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    getJSON(`/api/session_control/${sessionId}/windows?limit=200`)
      .then((d) => { setData(d); setError(""); })
      .catch((e) => setError(e.message || "could not read the journal"));
  }, [sessionId]);

  useEffect(() => {
    load();
    // The journal is history, not the live loop — a slow refresh keeps it current without
    // pretending to be the cockpit's fast eye.
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  if (error) return <div className="coaching-error">{error}</div>;
  if (!data) return <p className="empty-hint">Reading the window record…</p>;

  const s = data.summary || {};
  const timeline = [...(data.timeline || [])].reverse(); // newest first — the recent past is the useful past

  return (
    <div className="journal">
      <div className="cockpit__pane journal__summary">
        <div className="cockpit__pane-head">
          <AppIcon name="boxes" size={14} /> Window census
        </div>
        <div className="journal__chips">
          <span className="badge badge--muted">{s.open_tabs ?? "?"} open now</span>
          <span className="badge badge--muted">{s.windows_opened ?? 0} opened all told</span>
          {Object.entries(s.roles || {}).map(([role, n]) => (
            <span key={role} className={`badge badge--${ROLE_TONE[role] || "muted"}`}>
              {n} {role}
            </span>
          ))}
          {Object.entries(s.events || {}).map(([kind, n]) => (
            <span key={kind} className="badge badge--muted">{n} {kind}</span>
          ))}
        </div>
      </div>

      <div className="cockpit__pane">
        <div className="cockpit__pane-head">
          <AppIcon name="listTree" size={14} /> Timeline
          <span className="badge badge--muted">newest first</span>
        </div>
        {timeline.length === 0 ? (
          <p className="empty-hint">No window events recorded for this session.</p>
        ) : (
          <ul className="journal__timeline">
            {timeline.map((e, i) => {
              const { host, path } = splitUrl(e.url);
              return (
                <li key={i} className="journal__event" data-kind={e.kind}>
                  <span className="journal__event-mark">
                    <AppIcon name={KIND_ICON[e.kind] || "circle"} size={12} />
                  </span>
                  <div className="journal__event-body">
                    <div className="journal__event-line">
                      <strong>{e.kind}</strong>
                      <span className={`badge badge--${ROLE_TONE[e.role] || "muted"}`}>{e.role}</span>
                      <span className="journal__event-host">{host}</span>
                      {/* WHO moved the window. Provenance travels with the record — a tab the
                          system opened and a tab the operator opened are different facts. */}
                      <span className="badge badge--muted">{e.actor || "system"}</span>
                      <span className="journal__event-ts">{fmtTime(e.ts)}</span>
                    </div>
                    {path && <div className="journal__event-path"><code>{path}</code></div>}
                    {e.from_url && (
                      <div className="journal__event-path journal__event-from">
                        from <code>{splitUrl(e.from_url).host}{splitUrl(e.from_url).path}</code>
                      </div>
                    )}
                    {e.note && <div className="journal__event-path">{e.note}</div>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
