import { useEffect, useMemo, useState } from "react";

const EMPTY_DOMAIN = {
  domain_id: "",
  display_name: "",
  host_patterns: "",
  page_states: "",
  capture_defaults: "{\n  \"profile\": \"viewport\",\n  \"shot_types\": [\"viewport\"]\n}",
  validation_expectations: "[]",
  config_version: "v1",
};

const EMPTY_GOAL = {
  goal_id: "",
  display_name: "",
  action_type_hints: "click",
};

const EMPTY_TASK = {
  task_id: "",
  scope_level: "domain",
  goal_id: "",
  display_name: "",
};

const EMPTY_SCENARIO = {
  scenario_id: "",
  goal_id: "",
  task_id: "",
  display_name: "",
  start_page_state: "",
  description: "",
  capture_profile_override: "",
};

function slugify(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function splitList(value) {
  return String(value || "")
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseJsonField(value, fallback) {
  const text = String(value || "").trim();
  if (!text) return fallback;
  return JSON.parse(text);
}

function pageStatesToText(pageStates) {
  return (pageStates ?? [])
    .map((item) => `${item.page_state_id || ""}:${item.display_name || item.page_state_id || ""}`)
    .join("\n");
}

function textToPageStates(value) {
  return splitList(value).map((line) => {
    const [rawId, ...rest] = line.split(":");
    const pageStateId = slugify(rawId);
    return {
      page_state_id: pageStateId,
      display_name: rest.join(":").trim() || rawId.trim(),
    };
  });
}

function domainToForm(domain) {
  if (!domain) return EMPTY_DOMAIN;
  return {
    domain_id: domain.domain_id,
    display_name: domain.display_name,
    host_patterns: (domain.host_patterns ?? []).join("\n"),
    page_states: pageStatesToText(domain.page_states),
    capture_defaults: JSON.stringify(domain.capture_defaults ?? {}, null, 2),
    validation_expectations: JSON.stringify(domain.validation_expectations ?? [], null, 2),
    config_version: domain.config_version ?? "v1",
  };
}

function compactPayload(payload) {
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== ""));
}

function findName(items, id, idKey) {
  return items.find((item) => item[idKey] === id)?.display_name ?? id ?? "-";
}

