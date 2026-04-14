import { fmt } from "./utils";

export function TrainingExportSection({
  selectedObs,
  selectedObsFilename,
  labels,
  buildTrainingDataset,
  trainGroundingModel,
  datasetStatus,
  trainingStatus,
  onOpenDataset,
}) {
  if (!selectedObs) {
    return (
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Export / Model Prep</h2>
            <p>Select an artifact in Dataset Browser before exporting labels or preparing model data.</p>
          </div>
        </div>

        <div className="empty-state">
          No artifact selected. Choose a record in Dataset Browser, then return here to export reviewed labels.
        </div>
        <button className="primary-btn" onClick={onOpenDataset}>Open Dataset Browser</button>
      </section>
    );
  }

  const candidates = selectedObs.ranked_candidates ?? [];
  const labeledCount = candidates.filter((candidate) => labels[candidate.candidate_id]).length;
  const approvedCount = candidates.filter((candidate) => labels[candidate.candidate_id] === "approve").length;
  const rejectedCount = candidates.filter((candidate) => labels[candidate.candidate_id] === "reject").length;

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Export Labels</h2>
            <p>Prepare reviewed labels from the currently selected training artifact.</p>
          </div>
        </div>

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
              accuracy {trainingStatus.result.metrics?.target_accuracy ?? "-"}, mean IoU {trainingStatus.result.metrics?.mean_bbox_iou ?? "-"}.
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
            <h2>Model Prep Staging</h2>
            <p>This area is reserved for dataset assembly, split generation, and model prep steps.</p>
          </div>
        </div>

        <div className="summary-stack">
          <div className="summary-item">
            <div className="summary-title">Current state</div>
            <div className="summary-text">
              IA is now aligned: training artifacts are selected in Dataset Browser, reviewed in Review / Label, and exported here.
            </div>
          </div>
          <div className="summary-item">
            <div className="summary-title">Next implementation step</div>
            <div className="summary-text">
              Add persisted review annotations and batch dataset export once the workflow is stable.
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
