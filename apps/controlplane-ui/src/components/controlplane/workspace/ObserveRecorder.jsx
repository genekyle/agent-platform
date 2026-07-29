import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "./api";
import { AppIcon } from "../../../ui/Icon";

// The record button — and the log it keeps.
//
// Every other probe in this cockpit answers "what is true now". This one answers "what happened,
// and in what order", which is the only question that helps when an interaction fails in the GAP
// between two snapshots. It exists because four separate mechanisms were invented to explain one
// such gap and all four were wrong; the first 22-second recording settled it.
//
// Deliberately a TOGGLE the operator holds, not something the system starts for itself: a
// MutationObserver on a busy SPA is real overhead, and a recorder you forget is running is worse
// than one you have to remember to start. On while you do the thing, off when you are done.
//
// The recording is KEPT. Reviewing it together is the point, and a window that exists only in one
// reply is a screenshot nobody saved.

const KIND_STYLE = {
  click: { c: "var(--accent, #58a6ff)", label: "click" },
  focus: { c: "var(--success, #3fb950)", label: "focus" },
  blur: { c: "var(--text-subtle, #8b949e)", label: "blur " },
  keydown: { c: "var(--text, #c9d1d9)", label: "key ↓" },
  keyup: { c: "var(--text-subtle, #8b949e)", label: "key ↑" },
  input: { c: "var(--accent, #58a6ff)", label: "input" },
  change: { c: "var(--warn, #d29922)", label: "chnge" },
};

function who(t) {
  if (!t) return "—";
  return t.ph || t.label || t.id || t.tag || "—";
}

function Row({ e }) {
  const st = KIND_STYLE[e.k] || { c: "var(--text-subtle, #8b949e)", label: e.k };
  let detail = "";
  if (e.k === "input" || e.k === "change") detail = `value=${JSON.stringify(e.value ?? "")}`;
  else if (e.k === "keydown" || e.k === "keyup") detail = `key=${JSON.stringify(e.key ?? "")}${e.trusted ? " trusted" : " SYNTHETIC"}`;
  else if (e.k === "click") detail = `${e.trusted ? "trusted" : "SYNTHETIC"} at ${JSON.stringify(e.at || [])}`;
  return (
    <div style={{ display: "flex", gap: 8, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
      <span className="muted" style={{ minWidth: 62, textAlign: "right" }}>{e.t}ms</span>
      <span style={{ color: st.c, minWidth: 46 }}>{st.label}</span>
      <span style={{ minWidth: 0, flex: 1 }}>
        <span style={{ color: st.c }}>{String(who(e.target)).slice(0, 30)}</span>
        {detail ? <span className="muted">{"  "}{detail}</span> : null}
      </span>
    </div>
  );
}

export function ObserveRecorder({ domain, sessionId }) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const [list, setList] = useState([]);
  const [open, setOpen] = useState(null);   // recording id -> detail
  const [detail, setDetail] = useState(null);
  const [elapsed, setElapsed] = useState(0);

  const loadList = useCallback(() => {
    if (!sessionId) return;
    getJSON(`/api/session_control/${sessionId}/observe`)
      .then((d) => setList(d.recordings || [])).catch(() => {});
  }, [sessionId]);
  useEffect(() => { loadList(); }, [loadList]);

  // A running recorder should be impossible to forget: the button counts up while it is on.
  useEffect(() => {
    if (!recording) { setElapsed(0); return undefined; }
    const t0 = Date.now();
    const t = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(t);
  }, [recording]);

  const toggle = async () => {
    if (!sessionId) { setMsg("No active session to record."); return; }
    setBusy(true); setMsg("");
    try {
      const path = recording ? "stop" : "start";
      const r = await postJSON(`/api/session_control/${sessionId}/observe/${path}`, { note });
      setRecording(!!r.recording);
      setMsg(r.detail || "");
      if (!r.recording) { setNote(""); loadList(); }
    } catch (e) {
      setMsg(String(e.message || e));
    } finally { setBusy(false); }
  };

  const openRecording = async (id) => {
    if (open === id) { setOpen(null); setDetail(null); return; }
    setOpen(id); setDetail(null);
    try {
      setDetail(await getJSON(`/api/session_control/${sessionId}/observe/${id}`));
    } catch (e) { setMsg(String(e.message || e)); }
  };

  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title layer__title--with-icon">
          <AppIcon name="inspect" size={17} /> Observe mode — record what changes
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {!recording && (
            <input className="input" style={{ width: 200, padding: "4px 8px" }} value={note}
              placeholder="what are you about to do?"
              onChange={(e) => setNote(e.target.value)} />
          )}
          <button
            className={`btn btn-sm ${recording ? "btn-danger" : "btn-primary"}`}
            disabled={busy || !sessionId}
            onClick={toggle}
          >
            {busy ? "…" : recording ? `■ Stop (${elapsed}s)` : "● Record"}
          </button>
        </div>
      </div>

      <p className="mode-hint" style={{ marginTop: 0 }}>
        Records DOM mutations, focus moves, input/change and keystrokes on {domain?.label || "this domain"}’s
        tab — the things a snapshot cannot show. <strong>Toggle it on, do the thing, toggle it off</strong>;
        it stays off otherwise because it is a diagnostic, not a background service.
        {" "}Passwords and one-time codes are never read — the event is recorded, the value is not.
      </p>

      {msg && <div className="mode-hint" style={{ marginTop: 0 }}>{msg}</div>}

      {list.length === 0 ? (
        <div className="empty-hint">No recordings yet. Press Record, do the thing, press Stop.</div>
      ) : (
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>When</th><th>Note</th><th>Events</th><th>Length</th><th></th></tr></thead>
            <tbody>
              {list.map((r) => (
                <>
                  <tr key={r.id}>
                    <td className="muted">{new Date(r.stored_at).toLocaleTimeString()}</td>
                    <td>{r.note || <span className="muted">—</span>}</td>
                    <td>{r.count}{r.dropped ? <span className="muted"> (+{r.dropped} dropped)</span> : null}</td>
                    <td className="muted">{Math.round((r.duration_ms || 0) / 100) / 10}s</td>
                    <td><button className="btn btn-sm" onClick={() => openRecording(r.id)}>
                      {open === r.id ? "Hide" : "Open"}</button></td>
                  </tr>
                  {open === r.id && (
                    <tr key={`${r.id}-d`}>
                      <td colSpan={5}>
                        {!detail ? <span className="muted">Loading…</span> : (
                          <>
                            <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
                              {Object.entries(detail.kinds || {}).map(([k, n]) => `${k}:${n}`).join("  ")}
                              {"  ·  focus at start: "}{who(detail.active_at_start)}
                              {"  →  at stop: "}{who(detail.active_at_stop)}
                            </div>
                            <div style={{
                              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                              fontSize: 12, lineHeight: 1.5, maxHeight: 320, overflowY: "auto",
                              padding: "8px 10px", borderRadius: 6, background: "rgba(0,0,0,0.28)",
                              border: "1px solid var(--line, #30363d)",
                            }}>
                              {(detail.spine || []).map((e, i) => <Row key={i} e={e} />)}
                              {detail.spine_truncated ? (
                                <div className="muted">…{detail.spine_truncated} more (DOM mutations are kept but not shown)</div>
                              ) : null}
                            </div>
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
