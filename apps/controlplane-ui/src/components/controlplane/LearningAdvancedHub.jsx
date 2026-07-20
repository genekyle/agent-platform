import { AppIcon } from "../../ui/Icon";

const TOOLS = [
  { id: "session-capture", title: "Session capture", detail: "Capture from the active training browser.", icon: "inspect" },
  { id: "dataset-browser", title: "Dataset browser", detail: "Browse and curate captured artifacts.", icon: "database" },
  { id: "review-label", title: "Capture inspector", detail: "Inspect a screenshot, proposals, and candidates.", icon: "search" },
  { id: "page-states", title: "Page states", detail: "Manage the state taxonomy and scope.", icon: "listTree" },
  { id: "state-graph", title: "State graph", detail: "Explore intended and observed transitions.", icon: "network" },
  { id: "test", title: "Model test", detail: "Run the SELECT cascade against one capture.", icon: "flask" },
  { id: "eval", title: "Select metrics", detail: "Inspect cache, escalation, and cost behavior.", icon: "chart" },
  { id: "visualization", title: "Corpus visualization", detail: "Explore cost, layer mix, and reason codes.", icon: "chart" },
  { id: "eval-runs", title: "Evaluation runs", detail: "Browse model evaluations by recency.", icon: "listFilter" },
  { id: "playground", title: "Movement playground", detail: "Compare recorded and generated pointer paths.", icon: "route" },
  { id: "export-model-prep", title: "Export and model prep", detail: "Export reviewed data for model work.", icon: "archive" },
  { id: "domains", title: "Training registry", detail: "Manage domains, goals, tasks, and scenarios.", icon: "settings" },
];

export function LearningAdvancedHub({ onOpen }) {
  return (
    <section className="panel advanced-tools-panel">
      <div className="panel-header">
        <div>
          <h2>Advanced tools</h2>
          <p>Engineering and corpus utilities remain available without crowding the daily learning flow.</p>
        </div>
      </div>
      <div className="advanced-tools-grid">
        {TOOLS.map((tool) => (
          <button key={tool.id} className="advanced-tool" onClick={() => onOpen(tool.id)}>
            <span className="advanced-tool__icon"><AppIcon name={tool.icon} /></span>
            <span><strong>{tool.title}</strong><small>{tool.detail}</small></span>
            <AppIcon name="chevronRight" size={16} />
          </button>
        ))}
      </div>
    </section>
  );
}

