import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import { ApiUsageSection } from "./components/controlplane/ApiUsageSection";
import { ChatSection } from "./components/controlplane/ChatSection";
import { CONTROL_PLANE_NAV, DEFAULT_SECTION_VIEW } from "./components/controlplane/navigation";
import { LabSection } from "./components/controlplane/LabSection";
import { DomainsSection } from "./components/controlplane/DomainsSection";
import { HomeSection } from "./components/controlplane/HomeSection";
import { ModelsSection } from "./components/controlplane/ModelsSection";
import { SystemSection } from "./components/controlplane/SystemSection";
import { TrainingSection } from "./components/controlplane/TrainingSection";
import { PageStatesSection } from "./components/controlplane/PageStatesSection";
import { CoverageSection } from "./components/controlplane/CoverageSection";
import { ScorecardSection } from "./components/controlplane/ScorecardSection";
import { TrainingSpaceSection } from "./components/controlplane/TrainingSpaceSection";
import { StateGraphSection } from "./components/controlplane/StateGraphSection";
import { candidateLabelsFromAnnotation, positiveCandidateIdFromLabels, resolveBbox } from "./components/controlplane/utils";
import { WorkersSection } from "./components/controlplane/WorkersSection";

const API = import.meta.env.VITE_API_BASE_URL;

// The OmniParser/vision proposer is the CATCHALL, parked in the back for now.
// In the locked AX + Haiku architecture, CDP-AX is the primary candidate source
// and vision is only a fallback for AX gaps (canvas/icon-only) — and the heavier
// vision-native model is the catchall's catchall. Until the AX-gap + training
// path is wired, do NOT auto-run vision on every capture-open (it silently burns
// compute). The endpoints + module stay available; this just unwires the
// active path. Flip true (or drive from a per-session mode) to re-enable.
const VISION_CATCHALL_ENABLED = false;

const EMPTY_INTERACTION_EDITS = {
  element_query: "",
  action_type: "any",
  action_text: "",
  observed_page_state: "",
  post_action_state: "",
};

const mockWorkers = [
  { id: "worker-01", name: "Seat-01", domain: "Marketplace", status: "Busy", seat: "VM-01" },
  { id: "worker-02", name: "Seat-02", domain: "Jobs", status: "Idle", seat: "VM-02" },
  { id: "worker-03", name: "Seat-03", domain: "Finance", status: "Blocked", seat: "VM-03" },
];

