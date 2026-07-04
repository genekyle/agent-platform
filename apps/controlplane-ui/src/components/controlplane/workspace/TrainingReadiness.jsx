import { useCallback, useEffect, useState } from "react";
import { getJSON, postJSON } from "./api";

// The money-saving flywheel for one domain, made legible. Claude/Haiku is the TEACHER: every
// paid pick also emits a page-state label (L3 fuel) and a selection row (L4 fuel) the cheap
// local students distill from. This panel shows how close those students are to taking work
// off the teacher — and points at the next gap to capture.

const PCT = (x) => `${Math.round((x || 0) * 100)}%`;
const STATUS_BADGE = { gap: "badge--danger", thin: "badge--warn", covered: "badge--ok", over: "badge--muted" };

function Bar({ value, max, tone = "var(--cc-accent)" }) {
  const pct = Math.max(0, Math.min(100, max ? (value / max) * 100 : 0));
  return (
    <div style={{ height: 6, background: "#eef2f8", borderRadius: 999, overflow: "hidden", minWidth: 60 }}>
      <div style={{ width: `${pct}%`, height: "100%", background: tone, borderRadius: 999 }} />
    </div>
  );
}

function Metric({ label, value, sub, tone }) {
  return (
    <div className="stat-card" style={{ padding: "12px 14px", borderTop: `3px solid ${tone || "#cbd5e1"}` }}>
      <div className="stat-label" style={{ marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1 }}>{value}</div>
      {sub && <div className="stat-footnote" style={{ marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

export function TrainingReadiness({ domain, onOpenTraining }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = useCallback(() => {
    getJSON(`/api/domains/${domain.id}/training_readiness`).then(setData).catch(() => {});
  }, [domain.id]);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const trainL3 = async () => {
    if (!window.confirm("Build the dataset and train the page-state classifier now? This runs on the server and can take a little while.")) return;
    setBusy(true); setMsg(null);
    try {
      await postJSON("/api/training/build-dataset");
      const r = await postJSON("/api/training/train_stage_observer");
      setMsg({ type: "ok", text: `Training kicked off — ${JSON.stringify(r).slice(0, 200)}` });
      load();
    } catch (e) {
      setMsg({ type: "error", text: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  };

  if (!data) return <div className="empty-hint">Loading training readiness…</div>;

  const { coverage: cov, l3, l4, next_gap: gap, states } = data;

  return (
    <div className="cockpit">
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title">🎓 Teaching the cheap models</div>
          <span className="layer__sub">Claude is the teacher — the students distill from every rep</span>
        </div>
        <p className="mode-hint" style={{ marginTop: 0 }}>
          Every paid pick also labels a page-state (fuel for the <strong>L3</strong> screen classifier) and logs a
          selection (fuel for the <strong>L4</strong> element selector). As those students get trained they slot in
          <em> below</em> the Haiku catchall in the cascade — driving the Haiku share (and cost) down.
        </p>

        {/* The money-saving scoreboard — system-wide SELECT telemetry */}
        <div className="stats-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginTop: 4 }}>
          <Metric label="Haiku share ↓" value={PCT(l4.haiku_share)} tone="#ea580c" sub="drive this down" />
          <Metric label="Cache hits ↑" value={PCT(l4.cache_hit_rate)} tone="#16a34a" sub="free, practiced picks" />
          <Metric label="Escalations" value={PCT(l4.escalation_rate)} tone="#3b82f6" sub="handed to a human" />
          <Metric label="Avg cost / pick" value={`$${(l4.avg_cost_usd || 0).toFixed(4)}`} tone="#7c3aed" sub={`${l4.corpus_size} picks logged`} />
        </div>
        <div className="status-card__meta" style={{ marginTop: 8 }}>
          Scoreboard is system-wide SELECT telemetry (not yet sharded per domain).
        </div>
      </div>

      {/* The two students */}
      <div className="cockpit-grid">
        <div className="layer">
          <div className="layer__head">
            <div className="layer__title">🧭 L3 · Page-state classifier</div>
            <span className={`badge ${l3.enough_to_train ? "badge--ok" : "badge--muted"}`}>
              {l3.enough_to_train ? "Ready to train" : "Needs data"}
            </span>
          </div>
          <p className="layer__sub" style={{ marginBottom: 10 }}>“Which Marketplace screen am I on?” — cheap, gates which recipe runs.</p>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 4 }}>
            <span>Deep states (≥{l3.min_per_state} examples)</span>
            <strong>{l3.deep_states} / {l3.min_deep_states} needed</strong>
          </div>
          <Bar value={l3.deep_states} max={l3.min_deep_states} tone={l3.enough_to_train ? "#16a34a" : "#d29922"} />
          <div className="mode-hint" style={{ marginTop: 10 }}>
            {l3.enough_to_train
              ? "Enough depth to train a first classifier."
              : `Get ${l3.min_deep_states} states to ≥${l3.min_per_state} examples each. Capture the thin states below.`}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="btn btn-primary" disabled={!l3.enough_to_train || busy} onClick={trainL3}>
              {busy ? "Training…" : "Build dataset & train"}
            </button>
          </div>
          {msg && <div className={msg.type === "error" ? "error-banner" : "mode-hint"} style={{ marginTop: 10 }}>{msg.text}</div>}
        </div>

        <div className="layer">
          <div className="layer__head">
            <div className="layer__title">🎯 L4 · Element selector</div>
            <span className="badge badge--muted">Distilling</span>
          </div>
          <p className="layer__sub" style={{ marginBottom: 10 }}>“Which element do I act on, given the screen + goal?” — the pick that replaces Haiku.</p>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}>
            <span>Selection corpus</span><strong>{l4.corpus_size} picks</strong>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {l4.by_layer.map((r) => (
              <span key={r.layer} className="chip muted">{r.layer}: {r.count}</span>
            ))}
          </div>
          <div className="mode-hint" style={{ marginTop: 10 }}>
            Grows from every live pick + <code>run_batch</code> replays. Trains once the create-listing / reply flows
            are walked enough that the corpus has real (state → action) reps.
          </div>
        </div>
      </div>

      {/* Next gap + capture CTA */}
      {gap && (
        <div className="attention-item" style={{ borderColor: "#cfe0ff", background: "#f4f8ff" }}>
          <div style={{ fontSize: 18 }}>📸</div>
          <div className="attention-item__body">
            <div className="attention-item__why" style={{ color: "#1e3a8a" }}>
              Next to capture: {gap.display_name} <span className="muted">({gap.count}/{gap.target})</span>
            </div>
            <div className="attention-item__hint" style={{ color: "#3b5bdb" }}>
              Walk the {domain.short} flow to this screen and burst-capture ~{Math.max(1, gap.target - gap.count)} more.
            </div>
          </div>
          {onOpenTraining && <button className="btn btn-primary" onClick={onOpenTraining}>Open capture session →</button>}
        </div>
      )}

      {/* Per-state coverage — the "drive to the gaps" table */}
      <div className="layer">
        <div className="layer__head">
          <div className="layer__title">🗺️ Capture coverage</div>
          <span className="layer__count">
            {cov.covered_states}/{cov.relevant_states} covered · {cov.gap_states} gaps · {cov.tagged_captures} tagged
          </span>
        </div>
        <div className="table-wrap">
          <table className="runs-table">
            <thead><tr><th>Page state</th><th>Stage</th><th>Examples</th><th>Progress</th><th>Status</th></tr></thead>
            <tbody>
              {states.map((s) => (
                <tr key={s.state_id}>
                  <td style={{ fontWeight: 600 }}>{s.display_name}</td>
                  <td className="muted">{s.stage || "—"}</td>
                  <td>{s.count} / {s.target}</td>
                  <td style={{ width: 140 }}><Bar value={s.count} max={s.target} tone={s.count >= s.target ? "#16a34a" : "#d29922"} /></td>
                  <td><span className={`badge ${STATUS_BADGE[s.status] || "badge--muted"}`}>{s.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
