export function ModelsSection({ section }) {
  if (section === "registry") {
    return <ModelsRegistryStub />;
  }
  if (section === "eval-runs") {
    return <ModelEvalRunsStub />;
  }
  if (section === "run-detail") {
    return <ModelRunDetailStub />;
  }
  return <ModelsRegistryStub />;
}

function ModelsRegistryStub() {
  return (
    <div className="workspace-card">
      <h2 className="card-title">Models — Registry</h2>
      <p className="card-subtitle">
        Models registered against each training target. The v0 baseline is{" "}
        <code>vision_element_grounding__v0_zero_shot_florence2_base</code>.
      </p>

      <div className="empty-state" style={{ marginTop: 24 }}>
        <div className="empty-state-title">No models registered yet</div>
        <p className="empty-state-copy">
          The v0 implementation (Florence-2-base zero-shot) is spec'd in{" "}
          <code>docs/v0-florence.md</code>. Once the backend ships, this view will list
          registered models and a "Register v0 Florence Baseline" button will hit{" "}
          <code>POST /api/models/seed</code>.
        </p>
        <button className="ghost-btn" disabled>
          Register v0 Florence Baseline (backend not yet implemented)
        </button>
      </div>
    </div>
  );
}

function ModelEvalRunsStub() {
  return (
    <div className="workspace-card">
      <h2 className="card-title">Models — Eval Runs</h2>
      <p className="card-subtitle">
        Recent eval runs across all registered models, ordered by recency.
      </p>
      <div className="empty-state" style={{ marginTop: 24 }}>
        <div className="empty-state-title">No eval runs yet</div>
        <p className="empty-state-copy">
          Eval runs will appear here once the v0 backend lands and the first model is
          run against reviewed captures with an approved bbox.
        </p>
      </div>
    </div>
  );
}

function ModelRunDetailStub() {
  return (
    <div className="workspace-card">
      <h2 className="card-title">Models — Run Detail</h2>
      <p className="card-subtitle">
        Per-scenario metrics and a sample of predictions for one selected eval run.
      </p>
      <div className="empty-state" style={{ marginTop: 24 }}>
        <div className="empty-state-title">No run selected</div>
        <p className="empty-state-copy">
          Pick an eval run from the Eval Runs view to see overall metrics, per-scenario
          breakdown, and a sample of predictions (predicted bbox vs. approved bbox).
        </p>
      </div>
    </div>
  );
}
