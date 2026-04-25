import { fmt } from "./utils";

export function TrainingExportSection({
  selectedObs,
  selectedObsFilename,
  labels,
  buildTrainingDataset,
  trainGroundingModel,
  loadTrainingTargetComparison,
  datasetStatus,
  trainingStatus,
  targetComparisonStatus,
  onOpenDataset,
}) {
  const candidates = selectedObs?.ranked_candidates ?? [];
  const labeledCount = candidates.filter((candidate) => labels[candidate.candidate_id]).length;
  const approvedCount = candidates.filter((candidate) => labels[candidate.candidate_id] === "approve").length;
  const rejectedCount = candidates.filter((candidate) => labels[candidate.candidate_id] === "reject").length;
  const comparison = targetComparisonStatus?.result;
  const summary = comparison?.capture_summary;
  const perScenario = trainingStatus?.result?.metrics?.per_scenario ?? {};

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Training Target Comparison</h2>
            <p>Compare near-term model targets against the reviewed scenario-specific captures.</p>
          </div>
        </div>

        <div className="detail-actions">
          <button
            className="secondary-btn"
            disabled={targetComparisonStatus?.loading}
            onClick={loadTrainingTargetComparison}
          >
            {targetComparisonStatus?.loading ? "Refreshing..." : "Refresh Comparison"}
          </button>
        </div>

        {targetComparisonStatus?.error ? (
          <div className="annotation-message error">{targetComparisonStatus.error}</div>
        ) : null}

        {comparison ? (
          <>
            <div className="stats-grid compact-stats-grid">
              <div className="stat-card">
                <div className="stat-label">Reviewed</div>
                <div className="stat-value small-stat-value">{summary?.reviewed_count ?? 0}</div>
                <div className="stat-footnote">Records eligible for datasets</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Scenarios</div>
                <div className="stat-value small-stat-value">{summary?.scenario_count ?? 0}</div>
                <div className="stat-footnote">Scenario coverage</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Candidates</div>
                <div className="stat-value small-stat-value">{summary?.total_candidates ?? 0}</div>
                <div className="stat-footnote">Candidate labels to learn from</div>
              </div>
              <div className="stat-card">
                <div className="stat-label">Recommendation</div>
                <div className="stat-value small-stat-value">Grounding</div>
                <div className="stat-footnote">Current leading target</div>
              </div>
            </div>

            <div className="table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>Target</th>
                    <th>Ready</th>
                    <th>Score</th>
                    <th>Usefulness</th>
                    <th>Eval</th>
                    <th>Blocker</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.targets.map((target) => (
                    <tr key={target.target_id}>
                      <td>
                        <div className="obs-page-title">{target.label}</div>
                        <div className="obs-page-url">{target.description}</div>
                      </td>
                      <td>{target.readiness.ready ? "Yes" : "No"}</td>
                      <td>{target.weighted_score}</td>
                      <td>{target.scores.app_usefulness}/5</td>
                      <td>{target.scores.evaluation_clarity}/5</td>
                      <td>{target.readiness.blocker ?? "Ready for baseline"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="empty-state">No comparison loaded yet.</div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Label Engine Staging</h2>
            <p>Keep the labeling loop focused on browser grounding data and scenario evaluation.</p>
          </div>
        </div>

        {selectedObs ? (
          <>
        <div className="stats-grid compact-stats-grid">
          <div className="stat-card">
            <div className="stat-label">Candidates</div>
            <div className="stat-value small-stat-value">{candidates.length}</div>
            <div className="stat-footnote">Ranked candidates in the artifact</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Labeled</div>
            <div className="stat-value small-stat-value">{labeledCount}</div>
            <div className="stat-footnote">Candidates with a review label</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Approved</div>
            <div className="stat-value small-stat-value">{approvedCount}</div>
            <div className="stat-footnote">Positive selections</div>
          </div>

          <div className="stat-card">
            <div className="stat-label">Rejected</div>
            <div className="stat-value small-stat-value">{rejectedCount}</div>
            <div className="stat-footnote">Negative selections</div>
          </div>
        </div>

        <div className="detail-card export-card">
          <div className="detail-row">
            <span className="detail-key">Artifact</span>
            <span className="detail-value mono">{selectedObsFilename}</span>
          </div>
          <div className="detail-row">
            <span className="detail-key">Page</span>
            <span className="detail-value">{selectedObs?.acquisition?.page_identity?.title || "Untitled"}</span>
          </div>
          <div className="detail-row">
            <span className="detail-key">Captured</span>
            <span className="detail-value">{fmt(selectedObs?.metadata?.timestamp)}</span>
          </div>
        </div>
          </>
        ) : (
          <>
            <div className="empty-state">
              No artifact selected. Choose a record in Dataset Browser, then return here to inspect its label readiness.
            </div>
            <button className="primary-btn" onClick={onOpenDataset}>Open Dataset Browser</button>
          </>
        )}

        <div className="detail-actions">
          <button
            className="primary-btn"
            disabled={datasetStatus?.loading}
            onClick={buildTrainingDataset}
          >
            {datasetStatus?.loading ? "Building Dataset..." : "Build Grounding Dataset"}
          </button>
          <button
            className="secondary-btn"
            disabled={trainingStatus?.loading}
            onClick={trainGroundingModel}
          >
            {trainingStatus?.loading ? "Training..." : "Train Latest Model"}
          </button>
          <button className="secondary-btn" onClick={onOpenDataset}>Choose Different Artifact</button>
        </div>
        {datasetStatus?.result ? (
          <div className="summary-item training-status-card">
            <div className="summary-title">Latest dataset build</div>
            <div className="summary-text">
              {datasetStatus.result.record_count} reviewed record(s) in {datasetStatus.result.dataset_id}.
            </div>
          </div>
        ) : null}
        {datasetStatus?.error ? (
          <div className="annotation-message error">{datasetStatus.error}</div>
        ) : null}
        {trainingStatus?.result ? (
          <div className="summary-item training-status-card">
            <div className="summary-title">Latest training run</div>
            <div className="summary-text">
              accuracy {trainingStatus.result.metrics?.target_accuracy ?? "-"}, mean IoU {trainingStatus.result.metrics?.mean_bbox_iou ?? "-"}, mean rank {trainingStatus.result.metrics?.mean_candidate_rank ?? "-"}.
            </div>
          </div>
        ) : null}
        {trainingStatus?.error ? (
          <div className="annotation-message error">{trainingStatus.error}</div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Scenario Benchmark</h2>
            <p>Evaluate experiments by scenario so broad averages do not hide weak workflows.</p>
          </div>
        </div>

        {Object.keys(perScenario).length > 0 ? (
          <div className="table-wrap">
            <table className="runs-table">
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Records</th>
                  <th>Grounding Accuracy</th>
                  <th>Mean IoU</th>
                  <th>Mean Rank</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(perScenario).map(([scenarioId, metrics]) => (
                  <tr key={scenarioId}>
                    <td className="mono">{scenarioId}</td>
                    <td>{metrics.record_count}</td>
                    <td>{metrics.grounding_accuracy}</td>
                    <td>{metrics.mean_bbox_iou}</td>
                    <td>{metrics.mean_candidate_rank}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="summary-stack">
            <div className="summary-item">
              <div className="summary-title">Evaluation gate</div>
              <div className="summary-text">
                Save reviewed labels, build the grounding dataset, then train the baseline to populate scenario metrics.
              </div>
            </div>
            <div className="summary-item">
              <div className="summary-title">Labeling engine direction</div>
              <div className="summary-text">
                Screenshot overlays, candidate approval, bbox correction, scenario filters, and reviewed-only exports are the foundation for the Label Studio-like engine.
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
