import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

const REASON_META = {
  escalated: { tone: "warning", hint: "the teacher itself punted — never a positive label" },
  verify_failed: { tone: "danger", hint: "the action did not advance the page" },
  low_confidence: { tone: "warning", hint: "borderline teacher pick — needs a human look" },
};

/**
 * Corpus Scorecard — "is this data good enough to train on?" The raw corpus stays
 * the record of every decision; this view applies the quality GATE (a derived
 * classification) so bad rows are quarantined for review instead of being silently
 * distilled into the cheap models. Joins three truth signals per state: teacher
 * self-confidence, behavioral verify, and human review. Reads GET /api/training/scorecard.
 */
export function ScorecardSection() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [onlyQuarantined, setOnlyQuarantined] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${API}/api/training/scorecard`);
      if (!r.ok) throw new Error(`Scorecard failed: ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const t = data?.totals;
  const q = data?.quarantine_breakdown;
  const states = (data?.states ?? []).filter((s) => !onlyQuarantined || !s.train_eligible);
  const eligiblePct = t && t.states ? Math.round((100 * t.train_eligible) / t.states) : 0;

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <h2>Corpus Scorecard</h2>
          <p>The quality gate — what's strong enough to train on vs. quarantined for review. The corpus stays raw; this is a derived view.</p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label className="chrome-label" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={onlyQuarantined} onChange={(e) => setOnlyQuarantined(e.target.checked)} />
            only quarantined
          </label>
          <button className="ghost-btn small-btn" onClick={load} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <span className="capture-error">{error}</span> : null}

      {t ? (
        <>
          <div className="coverage-totals" style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
            <Stat label="States" value={t.states} />
            <Stat label="Train-eligible" value={`${t.train_eligible} (${eligiblePct}%)`} good />
            <Stat label="Quarantined" value={t.quarantined} accent={t.quarantined > 0} />
            <Stat label="Mean confidence" value={t.mean_confidence ?? "—"} />
            <Stat label="Verified" value={`${t.verified_states}/${t.states}`} />
            <Stat label="Escalation rate" value={t.escalation_rate ?? "—"} accent={(t.escalation_rate ?? 0) > 0.3} />
            <Stat label="Human golden labels" value={t.human_confirmed_label} accent={t.human_confirmed_label === 0} />
            <Stat label="Teacher accuracy" value={t.agreement_pct != null ? `${t.agreement_pct}%` : "—"}
              good={(t.agreement_pct ?? 0) >= 80} accent={t.agreement_pct != null && t.agreement_pct < 60} />
          </div>

          {data.agreement && data.agreement.scored > 0 ? (
            <div className="chrome-label muted" style={{ marginBottom: 12 }}>
              teacher-vs-human over {data.agreement.scored} golden-labeled states:
              {" "}{data.agreement.golden} exact · {data.agreement.acceptable} acceptable · {data.agreement.miss} miss
              {data.agreement.no_model_pick ? ` · ${data.agreement.no_model_pick} not-yet-seen by model` : ""}
              {" "}— this is real accuracy (golden OR acceptable counts), not the teacher's self-confidence.
            </div>
          ) : (
            <div className="chrome-label muted" style={{ marginBottom: 12 }}>
              teacher accuracy needs golden labels — label states in the Training Space and this fills in.
            </div>
          )}

          {/* train-eligible bar */}
          <div style={{ height: 10, borderRadius: 6, overflow: "hidden", display: "flex", marginBottom: 6, background: "rgba(127,127,127,0.12)" }}>
            <div style={{ width: `${eligiblePct}%`, background: "#16a34a" }} title={`${t.train_eligible} train-eligible`} />
            <div style={{ width: `${100 - eligiblePct}%`, background: "#d97706" }} title={`${t.quarantined} quarantined`} />
          </div>
          <div className="chrome-label muted" style={{ marginBottom: 14 }}>
            escalated {q?.escalated ?? 0} · verify-failed {q?.verify_failed ?? 0} · low-confidence {q?.low_confidence ?? 0}
            {t.human_confirmed_label === 0 ? "  —  no golden labels stored yet: Review/Label confirm isn't persisting positive_candidate_id" : null}
          </div>
        </>
      ) : null}

      <div className="coverage-rows" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {states.map((s, i) => {
          const meta = s.train_eligible ? { tone: "success", hint: "passes the gate — train on it" } : (REASON_META[s.quarantine_reason] ?? { tone: "neutral", hint: "" });
          return (
            <div key={`${s.fingerprint}-${i}`} title={meta.hint}
              style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 10px",
                borderRadius: 8, background: "rgba(127,127,127,0.06)" }}>
              <span className={`coverage-status coverage-status--${meta.tone}`} />
              <span style={{ flex: 1, fontFamily: "monospace", fontSize: 13 }}>{s.route}</span>
              <span className="chip">{s.action_id}</span>
              <span className="chrome-label muted" style={{ minWidth: 70, textAlign: "right" }}>
                conf {s.confidence ?? "—"}
              </span>
              <span className="chrome-label muted" style={{ minWidth: 76, textAlign: "right" }}>
                verify {s.verify_ok === true ? "yes" : s.verify_ok === false ? "no" : "—"}
              </span>
              <span className="chip" style={{ minWidth: 96, textAlign: "right" }}>
                {s.train_eligible ? "eligible" : s.quarantine_reason}
              </span>
            </div>
          );
        })}
      </div>

      {data && states.length === 0 ? (
        <div className="empty-state">No corpus rows yet — run a capture burst + run_batch first.</div>
      ) : null}
    </section>
  );
}

function Stat({ label, value, accent, good }) {
  const color = good ? "var(--success)" : accent ? "var(--warning)" : "inherit";
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <span className="chrome-label muted">{label}</span>
      <span style={{ fontSize: 22, fontWeight: 600, color }}>{value}</span>
    </div>
  );
}
