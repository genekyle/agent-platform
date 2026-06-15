import { useCallback, useEffect, useRef, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

// --- Movement helpers (dependency-free) -------------------------------------
// 5th-order minimum-jerk position profile s(u) = 10u^3 - 15u^4 + 6u^5.
function minJerkPath(p0, p1, steps = 60, durationMs = 600) {
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const u = i / steps;
    const s = 10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5;
    pts.push({ x: p0.x + (p1.x - p0.x) * s, y: p0.y + (p1.y - p0.y) * s, t: u * durationMs });
  }
  return pts;
}
function pathLength(pts) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  return d;
}
function directness(pts) {
  if (pts.length < 2) return 1;
  const straight = Math.hypot(pts[pts.length - 1].x - pts[0].x, pts[pts.length - 1].y - pts[0].y);
  const len = pathLength(pts);
  return len > 0 ? straight / len : 1;
}
function polyline(pts) {
  return pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}
// Fitts-style duration: longer/narrower targets take longer.
function fittsMs(dist, width) {
  return Math.round(180 + 100 * Math.log2(2 * dist / Math.max(8, width) + 1));
}

function usd(v) {
  const n = Number(v || 0);
  if (n === 0) return "$0.00";
  return n < 0.01 ? `$${n.toFixed(6)}` : `$${n.toFixed(2)}`;
}
function pct(v) {
  return `${Math.round(Number(v || 0) * 100)}%`;
}

// Dependency-free horizontal bar chart (no chart lib installed).
function BarChart({ rows, labelKey, valueKey, format = (v) => v, color = "#2f6feb" }) {
  const max = Math.max(1, ...rows.map((r) => Number(r[valueKey] || 0)));
  if (!rows.length) return <div className="empty-state">No data yet.</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map((r) => (
        <div key={String(r[labelKey])} style={{ display: "grid", gridTemplateColumns: "160px 1fr 70px", alignItems: "center", gap: 8 }}>
          <span className="mono" style={{ fontSize: "0.8rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r[labelKey]}</span>
          <div style={{ height: 14, background: "#eef2f7", borderRadius: 4, overflow: "hidden" }}>
            <div style={{ width: `${(Number(r[valueKey] || 0) / max) * 100}%`, height: "100%", background: color }} />
          </div>
          <span style={{ fontSize: "0.8rem", textAlign: "right" }}>{format(r[valueKey])}</span>
        </div>
      ))}
    </div>
  );
}