export function DomainsSection({ registry, registryStatus, saveRegistryItem, archiveRegistryItem }) {
  const [selectedDomainId, setSelectedDomainId] = useState(registry.domains[0]?.domain_id ?? "__new");
  const [domainForm, setDomainForm] = useState(EMPTY_DOMAIN);
  const [goalForm, setGoalForm] = useState(EMPTY_GOAL);
  const [taskForm, setTaskForm] = useState(EMPTY_TASK);
  const [scenarioForm, setScenarioForm] = useState(EMPTY_SCENARIO);
  const [editingGoalId, setEditingGoalId] = useState(null);
  const [editingTaskId, setEditingTaskId] = useState(null);
  const [editingScenarioId, setEditingScenarioId] = useState(null);
  const [formError, setFormError] = useState(null);

  const selectedDomain = useMemo(
    () => registry.domains.find((domain) => domain.domain_id === selectedDomainId) ?? null,
    [registry.domains, selectedDomainId],
  );
  const domainGoals = useMemo(
    () => registry.goals.filter((goal) => goal.domain_id === selectedDomainId || goal.domain_id === null),
    [registry.goals, selectedDomainId],
  );
  const domainOnlyGoals = useMemo(
    () => registry.goals.filter((goal) => goal.domain_id === selectedDomainId),
    [registry.goals, selectedDomainId],
  );
  const domainTasks = useMemo(
    () => registry.tasks.filter((task) => task.domain_id === selectedDomainId || task.domain_id === null),
    [registry.tasks, selectedDomainId],
  );
  const domainScenarios = useMemo(
    () => registry.scenarios.filter((scenario) => scenario.domain_id === selectedDomainId),
    [registry.scenarios, selectedDomainId],
  );
  const selectedDomainStats = useMemo(() => ({
    goals: domainOnlyGoals.length,
    tasks: domainTasks.filter((task) => task.domain_id === selectedDomainId).length,
    scenarios: domainScenarios.length,
    pageStates: selectedDomain?.page_states?.length ?? 0,
  }), [domainOnlyGoals.length, domainScenarios.length, domainTasks, selectedDomain, selectedDomainId]);

  useEffect(() => {
    if (selectedDomainId === "__new") return;
    if (!selectedDomainId && registry.domains[0]?.domain_id) {
      setSelectedDomainId(registry.domains[0].domain_id);
      return;
    }
    if (selectedDomainId && !registry.domains.some((domain) => domain.domain_id === selectedDomainId)) {
      setSelectedDomainId(registry.domains[0]?.domain_id ?? "__new");
    }
  }, [registry.domains, selectedDomainId]);

  useEffect(() => {
    setDomainForm(domainToForm(selectedDomain));
  }, [selectedDomain]);

  useEffect(() => {
    setGoalForm(EMPTY_GOAL);
    setTaskForm(EMPTY_TASK);
    setScenarioForm(EMPTY_SCENARIO);
    setEditingGoalId(null);
    setEditingTaskId(null);
    setEditingScenarioId(null);
  }, [selectedDomainId]);

  const saveDomain = async () => {
    setFormError(null);
    try {
      const payload = {
        domain_id: domainForm.domain_id || slugify(domainForm.display_name),
        display_name: domainForm.display_name,
        host_patterns: splitList(domainForm.host_patterns),
        page_states: textToPageStates(domainForm.page_states),
        capture_defaults: parseJsonField(domainForm.capture_defaults, {}),
        validation_expectations: parseJsonField(domainForm.validation_expectations, []),
        config_version: domainForm.config_version || "v1",
        status: "active",
      };
      const saved = await saveRegistryItem("domains", payload, selectedDomain ? selectedDomain.domain_id : null);
      if (saved?.domain_id) setSelectedDomainId(saved.domain_id);
    } catch (error) {
      setFormError(error.message);
    }
  };

  const resetGoalForm = () => {
    setGoalForm(EMPTY_GOAL);
    setEditingGoalId(null);
  };

  const resetTaskForm = () => {
    setTaskForm(EMPTY_TASK);
    setEditingTaskId(null);
  };

  const resetScenarioForm = () => {
    setScenarioForm(EMPTY_SCENARIO);
    setEditingScenarioId(null);
  };

  const saveGoal = async () => {
    const payload = compactPayload({
      goal_id: editingGoalId ? "" : goalForm.goal_id || slugify(goalForm.display_name),
      domain_id: selectedDomainId,
      display_name: goalForm.display_name,
      action_type_hints: splitList(goalForm.action_type_hints),
      status: "active",
    });
    const saved = await saveRegistryItem("goals", payload, editingGoalId);
    if (saved) {
      resetGoalForm();
    }
  };

  const saveTask = async () => {
    const payload = compactPayload({
      task_id: editingTaskId ? "" : taskForm.task_id || slugify(taskForm.display_name),
      scope_level: taskForm.scope_level,
      domain_id: selectedDomainId,
      goal_id: taskForm.goal_id || null,
      display_name: taskForm.display_name,
      status: "active",
    });
    const saved = await saveRegistryItem("tasks", payload, editingTaskId);
    if (saved) {
      resetTaskForm();
    }
  };

  const saveScenario = async () => {
    const payload = compactPayload({
      scenario_id: editingScenarioId ? "" : scenarioForm.scenario_id || slugify(`${selectedDomainId}_${scenarioForm.display_name}`),
      domain_id: selectedDomainId,
      goal_id: scenarioForm.goal_id,
      task_id: scenarioForm.task_id || null,
      display_name: scenarioForm.display_name,
      start_page_state: scenarioForm.start_page_state || null,
      description: scenarioForm.description || null,
      capture_profile_override: scenarioForm.capture_profile_override || null,
      status: "active",
    });
    const saved = await saveRegistryItem("scenarios", payload, editingScenarioId);
    if (saved) {
      resetScenarioForm();
    }
  };

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Domain Registry</h2>
            <p>Configure domains, goals, tasks, and scenarios used by Session Setup.</p>
          </div>
        </div>

        {(registryStatus?.message || registryStatus?.error || formError) ? (
          <div className={`annotation-message ${registryStatus?.error || formError ? "error" : "success"}`}>
            {registryStatus?.error || formError || registryStatus?.message}
          </div>
        ) : null}

        <div className="domain-config-layout">
          <aside className="domain-list-panel">
            <div className="registry-sidebar-header">
              <span className="field-label">Domains</span>
              <span>{registry.domains.length}</span>
            </div>
            <button
              className={`recent-capture-item ${!selectedDomain ? "active" : ""}`}
              onClick={() => {
                setSelectedDomainId("__new");
                setDomainForm(EMPTY_DOMAIN);
              }}
            >
              <span className="recent-capture-title">New domain</span>
              <span className="recent-capture-meta">Create a domain for future sessions</span>
            </button>
            {registry.domains.map((domain) => (
              <button
                key={domain.domain_id}
                className={`recent-capture-item ${selectedDomainId === domain.domain_id ? "active" : ""}`}
                onClick={() => setSelectedDomainId(domain.domain_id)}
              >
                <span className="recent-capture-title">{domain.display_name}</span>
                <span className="recent-capture-meta">
                  {domain.domain_id} · {registry.scenarios.filter((scenario) => scenario.domain_id === domain.domain_id).length} scenario(s)
                </span>
              </button>
            ))}
          </aside>

          <div className="domain-editor-panel">
            <div className="registry-editor-heading">
              <div>
                <h3>{selectedDomain ? selectedDomain.display_name : "Create Domain"}</h3>
                <p>{selectedDomain ? selectedDomain.domain_id : "Define a new configurable training domain."}</p>
              </div>
              {selectedDomain ? (
                <div className="registry-metrics">
                  <span>{selectedDomainStats.scenarios} scenarios</span>
                  <span>{selectedDomainStats.goals} goals</span>
                  <span>{selectedDomainStats.tasks} tasks</span>
                  <span>{selectedDomainStats.pageStates} states</span>
                </div>
              ) : null}
            </div>
            <div className="domain-form-grid">
              <label>
                <span className="field-label">Domain ID</span>
                <input
                  className="form-input"
                  value={domainForm.domain_id}
                  disabled={!!selectedDomain}
                  placeholder="indeed_jobs"
                  onChange={(event) => setDomainForm((current) => ({ ...current, domain_id: slugify(event.target.value) }))}
                />
              </label>
              <label>
                <span className="field-label">Display Name</span>
                <input
                  className="form-input"
                  value={domainForm.display_name}
                  placeholder="Indeed Jobs"
                  onChange={(event) => setDomainForm((current) => ({ ...current, display_name: event.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Host Patterns</span>
                <textarea
                  className="form-input"
                  rows="3"
                  value={domainForm.host_patterns}
                  placeholder={"indeed.com\nwww.indeed.com"}
                  onChange={(event) => setDomainForm((current) => ({ ...current, host_patterns: event.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Page States</span>
                <textarea
                  className="form-input"
                  rows="3"
                  value={domainForm.page_states}
                  placeholder={"search_results:Search Results\nlogin_wall:Login Wall"}
                  onChange={(event) => setDomainForm((current) => ({ ...current, page_states: event.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Capture Defaults JSON</span>
                <textarea
                  className="form-input mono"
                  rows="5"
                  value={domainForm.capture_defaults}
                  onChange={(event) => setDomainForm((current) => ({ ...current, capture_defaults: event.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Validation Expectations JSON</span>
                <textarea
                  className="form-input mono"
                  rows="5"
                  value={domainForm.validation_expectations}
                  onChange={(event) => setDomainForm((current) => ({ ...current, validation_expectations: event.target.value }))}
                />
              </label>
              <label>
                <span className="field-label">Config Version</span>
                <input
                  className="form-input"
                  value={domainForm.config_version}
                  placeholder="v1"
                  onChange={(event) => setDomainForm((current) => ({ ...current, config_version: event.target.value }))}
                />
              </label>
            </div>

            <div className="detail-actions">
              <button className="primary-btn" disabled={!domainForm.display_name || registryStatus?.loading} onClick={saveDomain}>
                {selectedDomain ? "Save Domain" : "Create Domain"}
              </button>
              {selectedDomain ? (
                <button
                  className="danger-btn"
                  disabled={registryStatus?.loading}
                  onClick={() => {
                    if (confirm(`Archive ${selectedDomain.display_name}?`)) archiveRegistryItem("domains", selectedDomain.domain_id);
                  }}
                >
                  Archive Domain
                </button>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      {selectedDomain ? (
        <>
          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>{editingGoalId ? "Edit Goal" : "Goals"}</h2>
                <p>Domain goals provide action hints and become scenario targets.</p>
              </div>
            </div>
            <div className="crud-form-row">
              <input className="form-input" placeholder="Goal name" value={goalForm.display_name} onChange={(event) => setGoalForm((current) => ({ ...current, display_name: event.target.value }))} />
              <input className="form-input" placeholder="goal_id" value={goalForm.goal_id} disabled={!!editingGoalId} onChange={(event) => setGoalForm((current) => ({ ...current, goal_id: slugify(event.target.value) }))} />
              <input className="form-input" placeholder="click, type" value={goalForm.action_type_hints} onChange={(event) => setGoalForm((current) => ({ ...current, action_type_hints: event.target.value }))} />
              <button className="primary-btn" disabled={!goalForm.display_name || registryStatus?.loading} onClick={saveGoal}>{editingGoalId ? "Save Goal" : "Add Goal"}</button>
              {editingGoalId ? <button className="secondary-btn" onClick={resetGoalForm}>Cancel</button> : null}
            </div>
            <RegistryTable
              columns={["Goal", "ID", "Action Hints", "", ""]}
              rows={domainOnlyGoals.map((goal) => [
                goal.display_name,
                <span className="mono">{goal.goal_id}</span>,
                (goal.action_type_hints ?? []).join(", ") || "-",
                <button className="ghost-btn small-btn" onClick={() => {
                  setEditingGoalId(goal.goal_id);
                  setGoalForm({
                    goal_id: goal.goal_id,
                    display_name: goal.display_name,
                    action_type_hints: (goal.action_type_hints ?? []).join(", "),
                  });
                }}>Edit</button>,
                <button className="table-delete-btn" onClick={() => {
                  if (confirm(`Archive goal ${goal.display_name}? Scenarios using it will also be archived.`)) archiveRegistryItem("goals", goal.goal_id);
                }}>Archive</button>,
              ])}
              empty="No domain-specific goals yet."
            />
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>Tasks</h2>
                <p>Tasks are optional workflow wrappers that scenarios can reference.</p>
              </div>
            </div>
            <div className="crud-form-row task-form-row">
              <input className="form-input" placeholder="Task name" value={taskForm.display_name} onChange={(event) => setTaskForm((current) => ({ ...current, display_name: event.target.value }))} />
              <input className="form-input" placeholder="task_id" value={taskForm.task_id} disabled={!!editingTaskId} onChange={(event) => setTaskForm((current) => ({ ...current, task_id: slugify(event.target.value) }))} />
              <select className="form-select" value={taskForm.scope_level} onChange={(event) => setTaskForm((current) => ({ ...current, scope_level: event.target.value }))}>
                <option value="domain">Domain</option>
                <option value="goal">Goal</option>
                <option value="browser">Browser</option>
              </select>
              <select className="form-select" value={taskForm.goal_id} onChange={(event) => setTaskForm((current) => ({ ...current, goal_id: event.target.value }))}>
                <option value="">No goal binding</option>
                {domainGoals.map((goal) => <option key={goal.goal_id} value={goal.goal_id}>{goal.display_name}</option>)}
              </select>
              <button className="primary-btn" disabled={!taskForm.display_name || registryStatus?.loading} onClick={saveTask}>{editingTaskId ? "Save Task" : "Add Task"}</button>
              {editingTaskId ? <button className="secondary-btn" onClick={resetTaskForm}>Cancel</button> : null}
            </div>
            <RegistryTable
              columns={["Task", "ID", "Scope", "Goal", "", ""]}
              rows={domainTasks.filter((task) => task.domain_id === selectedDomainId).map((task) => [
                task.display_name,
                <span className="mono">{task.task_id}</span>,
                task.scope_level,
                findName(domainGoals, task.goal_id, "goal_id"),
                <button className="ghost-btn small-btn" onClick={() => {
                  setEditingTaskId(task.task_id);
                  setTaskForm({
                    task_id: task.task_id,
                    display_name: task.display_name,
                    scope_level: task.scope_level,
                    goal_id: task.goal_id ?? "",
                  });
                }}>Edit</button>,
                <button className="table-delete-btn" onClick={() => {
                  if (confirm(`Archive task ${task.display_name}? Scenarios using it will keep running without a task binding.`)) archiveRegistryItem("tasks", task.task_id);
                }}>Archive</button>,
              ])}
              empty="No domain-specific tasks yet."
            />
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h2>Scenarios</h2>
                <p>Session Setup uses these scenarios as the primary training entry points.</p>
              </div>
            </div>
            <div className="scenario-form-grid">
              <input className="form-input" placeholder="Scenario name" value={scenarioForm.display_name} onChange={(event) => setScenarioForm((current) => ({ ...current, display_name: event.target.value }))} />
              <input className="form-input" placeholder="scenario_id" value={scenarioForm.scenario_id} disabled={!!editingScenarioId} onChange={(event) => setScenarioForm((current) => ({ ...current, scenario_id: slugify(event.target.value) }))} />
              <select className="form-select" value={scenarioForm.goal_id} onChange={(event) => setScenarioForm((current) => ({ ...current, goal_id: event.target.value }))}>
                <option value="">Select goal</option>
                {domainGoals.map((goal) => <option key={goal.goal_id} value={goal.goal_id}>{goal.display_name}</option>)}
              </select>
              <select className="form-select" value={scenarioForm.task_id} onChange={(event) => setScenarioForm((current) => ({ ...current, task_id: event.target.value }))}>
                <option value="">No task</option>
                {domainTasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.display_name}</option>)}
              </select>
              <select className="form-select" value={scenarioForm.start_page_state} onChange={(event) => setScenarioForm((current) => ({ ...current, start_page_state: event.target.value }))}>
                <option value="">Start page state</option>
                {(selectedDomain.page_states ?? []).map((state) => <option key={state.page_state_id} value={state.page_state_id}>{state.display_name}</option>)}
              </select>
              <select className="form-select" value={scenarioForm.capture_profile_override} onChange={(event) => setScenarioForm((current) => ({ ...current, capture_profile_override: event.target.value }))}>
                <option value="">Default capture profile</option>
                <option value="viewport">Viewport</option>
                <option value="fullpage">Full page</option>
              </select>
              <textarea className="form-input scenario-description-input" rows="2" placeholder="Scenario description" value={scenarioForm.description} onChange={(event) => setScenarioForm((current) => ({ ...current, description: event.target.value }))} />
              <button className="primary-btn" disabled={!scenarioForm.display_name || !scenarioForm.goal_id || registryStatus?.loading} onClick={saveScenario}>{editingScenarioId ? "Save Scenario" : "Add Scenario"}</button>
              {editingScenarioId ? <button className="secondary-btn" onClick={resetScenarioForm}>Cancel</button> : null}
            </div>
            <RegistryTable
              columns={["Scenario", "Goal", "Task", "Start State", "Profile", "", ""]}
              rows={domainScenarios.map((scenario) => [
                <><div>{scenario.display_name}</div><div className="mono table-cell-small">{scenario.scenario_id}</div></>,
                findName(domainGoals, scenario.goal_id, "goal_id"),
                findName(domainTasks, scenario.task_id, "task_id"),
                findName(selectedDomain.page_states ?? [], scenario.start_page_state, "page_state_id"),
                scenario.capture_profile_override ?? "default",
                <button className="ghost-btn small-btn" onClick={() => {
                  setEditingScenarioId(scenario.scenario_id);
                  setScenarioForm({
                    scenario_id: scenario.scenario_id,
                    display_name: scenario.display_name,
                    goal_id: scenario.goal_id,
                    task_id: scenario.task_id ?? "",
                    start_page_state: scenario.start_page_state ?? "",
                    description: scenario.description ?? "",
                    capture_profile_override: scenario.capture_profile_override ?? "",
                  });
                }}>Edit</button>,
                <button className="table-delete-btn" onClick={() => {
                  if (confirm(`Archive scenario ${scenario.display_name}? It will no longer appear in Session Setup.`)) archiveRegistryItem("scenarios", scenario.scenario_id);
                }}>Archive</button>,
              ])}
              empty="No scenarios yet. Add one to make this domain available for targeted sessions."
            />
          </section>
        </>
      ) : null}
    </div>
  );
}

function RegistryTable({ columns, rows, empty }) {
  if (!rows.length) return <div className="empty-state">{empty}</div>;
  return (
    <div className="table-wrap">
      <table className="runs-table">
        <thead>
          <tr>{columns.map((column, index) => <th key={`${column || "action"}-${index}`}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => <td key={cellIndex}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
