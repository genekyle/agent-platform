export function DomainsSection({ registry }) {
  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Domain Configuration</h2>
            <p>Host rules, allowed goals, scenarios, page states, capture defaults, and validation expectations live here.</p>
          </div>
        </div>

        <div className="summary-stack">
          {registry.domains.map((domain) => {
            const allowedGoals = registry.goals.filter((goal) => goal.domain_id === domain.domain_id || goal.domain_id === null);
            const domainScenarios = registry.scenarios.filter((scenario) => scenario.domain_id === domain.domain_id);
            return (
              <div className="summary-item" key={domain.domain_id}>
                <div className="summary-title">{domain.display_name} · <span className="mono">{domain.domain_id}</span></div>
                <div className="summary-text">Version {domain.config_version} · status {domain.status}</div>
                <div className="summary-text">Host rules: {(domain.host_patterns ?? []).join(", ") || "-"}</div>
                <div className="summary-text">Allowed goals: {allowedGoals.map((goal) => goal.display_name).join(", ") || "-"}</div>
                <div className="summary-text">Page states: {(domain.page_states ?? []).map((item) => item.display_name).join(", ") || "-"}</div>
                <div className="summary-text">Capture defaults: {JSON.stringify(domain.capture_defaults ?? {})}</div>
                <div className="summary-text">Validation expectations: {JSON.stringify(domain.validation_expectations ?? [])}</div>
                <div className="summary-text">Scenarios: {domainScenarios.map((scenario) => scenario.display_name).join(", ") || "-"}</div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Goal Registry</h2>
            <p>Allowed global and domain-scoped goals plus their action hints.</p>
          </div>
        </div>

        <div className="table-wrap">
          <table className="runs-table">
            <thead>
              <tr>
                <th>Goal</th>
                <th>Domain</th>
                <th>Name</th>
                <th>Action Hints</th>
              </tr>
            </thead>
            <tbody>
              {registry.goals.map((goal) => (
                <tr key={goal.goal_id}>
                  <td className="mono">{goal.goal_id}</td>
                  <td className="mono">{goal.domain_id ?? "global"}</td>
                  <td>{goal.display_name}</td>
                  <td>{(goal.action_type_hints ?? []).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Scenario Catalog</h2>
            <p>Operators should start training from scenarios rather than raw task fields.</p>
          </div>
        </div>

        <div className="table-wrap">
          <table className="runs-table">
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Domain</th>
                <th>Goal</th>
                <th>Task</th>
                <th>Start State</th>
                <th>Name</th>
              </tr>
            </thead>
            <tbody>
              {registry.scenarios.map((scenario) => (
                <tr key={scenario.scenario_id}>
                  <td className="mono">{scenario.scenario_id}</td>
                  <td className="mono">{scenario.domain_id}</td>
                  <td className="mono">{scenario.goal_id}</td>
                  <td className="mono">{scenario.task_id ?? "-"}</td>
                  <td>{scenario.start_page_state ?? "-"}</td>
                  <td>{scenario.display_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>Task Catalog</h2>
            <p>Tasks remain available, but scenarios are now the operator-facing training entry point.</p>
          </div>
        </div>

        <div className="table-wrap">
          <table className="runs-table">
            <thead>
              <tr>
                <th>Task</th>
                <th>Scope</th>
                <th>Domain</th>
                <th>Goal</th>
                <th>Name</th>
              </tr>
            </thead>
            <tbody>
              {registry.tasks.map((task) => (
                <tr key={task.task_id}>
                  <td className="mono">{task.task_id}</td>
                  <td>{task.scope_level}</td>
                  <td className="mono">{task.domain_id ?? "-"}</td>
                  <td className="mono">{task.goal_id ?? "-"}</td>
                  <td>{task.display_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
