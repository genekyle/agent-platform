import { useMemo } from "react";

export function CaptureSection({
  mode,
  domains,
  goals = [],
  tasks = [],
  scenarios,
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
  recentObservations,
  onOpenRecent,
}) {
  const selectedSession = useMemo(
    () => sessions.find((session) => session.id === selectedTrainingSessionId) ?? null,
    [sessions, selectedTrainingSessionId],
  );

  const activeScenarios = useMemo(
    () => scenarios.filter((scenario) => scenario.domain_id === sessionForm.domain_id),
    [scenarios, sessionForm.domain_id],
  );
  const selectedScenario = useMemo(
    () => activeScenarios.find((item) => item.scenario_id === sessionForm.scenario_id) ?? null,
    [activeScenarios, sessionForm.scenario_id],
  );
  const selectedScenarioGoal = useMemo(
    () => goals.find((goal) => goal.goal_id === selectedScenario?.goal_id) ?? null,
    [goals, selectedScenario],
  );
  const selectedScenarioTask = useMemo(
    () => tasks.find((task) => task.task_id === selectedScenario?.task_id) ?? null,
    [tasks, selectedScenario],
  );

  const selectedTab = useMemo(
    () => tabs.find((tab) => tab.id === selectedTabId),
    [tabs, selectedTabId],
  );

  if (mode === "setup") {
    return (
      <div className="section-stack">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Start Training Session</h2>
              <p>Create a structured session first. Domain and goal are mandatory; task is optional.</p>
            </div>
          </div>

          <div className="capture-task-grid">
            <select
              className="form-select"
              value={sessionForm.domain_id}
              onChange={(event) => setSessionForm((current) => ({ ...current, domain_id: event.target.value, scenario_id: "" }))}
            >
              <option value="">Select domain</option>
              {domains.map((domain) => (
                <option key={domain.domain_id} value={domain.domain_id}>{domain.display_name}</option>
              ))}
            </select>

            <select
              className="form-select"
              value={sessionForm.scenario_id}
              onChange={(event) => setSessionForm((current) => ({ ...current, scenario_id: event.target.value }))}
              disabled={!sessionForm.domain_id}
            >
              <option value="">Select scenario</option>
              {activeScenarios.map((scenario) => (
                <option key={scenario.scenario_id} value={scenario.scenario_id}>{scenario.display_name}</option>
              ))}
            </select>
          </div>

          {selectedScenario ? (
            <div className="summary-stack">
              <div className="summary-item">
                <div className="summary-title">Scenario</div>
                <div className="summary-text">
                  {selectedScenario.description || "Selected scenario"}
                </div>
              </div>
              <div className="summary-item">
                <div className="summary-title">Training Target</div>
                <div className="summary-text">
                  {selectedScenarioGoal?.display_name || selectedScenario.goal_id}
                  {selectedScenarioTask ? ` · ${selectedScenarioTask.display_name}` : " · no task wrapper"}
                  {selectedScenario.start_page_state ? ` · starts at ${selectedScenario.start_page_state}` : ""}
                </div>
              </div>
            </div>
          ) : null}

          <textarea
            className="form-input"
            rows="3"
            placeholder="Operator notes for this session"
            value={sessionForm.notes}
            onChange={(event) => setSessionForm((current) => ({ ...current, notes: event.target.value }))}
          />

          <div className="detail-actions">
            <button
              className="primary-btn"
              onClick={createTrainingSession}
              disabled={!sessionForm.domain_id || !sessionForm.scenario_id || creatingSession}
            >
              {creatingSession ? "Creating..." : "Create Training Session"}
            </button>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Existing Sessions</h2>
              <p>Choose a session to continue capture or review its runtime state.</p>
            </div>
          </div>

          {sessions.length === 0 ? (
            <div className="empty-state">No training sessions yet.</div>
          ) : (
            <div className="recent-capture-list">
              {sessions.map((session) => (
                <button
                  key={session.id}
                  className={`recent-capture-item ${selectedTrainingSessionId === session.id ? "active" : ""}`}
                  onClick={() => setSelectedTrainingSessionId(session.id)}
                >
                  <span className="recent-capture-title">session-{session.id} · {session.domain_id} / {session.scenario_id}</span>
                  <span className="recent-capture-meta">
                    {session.status} · {session.capture_profile} · {session.chrome_debug_port ?? "no-port"}
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Session Capture</h2>
            <p>Capture is locked to an active structured session and its dedicated Chrome profile.</p>
          </div>
        </div>

        {!selectedSession ? (
          <div className="empty-state">Select a training session in Session Setup first.</div>
        ) : (
          <>
            <div className="summary-stack">
              <div className="summary-item">
                <div className="summary-title">Session</div>
                <div className="summary-text">session-{selectedSession.id} · {selectedSession.domain_id} / {selectedSession.scenario_id}</div>
              </div>
              <div className="summary-item">
                <div className="summary-title">Browser</div>
                <div className="summary-text">{selectedSession.status} · port {selectedSession.chrome_debug_port ?? "-"}</div>
              </div>
            </div>

            <div className="detail-actions">
              <button
                className="primary-btn"
                onClick={startTrainingSession}
                disabled={selectedSession.status === "active" || sessionActionLoading}
              >
                {sessionActionLoading && selectedSession.status !== "active" ? "Starting..." : "Start Session Chrome"}
              </button>
              <button
                className="secondary-btn"
                onClick={stopTrainingSession}
                disabled={selectedSession.status !== "active" || sessionActionLoading}
              >
                {sessionActionLoading && selectedSession.status === "active" ? "Stopping..." : "Stop Session Chrome"}
              </button>
            </div>

            <div className="capture-toolbar">
              <div className="chrome-status">
                <span className={`chrome-dot ${tabsWarning ? "disconnected" : tabs.length > 0 ? "connected" : "unknown"}`} />
                {tabsLoading ? (
                  <span className="chrome-label">Detecting session tabs...</span>
                ) : tabsWarning ? (
                  <span className="chrome-label error" title={tabsWarning}>Session browser not connected</span>
                ) : tabs.length > 0 ? (
                  <span className="chrome-label">{tabs.length} session tab{tabs.length !== 1 ? "s" : ""} detected</span>
                ) : (
                  <span className="chrome-label muted">No session tabs</span>
                )}
                <button className="ghost-btn small-btn" onClick={loadTabs} disabled={tabsLoading || selectedSession.status !== "active"}>↻</button>
              </div>

              <div className="capture-row">
                <div className="capture-row-main">
                  <select
                    className="tab-select form-select"
                    value={selectedTabId ?? ""}
                    onChange={(event) => setSelectedTabId(event.target.value)}
                    disabled={captureInProgress || selectedSession.status !== "active"}
                  >
                    <option value="">Select session tab</option>
                    {tabs.map((tab) => (
                      <option key={tab.id} value={tab.id}>
                        {(tab.title || "Untitled").slice(0, 40)}
                      </option>
                    ))}
                  </select>

                  <button
                    className="primary-btn small-btn"
                    onClick={triggerCapture}
                    disabled={!selectedTabId || selectedSession.status !== "active" || captureInProgress}
                  >
                    {captureInProgress
                      ? <><span className="capture-spinner" />{["Connecting to Chrome...", "Capturing page state...", "Running pipeline...", "Finalizing..."][capturePhase]} ({captureElapsed}s)</>
                      : "Capture Session Tab"}
                  </button>
                </div>
              </div>
            </div>
          </>
        )}

        {captureError ? <span className="capture-error">{captureError}</span> : null}
        {captureSuccess ? (
          <div className="capture-success">
            Captured {captureSuccess.candidate_count} candidates from {selectedTab?.title || "selected tab"}.
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Recent Session Captures</h2>
            <p>Only structured training captures appear here.</p>
          </div>
        </div>

        {recentObservations.length === 0 ? (
          <div className="empty-state">No captures yet.</div>
        ) : (
          <div className="recent-capture-list">
            {recentObservations.map((observation) => (
              <button
                key={observation.filename}
                className={`recent-capture-item ${captureSuccess?.filename === observation.filename ? "active" : ""}`}
                onClick={() => onOpenRecent(observation.filename)}
              >
                <span className="recent-capture-title">{observation.page_title || "Untitled"}</span>
                <span className="recent-capture-meta">
                  {observation.domain_id} / {observation.scenario_id || observation.goal_id} · {observation.timestamp ? new Date(observation.timestamp).toLocaleString() : "unknown time"}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
