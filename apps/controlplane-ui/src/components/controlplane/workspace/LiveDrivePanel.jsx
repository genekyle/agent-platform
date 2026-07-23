import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON, fmtTime } from "./api";
import { AppIcon } from "../../../ui/Icon";
import { ContextView } from "./ContextView";
import { WindowCard } from "./WindowCard";

// The Live drive tab — the operator's seat in a running drive, in its own place.
//
// This replaces the panels that had been piled onto the Overview tab (a coaching pane, a context
// blob, a window card), which crowded the overview and buried each other. Here they get room: a
// full-width session-window strip on top, then a master/detail — the parked-drive QUEUE on the
// left, the selected drive's full CONTEXT on the right, with the write-back controls under it.
//
// Load-bearing, unchanged from before the move:
//   * Correct is a peer of Go, never quieter — the golden training rows come from corrections, and
//     a surface whose easy path is always "yes" produces agreement and no signal.
//   * The note field rides into the acting decision's rationale and lands in the journal as
//     evidence; it is the only home the operator's situational knowledge has.
//   * Go disables (with its reason shown) when the proposal carries no value, because decide()
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

const NEEDS_A_VALUE = new Set(["set_text", "select_option", "set_date", "check_group", "upload"]);
function incompleteReason(pred) {
  if (!pred?.intent) return "nothing was proposed to approve";
  const p = pred.params || {};
  if (NEEDS_A_VALUE.has(pred.intent) && !p.value && !p.values && !p.month) {
    return `proposed ${pred.intent} on "${p.field || "a field"}" but no value — it will not invent one. Use Correct.`;
  }
  return "";
}

export function LiveDrivePanel({ domain }) {
  const [items, setItems] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState(null); // {intent, params} when correcting
  const [error, setError] = useState("");

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

  // Auto-select the first parked drive; drop the selection/draft when it is answered and gone.
  useEffect(() => {
    if (!items.length) { setOpenId(null); return; }
    if (!items.find((r) => r.id === openId)) {
      setOpenId(items[0].id);
      setDraft(null);
      setNote("");
    }
  }, [items, openId]);

  const req = items.find((r) => r.id === openId) || null;
  const blocked = req ? incompleteReason(req.prediction || {}) : "";

  const answer = async (action, extra = {}) => {
    if (!req) return;
    setBusy(true);
    setError("");
    try {
      await postJSON(`/api/controller/teacher/${req.id}/respond`, { action, note, ...extra });
      setNote("");
      setDraft(null);
      load();
    } catch (e) {
      setError(e.message || "could not answer");
    } finally {
      setBusy(false);
    }
  };

  const sendCorrection = () => {
    if (!draft?.intent) { setError("a correction needs an intent"); return; }
    let params = {};
    try {
      params = draft.params ? JSON.parse(draft.params) : {};
    } catch {
      setError('params must be JSON, e.g. {"field": "Job title *", "value": "…"}');
      return;
    }
    const why = note.trim();
    if (why.length < 12) {
      setError("a correction needs a reason — that reasoning is the training signal");
      return;
    }
    answer("correct", { decision: { intent: draft.intent, params, confidence: 1.0 }, rationale: why });
  };

  const startCorrect = () =>
    setDraft({
      intent: req?.prediction?.intent || "",
      params: req?.prediction?.params ? JSON.stringify(req.prediction.params) : "",
    });

  return (
    <div className="live">
      <WindowCard domainId={domain.id} />

      <div className="live-split">
        {/* LEFT — the queue */}
        <div className="live-queue layer">
          <div className="layer__head">
            <div className="layer__title layer__title--with-icon">
              <AppIcon name="waypoints" size={17} /> Waiting on you
            </div>
            <span className="layer__count">{items.length || ""}</span>
          </div>

          {items.length === 0 ? (
            <div className="attention-empty">
              <AppIcon name="checkCircle" size={17} /> No drive is waiting.
            </div>
          ) : (
            items.map((r) => {
              const st = section(r.bundle_prompt, "STATE");
              const bel = r.belief_prompt || "";
              const disagree =
                fieldOf(bel, "state") && fieldOf(st, "state") && fieldOf(bel, "state") !== fieldOf(st, "state");
              const pred = r.prediction || {};
              return (
                <button
                  key={r.id}
                  className={`live-q ${r.id === openId ? "is-active" : ""}`}
                  onClick={() => { setOpenId(r.id); setDraft(null); setNote(""); setError(""); }}
                >
                  <div className="live-q__line">
                    <span className={`badge badge--${MODE_TONE[r.mode] || "warn"}`}>{r.mode}</span>
                    <span className="live-q__state">{r.state || "unrecognised page"}</span>
                  </div>
                  {disagree && <div className="live-q__tag">witnesses disagree</div>}
                  <div className="live-q__wants">
                    wants <code>{pred.intent || "—"}</code>
                  </div>
                  <div className="live-q__meta">{r.kind} · {fmtTime(r.ts)}</div>
                </button>
              );
            })
          )}
        </div>

        {/* RIGHT — the selected drive's context + write-back */}
        <div className="live-detail layer">
          {req && (
            <div className="live-detail__head">
              <span className={`badge badge--${MODE_TONE[req.mode] || "warn"}`}>{req.mode}</span>
              <span className="live-detail__state">{req.state || "an unrecognised page"}</span>
              {req.maturity ? <span className="badge badge--muted">{req.maturity}</span> : null}
            </div>
          )}
          {req && <p className="live-detail__why">{req.authority_reason}</p>}

          <div className="live-detail__body">
            <ContextView req={req} />
          </div>

          {req && (
            <div className="live-detail__foot">
              {error && <div className="coaching-error">{error}</div>}
              {draft ? (
                <div className="cv-correct">
                  <input
                    placeholder="intent (click / set_text / select_option / submit)"
                    value={draft.intent || ""}
                    onChange={(e) => setDraft((d) => ({ ...d, intent: e.target.value }))}
                  />
                  <input
                    placeholder={'params, e.g. {"field": "Job title *", "value": "…"}'}
                    value={draft.params || ""}
                    onChange={(e) => setDraft((d) => ({ ...d, params: e.target.value }))}
                  />
                </div>
              ) : null}
              <textarea
                className="cv-note-in"
                rows={2}
                placeholder="Note / reason — what you know that it doesn't. Rides into the journal. Required to Correct."
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <div className="cv-actions">
                {draft ? (
                  <>
                    <button className="btn btn-sm btn-primary" onClick={sendCorrection} disabled={busy}>
                      {busy ? "…" : "Send correction"}
                    </button>
                    <button className="btn btn-sm" onClick={() => setDraft(null)}>Cancel</button>
                  </>
                ) : (
                  <>
                    <button className="btn btn-sm" onClick={() => answer("approve")} disabled={busy || !!blocked} title={blocked || "Act on the proposal as-is"}>
                      {busy ? "…" : "Go"}
                    </button>
                    <button className="btn btn-sm" onClick={startCorrect} disabled={busy}>Correct</button>
                    <button className="btn btn-sm btn-ghost" onClick={() => answer("abort", { rationale: note || "operator stopped the drive" })} disabled={busy}>
                      Stop
                    </button>
                  </>
                )}
              </div>
              {blocked && !draft && <p className="cv-blocked">{blocked}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
