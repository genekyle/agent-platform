import { useCallback, useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;
const POLL_MS = 10000;

// Why a row needs a teacher, in the queue's own ranking (mismatch first — one label there both
// corrects the witnesses AND explains a failed act).
const WHY_META = {
  mismatch: { tone: "danger", hint: "the action claimed ok; the world disagreed — the most valuable label" },
  no_belief: { tone: "neutral", hint: "the witnesses were blind on at least one side" },
  witness_split: { tone: "warning", hint: "the witnesses named a state but split or were too uncertain to self-label" },
};

function hostOf(url) {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url || "—"; }
}

function shotUrl(name) {
  return name ? `${API}/api/observations/screenshots/${encodeURIComponent(name)}` : null;
}

function Shot({ name, label }) {
  const [missing, setMissing] = useState(false);
  const url = shotUrl(name);
  if (!url || missing) {
    return (
      <figure style={{ margin: 0, flex: 1, minWidth: 120 }}>
        <figcaption className="chrome-label muted">{label}</figcaption>
        <div className="chrome-label muted" style={{ padding: "18px 8px", borderRadius: 6, background: "rgba(127,127,127,0.08)", textAlign: "center" }}>
          {missing ? "file gone from disk" : "no screenshot"}
        </div>
      </figure>
    );
  }
  return (
    <figure style={{ margin: 0, flex: 1, minWidth: 120 }}>
      <figcaption className="chrome-label muted">{label}</figcaption>
      <a href={url} target="_blank" rel="noreferrer">
        <img src={url} alt={`${label} screenshot`} loading="lazy" onError={() => setMissing(true)}
          style={{ maxWidth: "100%", borderRadius: 6, border: "1px solid rgba(127,127,127,0.25)" }} />
      </a>
    </figure>
  );
}

/** One queued row: what the witnesses made of it, the evidence, and the correction form —
 *  the same contract as the cockpit Trace's StepCorrection (note required; states both-or-
 *  neither; server errors shown verbatim, they are the contract speaking). */
function QueueItem({ item, onAnswered }) {
  const meta = WHY_META[item.why_queued] ?? { tone: "neutral", hint: "" };
  const [note, setNote] = useState("");
  // The witnesses' own reading is a PLACEHOLDER, never a value: a mismatch row is queued
  // precisely because the world disputed the step, and prefilled values would let one keypress
  // (a note) teach the disputed belief back to the trainer as ground truth. The cockpit's
  // StepCorrection starts empty for the same reason; states here are typed, or nothing retrains.
  const [beforeState, setBeforeState] = useState("");
  const [afterState, setAfterState] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const statesHalf = (beforeState.trim() === "") !== (afterState.trim() === "");

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const r = await fetch(`${API}/api/transitions/${encodeURIComponent(item.key)}/correct`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          index: item.index, ts: item.ts, note,
          before_state: beforeState.trim(), after_state: afterState.trim(),
        }),
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({})))?.detail;
        throw new Error(detail || `correction failed: ${r.status}`);
      }
      onAnswered();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const unc = (u) => (u == null ? "" : ` (unc ${u})`);

  return (
    <div style={{ borderRadius: 10, background: "rgba(127,127,127,0.06)", padding: "10px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className={`coverage-status coverage-status--${meta.tone}`} title={meta.hint} />
        <span className="chip" title={meta.hint}>{item.why_queued}</span>
        <span className="chip">{item.rung}</span>
        <span className="chrome-label muted">{item.key} · row {item.index}</span>
        <span className="chrome-label muted" style={{ marginLeft: "auto" }}>{(item.ts || "").slice(0, 19)}</span>
      </div>

      <div style={{ marginTop: 6, fontSize: 13, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
        <span>
          witnesses: <strong>{item.before?.state || "—"}</strong>{unc(item.before?.uncertainty)}
          {" "}→ <strong>{item.after?.state || "—"}</strong>{unc(item.after?.uncertainty)}
        </span>
        <span className="chrome-label muted">
          {hostOf(item.before?.url)} → {hostOf(item.after?.url)}
        </span>
      </div>

      {(item.page_says || []).length ? (
        <div className="chrome-label muted" style={{ marginTop: 4 }}>
          page says: {item.page_says.slice(0, 3).join(" · ")}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
        <Shot name={item.screenshots?.before} label="before" />
        <Shot name={item.screenshots?.after} label="after" />
      </div>

      <form onSubmit={save} style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
        <textarea
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Correction note — cite what the row's own evidence shows (required)"
          style={{ width: "100%", boxSizing: "border-box" }}
        />
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input style={{ minWidth: 180 }} value={beforeState}
            placeholder={item.before?.state ? `witnesses read: ${item.before.state}` : "true before-state"}
            onChange={(e) => setBeforeState(e.target.value)} />
          <span aria-hidden>→</span>
          <input style={{ minWidth: 180 }} value={afterState}
            placeholder={item.after?.state ? `witnesses read: ${item.after.state}` : "true after-state"}
            onChange={(e) => setAfterState(e.target.value)} />
          <button type="submit" className="ghost-btn small-btn"
            disabled={saving || !note.trim() || statesHalf}>
            {saving ? "Saving…" : "Teach it"}
          </button>
          <span className="chrome-label muted">
            both states → the row trains and the refit runs; note alone → annotated, nothing retrains.
          </span>
        </div>
        {error ? <span className="capture-error">{error}</span> : null}
      </form>
    </div>
  );
}

/**
 * The transition label queue — the teacher's ranked worklist (`GET /api/transitions/label_queue`),
 * which until 2026-08-22 had zero UI callers: the Label tab reads the OLDER observation queue and
 * the only way to answer these rows was curl. Polls so the queue follows a live drive; a label
 * written here feeds three organs at once (transition table, witnesses, program recompile).
 */
export function TransitionQueueSection() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  // Monotonic guard (same reason as the cockpit Trace): a slow poll that started before a
  // correction must not resolve after it and resurrect the just-answered row.
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/transitions/label_queue?limit=25`);
      if (!r.ok) throw new Error(`label queue failed: ${r.status}`);
      const payload = await r.json();
      if (seq === loadSeq.current) {
        setData(payload);
        setError(null);
      }
    } catch (e) {
      if (seq === loadSeq.current) setError(e.message);
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const queue = data?.queue ?? [];
  const remaining = data?.remaining ?? 0;

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Transition label queue</h2>
          <p>
            The rows self-supervision cannot claim, ranked by what a label buys: the world
            disagreed (mismatch) first, then blind witnesses, then splits. Confirmed-and-confident
            rows label themselves and never appear here. One label teaches the transition table,
            the witnesses, and the $0 program rung at once.
          </p>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <span className="chrome-label muted">remaining</span>
            <span style={{ fontSize: 22, fontWeight: 600 }}>{remaining}</span>
          </div>
          <button className="ghost-btn small-btn" onClick={load} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <span className="capture-error">{error}</span> : null}

      {data && queue.length === 0 ? (
        <div className="empty-state">
          The queue is empty — every transition row is either teacher-labeled or confidently
          self-labeled. New rows appear here the moment a live drive banks one the witnesses
          cannot claim.
        </div>
      ) : null}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {queue.map((item) => (
          <QueueItem key={`${item.key}:${item.index}`} item={item} onAnswered={load} />
        ))}
      </div>

      {queue.length > 0 && remaining > queue.length ? (
        <div className="chrome-label muted" style={{ marginTop: 10 }}>
          showing the top {queue.length} of {remaining} — answering removes a row and pulls the
          next one up.
        </div>
      ) : null}
    </section>
  );
}
