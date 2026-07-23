import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, fmtTime } from "./api";
import { AppIcon } from "../../../ui/Icon";

// The Coaching pane — the operator's seat in a RUNNING drive.
//
// Operator-directed 2026-07-23. The teacher inbox already existed and was serviced over HTTP by
// the local Claude agent; this is the same seam with the operator in it. Deliberately NOT a
// play/pause button: a parked drive is not "paused", it is asking a specific question, and the
// useful control is a CONTINUATION that shows what it knows, what it has done, and what it wants
// to do next — so pressing Go is a judgement rather than a shrug.
//
// Two things here are load-bearing rather than decorative:
//
//   * "Correct" is as prominent as "Go". The golden training rows come from CORRECTIONS; a pane
//     whose easy path is always "yes" produces agreement and no signal, which quietly starves the
//     thing the whole flywheel runs on.
//   * The note field. The operator's situational knowledge ("this ATS always asks twice", "skip
//     the resume step here") was the one input to this system with nowhere to live — it was said
//     in chat and lost. A note rides into the acting decision's rationale and lands in the
//     journal as evidence, which is what makes it lesson material.

// Intents that are meaningless without a value. `decide()` deliberately never invents an answer
// value — that axis belongs to resolve_answer and past it to the human — so its proposal for a
// text field is a SHAPE ("set_text on Job title"), not a bid to act. Approving one as-is would
// type an empty string, which looks like the operator agreeing to something they did not read.
// Go is disabled for these; Correct is the honest path, and the pane says so.
const NEEDS_A_VALUE = new Set(["set_text", "select_option", "set_date", "check_group", "upload"]);

function incompleteReason(pred) {
  if (!pred?.intent) return "nothing was proposed to approve";
  const p = pred.params || {};
  if (NEEDS_A_VALUE.has(pred.intent) && !p.value && !p.values && !p.month) {
    return `it proposed ${pred.intent} on "${p.field || "a field"}" but no value — it will not invent one. Use Correct.`;
  }
  return "";
}

const MODE_TONE = {
  green: "ok",
  yellow: "warn",
  orange: "warn",
  red: "bad",
};

// The bundle_prompt is the frozen serialisation the reasoner itself reads. Rather than invent a
// second shape for the UI, pull the sections back out of it — one source of truth, and what the
// operator sees is literally what the policy saw.
function section(prompt, name) {
  if (!prompt) return "";
  const re = new RegExp(`# ${name}\\n([\\s\\S]*?)(?=\\n# |$)`);
  return (prompt.match(re)?.[1] || "").trim();
}

function fieldOf(block, key) {
  const line = block.split("\n").find((l) => l.trim().startsWith(`${key}:`));
  return line ? line.split(":").slice(1).join(":").trim() : "";
}

