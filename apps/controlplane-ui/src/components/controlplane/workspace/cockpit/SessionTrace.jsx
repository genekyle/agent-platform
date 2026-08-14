import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppIcon } from "../../../../ui/Icon";
import { API, fmtTime, getJSON, postJSON } from "../api";

const POLL_MS = 10000;

function hostOf(url) {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url || "—"; }
}

function iconFor(kind) {
  if (kind.includes("closed")) return "close";
  if (kind.includes("opened")) return "play";
  if (kind.includes("navigate")) return "arrowRight";
  if (kind.includes("recording")) return "eye";
  if (kind.includes("step")) return "listTree";
  if (kind.includes("decision")) return "inspect";
  if (kind.includes("teacher")) return "user";
  return "circleDot";
}

function shotUrl(name) {
  return name ? `${API}/api/observations/screenshots/${encodeURIComponent(name)}` : null;
}

function verdictBadge(verdict) {
  if (verdict === "confirmed") return "badge badge--ready";
  if (verdict === "mismatch") return "badge badge--warn";
  return "badge badge--muted";
}

function Shot({ name, label }) {
  const [missing, setMissing] = useState(false);
  const url = shotUrl(name);
  if (!url || missing) {
    return (
      <figure className="trace__shot trace__shot--empty">
        <span>{label}</span>
        <small>{missing
          ? "screenshot recorded but the file is gone from disk"
          : "no screenshot — credential posture, or the capture did not land"}</small>
      </figure>
    );
  }
  return (
    <figure className="trace__shot">
      <span>{label}</span>
      <a href={url} target="_blank" rel="noreferrer">
        <img src={url} alt={`${label} screenshot for this step`} loading="lazy"
          onError={() => setMissing(true)} />
      </a>
    </figure>
  );
}

/** The correction affordance: the system made its claim, the teacher writes what was actually
 *  true — note required, states both-or-neither, server errors surfaced verbatim (they are the
 *  contract speaking, not noise). */
function StepCorrection({ sessionKey, row, onSaved }) {
  const [note, setNote] = useState("");
  const [beforeState, setBeforeState] = useState("");
  const [afterState, setAfterState] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await postJSON(`/api/transitions/${sessionKey}/correct`, {
        index: row.index, ts: row.ts, note,
        before_state: beforeState.trim(), after_state: afterState.trim(),
      });
      setNote(""); setBeforeState(""); setAfterState("");
      onSaved();
    } catch (e) {
      setError(e.message || "The correction was refused.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="trace__correct" onSubmit={save}>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Correction note — cite what the row's own evidence shows (required)"
        rows={2}
      />
      <div className="trace__correct-states">
        <input value={beforeState} onChange={(e) => setBeforeState(e.target.value)}
          placeholder="true before-state (optional)" />
        <input value={afterState} onChange={(e) => setAfterState(e.target.value)}
          placeholder="true after-state (optional)" />
        <button type="submit" className="btn btn-sm"
          disabled={saving || !note.trim()
            || (beforeState.trim() === "") !== (afterState.trim() === "")}>
          {saving ? "Saving…" : "Teach it"}
        </button>
      </div>
      <small>Both states → the row becomes training data and the refit runs. Note alone → the
        verdict is annotated, nothing retrains.</small>
      {error && <div className="coaching-error">{error}</div>}
    </form>
  );
}

function StepDetail({ sessionKey, entry, onSaved }) {
  const row = entry.row;
  const narration = row.narration || {};
  const correction = row.teacher_correction;
  return (
    <details className="trace__step">
      <summary>
        What the system knew
        {correction && <span className="badge badge--reasoning">teacher-labeled</span>}
      </summary>
      <div className="trace__shots">
        <Shot name={entry.shots.before} label="before" />
        <Shot name={entry.shots.after} label="after" />
      </div>
      <dl className="trace__facts">
        {["believed", "expected", "did", "saw", "changed", "window"].map((k) =>
          narration[k] ? (
            <div key={k}><dt>{k}</dt><dd>{narration[k]}</dd></div>
          ) : null)}
        <div><dt>verdict</dt><dd>
          <span className={verdictBadge(row.verdict)}>{row.verdict || "unrecorded"}</span>
          {row.evidence ? ` — ${row.evidence}` : ""}
        </dd></div>
      </dl>
      {correction && (
        <div className="trace__teacher-label">
          <strong>Teacher ({correction.by || "operator"})</strong>
          {correction.before_state && (
            <code>{correction.before_state} → {correction.after_state}</code>
          )}
          <p>{correction.note}</p>
        </div>
      )}
      <StepCorrection sessionKey={sessionKey} row={row} onSaved={onSaved} />
    </details>
  );
}

