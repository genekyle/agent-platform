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

  // TWO EXITS, because they mean opposite things about the work. Shutting the session down at the
  // end of a sitting is routine; deciding its half-finished applications are over is not, and
  // welding them together made the routine press the dangerous one — so it stopped being pressed
  // (operator, 2026-08-13: "make sure that always gets done when closing"). `keepWork` shuts the
  // session down and leaves the applications resumable, exactly as RETIRE does on the start-fresh
  // side; the discard stays one press away for when it is what the operator means.
  const close = async (keepWork) => {
    setBusy(true);
    setErr("");
    try {
      const d = await postJSON(`/api/session_control/${sessionId}/close_out`, {
        keep_work: !!keepWork,
        confirm_discards_work: !keepWork && dying.length > 0,
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
        {/* The drive-end inbox sweep's account. A blocked sweep MUST be visible: the whole point
            of the automatic crank is outcomes landing without anyone pressing a button, and a
            silently-dead crank looks identical to an empty mailbox (review catch). */}
        {report.inbox_sweep && (
          <p className="rung__meta">
            {report.inbox_sweep.ok
              ? `Inbox swept: ${(report.inbox_sweep.recorded || []).length} outcome(s) recorded, `
                + `${(report.inbox_sweep.needs_review || []).length} for review, `
                + `${report.inbox_sweep.skipped_known ?? 0} already seen.`
              : `Inbox sweep blocked — ${report.inbox_sweep.blocked}`}
          </p>
        )}
        {(report.discarded || []).length > 0 && (
          <p className="rung__meta">
            Discarded on the record:{" "}
            {report.discarded.map((d) => d.title || d.job_id).join(" · ")}
          </p>
        )}
        {(report.kept || []).length > 0 && (
          <p className="rung__meta">
            Kept, resumable: {report.kept.map((d) => d.title || d.job_id).join(" · ")}
          </p>
        )}
        {/* What the shutdown closed, named. A window can hold tabs nobody logged — the operator's
            own browsing included — and a tidy-up that lists nothing looks identical to one that
            quietly threw something away. */}
        {(report.tabs_at_close || []).length > 0 && (
          <details className="rung__meta">
            <summary>{report.tabs_at_close.length} tab(s) were open at close</summary>
            <ul>{report.tabs_at_close.map((u) => <li key={u}><code>{u}</code></li>)}</ul>
          </details>
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
        <p className="rung__meta">
          {dying.length} application{dying.length === 1 ? " is" : "s are"} half-finished:{" "}
          {dying.map((d) => `${d.title || d.job_id} (${d.state})`).join(" · ")}. Shutting down
          keeps {dying.length === 1 ? "it" : "them"} on the ledger, resumable. Discarding flags
          {dying.length === 1 ? " it" : " each"} abandoned with your reason — never silently.
        </p>
      ) : (
        <p className="rung__meta">
          Nothing half-finished here — searches close, the Chrome stops, the sign-in stays.
        </p>
      )}
      {err && <div className="coaching-error">{err}</div>}
      <textarea className="work-note" rows={2} value={reason} disabled={busy}
                placeholder="Why this session is ending — rides into the record either way."
                onChange={(e) => setReason(e.target.value)} />
      <div className="work__actions">
        {/* The routine end-of-sitting press is the SAFE one, and it is the primary. */}
        <button className="btn btn-sm btn-primary" disabled={busy}
                aria-label="Shut the session down and keep the work"
                title="Stops the Chrome, closes this session's searches, releases the drive latch. Half-finished applications stay on the ledger, resumable. The sign-in is kept."
                onClick={() => close(true)}>
          {busy ? "…" : dying.length ? `Shut down · keep ${dying.length}` : "Shut it down"}
        </button>
        {dying.length > 0 && (
          <button className="btn btn-sm btn-consequential" disabled={busy}
                  aria-label={`Discard ${dying.length} and close out`}
                  title="Ends the session AND flags every half-finished application abandoned with your reason. Not reversible."
                  onClick={() => close(false)}>
            Discard {dying.length} and close out
          </button>
        )}
        <button className="btn btn-sm" disabled={busy} onClick={() => setArming(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