export function LabSection({ section }) {
  const [tel, setTel] = useState({ loading: true, data: null, error: null });

  const loadTelemetry = useCallback(async () => {
    setTel((c) => ({ ...c, loading: true, error: null }));
    try {
      const r = await fetch(`${API}/api/select/telemetry`);
      if (!r.ok) throw new Error(`Telemetry ${r.status}`);
      setTel({ loading: false, data: await r.json(), error: null });
    } catch (e) {
      setTel({ loading: false, data: null, error: e.message });
    }
  }, []);

  useEffect(() => { loadTelemetry(); }, [loadTelemetry]);

  const d = tel.data;

  if (section === "playground") return <PlaygroundPanel />;
  if (section === "test") return <TestPanel />;

  if (section === "trainer") {
    return (
      <div className="section-stack">
        <section className="panel">
          <div className="panel-header">
            <div><h2>Trainer</h2><p>Local layers train from the selection telemetry corpus. The corpus grows every time the SELECT cascade runs.</p></div>
            <button className="ghost-btn small-btn" onClick={loadTelemetry} disabled={tel.loading}>{tel.loading ? "..." : "Refresh"}</button>
          </div>
          <div className="system-card-grid">
            <article className="system-card"><div className="system-card-header"><h3>Corpus size</h3></div><p className="system-card-copy" style={{ fontSize: "1.6rem", fontWeight: 600 }}>{d?.corpus_size ?? 0}</p><p className="system-micro-copy">logged selections</p></article>
            <article className="system-card"><div className="system-card-header"><h3>Cache-hit rate</h3></div><p className="system-card-copy" style={{ fontSize: "1.6rem", fontWeight: 600 }}>{pct(d?.rates?.cache_hit)}</p><p className="system-micro-copy">free reuse (target ↑)</p></article>
          </div>
          <div className="system-summary" style={{ marginTop: 12 }}>
            Local layers (tiny page-state classifier, micro-model selector) are <strong>not built yet</strong> — by design, they're earned from this corpus once the logs show where Haiku is reached too often. Keep running the cascade to grow the corpus; train buttons activate when a layer is wired.
          </div>
          <div className="detail-actions">
            <button className="primary-btn" disabled title="Activates once a local layer is wired + the corpus is large enough">Train local layer (coming soon)</button>
          </div>
        </section>
      </div>
    );
  }

  if (section === "eval") {
    const t = d?.totals ?? {};
    return (
      <div className="section-stack">
        <section className="panel">
          <div className="panel-header">
            <div><h2>Eval — flywheel metrics</h2><p>The four numbers that say whether the flywheel is turning.</p></div>
            <button className="ghost-btn small-btn" onClick={loadTelemetry} disabled={tel.loading}>{tel.loading ? "..." : "Refresh"}</button>
          </div>
          {tel.error && <div className="empty-state error">{tel.error}</div>}
          <div className="system-card-grid">
            <article className="system-card"><div className="system-card-header"><h3>Selections</h3></div><p className="system-card-copy" style={{ fontSize: "1.5rem", fontWeight: 600 }}>{t.selections ?? 0}</p></article>
            <article className="system-card"><div className="system-card-header"><h3>Cache-hit rate</h3></div><p className="system-card-copy" style={{ fontSize: "1.5rem", fontWeight: 600 }}>{pct(d?.rates?.cache_hit)}</p><p className="system-micro-copy">↑ = cheaper over time</p></article>
            <article className="system-card"><div className="system-card-header"><h3>Escalation rate</h3></div><p className="system-card-copy" style={{ fontSize: "1.5rem", fontWeight: 600 }}>{pct(d?.rates?.escalation)}</p><p className="system-micro-copy">↓ = more autonomous</p></article>
            <article className="system-card"><div className="system-card-header"><h3>Avg cost / selection</h3></div><p className="system-card-copy" style={{ fontSize: "1.5rem", fontWeight: 600 }}>{usd(d?.rates?.avg_cost_usd)}</p><p className="system-micro-copy">total {usd(t.cost_usd)}</p></article>
          </div>
        </section>
        <section className="panel">
          <div className="panel-header"><div><h2>Escalation reasons</h2><p>Why selections went to a human — these are the gaps the local layers will close.</p></div></div>
          <BarChart rows={d?.by_reason ?? []} labelKey="reason_code" valueKey="count" color="#dc2626" />
        </section>
      </div>
    );
  }

  // visualization (default)
  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div><h2>Visualization</h2><p>SELECT-stage corpus over time. Cache-hit up + cost flat/down = flywheel working.</p></div>
          <button className="ghost-btn small-btn" onClick={loadTelemetry} disabled={tel.loading}>{tel.loading ? "..." : "Refresh"}</button>
        </div>
        {tel.error && <div className="empty-state error">{tel.error}</div>}
        {(!d || d.corpus_size === 0) ? (
          <div className="empty-state">No selections logged yet — run the Test panel or the /select endpoint to populate the corpus.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div><h3 style={{ marginBottom: 8 }}>Cost by day</h3><BarChart rows={d.by_day} labelKey="day" valueKey="cost_usd" format={usd} color="#16a34a" /></div>
            <div><h3 style={{ marginBottom: 8 }}>Selections by day</h3><BarChart rows={d.by_day} labelKey="day" valueKey="selections" color="#2f6feb" /></div>
            <div><h3 style={{ marginBottom: 8 }}>Layer mix (which tier answered)</h3><BarChart rows={d.by_layer} labelKey="layer" valueKey="count" color="#f59e0b" /></div>
            <div><h3 style={{ marginBottom: 8 }}>Reason codes</h3><BarChart rows={d.by_reason} labelKey="reason_code" valueKey="count" color="#a855f7" /></div>
          </div>
        )}
      </section>
    </div>
  );
}

