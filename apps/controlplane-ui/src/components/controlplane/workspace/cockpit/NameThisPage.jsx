import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { API, getJSON, postJSON } from "../api";

// NAME THIS PAGE — the teaching affordance at the exact moment of knowledge.
//
// The lost surface says "we read the page and recognised nothing", and the human looking at it
// usually KNOWS the answer. Until now that knowledge had to travel two tabs away (Trace → find
// the row → correction form) — farthest from the moment it exists. This control labels the
// session's newest transition row right here: the label lands on the same seam the Trace uses
// (`/api/transitions/{key}/correct`), so train-on-label refits the witnesses in the background
// and the next orient can recognise the screen.
//
// THE CAPTURE IS SHOWN BEFORE THE LABEL IS TAKEN. A label anchors to the row's screenshots, not
// to the live window — those are usually the same page in a lost loop, and sometimes not
// (state is context-bound; a hand-navigation since the last act breaks the match). The operator
// confirms against the thumbnail, or declines to label a capture that no longer matches.

export default function NameThisPage({ sessionId, whereabouts }) {
  const [open, setOpen] = useState(false);
  const [row, setRow] = useState(null);
  const [fetchErr, setFetchErr] = useState("");
  const [form, setForm] = useState({ before_state: "", after_state: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const [err, setErr] = useState("");

  const claims = (whereabouts?.witnesses || [])
    .map((w) => w.claim).filter((c) => c && c !== "abstains");

  const openIt = async () => {
    setOpen(true);
    setDone("");
    setErr("");
    try {
      const d = await getJSON(`/api/transitions/${sessionId}`);
      const rows = d.rows || [];
      const newest = rows.length ? rows[rows.length - 1] : null;
      if (!newest) { setFetchErr("The corpus for this session is empty."); return; }
      setRow(newest);
      const beforeBelief = newest.before?.belief?.state || "";
      const afterBelief = newest.after?.belief?.state || "";
      setForm({
        before_state: beforeBelief && beforeBelief !== "unknown" ? beforeBelief : "",
        after_state: afterBelief && afterBelief !== "unknown" ? afterBelief : (claims[0] || ""),
        note: "named from the cockpit's lost surface — the live window shows this screen",
      });
    } catch (e) {
      // 404 = no corpus yet. A label needs a captured row to anchor to — that is a fact worth
      // stating, not a failure to hide.
      setFetchErr(e.message || "no transition corpus for this session yet");
    }
  };

  const teach = async () => {
    setBusy(true);
    setErr("");
    try {
      const res = await postJSON(`/api/transitions/${sessionId}/correct`, {
        index: row.index, ts: row.ts || "",
        note: form.note.trim(),
        before_state: form.before_state.trim(), after_state: form.after_state.trim(),
        by: "operator",
      });
      setDone(res.training_queued
        ? "Labeled — the witnesses refit in the background. Orient again shortly and they should recognise this screen."
        : "Labeled. (Train-on-label is off, so the refit waits for the next training run.)");
      setRow(null);
    } catch (e) {
      setErr(e.message || "the label did not save");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="cv-actions">
        <button className="btn btn-sm" onClick={openIt} aria-label="Name this page — teach it"
                title="You know where we are and the witnesses don't — label the newest captured step so they learn this screen. The label is the training signal.">
          <AppIcon name="sparkle" size={12} /> Name this page — teach it
        </button>
      </div>
    );
  }

  const shot = row?.screenshots?.after;
  return (
    <div className="cv-correct name-page">
      {done && <p className="rung__meta">{done}</p>}
      {fetchErr && !row && (
        <p className="cv-blocked">
          Nothing to anchor a label to yet — {fetchErr} A label attaches to a captured step
          (screenshot + reading); work one step and the row appears.
        </p>
      )}
      {row && (
        <>
          <p className="rung__meta">
            Labeling the newest captured step{row.ts ? ` (${row.ts.slice(0, 19)})` : ""}. Confirm
            the capture matches the window — a label on a stale capture teaches the wrong page.
          </p>
          {shot && (
            <img className="name-page__shot" alt="the captured page this label attaches to"
                 src={`${API}/api/observations/screenshots/${encodeURIComponent(shot)}`} />
          )}
          <div className="name-page__fields">
            <label className="work-field">
              <span>Before the step, this was</span>
              <input value={form.before_state} disabled={busy} list="known-states"
                     placeholder="e.g. indeed_apply_resume_selection"
                     onChange={(e) => setForm((f) => ({ ...f, before_state: e.target.value }))} />
            </label>
            <label className="work-field">
              <span>The page it shows (now) is</span>
              <input value={form.after_state} disabled={busy} list="known-states"
                     placeholder="e.g. indeed_apply_questions"
                     onChange={(e) => setForm((f) => ({ ...f, after_state: e.target.value }))} />
            </label>
            <datalist id="known-states">
              {[...new Set([...claims,
                row.before?.belief?.state, row.after?.belief?.state])].filter(Boolean)
                .map((s) => <option key={s} value={s} />)}
            </datalist>
          </div>
          <textarea className="work-note" rows={2} value={form.note} disabled={busy}
                    placeholder="Why you are sure — cite what the window shows. Required."
                    onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} />
          {err && <div className="coaching-error">{err}</div>}
          <div className="work__actions">
            <button className="btn btn-sm btn-primary" aria-label="Teach it"
                    disabled={busy || !form.before_state.trim() || !form.after_state.trim()
                      || !form.note.trim()}
                    title="Both sides are required — a transition is an edge, and half an edge trains nothing"
                    onClick={teach}>
              {busy ? "…" : "Teach it"}
            </button>
            <button className="btn btn-sm" disabled={busy}
                    onClick={() => { setOpen(false); setRow(null); }}>
              Cancel
            </button>
          </div>
        </>
      )}
      {!row && (fetchErr || done) && (
        <div className="work__actions">
          <button className="btn btn-sm" onClick={() => setOpen(false)}>Close</button>
        </div>
      )}
    </div>
  );
}
