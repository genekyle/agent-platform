import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, fmtTime } from "./api";
import { AppIcon } from "../../../ui/Icon";
import { ContextDrawer } from "./ContextDrawer";

// The Coaching pane — the operator's seat in a running drive.
//
// Two surfaces, split on purpose (redesigned 2026-07-23). The pane itself is a COMPACT QUEUE:
// one row per parked drive — mode, where it is, the one-line reason it stopped, the action it
// wants — so the whole thing is a glance, and the panels below it (the session window, the
// attention inbox) are not shoved a full screen down by an inline wall of context. The full
// picture — where it thinks it is, how sure, what each witness saw, what the page is asking,
// what it intends and why — opens in its own drawer (ContextDrawer), because that much
// information deserves width and a layout, not a cramped column inside a list item.
//
// Load-bearing, not decorative:
//   * Correct is a peer of Go, never quieter. The golden training rows come from CORRECTIONS; a
//     pane whose easy path is always "yes" produces agreement and no signal.
//   * The note field. Situational knowledge ("this ATS always asks twice") was the one input to
//     this system with nowhere to live; a note rides into the acting decision's rationale and
//     lands in the journal as evidence.
//   * Go disables — with its reason shown — when the proposal carries no value, because decide()
//     never invents an answer and approving a valueless set_text would type an empty string.

const MODE_TONE = { green: "ok", yellow: "warn", orange: "warn", red: "bad" };

function section(prompt, name) {
  if (!prompt) return "";
  const re = new RegExp(`# ${name}\\n([\\s\\S]*?)(?=\\n# |$)`);
  return (prompt.match(re)?.[1] || "").trim();
}
function fieldOf(block, key) {
  const line = (block || "").split("\n").find((l) => l.trim().startsWith(`${key}:`));
  return line ? line.split(":").slice(1).join(":").trim() : "";
}

// decide() deliberately never invents an answer value, so its proposal for a text field is a
// shape ("set_text on Job title"), not a bid to act. Approving one would type an empty string.
const NEEDS_A_VALUE = new Set(["set_text", "select_option", "set_date", "check_group", "upload"]);
export function incompleteReason(pred) {
  if (!pred?.intent) return "nothing was proposed to approve";
  const p = pred.params || {};
  if (NEEDS_A_VALUE.has(pred.intent) && !p.value && !p.values && !p.month) {
    return `proposed ${pred.intent} on "${p.field || "a field"}" but no value — it will not invent one. Use Correct.`;
  }
  return "";
}

export function CoachingPane({ title = "Coaching" }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState("");
  const [notes, setNotes] = useState({});
  const [editing, setEditing] = useState({});
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState(null);

  const load = useCallback(() => {
    getJSON("/api/controller/teacher/pending")
      .then((d) => setItems(d.pending || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  const answer = async (req, action, extra = {}) => {
    setBusy(req.id);
    setError("");
    try {
      await postJSON(`/api/controller/teacher/${req.id}/respond`, {
        action,
        note: notes[req.id] || "",
        ...extra,
      });
      setNotes((n) => ({ ...n, [req.id]: "" }));
      setEditing((e) => ({ ...e, [req.id]: null }));
      setOpenId(null);
      load();
    } catch (e) {
      setError(e.message || "could not answer");
    } finally {
      setBusy("");
    }
  };

  const correct = (req) => {
    const draft = editing[req.id];
    if (!draft?.intent) return;
    let params = {};
    try {
      params = draft.params ? JSON.parse(draft.params) : {};
    } catch {
      setError('params must be JSON, e.g. {"control": "Continue"}');
      return;
    }
    const why = (notes[req.id] || "").trim();
    if (why.length < 12) {
      setError("a correction needs a reason — that reasoning is the training signal");
      return;
    }
    answer(req, "correct", {
      decision: { intent: draft.intent, params, confidence: 1.0 },
      rationale: why,
    });
  };

  const openReq = items.find((r) => r.id === openId) || null;

  return (
    <div className="layer">
      <div className="layer__head">
        <div className="layer__title layer__title--with-icon">
          <AppIcon name="waypoints" size={17} /> {title}
        </div>
        <span className="layer__count">{items.length ? `${items.length} waiting` : ""}</span>
      </div>

      {error && <div className="attention-item__hint coaching-error">{error}</div>}

      {items.length === 0 ? (
        <div className="attention-empty">
          <AppIcon name="checkCircle" size={17} /> No drive is waiting on you.
        </div>
      ) : (
        items.map((req) => {
          const state = section(req.bundle_prompt, "STATE");
          const recipe = section(req.bundle_prompt, "RECIPE");
          const belief = req.belief_prompt || "";
          const pred = req.prediction || {};
          const tone = MODE_TONE[req.mode] || "warn";
          const blocked = incompleteReason(pred);
          const recipeState = fieldOf(state, "state");
          const beliefState = fieldOf(belief, "state");
          const disagree = beliefState && recipeState && beliefState !== recipeState;

          return (
            <div key={req.id} className="coach-row">
              <button
                className="coach-row__main"
                onClick={() => setOpenId(req.id)}
                title="Open the full context"
              >
                <div className="coach-row__line">
                  <span className={`badge badge--${tone}`}>{req.mode}</span>
                  <strong className="coach-row__state">{req.state || "an unrecognised page"}</strong>
                  {disagree && (
                    <span className="coach-row__tag coach-row__tag--warn">witnesses disagree</span>
                  )}
                </div>
                <div className="coach-row__wants">
                  wants <code>{pred.intent || "—"}{pred.params ? " " + JSON.stringify(pred.params) : ""}</code>
                </div>
                <div className="coach-row__meta">
                  {req.task ? `${req.task} · ` : ""}{req.kind} · {fmtTime(req.ts)} · open for detail →
                </div>
              </button>

              <div className="coach-row__actions">
                <button
                  className="btn btn-sm"
                  disabled={busy === req.id || !!blocked}
                  onClick={() => answer(req, "approve")}
                  title={blocked || "Act on the proposal as-is and keep going"}
                >
                  {busy === req.id ? "…" : "Go"}
                </button>
                <button className="btn btn-sm" disabled={busy === req.id} onClick={() => setOpenId(req.id)}>
                  Correct
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  disabled={busy === req.id}
                  onClick={() => answer(req, "abort", { rationale: notes[req.id] || "operator stopped the drive" })}
                >
                  Stop
                </button>
              </div>
            </div>
          );
        })
      )}

      {openReq && (
        <ContextDrawer
          req={openReq}
          busy={busy === openReq.id}
          blocked={incompleteReason(openReq.prediction || {})}
          note={notes[openReq.id] || ""}
          draft={editing[openReq.id]}
          error={error}
          onNote={(v) => setNotes((n) => ({ ...n, [openReq.id]: v }))}
          onStartCorrect={() =>
            setEditing((s) => ({
              ...s,
              [openReq.id]: {
                intent: openReq.prediction?.intent || "",
                params: openReq.prediction?.params ? JSON.stringify(openReq.prediction.params) : "",
              },
            }))
          }
          onDraft={(patch) =>
            setEditing((s) => ({ ...s, [openReq.id]: { ...(s[openReq.id] || {}), ...patch } }))
          }
          onCancelCorrect={() => setEditing((s) => ({ ...s, [openReq.id]: null }))}
          onGo={() => answer(openReq, "approve")}
          onCorrect={() => correct(openReq)}
          onStop={() => answer(openReq, "abort", { rationale: notes[openReq.id] || "operator stopped the drive" })}
          onClose={() => setOpenId(null)}
        />
      )}
    </div>
  );
}