// --- Movement Playground: record real cursor paths, compare vs generated -----
const W = 720, H = 440;
const DEFAULT_START = { x: 90, y: 360 };
const DEFAULT_TARGET = { x: 470, y: 70, w: 150, h: 64 };

function PlaygroundPanel() {
  const svgRef = useRef(null);
  const [start, setStart] = useState(DEFAULT_START);
  const [target, setTarget] = useState(DEFAULT_TARGET);
  const [mode, setMode] = useState("record"); // record | set-start | set-target
  const [recording, setRecording] = useState(false);
  const [recorded, setRecorded] = useState([]);
  const [generated, setGenerated] = useState([]);
  const [corpus, setCorpus] = useState(0);
  const [saveMsg, setSaveMsg] = useState(null);
  const [playhead, setPlayhead] = useState(null); // {rec:{x,y}, gen:{x,y}}
  const startRef = useRef(0);

  const targetCenter = { x: target.x + target.w / 2, y: target.y + target.h / 2 };

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/api/select/trajectories/count`);
        if (r.ok) setCorpus((await r.json()).corpus_size ?? 0);
      } catch { /* best-effort */ }
    })();
  }, []);

  const toSvg = (e) => {
    const rect = svgRef.current.getBoundingClientRect();
    return { x: ((e.clientX - rect.left) / rect.width) * W, y: ((e.clientY - rect.top) / rect.height) * H };
  };

  const onDown = (e) => {
    const p = toSvg(e);
    if (mode === "set-start") { setStart(p); setMode("record"); return; }
    if (mode === "set-target") { setTarget({ x: p.x - 75, y: p.y - 32, w: 150, h: 64 }); setMode("record"); return; }
    // record: begin capturing from the start point
    setRecording(true);
    setRecorded([{ ...start, t: 0 }]);
    setGenerated([]); setPlayhead(null); setSaveMsg(null);
    startRef.current = performance.now();
  };
  const onMove = (e) => {
    if (!recording) return;
    const p = toSvg(e);
    setRecorded((prev) => [...prev, { ...p, t: performance.now() - startRef.current }]);
  };
  const onUp = () => {
    if (!recording) return;
    setRecording(false);
    setRecorded((prev) => {
      if (prev.length < 2) return prev;
      const dist = Math.hypot(targetCenter.x - prev[0].x, targetCenter.y - prev[0].y);
      setGenerated(minJerkPath(prev[0], targetCenter, 60, fittsMs(dist, target.w)));
      return prev;
    });
  };

  const replay = useCallback(() => {
    const rec = recorded, gen = generated;
    if (rec.length < 2) return;
    const dur = Math.max(rec[rec.length - 1].t, 300);
    const t0 = performance.now();
    const step = () => {
      const e = performance.now() - t0;
      const at = (pts, useT) => {
        if (useT) {
          let i = pts.findIndex((p) => p.t >= e);
          if (i < 0) i = pts.length - 1;
          return pts[i];
        }
        return pts[Math.min(pts.length - 1, Math.floor((e / dur) * (pts.length - 1)))];
      };
      setPlayhead({ rec: at(rec, true), gen: gen.length ? at(gen, false) : null });
      if (e < dur) requestAnimationFrame(step); else setPlayhead(null);
    };
    requestAnimationFrame(step);
  }, [recorded, generated]);

  const save = useCallback(async () => {
    if (recorded.length < 2) return;
    try {
      const r = await fetch(`${API}/api/select/trajectory`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start: recorded[0], target, viewport: { w: W, h: H },
          path: recorded, endpoint: recorded[recorded.length - 1],
          duration_ms: recorded[recorded.length - 1].t,
          source: "playground_human", label: "human_cursor",
        }),
      });
      const p = await r.json();
      setCorpus(p.corpus_size ?? corpus + 1);
      setSaveMsg("Saved to corpus ✓");
    } catch (e) { setSaveMsg(`Save failed: ${e.message}`); }
  }, [recorded, target, corpus]);

  const recMetrics = recorded.length > 1
    ? { dur: Math.round(recorded[recorded.length - 1].t), len: Math.round(pathLength(recorded)), direct: directness(recorded).toFixed(2) }
    : null;
  const genMetrics = generated.length > 1
    ? { dur: Math.round(generated[generated.length - 1].t), len: Math.round(pathLength(generated)), direct: directness(generated).toFixed(2) }
    : null;

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Movement Playground</h2>
            <p>Press and drag from the start dot to the target box to record your real cursor motion. We overlay the model&apos;s generated path (minimum-jerk) so you can compare smoothness — and every recording grows the corpus the diffusion input-model will train on.</p>
          </div>
          <span className="inline-badge">corpus: {corpus}</span>
        </div>

        <div className="controller-actions" style={{ marginBottom: 10, gap: 8, display: "flex", flexWrap: "wrap" }}>
          <button className={`ghost-btn small-btn ${mode === "set-start" ? "active" : ""}`} onClick={() => setMode("set-start")}>Set start</button>
          <button className={`ghost-btn small-btn ${mode === "set-target" ? "active" : ""}`} onClick={() => setMode("set-target")}>Set target</button>
          <button className="ghost-btn small-btn" onClick={replay} disabled={recorded.length < 2}>Replay</button>
          <button className="primary-btn small-btn" onClick={save} disabled={recorded.length < 2}>Save to corpus</button>
          <button className="ghost-btn small-btn" onClick={() => { setRecorded([]); setGenerated([]); setPlayhead(null); setSaveMsg(null); }}>Clear</button>
          {mode !== "record" && <span className="system-micro-copy">Click on the canvas to place the {mode === "set-start" ? "start point" : "target"}.</span>}
          {saveMsg && <span className="system-micro-copy">{saveMsg}</span>}
        </div>

        <svg
          ref={svgRef} viewBox={`0 0 ${W} ${H}`}
          style={{ width: "100%", maxWidth: W, border: "1px solid #e5e7eb", borderRadius: 8, background: "#fafafa", cursor: mode === "record" ? "crosshair" : "pointer", touchAction: "none" }}
          onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}
        >
          {/* target box */}
          <rect x={target.x} y={target.y} width={target.w} height={target.h} rx={6} fill="#dbeafe" stroke="#2f6feb" strokeWidth={2} />
          <text x={targetCenter.x} y={targetCenter.y + 4} textAnchor="middle" fontSize="13" fill="#1e3a8a">target</text>
          {/* recorded path (your motion) */}
          {recorded.length > 1 && <polyline points={polyline(recorded)} fill="none" stroke="#dc2626" strokeWidth={2.5} />}
          {/* generated path (model) */}
          {generated.length > 1 && <polyline points={polyline(generated)} fill="none" stroke="#2f6feb" strokeWidth={2} strokeDasharray="5 4" />}
          {/* start dot */}
          <circle cx={start.x} cy={start.y} r={8} fill="#16a34a" />
          <text x={start.x} y={start.y + 24} textAnchor="middle" fontSize="12" fill="#166534">start</text>
          {/* playheads */}
          {playhead?.rec && <circle cx={playhead.rec.x} cy={playhead.rec.y} r={6} fill="#dc2626" />}
          {playhead?.gen && <circle cx={playhead.gen.x} cy={playhead.gen.y} r={6} fill="#2f6feb" />}
        </svg>

        <div className="system-card-grid" style={{ marginTop: 12 }}>
          <article className="system-card">
            <div className="system-card-header"><h3 style={{ color: "#dc2626" }}>Your motion (recorded)</h3></div>
            {recMetrics ? (
              <p className="system-card-copy">{recMetrics.dur} ms · {recMetrics.len} px · directness {recMetrics.direct}</p>
            ) : <p className="system-micro-copy">Drag from start to target to record.</p>}
          </article>
          <article className="system-card">
            <div className="system-card-header"><h3 style={{ color: "#2f6feb" }}>Model motion (min-jerk)</h3></div>
            {genMetrics ? (
              <p className="system-card-copy">{genMetrics.dur} ms · {genMetrics.len} px · directness {genMetrics.direct}</p>
            ) : <p className="system-micro-copy">Generated after you record.</p>}
          </article>
        </div>
        <div className="system-summary" style={{ marginTop: 12 }}>
          Today the model path is deterministic <strong>minimum-jerk</strong> (the Phase 6 driver) — smooth but identical every time. The <strong>diffusion input-model</strong> trains on the recordings you save here to reproduce <em>your</em> motion&apos;s variability and feel. Directness near 1.0 = straight; humans curve, so recorded directness is usually a bit lower than the model&apos;s.
        </div>
      </section>
    </div>
  );
}

// --- Test panel: run the live SELECT cascade against a capture + goal --------
function TestPanel() {
  const [obs, setObs] = useState([]);
  const [filename, setFilename] = useState("");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/api/observations`);
        const rows = await r.json();
        const list = Array.isArray(rows) ? rows : [];
        setObs(list);
        if (list.length) setFilename(list[0].filename);
      } catch { /* best-effort */ }
    })();
  }, []);

  const run = useCallback(async () => {
    if (!filename || !goal.trim()) return;
    setBusy(true); setError(null); setResult(null);
    try {
      const r = await fetch(`${API}/api/observations/${encodeURIComponent(filename)}/select?element_query=${encodeURIComponent(goal)}`, { method: "POST" });
      const payload = await r.json();
      if (!r.ok) throw new Error(payload.detail || `Select ${r.status}`);
      setResult(payload);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [filename, goal]);

  const badge = result ? (result.status === "resolved" ? "status-healthy" : "status-down") : "";

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header"><div><h2>Model Test</h2><p>Run the live SELECT cascade (classify → cache → Haiku) against a capture. Budget-gated; cache hits are free.</p></div></div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 720 }}>
          <label className="nav-label">Capture</label>
          <select className="form-input" value={filename} onChange={(e) => setFilename(e.target.value)}>
            {obs.map((o) => <option key={o.filename} value={o.filename}>{(o.domain_id || "?")}/{o.goal_id || o.scenario_id || "?"} · {o.filename.slice(0, 19)}</option>)}
          </select>
          <label className="nav-label">Target / goal</label>
          <input className="form-input" placeholder="e.g. the password field" value={goal} onChange={(e) => setGoal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} />
          <div className="detail-actions">
            <button className="primary-btn" onClick={run} disabled={busy || !filename || !goal.trim()}>{busy ? "Running…" : "Run SELECT"}</button>
          </div>
        </div>
        {error && <div className="empty-state error">{error}</div>}
        {result && (
          <div className="status-stack" style={{ marginTop: 12 }}>
            <div className="status-row"><span className="status-key">Status</span><span className={`inline-badge ${badge}`}>{result.status}</span></div>
            <div className="status-row"><span className="status-key">Layer</span><span className="status-value mono">{result.layer}</span></div>
            <div className="status-row"><span className="status-key">Action</span><span className="status-value mono">{result.action_id}</span></div>
            <div className="status-row"><span className="status-key">Target (backend_node_id)</span><span className="status-value mono">{result.target_backend_node_id ?? "—"}</span></div>
            <div className="status-row"><span className="status-key">Picked element</span><span className="status-value">{result.candidate ? `${result.candidate.role} · ${result.candidate.name}` : "—"}</span></div>
            <div className="status-row"><span className="status-key">Confidence</span><span className="status-value">{result.confidence}</span></div>
            <div className="status-row"><span className="status-key">Reason</span><span className="status-value mono">{result.reason_code}</span></div>
            <div className="status-row"><span className="status-key">Cost</span><span className="status-value">{usd(result.cost_usd)}</span></div>
          </div>
        )}
      </section>
    </div>
  );
}
