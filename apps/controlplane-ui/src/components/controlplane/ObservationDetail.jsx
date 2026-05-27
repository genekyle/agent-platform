import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmt, resolveBbox, screenshotFilename } from "./utils";

const API = import.meta.env.VITE_API_BASE_URL;

// Roles offered when the annotator labels a brand-new element the observer missed.
// Kept short on purpose — these feed forward into the state_transition model.
const MANUAL_ROLE_OPTIONS = ["button", "link", "input", "image", "text", "container", "other"];

// Action types the agent can perform at a labeled bbox. "any" is the legacy default
// from the goal — once an annotator labels a capture they should pick something specific.
const ACTION_TYPE_OPTIONS = ["click", "type", "scroll", "select", "wait", "any"];

// IoU between two rects in image-pixel space (used to sort the link picker so the
// most-overlapping observer candidate floats to the top).
function rectIoU(a, b) {
  if (!a || !b) return 0;
  const ax2 = a.x + a.width;
  const ay2 = a.y + a.height;
  const bx2 = b.x + b.width;
  const by2 = b.y + b.height;
  const ix1 = Math.max(a.x, b.x);
  const iy1 = Math.max(a.y, b.y);
  const ix2 = Math.min(ax2, bx2);
  const iy2 = Math.min(ay2, by2);
  const iw = Math.max(0, ix2 - ix1);
  const ih = Math.max(0, iy2 - iy1);
  const inter = iw * ih;
  const union = a.width * a.height + b.width * b.height - inter;
  return union > 0 ? inter / union : 0;
}

