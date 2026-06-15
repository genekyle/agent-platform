import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmt, resolveBbox, screenshotFilename } from "./utils";

const API = import.meta.env.VITE_API_BASE_URL;

// Roles offered when the annotator labels a brand-new element the observer missed.
// Kept short on purpose — these feed forward into the state_transition model.
const MANUAL_ROLE_OPTIONS = ["button", "link", "input", "image", "text", "container", "other"];

// Fallback action list used only if the registry hasn't loaded yet. The real list
// comes from the action registry via the actionOptions prop (user-extensible).
const FALLBACK_ACTIONS = [
  { action_id: "click", label: "Click", value_label: "Optional Payload" },
  { action_id: "type", label: "Type", value_label: "Text to Type" },
  { action_id: "clear", label: "Clear", value_label: null },
  { action_id: "any", label: "Any", value_label: "Optional Payload" },
];

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
  pageStateOptions = [],
  goals = [],
  domains = [],
  onCreatePageState,
  actionOptions = [],
  onCreateAction,
  onRefreshVision,
  onGenerateCaptions,
  captionsLoading,
  onSaveAnnotation,
  annotationSaving,
  annotationMessage,
  onBack,
}) {
  const [addingAction, setAddingAction] = useState(false);
  const [newActionName, setNewActionName] = useState("");
  const [actionError, setActionError] = useState(null);
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
  const [visibleSources, setVisibleSources] = useState({ ax: true, observer: true, vision: true, manual: true });
  // Candidate the cursor is hovering in the link picker — highlighted on the screenshot
  // so the annotator can instantly see which box a row refers to (disambiguates same-label
  // candidates like two "input"s). Forced visible regardless of source toggles.
  const [hoveredCandidateId, setHoveredCandidateId] = useState(null);
  const [newPageStateName, setNewPageStateName] = useState("");
  const [newPageStateCategory, setNewPageStateCategory] = useState("Login");
  // Single stepped state picker: only one of {observed, post_action} is active/rendered
  // at a time (no double-rendering the whole drill-down). Selecting current auto-advances.
  const [activeStateField, setActiveStateField] = useState("observed_page_state");
  // Per-field drill-down + search UI state for the state picker (keyed by field
  // so the observed/post pickers don't interfere with each other).
  const [searchByField, setSearchByField] = useState({});
  // Folder navigation per field: { level: 'root'|'domain'|'objective', domainId, goalId }.
  // Undefined ⇒ fall back to the capture's home folder (computed in the picker).
  const [navByField, setNavByField] = useState({});
  const [addingStateTarget, setAddingStateTarget] = useState(null);
  const [pageStateError, setPageStateError] = useState(null);
  const svgRef = useRef(null);
  const imgRef = useRef(null);

  const fileNameForSync = screenshotFilename(selectedObs);

  // Single source of truth for the screenshot's natural dimensions: read straight
  // off the <img> DOM node. Called from the callback ref (fires on mount), onLoad
  // (uncached loads), and a filename-keyed effect with a rAF retry (cached races).
  // Only updates state when the value actually changes, so it never churns renders.
  const measureImg = useCallback((el) => {
    if (!el || !el.complete || !el.naturalWidth) return false;
    setImgSize((current) => (
      current.natW === el.naturalWidth && current.natH === el.naturalHeight
        ? current
        : { natW: el.naturalWidth, natH: el.naturalHeight }
    ));
    return true;
  }, []);

  useEffect(() => {
    let raf = 0;
    if (!measureImg(imgRef.current)) {
      raf = requestAnimationFrame(() => measureImg(imgRef.current));
    }
    return () => { if (raf) cancelAnimationFrame(raf); };
  }, [fileNameForSync, measureImg]);

  useEffect(() => {
    setActiveTab(mode === "training" ? "screenshot" : "overview");
    setElementSearch("");
    setExpandedStages({});
    // NOTE: do NOT reset imgSize to {0,0} here. Doing so runs AFTER the image's
    // ref/onLoad has already measured the new screenshot, clobbering it back to zero
    // and hiding the overlay until some unrelated re-render. The <img> (remounted via
    // key per screenshot) is the sole driver of imgSize now.
    setSelectedCandidateId(null);
    setDrawMode(false);
    setDrawingRect(null);
    setPendingDraw(null);
    setLinkChoice({ type: "manual", candidateId: null, name: "", role: "button" });
    setVisibleSources({ ax: true, observer: true, vision: true, manual: true });
    setActiveStateField("observed_page_state");
    setNewPageStateName("");
    setAddingStateTarget(null);
    setPageStateError(null);
    setNavByField({});   // new capture → reopen each picker in its home folder
    setSearchByField({});
  }, [mode, selectedObsFilename]);

  if (selectedObs?._error) {
    return <section className="panel"><div className="empty-state error">Error: {selectedObs._error}</div></section>;
  }

  const acquisition = selectedObs?.acquisition ?? {};
  const candidates = selectedObs?.ranked_candidates ?? [];
  // CDP-AX candidates — the PRIMARY proposer (captured live, role + accessible
  // name + bbox already in screenshot pixels). Empty on captures taken before AX
  // was wired in. Rendered amber, solid (primary), vs vision's dashed cyan.
  const axCandidates = selectedObs?.ax_candidates ?? [];
  const axMeta = selectedObs?.ax_candidates_meta ?? null;
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
  // This capture's "home" context — the picker opens already inside this objective folder.
  const captureDomainId = trainingAnnotation?.domain_id || null;
  const captureGoalId = trainingAnnotation?.goal_id || null;
  const elementQuery = trainingAnnotation.element_query
    ?? selectedObs?.acquisition?.training_metadata?.element_query
    ?? null;
  const currentActionType = interactionEdits?.action_type || "any";
  // Action list comes from the registry (user-extensible); fall back to a minimal
  // built-in set only until the fetch lands.
  const actionList = (actionOptions && actionOptions.length) ? actionOptions : FALLBACK_ACTIONS;
  const actionValueLabel = actionList.find((a) => a.action_id === currentActionType)?.value_label ?? "Action Payload";
  // "clear" (and any action with no value_label) has no payload field.
  const actionHasPayload = Boolean(actionValueLabel);
  // The payload field is action-aware. For value-bearing actions (type/select) it
  // captures a VALUE SLOT — the variable the workflow binds at runtime (email,
  // password, …), never the literal secret. For everything else it captures free-text
  // CONTEXT about the target UI ("primary Log In button"), since the action type
  // already says what to do.
  const VALUE_SLOT_ACTIONS = new Set(["type", "select"]);
  const payloadMode = VALUE_SLOT_ACTIONS.has(currentActionType) ? "slot" : "context";

  // Has any drawable label — drives Save button + bbox field enable state.
  const hasLabel = Boolean(approvedCandidateId || bboxOverride);

  // Resolve the current approved selection across ALL sources (observer / vision /
  // manual) so the annotator can SEE what's saved on this capture — which element,
  // its caption/label, and where. Without this the gold box is anonymous and you
  // can't tell what you picked or whether to change it.
  const approvedSelection = useMemo(() => {
    const approvedId = Object.keys(labels).find((id) => labels[id] === "approve") ?? null;
    if (approvedId) {
      const ax = axCandidates.find((a) => a.candidate_id === approvedId);
      if (ax) {
        return { source: "ax", id: approvedId, label: ax.caption?.trim() || ax.role || approvedId };
      }
      const obs = candidates.find((c) => c.candidate_id === approvedId);
      if (obs) {
        return { source: "observer", id: approvedId, label: obs.target?.label || obs.target?.tag || obs.element_id };
      }
      const vis = visionCandidates.find((v) => v.candidate_id === approvedId);
      if (vis) {
        return { source: "vision", id: approvedId, label: vis.caption?.trim() || approvedId };
      }
      const man = (manualCandidates ?? []).find((m) => m.candidate_id === approvedId);
      if (man) {
        return { source: "manual", id: approvedId, label: man.name || "(unnamed manual element)" };
      }
      return { source: "unknown", id: approvedId, label: approvedId };
    }
    // A drawn box with no linked candidate (pure manual draw).
    if (bboxOverride) return { source: "drawn", id: null, label: "Drawn box (no element link)" };
    return null;
  }, [labels, axCandidates, candidates, visionCandidates, manualCandidates, bboxOverride]);

  const updateInteractionEdit = useCallback((field, value) => {
    setInteractionEdits?.((current) => ({
      ...(current ?? {}),
      [field]: value,
    }));
  }, [setInteractionEdits]);

  const selectPageState = useCallback((field, value) => {
    updateInteractionEdit(field, value);
    setAddingStateTarget(null);
    setPageStateError(null);
    // Step-through: choosing the CURRENT state advances the single window to the
    // EXPECTED-next step (fresh screen), instead of showing both pickers at once.
    if (field === "observed_page_state") setActiveStateField("post_action_state");
  }, [updateInteractionEdit]);

  const addPageState = useCallback(async (field) => {
    if (!onCreatePageState) return;
    setPageStateError(null);
    try {
      const created = await onCreatePageState(newPageStateName, { category: newPageStateCategory });
      if (created?.page_state_id) {
        updateInteractionEdit(field, created.page_state_id);
        setNewPageStateName("");
        setAddingStateTarget(null);
      }
    } catch (error) {
      setPageStateError(error.message || String(error));
    }
  }, [newPageStateName, newPageStateCategory, onCreatePageState, updateInteractionEdit]);

  const goalById = useMemo(() => {
    const m = new Map();
    for (const g of goals || []) m.set(g.goal_id, g);
    return m;
  }, [goals]);
  const domainLabelOf = useCallback((id) => {
    const d = (domains || []).find((x) => (x.domain_id || x.id) === id);
    return d?.display_name || d?.label || id;
  }, [domains]);

  // Folder index: relevance rings centered on a capture, matching the canonical
  // taxonomy Domain ▸ Stage ▸ Objective ▸ states. Each domain splits into lifecycle
  // stages (unauthenticated / authenticated / neutral); each stage holds objective
  // sub-folders (goal-scoped states) plus stage-wide domain states (homepage, nav).
  // Global states sit beside the domains at the root.
  const sortCat = (m) =>
    [...m.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([cat, list]) => [cat, list.sort((a, b) => a.display_name.localeCompare(b.display_name))]);
  // Effective stage of a state: explicit field, else inherit from its goal, else neutral.
  const stageOf = (s) =>
    s.stage || ((s.scope === "goal" || s.scope === "scenario") && goalById.get(s.goal_id)?.stage) || "neutral";
  const folderIndex = useMemo(() => {
    // domainId -> Map(stage -> { objectives:Map(goalId->{label,byCat}), stageWide:Map(cat->[]) })
    const domainsMap = new Map();
    const globalByCat = new Map();
    const push = (m, cat, s) => { if (!m.has(cat)) m.set(cat, []); m.get(cat).push(s); };
    const ensureStage = (domId, stage) => {
      if (!domainsMap.has(domId)) domainsMap.set(domId, new Map());
      const stages = domainsMap.get(domId);
      if (!stages.has(stage)) stages.set(stage, { objectives: new Map(), stageWide: new Map() });
      return stages.get(stage);
    };
    for (const s of pageStateOptions) {
      const cat = s.category || "general";
      if (s.scope === "global" || (!s.domain_id && !s.goal_id)) {
        push(globalByCat, cat, s);
        continue;
      }
      const domId = s.domain_id || goalById.get(s.goal_id)?.domain_id || "_unscoped";
      const st = ensureStage(domId, stageOf(s));
      if ((s.scope === "goal" || s.scope === "scenario") && s.goal_id) {
        if (!st.objectives.has(s.goal_id)) {
          const g = goalById.get(s.goal_id);
          st.objectives.set(s.goal_id, { label: g?.display_name || s.goal_id, byCat: new Map() });
        }
        push(st.objectives.get(s.goal_id).byCat, cat, s);
      } else {
        push(st.stageWide, cat, s);
      }
    }
    return { domainsMap, globalByCat };
  }, [pageStateOptions, goalById]);

  const STAGE_ORDER = ["unauthenticated", "authenticated", "neutral"];
  const STAGE_LABEL = { unauthenticated: "Unauthenticated", authenticated: "Authenticated", neutral: "Unstaged" };
  const countStage = (st) =>
    [...st.objectives.values()].reduce((n, o) => n + [...o.byCat.values()].reduce((k, l) => k + l.length, 0), 0)
    + [...st.stageWide.values()].reduce((n, l) => n + l.length, 0);
  const countDomain = (stages) => [...stages.values()].reduce((n, st) => n + countStage(st), 0);

  // Known category names (from existing states + a few defaults) — powers the
  // combobox so you can pick an existing category or type a brand-new one ("Login").
  const knownCategories = useMemo(() => {
    const set = new Set(["Login", "Navigation", "Content", "Error", "Checkout", "General"]);
    for (const s of pageStateOptions) if (s.category) set.add(s.category);
    return [...set];
  }, [pageStateOptions]);

  const renderPageStatePicker = (field, title, helper) => {
    const selected = interactionEdits?.[field] ?? "";
    const selectedState = pageStateOptions.find((s) => s.page_state_id === selected) || null;
    const isAdding = addingStateTarget === field;
    const search = (searchByField[field] || "").trim().toLowerCase();
    const setSearch = (v) => setSearchByField((m) => ({ ...m, [field]: v }));

    const { domainsMap, globalByCat } = folderIndex;

    // Where this field's picker is currently pointed. Default = the capture's home
    // folder: its own objective (inside that objective's stage) if known, else its
    // domain, else the root.
    const homeStage = captureGoalId ? (goalById.get(captureGoalId)?.stage || "neutral") : null;
    const homeNav = captureGoalId
      ? { level: "objective", domainId: captureDomainId, stage: homeStage, goalId: captureGoalId }
      : captureDomainId
        ? { level: "domain", domainId: captureDomainId }
        : { level: "root" };
    const nav = navByField[field] || homeNav;
    const setNav = (n) => setNavByField((m) => ({ ...m, [field]: n }));

    // Search bypasses folders entirely — flat matching states (the quick escape hatch).
    const searchMatches = search
      ? pageStateOptions.filter((s) =>
          [s.display_name, s.state_id, s.category].filter(Boolean).join(" ").toLowerCase().includes(search))
      : [];

    const chip = (state) => (
      <button
        key={`${field}-${state.page_state_id}`}
        type="button"
        className={`dd-state-chip scope-${state.scope || "global"}${selected === state.page_state_id ? " selected" : ""}`}
        onClick={() => { selectPageState(field, state.page_state_id); setSearch(""); }}
        title={`${state.state_id} · ${state.scope}`}
      >
        {state.display_name}
        <span className="dd-state-scope-tag">{(state.scope || "global")[0].toUpperCase()}</span>
      </button>
    );

    const catBlocks = (byCat) =>
      sortCat(byCat).map(([cat, list]) => (
        <div key={`${field}-cat-${cat}`} className="dd-state-cat-block">
          <div className="dd-state-cat-label">{cat}</div>
          <div className="dd-state-chip-row">{list.map(chip)}</div>
        </div>
      ));

    // Breadcrumb: All domains › <Domain> › <Stage> › <Objective>. Each crumb pops out.
    const crumbs = [{ label: "All domains", nav: { level: "root" } }];
    if (["domain", "stage", "objective"].includes(nav.level) && nav.domainId) {
      crumbs.push({ label: domainLabelOf(nav.domainId), nav: { level: "domain", domainId: nav.domainId } });
    }
    if (["stage", "objective"].includes(nav.level) && nav.stage) {
      crumbs.push({ label: STAGE_LABEL[nav.stage] || nav.stage, nav: { level: "stage", domainId: nav.domainId, stage: nav.stage } });
    }
    if (nav.level === "objective" && nav.goalId) {
      crumbs.push({ label: goalById.get(nav.goalId)?.display_name || nav.goalId, nav });
    }

    const folder = (key, cls, icon, name, count, onClick, badge) => (
      <button key={key} type="button" className={`dd-state-folder ${cls}`} onClick={onClick}>
        <span className="dd-state-folder-icon">{icon}</span>
        <span className="dd-state-folder-name">{name}</span>
        {badge ? <span className="dd-state-home-badge">{badge}</span> : null}
        <span className="dd-state-folder-count">{count}</span>
      </button>
    );

    const renderBody = () => {
      // Objective: the leaf — category blocks of that objective's states.
      if (nav.level === "objective") {
        const obj = domainsMap.get(nav.domainId)?.get(nav.stage)?.objectives.get(nav.goalId);
        const blocks = obj ? catBlocks(obj.byCat) : [];
        return blocks.length
          ? <div className="dd-state-folder-body">{blocks}</div>
          : <div className="dd-state-empty">No states for this objective yet — add one below, or pop up a level.</div>;
      }
      // Stage: objective sub-folders + stage-wide domain states.
      if (nav.level === "stage") {
        const st = domainsMap.get(nav.domainId)?.get(nav.stage);
        const objEntries = st ? [...st.objectives.entries()].sort((a, b) => a[1].label.localeCompare(b[1].label)) : [];
        return (
          <div className="dd-state-folder-body">
            {objEntries.length ? (
              <div className="dd-state-folder-grid">
                {objEntries.map(([gid, o]) =>
                  folder(`${field}-objf-${gid}`, `stage-${nav.stage}`, "📁", o.label,
                    [...o.byCat.values()].reduce((n, l) => n + l.length, 0),
                    () => setNav({ level: "objective", domainId: nav.domainId, stage: nav.stage, goalId: gid })))}
              </div>
            ) : null}
            {st && st.stageWide.size ? (
              <div className="dd-state-domainwide">
                <div className="dd-state-section-label">{STAGE_LABEL[nav.stage]} · domain-wide states</div>
                {catBlocks(st.stageWide)}
              </div>
            ) : null}
            {!objEntries.length && !(st && st.stageWide.size) ? (
              <div className="dd-state-empty">No states in this stage yet.</div>
            ) : null}
          </div>
        );
      }
      // Domain: stage folders.
      if (nav.level === "domain") {
        const stages = domainsMap.get(nav.domainId);
        const stageEntries = stages
          ? [...stages.entries()].sort((a, b) => STAGE_ORDER.indexOf(a[0]) - STAGE_ORDER.indexOf(b[0]))
          : [];
        return stageEntries.length ? (
          <div className="dd-state-folder-body">
            <div className="dd-state-folder-grid">
              {stageEntries.map(([stage, st]) =>
                folder(`${field}-stagef-${stage}`, `stage-${stage}`, "🗂",
                  STAGE_LABEL[stage] || stage, countStage(st),
                  () => setNav({ level: "stage", domainId: nav.domainId, stage })))}
            </div>
          </div>
        ) : <div className="dd-state-empty">No states in this domain yet.</div>;
      }
      // Root: domain folders + Global section.
      const domEntries = [...domainsMap.entries()].sort((a, b) => domainLabelOf(a[0]).localeCompare(domainLabelOf(b[0])));
      return (
        <div className="dd-state-folder-body">
          <div className="dd-state-folder-grid">
            {domEntries.map(([did, stages]) =>
              folder(`${field}-domf-${did}`, did === captureDomainId ? "is-home" : "", "📂",
                domainLabelOf(did), countDomain(stages),
                () => setNav({ level: "domain", domainId: did }),
                did === captureDomainId ? "home" : null))}
          </div>
          {globalByCat.size ? (
            <div className="dd-state-domainwide">
              <div className="dd-state-section-label">Global states (every domain)</div>
              {catBlocks(globalByCat)}
            </div>
          ) : null}
        </div>
      );
    };

    return (
      <div className="dd-state-picker">
        <div className="dd-state-picker-header">
          <div>
            <span className="dd-action-label">{title}</span>
            <span className="dd-state-helper">{helper}</span>
          </div>
          <button
            type="button"
            className="ghost-btn dd-mini-btn"
            onClick={() => { setAddingStateTarget(isAdding ? null : field); setPageStateError(null); }}
          >
            {isAdding ? "Cancel" : "+ New state"}
          </button>
        </div>

        {/* Current selection + clear */}
        <div className="dd-state-current">
          {selectedState ? (
            <span className={`dd-state-chip selected scope-${selectedState.scope || "global"}`}>
              {selectedState.display_name}
              <span className="dd-state-scope-tag">{(selectedState.scope || "global")[0].toUpperCase()}</span>
            </span>
          ) : (
            <span className="dd-state-current-empty">Not set</span>
          )}
          {selected ? (
            <button type="button" className="ghost-btn dd-mini-btn" onClick={() => selectPageState(field, "")}>Clear</button>
          ) : null}
        </div>

        {/* Quick search — jumps past the folders entirely */}
        <input
          className="form-input dd-state-search"
          placeholder="Search all states…"
          value={searchByField[field] || ""}
          onChange={(e) => setSearch(e.target.value)}
        />

        {search ? (
          <div className="dd-state-chip-row dd-state-results">
            {searchMatches.length === 0
              ? <div className="dd-state-empty">No states match “{searchByField[field]}”.</div>
              : searchMatches.map(chip)}
          </div>
        ) : (
          <>
            {/* Breadcrumb — click any crumb to pop outward */}
            <nav className="dd-state-crumbs" aria-label="State folders">
              {crumbs.map((c, i) => (
                <span key={`${field}-crumb-${i}`} className="dd-state-crumb-wrap">
                  {i > 0 ? <span className="dd-state-crumb-sep">›</span> : null}
                  {i < crumbs.length - 1 ? (
                    <button type="button" className="dd-state-crumb-link" onClick={() => setNav(c.nav)}>{c.label}</button>
                  ) : (
                    <span className="dd-state-crumb-current">{c.label}</span>
                  )}
                </span>
              ))}
            </nav>
            {renderBody()}
          </>
        )}

        {isAdding ? (
          <div className="dd-state-add-row">
            <input
              className="form-input"
              value={newPageStateName}
              placeholder="New state name (e.g. Email Entered)"
              onChange={(event) => setNewPageStateName(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addPageState(field); } }}
              autoFocus
            />
            <input
              className="form-input"
              list="dd-known-categories"
              value={newPageStateCategory}
              placeholder="Category"
              onChange={(e) => setNewPageStateCategory(e.target.value)}
            />
            <datalist id="dd-known-categories">
              {knownCategories.map((c) => <option key={c} value={c} />)}
            </datalist>
            <button className="primary-btn" type="button" onClick={() => addPageState(field)}>Add &amp; select</button>
          </div>
        ) : null}
      </div>
    );
  };

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
    setHoveredCandidateId(null);
  }, [pendingDraw, linkChoice, setLabels, setBboxOverride, setManualCandidates]);

  const handleCancelLink = useCallback(() => {
    setPendingDraw(null);
    setLinkChoice({ type: "manual", candidateId: null, name: "", role: "button" });
    setHoveredCandidateId(null);
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

  // Approve a candidate directly by clicking its box on the screenshot (or toggle it
  // off if already approved). This is the fast path the selection banner promises —
  // no draw required. Sets the approved label AND the approved_bbox to that candidate's
  // box, clearing any prior approval (single-target). Pass the candidate's bbox.
  const toggleApproveCandidate = useCallback((candidateId, bbox) => {
    const wasApproved = labels?.[candidateId] === "approve";
    setLabels?.((current) => {
      const next = { ...current };
      for (const [id, v] of Object.entries(next)) {
        if (v === "approve") next[id] = null;
      }
      if (!wasApproved) next[candidateId] = "approve";
      return next;
    });
    if (wasApproved) {
      setBboxOverride?.(null);
    } else if (bbox) {
      setBboxOverride?.({
        x: bbox.x, y: bbox.y, width: bbox.width, height: bbox.height,
      });
    }
    setSelectedCandidateId(candidateId);
  }, [labels, setLabels, setBboxOverride]);

  // Full reset of the current target: clears the drawn/approved box AND un-approves
  // whatever candidate was selected, so "re-pick" truly starts fresh. (clearDrawnBox
  // alone only drops the box, leaving an approved candidate label behind.)
  const clearSelection = useCallback(() => {
    setBboxOverride?.(null);
    setDrawingRect(null);
    setLabels?.((current) => {
      const next = { ...current };
      for (const [id, value] of Object.entries(next)) {
        if (value === "approve") next[id] = null;
      }
      return next;
    });
  }, [setBboxOverride, setLabels]);

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
        {onBack ? (
          <nav className="obs-breadcrumb" aria-label="Breadcrumb">
            <button type="button" className="obs-crumb obs-crumb-link" onClick={onBack}>‹ Dataset Browser</button>
            {trainingAnnotation?.scenario_id ? (
              <><span className="obs-crumb-sep">/</span><span className="obs-crumb">{trainingAnnotation.scenario_id}</span></>
            ) : null}
            {trainingAnnotation?.observed_page_state ? (
              <><span className="obs-crumb-sep">/</span><span className="obs-crumb">{pageStateOptions.find((s) => s.page_state_id === trainingAnnotation.observed_page_state)?.display_name || trainingAnnotation.observed_page_state}</span></>
            ) : null}
            <span className="obs-crumb-sep">/</span>
            <span className="obs-crumb obs-crumb-current">{trainingAnnotation?.element_query?.slice(0, 40) || selectedObsFilename}</span>
          </nav>
        ) : (
          <span className="obs-detail-filename">{selectedObsFilename}</span>
        )}
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
                  className={`dd-source-pill source-ax${visibleSources.ax ? " active" : ""}`}
                  onClick={() => setVisibleSources((current) => ({ ...current, ax: !current.ax }))}
                  title="CDP-AX candidates (amber) — the primary proposer"
                >
                  AX ({axCandidates.length})
                </button>
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
                {onRefreshVision ? (
                  <button
                    type="button"
                    className="ghost-btn dd-mini-btn dd-vision-reload"
                    onClick={() => onRefreshVision(selectedObsFilename)}
                    title="Re-fetch vision proposals"
                  >
                    ↻ Vision
                  </button>
                ) : null}
                {onGenerateCaptions && visionCandidates.length > 0 ? (
                  <button
                    type="button"
                    className="ghost-btn dd-mini-btn"
                    onClick={() => onGenerateCaptions(selectedObsFilename)}
                    disabled={captionsLoading}
                    title="Run Florence-2 to add human-readable captions to these boxes (slow; on-demand)"
                  >
                    {captionsLoading ? "🅰 Captioning…" : (visionMeta?.timing?.captioned ? "🅰 Recaption" : "🅰 Generate captions")}
                  </button>
                ) : null}
                {visionCandidates.length === 0 ? (
                  <span className="dd-vision-pending">generating vision proposals…</span>
                ) : null}
                {visionMeta?.timing?.total_ms ? (
                  <span
                    className="dd-vision-timing mono"
                    title={`device ${visionMeta.timing.device} · load ${visionMeta.timing.load_ms}ms · detect ${visionMeta.timing.detect_ms}ms · caption ${visionMeta.timing.caption_ms}ms · raw ${visionMeta.timing.raw_detections} → kept ${visionMeta.timing.kept}, captioned ${visionMeta.timing.captioned}`}
                  >
                    ⏱ {(visionMeta.timing.total_ms / 1000).toFixed(1)}s
                    {" "}(detect {(visionMeta.timing.detect_ms / 1000).toFixed(1)}s · caption {(visionMeta.timing.caption_ms / 1000).toFixed(1)}s)
                  </span>
                ) : null}
              </div>
            ) : null}

            {/* Current selection banner — shows WHAT is currently labeled on this capture
                (which element + source) so the annotator can see their choice and decide
                whether to change it. Persists after save (restored from the annotation). */}
            {mode === "training" && fileName ? (
              <div className={`dd-selection-banner${approvedSelection ? " has-selection" : ""}`}>
                {approvedSelection ? (
                  <>
                    <span className="dd-selection-check">✓ Target:</span>
                    <span className={`dd-selection-badge source-${approvedSelection.source}`}>
                      {approvedSelection.source}
                    </span>
                    <span className="dd-selection-label">{approvedSelection.label}</span>
                    {bboxOverride ? (
                      <span className="dd-selection-box mono">
                        box {Math.round(bboxOverride.width)}×{Math.round(bboxOverride.height)} @{Math.round(bboxOverride.x)},{Math.round(bboxOverride.y)}
                      </span>
                    ) : null}
                    <button className="ghost-btn dd-mini-btn dd-selection-clear" onClick={clearSelection}>
                      Clear / re-pick
                    </button>
                  </>
                ) : (
                  <span className="dd-selection-empty">
                    No target selected yet — approve a candidate (click its box or a picker row) or draw one.
                  </span>
                )}
              </div>
            ) : null}

            {fileName ? (
              <div className="dd-viewport">
                <img
                  key={fileName}
                  ref={(el) => { imgRef.current = el; measureImg(el); }}
                  className="obs-screenshot"
                  src={`${API}/api/observations/screenshots/${fileName}`}
                  alt="Page screenshot"
                  onLoad={(event) => measureImg(event.currentTarget)}
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
                    {/* CDP-AX candidates — the PRIMARY proposer. Amber, solid (vs vision's dashed cyan). */}
                    {visibleSources.ax && axCandidates.map((ax) => {
                      if (!ax?.bbox) return null;
                      const isApproved = labels[ax.candidate_id] === "approve";
                      const isSelected = selectedCandidateId === ax.candidate_id;
                      const stroke = isApproved ? "#16a34a" : "#f59e0b";
                      return (
                        <rect
                          key={ax.candidate_id}
                          x={ax.bbox.x}
                          y={ax.bbox.y}
                          width={ax.bbox.width}
                          height={ax.bbox.height}
                          fill={stroke}
                          fillOpacity={isSelected ? 0.18 : 0.06}
                          stroke={stroke}
                          strokeWidth={isSelected ? 2.5 : 1.6}
                          rx={3}
                          style={{ cursor: drawMode ? "crosshair" : "pointer", pointerEvents: drawMode ? "none" : "auto" }}
                          onClick={() => !drawMode && toggleApproveCandidate(ax.candidate_id, ax.bbox)}
                        />
                      );
                    })}

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
                          onClick={() => !drawMode && toggleApproveCandidate(candidate.candidate_id, bbox)}
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
                          onClick={() => !drawMode && toggleApproveCandidate(vision.candidate_id, vision.bbox)}
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
                          onClick={() => !drawMode && toggleApproveCandidate(manual.candidate_id, manual.bbox)}
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

                    {/* Hover highlight — lights up the box for whichever picker row the
                        cursor is on, so the annotator can see which element a row maps to.
                        Looks across observer + vision; forced visible ignoring source toggles. */}
                    {(() => {
                      if (!hoveredCandidateId) return null;
                      const obs = candidates.find((c) => c.candidate_id === hoveredCandidateId);
                      const hb = obs
                        ? resolveBbox(obs, acquisition)
                        : visionCandidates.find((v) => v.candidate_id === hoveredCandidateId)?.bbox;
                      if (!hb) return null;
                      return (
                        <rect
                          x={hb.x}
                          y={hb.y}
                          width={hb.width}
                          height={hb.height}
                          fill="#f59e0b"
                          fillOpacity={0.25}
                          stroke="#f59e0b"
                          strokeWidth={3}
                          rx={3}
                          style={{ pointerEvents: "none" }}
                        />
                      );
                    })()}
                  </svg>
                )}
              </div>
            ) : (
              <div className="empty-state">No screenshot in this artifact.</div>
            )}

            {mode === "training" && fileName ? (
              <div className="dd-action-panel">
                <div className="dd-action-panel-header">
                  <div>
                    <h3>Step Label</h3>
                    <p>Describe the screen, choose the action, and pick the state labels from the registry.</p>
                  </div>
                  <button
                    className="primary-btn"
                    type="button"
                    onClick={onSaveAnnotation}
                    disabled={annotationSaving}
                  >
                    {annotationSaving ? "Saving..." : "Save Step Label"}
                  </button>
                </div>

                <label className="dd-action-field dd-action-field-full">
                  <span className="dd-action-label">Next Step Instruction</span>
                  <textarea
                    className="form-input dd-action-textarea"
                    rows="2"
                    value={interactionEdits?.element_query ?? ""}
                    placeholder={elementQuery || "e.g. click the Sign In button, type the user's email address"}
                    onChange={(event) => updateInteractionEdit("element_query", event.target.value)}
                  />
                </label>

                <div className="dd-action-type-row">
                  <span className="dd-action-label">Action Type</span>
                  <div className="dd-action-type-buttons">
                    {actionList.map((action) => (
                      <button
                        key={action.action_id}
                        type="button"
                        className={`dd-action-type-btn${currentActionType === action.action_id ? " selected" : ""}`}
                        onClick={() => updateInteractionEdit("action_type", action.action_id)}
                      >
                        {action.label}
                      </button>
                    ))}
                    {onCreateAction ? (
                      addingAction ? (
                        <span className="dd-action-add">
                          <input
                            className="form-input dd-action-add-input"
                            autoFocus
                            value={newActionName}
                            placeholder="New action (e.g. Hover)"
                            onChange={(e) => setNewActionName(e.target.value)}
                            onKeyDown={async (e) => {
                              if (e.key === "Escape") { setAddingAction(false); setNewActionName(""); setActionError(null); }
                              if (e.key === "Enter") {
                                e.preventDefault();
                                try {
                                  const created = await onCreateAction(newActionName);
                                  if (created?.action_id) updateInteractionEdit("action_type", created.action_id);
                                  setAddingAction(false); setNewActionName(""); setActionError(null);
                                } catch (err) { setActionError(err.message || String(err)); }
                              }
                            }}
                          />
                          <button type="button" className="ghost-btn dd-mini-btn" onClick={() => { setAddingAction(false); setNewActionName(""); setActionError(null); }}>Cancel</button>
                        </span>
                      ) : (
                        <button type="button" className="dd-action-type-btn dd-action-add-btn" onClick={() => setAddingAction(true)}>+ Add</button>
                      )
                    ) : null}
                  </div>
                  {actionError ? <span className="dd-action-error">{actionError}</span> : null}
                </div>

                {actionHasPayload ? (
                  payloadMode === "slot" ? (
                    <label className="dd-action-field dd-action-field-full">
                      <span className="dd-action-label">Value slot</span>
                      <input
                        className="form-input"
                        list="dd-value-slots"
                        value={interactionEdits?.action_text ?? ""}
                        placeholder="variable bound at runtime — e.g. email, password, search_query"
                        onChange={(event) => updateInteractionEdit("action_text", event.target.value)}
                      />
                      <datalist id="dd-value-slots">
                        <option value="email" />
                        <option value="password" />
                        <option value="search_query" />
                        <option value="otp" />
                        <option value="phone" />
                      </datalist>
                      <span className="dd-state-helper">The workflow injects the real value at runtime — store the variable name, never the literal secret.</span>
                    </label>
                  ) : (
                    <label className="dd-action-field dd-action-field-full">
                      <span className="dd-action-label">Context <span className="dd-action-optional">(optional)</span></span>
                      <input
                        className="form-input"
                        value={interactionEdits?.action_text ?? ""}
                        placeholder="describe the target UI, e.g. “primary blue Log In button”"
                        onChange={(event) => updateInteractionEdit("action_text", event.target.value)}
                      />
                      <span className="dd-state-helper">Context about the element or intent — not what to do (the action type already says that).</span>
                    </label>
                  )
                ) : (
                  <div className="dd-action-nopayload">No payload needed for “{actionList.find((a) => a.action_id === currentActionType)?.label || currentActionType}”.</div>
                )}

                {/* Single stepped state picker — only the active step's drill-down is
                    rendered (no double-load). Picking the current state auto-advances to expected. */}
                {(() => {
                  const labelFor = (field) => {
                    const v = interactionEdits?.[field];
                    if (!v) return null;
                    return pageStateOptions.find((s) => s.page_state_id === v)?.display_name || v;
                  };
                  const steps = [
                    { field: "observed_page_state", n: 1, name: "Current", title: "Current Page State", helper: "What is visible before the action?" },
                    { field: "post_action_state", n: 2, name: "Expected next", title: "Expected Next State", helper: "Where should the agent land after the action?" },
                  ];
                  const active = steps.find((s) => s.field === activeStateField) || steps[0];
                  return (
                    <div className="dd-state-stepper">
                      <div className="dd-state-steps">
                        {steps.map((s, i) => {
                          const val = labelFor(s.field);
                          return (
                            <span key={s.field} className="dd-state-step-wrap">
                              {i > 0 ? <span className="dd-state-step-arrow">→</span> : null}
                              <button
                                type="button"
                                className={`dd-state-step${activeStateField === s.field ? " active" : ""}${val ? " filled" : ""}`}
                                onClick={() => setActiveStateField(s.field)}
                              >
                                <span className="dd-state-step-num">{s.n}</span>
                                <span className="dd-state-step-label">{s.name}</span>
                                <span className="dd-state-step-val">{val || "choose…"}</span>
                              </button>
                            </span>
                          );
                        })}
                      </div>
                      {renderPageStatePicker(active.field, active.title, active.helper)}
                    </div>
                  );
                })()}
                {pageStateError ? <div className="annotation-message error">{pageStateError}</div> : null}
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
                          const role = candidate.target?.role;
                          // Two same-label elements (e.g. both "input") are only told apart
                          // by position — show where each sits so the annotator can pick right.
                          const cb = resolveBbox(candidate, acquisition);
                          const pos = cb ? `@${Math.round(cb.x)},${Math.round(cb.y)} · ${Math.round(cb.width)}×${Math.round(cb.height)}` : "no box";
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
                              onMouseEnter={() => setHoveredCandidateId(candidate.candidate_id)}
                              onMouseLeave={() => setHoveredCandidateId(null)}
                            >
                              <span className="dd-link-rank">#{candidate.rank}</span>
                              <span className="dd-link-label">
                                {label}{role ? ` · ${role}` : ""}
                                <span className="dd-link-pos mono">{pos}</span>
                              </span>
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
                              onMouseEnter={() => setHoveredCandidateId(vision.candidate_id)}
                              onMouseLeave={() => setHoveredCandidateId(null)}
                            >
                              <span className="dd-link-rank dd-vision-badge">V</span>
                              <span className="dd-link-label">
                                {primary}
                                <span className="dd-link-pos mono">
                                  {vision.bbox ? `@${Math.round(vision.bbox.x)},${Math.round(vision.bbox.y)} · ${Math.round(vision.bbox.width)}×${Math.round(vision.bbox.height)}` : ""}
                                </span>
                              </span>
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
