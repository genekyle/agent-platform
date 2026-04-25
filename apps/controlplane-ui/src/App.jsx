import { useCallback, useEffect, useMemo, useState } from "react";
import "./App.css";
import { ChatSection } from "./components/controlplane/ChatSection";
import { CONTROL_PLANE_NAV, DEFAULT_SECTION_VIEW } from "./components/controlplane/navigation";
import { DomainsSection } from "./components/controlplane/DomainsSection";
import { HomeSection } from "./components/controlplane/HomeSection";
import { SystemSection } from "./components/controlplane/SystemSection";
import { TrainingSection } from "./components/controlplane/TrainingSection";
import { candidateLabelsFromAnnotation, positiveCandidateIdFromLabels, resolveBbox } from "./components/controlplane/utils";
import { WorkersSection } from "./components/controlplane/WorkersSection";

const API = import.meta.env.VITE_API_BASE_URL;

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
  });

  const [observations, setObservations] = useState({ loading: false, data: [], error: null });
  const [selectedObsFilename, setSelectedObsFilename] = useState(null);
  const [selectedObs, setSelectedObs] = useState(null);
  const [labels, setLabels] = useState({});
  const [bboxOverride, setBboxOverride] = useState(null);
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
  const canEnterSecondary = ["training", "system", "workers", "domains"].includes(activePrimaryView);
  const selectedTrainingSession = useMemo(
    () => sessions.find((session) => session.id === selectedTrainingSessionId) ?? null,
    [sessions, selectedTrainingSessionId],
  );

  const setActiveSection = useCallback((sectionId) => {
    setActiveSecondaryViewByPrimary((current) => ({ ...current, [activePrimaryView]: sectionId }));
  }, [activePrimaryView]);

  const openPrimaryView = useCallback((view) => {
    setActivePrimaryView(view);
    if (view === "training" || view === "system" || view === "workers" || view === "domains") {
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
      setSelectedObs(payload);
    } catch (error) {
      setSelectedObs({ _error: error.message });
    }
  }, []);

  const clearSelectedObservation = useCallback(() => {
    setSelectedObs(null);
    setSelectedObsFilename(null);
    setLabels({});
    setBboxOverride(null);
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
            approved_bbox: positiveCandidateId ? bboxOverride : null,
          },
        }),
      });
      if (!response.ok) throw new Error(`Failed to save review: ${response.status}`);
      await loadObservation(selectedObsFilename);
      await loadObservations();
      setAnnotationMessage({ type: "success", text: "Review saved." });
    } catch (error) {
      setAnnotationMessage({ type: "error", text: error.message });
    } finally {
      setAnnotationSaving(false);
    }
  }, [bboxOverride, labels, loadObservation, loadObservations, selectedObsFilename]);

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
    try {
      const response = await fetch(`${API}/api/training/sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domain_id: sessionForm.domain_id,
          scenario_id: sessionForm.scenario_id,
          notes: sessionForm.notes || null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Create failed: ${response.status}`);
      await loadTrainingSessions();
      setSelectedTrainingSessionId(payload.id);
      setActivePrimaryView("training");
      setSidebarLevel("secondary");
      setActiveSecondaryViewByPrimary((current) => ({ ...current, training: "session-capture" }));
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
    if (activePrimaryView === "system") {
      loadSystemStatus();
      const pollTimer = setInterval(() => loadSystemStatus(), 15000);
      return () => clearInterval(pollTimer);
    }
    return undefined;
  }, [activePrimaryView, loadSystemStatus]);

  useEffect(() => {
    if (activePrimaryView === "training" || activePrimaryView === "domains") {
      loadTrainingRegistry();
    }
    if (activePrimaryView === "training") {
      loadTrainingSessions();
      loadObservations();
      loadTrainingTargetComparison();
    }
    if (activePrimaryView === "workers") {
      loadObservations();
    }
  }, [activePrimaryView, loadObservations, loadTrainingRegistry, loadTrainingSessions, loadTrainingTargetComparison]);

  useEffect(() => {
    if (activePrimaryView === "training" && activeSectionId === "session-capture" && selectedTrainingSession?.status === "active") {
      loadTabs();
    }
  }, [activePrimaryView, activeSectionId, loadTabs, selectedTrainingSession]);

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
  } else if (activePrimaryView === "system") {
    sectionContent = <SystemSection section={activeSectionId} systemStatus={systemStatus} loadSystemStatus={loadSystemStatus} />;
  } else if (activePrimaryView === "training") {
    sectionContent = (
      <TrainingSection
        section={activeSectionId}
        trainingRegistry={trainingRegistry}
        sessionForm={sessionForm}
        setSessionForm={setSessionForm}
        createTrainingSession={createTrainingSession}
        creatingSession={creatingSession}
        sessions={sessions}
        selectedTrainingSessionId={selectedTrainingSessionId}
        setSelectedTrainingSessionId={setSelectedTrainingSessionId}
        startTrainingSession={startTrainingSession}
        stopTrainingSession={stopTrainingSession}
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
