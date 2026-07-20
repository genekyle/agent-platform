import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

const STATUS_META = {
  gap: { tone: "danger", label: "gap", hint: "no captures yet — capture this next" },
  thin: { tone: "warning", label: "thin", hint: "below target — a few more help" },
  covered: { tone: "success", label: "covered", hint: "at target" },
  over: { tone: "neutral", label: "over", hint: "over-collected — spend diversity on a new state/domain" },
};

/**
 * Coverage tracker — the data-collection navigation aid. For the active session's
 * domain/goal/scenario it shows every relevant page-state and how many captures are
 * tagged with it, so "what should I capture next?" becomes visible at a glance.
 * Reads GET /api/training/coverage (counts come from tagged captures only).
 */
export function CoverageSection({ session }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [target, setTarget] = useState(5);

  const load = useCallback(async () => {
    if (!session) return;
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        domain_id: session.domain_id ?? "",
        goal_id: session.goal_id ?? "",
        scenario_id: session.scenario_id ?? "",
        target_per_state: String(target),
      });
      const r = await fetch(`${API}/api/training/coverage?${qs}`);
      if (!r.ok) throw new Error(`Coverage failed: ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [session, target]);

  useEffect(() => { load(); }, [load]);

  if (!session) {
    return (
      <section className="panel">
        <div className="panel-header"><div><h2>Coverage</h2>
          <p>Select an active training session to see its capture coverage.</p></div></div>
        <div className="empty-state">No session selected.</div>
      </section>
    );
  }

  const totals = data?.totals;
  const byCategory = {};
  (data?.states ?? []).forEach((s) => {
    (byCategory[`${s.scope} · ${s.category}`] ||= []).push(s);
  });

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Coverage</h2>
          <p>session-{session.id} · {session.domain_id} / {session.scenario_id} — prioritize gaps and stop once a class is covered.</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label className="chrome-label" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            target/state
            <input type="number" min={1} max={50} value={target}
              onChange={(e) => setTarget(Math.max(1, Number(e.target.value) || 1))}
              className="form-select" style={{ width: 64 }} />
          </label>
          <button className="ghost-btn small-btn" onClick={load} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <span className="capture-error">{error}</span> : null}

      {totals ? (
        <div className="coverage-totals" style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
          <Stat label="Relevant states" value={totals.relevant_states} />
          <Stat label="Covered" value={`${totals.covered_states}/${totals.relevant_states}`} />
          <Stat label="Gaps" value={totals.gap_states} accent={totals.gap_states > 0} />
          <Stat label="Tagged captures" value={totals.tagged_captures} />
          <Stat label="Untagged" value={totals.untagged_captures} accent={totals.untagged_captures > 0} />
        </div>
      ) : null}

      {Object.entries(byCategory).map(([group, states]) => (
        <div key={group} style={{ marginBottom: 14 }}>
          <div className="nav-label" style={{ marginBottom: 6 }}>{group}</div>
          <div className="coverage-rows" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {states.map((s) => {
              const meta = STATUS_META[s.status] ?? STATUS_META.gap;
              return (
                <div key={s.state_id} title={meta.hint}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 10px",
                    borderRadius: 8, background: "rgba(127,127,127,0.06)" }}>
                  <span className={`coverage-status coverage-status--${meta.tone}`} aria-label={meta.label} />
                  <span style={{ flex: 1 }}>{s.display_name}</span>
                  <span className="chrome-label muted">{s.state_id}</span>
                  <span className="chip" style={{ minWidth: 52, textAlign: "right" }}>
                    {s.count}/{s.target}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {data && data.states.length === 0 ? (
        <div className="empty-state">No page-states are scoped to this session yet.</div>
      ) : null}
    </section>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <span className="chrome-label muted">{label}</span>
      <span style={{ fontSize: 22, fontWeight: 600, color: accent ? "var(--warning)" : "inherit" }}>{value}</span>
    </div>
  );
}
