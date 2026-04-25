import { useMemo, useState } from "react";

export function ObservationsTable({
  observations,
  title,
  subtitle,
  loading,
  error,
  justCapturedFilename,
  loadObservations,
  onOpenObservation,
  updateObsMeta,
  deleteObservation,
  bulkDeleteObservations,
  emptyMessage,
}) {
  const [obsSelection, setObsSelection] = useState(new Set());
  const [obsGroupFilter, setObsGroupFilter] = useState("");
  const [obsScenarioFilter, setObsScenarioFilter] = useState("");
  const [obsStatusFilter, setObsStatusFilter] = useState("");
  const [obsSearch, setObsSearch] = useState("");

  const groups = useMemo(
    () => [...new Set(observations.map((item) => item.group).filter(Boolean))],
    [observations],
  );
  const customLabels = useMemo(
    () => [...new Set(observations.map((item) => item.label).filter(Boolean))],
    [observations],
  );
  const scenarios = useMemo(
    () => [...new Set(observations.map((item) => item.scenario_id || item.scenario).filter(Boolean))],
    [observations],
  );
  const statusCounts = useMemo(() => {
    const counts = {};
    observations.forEach((item) => {
      const status = item.status || "new";
      counts[status] = (counts[status] || 0) + 1;
    });
    return counts;
  }, [observations]);

  const filtered = useMemo(() => {
    const query = obsSearch.trim().toLowerCase();
    return observations.filter((observation) => {
      if (obsStatusFilter && (observation.status || "new") !== obsStatusFilter) return false;
      if (obsGroupFilter && observation.group !== obsGroupFilter) return false;
      if (obsScenarioFilter && (observation.scenario_id || observation.scenario) !== obsScenarioFilter) return false;
      if (!query) return true;
      const haystack = [
        observation.filename,
        observation.scenario,
        observation.page_url,
        observation.page_title,
        observation.group,
        observation.label,
        observation.status,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [observations, obsGroupFilter, obsScenarioFilter, obsSearch, obsStatusFilter]);

  return (
    <section className="panel obs-list-view">
      <div className="panel-header">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
      </div>

      <div className="obs-toolbar">
        <input
          className="obs-search-input form-input"
          type="text"
          placeholder="Search by URL, title, scenario, label..."
          value={obsSearch}
          onChange={(event) => setObsSearch(event.target.value)}
        />
        <div className="obs-filter-row">
          <div className="obs-filter-chips">
            <button className={`filter-chip ${obsStatusFilter === "" ? "active" : ""}`} onClick={() => setObsStatusFilter("")}>
              All ({observations.length})
            </button>
            {Object.entries(statusCounts).map(([status, count]) => (
              <button
                key={status}
                className={`filter-chip status-${status} ${obsStatusFilter === status ? "active" : ""}`}
                onClick={() => setObsStatusFilter(obsStatusFilter === status ? "" : status)}
              >
                {status} ({count})
              </button>
            ))}
          </div>
          {groups.length > 0 && (
            <select className="obs-group-dropdown form-select" value={obsGroupFilter} onChange={(event) => setObsGroupFilter(event.target.value)}>
              <option value="">All groups</option>
              {groups.map((group) => (
                <option key={group} value={group}>{group}</option>
              ))}
            </select>
          )}
          {scenarios.length > 0 && (
            <select className="obs-group-dropdown form-select" value={obsScenarioFilter} onChange={(event) => setObsScenarioFilter(event.target.value)}>
              <option value="">All scenarios</option>
              {scenarios.map((scenario) => (
                <option key={scenario} value={scenario}>{scenario}</option>
              ))}
            </select>
          )}
          <div className="obs-toolbar-actions">
            {obsSelection.size > 0 && (
              <button
                className="danger-btn small-btn"
                onClick={() => {
                  if (confirm(`Delete ${obsSelection.size} artifact(s)?`)) {
                    bulkDeleteObservations([...obsSelection]);
                    setObsSelection(new Set());
                  }
                }}
              >
                Delete {obsSelection.size}
              </button>
            )}
            <button className="ghost-btn small-btn" onClick={loadObservations}>Refresh</button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="empty-state">Loading...</div>
      ) : error ? (
        <div className="empty-state error">Error: {error}</div>
      ) : filtered.length === 0 ? (
        <div className="empty-state">{observations.length === 0 ? emptyMessage : "No artifacts match your filters."}</div>
      ) : (
        <div className="table-wrap">
          <table className="obs-table">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input
                    className="table-checkbox"
                    type="checkbox"
                    checked={obsSelection.size === filtered.length && filtered.length > 0}
                    onChange={(event) => setObsSelection(event.target.checked ? new Set(filtered.map((item) => item.filename)) : new Set())}
                  />
                </th>
                <th>Status</th>
                <th>Page</th>
                <th>Scenario</th>
                <th>Candidates</th>
                <th>Label</th>
                <th>Group</th>
                <th>Captured</th>
                <th style={{ width: 40 }} />
              </tr>
            </thead>
            <tbody>
              {filtered.map((observation) => (
                <tr
                  key={observation.filename}
                  className={`obs-row ${justCapturedFilename === observation.filename ? "just-captured" : ""} ${obsSelection.has(observation.filename) ? "multi-selected" : ""}`}
                >
                  <td>
                    <input
                      className="table-checkbox"
                      type="checkbox"
                      checked={obsSelection.has(observation.filename)}
                      onChange={(event) => {
                        setObsSelection((previous) => {
                          const next = new Set(previous);
                          if (event.target.checked) next.add(observation.filename);
                          else next.delete(observation.filename);
                          return next;
                        });
                      }}
                    />
                  </td>
                  <td>
                    <select
                      className={`status-select form-select status-${observation.status || "new"}`}
                      value={observation.status || "new"}
                      onChange={(event) => updateObsMeta(observation.filename, { status: event.target.value })}
                    >
                      <option value="draft">Draft</option>
                      <option value="reviewed">Reviewed</option>
                      <option value="approved">Approved</option>
                      <option value="rejected">Rejected</option>
                      <option value="archived">Archived</option>
                    </select>
                  </td>
                  <td className="obs-page-cell" onClick={() => onOpenObservation(observation.filename)} style={{ cursor: "pointer" }}>
                    <div className="obs-page-title">{observation.page_title || "Untitled"}</div>
                    <div className="obs-page-url">{observation.page_url || observation.filename}</div>
                  </td>
                  <td><span className="obs-scenario-tag">{observation.scenario_id || observation.scenario || "live_capture"}</span></td>
                  <td>
                    <span className="inline-badge ok">{observation.candidate_count}</span>
                    {observation.has_screenshot && <span className="inline-badge screenshot-badge" title="Has screenshot">img</span>}
                  </td>
                  <td>
                    <select
                      className="label-select form-select"
                      value={observation.label ?? ""}
                      onChange={(event) => {
                        if (event.target.value === "__new") {
                          const name = prompt("Custom label:");
                          if (name?.trim()) updateObsMeta(observation.filename, { label: name.trim() });
                          event.target.value = observation.label ?? "";
                        } else {
                          updateObsMeta(observation.filename, { label: event.target.value });
                        }
                      }}
                    >
                      <option value="">--</option>
                      <option value="do-not-export">Do not export</option>
                      {customLabels.filter((label) => label !== "do-not-export").map((label) => (
                        <option key={label} value={label}>{label}</option>
                      ))}
                      <option value="__new">+ Custom...</option>
                    </select>
                  </td>
                  <td>
                    <select
                      className="group-select form-select"
                      value={observation.group ?? ""}
                      onChange={(event) => {
                        if (event.target.value === "__new") {
                          const name = prompt("Group name:");
                          if (name?.trim()) updateObsMeta(observation.filename, { group: name.trim() });
                          event.target.value = observation.group ?? "";
                        } else {
                          updateObsMeta(observation.filename, { group: event.target.value });
                        }
                      }}
                    >
                      <option value="">--</option>
                      {groups.map((group) => (
                        <option key={group} value={group}>{group}</option>
                      ))}
                      <option value="__new">+ New...</option>
                    </select>
                  </td>
                  <td className="obs-ts-cell">{observation.timestamp ? new Date(observation.timestamp).toLocaleString() : "-"}</td>
                  <td>
                    <button
                      className="table-delete-btn"
                      title="Delete"
                      onClick={() => {
                        if (confirm("Delete this artifact?")) deleteObservation(observation.filename);
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
