import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { postJSON } from "../api";

// CLOSE OUT — the cleanup protocol's cockpit end (parity rule: the endpoint exists, so the
// operator can press it). Ends the session completely and on the record: unfinished applications
// flagged with the reason, active Search rows closed, the drive latch released, the session's
// Chrome stopped — and the signed-in profile KEPT, because the sign-in is the savings account.
//
// The confirm lists exactly what dies before anything does — the same clean-start rule: work
// that may hold somebody's half-finished application never dies silently.

export default function CloseOut({ sessionId, panel, onClosed }) {
  const [arming, setArming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);
  const [err, setErr] = useState("");

  const steps = panel?.queue?.steps || [];
  const dying = [
    ...steps.filter((s) => !s.done || (s.terminal || "").startsWith("parked:"))
      .map((s) => ({ job_id: s.job_id, title: s.title, state: s.done ? s.terminal : "in flight" })),
    ...(panel?.parked || []).filter((p) => !p.in_current_queue)
      .map((p) => ({ job_id: p.job_id, title: p.title, state: p.terminal || "parked" })),
  ];

  const close = async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await postJSON(`/api/session_control/${sessionId}/close_out`, {
        confirm_discards_work: dying.length > 0,
        reason: reason.trim() || "closed out from the cockpit",
        initiator: "operator",
      });
      setReport(d);
      onClosed?.(d);
    } catch (e) {
      setErr(e.message || "the close-out failed");
    } finally {
      setBusy(false);
    }
  };

  if (report) {
    return (
      <div className="close-out">
        <div className="close-out__head">
          <AppIcon name="checkCircle" size={13} /> Session closed out
        </div>
        <p className="rung__meta">{report.detail}</p>
        {(report.discarded || []).length > 0 && (
          <p className="rung__meta">
            Discarded on the record:{" "}
            {report.discarded.map((d) => d.title || d.job_id).join(" · ")}
          </p>
        )}
      </div>
    );
  }

  if (!arming) {
    return (
      <div className="close-out close-out--quiet">
        <button className="btn btn-sm btn-ghost" aria-label="Close out this session"
                title="End this session completely: unfinished applications flagged with your reason, searches closed, the Chrome stopped. The signed-in profile is kept."
                onClick={() => setArming(true)}>
          Close out this session
        </button>
      </div>
    );
  }

  return (
    <div className="close-out">
      <div className="close-out__head">
        <AppIcon name="alert" size={13} /> Close out session #{sessionId}
      </div>
      {dying.length > 0 ? (
        <p className="cv-blocked">
          This discards {dying.length} half-finished application{dying.length === 1 ? "" : "s"}:{" "}
          {dying.map((d) => `${d.title || d.job_id} (${d.state})`).join(" · ")} — each is flagged
          abandoned with your reason, never dropped silently.
        </p>
      ) : (
        <p className="rung__meta">
          Nothing half-finished here — searches close, the Chrome stops, the sign-in stays.
        </p>
      )}
      {err && <div className="coaching-error">{err}</div>}
      <textarea className="work-note" rows={2} value={reason} disabled={busy}
                placeholder="Why this session is ending — rides into every discarded application's record."
                onChange={(e) => setReason(e.target.value)} />
      <div className="work__actions">
        <button className="btn btn-sm btn-primary" disabled={busy} aria-label="Close it out"
                onClick={close}>
          {busy ? "…" : dying.length ? `Discard ${dying.length} and close` : "Close it out"}
        </button>
        <button className="btn btn-sm" disabled={busy} onClick={() => setArming(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