export function SessionTrace({ sessionId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  // Monotonic guard: load() is called by the 10s poll AND by onSaved after a correction —
  // without it, a slow poll response that started BEFORE the save can resolve after it and
  // clobber the just-labeled rows ("my correction vanished"). Superseded loads are discarded.
  const loadSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    const [windows, panel, recordings, steps, decisions, parks] = await Promise.all([
      getJSON(`/api/session_control/${sessionId}/windows?limit=200`).catch(() => null),
      getJSON(`/api/session_control/${sessionId}`).catch(() => null),
      getJSON(`/api/session_control/${sessionId}/observe`).catch(() => null),
      // 404 until the first wrapped step runs — absence is a normal reading, not an error.
      getJSON(`/api/transitions/${sessionId}`).catch(() => null),
      getJSON(`/api/controller/decisions?session_id=${encodeURIComponent(String(sessionId))}&limit=100`).catch(() => null),
      getJSON(`/api/controller/teacher/pending`).catch(() => null),
    ]);
    if (seq !== loadSeq.current) return;
    if (!windows && !panel && !recordings && !steps) {
      setError("Could not read this session's trace.");
      return;
    }
    setData({
      windows, panel,
      recordings: recordings?.recordings || [],
      steps: steps?.rows || [],
      decisions: decisions?.decisions || [],
      // ALL open parks, not just this session's: a run launched without the cockpit session id
      // journals under its run-{task} key, and a park invisible here is a drive silently frozen
      // for park_seconds. Foreign keys are badged on the entry instead of filtered out.
      parks: parks?.pending || [],
    });
    setError("");
  }, [sessionId]);

  useEffect(() => {
    const initial = setTimeout(load, 0);
    const timer = setInterval(load, POLL_MS);
    return () => { clearTimeout(initial); clearInterval(timer); };
  }, [load]);

  const entries = useMemo(() => {
    if (!data) return [];
    const system = (data.panel?.events || []).map((event) => ({
      key: `sys-${event.ts}-${event.kind}-${(event.detail || "").length}`,
      ts: event.ts,
      kind: event.kind || "system",
      source: "system",
      actor: "recorded step",
      title: event.kind?.replaceAll("_", " ") || "system event",
      detail: event.detail || "",
      why: event.why || "",
      next_up: event.next_up || "",
    }));
    const windows = (data.windows?.timeline || []).map((event) => ({
      key: `win-${event.ts}-${event.kind}-${event.url || ""}`,
      ts: event.ts,
      kind: `window ${event.kind}`,
      source: "window",
      actor: event.actor || "system",
      title: `${event.kind} · ${hostOf(event.url)}`,
      detail: event.note || event.url || "",
      from: event.from_url,
      role: event.role,
    }));
    const recordings = data.recordings.map((recording) => ({
      key: `rec-${recording.stored_at}`,
      ts: recording.stored_at,
      kind: "observer recording",
      source: "observer",
      actor: "operator",
      title: recording.note || "Observation recording",
      detail: `${recording.count || 0} events over ${Math.round((recording.duration_ms || 0) / 100) / 10}s`,
    }));
    // The per-step record: the transition row IS "what the system knew at the time" — belief,
    // declared expectation, act, delta, verdict, and the screenshots it saw. The register the
    // 2026-08-09 refocus names as the visualizer's source of truth.
    const steps = data.steps.map((row) => ({
      key: `step-${row.index}`,
      ts: row.ts,
      kind: "recorded step",
      source: "step",
      actor: row.action?.initiator || "system",
      title: `${row.rung || row.action?.rung || "step"} · ${row.narration?.did || row.verdict || ""}`,
      detail: "",
      row,
      shots: row.screenshots || {},
    }));
    const decisions = data.decisions.map((d) => ({
      key: `dec-${d.ts}-${d.intent}`,
      ts: d.ts,
      kind: "controller decision",
      source: "decide",
      actor: d.rung || "controller",
      title: `${d.intent}${d.state ? ` · ${d.state.replaceAll("_", " ")}` : ""}`,
      detail: d.rationale || "",
      decision: d,
    }));
    const parks = data.parks.map((p) => ({
      key: `park-${p.id}`,
      ts: p.ts,
      kind: "teacher park open",
      source: "teacher",
      actor: "teacher seat",
      title: `Waiting on the teacher · ${p.kind}${p.state ? ` · ${p.state.replaceAll("_", " ")}` : ""}`,
      detail: p.authority_reason
        || (p.prediction?.intent ? `local proposal: ${p.prediction.intent}` : "no local proposal"),
      park: p,
    }));
    return [...system, ...windows, ...recordings, ...steps, ...decisions, ...parks]
      .sort((a, b) => (Date.parse(b.ts || "") || 0) - (Date.parse(a.ts || "") || 0));
  }, [data]);

  if (!data && !error) return <p className="empty-hint">Building the session trace…</p>;

  const summary = data?.windows?.summary || {};
  const taught = (data?.steps || []).filter((r) => r.teacher_correction).length;
  return (
    <div className="trace">
      {error && <div className="coaching-error">{error}</div>}

      {data?.parks?.length > 0 && (
        <div className="trace__parks-banner">
          <AppIcon name="user" size={14} />
          <strong>{data.parks.length} question{data.parks.length > 1 ? "s" : ""} parked for the
            teacher</strong>
          <span>the drive is waiting — answer via the teacher seat, or it escalates on timeout</span>
        </div>
      )}

      <section className="trace__summary" aria-label="Trace summary">
        <article><span>System events</span><strong>{data?.panel?.events?.length || 0}</strong><small>recent decisions and actions</small></article>
        <article><span>Recorded steps</span><strong>{data?.steps?.length || 0}</strong><small>{taught} teacher-labeled</small></article>
        <article><span>Window events</span><strong>{data?.windows?.timeline?.length || 0}</strong><small>{summary.open_tabs ?? "?"} tabs open now</small></article>
        <article><span>Observer recordings</span><strong>{data?.recordings?.length || 0}</strong><small>kept interaction windows</small></article>
        <article><span>Last verification</span><strong>{data?.panel?.last_step?.ok === false ? "Mismatch" : data?.panel?.last_step ? "Recorded" : "—"}</strong><small>{data?.panel?.last_step?.action || "no action yet"}</small></article>
      </section>

      <section className="cockpit__pane trace__timeline">
        <div className="cockpit__pane-head">
          <AppIcon name="activity" size={14} /> Observation → decision → action record
          <span className="badge badge--muted">newest first</span>
        </div>
        {entries.length === 0 ? (
          <p className="empty-hint">Nothing has been recorded for this session yet.</p>
        ) : (
          <ol className="trace__entries">
            {entries.map((entry, i) => (
              <li key={entry.key || `${entry.ts}-${entry.kind}-${i}`} data-source={entry.source}>
                <span className="trace__mark"><AppIcon name={iconFor(entry.kind)} size={12} /></span>
                <div className="trace__body">
                  <div className="trace__line">
                    <strong>{entry.title}</strong>
                    {entry.role && <span className="badge badge--muted">{entry.role}</span>}
                    {entry.park && String(entry.park.session_id) !== String(sessionId) && (
                      <span className="badge badge--muted">run: {entry.park.session_id || "unscoped"}</span>
                    )}
                    {entry.decision?.escalate && (
                      <span className="badge badge--warn">
                        escalated{entry.decision.escalation_axis ? ` · ${entry.decision.escalation_axis}` : ""}
                      </span>
                    )}
                    {entry.decision?.golden && <span className="badge badge--reasoning">golden</span>}
                    <span className="badge badge--muted">{entry.actor}</span>
                    <time>{fmtTime(entry.ts)}</time>
                  </div>
                  {entry.detail && <p>{entry.detail}</p>}
                  {/* WHY IT HAPPENED, AND WHAT WAS EXPECTED NEXT. The timeline used to be a list
                      of arrivals — legible only to somebody who already knew the story. `why` is
                      the reason a state changed; `next_up` is the consequence we DECLARED at the
                      time, which the entry above it then either matches or contradicts. That is
                      what makes the record falsifiable rather than merely complete. */}
                  {entry.why && <p className="trace__why"><b>Why:</b> {entry.why}</p>}
                  {entry.next_up && (
                    <p className="trace__next"><b>Next:</b> {entry.next_up}</p>
                  )}
                  {entry.from && <small>from {entry.from}</small>}
                  {entry.source === "step" && (
                    <StepDetail sessionKey={String(sessionId)} entry={entry} onSaved={load} />
                  )}
                  {entry.source === "decide" && entry.decision?.capture_screenshot && (
                    <div className="trace__shots trace__shots--single">
                      <Shot name={entry.decision.capture_screenshot} label="what it saw" />
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
