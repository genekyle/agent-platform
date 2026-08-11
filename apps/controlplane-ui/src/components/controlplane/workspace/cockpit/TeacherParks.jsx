import { useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { postJSON } from "../api";

// THE TEACHER'S SEAT, ON THE SURFACE. A parked drive is frozen mid-question: the controller asked
// (review / instruct / takeover), and until now the cockpit could only COUNT the questions
// ("N waiting on you") while the answers lived behind an endpoint only session-Claude called
// (`POST /api/controller/teacher/{id}/respond` — zero UI callers, 2026-08-10 audit). The human is
// the top of the escalation ladder; the cockpit is their seat; so the seat answers here.
//
// Kinds, mirroring controller/inbox.py:
//   review   — "here is my proposal, approve or correct it"  → Approve / Correct / Escalate
//   instruct — "give me ONE bounded action, I'll verify it"  → an intent+params+rationale form
//   takeover — "take the wheel until a stop condition"       → I drove it — resume / Escalate
//
// A correction or instruction REQUIRES a real rationale — the WHY is the training signal, and the
// server rejects boilerplate. Approve rides with whatever note you give it.

function ParkCard({ park, onAnswered }) {
  const [mode, setMode] = useState(null);      // null | "correct" | "instruct"
  const [draft, setDraft] = useState({ intent: "", params: "{}", rationale: "", newTab: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const answer = async (action, body = {}) => {
    setBusy(true);
    setErr("");
    try {
      const res = await postJSON(`/api/controller/teacher/${park.id}/respond`,
                                 { action, ...body });
      if (res.ok === false) { setErr(res.detail || "the answer was refused"); return; }
      onAnswered(park.id);
    } catch (e) {
      setErr(e.message || "the answer did not send");
    } finally {
      setBusy(false);
    }
  };

  const sendDecision = (action) => {
    let params;
    try { params = draft.params ? JSON.parse(draft.params) : {}; }
    catch { setErr('params must be JSON, e.g. {"field": "Work authorization", "value": "Yes"}'); return; }
    if (!draft.intent.trim()) { setErr("an instructed action needs an intent"); return; }
    if (draft.rationale.trim().length < 12) {
      setErr("give the reason — the rationale is the training signal, and the server refuses boilerplate");
      return;
    }
    answer(action, {
      decision: { intent: draft.intent.trim(), params },
      rationale: draft.rationale.trim(),
    });
  };

  const pred = park.prediction || {};
  return (
    <div className="teacher-park">
      <div className="teacher-park__head">
        <span className="badge badge--warn">{park.kind}</span>
        {park.state && <code>{park.state}</code>}
        {park.task && <span className="badge badge--muted">{park.task}</span>}
        {park.ts && <small>{String(park.ts).slice(11, 19)}</small>}
      </div>
      {park.authority_reason && <p className="rung__meta">{park.authority_reason}</p>}
      {park.url && <p className="rung__meta"><code>{park.url.slice(0, 110)}</code></p>}
      {pred.intent && (
        <p className="rung__meta">
          Local proposal: <code>{pred.intent}</code>
          {Object.keys(pred.params || {}).length > 0 &&
            <> · {Object.entries(pred.params).map(([k, v]) => `${k}: ${v}`).join(" · ")}</>}
          {pred.confidence !== undefined && <> · conf {Number(pred.confidence).toFixed(2)}</>}
        </p>
      )}
      {(park.reach_gaps || []).length > 0 && (
        <p className="rung__meta">Reach gaps: {park.reach_gaps.join(" · ")}</p>
      )}
      {(park.stop_conditions || []).length > 0 && (
        <p className="rung__meta">Stops when: {park.stop_conditions.join(" · ")}</p>
      )}
      {park.bundle_prompt && (
        <details className="work__more">
          <summary>The frozen bundle the drive decided from</summary>
          <pre className="teacher-park__bundle">{park.bundle_prompt}</pre>
        </details>
      )}
      {err && <div className="coaching-error">{err}</div>}

      {mode ? (
        <div className="cv-correct">
          <input placeholder="intent (click / set_text / select_option / check_group …)"
                 value={draft.intent} disabled={busy}
                 onChange={(e) => setDraft((d) => ({ ...d, intent: e.target.value }))} />
          <input placeholder='params, e.g. {"field": "Work authorization", "value": "Yes"}'
                 value={draft.params} disabled={busy}
                 onChange={(e) => setDraft((d) => ({ ...d, params: e.target.value }))} />
          <textarea className="work-note" rows={2} value={draft.rationale} disabled={busy}
                    placeholder="Why this action — cite what the page shows. The server refuses an empty why."
                    onChange={(e) => setDraft((d) => ({ ...d, rationale: e.target.value }))} />
          <div className="work__actions">
            <button className="btn btn-sm" disabled={busy}
                    onClick={() => sendDecision(mode === "correct" ? "correct" : "instruct")}>
              {mode === "correct" ? "Send the correction" : "Send the instruction"}
            </button>
            <button className="btn btn-sm btn-ghost" disabled={busy}
                    onClick={() => { setMode(null); setErr(""); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="work__actions">
          {park.kind === "review" && (
            <>
              <button className="btn btn-sm" disabled={busy || !pred.intent} aria-label="Approve"
                      title="Act the local proposal as it stands"
                      onClick={() => answer("approve", { rationale: "operator approved from the cockpit seat" })}>
                Approve
              </button>
              <button className="btn btn-sm" disabled={busy} aria-label="Correct"
                      onClick={() => setMode("correct")}
                      title="Act a different action instead — journals a golden correction pair">
                Correct…
              </button>
            </>
          )}
          {park.kind === "instruct" && (
            <button className="btn btn-sm" disabled={busy} aria-label="Instruct"
                    onClick={() => { setMode("instruct");
                      setDraft((d) => ({ ...d, intent: pred.intent || "",
                        params: JSON.stringify(pred.params || {}) })); }}
                    title="Give the drive ONE bounded action; it performs and verifies it">
              Instruct…
            </button>
          )}
          {park.kind === "takeover" && (
            <>
              <span className="form-census__type">
                <input placeholder="new tab id, if the work moved (optional)"
                       value={draft.newTab} disabled={busy}
                       onChange={(e) => setDraft((d) => ({ ...d, newTab: e.target.value }))} />
              </span>
              <button className="btn btn-sm" disabled={busy} aria-label="I drove it — resume"
                      title="You drove it by hand to a checkpoint — the drive resumes locally"
                      onClick={() => answer("takeover_done",
                        { note: draft.rationale || "operator drove it from the cockpit",
                          new_tab_id: draft.newTab.trim() })}>
                I drove it — resume
              </button>
            </>
          )}
          <button className="btn btn-sm btn-ghost" disabled={busy} aria-label="Escalate"
                  title="Not answerable from here — hand it further up; the drive ends cleanly"
                  onClick={() => answer("escalate", { note: "escalated from the cockpit seat" })}>
            Escalate
          </button>
        </div>
      )}
    </div>
  );
}

export default function TeacherParks({ parks, sessionId, onAnswered }) {
  const [answered, setAnswered] = useState([]);
  const open = (parks || []).filter((pk) => !answered.includes(pk.id));
  if (!open.length) return null;
  return (
    <div className="teacher-parks">
      <div className="teacher-parks__head">
        <AppIcon name="user" size={14} />
        <strong>{open.length} question{open.length > 1 ? "s" : ""} parked for the teacher</strong>
        <span>the drive is frozen until each is answered — timeout escalates, never approves</span>
      </div>
      {open.map((pk) => (
        <div key={pk.id}>
          {String(pk.session_id || "") !== String(sessionId) && (
            <span className="badge badge--muted"
                  title="This park came from a run keyed to another session id — it is still yours to answer">
              run: {pk.session_id || "unscoped"}
            </span>
          )}
          <ParkCard park={pk}
                    onAnswered={(id) => { setAnswered((a) => [...a, id]); onAnswered?.(id); }} />
        </div>
      ))}
    </div>
  );
}
