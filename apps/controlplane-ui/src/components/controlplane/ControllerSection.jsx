import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL;

// The rung colours read cheapest→most-expensive: green (free recipe) → amber (model) →
// red (needs a human). One glance tells the operator how much the controller is paying.
const RUNG_STYLE = {
  recipe: { label: "recipe · $0", color: "#16a34a" },
  cache: { label: "cache · $0", color: "#8fb28a" },
  model: { label: "model · Haiku", color: "#f59e0b" },
  teacher: { label: "teacher · Claude", color: "#a855f7" },
  human: { label: "human", color: "#dc2626" },
};

function pct(x) {
  return `${Math.round((Number(x) || 0) * 100)}%`;
}

function Tile({ label, value, sub, accent }) {
  return (
    <div className="panel" style={{ flex: "1 1 150px", minWidth: 140, padding: "14px 16px" }}>
      <div className="system-micro-copy" style={{ textTransform: "uppercase", letterSpacing: 0.4, fontSize: "0.68rem" }}>
        {label}
      </div>
      <div style={{ fontSize: "1.7rem", fontWeight: 700, color: accent || "inherit", lineHeight: 1.15 }}>
        {value}
      </div>
      {sub ? <div className="system-micro-copy" style={{ fontSize: "0.72rem" }}>{sub}</div> : null}
    </div>
  );
}

function RungBadge({ rung }) {
  const s = RUNG_STYLE[rung] || { label: rung || "?", color: "#64748b" };
  return (
    <span className="inline-badge mono" style={{ background: `${s.color}1a`, color: s.color, borderColor: `${s.color}55` }}>
      {s.label}
    </span>
  );
}

// A display-side mirror of interaction.decision.is_real_rationale — the AUTHORITATIVE gate is
// server-side (summarize's reasoned_rate); this only tints a blank/stub "why" red in the feed.
const PLACEHOLDER_WHY = new Set([
  "", "operator correction", "correction", "teacher correction", "manual", "n/a", "na", "none",
  "-", "--", "x", "t", "r", "login", "test", "todo", "tbd", "fixme", "wip", "placeholder",
]);
function isRealWhy(s) {
  if (!s) return false;
  const t = String(s).trim().toLowerCase();
  return !PLACEHOLDER_WHY.has(t) && t.length >= 8;
}

// The Bundle facts a rationale cites — rendered as small chips (§10: the receipts, not a vibe).
function EvidenceChips({ items }) {
  const list = items || [];
  if (!list.length) return null;
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", verticalAlign: "middle" }}>
      {list.map((e, i) => (
        <span key={i} className="mono" style={{ fontSize: "0.68rem", background: "#1417120a", color: "#475569", border: "1px solid #e2e8f0", borderRadius: 4, padding: "1px 5px" }}>
          {e}
        </span>
      ))}
    </span>
  );
}