export function ObservationDetail({
  mode,
  selectedObs,
  selectedObsFilename,
  labels,
  setLabels,
  bboxOverride,
  setBboxOverride,
  manualCandidates,
  setManualCandidates,
  interactionEdits,
  setInteractionEdits,
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
  const [drawMode, setDrawMode] = useState(false);
  const [drawingRect, setDrawingRect] = useState(null);
  // The just-finished draw waiting on the user to link it (to a candidate or a manual element).
  const [pendingDraw, setPendingDraw] = useState(null);
  // The user's link choice form state.
  const [linkChoice, setLinkChoice] = useState({ type: "manual", candidateId: null, name: "", role: "button" });
  // Per-source visibility toggles for the screenshot overlay (does NOT hide from the
  // Candidates tab list or the link picker — those stay complete for labeling).
  const [visibleSources, setVisibleSources] = useState({ observer: true, vision: true, manual: true });
  const svgRef = useRef(null);

  useEffect(() => {
    setActiveTab(mode === "training" ? "screenshot" : "overview");
    setElementSearch("");
    setExpandedStages({});
    setImgSize({ natW: 0, natH: 0 });
    setSelectedCandidateId(null);
    setDrawMode(false);
    setDrawingRect(null);
    setPendingDraw(null);
    setLinkChoice({ type: "manual", candidateId: null, name: "", role: "button" });
    setVisibleSources({ observer: true, vision: true, manual: true });
  }, [mode, selectedObsFilename]);

  if (selectedObs?._error) {
    return <section className="panel"><div className="empty-state error">Error: {selectedObs._error}</div></section>;
  }

  const acquisition = selectedObs?.acquisition ?? {};
  const candidates = selectedObs?.ranked_candidates ?? [];
  // Vision-proposed candidates from OmniParser sidecar. Empty until the async
  // backfill writes them — the labeler handles that gracefully.
  const visionCandidates = selectedObs?.vision_candidates ?? [];
  const visionMeta = selectedObs?.vision_candidates_meta ?? null;
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

  // Vision-grounding prompt — what the model will be asked at inference time.
  // Surfaced into the labeler so the annotator knows what to draw a box around.
  const trainingAnnotation = selectedObs?.meta?.training_annotation ?? {};
  const elementQuery = trainingAnnotation.element_query
    ?? selectedObs?.acquisition?.training_metadata?.element_query
    ?? null;

  // Has any drawable label — drives Save button + bbox field enable state.
  const hasLabel = Boolean(approvedCandidateId || bboxOverride);

  // Map a pointer event to image-space (natural-pixel) coordinates.
  // The SVG uses viewBox = 0 0 natW natH with preserveAspectRatio="none",
  // so we scale by the rendered bounding rect.
  const eventToImageCoords = useCallback((event) => {
    const svg = svgRef.current;
    if (!svg || !imgSize.natW || !imgSize.natH) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const x = ((event.clientX - rect.left) / rect.width) * imgSize.natW;
    const y = ((event.clientY - rect.top) / rect.height) * imgSize.natH;
    return {
      x: Math.max(0, Math.min(imgSize.natW, x)),
      y: Math.max(0, Math.min(imgSize.natH, y)),
    };
  }, [imgSize]);

  const handleDrawPointerDown = useCallback((event) => {
    if (!drawMode || pendingDraw) return;
    const point = eventToImageCoords(event);
    if (!point) return;
    event.preventDefault();
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* noop */ }
    setDrawingRect({ startX: point.x, startY: point.y, x: point.x, y: point.y, width: 0, height: 0 });
  }, [drawMode, pendingDraw, eventToImageCoords]);

  const handleDrawPointerMove = useCallback((event) => {
    if (!drawMode || !drawingRect) return;
    const point = eventToImageCoords(event);
    if (!point) return;
    setDrawingRect((current) => {
      if (!current) return current;
      return {
        ...current,
        x: Math.min(current.startX, point.x),
        y: Math.min(current.startY, point.y),
        width: Math.abs(point.x - current.startX),
        height: Math.abs(point.y - current.startY),
      };
    });
  }, [drawMode, drawingRect, eventToImageCoords]);

  const handleDrawPointerUp = useCallback((event) => {
    if (!drawMode || !drawingRect) return;
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* noop */ }
    // Minimum size in image pixels — discard accidental taps.
    const MIN_DIM = 4;
    if (drawingRect.width >= MIN_DIM && drawingRect.height >= MIN_DIM) {
      const finalRect = {
        x: drawingRect.x,
        y: drawingRect.y,
        width: drawingRect.width,
        height: drawingRect.height,
      };
      // Open the link picker — the user must say what this box represents.
      // Pre-select the highest-IoU candidate (observer or vision) as a default.
      const observerRanked = (candidates ?? [])
        .map((candidate) => ({ source: "candidate", candidate, iou: rectIoU(finalRect, resolveBbox(candidate, acquisition)) }))
        .filter((entry) => entry.iou > 0);
      const visionRanked = (visionCandidates ?? [])
        .map((candidate) => ({ source: "vision", candidate, iou: rectIoU(finalRect, candidate.bbox) }))
        .filter((entry) => entry.iou > 0);
      const ranked = [...observerRanked, ...visionRanked].sort((a, b) => b.iou - a.iou);
      const best = ranked[0] ?? null;
      setLinkChoice({
        type: best ? best.source : "manual",
        candidateId: best?.candidate?.candidate_id ?? null,
        name: "",
        role: "button",
      });
      setPendingDraw(finalRect);
    }
    setDrawingRect(null);
  }, [drawMode, drawingRect, candidates, visionCandidates, acquisition]);

  // Commit the linked draw — writes either to an existing candidate's approve label
  // or creates a new manual_candidates entry. Either way, bboxOverride = drawn rect.
  const handleConfirmLink = useCallback(() => {
    if (!pendingDraw) return;
    if ((linkChoice.type === "candidate" || linkChoice.type === "vision") && linkChoice.candidateId) {
      // Approving an existing observer or vision candidate, with the drawn rect as the refined bbox.
      setLabels?.((current) => {
        const next = { ...current };
        for (const [id, value] of Object.entries(next)) {
          if (value === "approve") next[id] = null;
        }
        next[linkChoice.candidateId] = "approve";
        return next;
      });
      setBboxOverride?.(pendingDraw);
    } else {
      // Creating a brand-new manual candidate the observer didn't surface.
      const name = (linkChoice.name || "").trim();
      const role = (linkChoice.role || "other").trim();
      const newId = `manual-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
      const newEntry = {
        candidate_id: newId,
        bbox: pendingDraw,
        name,
        role,
        created_at: new Date().toISOString(),
      };
      setManualCandidates?.((current) => [...(current ?? []), newEntry]);
      setLabels?.((current) => {
        const next = { ...current };
        for (const [id, value] of Object.entries(next)) {
          if (value === "approve") next[id] = null;
        }
        next[newId] = "approve";
        return next;
      });
      setBboxOverride?.(pendingDraw);
    }
    setPendingDraw(null);
    setDrawMode(false);
  }, [pendingDraw, linkChoice, setLabels, setBboxOverride, setManualCandidates]);

  const handleCancelLink = useCallback(() => {
    setPendingDraw(null);
    setLinkChoice({ type: "manual", candidateId: null, name: "", role: "button" });
  }, []);

  // Candidates ranked by IoU against the pending draw — used to surface the best
  // observer matches at the top of the picker (degenerates to detector order when no draw).
  const candidatesByOverlap = useMemo(() => {
    if (!pendingDraw) return candidates;
    return [...candidates]
      .map((candidate) => ({
        candidate,
        iou: rectIoU(pendingDraw, resolveBbox(candidate, acquisition) ?? { x: 0, y: 0, width: 0, height: 0 }),
      }))
      .sort((a, b) => b.iou - a.iou)
      .map((entry) => ({ ...entry.candidate, _iou: entry.iou }));
  }, [pendingDraw, candidates, acquisition]);

  // Same idea for vision-proposed candidates — sorted by IoU against the draw,
  // ungated by detector order (we don't have a "rank" for these).
  const visionCandidatesByOverlap = useMemo(() => {
    if (!pendingDraw) {
      return [...visionCandidates].sort((a, b) => (b.confidence ?? 0) - (a.confidence ?? 0));
    }
    return [...visionCandidates]
      .map((candidate) => ({
        candidate,
        iou: rectIoU(pendingDraw, candidate.bbox ?? { x: 0, y: 0, width: 0, height: 0 }),
      }))
      .sort((a, b) => b.iou - a.iou)
      .map((entry) => ({ ...entry.candidate, _iou: entry.iou }));
  }, [pendingDraw, visionCandidates]);

  const clearDrawnBox = useCallback(() => {
    setBboxOverride?.(null);
    setDrawingRect(null);
  }, [setBboxOverride]);

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
            {mode === "training" && fileName ? (
              <div className="dd-draw-toolbar">
                <div className="dd-prompt">
                  <span className="dd-prompt-label">Target prompt</span>
                  <span className="dd-prompt-value">
                    {elementQuery
                      ? elementQuery
                      : <em className="dd-prompt-missing">No element_query on the scenario — set one in Domains before labeling.</em>}
                  </span>
                </div>
                <div className="dd-draw-actions">
                  <button
                    className={drawMode ? "primary-btn" : "ghost-btn"}
                    onClick={() => setDrawMode((current) => !current)}
                    disabled={!imgSize.natW || Boolean(pendingDraw)}
                  >
                    {drawMode ? "Drawing… click & drag on screenshot" : "Draw bounding box"}
                  </button>
                  {bboxOverride && !pendingDraw ? (
                    <button className="ghost-btn" onClick={clearDrawnBox}>Clear box</button>
                  ) : null}
                </div>
              </div>
            ) : null}

            {/* Per-source visibility toggles — overlay only. Candidates tab and link
                picker stay complete so labeling isn't disrupted. */}
            {mode === "training" && fileName ? (
              <div className="dd-source-filters">
                <span className="dd-source-filters-label">Show on screenshot:</span>
                <button
                  type="button"
                  className={`dd-source-pill source-observer${visibleSources.observer ? " active" : ""}`}
                  onClick={() => setVisibleSources((current) => ({ ...current, observer: !current.observer }))}
                  title="Observer / DOM heuristic candidates (blue)"
                >
                  Observer ({candidates.length})
                </button>
                <button
                  type="button"
                  className={`dd-source-pill source-vision${visibleSources.vision ? " active" : ""}`}
                  onClick={() => setVisibleSources((current) => ({ ...current, vision: !current.vision }))}
                  title="Vision-proposed candidates (cyan, OmniParser)"
                >
                  Vision ({visionCandidates.length})
                </button>
                <button
                  type="button"
                  className={`dd-source-pill source-manual${visibleSources.manual ? " active" : ""}`}
                  onClick={() => setVisibleSources((current) => ({ ...current, manual: !current.manual }))}
                  title="Manual / annotator-created elements (purple)"
                >
                  Manual ({(manualCandidates ?? []).length})
                </button>
              </div>
            ) : null}

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
                  <svg
                    ref={svgRef}
                    className={`obs-overlay${drawMode ? " is-drawing" : ""}`}
                    viewBox={`0 0 ${imgSize.natW} ${imgSize.natH}`}
                    preserveAspectRatio="none"
                    onPointerDown={handleDrawPointerDown}
                    onPointerMove={handleDrawPointerMove}
                    onPointerUp={handleDrawPointerUp}
                    onPointerCancel={handleDrawPointerUp}
                  >
                    {visibleSources.observer && candidates.map((candidate) => {
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
                          // While drawing, candidates must not intercept pointer events.
                          style={{ cursor: drawMode ? "crosshair" : "pointer", pointerEvents: drawMode ? "none" : "auto" }}
                          onClick={() => !drawMode && setSelectedCandidateId(isSelected ? null : candidate.candidate_id)}
                        />
                      );
                    })}

                    {/* Vision-proposed candidates (OmniParser) — cyan dashed to distinguish from observer-blue */}
                    {visibleSources.vision && visionCandidates.map((vision) => {
                      if (!vision?.bbox) return null;
                      const isApproved = labels[vision.candidate_id] === "approve";
                      const isSelected = selectedCandidateId === vision.candidate_id;
                      const stroke = isApproved ? "#16a34a" : "#06b6d4";
                      return (
                        <rect
                          key={vision.candidate_id}
                          x={vision.bbox.x}
                          y={vision.bbox.y}
                          width={vision.bbox.width}
                          height={vision.bbox.height}
                          fill={stroke}
                          fillOpacity={isSelected ? 0.18 : 0.06}
                          stroke={stroke}
                          strokeWidth={isSelected ? 2.5 : 1.6}
                          strokeDasharray={isApproved ? "" : "3 2"}
                          rx={3}
                          style={{ cursor: drawMode ? "crosshair" : "pointer", pointerEvents: drawMode ? "none" : "auto" }}
                          onClick={() => !drawMode && setSelectedCandidateId(
                            isSelected ? null : vision.candidate_id,
                          )}
                        />
                      );
                    })}

                    {/* Manual candidates the annotator created — purple to distinguish from observer */}
                    {visibleSources.manual && (manualCandidates ?? []).map((manual) => {
                      if (!manual?.bbox) return null;
                      const isApproved = labels[manual.candidate_id] === "approve";
                      return (
                        <rect
                          key={manual.candidate_id}
                          x={manual.bbox.x}
                          y={manual.bbox.y}
                          width={manual.bbox.width}
                          height={manual.bbox.height}
                          fill={isApproved ? "#16a34a" : "#a855f7"}
                          fillOpacity={isApproved ? 0.10 : 0.08}
                          stroke={isApproved ? "#16a34a" : "#a855f7"}
                          strokeWidth={1.8}
                          strokeDasharray={isApproved ? "" : "4 3"}
                          rx={3}
                          style={{ cursor: drawMode ? "crosshair" : "pointer", pointerEvents: drawMode ? "none" : "auto" }}
                          onClick={() => !drawMode && setSelectedCandidateId(
                            selectedCandidateId === manual.candidate_id ? null : manual.candidate_id,
                          )}
                        />
                      );
                    })}

                    {/* Persisted manual / approved bbox — distinct gold-on-dark style */}
                    {bboxOverride && !drawingRect && !pendingDraw ? (
                      <rect
                        x={bboxOverride.x}
                        y={bboxOverride.y}
                        width={bboxOverride.width}
                        height={bboxOverride.height}
                        fill="#facc15"
                        fillOpacity={0.12}
                        stroke="#facc15"
                        strokeWidth={2.5}
                        rx={3}
                        style={{ pointerEvents: "none" }}
                      />
                    ) : null}

                    {/* Pending draw waiting for link — same gold, pulsing-feel dashed border */}
                    {pendingDraw ? (
                      <rect
                        x={pendingDraw.x}
                        y={pendingDraw.y}
                        width={pendingDraw.width}
                        height={pendingDraw.height}
                        fill="#facc15"
                        fillOpacity={0.16}
                        stroke="#facc15"
                        strokeWidth={2.5}
                        strokeDasharray="5 3"
                        rx={3}
                        style={{ pointerEvents: "none" }}
                      />
                    ) : null}

                    {/* In-progress rect being drawn */}
                    {drawingRect ? (
                      <rect
                        x={drawingRect.x}
                        y={drawingRect.y}
                        width={drawingRect.width}
                        height={drawingRect.height}
                        fill="#facc15"
                        fillOpacity={0.18}
                        stroke="#facc15"
                        strokeWidth={2}
                        strokeDasharray="6 4"
                        rx={2}
                        style={{ pointerEvents: "none" }}
                      />
                    ) : null}
                  </svg>
                )}
              </div>
            ) : (
              <div className="empty-state">No screenshot in this artifact.</div>
            )}

            {/* Action panel — per-capture interaction layer. Sits below the screenshot so
                the annotator labels (location → identity → action) as a single mental motion. */}
            {mode === "training" && fileName ? (
              <div className="dd-action-panel">
                <div className="dd-action-panel-header">
                  <strong>Action at this step</strong>
                  <span className="dd-action-panel-sub">What the agent does once the bbox is grounded</span>
                </div>
                <div className="dd-action-panel-body">
                  <label className="dd-action-field dd-action-field-wide">
                    <span className="dd-action-field-label">Step intent (element_query)</span>
                    <input
                      className="form-input"
                      type="text"
                      placeholder={elementQuery || "e.g. type the user's email, click the Sign In button"}
                      value={interactionEdits?.element_query ?? ""}
                      onChange={(event) => setInteractionEdits?.((current) => ({
                        ...(current ?? {}),
                        element_query: event.target.value,
                      }))}
                    />
                  </label>
                  <label className="dd-action-field">
                    <span className="dd-action-field-label">Action type</span>
                    <select
                      className="form-input"
                      value={interactionEdits?.action_type ?? "any"}
                      onChange={(event) => setInteractionEdits?.((current) => ({
                        ...(current ?? {}),
                        action_type: event.target.value,
                      }))}
                    >
                      {ACTION_TYPE_OPTIONS.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </label>
                  {interactionEdits?.action_type === "type" ? (
                    <label className="dd-action-field dd-action-field-wide">
                      <span className="dd-action-field-label">Text to type</span>
                      <input
                        className="form-input"
                        type="text"
                        placeholder="literal value to enter at this step"
                        value={interactionEdits?.action_text ?? ""}
                        onChange={(event) => setInteractionEdits?.((current) => ({
                          ...(current ?? {}),
                          action_text: event.target.value,
                        }))}
                      />
                    </label>
                  ) : null}
                </div>

                {/* Page-state labels — separate row, optional, feeds future state-classifier
                    and state-transition models. Free text on purpose: don't formalize taxonomies
                    until 30+ labels exist and patterns are visible. */}
                <div className="dd-action-state-row">
                  <label className="dd-action-field">
                    <span className="dd-action-field-label">
                      Observed page state <span className="dd-action-optional">(optional)</span>
                    </span>
                    <input
                      className="form-input"
                      type="text"
                      placeholder="e.g. login_landing, logged_in_home, out_of_domain"
                      value={interactionEdits?.observed_page_state ?? ""}
                      onChange={(event) => setInteractionEdits?.((current) => ({
                        ...(current ?? {}),
                        observed_page_state: event.target.value,
                      }))}
                    />
                  </label>
                  <label className="dd-action-field">
                    <span className="dd-action-field-label">
                      Post-action state <span className="dd-action-optional">(optional)</span>
                    </span>
                    <input
                      className="form-input"
                      type="text"
                      placeholder="state the agent lands on AFTER this action"
                      value={interactionEdits?.post_action_state ?? ""}
                      onChange={(event) => setInteractionEdits?.((current) => ({
                        ...(current ?? {}),
                        post_action_state: event.target.value,
                      }))}
                    />
                  </label>
                </div>

                <div className="dd-action-panel-hint">
                  Saved alongside the bbox when you hit Save Review.
                  Top row overrides the scenario-level defaults for this capture (use for multi-step flows).
                  Bottom row tags the page state — use <code>out_of_domain</code> for wrong-tab captures
                  (e.g. session opened on YouTube instead of Marketplace). Optional, but tagging from
                  day 1 saves re-labeling later.
                </div>
              </div>
            ) : null}

            {/* Link picker — appears after a draw finishes, blocks further drawing until resolved */}
            {pendingDraw ? (
              <div className="dd-link-picker">
                <div className="dd-link-picker-header">
                  <strong>Link this bounding box to an element</strong>
                  <span className="dd-link-picker-sub">
                    {pendingDraw.width.toFixed(0)}×{pendingDraw.height.toFixed(0)} at ({pendingDraw.x.toFixed(0)}, {pendingDraw.y.toFixed(0)})
                  </span>
                </div>

                <div className="dd-link-picker-body">
                  <div className="dd-link-section">
                    <div className="dd-link-section-title">Existing observer candidate</div>
                    {candidatesByOverlap.length === 0 ? (
                      <div className="dd-link-empty">No observer candidates on this capture.</div>
                    ) : (
                      <div className="dd-link-list">
                        {candidatesByOverlap.slice(0, 8).map((candidate) => {
                          const isSelected = linkChoice.type === "candidate" && linkChoice.candidateId === candidate.candidate_id;
                          const label = candidate.target?.label || candidate.target?.tag || candidate.element_id;
                          return (
                            <button
                              key={candidate.candidate_id}
                              type="button"
                              className={`dd-link-row${isSelected ? " selected" : ""}`}
                              onClick={() => setLinkChoice({
                                type: "candidate",
                                candidateId: candidate.candidate_id,
                                name: "",
                                role: "button",
                              })}
                            >
                              <span className="dd-link-rank">#{candidate.rank}</span>
                              <span className="dd-link-label">{label}</span>
                              <span className="dd-link-meta mono">{candidate.action_type}</span>
                              <span className="dd-link-iou">IoU {(candidate._iou ?? 0).toFixed(2)}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  <div className="dd-link-section">
                    <div className="dd-link-section-title">
                      Vision-proposed candidate
                      {visionMeta?.version ? <span className="dd-link-meta"> · {visionMeta.version}</span> : null}
                    </div>
                    {visionCandidatesByOverlap.length === 0 ? (
                      <div className="dd-link-empty">No vision proposals on this capture yet. Backfill may still be running.</div>
                    ) : (
                      <div className="dd-link-list">
                        {visionCandidatesByOverlap.slice(0, 8).map((vision) => {
                          const isSelected = linkChoice.type === "vision" && linkChoice.candidateId === vision.candidate_id;
                          // Prefer the model's caption as the human-readable label;
                          // fall back to the id when caption is missing (older captures).
                          const primary = vision.caption?.trim() || vision.candidate_id;
                          return (
                            <button
                              key={vision.candidate_id}
                              type="button"
                              className={`dd-link-row dd-link-row-vision${isSelected ? " selected" : ""}`}
                              onClick={() => setLinkChoice({
                                type: "vision",
                                candidateId: vision.candidate_id,
                                name: "",
                                role: "button",
                              })}
                            >
                              <span className="dd-link-rank dd-vision-badge">V</span>
                              <span className="dd-link-label">{primary}</span>
                              <span className="dd-link-meta">conf {(vision.confidence ?? 0).toFixed(2)}</span>
                              <span className="dd-link-iou">{pendingDraw ? `IoU ${(vision._iou ?? 0).toFixed(2)}` : ""}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  <div className="dd-link-section">
                    <div className="dd-link-section-title">Or, this is a new element neither saw</div>
                    <button
                      type="button"
                      className={`dd-link-row dd-link-manual${linkChoice.type === "manual" ? " selected" : ""}`}
                      onClick={() => setLinkChoice((current) => ({ ...current, type: "manual", candidateId: null }))}
                    >
                      Create new manual element
                    </button>
                    {linkChoice.type === "manual" ? (
                      <div className="dd-link-manual-form">
                        <label className="dd-link-field">
                          <span className="dd-link-field-label">Name</span>
                          <input
                            className="form-input"
                            type="text"
                            placeholder="e.g. Google logo, Sign in button"
                            value={linkChoice.name}
                            onChange={(event) => setLinkChoice((current) => ({ ...current, name: event.target.value }))}
                            autoFocus
                          />
                        </label>
                        <label className="dd-link-field">
                          <span className="dd-link-field-label">Role</span>
                          <select
                            className="form-input"
                            value={linkChoice.role}
                            onChange={(event) => setLinkChoice((current) => ({ ...current, role: event.target.value }))}
                          >
                            {MANUAL_ROLE_OPTIONS.map((role) => (
                              <option key={role} value={role}>{role}</option>
                            ))}
                          </select>
                        </label>
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="dd-link-picker-actions">
                  <button className="ghost-btn" onClick={handleCancelLink}>Cancel</button>
                  <button
                    className="primary-btn"
                    onClick={handleConfirmLink}
                    disabled={linkChoice.type === "candidate" && !linkChoice.candidateId}
                  >
                    Confirm link
                  </button>
                </div>
              </div>
            ) : null}

            {selectedCandidateId && (() => {
              // Look across all three candidate sources so clicking any rect surfaces its info.
              const observer = candidates.find((item) => item.candidate_id === selectedCandidateId);
              if (observer) {
                return (
                  <div className="dd-bbox-info">
                    <strong>#{observer.rank}</strong> {observer.target?.label || observer.element_id} — {observer.action_type} — score {(observer.score ?? 0).toFixed(2)}
                  </div>
                );
              }
              const vision = visionCandidates.find((item) => item.candidate_id === selectedCandidateId);
              if (vision) {
                const w = Math.round(vision.bbox?.width ?? 0);
                const h = Math.round(vision.bbox?.height ?? 0);
                const cap = vision.caption?.trim();
                return (
                  <div className="dd-bbox-info">
                    <span className="dd-vision-badge">V</span>{" "}
                    {cap ? <strong>"{cap}"</strong> : <span className="mono">{vision.candidate_id}</span>}
                    {" "}— conf {(vision.confidence ?? 0).toFixed(2)} — {w}×{h} — {visionMeta?.version ?? "omniparser"}
                  </div>
                );
              }
              const manual = (manualCandidates ?? []).find((item) => item.candidate_id === selectedCandidateId);
              if (manual) {
                const w = Math.round(manual.bbox?.width ?? 0);
                const h = Math.round(manual.bbox?.height ?? 0);
                return (
                  <div className="dd-bbox-info">
                    <span className="dd-manual-badge">M</span>{" "}
                    <strong>{manual.name || "(unnamed)"}</strong> — role {manual.role || "other"} — {w}×{h}
                  </div>
                );
              }
              return null;
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
                <span>
                  {approvedCandidateId
                    ? approvedCandidateId
                    : bboxOverride
                      ? "Manual (drawn bbox)"
                      : "Not selected"}
                </span>
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
                      disabled={!hasLabel}
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
                  disabled={!hasLabel || annotationSaving}
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

            {visionCandidates.length > 0 ? (
              <>
                <div className="obs-candidates-header">
                  Vision Candidates ({visionCandidates.length}) — {visionMeta?.version ?? "omniparser"}
                </div>
                <div className="obs-candidate-list">
                  {visionCandidates.map((vision) => {
                    const isSelected = selectedCandidateId === vision.candidate_id;
                    const label = labels[vision.candidate_id];
                    return (
                      <div
                        key={vision.candidate_id}
                        className={`obs-candidate-item${isSelected ? " selected" : ""}${label ? ` ${label}` : ""}`}
                        onClick={() => setSelectedCandidateId(isSelected ? null : vision.candidate_id)}
                      >
                        <div className="obs-candidate-rank dd-vision-badge">V</div>
                        <div className="obs-candidate-body">
                          <div className="obs-candidate-label">
                            {vision.caption?.trim() || <span className="mono">{vision.candidate_id}</span>}
                          </div>
                          <div className="obs-candidate-meta">
                            <span>conf {(vision.confidence ?? 0).toFixed(2)}</span>
                            <span className="mono">{vision.bbox ? `${Math.round(vision.bbox.width)}×${Math.round(vision.bbox.height)}` : "no bbox"}</span>
                            <span className="mono table-cell-small">{vision.candidate_id}</span>
                          </div>
                        </div>
                        <div className="obs-label-btns">
                          <button
                            className={`obs-label-btn approve${label === "approve" ? " active" : ""}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              setLabels((current) => {
                                const next = { ...current };
                                for (const [id, value] of Object.entries(next)) {
                                  if (value === "approve") next[id] = null;
                                }
                                if (label === "approve") {
                                  next[vision.candidate_id] = null;
                                } else {
                                  next[vision.candidate_id] = "approve";
                                }
                                return next;
                              });
                              if (label !== "approve" && vision.bbox) {
                                setBboxOverride?.(vision.bbox);
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
                                [vision.candidate_id]: label === "reject" ? null : "reject",
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
              </>
            ) : null}

            {(manualCandidates?.length ?? 0) > 0 ? (
              <>
                <div className="obs-candidates-header">
                  Manual Elements ({manualCandidates.length}) — annotator-created
                </div>
                <div className="obs-candidate-list">
                  {manualCandidates.map((manual) => {
                    const isSelected = selectedCandidateId === manual.candidate_id;
                    const label = labels[manual.candidate_id];
                    return (
                      <div
                        key={manual.candidate_id}
                        className={`obs-candidate-item${isSelected ? " selected" : ""}${label ? ` ${label}` : ""}`}
                        onClick={() => setSelectedCandidateId(isSelected ? null : manual.candidate_id)}
                      >
                        <div className="obs-candidate-rank dd-manual-badge">M</div>
                        <div className="obs-candidate-body">
                          <div className="obs-candidate-label">{manual.name || "(unnamed)"}</div>
                          <div className="obs-candidate-meta">
                            <span className="mono">{manual.role || "other"}</span>
                            <span className="mono table-cell-small">{manual.candidate_id}</span>
                          </div>
                        </div>
                        <div className="obs-label-btns">
                          <button
                            className={`obs-label-btn approve${label === "approve" ? " active" : ""}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              setLabels((current) => {
                                const next = { ...current };
                                for (const [id, value] of Object.entries(next)) {
                                  if (value === "approve") next[id] = null;
                                }
                                if (label === "approve") {
                                  next[manual.candidate_id] = null;
                                } else {
                                  next[manual.candidate_id] = "approve";
                                }
                                return next;
                              });
                              if (label !== "approve" && manual.bbox) {
                                setBboxOverride?.(manual.bbox);
                              }
                            }}
                          >
                            ✓
                          </button>
                          <button
                            className="obs-label-btn reject"
                            title="Delete this manual element"
                            onClick={(event) => {
                              event.stopPropagation();
                              setManualCandidates?.((current) =>
                                (current ?? []).filter((entry) => entry.candidate_id !== manual.candidate_id),
                              );
                              setLabels((current) => {
                                const next = { ...current };
                                delete next[manual.candidate_id];
                                return next;
                              });
                            }}
                          >
                            🗑
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}
