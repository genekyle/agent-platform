import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON } from "./api";
import { AppIcon } from "../../../ui/Icon";

// The domain's own terminal — what just happened, in this domain, newest at the BOTTOM.
//
// Two deliberate differences from the system-wide EventsConsole:
//   * It is SCOPED. `/api/activity?domain=<id>` returns only this engine's rows; a group (Career
//     Search) also collects its members', an engine does not collect its siblings'. A feed titled
//     "LinkedIn" that fills with rows from an Indeed drive is worse than an empty one.
//   * It reads like a terminal, because that is what it is used as: oldest→newest, monospace,
//     pinned to the bottom, with the single most recent line called out on top. Mid-drive the
//     question is always "what did it just do", and a reverse-chronological table answers it
//     one scroll too late.
//
// Auto-scroll follows only while the operator is already at the bottom — scrolling up to read
// something is an intent, and yanking them back on the next 4s poll would make the panel unusable
// exactly when it matters.

const KIND_STYLE = {
  reasoning: { c: "var(--accent, #58a6ff)", t: "think" },
  action: { c: "var(--success, #3fb950)", t: " act " },
  escalation: { c: "var(--danger, #f85149)", t: "STOP " },
  error: { c: "var(--danger, #f85149)", t: "error" },
  event: { c: "var(--text-subtle, #8b949e)", t: "event" },
  api: { c: "var(--text-subtle, #8b949e)", t: " api " },
};

function clock(ts) {
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? "--:--:--"
    : d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function ago(ts) {
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (!Number.isFinite(s)) return "";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function DomainTerminal({ domain, limit = 200 }) {
  const [entries, setEntries] = useState([]);
  const [live, setLive] = useState(true);
  const [err, setErr] = useState("");
  const [kind, setKind] = useState("");
  const bodyRef = useRef(null);
  const pinnedRef = useRef(true);

  const load = useCallback(() => {
    getJSON(`/api/activity?domain=${encodeURIComponent(domain.id)}&limit=${limit}`)
      .then((d) => {
        setErr("");
        // The API is newest-first (every other consumer wants that); a terminal reads the other way.
        setEntries([...(d.entries || [])].reverse());
      })
      .catch((e) => setErr(String(e.message || e)));
  }, [domain.id, limit]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!live) return undefined;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [live, load]);

  // Follow the tail only if they were already at it.
  useEffect(() => {
    const el = bodyRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [entries]);

  const onScroll = () => {
    const el = bodyRef.current;
    if (el) pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const kinds = Array.from(new Set(entries.map((e) => e.kind))).sort();
  const shown = kind ? entries.filter((e) => e.kind === kind) : entries;
  const last = entries[entries.length - 1];

  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title layer__title--with-icon">
          <AppIcon name="terminal" size={17} /> {domain.label} — what just happened
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select className="input" style={{ padding: "4px 8px", width: "auto" }}
            value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">all</option>
            {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <button className="btn btn-sm" onClick={() => setLive((v) => !v)}>{live ? "Pause" : "Resume"}</button>
          <button className="btn btn-sm" onClick={load}>Refresh</button>
        </div>
      </div>

      {/* THE ONE LINE. Mid-drive this is the whole question, so it does not have to be found. */}
      <div style={{
        display: "flex", gap: 10, alignItems: "baseline", padding: "8px 10px", borderRadius: 6,
        background: "rgba(127,127,127,0.08)", marginBottom: 8, minHeight: 34,
      }}>
        <span className="muted" style={{ fontSize: 11, letterSpacing: 0.4 }}>LAST</span>
        {last ? (
          <>
            <strong style={{ color: (KIND_STYLE[last.kind] || {}).c }}>{last.title}</strong>
            <span className="muted" style={{ fontSize: 12, flex: 1, minWidth: 0, overflow: "hidden",
              textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{last.detail}</span>
            <span className="muted" style={{ fontSize: 11 }}>{ago(last.ts)}</span>
          </>
        ) : <span className="muted">Nothing yet — this fills in as the session works.</span>}
      </div>

      {err && <div className="error-banner" style={{ marginBottom: 8 }}>{err}</div>}

      <div
        ref={bodyRef}
        onScroll={onScroll}
        style={{
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12,
          lineHeight: 1.55, maxHeight: 340, overflowY: "auto", overflowX: "hidden",
          padding: "8px 10px", borderRadius: 6, background: "rgba(0,0,0,0.28)",
          border: "1px solid var(--line, #30363d)",
        }}
      >
        {shown.length === 0 && (
          <div className="muted">
            No {kind || "activity"} recorded for {domain.label} yet. Start a session and step — every
            decision, action and stop lands here.
          </div>
        )}
        {shown.map((e, i) => {
          const st = KIND_STYLE[e.kind] || KIND_STYLE.event;
          return (
            <div key={`${e.ts}-${i}`} style={{ display: "flex", gap: 8, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
              <span className="muted">{clock(e.ts)}</span>
              <span style={{ color: st.c }}>{st.t}</span>
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ color: st.c }}>{e.title}</span>
                {e.detail ? <span className="muted">{"  "}{e.detail}</span> : null}
              </span>
            </div>
          );
        })}
      </div>
      <p className="mode-hint" style={{ marginTop: 8, marginBottom: 0 }}>
        Scoped to {domain.label} only. Reasoning, actions, escalations and errors, oldest first —
        {live ? " following the tail every 4s." : " paused."}
      </p>
    </div>
  );
}
