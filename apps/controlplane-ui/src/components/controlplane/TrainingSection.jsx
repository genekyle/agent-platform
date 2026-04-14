import { CaptureSection } from "./CaptureSection";
import { ObservationDetail } from "./ObservationDetail";
import { ObservationsTable } from "./ObservationsTable";
import { TrainingExportSection } from "./TrainingExportSection";

export function TrainingSection({
  section,
  trainingRegistry,
  sessionForm,
  setSessionForm,
  createTrainingSession,
  creatingSession,
  sessions,
  selectedTrainingSessionId,
  setSelectedTrainingSessionId,
  startTrainingSession,
  stopTrainingSession,
  sessionActionLoading,
  tabs,
  tabsLoading,
  tabsWarning,
  selectedTabId,
  setSelectedTabId,
  loadTabs,
  triggerCapture,
  captureInProgress,
  captureError,
  capturePhase,
  captureElapsed,
  captureSuccess,
  observations,
  loadObservations,
  updateObsMeta,
  deleteObservation,
  bulkDeleteObservations,
  justCapturedFilename,
  openTrainingObservation,
  selectedObs,
  selectedObsFilename,
  labels,
  setLabels,
  bboxOverride,
  setBboxOverride,
  saveTrainingAnnotation,
  annotationSaving,
  annotationMessage,
  buildTrainingDataset,
  trainGroundingModel,
  datasetStatus,
  trainingStatus,
  onChangeSection,
}) {
  if (section === "session-setup") {
    return (
      <CaptureSection
        mode="setup"
        domains={trainingRegistry.domains}
        scenarios={trainingRegistry.scenarios}
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
        recentObservations={observations.data.slice(0, 6)}
        onOpenRecent={openTrainingObservation}
      />
    );
  }

  if (section === "session-capture") {
    return (
      <CaptureSection
        mode="capture"
        domains={trainingRegistry.domains}
        scenarios={trainingRegistry.scenarios}
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
        recentObservations={observations.data.slice(0, 6)}
        onOpenRecent={openTrainingObservation}
      />
    );
  }

  if (section === "dataset-browser") {
    return (
      <ObservationsTable
        observations={observations.data}
        title="Dataset Browser"
        subtitle="Browse, curate, and select captured artifacts for review."
        loading={observations.loading}
        error={observations.error}
        justCapturedFilename={justCapturedFilename}
        loadObservations={loadObservations}
        onOpenObservation={openTrainingObservation}
        updateObsMeta={updateObsMeta}
        deleteObservation={deleteObservation}
        bulkDeleteObservations={bulkDeleteObservations}
        emptyMessage="No artifacts yet. Use Capture to create one."
      />
    );
  }

  if (section === "review-label") {
    if (!selectedObs) {
      return (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Review / Label</h2>
              <p>Select an artifact in Dataset Browser before reviewing screenshot grounding and candidate labels.</p>
            </div>
          </div>

          <div className="empty-state">
            No artifact selected. Open Dataset Browser to pick a capture, then continue review here.
          </div>
          <button className="primary-btn" onClick={() => onChangeSection("dataset-browser")}>
            Open Dataset Browser
          </button>
        </section>
      );
    }

    return (
      <ObservationDetail
        mode="training"
        selectedObs={selectedObs}
        selectedObsFilename={selectedObsFilename}
        labels={labels}
        setLabels={setLabels}
        bboxOverride={bboxOverride}
        setBboxOverride={setBboxOverride}
        onSaveAnnotation={saveTrainingAnnotation}
        annotationSaving={annotationSaving}
        annotationMessage={annotationMessage}
      />
    );
  }

  return (
    <TrainingExportSection
      selectedObs={selectedObs}
      selectedObsFilename={selectedObsFilename}
      labels={labels}
      buildTrainingDataset={buildTrainingDataset}
      trainGroundingModel={trainGroundingModel}
      datasetStatus={datasetStatus}
      trainingStatus={trainingStatus}
      onOpenDataset={() => onChangeSection("dataset-browser")}
    />
  );
}