// One entry in the reasoning feed — the teacher's (or model's) WHY + the facts it cites. On a
// golden correction it shows BOTH sides: the contrast between the backstop's why and the teacher's
// is the lesson (§10 — the Open Brain).
function ReasoningEntry({ d }) {
  const isCorrection = d.golden && d.proposed_intent;
  const real = isRealWhy(d.rationale);
  const color = RUNG_STYLE[d.rung]?.color || "#64748b";
  return (
    <div className="panel" style={{ padding: "12px 14px", borderLeft: `4px solid ${color}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <RungBadge rung={d.rung} />
        <strong className="mono">{d.escalate ? "↑ escalate" : d.intent}</strong>
        {d.params?.field ? <span className="mono system-micro-copy">{d.params.field}</span> : null}
        {isCorrection ? (
          <span className="inline-badge" style={{ background: "#a855f71a", color: "#a855f7", borderColor: "#a855f755" }}>correction</span>
        ) : null}
        <span className="system-micro-copy" style={{ marginLeft: "auto" }}>{d.state || ""}</span>
      </div>
      <p style={{ margin: "8px 0 0", fontStyle: "italic", color: real ? "#495143" : "#dc2626" }}>
        {d.rationale ? d.rationale : "No reasoning was captured (§10)"}
      </p>
      {d.evidence?.length ? (
        <div className="system-micro-copy" style={{ marginTop: 6, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          cites <EvidenceChips items={d.evidence} />
        </div>
      ) : null}
      {isCorrection ? (
        <div style={{ marginTop: 8, padding: "8px 10px", background: "#a855f70d", borderRadius: 6, border: "1px solid #a855f722" }}>
          <div className="system-micro-copy" style={{ marginBottom: 3 }}>
            backstop proposed <span className="mono">{d.proposed_intent}</span>
            {d.proposed_rung ? <> · <RungBadge rung={d.proposed_rung} /></> : null}
          </div>
          <div className="system-micro-copy" style={{ fontStyle: "italic", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            {d.proposed_rationale || "(no why given)"} <EvidenceChips items={d.proposed_evidence} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function DecisionCard({ decision, prompt, bundle, cost }) {
  if (!decision) return null;
  const esc = decision.escalate;
  return (
    <div className="panel" style={{ marginTop: 12, borderLeft: `4px solid ${esc ? "#dc2626" : "#16a34a"}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <RungBadge rung={decision.rung} />
        <span className="inline-badge" style={{ background: esc ? "#dc26261a" : "#16a34a1a", color: esc ? "#dc2626" : "#16a34a" }}>
          {esc ? "ESCALATE ↑" : "ACT"}
        </span>
        <strong className="mono" style={{ fontSize: "1.05rem" }}>{decision.intent}</strong>
        {decision.params && Object.keys(decision.params).length ? (
          <span className="mono system-micro-copy">{JSON.stringify(decision.params)}</span>
        ) : null}
        <span style={{ marginLeft: "auto" }} className="system-micro-copy">
          confidence <strong>{decision.confidence?.toFixed?.(2) ?? decision.confidence}</strong>
          {cost ? ` · $${Number(cost).toFixed(4)}` : ""}
        </span>
      </div>
      <p style={{ margin: "10px 0 0", fontStyle: "italic", color: "#475569" }}>{decision.rationale}</p>
      {decision.evidence?.length ? (
        <div className="system-micro-copy" style={{ marginTop: 6, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          cites <EvidenceChips items={decision.evidence} />
        </div>
      ) : null}
      {decision.expected_next?.length ? (
        <div className="system-micro-copy" style={{ marginTop: 6 }}>
          expects → <span className="mono">{decision.expected_next.join(", ")}</span>
        </div>
      ) : null}
      {prompt ? (
        <details style={{ marginTop: 10 }}>
          <summary className="system-micro-copy" style={{ cursor: "pointer" }}>
            the bundle the reasoner read {bundle?.ats ? `· ${bundle.ats} · ${bundle.state || "unknown state"}` : ""}
          </summary>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: "0.75rem", background: "#1417120a", padding: 12, borderRadius: 6, marginTop: 6 }}>
            {prompt}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

export function ControllerSection() {
  const [summary, setSummary] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // preview form
  const [task, setTask] = useState("indeed_apply");
  const [url, setUrl] = useState("");
  const [allowModel, setAllowModel] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, d] = await Promise.all([
        fetch(`${API}/api/controller/summary`).then((r) => r.json()),
        fetch(`${API}/api/controller/decisions?limit=40`).then((r) => r.json()),
      ]);
      setSummary(s);
      setDecisions(d.decisions || []);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const runPreview = useCallback(async () => {
    setPreviewing(true);
    setError(null);
    try {
      const isLive = url.includes("://") && url.includes("http");
      const body = {
        task,
        goal_text: task === "indeed_apply" ? "apply with indeed" : "apply",
        allow_model: allowModel,
        ...(allowModel ? { budget_limit: null } : {}),
        // A full http(s) url is treated as a manual url; the operator can paste a tab URL too.
        ...(isLive ? { url } : { url }),
      };
      const res = await fetch(`${API}/api/controller/observe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then((r) => r.json());
      setPreview(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setPreviewing(false);
    }
  }, [task, url, allowModel]);

  const rungMix = summary?.by_rung || [];
  const totalRung = rungMix.reduce((a, r) => a + (r.count || 0), 0) || 1;
  const agreement = summary?.agreement || { agreement: 0, n: 0, by_scenario: [] };
  const programs = summary?.programs || [];
  // The reasoning feed shows the rows that actually REASON — teacher/model decisions and golden
  // corrections. Recipe replays carry a templated rationale, not a taught "why".
  const reasoningRows = decisions.filter((d) => d.golden || d.rung === "teacher" || d.rung === "model");

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Controller — the teachable decide()</h2>
            <p>
              observe() → <strong>decide()</strong> → act(). The cheapest confident rung wins; anything
              uncertain escalates to the teacher or you. Every decision is journaled as training data.
            </p>
          </div>
          <div className="controller-actions">
            <button className="ghost-btn small-btn" onClick={load} disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </div>

        {error && <div className="empty-state error">{error}</div>}

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
          <Tile label="Decisions journaled" value={summary?.corpus_size ?? "—"} sub="the controller corpus" />
          <Tile label="Verified rate" value={pct(summary?.verified_rate)} sub="landed where it expected" accent="#16a34a" />
          <Tile label="Escalation rate" value={pct(summary?.escalation_rate)} sub="handed up the ladder" accent="#f59e0b" />
          <Tile label="Intent programs" value={summary?.program_count ?? "—"} sub="the $0 rung-0 library" accent="#8fb28a" />
          <Tile
            label="Shadow agreement"
            value={agreement.n ? pct(agreement.agreement) : "—"}
            sub={agreement.n ? `over ${agreement.n} paired steps` : "no shadow runs yet"}
            accent="#a855f7"
          />
          <Tile
            label="Reasoning coverage"
            value={summary?.teach_row_count ? pct(summary?.reasoned_rate) : "—"}
            sub={summary?.teach_row_count ? `${summary.unreasoned_teach_count} blank / ${summary.teach_row_count} taught` : "§10 — no teaching rows yet"}
            accent={summary?.teach_row_count && summary?.reasoned_rate < 1 ? "#dc2626" : "#16a34a"}
          />
        </div>

        {rungMix.length ? (
          <div style={{ marginTop: 16 }}>
            <div className="system-micro-copy" style={{ marginBottom: 6 }}>Rung mix — who decided</div>
            <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", border: "1px solid #e2e8f0" }}>
              {rungMix.map((r) => {
                const s = RUNG_STYLE[r.rung] || { color: "#64748b" };
                return (
                  <div
                    key={r.rung}
                    title={`${r.rung}: ${r.count}`}
                    style={{ width: `${(r.count / totalRung) * 100}%`, background: s.color }}
                  />
                );
              })}
            </div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 6 }}>
              {rungMix.map((r) => (
                <span key={r.rung} className="system-micro-copy">
                  <RungBadge rung={r.rung} /> {r.count}
                </span>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      {/* The reasoning feed — the OPEN BRAIN: the why, with receipts, both sides of a correction (§10) */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Reasoning feed <span className="system-micro-copy">the open brain</span></h2>
            <p>
              Every teacher/model decision's <em>why</em> and the Bundle facts it cites. On a correction
              you see <strong>both</strong> sides — the contrast between the backstop's why and the
              teacher's is the lesson. This is how the teacher's reasoning transfers into the students'
              corpus (PRINCIPLES §10).
            </p>
          </div>
        </div>
        {reasoningRows.length ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {reasoningRows.slice(0, 20).map((d, i) => (
              <ReasoningEntry key={i} d={d} />
            ))}
          </div>
        ) : (
          <div className="empty-state">
            No reasoned decisions yet. During a teacher-supervised drive, every decision streams here
            with its why + evidence — watch the open brain fill.
          </div>
        )}
      </section>

      {/* Watch it think — observe→decide on a tab, WITHOUT acting */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Watch it think</h2>
            <p>
              Point the controller at a Career Search page (paste the URL, or a live tab URL) and see the
              Decision it would make — read-only, it never acts. The model rung stays off unless you allow it.
            </p>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <select className="mono" value={task} onChange={(e) => setTask(e.target.value)} style={{ padding: "6px 8px" }}>
            <option value="indeed_apply">indeed_apply</option>
            <option value="workday_apply">workday_apply</option>
            <option value="greenhouse_apply">greenhouse_apply</option>
          </select>
          <input
            className="mono"
            style={{ flex: "1 1 320px", padding: "6px 8px" }}
            placeholder="https://smartapply.indeed.com/questions/…  (or a live tab URL)"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <label className="system-micro-copy" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={allowModel} onChange={(e) => setAllowModel(e.target.checked)} />
            allow Haiku rung (budget-gated)
          </label>
          <button className="primary-btn small-btn" onClick={runPreview} disabled={previewing || !url}>
            {previewing ? "Thinking…" : "Preview decision"}
          </button>
        </div>
        {preview?.scan_detail ? (
          <div className="system-micro-copy" style={{ marginTop: 8 }}>{preview.scan_detail}</div>
        ) : null}
        {preview ? (
          <DecisionCard
            decision={preview.decision}
            prompt={preview.prompt}
            bundle={preview.bundle}
            cost={preview.model_cost_usd}
          />
        ) : null}
      </section>

      {/* The compiled programs — the $0 library */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Intent programs <span className="system-micro-copy">({programs.length})</span></h2>
            <p>Verified decision sequences compiled per (task, state). Rung 0 replays these for $0 — the flywheel's first turn, inside the controller.</p>
          </div>
        </div>
        {programs.length ? (
          <table className="data-table">
            <thead>
              <tr><th>Task</th><th>State</th><th>Steps</th><th>Guard fields</th><th>Exit</th><th>Status</th></tr>
            </thead>
            <tbody>
              {programs.map((p) => (
                <tr key={`${p.task}:${p.state}`}>
                  <td className="mono">{p.task}</td>
                  <td className="mono">{p.state}</td>
                  <td>{p.steps}</td>
                  <td className="system-micro-copy">{(p.guard_fields || []).join(", ") || "—"}</td>
                  <td className="mono system-micro-copy">{(p.expected_exit || []).join(", ") || "—"}</td>
                  <td>
                    <span className={`inline-badge ${p.stale ? "status-down" : "status-healthy"}`}>
                      {p.stale ? "stale" : "ready"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">
            No programs compiled yet. They appear once a teacher-compile drive verifies a sequence
            for a state — the first happy path turns the crank.
          </div>
        )}
      </section>

      {/* The decision feed */}
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Recent decisions <span className="system-micro-copy">({decisions.length})</span></h2>
            <p>What the reasoner decided, at which rung, and whether it verified. The corpus that trains L4.</p>
          </div>
        </div>
        {decisions.length ? (
          <table className="data-table">
            <thead>
              <tr><th>Rung</th><th>Intent</th><th>State</th><th>Outcome</th><th>Verified</th><th>Rationale</th></tr>
            </thead>
            <tbody>
              {decisions.map((d, i) => (
                <tr key={i}>
                  <td><RungBadge rung={d.rung} /></td>
                  <td className="mono">{d.escalate ? "↑ escalate" : d.intent}</td>
                  <td className="mono system-micro-copy">{d.state || "—"}</td>
                  <td className="mono">{d.outcome || "—"}</td>
                  <td>
                    {d.verified === true ? "✓" : d.verified === false ? "✗" : "—"}
                  </td>
                  <td className="system-micro-copy" style={{ maxWidth: 320 }}>{d.rationale}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty-state">
            No decisions journaled yet. Run a preview above, or drive the loop live — every step lands here.
          </div>
        )}
      </section>
    </div>
  );
}
