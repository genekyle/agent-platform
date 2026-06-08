import { useCallback, useEffect, useMemo, useState } from "react";
import { fmt } from "./utils";

const API = import.meta.env.VITE_API_BASE_URL;

function pct(value) {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function iou(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(3);
}

export function ModelsSection({ section }) {
  const [models, setModels] = useState([]);
  const [modelsError, setModelsError] = useState(null);
  const [modelsLoading, setModelsLoading] = useState(false);

  const [evalRuns, setEvalRuns] = useState([]);
  const [evalRunsError, setEvalRunsError] = useState(null);
  const [evalRunsLoading, setEvalRunsLoading] = useState(false);

  const [selectedRunId, setSelectedRunId] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [runDetailError, setRunDetailError] = useState(null);

  const [seedingId, setSeedingId] = useState(null);
  const [runningEvalFor, setRunningEvalFor] = useState(null);

  const loadModels = useCallback(async () => {
    setModelsLoading(true);
    setModelsError(null);
    try {
      const res = await fetch(`${API}/api/models`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setModels(await res.json());
    } catch (err) {
      setModelsError(String(err?.message ?? err));
    } finally {
      setModelsLoading(false);
    }
  }, []);

  const loadEvalRuns = useCallback(async () => {
    setEvalRunsLoading(true);
    setEvalRunsError(null);
    try {
      const res = await fetch(`${API}/api/models/eval-runs`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setEvalRuns(await res.json());
    } catch (err) {
      setEvalRunsError(String(err?.message ?? err));
    } finally {
      setEvalRunsLoading(false);
    }
  }, []);

  const loadRunDetail = useCallback(async (runId) => {
    setRunDetailError(null);
    setRunDetail(null);
    try {
      const res = await fetch(`${API}/api/models/eval-runs/${runId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setRunDetail(await res.json());
    } catch (err) {
      setRunDetailError(String(err?.message ?? err));
    }
  }, []);

  useEffect(() => {
    loadModels();
    loadEvalRuns();
  }, [loadModels, loadEvalRuns]);

  useEffect(() => {
    if (section === "run-detail" && selectedRunId) {
      loadRunDetail(selectedRunId);
    }
  }, [section, selectedRunId, loadRunDetail]);

  // Auto-select the most recent run when entering the detail view without one.
  useEffect(() => {
    if (section === "run-detail" && !selectedRunId && evalRuns.length > 0) {
      setSelectedRunId(evalRuns[0].id);
    }
  }, [section, selectedRunId, evalRuns]);

  const seedBaseline = async () => {
    setSeedingId("v0-florence");
    try {
      const res = await fetch(`${API}/api/models/seed`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadModels();
    } catch (err) {
      setModelsError(String(err?.message ?? err));
    } finally {
      setSeedingId(null);
    }
  };

  const runEval = async (modelId) => {
    setRunningEvalFor(modelId);
    try {
      const res = await fetch(`${API}/api/models/${modelId}/eval`, { method: "POST" });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`HTTP ${res.status}: ${body}`);
      }
      await Promise.all([loadModels(), loadEvalRuns()]);
    } catch (err) {
      setEvalRunsError(String(err?.message ?? err));
    } finally {
      setRunningEvalFor(null);
    }
  };

  const handleOpenRun = (runId) => {
    setSelectedRunId(runId);
    loadRunDetail(runId);
  };

  if (section === "registry") {
    return (
      <ModelsRegistryView
        models={models}
        loading={modelsLoading}
        error={modelsError}
        onReload={loadModels}
        onSeed={seedBaseline}
        seedingId={seedingId}
        onRunEval={runEval}
        runningEvalFor={runningEvalFor}
      />
    );
  }
  if (section === "eval-runs") {
    return (
      <EvalRunsView
        runs={evalRuns}
        models={models}
        loading={evalRunsLoading}
        error={evalRunsError}
        onReload={loadEvalRuns}
        onOpenRun={handleOpenRun}
      />
    );
  }
  if (section === "run-detail") {
    return (
      <RunDetailView
        runs={evalRuns}
        selectedRunId={selectedRunId}
        onSelectRun={handleOpenRun}
        detail={runDetail}
        error={runDetailError}
      />
    );
  }
  return null;
}

function ModelsRegistryView({ models, loading, error, onReload, onSeed, seedingId, onRunEval, runningEvalFor }) {
  return (
    <div className="workspace-card">
      <div className="panel-header">
        <div>
          <h2 className="card-title">Models — Registry</h2>
          <p className="card-subtitle">
            Models registered against each training target with their last-eval summary.
            The model id is <code>{`{target_id}__{implementation}`}</code> — the stable
            swap point for new model versions.
          </p>
        </div>
        <div className="detail-actions">
          <button className="secondary-btn" onClick={onReload} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <div className="annotation-message error">{error}</div> : null}

      {models.length === 0 ? (
        <div className="empty-state" style={{ marginTop: 24 }}>
          <div className="empty-state-title">No models registered yet</div>
          <p className="empty-state-copy">
            Register the v0 Florence-2-base zero-shot baseline to get a first measurable
            number on <code>vision_element_grounding</code>.
          </p>
          <button className="primary-btn" onClick={onSeed} disabled={!!seedingId}>
            {seedingId ? "Registering..." : "Register v0 Florence Baseline"}
          </button>
        </div>
      ) : (
        <div className="table-wrap" style={{ marginTop: 16 }}>
          <table className="runs-table">
            <thead>
              <tr>
                <th>Model ID</th>
                <th>Target</th>
                <th>Implementation</th>
                <th>Backing model</th>
                <th>Last eval</th>
                <th>Mean IoU</th>
                <th>IoU@50</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.id}>
                  <td><code>{m.id}</code></td>
                  <td>{m.target_id}</td>
                  <td>{m.implementation}</td>
                  <td>{m.model_name ?? "—"}</td>
                  <td>{m.last_eval ? fmt(m.last_eval.started_at) : "never"}</td>
                  <td>{m.last_eval ? iou(m.last_eval.mean_bbox_iou) : "—"}</td>
                  <td>{m.last_eval ? pct(m.last_eval.iou_at_50_accuracy) : "—"}</td>
                  <td>
                    <button
                      className="secondary-btn"
                      onClick={() => onRunEval(m.id)}
                      disabled={runningEvalFor === m.id}
                    >
                      {runningEvalFor === m.id ? "Running eval..." : "Run Eval"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function EvalRunsView({ runs, models, loading, error, onReload, onOpenRun }) {
  const modelById = useMemo(() => {
    const m = {};
    for (const row of models) m[row.id] = row;
    return m;
  }, [models]);

  return (
    <div className="workspace-card">
      <div className="panel-header">
        <div>
          <h2 className="card-title">Models — Eval Runs</h2>
          <p className="card-subtitle">
            Recent eval runs across all registered models, most recent first.
            Each run scores the model against the eval-split of reviewed captures
            (captures with <code>element_query</code> + <code>approved_bbox</code>).
          </p>
        </div>
        <div className="detail-actions">
          <button className="secondary-btn" onClick={onReload} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error ? <div className="annotation-message error">{error}</div> : null}

      {runs.length === 0 ? (
        <div className="empty-state" style={{ marginTop: 24 }}>
          <div className="empty-state-title">No eval runs yet</div>
          <p className="empty-state-copy">
            Trigger a run from the Registry tab — the v0 Florence baseline measures
            zero-shot grounding accuracy against your reviewed captures.
          </p>
        </div>
      ) : (
        <div className="table-wrap" style={{ marginTop: 16 }}>
          <table className="runs-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Model</th>
                <th>Started</th>
                <th>Status</th>
                <th>Records</th>
                <th>Mean IoU</th>
                <th>IoU@50</th>
                <th>Center-in-target</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const metrics = r.metrics || {};
                return (
                  <tr key={r.id} className="runs-table-row" onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>
                    <td><code>{r.id.slice(0, 8)}</code></td>
                    <td>{modelById[r.model_id]?.implementation || r.model_id}</td>
                    <td>{fmt(r.started_at)}</td>
                    <td>{r.status}</td>
                    <td>{r.record_count}</td>
                    <td>{iou(metrics.mean_bbox_iou)}</td>
                    <td>{pct(metrics.iou_at_50_accuracy)}</td>
                    <td>{pct(metrics.center_in_target_accuracy)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RunDetailView({ runs, selectedRunId, onSelectRun, detail, error }) {
  const metrics = detail?.metrics || {};
  const perScenario = metrics.per_scenario || {};
  const sample = detail?.predictions_sample || [];

  return (
    <div className="workspace-card">
      <div className="panel-header">
        <div>
          <h2 className="card-title">Models — Run Detail</h2>
          <p className="card-subtitle">
            Per-scenario metrics and a sample of predictions for one selected eval run.
          </p>
        </div>
        <div className="detail-actions">
          <select
            value={selectedRunId || ""}
            onChange={(e) => onSelectRun(e.target.value)}
            disabled={runs.length === 0}
          >
            {runs.length === 0 ? <option value="">No runs available</option> : null}
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.id.slice(0, 8)} — {fmt(r.started_at)} — {r.status}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error ? <div className="annotation-message error">{error}</div> : null}

      {!detail ? (
        <div className="empty-state" style={{ marginTop: 24 }}>
          <div className="empty-state-title">No run selected</div>
          <p className="empty-state-copy">Pick a run from the dropdown or the Eval Runs tab.</p>
        </div>
      ) : (
        <>
          <div className="stats-grid compact-stats-grid" style={{ marginTop: 16 }}>
            <div className="stat-card">
              <div className="stat-label">Records</div>
              <div className="stat-value small-stat-value">{metrics.record_count ?? 0}</div>
              <div className="stat-footnote">Eval-split captures scored</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Mean IoU</div>
              <div className="stat-value small-stat-value">{iou(metrics.mean_bbox_iou)}</div>
              <div className="stat-footnote">Average bbox overlap</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">IoU @ 0.5</div>
              <div className="stat-value small-stat-value">{pct(metrics.iou_at_50_accuracy)}</div>
              <div className="stat-footnote">Fraction with IoU ≥ 0.5</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Center-in-target</div>
              <div className="stat-value small-stat-value">{pct(metrics.center_in_target_accuracy)}</div>
              <div className="stat-footnote">Click would land in approved bbox</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Mean latency</div>
              <div className="stat-value small-stat-value">{metrics.mean_latency_ms ?? 0} ms</div>
              <div className="stat-footnote">Per-prediction wall time</div>
            </div>
          </div>

          <section className="panel" style={{ marginTop: 24 }}>
            <div className="panel-header">
              <div>
                <h3>Per-scenario breakdown</h3>
                <p>Same metrics, split by scenario.</p>
              </div>
            </div>
            <div className="table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>Scenario</th>
                    <th>Records</th>
                    <th>Mean IoU</th>
                    <th>IoU@50</th>
                    <th>Center-in-target</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(perScenario).length === 0 ? (
                    <tr><td colSpan={5}>No per-scenario data.</td></tr>
                  ) : (
                    Object.entries(perScenario).map(([sid, s]) => (
                      <tr key={sid}>
                        <td>{sid}</td>
                        <td>{s.record_count}</td>
                        <td>{iou(s.mean_bbox_iou)}</td>
                        <td>{pct(s.iou_at_50_accuracy)}</td>
                        <td>{pct(s.center_in_target_accuracy)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel" style={{ marginTop: 24 }}>
            <div className="panel-header">
              <div>
                <h3>Prediction sample</h3>
                <p>Predicted bbox vs. approved bbox. Overlay rendering deferred to v1.</p>
              </div>
            </div>
            <div className="table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>Capture</th>
                    <th>Element query</th>
                    <th>Predicted bbox</th>
                    <th>Approved bbox</th>
                    <th>IoU</th>
                    <th>Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {sample.length === 0 ? (
                    <tr><td colSpan={6}>No predictions available.</td></tr>
                  ) : (
                    sample.map((p) => (
                      <tr key={p.artifact_filename}>
                        <td title={p.artifact_filename}><code>{(p.artifact_filename || "").slice(0, 22)}…</code></td>
                        <td>{p.element_query}</td>
                        <td><code>{p.predicted_bbox ? JSON.stringify(p.predicted_bbox) : "—"}</code></td>
                        <td><code>{p.approved_bbox ? JSON.stringify(p.approved_bbox) : "—"}</code></td>
                        <td>{iou(p.bbox_iou)}</td>
                        <td>{p.latency_ms} ms</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
