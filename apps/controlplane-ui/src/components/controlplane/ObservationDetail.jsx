import { useEffect, useMemo, useState } from "react";
import { fmt, resolveBbox, screenshotFilename } from "./utils";

const API = import.meta.env.VITE_API_BASE_URL;

export function ObservationDetail({
  mode,
  selectedObs,
  selectedObsFilename,
  labels,
  setLabels,
  bboxOverride,
  setBboxOverride,
  onSaveAnnotation,
  annotationSaving,
  annotationMessage,
  onBack,
}) {
  const [activeTab, setActiveTab] = useState(mode === "training" ? "screenshot" : "overview");
  const [elementSearch, setElementSearch] = useState("");
  const [expandedStages, setExpandedStages] = useState({});
  const [imgSize, setImgSize] = useState({ natW: 0, natH: 0 });
  const [selectedCandidateId, setSelectedCandidateId] = useState(null);

  useEffect(() => {
    setActiveTab(mode === "training" ? "screenshot" : "overview");
    setElementSearch("");
    setExpandedStages({});
    setImgSize({ natW: 0, natH: 0 });
    setSelectedCandidateId(null);
  }, [mode, selectedObsFilename]);

  if (selectedObs?._error) {
    return <section className="panel"><div className="empty-state error">Error: {selectedObs._error}</div></section>;
  }

  const acquisition = selectedObs?.acquisition ?? {};
  const candidates = selectedObs?.ranked_candidates ?? [];
  const stages = selectedObs?.pipeline?.stages ?? {};
  const stageOrder = selectedObs?.pipeline?.stage_order ?? [];
  const sceneInterpretation = selectedObs?.scene_interpretation ?? {};
  const captureStatus = acquisition.capture_status ?? {};
  const pageIdentity = acquisition.page_identity ?? {};
  const frameState = acquisition.frame_state ?? {};
  const fileName = screenshotFilename(selectedObs);
  const channels = ["js_state", "accessibility_snapshot", "console", "network", "screenshot"];
  const labeledCount = candidates.filter((candidate) => labels[candidate.candidate_id]).length;
  const approvedCandidateId = candidates.find((candidate) => labels[candidate.candidate_id] === "approve")?.candidate_id ?? null;

  const tabs = useMemo(() => {
    const baseTabs = [
      { id: "overview", label: "Overview" },
      { id: "screenshot", label: "Screenshot" },
      { id: "elements", label: `Elements (${(acquisition.actionable_elements ?? []).length})` },
      { id: "pipeline", label: "Pipeline" },
    ];
    if (mode === "training") {
      baseTabs.push({ id: "candidates", label: `Candidates${labeledCount ? ` (${labeledCount})` : ""}` });
    }
    return baseTabs;
  }, [acquisition.actionable_elements, labeledCount, mode]);

  const filteredElements = useMemo(() => {
    const elements = acquisition.actionable_elements ?? [];
    const query = elementSearch.trim().toLowerCase();
    if (!query) return elements;
    return elements.filter((element) =>
      [element.uid, element.tag, element.type, element.role, element.label, element.text]
        .some((value) => value && String(value).toLowerCase().includes(query)),
    );
  }, [acquisition.actionable_elements, elementSearch]);

  return (
    <section className="panel obs-detail-view">
      <div className="obs-detail-topbar">
        {onBack ? <button className="ghost-btn" onClick={onBack}>Back to list</button> : null}
        <span className="obs-detail-filename">{selectedObsFilename}</span>
      </div>

      <div className="dd-panel">
        <div className="dd-tabbar">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`dd-tab${activeTab === tab.id ? " active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "overview" && (
          <div className="dd-overview">
            <div className="dd-card">
              <div className="dd-card-title">Scene Interpretation</div>
              {[
                ["Page type", sceneInterpretation.page_type],
                ["Primary goal", sceneInterpretation.primary_goal],
                ["Headline", sceneInterpretation.headline],
                ["Summary", sceneInterpretation.summary_text],
                ["Visual context", sceneInterpretation.visual_context],
              ].map(([key, value]) => (
                <div className="dd-row" key={key}>
                  <span className="detail-key">{key}</span>
                  <span>{value ?? "-"}</span>
                </div>
              ))}
            </div>

            <div className="dd-card">
              <div className="dd-card-title">Capture Channels</div>
              <div className="dd-channels">
                {channels.map((channel) => {
                  const status = captureStatus[channel]?.status ?? "unavailable";
                  const className = status === "success" ? "badge-success" : status === "failed" ? "badge-failed" : "badge-unavailable";
                  return (
                    <span key={channel} className={`channel-badge ${className}`}>
                      {channel.replace(/_/g, " ")}: {status}
                    </span>
                  );
                })}
              </div>
            </div>

            <div className="dd-card">
              <div className="dd-card-title">Page Metadata</div>
              {[
                ["URL", pageIdentity.url, "dd-url mono"],
                ["Title", pageIdentity.title],
                ["Frame count", frameState.frame_count ?? 0],
                ["Dialog present", frameState.dialog_present ? "yes" : "no"],
                ["Timestamp", fmt(selectedObs?.metadata?.timestamp)],
              ].map(([key, value, className]) => (
                <div className="dd-row" key={key}>
                  <span className="detail-key">{key}</span>
                  <span className={className ?? ""}>{value ?? "-"}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "screenshot" && (
          <div className="dd-screenshot-wrap">
            {fileName ? (
              <div className="dd-viewport">
                <img
                  className="obs-screenshot"
                  src={`${API}/api/observations/screenshots/${fileName}`}
                  alt="Page screenshot"
                  onLoad={(event) => {
                    const element = event.currentTarget;
                    setImgSize({ natW: element.naturalWidth, natH: element.naturalHeight });
                  }}
                />
                {imgSize.natW > 0 && (
                  <svg className="obs-overlay" viewBox={`0 0 ${imgSize.natW} ${imgSize.natH}`} preserveAspectRatio="none">
                    {candidates.map((candidate) => {
                      const bbox = resolveBbox(candidate, acquisition);
                      if (!bbox) return null;
                      const label = labels[candidate.candidate_id];
                      const isSelected = selectedCandidateId === candidate.candidate_id;
                      const stroke = label === "approve"
                        ? "#16a34a"
                        : label === "reject"
                          ? "#dc2626"
                          : isSelected
                            ? "#f59e0b"
                            : "#2f6feb";

                      return (
                        <rect
                          key={candidate.candidate_id}
                          x={bbox.x}
                          y={bbox.y}
                          width={bbox.width}
                          height={bbox.height}
                          fill={stroke}
                          fillOpacity={isSelected ? 0.18 : 0.07}
                          stroke={stroke}
                          strokeWidth={isSelected ? 2.5 : 1.5}
                          rx={3}
                          style={{ cursor: "pointer" }}
                          onClick={() => setSelectedCandidateId(isSelected ? null : candidate.candidate_id)}
                        />
                      );
                    })}
                  </svg>
                )}
              </div>
            ) : (
              <div className="empty-state">No screenshot in this artifact.</div>
            )}

            {selectedCandidateId && (() => {
              const candidate = candidates.find((item) => item.candidate_id === selectedCandidateId);
              if (!candidate) return null;
              return (
                <div className="dd-bbox-info">
                  <strong>#{candidate.rank}</strong> {candidate.target?.label || candidate.element_id} — {candidate.action_type} — score {(candidate.score ?? 0).toFixed(2)}
                </div>
              );
            })()}
          </div>
        )}

        {activeTab === "elements" && (
          <div className="dd-elements">
            <input
              className="runs-search"
              placeholder="Search uid, tag, role, label, text..."
              value={elementSearch}
              onChange={(event) => setElementSearch(event.target.value)}
              style={{ marginBottom: 12 }}
            />
            <div className="table-wrap">
              <table className="runs-table">
                <thead>
                  <tr>
                    <th>uid</th>
                    <th>tag</th>
                    <th>type</th>
                    <th>role</th>
                    <th>label</th>
                    <th>text</th>
                    <th>vis</th>
                    <th>rect</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredElements.map((element, index) => (
                    <tr key={element.uid ?? index}>
                      <td className="mono table-cell-small">{element.uid ?? "-"}</td>
                      <td>{element.tag}</td>
                      <td>{element.type}</td>
                      <td>{element.role}</td>
                      <td>{element.label}</td>
                      <td className="table-ellipsis">{element.text}</td>
                      <td>{element.visible ? "✓" : "–"}</td>
                      <td className="mono table-cell-small">
                        {element.rect ? `${Math.round(element.rect.x)},${Math.round(element.rect.y)} ${Math.round(element.rect.width)}×${Math.round(element.rect.height)}` : "–"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="table-footnote">{filteredElements.length} of {(acquisition.actionable_elements ?? []).length} elements</div>
          </div>
        )}

        {activeTab === "pipeline" && (
          <div className="dd-pipeline">
            {stageOrder.map((stageName) => {
              const stage = stages[stageName] ?? {};
              const isExpanded = expandedStages[stageName];
              const outputCount = Array.isArray(stage.output)
                ? stage.output.length
                : (typeof stage.output === "object" && stage.output ? Object.keys(stage.output).length : 0);
              return (
                <div key={stageName} className="dd-stage-accordion">
                  <button
                    className="dd-stage-header"
                    onClick={() => setExpandedStages((current) => ({ ...current, [stageName]: !current[stageName] }))}
                  >
                    <span className="dd-stage-name">{stageName.replace(/_/g, " ")}</span>
                    <span className={`status-pill ${stage.status === "success" ? "success" : "neutral"}`}>{stage.status ?? "unknown"}</span>
                    <span className="dd-stage-meta">{stage.adapter_id}</span>
                    <span className="dd-stage-meta">{outputCount} items</span>
                    <span className="dd-stage-caret">{isExpanded ? "▲" : "▼"}</span>
                  </button>
                  {isExpanded && (
                    <div className="dd-stage-body">
                      {stage.diagnostics && (
                        <div className="dd-stage-diag">
                          {Object.entries(stage.diagnostics).map(([key, value]) => (
                            <span key={key} className="diag-chip">{key}: {String(value)}</span>
                          ))}
                        </div>
                      )}
                      <pre className="dd-stage-json">{JSON.stringify(stage.output, null, 2)}</pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {activeTab === "candidates" && mode === "training" && (
          <div className="dd-training">
            <div className="dd-card">
              <div className="dd-card-title">Training Annotation</div>
              <div className="dd-row">
                <span className="detail-key">Approved target</span>
                <span>{approvedCandidateId ?? "Not selected"}</span>
              </div>
              <div className="bbox-grid">
                {["x", "y", "width", "height"].map((key) => (
                  <label key={key} className="bbox-field">
                    <span className="bbox-field-label">{key}</span>
                    <input
                      className="form-input"
                      type="number"
                      step="0.1"
                      value={bboxOverride?.[key] ?? ""}
                      onChange={(event) => setBboxOverride?.((current) => ({
                        ...(current ?? { x: 0, y: 0, width: 0, height: 0 }),
                        [key]: Number(event.target.value),
                      }))}
                      disabled={!approvedCandidateId}
                    />
                  </label>
                ))}
              </div>
              {annotationMessage ? (
                <div className={`annotation-message ${annotationMessage.type}`}>
                  {annotationMessage.text}
                </div>
              ) : null}
              <div className="detail-actions">
                <button
                  className="primary-btn"
                  onClick={onSaveAnnotation}
                  disabled={!approvedCandidateId || annotationSaving}
                >
                  {annotationSaving ? "Saving..." : "Save Review"}
                </button>
              </div>
            </div>

            <div className="obs-candidates-header">
              Candidate Review ({candidates.length}) — {labeledCount} labeled
            </div>
            <div className="obs-candidate-list">
              {candidates.length === 0 ? (
                <div className="empty-state">No candidates. Live captures with real bboxes needed.</div>
              ) : candidates.map((candidate) => {
                const isSelected = selectedCandidateId === candidate.candidate_id;
                const label = labels[candidate.candidate_id];
                return (
                  <div
                    key={candidate.candidate_id}
                    className={`obs-candidate-item${isSelected ? " selected" : ""}${label ? ` ${label}` : ""}`}
                    onClick={() => setSelectedCandidateId(isSelected ? null : candidate.candidate_id)}
                  >
                    <div className="obs-candidate-rank">#{candidate.rank}</div>
                    <div className="obs-candidate-body">
                      <div className="obs-candidate-label">{candidate.target?.label || candidate.target?.tag || candidate.element_id}</div>
                      <div className="obs-candidate-meta">
                        <span className="mono">{candidate.action_type}</span>
                        <span>score {(candidate.score ?? 0).toFixed(2)}</span>
                        <span>conf {(candidate.confidence ?? 0).toFixed(2)}</span>
                      </div>
                    </div>
                    <div className="obs-label-btns">
                      <button
                        className={`obs-label-btn approve${label === "approve" ? " active" : ""}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          setLabels((current) => {
                            const next = { ...current };
                            for (const [candidateId, value] of Object.entries(next)) {
                              if (value === "approve") next[candidateId] = null;
                            }
                            if (label === "approve") {
                              next[candidate.candidate_id] = null;
                            } else {
                              next[candidate.candidate_id] = "approve";
                            }
                            return next;
                          });
                          const nextBbox = resolveBbox(candidate, acquisition);
                          if (nextBbox && label !== "approve") {
                            setBboxOverride?.(nextBbox);
                          }
                        }}
                      >
                        ✓
                      </button>
                      <button
                        className={`obs-label-btn reject${label === "reject" ? " active" : ""}`}
                        onClick={(event) => {
                          event.stopPropagation();
                          setLabels((current) => ({
                            ...current,
                            [candidate.candidate_id]: label === "reject" ? null : "reject",
                          }));
                        }}
                      >
                        ✗
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
