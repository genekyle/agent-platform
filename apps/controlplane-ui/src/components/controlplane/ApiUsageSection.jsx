const CONSOLE_USAGE_URL = "https://console.anthropic.com/settings/usage";
const CONSOLE_COST_URL = "https://console.anthropic.com/settings/cost";

function usd(value) {
  const n = Number(value || 0);
  // Show enough precision for sub-cent per-call costs without losing readability.
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(6)}`;
  return `$${n.toFixed(2)}`;
}

function num(value) {
  return Number(value || 0).toLocaleString();
}

function formatTs(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

export function ApiUsageSection({ usage, loadUsage }) {
  const data = usage?.data;
  const loading = usage?.loading;
  const error = usage?.error;

  const totals = data?.totals ?? { calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 };
  const budget = data?.budget ?? null;
  const byPurpose = data?.by_purpose ?? [];
  const byDay = data?.by_day ?? [];
  const recent = data?.recent ?? [];
  const keyConfigured = data?.key_configured;

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>API Usage &amp; Cost</h2>
            <p>Self-logged Claude API spend, tagged by what each call was for. The authoritative org-wide numbers live in the Anthropic Console.</p>
          </div>
          <div className="controller-actions">
            <span className={`inline-badge ${keyConfigured ? "status-healthy" : "status-down"}`}>
              {keyConfigured ? "API key configured" : "API key not set"}
            </span>
            <button className="ghost-btn small-btn" onClick={loadUsage} disabled={loading}>
              {loading ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </div>

        {error && <div className="empty-state error">{error}</div>}

        {!keyConfigured && (
          <div className="system-summary bad">
            No <span className="mono">ANTHROPIC_API_KEY</span> set yet. Add it to{" "}
            <span className="mono">apps/controlplane-api/.env</span> and run the smoke test to log a first call.
          </div>
        )}

        {budget && (() => {
          const pct = Math.min(100, Math.round((budget.fraction_used ?? 0) * 100));
          const near = pct >= 80 && !budget.over_budget;
          const barColor = budget.over_budget ? "#dc2626" : near ? "#f59e0b" : "#16a34a";
          return (
            <div style={{ marginTop: 12, marginBottom: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem", marginBottom: 6 }}>
                <span><strong>Weekly autonomous budget</strong> <span className="system-micro-copy">(rolling 7d)</span></span>
                <span className="mono">{usd(budget.spent_usd)} / {usd(budget.limit_usd)}</span>
              </div>
              <div style={{ height: 8, background: "#e5e7eb", borderRadius: 4, overflow: "hidden" }}>
                <div style={{ width: `${pct}%`, height: "100%", background: barColor, transition: "width .3s" }} />
              </div>
              {budget.over_budget ? (
                <div className="system-summary bad" style={{ marginTop: 8 }}>
                  Weekly budget exceeded — autonomous Claude calls are blocked and must escalate to a human.
                </div>
              ) : near ? (
                <div className="system-micro-copy" style={{ marginTop: 6 }}>
                  {usd(budget.remaining_usd)} remaining this week.
                </div>
              ) : null}
            </div>
          );
        })()}

        <div className="system-card-grid">
          <article className="system-card">
            <div className="system-card-header"><h3>Total Cost</h3></div>
            <p className="system-card-copy" style={{ fontSize: "1.6rem", fontWeight: 600 }}>{usd(totals.cost_usd)}</p>
          </article>
          <article className="system-card">
            <div className="system-card-header"><h3>Calls</h3></div>
            <p className="system-card-copy" style={{ fontSize: "1.6rem", fontWeight: 600 }}>{num(totals.calls)}</p>
          </article>
          <article className="system-card">
            <div className="system-card-header"><h3>Input Tokens</h3></div>
            <p className="system-card-copy" style={{ fontSize: "1.6rem", fontWeight: 600 }}>{num(totals.input_tokens)}</p>
          </article>
          <article className="system-card">
            <div className="system-card-header"><h3>Output Tokens</h3></div>
            <p className="system-card-copy" style={{ fontSize: "1.6rem", fontWeight: 600 }}>{num(totals.output_tokens)}</p>
          </article>
        </div>

        <div className="status-stack" style={{ marginTop: "1rem" }}>
          <div className="status-row">
            <span className="status-key">Anthropic Console</span>
            <span className="status-value">
              <a href={CONSOLE_USAGE_URL} target="_blank" rel="noreferrer">Usage</a>
              {" · "}
              <a href={CONSOLE_COST_URL} target="_blank" rel="noreferrer">Cost</a>
            </span>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Cost by Purpose</h2>
            <p>Which part of the loop is spending — som_pick, verify, smoke_test, etc.</p>
          </div>
        </div>
        {byPurpose.length === 0 ? (
          <div className="empty-state">No calls logged yet.</div>
        ) : (
          <div className="system-table-panel">
            <div className="system-table system-table-head" style={{ gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr" }}>
              <span>Purpose</span><span>Calls</span><span>Input</span><span>Output</span><span>Cost</span>
            </div>
            {byPurpose.map((r) => (
              <article key={r.purpose} className="system-table system-table-row" style={{ gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr" }}>
                <div className="mono">{r.purpose}</div>
                <div>{num(r.calls)}</div>
                <div>{num(r.input_tokens)}</div>
                <div>{num(r.output_tokens)}</div>
                <div>{usd(r.cost_usd)}</div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Cost by Day</h2>
            <p>Daily spend — the trend line for the 30-day n=1 cost-per-task number.</p>
          </div>
        </div>
        {byDay.length === 0 ? (
          <div className="empty-state">No calls logged yet.</div>
        ) : (
          <div className="system-table-panel">
            <div className="system-table system-table-head" style={{ gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr" }}>
              <span>Day</span><span>Calls</span><span>Input</span><span>Output</span><span>Cost</span>
            </div>
            {byDay.map((r) => (
              <article key={r.day} className="system-table system-table-row" style={{ gridTemplateColumns: "2fr 1fr 1fr 1fr 1fr" }}>
                <div className="mono">{r.day || "—"}</div>
                <div>{num(r.calls)}</div>
                <div>{num(r.input_tokens)}</div>
                <div>{num(r.output_tokens)}</div>
                <div>{usd(r.cost_usd)}</div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Recent Calls</h2>
            <p>The last {recent.length} logged calls.</p>
          </div>
        </div>
        {recent.length === 0 ? (
          <div className="empty-state">No calls logged yet.</div>
        ) : (
          <div className="system-table-panel">
            <div className="system-table system-table-head" style={{ gridTemplateColumns: "2fr 1.5fr 1fr 1fr 1fr 1fr" }}>
              <span>When</span><span>Purpose</span><span>Model</span><span>In</span><span>Out</span><span>Cost</span>
            </div>
            {recent.map((r, i) => (
              <article key={`${r.ts}-${i}`} className="system-table system-table-row" style={{ gridTemplateColumns: "2fr 1.5fr 1fr 1fr 1fr 1fr" }}>
                <div className="system-micro-copy">{formatTs(r.ts)}</div>
                <div className="mono">{r.purpose}</div>
                <div className="mono system-cell-target">{r.model}</div>
                <div>{num(r.input_tokens)}</div>
                <div>{num(r.output_tokens)}</div>
                <div>{usd(r.cost_usd)}</div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