export default function App() {
  const [sidebarLevel, setSidebarLevel] = useState("primary");
  const [activePrimaryView, setActivePrimaryView] = useState("home");
  const [activeSecondaryViewByPrimary, setActiveSecondaryViewByPrimary] = useState(DEFAULT_SECTION_VIEW);

  const [health, setHealth] = useState({ loading: true, ok: false, error: null });
  const [systemStatus, setSystemStatus] = useState({ loading: false, data: null, error: null });
  const [usage, setUsage] = useState({ loading: false, data: null, error: null });
  const [runs, setRuns] = useState({ loading: true, data: [], error: null });
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [runSearch, setRunSearch] = useState("");

  const [trainingRegistry, setTrainingRegistry] = useState({ domains: [], goals: [], tasks: [], scenarios: [] });
  const [registryStatus, setRegistryStatus] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [selectedTrainingSessionId, setSelectedTrainingSessionId] = useState(null);
  const [creatingSession, setCreatingSession] = useState(false);
  const [sessionActionLoading, setSessionActionLoading] = useState(false);
  const [sessionForm, setSessionForm] = useState({
    domain_id: "",
    scenario_id: "",
    notes: "",
    purpose: "data_collection",
  });
  const [sessionFormError, setSessionFormError] = useState(null);

  const [observations, setObservations] = useState({ loading: false, data: [], error: null });
  const [selectedObsFilename, setSelectedObsFilename] = useState(null);
  const [selectedObs, setSelectedObs] = useState(null);
  const [labels, setLabels] = useState({});
  const [bboxOverride, setBboxOverride] = useState(null);
  // Annotator-created candidates for the current observation — surfaced into the
  // Candidates tab and the link picker after a draw. Persisted via the same PATCH.
  const [manualCandidates, setManualCandidates] = useState([]);
  const [interactionEdits, setInteractionEdits] = useState(EMPTY_INTERACTION_EDITS);
  const [annotationSaving, setAnnotationSaving] = useState(false);
  const [annotationMessage, setAnnotationMessage] = useState(null);
  const [datasetStatus, setDatasetStatus] = useState(null);
  const [trainingStatus, setTrainingStatus] = useState(null);
  const [targetComparisonStatus, setTargetComparisonStatus] = useState(null);

  const [tabs, setTabs] = useState([]);
  const [tabsLoading, setTabsLoading] = useState(false);
  const [tabsWarning, setTabsWarning] = useState(null);
  const [selectedTabId, setSelectedTabId] = useState(null);
  const [captureInProgress, setCaptureInProgress] = useState(false);
  const [captureError, setCaptureError] = useState(null);
  const [capturePhase, setCapturePhase] = useState(0);
  const [captureElapsed, setCaptureElapsed] = useState(0);
  const [captureSuccess, setCaptureSuccess] = useState(null);
  const [justCapturedFilename, setJustCapturedFilename] = useState(null);

  const apiLabel = useMemo(() => API ?? "(missing VITE_API_BASE_URL)", []);
  const currentNav = CONTROL_PLANE_NAV[activePrimaryView];
  const activeSectionId = activeSecondaryViewByPrimary[activePrimaryView] ?? currentNav.sections[0]?.id;
  const activeSection = currentNav.sections.find((section) => section.id === activeSectionId) ?? currentNav.sections[0];
  const canEnterSecondary = ["training", "system", "lab"].includes(activePrimaryView);
  const selectedTrainingSession = useMemo(
    () => sessions.find((session) => session.id === selectedTrainingSessionId) ?? null,
    [sessions, selectedTrainingSessionId],
  );
  const selectedObservationAnnotation = selectedObs?.meta?.training_annotation ?? null;
  const selectedObservationDomainId = selectedObservationAnnotation?.domain_id
    ?? selectedObs?.acquisition?.training_metadata?.domain_id
    ?? selectedObs?.metadata?.domain_id
    ?? selectedTrainingSession?.domain_id
    ?? null;
  const selectedObservationScenarioId = selectedObservationAnnotation?.scenario_id
    ?? selectedTrainingSession?.scenario_id
    ?? null;
  const selectedObservationGoalId = selectedObservationAnnotation?.goal_id
    ?? selectedTrainingSession?.goal_id
    ?? null;

  // Page states relevant to the selected capture (global + its domain + its objective/goal
  // + its scenario), fetched from the PageStateRegistry. Each carries scope + category so
  // the picker can group them.
  const [pageStateOptions, setPageStateOptions] = useState([]);
  const loadPageStateOptions = useCallback(async () => {
    try {
      // Fetch ALL states (no domain filter): the folder-nav picker needs every
      // domain so you can navigate out of your home domain into the others. The
      // picker centers on the capture's domain/objective itself via annotation.
      const r = await fetch(`${API}/api/training/page-states`);
      if (!r.ok) throw new Error();
      const rows = await r.json();
      // Normalize to what the picker expects (keep page_state_id alias for back-compat).
      // domain_id/goal_id/scenario_id are REQUIRED for folder classification.
      setPageStateOptions(rows.map((s) => ({
        page_state_id: s.state_id,
        state_id: s.state_id,
        display_name: s.display_name || s.state_id,
        scope: s.scope,
        domain_id: s.domain_id,
        goal_id: s.goal_id,
        scenario_id: s.scenario_id,
        category: s.category || "general",
        stage: s.stage,
      })));
    } catch {
      setPageStateOptions([]);
    }
  }, []);

  useEffect(() => { loadPageStateOptions(); }, [loadPageStateOptions]);

  const setActiveSection = useCallback((sectionId) => {
    setActiveSecondaryViewByPrimary((current) => ({ ...current, [activePrimaryView]: sectionId }));
  }, [activePrimaryView]);

  const openPrimaryView = useCallback((view) => {
    setActivePrimaryView(view);
    if (view === "training" || view === "system" || view === "lab") {
      setSidebarLevel("secondary");
      return;
    }
    setSidebarLevel("primary");
  }, []);

  const goHome = useCallback(() => {
    setActivePrimaryView("home");
    setSidebarLevel("primary");
  }, []);

  const returnToPrimaryRail = useCallback(() => {
    setSidebarLevel("primary");
  }, []);

  const refresh = useCallback(async () => {
    setHealth((current) => ({ ...current, loading: true, error: null }));
    try {
      const response = await fetch(`${API}/health`);
      if (!response.ok) throw new Error(`Health failed: ${response.status}`);
      const payload = await response.json();
      setHealth({ loading: false, ok: !!payload.ok, error: null });
    } catch (error) {
      setHealth({ loading: false, ok: false, error: error.message });
    }

    setRuns((current) => ({ ...current, loading: true, error: null }));
    try {
      const response = await fetch(`${API}/api/runs`);
      if (!response.ok) throw new Error(`Runs failed: ${response.status}`);
      const payload = await response.json();
      const safeRuns = Array.isArray(payload) ? payload : [];
      setRuns({ loading: false, data: safeRuns, error: null });
      if (safeRuns.length > 0 && !selectedRunId) setSelectedRunId(safeRuns[0].id);
    } catch (error) {
      setRuns({ loading: false, data: [], error: error.message });
    }
  }, [selectedRunId]);

  const loadSystemStatus = useCallback(async () => {
    setSystemStatus((current) => ({ ...current, loading: true, error: null }));
    try {
      const response = await fetch(`${API}/api/system/status`);
      if (!response.ok) throw new Error(`System status failed: ${response.status}`);
      const payload = await response.json();
      setSystemStatus({ loading: false, data: payload, error: null });
      return payload;
    } catch (error) {
      setSystemStatus((current) => ({ ...current, loading: false, error: error.message }));
      return null;
    }
  }, []);

  const loadUsage = useCallback(async () => {
    setUsage((current) => ({ ...current, loading: true, error: null }));
    try {
      const response = await fetch(`${API}/api/usage/anthropic`);
      if (!response.ok) throw new Error(`Usage failed: ${response.status}`);
      const payload = await response.json();
      setUsage({ loading: false, data: payload, error: null });
    } catch (error) {
      setUsage({ loading: false, data: null, error: error.message });
    }
  }, []);

  const loadTrainingRegistry = useCallback(async () => {
    try {
      const [domainsResponse, goalsResponse, tasksResponse, scenariosResponse] = await Promise.all([
        fetch(`${API}/api/training/domains`),
        fetch(`${API}/api/training/goals`),
        fetch(`${API}/api/training/tasks`),
        fetch(`${API}/api/training/scenarios`),
      ]);
      const [domains, goals, tasks, scenarios] = await Promise.all([
        domainsResponse.json(),
        goalsResponse.json(),
        tasksResponse.json(),
        scenariosResponse.json(),
      ]);
      setTrainingRegistry({
        domains: Array.isArray(domains) ? domains : [],
        goals: Array.isArray(goals) ? goals : [],
        tasks: Array.isArray(tasks) ? tasks : [],
        scenarios: Array.isArray(scenarios) ? scenarios : [],
      });
    } catch {
      setTrainingRegistry({ domains: [], goals: [], tasks: [], scenarios: [] });
    }
  }, []);

  const saveRegistryItem = useCallback(async (resource, payload, id = null) => {
    setRegistryStatus({ loading: true, message: null, error: null });
    const target = id ? `${API}/api/training/${resource}/${encodeURIComponent(id)}` : `${API}/api/training/${resource}`;
    try {
      const response = await fetch(target, {
        method: id ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `Save failed: ${response.status}`);
      await loadTrainingRegistry();
      setRegistryStatus({ loading: false, message: "Registry saved.", error: null });
      return result;
    } catch (error) {
      setRegistryStatus({ loading: false, message: null, error: error.message });
      return null;
    }
  }, [loadTrainingRegistry]);

  const archiveRegistryItem = useCallback(async (resource, id) => {
    setRegistryStatus({ loading: true, message: null, error: null });
    try {
      const response = await fetch(`${API}/api/training/${resource}/${encodeURIComponent(id)}`, { method: "DELETE" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `Archive failed: ${response.status}`);
      await loadTrainingRegistry();
      setRegistryStatus({ loading: false, message: "Registry item archived.", error: null });
      return result;
    } catch (error) {
      setRegistryStatus({ loading: false, message: null, error: error.message });
      return null;
    }
  }, [loadTrainingRegistry]);

  // Inline-create a page state from the labeler. Defaults to domain scope when the
  // capture has a domain (most states are domain-specific); falls back to global.
  // category is chosen in the picker. Writes to the PageStateRegistry and refreshes options.
  const createPageStateFromLabeler = useCallback(async (displayName, opts = {}) => {
    const cleanName = String(displayName || "").trim();
    if (!cleanName) throw new Error("Enter a page state name first.");
    // Default scope: the objective (goal) if we know it — Login-specific states belong
    // to the Login objective — else fall back to domain, then global.
    const scope = opts.scope || (
      selectedObservationGoalId ? "goal" : selectedObservationDomainId ? "domain" : "global"
    );
    const body = {
      display_name: cleanName,
      scope,
      category: opts.category || "general",
      domain_id: scope === "global" ? null : selectedObservationDomainId,
      goal_id: (scope === "goal" || scope === "scenario") ? selectedObservationGoalId : null,
      scenario_id: scope === "scenario" ? selectedObservationScenarioId : null,
    };
    const r = await fetch(`${API}/api/training/page-states`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const payload = await r.json();
    if (!r.ok) throw new Error(payload.detail || "Failed to create state");
    await loadPageStateOptions();
    return { page_state_id: payload.state_id, state_id: payload.state_id, display_name: payload.display_name };
  }, [selectedObservationDomainId, selectedObservationGoalId, selectedObservationScenarioId, loadPageStateOptions]);

  const loadTrainingSessions = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/training/sessions`);
      if (!response.ok) throw new Error(`Sessions failed: ${response.status}`);
      const payload = await response.json();
      const safeSessions = Array.isArray(payload) ? payload : [];
      setSessions(safeSessions);
      if (safeSessions.length > 0) {
        setSelectedTrainingSessionId((current) => current ?? safeSessions[0].id);
      }
    } catch {
      setSessions([]);
    }
  }, []);

  const loadTabs = useCallback(async () => {
    if (!selectedTrainingSessionId) {
      setTabs([]);
      setTabsWarning("Select a training session first");
      return;
    }
    setTabsLoading(true);
    setTabsWarning(null);
    try {
      const response = await fetch(`${API}/api/training/sessions/${selectedTrainingSessionId}/tabs`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Tabs failed: ${response.status}`);
      const list = Array.isArray(payload) ? payload : [];
      setTabs(list);
      if (list.length > 0) setSelectedTabId((current) => current ?? list[0].id);
    } catch (error) {
      setTabs([]);
      setTabsWarning(error.message);
    } finally {
      setTabsLoading(false);
    }
  }, [selectedTrainingSessionId]);

  const loadObservations = useCallback(async () => {
    setObservations((current) => ({ ...current, loading: true, error: null }));
    try {
      const response = await fetch(`${API}/api/observations`);
      if (!response.ok) throw new Error(`Observations failed: ${response.status}`);
      const payload = await response.json();
      setObservations({ loading: false, data: Array.isArray(payload) ? payload : [], error: null });
    } catch (error) {
      setObservations({ loading: false, data: [], error: error.message });
    }
  }, []);

  // Map of state_id -> {display_name, category} for the WHOLE registry, so the
  // Dataset Browser can label state-groups and titles ("login_wall" -> "Login Wall",
  // category "Login") regardless of which capture's domain is selected.
  const [stateMeta, setStateMeta] = useState({});
  const loadStateMeta = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/training/page-states`);
      if (!r.ok) return;
      const rows = await r.json();
      const map = {};
      for (const s of rows) map[s.state_id] = { display_name: s.display_name, category: s.category, stage: s.stage, goal_id: s.goal_id };
      setStateMeta(map);
    } catch { /* best-effort */ }
  }, []);

  // Domain + objective(goal) lookup maps for the dataset browser hierarchy
  // (Domain ▸ Stage ▸ Objective ▸ …). Derived from the already-loaded registry — no fetch.
  const domainMeta = useMemo(() => {
    const m = {};
    for (const d of trainingRegistry.domains) m[d.domain_id] = { display_name: d.display_name || d.domain_id };
    return m;
  }, [trainingRegistry.domains]);
  const goalMeta = useMemo(() => {
    const m = {};
    for (const g of trainingRegistry.goals) m[g.goal_id] = { display_name: g.display_name || g.goal_id, stage: g.stage || "neutral" };
    return m;
  }, [trainingRegistry.goals]);

  // Action vocabulary (registry-driven, user-extensible). Replaces the hardcoded list.
  const [actionOptions, setActionOptions] = useState([]);
  const loadActions = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/training/actions`);
      if (!r.ok) return;
      setActionOptions(await r.json());
    } catch { /* best-effort */ }
  }, []);
  const createAction = useCallback(async (label) => {
    const clean = String(label || "").trim();
    if (!clean) throw new Error("Enter an action name.");
    const r = await fetch(`${API}/api/training/actions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: clean }),
    });
    const payload = await r.json();
    if (!r.ok) throw new Error(payload.detail || "Failed to create action");
    await loadActions();
    return payload;
  }, [loadActions]);

  const deleteObservation = useCallback(async (filename) => {
    try {
      const response = await fetch(`${API}/api/observations/${encodeURIComponent(filename)}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`Delete failed: ${response.status}`);
      if (selectedObsFilename === filename) {
        setSelectedObsFilename(null);
        setSelectedObs(null);
      }
      await loadObservations();
    } catch (error) {
      setObservations((current) => ({ ...current, error: error.message }));
    }
  }, [loadObservations, selectedObsFilename]);

  const bulkDeleteObservations = useCallback(async (filenames) => {
    if (!filenames.length) return;
    try {
      const response = await fetch(`${API}/api/observations/bulk-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filenames }),
      });
      if (!response.ok) throw new Error(`Bulk delete failed: ${response.status}`);
      if (filenames.includes(selectedObsFilename)) {
        setSelectedObsFilename(null);
        setSelectedObs(null);
      }
      await loadObservations();
    } catch (error) {
      setObservations((current) => ({ ...current, error: error.message }));
    }
  }, [loadObservations, selectedObsFilename]);

  const updateObsMeta = useCallback(async (filename, patch) => {
    try {
      await fetch(`${API}/api/observations/${encodeURIComponent(filename)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      await loadObservations();
    } catch {
      // best-effort
    }
  }, [loadObservations]);

  const createRun = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/runs`, { method: "POST" });
      if (!response.ok) throw new Error(`Create run failed: ${response.status}`);
      await refresh();
    } catch {
      // best-effort
    }
  }, [refresh]);

  const loadObservation = useCallback(async (filename) => {
    setSelectedObsFilename(filename);
    setSelectedObs(null);
    setLabels({});
    setBboxOverride(null);
    setManualCandidates([]);
    setInteractionEdits(EMPTY_INTERACTION_EDITS);
    setAnnotationMessage(null);
    try {
      const response = await fetch(`${API}/api/observations/${encodeURIComponent(filename)}`);
      if (!response.ok) throw new Error(`Failed: ${response.status}`);
      const payload = await response.json();
      const annotation = payload?.meta?.training_annotation;
      const restoredLabels = candidateLabelsFromAnnotation(annotation);
      setLabels(restoredLabels);
      const restoredPositive = annotation?.positive_candidate_id;
      const positiveCandidate = (payload?.ranked_candidates ?? []).find((candidate) => candidate.candidate_id === restoredPositive);
      setBboxOverride(annotation?.approved_bbox ?? (positiveCandidate ? resolveBbox(positiveCandidate, payload?.acquisition) : null));
      setManualCandidates(Array.isArray(annotation?.manual_candidates) ? annotation.manual_candidates : []);
      setInteractionEdits({
        element_query: annotation?.element_query ?? payload?.acquisition?.training_metadata?.element_query ?? "",
        action_type: annotation?.action_type_hint ?? "any",
        action_text: annotation?.action_text ?? "",
        observed_page_state: annotation?.observed_page_state ?? "",
        post_action_state: annotation?.post_action_state ?? "",
      });
      setSelectedObs(payload);
    } catch (error) {
      setSelectedObs({ _error: error.message });
    }
  }, []);

  // Surgical re-fetch that updates ONLY the vision candidates (and observer/elements
  // if the artifact changed) without touching labels/bbox/interactionEdits. Used to
  // pick up async-backfilled vision proposals that land seconds after capture, so the
  // annotator doesn't have to full-page-refresh and lose in-progress edits.
  const refreshVisionCandidates = useCallback(async (filename) => {
    if (!filename) return 0;
    try {
      const response = await fetch(`${API}/api/observations/${encodeURIComponent(filename)}`);
      if (!response.ok) return 0;
      const payload = await response.json();
      const vc = Array.isArray(payload?.vision_candidates) ? payload.vision_candidates : [];
      setSelectedObs((current) => {
        // Only patch if it's still the same observation on screen.
        if (!current || current._error) return current;
        // CRITICAL: bail out with the SAME object reference when the candidate count
        // hasn't changed. Returning a new object every poll tick re-renders the whole
        // detail view and makes the screenshot overlay flicker/disappear. React skips
        // the re-render entirely when we return the identical reference.
        const prevCount = current.vision_candidates?.length ?? 0;
        if (vc.length === prevCount) return current;
        return {
          ...current,
          vision_candidates: vc,
          vision_candidates_meta: payload?.vision_candidates_meta ?? current.vision_candidates_meta,
        };
      });
      return vc.length;
    } catch {
      return 0;
    }
  }, []);

  // Tracks filenames we've already auto-triggered detect-only generation for this
  // session, so the lazy-on-open effect fires exactly once per capture.
  const visionRequestedRef = useRef(new Set());

  // Force-refresh vision candidates even when the count is unchanged (captions add
  // text to existing boxes — the surgical refresh above bails on equal counts).
  const refreshVisionForce = useCallback(async (filename) => {
    if (!filename) return;
    try {
      const r = await fetch(`${API}/api/observations/${encodeURIComponent(filename)}`);
      if (!r.ok) return;
      const payload = await r.json();
      setSelectedObs((cur) => (!cur || cur._error) ? cur : {
        ...cur,
        vision_candidates: Array.isArray(payload?.vision_candidates) ? payload.vision_candidates : [],
        vision_candidates_meta: payload?.vision_candidates_meta ?? null,
      });
    } catch { /* best-effort */ }
  }, []);

  // On-demand captioning: run Florence-2 for this capture (the slow/heavy step) only
  // when the annotator explicitly asks. Surgical refresh keeps in-progress labels.
  const [captionsLoading, setCaptionsLoading] = useState(false);
  const generateVisionCaptions = useCallback(async (filename) => {
    if (!filename) return;
    setCaptionsLoading(true);
    try {
      await fetch(`${API}/api/observations/${encodeURIComponent(filename)}/vision?captions=true`, { method: "POST" });
      await refreshVisionForce(filename);
    } finally {
      setCaptionsLoading(false);
    }
  }, [refreshVisionForce]);

  const clearSelectedObservation = useCallback(() => {
    setSelectedObs(null);
    setSelectedObsFilename(null);
    setLabels({});
    setBboxOverride(null);
    setManualCandidates([]);
    setInteractionEdits(EMPTY_INTERACTION_EDITS);
    setAnnotationMessage(null);
  }, []);

  const saveTrainingAnnotation = useCallback(async () => {
    if (!selectedObsFilename) return;
    const positiveCandidateId = positiveCandidateIdFromLabels(labels);
    const rejectedCandidateIds = Object.entries(labels)
      .filter(([, value]) => value === "reject")
      .map(([candidateId]) => candidateId);

    setAnnotationSaving(true);
    setAnnotationMessage(null);
    try {
      const response = await fetch(`${API}/api/observations/${encodeURIComponent(selectedObsFilename)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          training_annotation: {
            candidate_labels: labels,
            positive_candidate_id: positiveCandidateId,
            rejected_candidate_ids: rejectedCandidateIds,
            // A manually drawn bbox can stand alone — no positive candidate required.
            approved_bbox: bboxOverride,
            manual_candidates: manualCandidates,
            element_query: interactionEdits.element_query,
            action_type_hint: interactionEdits.action_type,
            action_text: interactionEdits.action_text,
            observed_page_state: interactionEdits.observed_page_state,
            post_action_state: interactionEdits.post_action_state,
          },
          // Interaction-layer overrides go alongside training_annotation in the same PATCH.
          // Empty string clears the override back to scenario-inherited default (server stores NULL).
          element_query: interactionEdits.element_query,
          action_type: interactionEdits.action_type,
          action_text: interactionEdits.action_text,
          // Page-state labels — feed the future page_state_classifier and state_transition models.
          // Free text now; taxonomy formalizes later from observed patterns.
          observed_page_state: interactionEdits.observed_page_state,
          post_action_state: interactionEdits.post_action_state,
        }),
      });
      if (!response.ok) throw new Error(`Failed to save review: ${response.status}`);
      const result = await response.json().catch(() => null);
      const savedStatus = result?.training_annotation?.review_status;
      await loadObservation(selectedObsFilename);
      await loadObservations();
      // Tell the annotator the OUTCOME, not just "saved". A capture only counts as
      // reviewed once it has a bbox or a page-state label — otherwise it silently
      // stays draft and would be excluded from dataset builds. Make that explicit.
      if (savedStatus === "reviewed" || savedStatus === "approved") {
        setAnnotationMessage({ type: "success", text: "Saved — marked Reviewed ✓" });
      } else {
        setAnnotationMessage({
          type: "warning",
          text: "Saved as Draft. Add a bounding box OR pick a Current Page State to mark it Reviewed (Draft captures are excluded from dataset export).",
        });
      }
    } catch (error) {
      setAnnotationMessage({ type: "error", text: error.message });
    } finally {
      setAnnotationSaving(false);
    }
  }, [bboxOverride, labels, manualCandidates, interactionEdits, loadObservation, loadObservations, selectedObsFilename]);

  const buildTrainingDataset = useCallback(async () => {
    setDatasetStatus({ loading: true });
    try {
      const response = await fetch(`${API}/api/training/build-dataset`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ? JSON.stringify(payload.detail) : `Build failed: ${response.status}`);
      setDatasetStatus({ loading: false, result: payload, error: null });
      return payload;
    } catch (error) {
      setDatasetStatus({ loading: false, result: null, error: error.message });
      return null;
    }
  }, []);

  const loadTrainingTargetComparison = useCallback(async () => {
    setTargetComparisonStatus({ loading: true });
    try {
      const response = await fetch(`${API}/api/training/target-comparison`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ? JSON.stringify(payload.detail) : `Comparison failed: ${response.status}`);
      setTargetComparisonStatus({ loading: false, result: payload, error: null });
      return payload;
    } catch (error) {
      setTargetComparisonStatus({ loading: false, result: null, error: error.message });
      return null;
    }
  }, []);

  const trainGroundingModel = useCallback(async () => {
    setTrainingStatus({ loading: true });
    try {
      const response = await fetch(`${API}/api/training/train`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rebuild_dataset: true }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ? JSON.stringify(payload.detail) : `Training failed: ${response.status}`);
      setTrainingStatus({ loading: false, result: payload, error: null });
      return payload;
    } catch (error) {
      setTrainingStatus({ loading: false, result: null, error: error.message });
      return null;
    }
  }, []);

  const createTrainingSession = useCallback(async () => {
    setCreatingSession(true);
    setSessionFormError(null);
    try {
      const response = await fetch(`${API}/api/training/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain_id: sessionForm.domain_id,
          scenario_id: sessionForm.scenario_id,
          notes: sessionForm.notes || null,
          purpose: sessionForm.purpose || "data_collection",
        }),
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        // empty / non-JSON response (e.g. 5xx with no body)
      }
      if (!response.ok) {
        const detail = payload?.detail
          ? typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail)
          : `Create failed: HTTP ${response.status}`;
        throw new Error(detail);
      }
      await loadTrainingSessions();
      setSelectedTrainingSessionId(payload.id);
      setActivePrimaryView("training");
      setSidebarLevel("secondary");
      setActiveSecondaryViewByPrimary((current) => ({ ...current, training: "session-capture" }));
    } catch (error) {
      setSessionFormError(error.message || String(error));
    } finally {
      setCreatingSession(false);
    }
  }, [loadTrainingSessions, sessionForm]);

  const startTrainingSession = useCallback(async () => {
    if (!selectedTrainingSessionId) return;
    setSessionActionLoading(true);
    try {
      const response = await fetch(`${API}/api/training/sessions/${selectedTrainingSessionId}/start`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Start failed: ${response.status}`);
      await loadTrainingSessions();
      setSelectedTrainingSessionId(payload.id);
      await loadTabs();
    } catch (error) {
      setTabsWarning(error.message);
    } finally {
      setSessionActionLoading(false);
    }
  }, [loadTabs, loadTrainingSessions, selectedTrainingSessionId]);

  const stopTrainingSession = useCallback(async () => {
    if (!selectedTrainingSessionId) return;
    setSessionActionLoading(true);
    try {
      const response = await fetch(`${API}/api/training/sessions/${selectedTrainingSessionId}/stop`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Stop failed: ${response.status}`);
      await loadTrainingSessions();
      setTabs([]);
      setSelectedTabId(null);
    } catch (error) {
      setTabsWarning(error.message);
    } finally {
      setSessionActionLoading(false);
    }
  }, [loadTrainingSessions, selectedTrainingSessionId]);

  const deleteTrainingSession = useCallback(async (sessionId) => {
    if (!sessionId) return;
    const confirmed = window.confirm(
      `Delete session ${sessionId} and all its captured artifacts?\n\nThis stops Chrome, removes the session row, and deletes every artifact JSON, screenshot, and sidecar tied to it. Registry stays intact. Cannot be undone.`,
    );
    if (!confirmed) return;
    try {
      const response = await fetch(`${API}/api/training/sessions/${sessionId}`, { method: "DELETE" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Delete failed: ${response.status}`);
      // Clear selection if we just deleted what was selected
      if (selectedTrainingSessionId === sessionId) {
        setSelectedTrainingSessionId(null);
        setTabs([]);
        setSelectedTabId(null);
        clearSelectedObservation();
      }
      await loadTrainingSessions();
      await loadObservations();
    } catch (error) {
      setTabsWarning(error.message);
    }
  }, [clearSelectedObservation, loadObservations, loadTrainingSessions, selectedTrainingSessionId]);

  const resetAllTrainingData = useCallback(async () => {
    const confirmed = window.confirm(
      "Reset ALL training data?\n\n" +
      "This will:\n" +
      "  • Stop any active training Chrome processes\n" +
      "  • Delete every training session\n" +
      "  • Delete every capture (artifact JSONs, screenshots, .meta.json, .vision.json)\n\n" +
      "This will NOT delete:\n" +
      "  • Domains, goals, tasks, scenarios (your registry stays)\n" +
      "  • Chrome profile directories on disk\n\n" +
      "Cannot be undone. Continue?",
    );
    if (!confirmed) return;
    // Second confirmation because this is genuinely destructive
    const reallyConfirmed = window.confirm("Really? Type-deleting-Marketplace-progress level destructive. Last chance.");
    if (!reallyConfirmed) return;
    try {
      const response = await fetch(`${API}/api/training/reset`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Reset failed: ${response.status}`);
      setSelectedTrainingSessionId(null);
      setTabs([]);
      setSelectedTabId(null);
      clearSelectedObservation();
      await loadTrainingSessions();
      await loadObservations();
      window.alert(
        `Reset complete.\n\n` +
        `Sessions deleted: ${payload.deleted_sessions}\n` +
        `Captures deleted: ${payload.deleted_captures}\n` +
        `Files deleted: ${payload.deleted_files}\n` +
        `Orphans swept: ${payload.swept_orphans}`,
      );
    } catch (error) {
      window.alert(`Reset failed: ${error.message}`);
    }
  }, [clearSelectedObservation, loadObservations, loadTrainingSessions]);

  const openTrainingObservation = useCallback(async (filename) => {
    setActivePrimaryView("training");
    setSidebarLevel("secondary");
    setActiveSecondaryViewByPrimary((current) => ({ ...current, training: "review-label" }));
    await loadObservation(filename);
  }, [loadObservation]);

  const openWorkerObservation = useCallback(async (filename) => {
    setActivePrimaryView("workers");
    setSidebarLevel("secondary");
    setActiveSecondaryViewByPrimary((current) => ({ ...current, workers: "worker-observations" }));
    await loadObservation(filename);
  }, [loadObservation]);

  const openSystemView = useCallback(() => {
    setActivePrimaryView("system");
    setSidebarLevel("secondary");
  }, []);

  const triggerCapture = useCallback(async () => {
    if (!selectedTrainingSessionId || !selectedTabId || !selectedTrainingSession) return null;
    setCaptureInProgress(true);
    setCaptureError(null);
    setCaptureSuccess(null);
    setCapturePhase(0);
    setCaptureElapsed(0);

    const phaseTimer = setInterval(() => setCapturePhase((phase) => Math.min(phase + 1, 3)), 2000);
    const elapsedTimer = setInterval(() => setCaptureElapsed((seconds) => seconds + 1), 1000);

    try {
      const selectedTab = tabs.find((tab) => tab.id === selectedTabId);
      const response = await fetch(`${API}/api/capture`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          training_session_id: selectedTrainingSessionId,
          tab_id: selectedTabId,
          tab_url: selectedTab?.url,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Capture failed: ${response.status}`);
      await loadObservations();
      if (payload?.filename) {
        await loadObservation(payload.filename);
        setCaptureSuccess({ filename: payload.filename, candidate_count: payload.candidate_count });
        setJustCapturedFilename(payload.filename);
        setActivePrimaryView("training");
        setSidebarLevel("secondary");
        setActiveSecondaryViewByPrimary((current) => ({ ...current, training: "review-label" }));
        setTimeout(() => setCaptureSuccess(null), 6000);
        setTimeout(() => setJustCapturedFilename(null), 8000);
      }
      return payload;
    } catch (error) {
      setCaptureError(error.message);
      return null;
    } finally {
      clearInterval(phaseTimer);
      clearInterval(elapsedTimer);
      setCaptureInProgress(false);
      setCapturePhase(0);
      setCaptureElapsed(0);
    }
  }, [loadObservation, loadObservations, selectedTabId, selectedTrainingSession, selectedTrainingSessionId, tabs]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (activePrimaryView === "system" && activeSectionId === "api-usage") {
      loadUsage();
      return undefined;
    }
    if (activePrimaryView === "system") {
      loadSystemStatus();
      const pollTimer = setInterval(() => loadSystemStatus(), 15000);
      return () => clearInterval(pollTimer);
    }
    return undefined;
  }, [activePrimaryView, activeSectionId, loadSystemStatus, loadUsage]);

  useEffect(() => {
    if (activePrimaryView === "training" || activePrimaryView === "domains") {
      loadTrainingRegistry();
    }
    if (activePrimaryView === "training") {
      loadTrainingSessions();
      loadObservations();
      loadTrainingTargetComparison();
      loadStateMeta();
      loadActions();
    }
    if (activePrimaryView === "workers") {
      loadObservations();
    }
  }, [activePrimaryView, loadObservations, loadTrainingRegistry, loadTrainingSessions, loadTrainingTargetComparison, loadStateMeta, loadActions]);

  useEffect(() => {
    if (activePrimaryView === "training" && activeSectionId === "session-capture" && selectedTrainingSession?.status === "active") {
      loadTabs();
    }
  }, [activePrimaryView, activeSectionId, loadTabs, selectedTrainingSession]);

  // Lazy-on-open gate: the proposer NO LONGER runs after every capture. Instead, when
  // a capture is opened in the labeler and has a screenshot but no vision sidecar yet,
  // generate detect-only candidates (fast, ~150ms-1s) right here — so only captures a
  // person actually reviews ever cost compute. Captions are separate/on-demand. Fires
  // once per filename (ref guard). Depends on PRIMITIVES so it isn't torn down on every
  // unrelated re-render.
  const selectedHasScreenshot = Boolean(selectedObs && !selectedObs._error
    && (selectedObs.acquisition?.screenshots?.length || selectedObs.acquisition?.screenshot));
  const selectedHasVisionSidecar = Boolean(selectedObs?.vision_candidates_meta);
  useEffect(() => {
    if (!VISION_CATCHALL_ENABLED) return;  // catchall parked in the back — see flag note above
    if (!selectedObsFilename || !selectedHasScreenshot || selectedHasVisionSidecar) return;
    if (visionRequestedRef.current.has(selectedObsFilename)) return;
    visionRequestedRef.current.add(selectedObsFilename);
    let cancelled = false;
    (async () => {
      try {
        await fetch(`${API}/api/observations/${encodeURIComponent(selectedObsFilename)}/vision?captions=false`, { method: "POST" });
        if (!cancelled) await refreshVisionCandidates(selectedObsFilename);
      } catch {
        visionRequestedRef.current.delete(selectedObsFilename);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedObsFilename, selectedHasScreenshot, selectedHasVisionSidecar, refreshVisionCandidates]);

  const activeRuns = runs.data.filter((run) => String(run.status || "").toLowerCase().includes("running")).length;
  const blockedRuns = runs.data.filter((run) => String(run.status || "").toLowerCase().includes("blocked")).length;
  const completedRuns = runs.data.filter((run) => String(run.status || "").toLowerCase().includes("success")).length;
  const filteredRuns = runs.data.filter((run) => {
    const query = runSearch.trim().toLowerCase();
    if (!query) return true;
    return String(run.id || "").toLowerCase().includes(query) || String(run.status || "").toLowerCase().includes(query);
  });

  const selectedRun =
    filteredRuns.find((run) => run.id === selectedRunId) ||
    runs.data.find((run) => run.id === selectedRunId) ||
    filteredRuns[0] ||
    null;

  let sectionContent = null;
  if (activePrimaryView === "home") {
    sectionContent = (
      <HomeSection
        section={activeSectionId}
        health={health}
        activeRuns={activeRuns}
        blockedRuns={blockedRuns}
        completedRuns={completedRuns}
        apiLabel={apiLabel}
        openSystemView={openSystemView}
      />
    );
  } else if (activePrimaryView === "system" && activeSectionId === "api-usage") {
    sectionContent = <ApiUsageSection usage={usage} loadUsage={loadUsage} />;
  } else if (activePrimaryView === "system") {
    sectionContent = <SystemSection section={activeSectionId} systemStatus={systemStatus} loadSystemStatus={loadSystemStatus} />;
  } else if (activePrimaryView === "training" && activeSectionId === "coverage") {
    sectionContent = <CoverageSection session={selectedTrainingSession} />;
  } else if (activePrimaryView === "training" && activeSectionId === "page-states") {
    sectionContent = <PageStatesSection registry={trainingRegistry} />;
  } else if (activePrimaryView === "training" && activeSectionId === "domains") {
    sectionContent = (
      <DomainsSection
        registry={trainingRegistry}
        registryStatus={registryStatus}
        saveRegistryItem={saveRegistryItem}
        archiveRegistryItem={archiveRegistryItem}
      />
    );
  } else if (activePrimaryView === "training") {
    sectionContent = (
      <TrainingSection
        section={activeSectionId}
        trainingRegistry={trainingRegistry}
        sessionForm={sessionForm}
        setSessionForm={setSessionForm}
        createTrainingSession={createTrainingSession}
        creatingSession={creatingSession}
        sessionFormError={sessionFormError}
        sessions={sessions}
        selectedTrainingSessionId={selectedTrainingSessionId}
        setSelectedTrainingSessionId={setSelectedTrainingSessionId}
        startTrainingSession={startTrainingSession}
        stopTrainingSession={stopTrainingSession}
        deleteTrainingSession={deleteTrainingSession}
        resetAllTrainingData={resetAllTrainingData}
        sessionActionLoading={sessionActionLoading}
        tabs={tabs}
        tabsLoading={tabsLoading}
        tabsWarning={tabsWarning}
        selectedTabId={selectedTabId}
        setSelectedTabId={setSelectedTabId}
        loadTabs={loadTabs}
        triggerCapture={triggerCapture}
        captureInProgress={captureInProgress}
        captureError={captureError}
        capturePhase={capturePhase}
        captureElapsed={captureElapsed}
        captureSuccess={captureSuccess}
        observations={observations}
        stateMeta={stateMeta}
        domainMeta={domainMeta}
        goalMeta={goalMeta}
        loadObservations={loadObservations}
        updateObsMeta={updateObsMeta}
        deleteObservation={deleteObservation}
        bulkDeleteObservations={bulkDeleteObservations}
        justCapturedFilename={justCapturedFilename}
        openTrainingObservation={openTrainingObservation}
        selectedObs={selectedObs}
        selectedObsFilename={selectedObsFilename}
        labels={labels}
        setLabels={setLabels}
        bboxOverride={bboxOverride}
        setBboxOverride={setBboxOverride}
        manualCandidates={manualCandidates}
        setManualCandidates={setManualCandidates}
        interactionEdits={interactionEdits}
        setInteractionEdits={setInteractionEdits}
        pageStateOptions={pageStateOptions}
        onCreatePageState={createPageStateFromLabeler}
        actionOptions={actionOptions}
        onCreateAction={createAction}
        onRefreshVision={refreshVisionCandidates}
        onGenerateCaptions={generateVisionCaptions}
        captionsLoading={captionsLoading}
        saveTrainingAnnotation={saveTrainingAnnotation}
        annotationSaving={annotationSaving}
        annotationMessage={annotationMessage}
        buildTrainingDataset={buildTrainingDataset}
        trainGroundingModel={trainGroundingModel}
        loadTrainingTargetComparison={loadTrainingTargetComparison}
        datasetStatus={datasetStatus}
        trainingStatus={trainingStatus}
        targetComparisonStatus={targetComparisonStatus}
        onChangeSection={setActiveSection}
      />
    );
  } else if (activePrimaryView === "workers") {
    sectionContent = (
      <WorkersSection
        section={activeSectionId}
        filteredRuns={filteredRuns}
        selectedRun={selectedRun}
        runSearch={runSearch}
        setRunSearch={setRunSearch}
        activeRuns={activeRuns}
        blockedRuns={blockedRuns}
        completedRuns={completedRuns}
        createRun={createRun}
        setSelectedRunId={setSelectedRunId}
        runs={runs}
        workers={mockWorkers}
        observations={observations}
        loadObservations={loadObservations}
        updateObsMeta={updateObsMeta}
        deleteObservation={deleteObservation}
        bulkDeleteObservations={bulkDeleteObservations}
        justCapturedFilename={justCapturedFilename}
        openWorkerObservation={openWorkerObservation}
        selectedObs={selectedObs}
        selectedObsFilename={selectedObsFilename}
        clearSelectedObservation={clearSelectedObservation}
      />
    );
  } else if (activePrimaryView === "chat") {
    sectionContent = <ChatSection />;
  } else if (activePrimaryView === "lab") {
    // Lab merges the grounding-model pipeline (Models/Eval Runs/Run Detail) with
    // the SELECT-stage flywheel + Movement Playground.
    if (["models", "eval-runs", "run-detail"].includes(activeSectionId)) {
      sectionContent = <ModelsSection section={activeSectionId === "models" ? "registry" : activeSectionId} />;
    } else if (activeSectionId === "scorecard") {
      sectionContent = <ScorecardSection />;
    } else if (activeSectionId === "training-space") {
      sectionContent = <TrainingSpaceSection />;
    } else if (activeSectionId === "state-graph") {
      sectionContent = <StateGraphSection />;
    } else {
      sectionContent = <LabSection section={activeSectionId} />;
    }
  } else if (activePrimaryView === "models") {
    sectionContent = <ModelsSection section={activeSectionId} />;
  } else {
    sectionContent = (
      <DomainsSection
        registry={trainingRegistry}
        registryStatus={registryStatus}
        saveRegistryItem={saveRegistryItem}
        archiveRegistryItem={archiveRegistryItem}
      />
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">◆</div>
          <div>
            <div className="brand-title">Ops Pilot</div>
            <div className="brand-subtitle">Control Plane</div>
          </div>
        </div>

        <nav className="nav-section">
          <div className="nav-label">{sidebarLevel === "primary" ? "Navigation" : "Section Menu"}</div>
          <div className={`sidebar-menu-stage ${sidebarLevel === "secondary" ? "is-secondary" : ""}`}>
            <div className="sidebar-menu-track">
              <div className="sidebar-menu-panel">
                <button className={`nav-item nav-home ${activePrimaryView === "home" ? "active" : ""}`} onClick={goHome}>
                  Home
                </button>
                {Object.entries(CONTROL_PLANE_NAV)
                  .filter(([key]) => key !== "home")
                  .map(([key, entry]) => (
                    <button key={key} className={`nav-item ${activePrimaryView === key ? "active" : ""}`} onClick={() => openPrimaryView(key)}>
                      {entry.label}
                    </button>
                  ))}
              </div>

              <div className="sidebar-menu-panel">
                <button className={`nav-item nav-home ${activePrimaryView === "home" ? "active" : ""}`} onClick={goHome}>
                  Home
                </button>
                <button className="nav-item nav-back" onClick={returnToPrimaryRail}>
                  ← All Sections
                </button>
                <div className="nav-section-heading">{currentNav.label}</div>
                {canEnterSecondary ? currentNav.sections.map((section) => (
                  <button key={section.id} className={`nav-item nav-subitem ${activeSectionId === section.id ? "active" : ""}`} onClick={() => setActiveSection(section.id)}>
                    <span className="nav-subitem-label">{section.label}</span>
                    <span className="nav-subitem-copy">{section.subtitle}</span>
                  </button>
                )) : null}
              </div>
            </div>
          </div>
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-footer-text">API</div>
          <div className="sidebar-footer-value">{apiLabel}</div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div>
            <h1 className="page-title">{currentNav.title}</h1>
            <p className="page-subtitle">{activeSection?.subtitle || currentNav.subtitle}</p>
          </div>

          <div className="topbar-actions">
            <button className="ghost-btn" onClick={refresh}>Refresh</button>
            <div className={`health-badge ${health.ok ? "ok" : "bad"}`}>
              {health.loading ? "Checking..." : health.ok ? "API Connected" : "API Down"}
            </div>
          </div>
        </header>

        <div className="workspace-content">{sectionContent}</div>
      </main>
    </div>
  );
}
