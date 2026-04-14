export function HomeSection({
  section,
  health,
  activeRuns,
  blockedRuns,
  completedRuns,
  apiLabel,
  openSystemView,
}) {
  if (section === "system-status") {
    return (
      <div className="section-stack">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>System Status</h2>
              <p>Home-level summary of readiness. Use the dedicated System workspace for full operational detail.</p>
            </div>
            <button className="ghost-btn small-btn" onClick={openSystemView}>Open System Workspace</button>
          </div>

          <div className="status-stack">
            <div className="status-row">
              <span className="status-key">API Health</span>
              <span className={health.ok ? "inline-badge ok" : "inline-badge bad"}>
                {health.loading ? "Checking..." : health.ok ? "Connected" : "Disconnected"}
              </span>
            </div>

            <div className="status-row">
              <span className="status-key">Base URL</span>
              <span className="status-value mono">{apiLabel}</span>
            </div>

            <div className="status-row">
              <span className="status-key">Notes</span>
              <span className="status-value">
                {health.ok
                  ? "API is reachable. Check System for capture server, Chrome, storage, and training gates."
                  : health.error || "Waiting for API connection."}
              </span>
            </div>
          </div>
        </section>

        <section className="stats-grid">
          <div className="stat-card">
            <div className="stat-label">Active Runs</div>
            <div className="stat-value">{activeRuns}</div>
            <div className="stat-footnote">Currently executing</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Blocked Runs</div>
            <div className="stat-value">{blockedRuns}</div>
            <div className="stat-footnote">Need intervention or approval</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Completed</div>
            <div className="stat-value">{completedRuns}</div>
            <div className="stat-footnote">Finished successfully</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">System Status</div>
            <div className="stat-value status-inline">
              {health.loading ? "..." : health.ok ? "Healthy" : "Issue"}
            </div>
            <div className="stat-footnote">
              {health.ok ? "Control plane reachable" : health.error || "Not reachable"}
            </div>
          </div>
        </section>
      </div>
    );
  }

  return (
    <>
      <section className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">Active Runs</div>
          <div className="stat-value">{activeRuns}</div>
          <div className="stat-footnote">Currently executing</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">Blocked Runs</div>
          <div className="stat-value">{blockedRuns}</div>
          <div className="stat-footnote">Need intervention or approval</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">Completed</div>
          <div className="stat-value">{completedRuns}</div>
          <div className="stat-footnote">Finished successfully</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">System Status</div>
          <div className="stat-value status-inline">
            {health.loading ? "..." : health.ok ? "Healthy" : "Issue"}
          </div>
          <div className="stat-footnote">
            {health.ok ? "Control plane reachable" : health.error || "Not reachable"}
          </div>
        </div>
      </section>

      <section className="content-grid">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>Quick Summary</h2>
              <p>Top-level control plane overview and current operating posture.</p>
            </div>
          </div>

          <div className="summary-stack">
            <div className="summary-item">
              <div className="summary-title">Training is now a dedicated workflow surface</div>
              <div className="summary-text">
                Capture, review, labeling, and export no longer need to live inside a generic observations bucket.
              </div>
            </div>

            <div className="summary-item">
              <div className="summary-title">Workers own operational execution concerns</div>
              <div className="summary-text">
                Runs, worker health, and raw observer inspection are grouped together as worker operations.
              </div>
            </div>

            <div className="summary-item">
              <div className="summary-title">Domains stay lightweight for now</div>
              <div className="summary-text">
                Marketplace, Jobs, and Finance are scaffolds until their workflow surfaces are implemented.
              </div>
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>System Status</h2>
              <p>Compact summary only. The System workspace holds detailed service readiness and topology.</p>
            </div>
            <button className="ghost-btn small-btn" onClick={openSystemView}>Open System Workspace</button>
          </div>

          <div className="status-stack">
            <div className="status-row">
              <span className="status-key">API Health</span>
              <span className={health.ok ? "inline-badge ok" : "inline-badge bad"}>
                {health.loading ? "Checking..." : health.ok ? "Connected" : "Disconnected"}
              </span>
            </div>

            <div className="status-row">
              <span className="status-key">Base URL</span>
              <span className="status-value mono">{apiLabel}</span>
            </div>

            <div className="status-row">
              <span className="status-key">Notes</span>
              <span className="status-value">
                {health.ok
                  ? "API is reachable. Check System for capture server, Chrome, storage, and training gates."
                  : health.error || "Waiting for API connection."}
              </span>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
