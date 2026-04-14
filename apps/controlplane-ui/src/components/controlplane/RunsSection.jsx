import { fmt, getStatusClass } from "./utils";

export function RunsSection({
  filteredRuns,
  selectedRun,
  runSearch,
  setRunSearch,
  activeRuns,
  blockedRuns,
  completedRuns,
  createRun,
  setSelectedRunId,
  runs,
}) {
  return (
    <>
      <section className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">Total Visible Runs</div>
          <div className="stat-value">{filteredRuns.length}</div>
          <div className="stat-footnote">Based on current filter</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">Running</div>
          <div className="stat-value">{activeRuns}</div>
          <div className="stat-footnote">Actively executing</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">Blocked</div>
          <div className="stat-value">{blockedRuns}</div>
          <div className="stat-footnote">Awaiting action or intervention</div>
        </div>

        <div className="stat-card">
          <div className="stat-label">Completed</div>
          <div className="stat-value">{completedRuns}</div>
          <div className="stat-footnote">Successfully finished</div>
        </div>
      </section>

      <section className="runs-layout">
        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>Run Queue</h2>
              <p>Review and select a run to inspect details.</p>
            </div>
            <button className="primary-btn small-btn" onClick={createRun}>
              + New Run
            </button>
          </div>

          <div className="runs-toolbar">
            <input
              className="runs-search"
              type="text"
              placeholder="Search run id or status..."
              value={runSearch}
              onChange={(event) => setRunSearch(event.target.value)}
            />
            <button className="ghost-btn small-btn">All</button>
            <button className="ghost-btn small-btn">Active</button>
            <button className="ghost-btn small-btn">Blocked</button>
          </div>

          {runs.loading ? (
            <div className="empty-state">Loading runs...</div>
          ) : runs.error ? (
            <div className="empty-state error">Error: {runs.error}</div>
          ) : filteredRuns.length === 0 ? (
            <div className="empty-state">No runs match this filter.</div>
          ) : (
            <div className="table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>Run ID</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Ended</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRuns.map((run) => (
                    <tr
                      key={run.id}
                      className={selectedRun?.id === run.id ? "selected-row" : ""}
                      onClick={() => setSelectedRunId(run.id)}
                    >
                      <td className="mono">{run.id}</td>
                      <td>
                        <span className={getStatusClass(run.status)}>{run.status}</span>
                      </td>
                      <td>{fmt(run.startedAt)}</td>
                      <td>{fmt(run.endedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="panel">
          <div className="panel-header">
            <div>
              <h2>Selected Run</h2>
              <p>Execution detail and next operational controls.</p>
            </div>
          </div>

          {!selectedRun ? (
            <div className="empty-state">Select a run to inspect details.</div>
          ) : (
            <>
              <div className="detail-card">
                <div className="detail-row">
                  <span className="detail-key">Run ID</span>
                  <span className="detail-value mono">{selectedRun.id}</span>
                </div>

                <div className="detail-row">
                  <span className="detail-key">Status</span>
                  <span className={getStatusClass(selectedRun.status)}>{selectedRun.status}</span>
                </div>

                <div className="detail-row">
                  <span className="detail-key">Started</span>
                  <span className="detail-value">{fmt(selectedRun.startedAt)}</span>
                </div>

                <div className="detail-row">
                  <span className="detail-key">Ended</span>
                  <span className="detail-value">{fmt(selectedRun.endedAt)}</span>
                </div>
              </div>

              <div className="detail-actions">
                <button className="primary-btn">Pause Run</button>
                <button className="secondary-btn">Resume</button>
                <button className="danger-btn">Abort</button>
              </div>

              <div className="timeline-block">
                <div className="timeline-title">Step Timeline</div>
                <div className="timeline-list">
                  {(selectedRun.steps ?? []).length === 0 ? (
                    <div className="empty-state">No steps recorded.</div>
                  ) : (
                    (selectedRun.steps ?? []).map((step) => (
                      <div key={step.id} className="timeline-item">
                        <div className="timeline-head">
                          <div className="timeline-item-title">{step.type}</div>
                          <span className={getStatusClass(step.status)}>{step.status}</span>
                        </div>
                        <div className="timeline-detail">{step.payload ?? "—"}</div>
                        <div className="timeline-meta">
                          <span className="mono">step-{step.id} (#{step.order_index})</span>
                          <span>{step.started_at ? fmt(step.started_at) : "pending"}</span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </section>
    </>
  );
}
