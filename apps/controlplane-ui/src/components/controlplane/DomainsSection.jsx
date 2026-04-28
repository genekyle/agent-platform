import { useEffect, useMemo, useState } from "react";

// ─── Constants ────────────────────────────────────────────────────────────────

const TABS = [
  { id: "overview",    label: "Overview" },
  { id: "scenarios",   label: "Scenarios",   stat: "scenarios" },
  { id: "goals",       label: "Goals",       stat: "goals" },
  { id: "tasks",       label: "Tasks",       stat: "tasks" },
  { id: "page_states", label: "Page States", stat: "pageStates" },
  { id: "capture",     label: "Capture" },
];

const SHOT_TYPE_OPTIONS = [
  { value: "viewport", label: "Viewport" },
  { value: "fullpage",  label: "Full Page" },
  { value: "sweep_1",  label: "Sweep 1" },
  { value: "sweep_2",  label: "Sweep 2" },
];

const VALIDATION_RULE_KINDS = ["host_match", "url_prefix", "title_match"];

const EMPTY_DOMAIN = {
  domain_id: "",
  display_name: "",
  host_patterns: "",
  page_states: [],
  capture_profile: "viewport",
  shot_types: ["viewport"],
  validation_rules: [],
  config_version: "v1",
};

const EMPTY_PAGE_STATE = {
  page_state_id: "", display_name: "",
  description: "", url_pattern: "", visual_description: "",
};
const EMPTY_VALIDATION_RULE = { kind: "host_match", value: "" };
const EMPTY_GOAL = {
  goal_id: "", display_name: "", action_type_hints: "click",
  description: "", typical_element_types: "", success_criteria: "",
};

const EMPTY_TASK = {
  task_id: "", scope_level: "domain", goal_id: "", display_name: "",
  description: "", estimated_steps: "", is_repeatable: true,
};

const ESTIMATED_STEPS_OPTIONS = [
  { value: "",     label: "Not specified" },
  { value: "1-3",  label: "1–3 interactions" },
  { value: "4-10", label: "4–10 interactions" },
  { value: "10+",  label: "10+ interactions" },
];
const EMPTY_SCENARIO = {
  scenario_id: "", goal_id: "", task_id: "", display_name: "",
  start_page_state: "", description: "", capture_profile_override: "",
  element_query: "", expected_outcome_state: "", difficulty: "", is_eval_only: false,
};

