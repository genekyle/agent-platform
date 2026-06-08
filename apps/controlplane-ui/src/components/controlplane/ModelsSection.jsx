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

// "what does this mean" tooltip helper — keeps the explanatory copy near the metric.
function MetricCard({ label, value, oneLine, range, goodIfHigher = true }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value small-stat-value">{value}</div>
      <div className="stat-footnote" style={{ lineHeight: 1.35 }}>
        {oneLine}
        {range ? (
          <div style={{ marginTop: 4, fontStyle: "italic", opacity: 0.8 }}>
            Range: {range}{goodIfHigher ? " (higher = better)" : " (lower = better)"}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// Render the screenshot with the approved (green) and predicted (red) bboxes
// drawn on top, scaled to fit. The image's native pixel dimensions drive the
// box-coordinate scaling (bboxes are stored in screenshot pixel coords).
function BboxOverlay({ prediction, onClose }) {
  const [imgDims, setImgDims] = useState(null);  // {natW, natH, dispW, dispH}
  const containerRef = useCallback((node) => {
    if (!node) return;
  }, []);

  if (!prediction) return null;

  const screenshotUrl = prediction.screenshot_filename
    ? `${API}/api/observations/screenshots/${encodeURIComponent(prediction.screenshot_filename)}`
    : null;

  const handleImgLoad = (e) => {
    const img = e.currentTarget;
    setImgDims({
      natW: img.naturalWidth,
      natH: img.naturalHeight,
      dispW: img.clientWidth,
      dispH: img.clientHeight,
    });
  };

  const scaleBox = (bbox) => {
    if (!bbox || !imgDims) return null;
    const { natW, natH, dispW, dispH } = imgDims;
    const sx = dispW / natW;
    const sy = dispH / natH;
    return {
      left: bbox.x * sx,
      top: bbox.y * sy,
      width: bbox.width * sx,
      height: bbox.height * sy,
    };
  };

  const approved = scaleBox(prediction.approved_bbox);
  const predicted = scaleBox(prediction.predicted_bbox);
  // Two-stage baselines also expose Florence's rough bbox before snapping
  // to an OmniParser candidate. Draw it dashed so you can see the snap effect.
  const florenceRough = scaleBox(prediction.florence_bbox);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.78)",
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--panel-bg, #1a1a1a)",
          borderRadius: 8,
          padding: 20,
          maxWidth: "92vw",
          maxHeight: "92vh",
          overflow: "auto",
          color: "var(--text, #eee)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: "1.05em" }}>Prediction overlay</div>
            <div style={{ fontSize: "0.85em", opacity: 0.7, marginTop: 4 }}>
              <span style={{ color: "#4caf50" }}>■ Green</span> = approved (human label) ·
              <span style={{ color: "#ff5252", marginLeft: 8 }}>■ Red</span> = predicted (final output)
              {prediction.florence_bbox ? (
                <>
                  {" · "}
                  <span style={{ color: "#ffa726" }}>■ Dashed orange</span> = Florence's rough guess (before OmniParser snap)
                </>
              ) : null}
              {" · "}IoU {iou(prediction.bbox_iou)}
              {prediction.snap_strategy ? (
                <span style={{ marginLeft: 8, opacity: 0.7 }}>· strategy: {prediction.snap_strategy}</span>
              ) : null}
            </div>
          </div>
          <button className="ghost-btn" onClick={onClose}>✕ Close</button>
        </div>

        <div style={{ marginBottom: 10, fontSize: "0.9em" }}>
          <div><strong>Original query:</strong> "{prediction.element_query}"</div>
          {prediction.sent_query && prediction.sent_query !== prediction.element_query ? (
            <div style={{ marginTop: 4 }}>
              <strong>Sent to model:</strong> "{prediction.sent_query}" <em style={{ opacity: 0.7 }}>(after normalization)</em>
            </div>
          ) : null}
        </div>

        {screenshotUrl ? (
          <div ref={containerRef} style={{ position: "relative", display: "inline-block", maxWidth: "100%" }}>
            <img
              src={screenshotUrl}
              onLoad={handleImgLoad}
              style={{ display: "block", maxWidth: "min(86vw, 1400px)", maxHeight: "70vh", height: "auto" }}
              alt="capture"
            />
            {approved ? (
              <div style={{
                position: "absolute",
                left: approved.left, top: approved.top,
                width: approved.width, height: approved.height,
                border: "3px solid #4caf50",
                boxShadow: "0 0 0 1px rgba(0,0,0,0.6)",
                pointerEvents: "none",
              }} />
            ) : null}
            {predicted ? (
              <div style={{
                position: "absolute",
                left: predicted.left, top: predicted.top,
                width: predicted.width, height: predicted.height,
                border: "3px solid #ff5252",
                boxShadow: "0 0 0 1px rgba(0,0,0,0.6)",
                pointerEvents: "none",
              }} />
            ) : (
              <div style={{ marginTop: 8, fontStyle: "italic", color: "#ff5252" }}>
                Model returned no bbox for this capture.
              </div>
            )}
            {florenceRough && prediction.florence_bbox &&
             JSON.stringify(prediction.florence_bbox) !== JSON.stringify(prediction.predicted_bbox) ? (
              <div
                title="Florence's raw guess before snapping to an OmniParser candidate"
                style={{
                  position: "absolute",
                  left: florenceRough.left, top: florenceRough.top,
                  width: florenceRough.width, height: florenceRough.height,
                  border: "2px dashed #ffa726",
                  pointerEvents: "none",
                }} />
            ) : null}
          </div>
        ) : (
          <div className="annotation-message error">No screenshot filename in prediction record.</div>
        )}

        <details style={{ marginTop: 12, fontSize: "0.85em" }}>
          <summary style={{ cursor: "pointer", opacity: 0.8 }}>Raw model output</summary>
          <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", background: "rgba(255,255,255,0.04)", padding: 10, borderRadius: 4, marginTop: 6 }}>
            {prediction.raw_response || "(none)"}
          </pre>
        </details>
      </div>
    </div>
  );
}

// Banner that explains "what this whole view IS" — appears at the top of every Models view.
function AboutPanel({ title, children }) {
  return (
    <section
      className="panel"
      style={{
        marginBottom: 20,
        borderLeft: "3px solid var(--accent, #5b8cff)",
        background: "rgba(91, 140, 255, 0.06)",
      }}
    >
      <div style={{ padding: "12px 16px" }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>{title}</div>
        <div style={{ fontSize: "0.92em", lineHeight: 1.5, opacity: 0.9 }}>{children}</div>
      </div>
    </section>
  );
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
  const [deletingRunId, setDeletingRunId] = useState(null);

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
    const ok = window.confirm(
      "Run a fresh eval?\n\n" +
        "This loads Florence-2 (already warm) and scores every eval-split capture.\n" +
        "Typical cost: ~1 second per capture (so ~10s for 9 captures).\n\n" +
        "A new run row will appear in Eval Runs. You can delete it from there if it was accidental.",
    );
    if (!ok) return;
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

  const deleteRun = async (runId) => {
    const ok = window.confirm(`Delete eval run ${runId.slice(0, 8)}? This removes the DB row and on-disk artifacts.`);
    if (!ok) return;
    setDeletingRunId(runId);
    try {
      const res = await fetch(`${API}/api/models/eval-runs/${runId}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (selectedRunId === runId) {
        setSelectedRunId(null);
        setRunDetail(null);
      }
      await Promise.all([loadEvalRuns(), loadModels()]);
    } catch (err) {
      setEvalRunsError(String(err?.message ?? err));
    } finally {
      setDeletingRunId(null);
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
        onDelete={deleteRun}
        deletingRunId={deletingRunId}
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
            The list of model versions registered against each training target. Each row is one
            measurable, swappable implementation.
          </p>
        </div>
        <div className="detail-actions">
          <button className="secondary-btn" onClick={onReload} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <AboutPanel title="What is the Model Registry?">
        Think of this as a leaderboard for "approaches we've tried to solve <em>vision element grounding</em>" —
        i.e. <strong>given a screenshot and a phrase like "click the log in button", draw a box around the right element</strong>.
        Today there are two rows — both use <code>microsoft/Florence-2-base</code> zero-shot (no fine-tuning),
        and the <em>only</em> difference is the input format:
        <ul style={{ margin: "6px 0 6px 18px", padding: 0 }}>
          <li><code>v0_zero_shot_florence2_base</code> — sends the raw human query: <em>"Click on the log in button"</em></li>
          <li><code>v0_zero_shot_florence2_base_short_query</code> — strips imperative scaffolding first: <em>"log in button"</em></li>
        </ul>
        That pair is a tiny experiment with a real lesson: <strong>input format matters</strong>. Florence-2's
        grounding task was trained on short noun phrases, not full sentences, so we'd expect the normalized variant
        to do at least slightly better. The Registry table makes the comparison concrete.
        <div style={{ marginTop: 8 }}>
          <strong>Mean IoU</strong> = average box-overlap with the human-approved box (0 = no overlap, 1 = perfect).
          <strong> IoU@50</strong> = fraction of predictions that got at least 50% overlap — i.e. "did it basically find the right thing."
        </div>
      </AboutPanel>

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
                <th title="0 = no box overlap, 1 = perfect overlap. Higher is better.">Mean IoU</th>
                <th title="Fraction of predictions where IoU ≥ 0.5 ('basically found it'). Higher is better.">IoU@50</th>
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
                      title="Score this model against the eval-split of reviewed captures (~10s)"
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

function EvalRunsView({ runs, models, loading, error, onReload, onOpenRun, onDelete, deletingRunId }) {
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
            Every time you click "Run Eval", one row appears here. Click a row for the per-scenario breakdown
            and a sample of predictions.
          </p>
        </div>
        <div className="detail-actions">
          <button className="secondary-btn" onClick={onReload} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <AboutPanel title="What is an eval run?">
        An <strong>eval run</strong> is one scoring of a model against the same fixed set of captures (the "eval split" —
        roughly 20% of the reviewed captures, held out by a stable hash so the split never changes between runs).
        Re-running the same model on the same data should produce the same metrics — if it doesn't, something is
        non-deterministic.
        <div style={{ marginTop: 8 }}>
          <strong>How to read a row:</strong> Mean IoU and IoU@50 are the headline accuracy numbers (higher is better).
          Mean latency tells you how slow inference is per capture — useful for deciding if a model can be used in the loop or only offline.
          <strong> Center-in-target</strong> is a forgiving sanity check: even if the box is sloppy, would clicking its
          center land inside the real element? For UI automation that's often what matters.
        </div>
        <div style={{ marginTop: 8 }}>
          Accidentally ran it twice? Use the 🗑 button to remove a run — it deletes the DB row and the on-disk artifacts.
        </div>
      </AboutPanel>

      {error ? <div className="annotation-message error">{error}</div> : null}

      {runs.length === 0 ? (
        <div className="empty-state" style={{ marginTop: 24 }}>
          <div className="empty-state-title">No eval runs yet</div>
          <p className="empty-state-copy">
            Trigger one from the Registry tab — the v0 Florence baseline measures
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
                <th title="Number of eval-split captures the model was scored against">Records</th>
                <th title="0 = no overlap, 1 = perfect. Higher is better.">Mean IoU</th>
                <th title="Fraction of predictions with IoU ≥ 0.5. Higher is better.">IoU@50</th>
                <th title="Even with a sloppy box, would a click on its center land inside the real element?">Center-in-target</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const metrics = r.metrics || {};
                return (
                  <tr key={r.id}>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}><code>{r.id.slice(0, 8)}</code></td>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>{modelById[r.model_id]?.implementation || r.model_id}</td>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>{fmt(r.started_at)}</td>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>{r.status}</td>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>{r.record_count}</td>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>{iou(metrics.mean_bbox_iou)}</td>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>{pct(metrics.iou_at_50_accuracy)}</td>
                    <td onClick={() => onOpenRun(r.id)} style={{ cursor: "pointer" }}>{pct(metrics.center_in_target_accuracy)}</td>
                    <td>
                      <button
                        className="ghost-btn"
                        onClick={() => onDelete(r.id)}
                        disabled={deletingRunId === r.id}
                        title="Delete this run"
                      >
                        {deletingRunId === r.id ? "Deleting..." : "🗑"}
                      </button>
                    </td>
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

function ResultInterpretation({ metrics }) {
  if (!metrics || metrics.record_count === 0) return null;
  const iouVal = metrics.mean_bbox_iou ?? 0;
  const hits = metrics.iou_at_50_accuracy ?? 0;

  let verdict;
  if (iouVal >= 0.5) {
    verdict = {
      tone: "good",
      headline: "Strong baseline — predictions are landing on the right element most of the time.",
      detail: "An average IoU above 0.5 means most predicted boxes substantially overlap the human-labeled box. This model could be used in production with light fine-tuning.",
    };
  } else if (iouVal >= 0.25) {
    verdict = {
      tone: "mid",
      headline: "Partial signal — the model is finding the right region but not boxing it tightly.",
      detail: "An IoU in the 0.25–0.5 range usually means the model spots the right area but the box is too big, too small, or shifted. Fine-tuning on labeled data typically fixes this.",
    };
  } else if (iouVal > 0) {
    verdict = {
      tone: "low",
      headline: "Weak signal — predictions occasionally graze the right region.",
      detail: "Very low non-zero IoU means the model is essentially guessing — sometimes a guess overlaps the real element by chance. Fine-tuning is required to make this useful.",
    };
  } else {
    verdict = {
      tone: "floor",
      headline: "Zero-shot floor — the model isn't finding the right element at all. This is the expected baseline.",
      detail: "An IoU of 0 means none of the predicted boxes overlap any of the human-labeled boxes. For a zero-shot generalist vision model on a specialized UI-grounding task, this is the expected starting point. The value of this number is as a floor — once we fine-tune (v1), the new score has to beat 0.0 to count as progress.",
    };
  }

  const color = {
    good: "rgba(80,200,120,0.12)",
    mid: "rgba(255,200,80,0.12)",
    low: "rgba(255,160,80,0.12)",
    floor: "rgba(140,140,140,0.12)",
  }[verdict.tone];

  return (
    <section
      className="panel"
      style={{ marginTop: 16, background: color, borderLeft: "3px solid rgba(255,255,255,0.25)" }}
    >
      <div style={{ padding: "12px 16px" }}>
        <div style={{ fontWeight: 600, marginBottom: 6 }}>Reading this result</div>
        <div style={{ fontSize: "0.92em", lineHeight: 1.5, opacity: 0.95 }}>
          <div>{verdict.headline}</div>
          <div style={{ marginTop: 6, opacity: 0.85 }}>{verdict.detail}</div>
          <div style={{ marginTop: 8, fontStyle: "italic", opacity: 0.8 }}>
            Hits at IoU@50: {pct(hits)} of {metrics.record_count} eval-split captures.
            Per-capture latency: ~{metrics.mean_latency_ms} ms.
          </div>
        </div>
      </div>
    </section>
  );
}

function RunDetailView({ runs, selectedRunId, onSelectRun, detail, error }) {
  const metrics = detail?.metrics || {};
  const perScenario = metrics.per_scenario || {};
  const sample = detail?.predictions_sample || [];
  const [overlayPrediction, setOverlayPrediction] = useState(null);

  return (
    <div className="workspace-card">
      <div className="panel-header">
        <div>
          <h2 className="card-title">Models — Run Detail</h2>
          <p className="card-subtitle">
            One eval run, expanded. Headline metrics, the same metrics split per scenario,
            and a sample of the actual predictions so you can sanity-check what the model said.
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

      <AboutPanel title="What am I looking at?">
        This is the detailed result of one scoring pass. The four headline numbers tell you "how accurate"
        and "how fast." The per-scenario table shows whether the model is bad <em>everywhere</em> or just bad
        on certain types of captures (e.g. it might do okay on "click sign in" but fail on "clear the email field"
        — and that tells you where to add training data).
        <div style={{ marginTop: 8 }}>
          The <strong>Prediction sample</strong> at the bottom shows the model's actual output next to the
          human-approved box, so you can eyeball <em>why</em> it failed — e.g. the box is huge when it should be small,
          or it pointed at a logo instead of a button.
        </div>
      </AboutPanel>

      {error ? <div className="annotation-message error">{error}</div> : null}

      {!detail ? (
        <div className="empty-state" style={{ marginTop: 24 }}>
          <div className="empty-state-title">No run selected</div>
          <p className="empty-state-copy">Pick a run from the dropdown or the Eval Runs tab.</p>
        </div>
      ) : (
        <>
          <ResultInterpretation metrics={metrics} />

          <div className="stats-grid compact-stats-grid" style={{ marginTop: 16 }}>
            <MetricCard
              label="Records scored"
              value={metrics.record_count ?? 0}
              oneLine="Eval-split captures the model was tested on. The split is held stable across runs so scores are comparable."
            />
            <MetricCard
              label="Mean IoU"
              value={iou(metrics.mean_bbox_iou)}
              oneLine="Average box-overlap with the human-approved box. The headline accuracy number."
              range="0.0 → 1.0"
            />
            <MetricCard
              label="IoU @ 0.5"
              value={pct(metrics.iou_at_50_accuracy)}
              oneLine="Fraction of predictions with ≥50% overlap — a binary 'did it find the thing' rate."
              range="0% → 100%"
            />
            <MetricCard
              label="Center-in-target"
              value={pct(metrics.center_in_target_accuracy)}
              oneLine="Would a click on the predicted box's center land inside the real element? Forgiving sanity check for UI automation."
              range="0% → 100%"
            />
            <MetricCard
              label="Mean latency"
              value={`${metrics.mean_latency_ms ?? 0} ms`}
              oneLine="Wall-clock time per prediction. Useful for deciding if the model is fast enough for the live agent loop or only offline analysis."
              range="0 ms → ∞"
              goodIfHigher={false}
            />
          </div>

          <section className="panel" style={{ marginTop: 24 }}>
            <div className="panel-header">
              <div>
                <h3>Per-scenario breakdown</h3>
                <p>Same four metrics, sliced by scenario. Reveals whether failure is uniform or concentrated.</p>
              </div>
            </div>
            <div className="table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>Scenario</th>
                    <th>Records</th>
                    <th title="Higher is better">Mean IoU</th>
                    <th title="Higher is better">IoU@50</th>
                    <th title="Higher is better">Center-in-target</th>
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
                <p>
                  Up to 25 per-capture predictions. Compare the predicted box to the approved one — when they're
                  far apart, look at the <em>element query</em> column to understand what the model was being asked to find.
                  (Bbox overlays on the screenshot will land in v1; today this is JSON.)
                </p>
              </div>
            </div>
            <div className="table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>Capture</th>
                    <th>Element query (what we asked for)</th>
                    <th title="The string the model actually saw, after any preprocessing">Sent to model</th>
                    <th title="Higher is better">IoU</th>
                    <th>Latency</th>
                    <th></th>
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
                        <td>
                          {p.sent_query && p.sent_query !== p.element_query ? (
                            <span style={{ fontStyle: "italic", opacity: 0.85 }}>{p.sent_query}</span>
                          ) : (
                            <span style={{ opacity: 0.5 }}>(same as query)</span>
                          )}
                        </td>
                        <td>{iou(p.bbox_iou)}</td>
                        <td>{p.latency_ms} ms</td>
                        <td>
                          <button
                            className="secondary-btn"
                            onClick={() => setOverlayPrediction(p)}
                            title="See the predicted and approved boxes drawn on the screenshot"
                          >
                            View overlay
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      <BboxOverlay
        prediction={overlayPrediction}
        onClose={() => setOverlayPrediction(null)}
      />
    </div>
  );
}
