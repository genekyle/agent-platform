import { useCallback, useEffect, useMemo, useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { fmtTime, getJSON } from "../api";

const POLL_MS = 10000;

function hostOf(url) {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url || "—"; }
}

function iconFor(kind) {
  if (kind.includes("closed")) return "close";
  if (kind.includes("opened")) return "play";
  if (kind.includes("navigate")) return "arrowRight";
  if (kind.includes("recording")) return "eye";
  return "circleDot";
}

export function SessionTrace({ sessionId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [windows, panel, recordings] = await Promise.all([
      getJSON(`/api/session_control/${sessionId}/windows?limit=200`).catch(() => null),
      getJSON(`/api/session_control/${sessionId}`).catch(() => null),
      getJSON(`/api/session_control/${sessionId}/observe`).catch(() => null),
    ]);
    if (!windows && !panel && !recordings) {
      setError("Could not read this session's trace.");
      return;
    }
    setData({ windows, panel, recordings: recordings?.recordings || [] });
    setError("");
  }, [sessionId]);

  useEffect(() => {
    const initial = setTimeout(load, 0);
    const timer = setInterval(load, POLL_MS);
    return () => { clearTimeout(initial); clearInterval(timer); };
  }, [load]);

  const entries = useMemo(() => {
    if (!data) return [];
    const system = (data.panel?.events || []).map((event) => ({
      ts: event.ts,
      kind: event.kind || "system",
      source: "system",
      actor: "recorded step",
      title: event.kind?.replaceAll("_", " ") || "system event",
      detail: event.detail || "",
    }));
    const windows = (data.windows?.timeline || []).map((event) => ({
      ts: event.ts,
      kind: `window ${event.kind}`,
      source: "window",
      actor: event.actor || "system",
      title: `${event.kind} · ${hostOf(event.url)}`,
      detail: event.note || event.url || "",
      from: event.from_url,
      role: event.role,
    }));
    const recordings = data.recordings.map((recording) => ({
      ts: recording.stored_at,
      kind: "observer recording",
      source: "observer",
      actor: "operator",
      title: recording.note || "Observation recording",
      detail: `${recording.count || 0} events over ${Math.round((recording.duration_ms || 0) / 100) / 10}s`,
    }));
    return [...system, ...windows, ...recordings].sort((a, b) =>
      (Date.parse(b.ts || "") || 0) - (Date.parse(a.ts || "") || 0));
  }, [data]);

  if (!data && !error) return <p className="empty-hint">Building the session trace…</p>;

  const summary = data?.windows?.summary || {};
  return (
    <div className="trace">
      {error && <div className="coaching-error">{error}</div>}

      <section className="trace__summary" aria-label="Trace summary">
        <article><span>System events</span><strong>{data?.panel?.events?.length || 0}</strong><small>recent decisions and actions</small></article>
        <article><span>Window events</span><strong>{data?.windows?.timeline?.length || 0}</strong><small>{summary.open_tabs ?? "?"} tabs open now</small></article>
        <article><span>Observer recordings</span><strong>{data?.recordings?.length || 0}</strong><small>kept interaction windows</small></article>
        <article><span>Last verification</span><strong>{data?.panel?.last_step?.ok === false ? "Mismatch" : data?.panel?.last_step ? "Recorded" : "—"}</strong><small>{data?.panel?.last_step?.action || "no action yet"}</small></article>
      </section>

      <section className="cockpit__pane trace__timeline">
        <div className="cockpit__pane-head">
          <AppIcon name="activity" size={14} /> Observation → decision → action record
          <span className="badge badge--muted">newest first</span>
        </div>
        {entries.length === 0 ? (
          <p className="empty-hint">Nothing has been recorded for this session yet.</p>
        ) : (
          <ol className="trace__entries">
            {entries.map((entry, i) => (
              <li key={`${entry.ts}-${entry.kind}-${i}`} data-source={entry.source}>
                <span className="trace__mark"><AppIcon name={iconFor(entry.kind)} size={12} /></span>
                <div className="trace__body">
                  <div className="trace__line">
                    <strong>{entry.title}</strong>
                    {entry.role && <span className="badge badge--muted">{entry.role}</span>}
                    <span className="badge badge--muted">{entry.actor}</span>
                    <time>{fmtTime(entry.ts)}</time>
                  </div>
                  {entry.detail && <p>{entry.detail}</p>}
                  {entry.from && <small>from {entry.from}</small>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