const DIFFICULTY_OPTIONS = [
  { value: "", label: "No difficulty set" },
  { value: "easy",   label: "Easy — common element, clear label" },
  { value: "medium", label: "Medium — context-dependent element" },
  { value: "hard",   label: "Hard — ambiguous, visually similar distractors" },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function slugify(value) {
  return String(value || "")
    .trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function splitList(value) {
  return String(value || "").split(/[,\n]/).map(s => s.trim()).filter(Boolean);
}

function domainToForm(domain) {
  if (!domain) return EMPTY_DOMAIN;
  const cd = domain.capture_defaults ?? {};
  return {
    domain_id:       domain.domain_id,
    display_name:    domain.display_name,
    host_patterns:   (domain.host_patterns ?? []).join("\n"),
    page_states:     domain.page_states ?? [],
    capture_profile: cd.profile ?? "viewport",
    shot_types:      cd.shot_types ?? ["viewport"],
    validation_rules: (domain.validation_expectations ?? []).map(r => ({
      kind: r.kind ?? "host_match",
      value: r.value ?? "",
    })),
    config_version: domain.config_version ?? "v1",
  };
}

function compactPayload(payload) {
  return Object.fromEntries(Object.entries(payload).filter(([, v]) => v !== ""));
}

function findName(items, id, idKey) {
  return items.find(i => i[idKey] === id)?.display_name ?? id ?? "—";
}

// ─── Main Component ───────────────────────────────────────────────────────────

export function DomainsSection({ registry, registryStatus, saveRegistryItem, archiveRegistryItem }) {
  const [selectedDomainId, setSelectedDomainId] = useState(registry.domains[0]?.domain_id ?? "__new");
  const [activeTab, setActiveTab]     = useState("overview");
  const [domainForm, setDomainForm]   = useState(EMPTY_DOMAIN);
  const [pageStateInput, setPageStateInput]             = useState(EMPTY_PAGE_STATE);
  const [validationRuleInput, setValidationRuleInput]   = useState(EMPTY_VALIDATION_RULE);
  const [goalForm, setGoalForm]         = useState(EMPTY_GOAL);
  const [taskForm, setTaskForm]         = useState(EMPTY_TASK);
  const [scenarioForm, setScenarioForm] = useState(EMPTY_SCENARIO);
  const [showGoalForm, setShowGoalForm]         = useState(false);
  const [showTaskForm, setShowTaskForm]         = useState(false);
  const [showScenarioForm, setShowScenarioForm] = useState(false);
  const [editingGoalId, setEditingGoalId]         = useState(null);
  const [editingTaskId, setEditingTaskId]         = useState(null);
  const [editingScenarioId, setEditingScenarioId] = useState(null);
  const [formError, setFormError] = useState(null);

  // ── Derived data ──

  const selectedDomain = useMemo(
    () => registry.domains.find(d => d.domain_id === selectedDomainId) ?? null,
    [registry.domains, selectedDomainId],
  );

  const domainGoals = useMemo(
    () => registry.goals.filter(g => g.domain_id === selectedDomainId || g.domain_id === null),
    [registry.goals, selectedDomainId],
  );

  const domainOnlyGoals = useMemo(
    () => registry.goals.filter(g => g.domain_id === selectedDomainId),
    [registry.goals, selectedDomainId],
  );

  const domainTasks = useMemo(
    () => registry.tasks.filter(t => t.domain_id === selectedDomainId || t.domain_id === null),
    [registry.tasks, selectedDomainId],
  );

  const domainOnlyTasks = useMemo(
    () => registry.tasks.filter(t => t.domain_id === selectedDomainId),
    [registry.tasks, selectedDomainId],
  );

  const domainScenarios = useMemo(
    () => registry.scenarios.filter(s => s.domain_id === selectedDomainId),
    [registry.scenarios, selectedDomainId],
  );

  const scenariosByGoal = useMemo(() =>
    domainGoals.reduce((acc, goal) => {
      acc[goal.goal_id] = domainScenarios.filter(s => s.goal_id === goal.goal_id);
      return acc;
    }, {}),
    [domainGoals, domainScenarios],
  );

  const domainStats = useMemo(() => ({
    goals:      domainOnlyGoals.length,
    tasks:      domainOnlyTasks.length,
    scenarios:  domainScenarios.length,
    pageStates: selectedDomain?.page_states?.length ?? 0,
  }), [domainOnlyGoals.length, domainOnlyTasks.length, domainScenarios.length, selectedDomain]);

  // ── Effects ──

  useEffect(() => {
    if (selectedDomainId === "__new") return;
    if (!selectedDomainId && registry.domains[0]?.domain_id) {
      setSelectedDomainId(registry.domains[0].domain_id); return;
    }
    if (selectedDomainId && !registry.domains.some(d => d.domain_id === selectedDomainId)) {
      setSelectedDomainId(registry.domains[0]?.domain_id ?? "__new");
    }
  }, [registry.domains, selectedDomainId]);

  useEffect(() => {
    setDomainForm(domainToForm(selectedDomain));
    setPageStateInput(EMPTY_PAGE_STATE);
    setValidationRuleInput(EMPTY_VALIDATION_RULE);
  }, [selectedDomain]);

  useEffect(() => {
    setActiveTab("overview");
    setGoalForm(EMPTY_GOAL);
    setTaskForm(EMPTY_TASK);
    setScenarioForm(EMPTY_SCENARIO);
    setShowGoalForm(false);
    setShowTaskForm(false);
    setShowScenarioForm(false);
    setEditingGoalId(null);
    setEditingTaskId(null);
    setEditingScenarioId(null);
    setFormError(null);
  }, [selectedDomainId]);

  // ── Page state helpers ──

  const addPageState = () => {
    if (!pageStateInput.display_name) return;
    const id = pageStateInput.page_state_id || slugify(pageStateInput.display_name);
    setDomainForm(c => ({
      ...c, page_states: [...c.page_states, { page_state_id: id, display_name: pageStateInput.display_name }],
    }));
    setPageStateInput(EMPTY_PAGE_STATE);
  };

  const removePageState = (idx) =>
    setDomainForm(c => ({ ...c, page_states: c.page_states.filter((_, i) => i !== idx) }));

  const updatePageState = (idx, field, value) =>
    setDomainForm(c => ({
      ...c, page_states: c.page_states.map((s, i) => i === idx ? { ...s, [field]: value } : s),
    }));

  // ── Validation rule helpers ──

  const addValidationRule = () => {
    if (!validationRuleInput.value) return;
    setDomainForm(c => ({
      ...c, validation_rules: [...c.validation_rules, { ...validationRuleInput }],
    }));
    setValidationRuleInput(EMPTY_VALIDATION_RULE);
  };

  const removeValidationRule = (idx) =>
    setDomainForm(c => ({ ...c, validation_rules: c.validation_rules.filter((_, i) => i !== idx) }));

  // ── Domain save ──

  const saveDomain = async () => {
    setFormError(null);
    try {
      const domainId = domainForm.domain_id || slugify(domainForm.display_name);
      const payload = {
        domain_id:    domainId,
        display_name: domainForm.display_name,
        host_patterns: splitList(domainForm.host_patterns),
        page_states: domainForm.page_states,
        capture_defaults: {
          profile:    domainForm.capture_profile,
          shot_types: domainForm.shot_types,
        },
        validation_expectations: domainForm.validation_rules,
        config_version: domainForm.config_version || "v1",
        status: "active",
      };
      const saved = await saveRegistryItem("domains", payload, selectedDomain ? selectedDomain.domain_id : null);
      if (saved?.domain_id) setSelectedDomainId(saved.domain_id);
    } catch (err) {
      setFormError(err.message);
    }
  };

  // ── Goal handlers ──

  const saveGoal = async () => {
    const payload = compactPayload({
      goal_id:                editingGoalId ? "" : goalForm.goal_id || slugify(goalForm.display_name),
      domain_id:              selectedDomainId,
      display_name:           goalForm.display_name,
      action_type_hints:      splitList(goalForm.action_type_hints),
      description:            goalForm.description || null,
      typical_element_types:  splitList(goalForm.typical_element_types),
      success_criteria:       goalForm.success_criteria || null,
      status: "active",
    });
    const saved = await saveRegistryItem("goals", payload, editingGoalId);
    if (saved) { setGoalForm(EMPTY_GOAL); setEditingGoalId(null); setShowGoalForm(false); }
  };

  const startEditGoal = (goal) => {
    setEditingGoalId(goal.goal_id);
    setGoalForm({
      goal_id:               goal.goal_id,
      display_name:          goal.display_name,
      action_type_hints:     (goal.action_type_hints ?? []).join(", "),
      description:           goal.description ?? "",
      typical_element_types: (goal.typical_element_types ?? []).join(", "),
      success_criteria:      goal.success_criteria ?? "",
    });
    setShowGoalForm(true);
  };

  const cancelGoal = () => { setGoalForm(EMPTY_GOAL); setEditingGoalId(null); setShowGoalForm(false); };

  // ── Task handlers ──

  const saveTask = async () => {
    const payload = compactPayload({
      task_id:         editingTaskId ? "" : taskForm.task_id || slugify(taskForm.display_name),
      scope_level:     taskForm.scope_level,
      domain_id:       selectedDomainId,
      goal_id:         taskForm.goal_id || null,
      display_name:    taskForm.display_name,
      description:     taskForm.description || null,
      estimated_steps: taskForm.estimated_steps || null,
      is_repeatable:   taskForm.is_repeatable,
      status: "active",
    });
    const saved = await saveRegistryItem("tasks", payload, editingTaskId);
    if (saved) { setTaskForm(EMPTY_TASK); setEditingTaskId(null); setShowTaskForm(false); }
  };

  const startEditTask = (task) => {
    setEditingTaskId(task.task_id);
    setTaskForm({
      task_id:         task.task_id,
      display_name:    task.display_name,
      scope_level:     task.scope_level,
      goal_id:         task.goal_id ?? "",
      description:     task.description ?? "",
      estimated_steps: task.estimated_steps ?? "",
      is_repeatable:   task.is_repeatable ?? true,
    });
    setShowTaskForm(true);
  };

  const cancelTask = () => { setTaskForm(EMPTY_TASK); setEditingTaskId(null); setShowTaskForm(false); };

  // ── Scenario handlers ──

  const saveScenario = async () => {
    const payload = compactPayload({
      scenario_id: editingScenarioId ? "" : scenarioForm.scenario_id || slugify(`${selectedDomainId}_${scenarioForm.display_name}`),
      domain_id:   selectedDomainId,
      goal_id:     scenarioForm.goal_id,
      task_id:     scenarioForm.task_id || null,
      display_name: scenarioForm.display_name,
      start_page_state:        scenarioForm.start_page_state || null,
      description:             scenarioForm.description || null,
      capture_profile_override: scenarioForm.capture_profile_override || null,
      // Vision training fields
      element_query:          scenarioForm.element_query || null,
      expected_outcome_state: scenarioForm.expected_outcome_state || null,
      difficulty:             scenarioForm.difficulty || null,
      is_eval_only:           scenarioForm.is_eval_only,
      status: "active",
    });
    const saved = await saveRegistryItem("scenarios", payload, editingScenarioId);
    if (saved) {
      setScenarioForm(EMPTY_SCENARIO);
      setEditingScenarioId(null);
      setShowScenarioForm(false);
    }
  };

  const startEditScenario = (scenario) => {
    setEditingScenarioId(scenario.scenario_id);
    setScenarioForm({
      scenario_id:             scenario.scenario_id,
      display_name:            scenario.display_name,
      goal_id:                 scenario.goal_id,
      task_id:                 scenario.task_id ?? "",
      start_page_state:        scenario.start_page_state ?? "",
      description:             scenario.description ?? "",
      capture_profile_override: scenario.capture_profile_override ?? "",
      element_query:           scenario.element_query ?? "",
      expected_outcome_state:  scenario.expected_outcome_state ?? "",
      difficulty:              scenario.difficulty ?? "",
      is_eval_only:            scenario.is_eval_only ?? false,
    });
    setShowScenarioForm(true);
  };

  const startAddScenario = (goalId = "") => {
    setEditingScenarioId(null);
    setScenarioForm({ ...EMPTY_SCENARIO, goal_id: goalId });
    setShowScenarioForm(true);
  };

  const cancelScenario = () => {
    setShowScenarioForm(false);
    setEditingScenarioId(null);
    setScenarioForm(EMPTY_SCENARIO);
  };

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="domain-registry-workspace">
      {/* ── Domain list sidebar ── */}
      <aside className="domain-list-sidebar">
        <div className="domain-list-header">
          <span className="field-label">Domains</span>
          <span className="domain-count-badge">{registry.domains.length}</span>
        </div>
        <button
          className={`domain-list-item ${selectedDomainId === "__new" ? "active" : ""}`}
          onClick={() => setSelectedDomainId("__new")}
        >
          <span className="domain-list-name">+ New domain</span>
          <span className="domain-list-meta">Create a training domain</span>
        </button>
        {registry.domains.map(domain => (
          <button
            key={domain.domain_id}
            className={`domain-list-item ${selectedDomainId === domain.domain_id ? "active" : ""}`}
            onClick={() => setSelectedDomainId(domain.domain_id)}
          >
            <span className="domain-list-name">{domain.display_name}</span>
            <span className="domain-list-meta">
              {domain.domain_id} · {registry.scenarios.filter(s => s.domain_id === domain.domain_id).length} scenario(s)
            </span>
          </button>
        ))}
      </aside>

      {/* ── Main workspace ── */}
      <div className="domain-workspace-area">
        {(registryStatus?.message || registryStatus?.error || formError) && (
          <div className={`annotation-message ${registryStatus?.error || formError ? "error" : "success"}`}>
            {registryStatus?.error || formError || registryStatus?.message}
          </div>
        )}

        <div className="domain-workspace-panel">
          {!selectedDomain ? (
            /* ── Create mode ── */
            <div className="domain-create-view">
              <div className="domain-create-header">
                <h2>New Domain</h2>
                <p>Define a new configurable training domain.</p>
              </div>
              <div className="domain-create-form">
                <label>
                  <span className="field-label">Display Name</span>
                  <input
                    className="form-input"
                    value={domainForm.display_name}
                    placeholder="Indeed Jobs"
                    onChange={(e) => {
                      const name = e.target.value;
                      setDomainForm(c => ({
                        ...c, display_name: name,
                        domain_id: c.domain_id ? c.domain_id : slugify(name),
                      }));
                    }}
                  />
                </label>
                <label>
                  <span className="field-label">Domain ID</span>
                  <input
                    className="form-input mono"
                    value={domainForm.domain_id}
                    placeholder="indeed_jobs"
                    onChange={(e) => setDomainForm(c => ({ ...c, domain_id: e.target.value }))}
                    onBlur={(e) => setDomainForm(c => ({ ...c, domain_id: slugify(e.target.value) }))}
                  />
                </label>
                <label>
                  <span className="field-label">Host Patterns</span>
                  <textarea
                    className="form-input"
                    rows="3"
                    value={domainForm.host_patterns}
                    placeholder={"indeed.com\nwww.indeed.com"}
                    onChange={(e) => setDomainForm(c => ({ ...c, host_patterns: e.target.value }))}
                  />
                </label>
                <label>
                  <span className="field-label">Config Version</span>
                  <input
                    className="form-input"
                    value={domainForm.config_version}
                    placeholder="v1"
                    onChange={(e) => setDomainForm(c => ({ ...c, config_version: e.target.value }))}
                  />
                </label>
              </div>
              <div className="detail-actions">
                <button
                  className="primary-btn"
                  disabled={!domainForm.display_name || registryStatus?.loading}
                  onClick={saveDomain}
                >
                  Create Domain
                </button>
              </div>
            </div>
          ) : (
            /* ── Workspace mode (existing domain) ── */
            <>
              <div className="domain-workspace-header">
                <div className="domain-workspace-title">
                  <h2>{selectedDomain.display_name}</h2>
                  <span className="mono domain-workspace-id">{selectedDomain.domain_id}</span>
                </div>
                <div className="domain-workspace-stats">
                  {domainStats.scenarios > 0  && <span>{domainStats.scenarios} scenarios</span>}
                  {domainStats.goals > 0      && <span>{domainStats.goals} goals</span>}
                  {domainStats.tasks > 0      && <span>{domainStats.tasks} tasks</span>}
                  {domainStats.pageStates > 0 && <span>{domainStats.pageStates} states</span>}
                </div>
              </div>

              {/* Tab bar */}
              <div className="domain-tab-bar">
                {TABS.map(tab => (
                  <button
                    key={tab.id}
                    className={`domain-tab ${activeTab === tab.id ? "active" : ""}`}
                    onClick={() => setActiveTab(tab.id)}
                  >
                    {tab.label}
                    {tab.stat && domainStats[tab.stat] > 0 && (
                      <span className="tab-count">{domainStats[tab.stat]}</span>
                    )}
                  </button>
                ))}
              </div>

              {/* Tab content */}
              <div className="domain-tab-content">
                {activeTab === "overview" && (
                  <OverviewTab
                    domainForm={domainForm}
                    setDomainForm={setDomainForm}
                    selectedDomain={selectedDomain}
                    registryStatus={registryStatus}
                    saveDomain={saveDomain}
                    archiveRegistryItem={archiveRegistryItem}
                  />
                )}
                {activeTab === "scenarios" && (
                  <ScenariosTab
                    domainGoals={domainGoals}
                    domainTasks={domainTasks}
                    scenariosByGoal={scenariosByGoal}
                    selectedDomain={selectedDomain}
                    scenarioForm={scenarioForm}
                    setScenarioForm={setScenarioForm}
                    editingScenarioId={editingScenarioId}
                    showScenarioForm={showScenarioForm}
                    registryStatus={registryStatus}
                    saveScenario={saveScenario}
                    startEditScenario={startEditScenario}
                    startAddScenario={startAddScenario}
                    cancelScenario={cancelScenario}
                    archiveRegistryItem={archiveRegistryItem}
                  />
                )}
                {activeTab === "goals" && (
                  <GoalsTab
                    domainOnlyGoals={domainOnlyGoals}
                    goalForm={goalForm}
                    setGoalForm={setGoalForm}
                    editingGoalId={editingGoalId}
                    showGoalForm={showGoalForm}
                    setShowGoalForm={setShowGoalForm}
                    registryStatus={registryStatus}
                    saveGoal={saveGoal}
                    startEditGoal={startEditGoal}
                    cancelGoal={cancelGoal}
                    archiveRegistryItem={archiveRegistryItem}
                  />
                )}
                {activeTab === "tasks" && (
                  <TasksTab
                    domainOnlyTasks={domainOnlyTasks}
                    domainGoals={domainGoals}
                    taskForm={taskForm}
                    setTaskForm={setTaskForm}
                    editingTaskId={editingTaskId}
                    showTaskForm={showTaskForm}
                    setShowTaskForm={setShowTaskForm}
                    registryStatus={registryStatus}
                    saveTask={saveTask}
                    startEditTask={startEditTask}
                    cancelTask={cancelTask}
                    archiveRegistryItem={archiveRegistryItem}
                  />
                )}
                {activeTab === "page_states" && (
                  <PageStatesTab
                    domainForm={domainForm}
                    pageStateInput={pageStateInput}
                    setPageStateInput={setPageStateInput}
                    addPageState={addPageState}
                    removePageState={removePageState}
                    updatePageState={updatePageState}
                    registryStatus={registryStatus}
                    saveDomain={saveDomain}
                  />
                )}
                {activeTab === "capture" && (
                  <CaptureTab
                    domainForm={domainForm}
                    setDomainForm={setDomainForm}
                    validationRuleInput={validationRuleInput}
                    setValidationRuleInput={setValidationRuleInput}
                    addValidationRule={addValidationRule}
                    removeValidationRule={removeValidationRule}
                    registryStatus={registryStatus}
                    saveDomain={saveDomain}
                  />
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Overview Tab ─────────────────────────────────────────────────────────────

function OverviewTab({ domainForm, setDomainForm, selectedDomain, registryStatus, saveDomain, archiveRegistryItem }) {
  return (
    <div className="tab-panel">
      <div className="tab-section">
        <h3 className="tab-section-title">Identity</h3>
        <div className="form-grid-2">
          <label>
            <span className="field-label">Display Name</span>
            <input
              className="form-input"
              value={domainForm.display_name}
              placeholder="Indeed Jobs"
              onChange={(e) => setDomainForm(c => ({ ...c, display_name: e.target.value }))}
            />
          </label>
          <label>
            <span className="field-label">Domain ID</span>
            <input className="form-input mono" value={domainForm.domain_id} disabled />
          </label>
          <label>
            <span className="field-label">Config Version</span>
            <input
              className="form-input"
              value={domainForm.config_version}
              placeholder="v1"
              onChange={(e) => setDomainForm(c => ({ ...c, config_version: e.target.value }))}
            />
          </label>
        </div>
      </div>

      <div className="tab-section">
        <h3 className="tab-section-title">Host Patterns</h3>
        <p className="tab-section-desc">Hostnames that identify pages belonging to this domain. One per line.</p>
        <textarea
          className="form-input"
          rows="4"
          value={domainForm.host_patterns}
          placeholder={"indeed.com\nwww.indeed.com"}
          onChange={(e) => setDomainForm(c => ({ ...c, host_patterns: e.target.value }))}
        />
      </div>

      <div className="tab-actions">
        <button className="primary-btn" disabled={!domainForm.display_name || registryStatus?.loading} onClick={saveDomain}>
          Save Domain
        </button>
        <button
          className="danger-btn"
          disabled={registryStatus?.loading}
          onClick={() => {
            if (confirm(`Archive ${selectedDomain.display_name}? All goals, tasks, and scenarios will also be archived.`)) {
              archiveRegistryItem("domains", selectedDomain.domain_id);
            }
          }}
        >
          Archive Domain
        </button>
      </div>
    </div>
  );
}

// ─── Scenarios Tab ────────────────────────────────────────────────────────────

function ScenariosTab({
  domainGoals, domainTasks, scenariosByGoal,
  selectedDomain, scenarioForm, setScenarioForm, editingScenarioId,
  showScenarioForm, registryStatus, saveScenario,
  startEditScenario, startAddScenario, cancelScenario, archiveRegistryItem,
}) {
  const pageStates = selectedDomain?.page_states ?? [];

  return (
    <div className="tab-panel">
      {/* Header row */}
      <div className="scenarios-header-row">
        <div>
          <h3 className="tab-section-title">Scenarios</h3>
          <p className="tab-section-desc">Training entry points grouped by goal. Each scenario needs an <strong>element query</strong> before it can produce vision training data.</p>
        </div>
        {!showScenarioForm && (
          <button className="primary-btn" onClick={() => startAddScenario()}>New Scenario</button>
        )}
      </div>

      {/* Inline add / edit form */}
      {showScenarioForm && (
        <div className="scenario-form-panel">
          <h4 className="scenario-form-title">
            {editingScenarioId ? "Edit Scenario" : "New Scenario"}
          </h4>

          {/* ── Basic identity ── */}
          <div className="scenario-form-grid">
            <label>
              <span className="field-label">Name</span>
              <input
                className="form-input"
                placeholder="Search Results → Open Job Posting"
                value={scenarioForm.display_name}
                onChange={(e) => setScenarioForm(c => ({ ...c, display_name: e.target.value }))}
              />
            </label>
            <label>
              <span className="field-label">Scenario ID</span>
              <input
                className="form-input mono"
                placeholder="auto-generated"
                value={scenarioForm.scenario_id}
                disabled={!!editingScenarioId}
                onChange={(e) => setScenarioForm(c => ({ ...c, scenario_id: e.target.value }))}
                onBlur={(e) => setScenarioForm(c => ({ ...c, scenario_id: slugify(e.target.value) }))}
              />
            </label>
            <label>
              <span className="field-label">Goal</span>
              <select className="form-select" value={scenarioForm.goal_id} onChange={(e) => setScenarioForm(c => ({ ...c, goal_id: e.target.value }))}>
                <option value="">Select goal</option>
                {domainGoals.map(g => <option key={g.goal_id} value={g.goal_id}>{g.display_name}</option>)}
              </select>
            </label>
            <label>
              <span className="field-label">Task</span>
              <select className="form-select" value={scenarioForm.task_id} onChange={(e) => setScenarioForm(c => ({ ...c, task_id: e.target.value }))}>
                <option value="">No task</option>
                {domainTasks.map(t => <option key={t.task_id} value={t.task_id}>{t.display_name}</option>)}
              </select>
            </label>
            <label>
              <span className="field-label">Start Page State</span>
              <select className="form-select" value={scenarioForm.start_page_state} onChange={(e) => setScenarioForm(c => ({ ...c, start_page_state: e.target.value }))}>
                <option value="">Any page state</option>
                {pageStates.map(s => <option key={s.page_state_id} value={s.page_state_id}>{s.display_name}</option>)}
              </select>
            </label>
            <label>
              <span className="field-label">Capture Override</span>
              <select className="form-select" value={scenarioForm.capture_profile_override} onChange={(e) => setScenarioForm(c => ({ ...c, capture_profile_override: e.target.value }))}>
                <option value="">Domain default</option>
                <option value="viewport">Viewport</option>
                <option value="fullpage">Full Page</option>
              </select>
            </label>
            <label className="scenario-desc-field">
              <span className="field-label">Description</span>
              <textarea
                className="form-input"
                rows="2"
                placeholder="Describe the training context and starting conditions..."
                value={scenarioForm.description}
                onChange={(e) => setScenarioForm(c => ({ ...c, description: e.target.value }))}
              />
            </label>
          </div>

          {/* ── Vision training configuration ── */}
          <div className="scenario-vision-section">
            <div className="scenario-vision-header">
              <span className="scenario-vision-title">Vision Training</span>
              <span className="scenario-vision-desc">
                The element query is the natural-language prompt the vision model receives at inference. It must be specific enough to identify a single element on screen.
              </span>
            </div>
            <label className="scenario-query-label">
              <span className="field-label">
                Element Query
                <span className="field-required">required for training</span>
              </span>
              <textarea
                className={`form-input scenario-query-input ${scenarioForm.element_query ? "has-value" : "needs-value"}`}
                rows="2"
                placeholder='e.g. "click the Apply Now button" · "find the job title search field" · "click the Sign In link in the top navigation"'
                value={scenarioForm.element_query}
                onChange={(e) => setScenarioForm(c => ({ ...c, element_query: e.target.value }))}
              />
            </label>
            <div className="scenario-vision-row">
              <label>
                <span className="field-label">Expected Outcome State</span>
                <select
                  className="form-select"
                  value={scenarioForm.expected_outcome_state}
                  onChange={(e) => setScenarioForm(c => ({ ...c, expected_outcome_state: e.target.value }))}
                >
                  <option value="">Unknown / not set</option>
                  {pageStates.map(s => <option key={s.page_state_id} value={s.page_state_id}>{s.display_name}</option>)}
                </select>
              </label>
              <label>
                <span className="field-label">Difficulty</span>
                <select
                  className="form-select"
                  value={scenarioForm.difficulty}
                  onChange={(e) => setScenarioForm(c => ({ ...c, difficulty: e.target.value }))}
                >
                  {DIFFICULTY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="scenario-eval-only-label">
                <span className="field-label">Held-out Eval</span>
                <div className="eval-only-toggle">
                  <input
                    type="checkbox"
                    id="is_eval_only"
                    checked={scenarioForm.is_eval_only}
                    onChange={(e) => setScenarioForm(c => ({ ...c, is_eval_only: e.target.checked }))}
                  />
                  <label htmlFor="is_eval_only" className="eval-only-check-label">
                    Exclude from training builds
                  </label>
                </div>
              </label>
            </div>
          </div>

          <div className="scenario-form-actions">
            <button
              className="primary-btn"
              disabled={!scenarioForm.display_name || !scenarioForm.goal_id || registryStatus?.loading}
              onClick={saveScenario}
            >
              {editingScenarioId ? "Save Scenario" : "Add Scenario"}
            </button>
            <button className="secondary-btn" onClick={cancelScenario}>Cancel</button>
            {!scenarioForm.element_query && (
              <span className="scenario-query-warning">⚠ Add an element query to enable vision training data collection</span>
            )}
          </div>
        </div>
      )}

      {/* Goal-grouped scenario list */}
      {domainGoals.length === 0 ? (
        <div className="empty-state">No goals defined yet. Add goals in the Goals tab first.</div>
      ) : (
        <div className="scenario-goal-groups">
          {domainGoals.map(goal => {
            const scenarios = scenariosByGoal[goal.goal_id] ?? [];
            return (
              <div key={goal.goal_id} className="scenario-goal-group">
                <div className="scenario-goal-header">
                  <div className="scenario-goal-info">
                    <span className="scenario-goal-name">{goal.display_name}</span>
                    <span className="mono scenario-goal-id">{goal.goal_id}</span>
                    {goal.domain_id === null && <span className="global-goal-badge">global</span>}
                  </div>
                  <div className="scenario-goal-actions">
                    <span className="scenario-count-label">
                      {scenarios.length} scenario{scenarios.length !== 1 ? "s" : ""}
                    </span>
                    {!showScenarioForm && (
                      <button className="ghost-btn small-btn" onClick={() => startAddScenario(goal.goal_id)}>
                        + Add
                      </button>
                    )}
                  </div>
                </div>
                {scenarios.length === 0 ? (
                  <div className="scenario-group-empty">No scenarios for this goal yet.</div>
                ) : (
                  <div className="scenario-items">
                    {scenarios.map(scenario => (
                      <div key={scenario.scenario_id} className="scenario-item">
                        <div className="scenario-item-main">
                          <div className="scenario-item-name-row">
                            <span className="scenario-item-name">{scenario.display_name}</span>
                            {scenario.is_eval_only && <span className="scenario-badge eval-badge">eval only</span>}
                            {scenario.difficulty && <span className={`scenario-badge difficulty-${scenario.difficulty}`}>{scenario.difficulty}</span>}
                          </div>
                          <span className="mono scenario-item-id">{scenario.scenario_id}</span>
                          {scenario.element_query ? (
                            <span className="scenario-query-preview">"{scenario.element_query}"</span>
                          ) : (
                            <span className="scenario-query-missing">⚠ No element query — not usable for vision training</span>
                          )}
                        </div>
                        <div className="scenario-item-tags">
                          {scenario.start_page_state && (
                            <span className="scenario-tag">
                              {findName(pageStates, scenario.start_page_state, "page_state_id")}
                            </span>
                          )}
                          {scenario.expected_outcome_state && (
                            <span className="scenario-tag outcome">→ {findName(pageStates, scenario.expected_outcome_state, "page_state_id")}</span>
                          )}
                          {scenario.task_id && (
                            <span className="scenario-tag task">
                              {findName(domainTasks, scenario.task_id, "task_id")}
                            </span>
                          )}
                          {scenario.capture_profile_override && (
                            <span className="scenario-tag">{scenario.capture_profile_override}</span>
                          )}
                        </div>
                        <div className="scenario-item-actions">
                          <button className="ghost-btn small-btn" onClick={() => startEditScenario(scenario)}>Edit</button>
                          <button className="table-delete-btn" onClick={() => {
                            if (confirm(`Archive "${scenario.display_name}"?`)) {
                              archiveRegistryItem("scenarios", scenario.scenario_id);
                            }
                          }}>Archive</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Goals Tab ────────────────────────────────────────────────────────────────

function GoalsTab({ domainOnlyGoals, goalForm, setGoalForm, editingGoalId, showGoalForm, setShowGoalForm, registryStatus, saveGoal, startEditGoal, cancelGoal, archiveRegistryItem }) {
  return (
    <div className="tab-panel">
      {/* Header */}
      <div className="tab-entity-header">
        <div>
          <h3 className="tab-section-title">Goals</h3>
          <p className="tab-section-desc">Goals define what the agent is trained to accomplish. Each goal drives scenario selection and feeds action type hints into capture labeling.</p>
        </div>
        {!showGoalForm && (
          <button className="primary-btn" onClick={() => setShowGoalForm(true)}>New Goal</button>
        )}
      </div>

      {/* Expanded form panel */}
      {showGoalForm && (
        <div className="config-form-panel">
          <h4 className="config-form-title">{editingGoalId ? "Edit Goal" : "New Goal"}</h4>

          <div className="config-form-section">
            <span className="config-section-label">Identity</span>
            <div className="form-grid-3">
              <label>
                <span className="field-label">Display Name</span>
                <input
                  className="form-input"
                  placeholder="Open Job Posting"
                  value={goalForm.display_name}
                  onChange={(e) => setGoalForm(c => ({ ...c, display_name: e.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Goal ID</span>
                <input
                  className="form-input mono"
                  placeholder="open_job_posting"
                  value={goalForm.goal_id}
                  disabled={!!editingGoalId}
                  onChange={(e) => setGoalForm(c => ({ ...c, goal_id: e.target.value }))}
                  onBlur={(e) => setGoalForm(c => ({ ...c, goal_id: slugify(e.target.value) }))}
                />
              </label>
              <label>
                <span className="field-label">Action Type Hints</span>
                <input
                  className="form-input"
                  placeholder="click, type, select"
                  value={goalForm.action_type_hints}
                  onChange={(e) => setGoalForm(c => ({ ...c, action_type_hints: e.target.value }))}
                />
              </label>
            </div>
          </div>

          <div className="config-form-section config-training-section">
            <span className="config-section-label training">Training Configuration</span>
            <label>
              <span className="field-label">Description</span>
              <textarea
                className="form-input"
                rows="2"
                placeholder="Semantic description of what completing this goal means for the agent..."
                value={goalForm.description}
                onChange={(e) => setGoalForm(c => ({ ...c, description: e.target.value }))}
              />
            </label>
            <div className="form-grid-2">
              <label>
                <span className="field-label">Typical Element Types</span>
                <input
                  className="form-input"
                  placeholder="button, a, input, select"
                  value={goalForm.typical_element_types}
                  onChange={(e) => setGoalForm(c => ({ ...c, typical_element_types: e.target.value }))}
                />
                <span className="field-hint">HTML/ARIA types the grounding model should prioritize</span>
              </label>
              <label>
                <span className="field-label">Success Criteria</span>
                <input
                  className="form-input"
                  placeholder="e.g. Job detail page is shown with Apply button"
                  value={goalForm.success_criteria}
                  onChange={(e) => setGoalForm(c => ({ ...c, success_criteria: e.target.value }))}
                />
                <span className="field-hint">How the task outcome model identifies goal completion</span>
              </label>
            </div>
          </div>

          <div className="config-form-actions">
            <button className="primary-btn" disabled={!goalForm.display_name || registryStatus?.loading} onClick={saveGoal}>
              {editingGoalId ? "Save Goal" : "Add Goal"}
            </button>
            <button className="secondary-btn" onClick={cancelGoal}>Cancel</button>
          </div>
        </div>
      )}

      {/* Goals table */}
      {domainOnlyGoals.length === 0 ? (
        <div className="empty-state">No domain-specific goals yet.</div>
      ) : (
        <div className="registry-card-list">
          {domainOnlyGoals.map(goal => (
            <div key={goal.goal_id} className="registry-card">
              <div className="registry-card-main">
                <div className="registry-card-title-row">
                  <span className="registry-card-name">{goal.display_name}</span>
                  {(goal.typical_element_types ?? []).map(t => (
                    <span key={t} className="element-type-badge">{t}</span>
                  ))}
                </div>
                <span className="mono registry-card-id">{goal.goal_id}</span>
                {goal.description && <p className="registry-card-desc">{goal.description}</p>}
                <div className="registry-card-meta-row">
                  {(goal.action_type_hints ?? []).length > 0 && (
                    <span className="registry-meta-item">Actions: {goal.action_type_hints.join(", ")}</span>
                  )}
                  {goal.success_criteria && (
                    <span className="registry-meta-item success-item">✓ {goal.success_criteria}</span>
                  )}
                </div>
              </div>
              <div className="registry-card-actions">
                <button className="ghost-btn small-btn" onClick={() => startEditGoal(goal)}>Edit</button>
                <button className="table-delete-btn" onClick={() => {
                  if (confirm(`Archive goal "${goal.display_name}"? Linked scenarios will also be archived.`)) {
                    archiveRegistryItem("goals", goal.goal_id);
                  }
                }}>Archive</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Tasks Tab ────────────────────────────────────────────────────────────────

function TasksTab({ domainOnlyTasks, domainGoals, taskForm, setTaskForm, editingTaskId, showTaskForm, setShowTaskForm, registryStatus, saveTask, startEditTask, cancelTask, archiveRegistryItem }) {
  return (
    <div className="tab-panel">
      {/* Header */}
      <div className="tab-entity-header">
        <div>
          <h3 className="tab-section-title">Tasks</h3>
          <p className="tab-section-desc">Tasks are structured multi-step flows that scenarios can reference. They tell the agent what workflow it is executing and help the task outcome model understand session intent.</p>
        </div>
        {!showTaskForm && (
          <button className="primary-btn" onClick={() => setShowTaskForm(true)}>New Task</button>
        )}
      </div>

      {/* Expanded form panel */}
      {showTaskForm && (
        <div className="config-form-panel">
          <h4 className="config-form-title">{editingTaskId ? "Edit Task" : "New Task"}</h4>

          <div className="config-form-section">
            <span className="config-section-label">Identity</span>
            <div className="form-grid-2">
              <label>
                <span className="field-label">Display Name</span>
                <input
                  className="form-input"
                  placeholder="Complete Indeed Apply Flow"
                  value={taskForm.display_name}
                  onChange={(e) => setTaskForm(c => ({ ...c, display_name: e.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Task ID</span>
                <input
                  className="form-input mono"
                  placeholder="indeed_apply_flow"
                  value={taskForm.task_id}
                  disabled={!!editingTaskId}
                  onChange={(e) => setTaskForm(c => ({ ...c, task_id: e.target.value }))}
                  onBlur={(e) => setTaskForm(c => ({ ...c, task_id: slugify(e.target.value) }))}
                />
              </label>
              <label>
                <span className="field-label">Scope Level</span>
                <select className="form-select" value={taskForm.scope_level} onChange={(e) => setTaskForm(c => ({ ...c, scope_level: e.target.value }))}>
                  <option value="domain">Domain — applies across all goals</option>
                  <option value="goal">Goal — tied to a specific goal</option>
                  <option value="browser">Browser — applies at the browser level</option>
                </select>
              </label>
              <label>
                <span className="field-label">Goal Binding</span>
                <select className="form-select" value={taskForm.goal_id} onChange={(e) => setTaskForm(c => ({ ...c, goal_id: e.target.value }))}>
                  <option value="">No goal binding</option>
                  {domainGoals.map(g => <option key={g.goal_id} value={g.goal_id}>{g.display_name}</option>)}
                </select>
              </label>
            </div>
          </div>

          <div className="config-form-section config-training-section">
            <span className="config-section-label training">Training Configuration</span>
            <label>
              <span className="field-label">Description</span>
              <textarea
                className="form-input"
                rows="2"
                placeholder="Step-by-step description of what this task flow involves..."
                value={taskForm.description}
                onChange={(e) => setTaskForm(c => ({ ...c, description: e.target.value }))}
              />
            </label>
            <div className="form-grid-3">
              <label>
                <span className="field-label">Estimated Steps</span>
                <select className="form-select" value={taskForm.estimated_steps} onChange={(e) => setTaskForm(c => ({ ...c, estimated_steps: e.target.value }))}>
                  {ESTIMATED_STEPS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <span className="field-hint">Helps curriculum ordering and session planning</span>
              </label>
              <label className="repeatable-label">
                <span className="field-label">Repeatability</span>
                <div className="eval-only-toggle">
                  <input
                    type="checkbox"
                    id="is_repeatable"
                    checked={taskForm.is_repeatable}
                    onChange={(e) => setTaskForm(c => ({ ...c, is_repeatable: e.target.checked }))}
                  />
                  <label htmlFor="is_repeatable" className="eval-only-check-label">
                    Can repeat in one session
                  </label>
                </div>
                <span className="field-hint">Unchecked tasks run once per training session</span>
              </label>
            </div>
          </div>

          <div className="config-form-actions">
            <button className="primary-btn" disabled={!taskForm.display_name || registryStatus?.loading} onClick={saveTask}>
              {editingTaskId ? "Save Task" : "Add Task"}
            </button>
            <button className="secondary-btn" onClick={cancelTask}>Cancel</button>
          </div>
        </div>
      )}

      {/* Tasks list */}
      {domainOnlyTasks.length === 0 ? (
        <div className="empty-state">No domain-specific tasks yet.</div>
      ) : (
        <div className="registry-card-list">
          {domainOnlyTasks.map(task => (
            <div key={task.task_id} className="registry-card">
              <div className="registry-card-main">
                <div className="registry-card-title-row">
                  <span className="registry-card-name">{task.display_name}</span>
                  <span className="scope-badge scope-badge--{task.scope_level}">{task.scope_level}</span>
                  {task.estimated_steps && <span className="steps-badge">{task.estimated_steps} steps</span>}
                  {!task.is_repeatable && <span className="once-badge">once</span>}
                </div>
                <span className="mono registry-card-id">{task.task_id}</span>
                {task.description && <p className="registry-card-desc">{task.description}</p>}
                {task.goal_id && (
                  <div className="registry-card-meta-row">
                    <span className="registry-meta-item">Goal: {findName(domainGoals, task.goal_id, "goal_id")}</span>
                  </div>
                )}
              </div>
              <div className="registry-card-actions">
                <button className="ghost-btn small-btn" onClick={() => startEditTask(task)}>Edit</button>
                <button className="table-delete-btn" onClick={() => {
                  if (confirm(`Archive task "${task.display_name}"?`)) {
                    archiveRegistryItem("tasks", task.task_id);
                  }
                }}>Archive</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Page States Tab ──────────────────────────────────────────────────────────

function PageStatesTab({ domainForm, pageStateInput, setPageStateInput, addPageState, removePageState, updatePageState, registryStatus, saveDomain }) {
  return (
    <div className="tab-panel">
      <div className="tab-section">
        <h3 className="tab-section-title">Page States</h3>
        <p className="tab-section-desc">
          Named checkpoints within this domain. Page states drive three things: where training sessions start, what context annotators assign to captures, and what the state classifier is trained to recognize.
        </p>

        {/* Existing state cards */}
        <div className="page-state-cards">
          {domainForm.page_states.map((state, idx) => (
            <div key={idx} className="page-state-card">
              <div className="page-state-card-header">
                <div className="page-state-card-id-row">
                  <input
                    className="form-input mono page-state-id-input"
                    value={state.page_state_id}
                    placeholder="state_id"
                    onChange={(e) => updatePageState(idx, "page_state_id", e.target.value)}
                    onBlur={(e) => updatePageState(idx, "page_state_id", slugify(e.target.value))}
                  />
                  <input
                    className="form-input page-state-name-input"
                    value={state.display_name}
                    placeholder="Display Name"
                    onChange={(e) => updatePageState(idx, "display_name", e.target.value)}
                  />
                  <button className="ghost-btn small-btn" onClick={() => removePageState(idx)}>Remove</button>
                </div>
              </div>
              <div className="page-state-card-body">
                <label>
                  <span className="field-label">Description</span>
                  <textarea
                    className="form-input"
                    rows="2"
                    placeholder="What this page state represents and when the agent is in it..."
                    value={state.description ?? ""}
                    onChange={(e) => updatePageState(idx, "description", e.target.value)}
                  />
                </label>
                <div className="form-grid-2">
                  <label>
                    <span className="field-label">URL Pattern</span>
                    <input
                      className="form-input mono"
                      placeholder="/jobs/search or *.indeed.com/viewjob*"
                      value={state.url_pattern ?? ""}
                      onChange={(e) => updatePageState(idx, "url_pattern", e.target.value)}
                    />
                    <span className="field-hint">Glob or path prefix used to programmatically identify this state</span>
                  </label>
                  <label>
                    <span className="field-label">Visual Description</span>
                    <input
                      className="form-input"
                      placeholder="e.g. List of job cards with title, company name, and location"
                      value={state.visual_description ?? ""}
                      onChange={(e) => updatePageState(idx, "visual_description", e.target.value)}
                    />
                    <span className="field-hint">What an annotator should see on screen — training signal for the state classifier</span>
                  </label>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Add new state card */}
        <div className="page-state-card page-state-add-card">
          <div className="page-state-card-header">
            <span className="config-section-label">New Page State</span>
          </div>
          <div className="page-state-card-body">
            <div className="page-state-card-id-row">
              <input
                className="form-input mono page-state-id-input"
                value={pageStateInput.page_state_id}
                placeholder="state_id (auto)"
                onChange={(e) => setPageStateInput(c => ({ ...c, page_state_id: e.target.value }))}
                onBlur={(e) => setPageStateInput(c => ({ ...c, page_state_id: slugify(e.target.value) }))}
              />
              <input
                className="form-input page-state-name-input"
                value={pageStateInput.display_name}
                placeholder="Display Name"
                onChange={(e) => setPageStateInput(c => ({ ...c, display_name: e.target.value }))}
                onKeyDown={(e) => { if (e.key === "Enter" && pageStateInput.display_name) addPageState(); }}
              />
            </div>
            <label>
              <span className="field-label">Description</span>
              <textarea
                className="form-input"
                rows="2"
                placeholder="What this page state represents..."
                value={pageStateInput.description}
                onChange={(e) => setPageStateInput(c => ({ ...c, description: e.target.value }))}
              />
            </label>
            <div className="form-grid-2">
              <label>
                <span className="field-label">URL Pattern</span>
                <input
                  className="form-input mono"
                  placeholder="/jobs or *.indeed.com/viewjob*"
                  value={pageStateInput.url_pattern}
                  onChange={(e) => setPageStateInput(c => ({ ...c, url_pattern: e.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Visual Description</span>
                <input
                  className="form-input"
                  placeholder="e.g. List of job cards with title and company"
                  value={pageStateInput.visual_description}
                  onChange={(e) => setPageStateInput(c => ({ ...c, visual_description: e.target.value }))}
                />
              </label>
            </div>
            <button
              className="secondary-btn"
              disabled={!pageStateInput.display_name}
              onClick={addPageState}
            >
              Add Page State
            </button>
          </div>
        </div>
      </div>

      <div className="tab-actions">
        <button className="primary-btn" disabled={registryStatus?.loading} onClick={saveDomain}>
          Save Page States
        </button>
      </div>
    </div>
  );
}

// ─── Capture Tab ──────────────────────────────────────────────────────────────

function CaptureTab({ domainForm, setDomainForm, validationRuleInput, setValidationRuleInput, addValidationRule, removeValidationRule, registryStatus, saveDomain }) {
  const toggleShotType = (type) =>
    setDomainForm(c => ({
      ...c,
      shot_types: c.shot_types.includes(type)
        ? c.shot_types.filter(t => t !== type)
        : [...c.shot_types, type],
    }));

  return (
    <div className="tab-panel">
      <div className="tab-section">
        <h3 className="tab-section-title">Default Capture Profile</h3>
        <p className="tab-section-desc">
          Controls how screenshots are taken during training sessions for this domain. Individual scenarios can override these settings.
        </p>
        <div className="form-grid-2">
          <label>
            <span className="field-label">Profile</span>
            <select
              className="form-select"
              value={domainForm.capture_profile}
              onChange={(e) => setDomainForm(c => ({ ...c, capture_profile: e.target.value }))}
            >
              <option value="viewport">Viewport</option>
              <option value="fullpage">Full Page</option>
              <option value="headless">Headless</option>
            </select>
          </label>
        </div>
        <div className="shot-type-group">
          <span className="field-label">Shot Types</span>
          <div className="shot-type-checkboxes">
            {SHOT_TYPE_OPTIONS.map(opt => (
              <label key={opt.value} className="shot-type-option">
                <input
                  type="checkbox"
                  checked={domainForm.shot_types.includes(opt.value)}
                  onChange={() => toggleShotType(opt.value)}
                />
                <span>{opt.label}</span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="tab-section">
        <h3 className="tab-section-title">Validation Rules</h3>
        <p className="tab-section-desc">
          Rules that verify a captured page belongs to this domain. Evaluated before a capture is accepted.
        </p>
        <div className="page-states-editor">
          {domainForm.validation_rules.map((rule, idx) => (
            <div key={idx} className="page-state-row">
              <select
                className="form-select validation-kind-col"
                value={rule.kind}
                onChange={(e) => setDomainForm(c => ({
                  ...c,
                  validation_rules: c.validation_rules.map((r, i) => i === idx ? { ...r, kind: e.target.value } : r),
                }))}
              >
                {VALIDATION_RULE_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
              </select>
              <input
                className="form-input page-state-name-col"
                value={rule.value}
                placeholder="Value"
                onChange={(e) => setDomainForm(c => ({
                  ...c,
                  validation_rules: c.validation_rules.map((r, i) => i === idx ? { ...r, value: e.target.value } : r),
                }))}
              />
              <button className="ghost-btn small-btn page-state-remove" onClick={() => removeValidationRule(idx)}>
                Remove
              </button>
            </div>
          ))}
          <div className="page-state-row page-state-add-row">
            <select
              className="form-select validation-kind-col"
              value={validationRuleInput.kind}
              onChange={(e) => setValidationRuleInput(c => ({ ...c, kind: e.target.value }))}
            >
              {VALIDATION_RULE_KINDS.map(k => <option key={k} value={k}>{k}</option>)}
            </select>
            <input
              className="form-input page-state-name-col"
              value={validationRuleInput.value}
              placeholder="e.g. indeed.com"
              onChange={(e) => setValidationRuleInput(c => ({ ...c, value: e.target.value }))}
              onKeyDown={(e) => { if (e.key === "Enter") addValidationRule(); }}
            />
            <button className="secondary-btn small-btn" disabled={!validationRuleInput.value} onClick={addValidationRule}>
              Add Rule
            </button>
          </div>
        </div>
      </div>

      <div className="tab-actions">
        <button className="primary-btn" disabled={registryStatus?.loading} onClick={saveDomain}>
          Save Capture Settings
        </button>
      </div>
    </div>
  );
}

// ─── Shared: Registry Table ───────────────────────────────────────────────────

function RegistryTable({ columns, rows, empty }) {
  if (!rows.length) return <div className="empty-state">{empty}</div>;
  return (
    <div className="table-wrap">
      <table className="runs-table">
        <thead>
          <tr>{columns.map((col, i) => <th key={`${col || "a"}-${i}`}>{col}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
