const STATUS_COPY = {
  healthy: "Healthy",
  degraded: "Degraded",
  down: "Down",
  unknown: "Unknown",
};

const TOPOLOGY_NODES = [
  {
    name: "Controlplane UI",
    target: "localhost:5173",
    detail: "React/Vite operator surface using a single backend summary endpoint.",
  },
  {
    name: "Control Plane API",
    target: "VITE_API_BASE_URL",
    detail: "Primary FastAPI service for runs, workers, training prep, and capture orchestration.",
  },
  {
    name: "Capture Server",
    target: "localhost:8082",
    detail: "Secondary FastAPI service that wraps browser capture and writes artifacts.",
  },
  {
    name: "Chrome CDP",
    target: "localhost:9222",
    detail: "Remote-debug Chrome target used to enumerate tabs and drive observation capture.",
  },
  {
    name: "Postgres",
    target: "settings.database_url",
    detail: "Primary persisted state for runs, steps, and worker metadata.",
  },
  {
    name: "Artifacts",
    target: "settings.observer_artifacts_dir",
    detail: "Observer traces, screenshots, and training prep outputs.",
  },
  {
    name: "Redis",
    target: "settings.redis_url",
    detail: "Available infra dependency for fast coordination and queue-style workflows.",
  },
];

const TOPOLOGY_LINKS = [
  "Controlplane UI -> Control Plane API",
  "Control Plane API -> Capture Server",
  "Control Plane API -> Chrome CDP",
  "Control Plane API -> Postgres",
  "Control Plane API -> Artifacts",
  "Control Plane API -> Redis",
];

function badgeClass(status) {
  return `inline-badge status-${status ?? "unknown"}`;
}

function formatTimestamp(value) {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Unknown";
  return parsed.toLocaleString();
}

function statusLabel(status) {
  return STATUS_COPY[status] ?? "Unknown";
}

export function SystemSection({
  section,
  systemStatus,
  loadSystemStatus,
}) {
  const services = systemStatus.data?.services ?? [];
  const generatedAt = systemStatus.data?.generated_at;
  const overallStatus = systemStatus.data?.overall_status ?? "unknown";
  const requiredServices = services.filter((service) => service.required_for_training);
  const readinessFailures = requiredServices.filter((service) => service.status !== "healthy");

  if (section === "topology") {
    return (
      <div className="section-stack">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>System Topology</h2>
              <p>Stable map of the runtime pieces that need to line up for capture and training workflows.</p>
            </div>
          </div>

          <div className="topology-flow">
            {TOPOLOGY_LINKS.map((link) => (
              <div key={link} className="topology-link">{link}</div>
            ))}
          </div>
        </section>

        <section className="system-card-grid">
          {TOPOLOGY_NODES.map((node) => (
            <article key={node.name} className="system-card topology-card">
              <div className="system-card-header">
                <h3>{node.name}</h3>
                <span className="system-card-target mono">{node.target}</span>
              </div>
              <p className="system-card-copy">{node.detail}</p>
            </article>
          ))}
        </section>
      </div>
    );
  }

  if (section === "training-readiness") {
    return (
      <div className="section-stack">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Training Readiness</h2>
              <p>Required checks for starting model-training and capture-heavy review work.</p>
            </div>
            <div className="controller-actions">
              <span className={badgeClass(overallStatus)}>{statusLabel(overallStatus)}</span>
              <button className="ghost-btn small-btn" onClick={loadSystemStatus} disabled={systemStatus.loading}>
                {systemStatus.loading ? "Refreshing..." : "Refresh Checks"}
              </button>
            </div>
          </div>

          <div className="readiness-grid">
            {requiredServices.map((service) => (
              <div key={service.id} className="readiness-item">
                <div>
                  <div className="readiness-title">{service.label}</div>
                  <div className="readiness-copy">{service.message}</div>
                </div>
                <span className={badgeClass(service.status)}>{statusLabel(service.status)}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h2>Gate Summary</h2>
              <p>Training can start only when every required dependency is green.</p>
            </div>
          </div>

          {systemStatus.error ? (
            <div className="empty-state error">{systemStatus.error}</div>
          ) : readinessFailures.length === 0 ? (
            <div className="system-summary ok">
              All required dependencies are healthy. The capture and training path is ready.
            </div>
          ) : (
            <div className="system-summary bad">
              Training is blocked by {readinessFailures.length} failing prerequisite{readinessFailures.length !== 1 ? "s" : ""}.
            </div>
          )}

          <div className="system-failure-list">
            {readinessFailures.map((service) => (
              <div key={service.id} className="failure-item">
                <div className="failure-title">{service.label}</div>
                <div className="failure-copy">{service.message}</div>
                <div className="failure-target mono">{service.endpoint_or_target}</div>
              </div>
            ))}
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="section-stack">
      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>System Status</h2>
            <p>Single-source operational snapshot for API, browser, storage, and infra readiness.</p>
          </div>
          <div className="controller-actions">
            <span className={badgeClass(overallStatus)}>{statusLabel(overallStatus)}</span>
            <button className="ghost-btn small-btn" onClick={loadSystemStatus} disabled={systemStatus.loading}>
              {systemStatus.loading ? "Refreshing..." : "Refresh Checks"}
            </button>
          </div>
        </div>

        <div className="status-stack">
          <div className="status-row">
            <span className="status-key">Overall Status</span>
            <span className={badgeClass(overallStatus)}>{statusLabel(overallStatus)}</span>
          </div>
          <div className="status-row">
            <span className="status-key">Last Checked</span>
            <span className="status-value">{formatTimestamp(generatedAt)}</span>
          </div>
          <div className="status-row">
            <span className="status-key">Required Training Services</span>
            <span className="status-value">{requiredServices.length}</span>
          </div>
        </div>

        {systemStatus.error && <div className="empty-state error">{systemStatus.error}</div>}
      </section>

      <section className="system-table-panel">
        <div className="system-table system-table-head">
          <span>Service</span>
          <span>Status</span>
          <span>Target</span>
          <span>Why It Matters</span>
          <span>Failure Detail</span>
        </div>

        {services.map((service) => (
          <article key={service.id} className="system-table system-table-row">
            <div className="system-service-title">
              <span>{service.label}</span>
              {service.required_for_training && <span className="training-flag">Training Critical</span>}
            </div>
            <div>
              <span className={badgeClass(service.status)}>{statusLabel(service.status)}</span>
            </div>
            <div className="mono system-cell-target">{service.endpoint_or_target}</div>
            <div className="system-cell-copy">
              {service.kind === "api" && "Drives application coordination and capture workflows."}
              {service.kind === "browser" && "Required for tab discovery and live page capture."}
              {service.kind === "database" && "Stores operational state and training metadata."}
              {service.kind === "storage" && "Holds artifacts used for review and model prep."}
              {service.kind === "cache" && "Supports low-latency coordination and future queue flows."}
            </div>
            <div className="system-cell-detail">
              <div>{service.message}</div>
              {typeof service.latency_ms === "number" && (
                <div className="system-micro-copy">Latency {Math.round(service.latency_ms)} ms</div>
              )}
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