export function CoachingPane({ title = "Coaching" }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState("");
  const [notes, setNotes] = useState({});
  const [editing, setEditing] = useState({});
  const [error, setError] = useState("");

  const load = useCallback(() => {
    getJSON("/api/controller/teacher/pending")
      .then((d) => setItems(d.pending || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    // A parked drive polls the queue every second; 3s here is well inside the park window and
    // cheap (a file tail), so the pane feels live without being a hot loop.
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
      setError("params must be JSON, e.g. {\"control\": \"Continue\"}");
      return;
    }
    // A correction MUST carry real reasoning — the server enforces it (§10), because the WHY is
    // the training signal and a placeholder teaches WHAT with no rule to generalise from.
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
          const recent = section(req.bundle_prompt, "RECENT");
          const windowBlock = section(req.bundle_prompt, "WINDOW");
          const unanswered = (req.bundle_prompt || "").match(/# UNANSWERED \((\d+)\)/)?.[1];
          const pred = req.prediction || {};
          const draft = editing[req.id];
          const tone = MODE_TONE[req.mode] || "warn";
          const blocked = incompleteReason(pred);

          return (
            <div key={req.id} className="attention-item coaching-item">
              <div className="attention-item__body">
                {/* WHERE we are */}
                <div className="attention-item__why">
                  <span className={`badge badge--${tone}`}>{req.mode}</span>{" "}
                  <strong>{req.state || "an unrecognised page"}</strong>
                  {req.maturity ? <span className="badge badge--muted"> {req.maturity}</span> : null}
                </div>

                {/* WHY it stopped — the authority verdict, in its own words */}
                <div className="attention-item__hint">{req.authority_reason}</div>
                {req.reach_gaps?.length ? (
                  <div className="attention-item__hint">
                    can’t operate: {req.reach_gaps.join(", ")}
                  </div>
                ) : null}

                {/* CONTEXT: done, next, form, window */}
                <div className="coaching-grid">
                  <div>
                    <div className="coaching-label">Next per the recipe</div>
                    <div>{fieldOf(recipe, "next_action") || "—"}</div>
                  </div>
                  <div>
                    <div className="coaching-label">Unanswered fields</div>
                    <div>{unanswered ?? "—"}</div>
                  </div>
                  <div>
                    <div className="coaching-label">ATS</div>
                    <div>{fieldOf(state, "ats") || "—"}</div>
                  </div>
                  {windowBlock ? (
                    <div>
                      <div className="coaching-label">Window</div>
                      <div>{fieldOf(windowBlock, "tabs")} · {fieldOf(windowBlock, "roles")}</div>
                    </div>
                  ) : null}
                </div>

                {recent ? (
                  <details className="coaching-details">
                    <summary>What it has done</summary>
                    <pre>{recent}</pre>
                  </details>
                ) : null}

                {/* WHAT IT WANTS TO DO — the thing Go actually approves */}
                <div className="coaching-proposal">
                  <div className="coaching-label">It proposes</div>
                  <code>
                    {pred.intent || "—"} {pred.params ? JSON.stringify(pred.params) : ""}
                  </code>
                  {pred.rationale ? <div className="attention-item__hint">{pred.rationale}</div> : null}
                  {blocked ? <div className="coaching-blocked">{blocked}</div> : null}
                </div>

                {/* The note — always visible, because it is the point */}
                <textarea
                  className="coaching-note"
                  rows={2}
                  placeholder="Note for the hand-off — what you know that it doesn't. Rides into the journal."
                  value={notes[req.id] || ""}
                  onChange={(e) => setNotes((n) => ({ ...n, [req.id]: e.target.value }))}
                />

                {draft ? (
                  <div className="coaching-correct">
                    <input
                      placeholder="intent (click / set_text / select_option / submit)"
                      value={draft.intent || ""}
                      onChange={(e) =>
                        setEditing((s) => ({ ...s, [req.id]: { ...draft, intent: e.target.value } }))
                      }
                    />
                    <input
                      placeholder={'params, e.g. {"control": "Continue"}'}
                      value={draft.params || ""}
                      onChange={(e) =>
                        setEditing((s) => ({ ...s, [req.id]: { ...draft, params: e.target.value } }))
                      }
                    />
                    <button className="btn btn-sm btn-primary" onClick={() => correct(req)}>
                      Send correction
                    </button>
                    <button
                      className="btn btn-sm"
                      onClick={() => setEditing((s) => ({ ...s, [req.id]: null }))}
                    >
                      Cancel
                    </button>
                  </div>
                ) : null}

                <div className="attention-item__meta">
                  {req.task ? `${req.task} · ` : ""}
                  {req.kind} · {fmtTime(req.ts)}
                </div>
              </div>

              {!draft && (
                <div className="coaching-actions">
                  {/* Go and Correct sit side by side, same weight. The corrections are the
                      training signal; a pane that makes "yes" the only easy answer starves it. */}
                  <button
                    className="btn btn-sm btn-primary"
                    disabled={busy === req.id || !!blocked}
                    onClick={() => answer(req, "approve")}
                    title={blocked || "Act on the proposal as-is and keep going"}
                  >
                    {busy === req.id ? "…" : "Go"}
                  </button>
                  <button
                    className={`btn btn-sm${blocked ? " btn-primary" : ""}`}
                    disabled={busy === req.id}
                    onClick={() =>
                      setEditing((s) => ({
                        ...s,
                        [req.id]: {
                          intent: pred.intent || "",
                          params: pred.params ? JSON.stringify(pred.params) : "",
                        },
                      }))
                    }
                  >
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
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
