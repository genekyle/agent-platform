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
  sessionFormError,
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
    const selectedDomain = domains.find((domain) => domain.domain_id === sessionForm.domain_id) ?? null;
    const scenarioCountByDomain = scenarios.reduce((accumulator, scenario) => {
      accumulator[scenario.domain_id] = (accumulator[scenario.domain_id] || 0) + 1;
      return accumulator;
    }, {});
    const goalById = Object.fromEntries(goals.map((goal) => [goal.goal_id, goal]));
    const taskById = Object.fromEntries(tasks.map((task) => [task.task_id, task]));

    return (
      <div className="section-stack">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Start Training Session</h2>
              <p>
                {!selectedDomain
                  ? "Step 1 of 2 — choose the domain you're training on."
                  : "Step 2 of 2 — choose the scenario to capture."}
              </p>
            </div>
            {selectedDomain ? (
              <button
                className="ghost-btn small-btn"
                onClick={() => setSessionForm((current) => ({ ...current, domain_id: "", scenario_id: "" }))}
              >
                ← Change domain
              </button>
            ) : null}
          </div>

          {!selectedDomain ? (
            domains.length === 0 ? (
              <div className="empty-state">
                No domains configured. Add one in the Domains tab first.
              </div>
            ) : (
              <div className="domain-card-grid">
                {domains.map((domain) => (
                  <button
                    key={domain.domain_id}
                    className="domain-card"
                    onClick={() => setSessionForm((current) => ({ ...current, domain_id: domain.domain_id, scenario_id: "" }))}
                  >
                    <div className="domain-card-name">{domain.display_name}</div>
                    <div className="domain-card-meta">
                      <span className="chip">{scenarioCountByDomain[domain.domain_id] || 0} scenario{scenarioCountByDomain[domain.domain_id] === 1 ? "" : "s"}</span>
                      {domain.capture_defaults?.profile ? (
                        <span className="chip muted">{domain.capture_defaults.profile}</span>
                      ) : null}
                    </div>
                  </button>
                ))}
              </div>
            )
          ) : (
            <>
              <div className="step-context">
                <span className="step-context-label">Domain</span>
                <span className="step-context-value">{selectedDomain.display_name}</span>
              </div>

              {activeScenarios.length === 0 ? (
                <div className="empty-state">
                  No scenarios configured for this domain. Add one in the Domains tab first.
                </div>
              ) : (
                <div className="scenario-card-list">
                  {activeScenarios.map((scenario) => {
                    const isActive = scenario.scenario_id === sessionForm.scenario_id;
                    const scenarioGoal = goalById[scenario.goal_id];
                    const scenarioTask = scenario.task_id ? taskById[scenario.task_id] : null;
                    return (
                      <button
                        key={scenario.scenario_id}
                        className={`scenario-card ${isActive ? "active" : ""}`}
                        onClick={() => setSessionForm((current) => ({ ...current, scenario_id: scenario.scenario_id }))}
                      >
                        <div className="scenario-card-header">
                          <div className="scenario-card-name">{scenario.display_name}</div>
                          {isActive ? <span className="scenario-card-checkmark">✓</span> : null}
                        </div>

                        <div className="scenario-card-chips">
                          <span className="chip strong">Goal · {scenarioGoal?.display_name || scenario.goal_id}</span>
                          {scenarioTask ? (
                            <span className="chip">Task · {scenarioTask.display_name}</span>
                          ) : (
                            <span className="chip muted">no task wrapper</span>
                          )}
                          {scenario.difficulty ? <span className="chip muted">{scenario.difficulty}</span> : null}
                          {scenario.is_eval_only ? <span className="chip warn">eval only</span> : null}
                        </div>

                        {scenario.element_query ? (
                          <div className="scenario-card-query">
                            <span className="scenario-card-query-label">Element query</span>
                            <span className="scenario-card-query-text">"{scenario.element_query}"</span>
                          </div>
                        ) : (
                          <div className="scenario-card-query missing">
                            <span className="scenario-card-query-label">Element query</span>
                            <span className="scenario-card-query-text">— not set; the model has nothing to ground</span>
                          </div>
                        )}

                        {scenario.description ? (
                          <div className="scenario-card-description">{scenario.description}</div>
                        ) : null}

                        {scenario.start_page_state ? (
                          <div className="scenario-card-footer">starts at: {scenario.start_page_state}</div>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              )}

              <textarea
                className="form-input"
                rows="3"
                placeholder="Operator notes for this session (optional)"
                value={sessionForm.notes}
                onChange={(event) => setSessionForm((current) => ({ ...current, notes: event.target.value }))}
                style={{ marginTop: 16 }}
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

              {sessionFormError ? (
                <div className="form-error-banner">
                  <strong>Create failed:</strong> {sessionFormError}
                </div>
              ) : null}
            </>
          )}
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
