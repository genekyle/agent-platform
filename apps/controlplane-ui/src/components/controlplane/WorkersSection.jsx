import { ObservationDetail } from "./ObservationDetail";
import { ObservationsTable } from "./ObservationsTable";
import { RunsSection } from "./RunsSection";
import { WorkerHealthSection } from "./WorkerHealthSection";

export function WorkersSection({
  section,
  filteredRuns,
  selectedRun,
  runSearch,
  setRunSearch,
  activeRuns,
  blockedRuns,
  completedRuns,
  createRun,
  setSelectedRunId,
  runs,
  workers,
  observations,
  loadObservations,
  updateObsMeta,
  deleteObservation,
  bulkDeleteObservations,
  justCapturedFilename,
  openWorkerObservation,
  selectedObs,
  selectedObsFilename,
  clearSelectedObservation,
}) {
  if (section === "runs") {
    return (
      <RunsSection
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
      />
    );
  }

  if (section === "worker-health") {
    return <WorkerHealthSection workers={workers} />;
  }

  if (selectedObs) {
    return (
      <ObservationDetail
        mode="worker"
        selectedObs={selectedObs}
        selectedObsFilename={selectedObsFilename}
        labels={{}}
        setLabels={() => {}}
        onBack={clearSelectedObservation}
      />
    );
  }

  return (
    <ObservationsTable
      observations={observations.data}
      title="Worker Observations"
      subtitle="Inspect raw observer artifacts without mixing them into the training workflow."
      loading={observations.loading}
      error={observations.error}
      justCapturedFilename={justCapturedFilename}
      loadObservations={loadObservations}
      onOpenObservation={openWorkerObservation}
      updateObsMeta={updateObsMeta}
      deleteObservation={deleteObservation}
      bulkDeleteObservations={bulkDeleteObservations}
      emptyMessage="No worker observations yet."
    />
  );
}
