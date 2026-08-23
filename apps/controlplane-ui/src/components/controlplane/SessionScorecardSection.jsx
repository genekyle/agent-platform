import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;
const POLL_MS = 15000;

function Stat({ label, value, sub, accent, good }) {
  const color = good ? "var(--success)" : accent ? "var(--warning)" : "inherit";
  return (
    <div style={{ display: "flex", flexDirection: "column", minWidth: 110 }}>
      <span className="chrome-label muted">{label}</span>
      <span style={{ fontSize: 22, fontWeight: 600, color }}>{value}</span>
      {sub ? <span className="chrome-label muted">{sub}</span> : null}
    </div>
  );
}

function pct(x) {
  return x == null ? "—" : `${Math.round(Number(x) * 100)}%`;
}

/** Applications per week as simple bars — the throughput number the whole pipeline serves. */
function WeekBars({ weeks }) {
  const max = Math.max(1, ...weeks.map((w) => w.count));
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-end", height: 72 }}>
      {weeks.map((w) => (
        <div key={w.week_start} title={`${w.week_start}: ${w.count} application${w.count === 1 ? "" : "s"}`}
          style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, flex: 1 }}>
          <span className="chrome-label muted">{w.count}</span>
          <div style={{ width: "100%", maxWidth: 42, borderRadius: 4,
            height: Math.max(4, (w.count / max) * 44),
            background: w.count ? "#16a34a" : "rgba(127,127,127,0.25)" }} />
          <span className="chrome-label muted" style={{ fontSize: 10 }}>{w.week_start.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}

/**
 * The session scorecard — `GET /api/learning/scorecard`. The docs' measure of a session (rows
 * banked, labels written, parks answered) plus the promotion gate and applications per week,
 * composed on one screen. Before 2026-08-22 three different agreement numbers sat on three
 * screens and none showed distance to the gate; the session measures rendered nowhere.
 */
export function SessionScorecardSection() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/learning/scorecard`);
      if (!r.ok) throw new Error(`scorecard failed: ${r.status}`);
      setData(await r.json());
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const s = data?.session;
  const q = data?.label_queue;
  const promo = data?.promotion;
  const witnesses = data?.witnesses;
  const apps = data?.applications;
  const outcomes = data?.outcomes;

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Session scorecard</h2>
            <p>
              A session is measured by rows banked, labels written, and parks answered — each one
              now feeds three organs. Today&apos;s tallies against the running totals, and the
              backlog the teacher still owes.
            </p>
          </div>
          <button className="ghost-btn small-btn" onClick={load} disabled={loading}>
            {loading ? "…" : "Refresh"}
          </button>
        </div>

        {error ? <span className="capture-error">{error}</span> : null}

        {s ? (
          <div className="coverage-totals" style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
            <Stat label="Rows banked today" value={s.rows_banked.today}
              sub={`${s.rows_banked.total} total`} good={s.rows_banked.today > 0} />
            <Stat label="Labels written today" value={s.labels_written.today}
              sub={`${s.labels_written.total} total`} good={s.labels_written.today > 0} />
            <Stat label="Parks answered today" value={s.parks.answered_today}
              sub={`${s.parks.answered_total} total · ${s.parks.expired} expired`} />
            <Stat label="Parks open" value={s.parks.open} accent={s.parks.open > 0} />
            <Stat label="Label queue" value={q?.remaining ?? "—"} accent={(q?.remaining ?? 0) > 0}
              sub={q?.by_reason ? Object.entries(q.by_reason).map(([k, v]) => `${v} ${k}`).join(" · ") : null} />
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Applications per week</h2>
            <p>Dated by when the application was marked applied (or the row was created).</p>
          </div>
          {apps ? (
            <div style={{ display: "flex", gap: 16 }}>
              <Stat label="This week" value={apps.this_week} good={apps.this_week > 0} />
              <Stat label="All time" value={apps.total} />
            </div>
          ) : null}
        </div>
        {apps?.by_week?.length ? <WeekBars weeks={apps.by_week} /> : null}

        {outcomes ? (
          <div style={{ marginTop: 14, display: "flex", gap: 20, flexWrap: "wrap", alignItems: "flex-end" }}>
            <Stat label="Outcomes recorded" value={outcomes.outcomes_recorded}
              accent={outcomes.outcomes_recorded === 0}
              sub={outcomes.outcomes_recorded === 0 ? "the ledger is write-only after submit" : null} />
            <Stat label="Flows closed" value={`${outcomes.flows.closed}/${outcomes.flows.total}`}
              accent={outcomes.flows.total > 0 && outcomes.flows.closed < outcomes.flows.total} />
            {/* Event kinds render GENERICALLY — when the inbox matcher starts writing new kinds
                (rejection, interview_invite, …) they appear here with no UI change. */}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {Object.entries(outcomes.events_by_kind || {}).map(([kind, n]) => (
                <span key={kind} className="chip">{kind} · {n}</span>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Promotion gate — per-scenario shadow agreement</h2>
            <p>
              The displayed gate: agreement ≥ {Math.round((promo?.gate?.min_agreement ?? 0.9) * 100)}%
              over ≥ {promo?.gate?.min_n ?? 25} paired teacher steps, judged per scenario
              (ats:state) — never a global average. Note: this gate is what the operator reads;
              what <code>authority()</code> enforces is the maturity ladder
              (CONTROLLER_PROMOTION.md, 2026-08-20).
            </p>
          </div>
          {promo ? (
            <Stat label="Overall (context only)" value={pct(promo.overall.agreement)}
              sub={`${promo.overall.n} paired steps`} />
          ) : null}
        </div>

        {promo?.scenarios?.length ? (
          <table className="data-table">
            <thead>
              <tr><th>Scenario (ats:state)</th><th>Paired</th><th>Agreement</th><th>Gate</th></tr>
            </thead>
            <tbody>
              {promo.scenarios.map((sc) => {
                const agreementShort = sc.agreement < (promo.gate?.min_agreement ?? 0.9);
                const need = [];
                if (sc.n_needed > 0) need.push(`${sc.n_needed} more paired steps`);
                if (agreementShort) need.push(`agreement below ${Math.round((promo.gate?.min_agreement ?? 0.9) * 100)}%`);
                return (
                  <tr key={sc.scenario}>
                    <td className="mono">{sc.scenario}</td>
                    <td>{sc.agree}/{sc.n}</td>
                    <td style={{ color: agreementShort ? "var(--warning)" : "var(--success)" }}>
                      {pct(sc.agreement)}
                    </td>
                    <td>
                      {sc.passes
                        ? <span className="inline-badge status-healthy">passes</span>
                        : <span className="chrome-label muted">{need.join(" · ") || "—"}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">
            No paired shadow rows yet — agreement appears once the controller decides silently
            beside the teacher.
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Witness corpus</h2>
            <p>
              What the perception witnesses train on. It grows only from new live captures —
              <em> from transitions</em> is the share the drive flywheel contributed.
            </p>
          </div>
        </div>
        {witnesses ? (
          <div className="coverage-totals" style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
            <Stat label="Labeled" value={witnesses.labeled} />
            <Stat label="From transitions" value={witnesses.from_transitions}
              good={witnesses.from_transitions > 0} />
            <Stat label="Self-supervised" value={witnesses.from_self_supervision ?? "—"} />
            <Stat label="Teacher-superseded" value={witnesses.superseded_by_teacher ?? "—"} />
            <Stat label="With screenshot" value={`${witnesses.with_screenshot}/${witnesses.labeled}`}
              accent={witnesses.labeled > 0 && witnesses.with_screenshot === 0} />
            <Stat label="Missing artifact" value={witnesses.missing_artifact}
              accent={witnesses.missing_artifact > 0} />
          </div>
        ) : (
          <div className="empty-state">The witness census could not load — the corpus reader is unavailable.</div>
        )}
      </section>
    </div>
  );
}
