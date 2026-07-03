// Top-level nav: Command Center · Domains · Training · Lab · System.
// Domains is a HUB — its secondary rail lists the domain workspaces (Facebook Marketplace,
// Indeed, …) instead of static sections, so "Selling" and "Indeed" no longer sit loose in the
// nav. Folded in: registry Domains → Training; Models → Lab. Hidden (components kept in code,
// off the menu): Workers, Chat.
export const CONTROL_PLANE_NAV = {
  command: {
    label: "Command Center",
    title: "Command Center",
    subtitle: "What needs you across every domain, health at a glance, and what just happened.",
    sections: [],
  },
  domains: {
    label: "Domains",
    title: "Domains",
    subtitle: "Every domain the agents work — pick one to open its workspace.",
    // sections are the domain workspaces themselves, rendered from the domain catalog.
    sections: [],
  },
  training: {
    label: "Training",
    title: "Training",
    subtitle: "Structured training sessions, capture, review, registry, and model-prep.",
    sections: [
      {
        id: "session-setup",
        label: "Session Setup",
        subtitle: "Create a structured training session with controlled domain, goal, and task context.",
      },
      {
        id: "session-capture",
        label: "Session Capture",
        subtitle: "Capture only from the active training session Chrome instance.",
      },
      {
        id: "coverage",
        label: "Coverage",
        subtitle: "Per-page-state capture coverage for the active session — drive to the gaps.",
      },
      {
        id: "dataset-browser",
        label: "Dataset Browser",
        subtitle: "Browse captured artifacts, curate metadata, and select records for review.",
      },
      {
        id: "page-states",
        label: "Page States",
        subtitle: "Organize the state taxonomy: global, per-domain, and per-scenario states by category.",
      },
      {
        id: "review-label",
        label: "Review / Label",
        subtitle: "Inspect the screenshot, proposals, and candidate set for one selected artifact.",
      },
      {
        id: "export-model-prep",
        label: "Export / Model Prep",
        subtitle: "Export reviewed labels from the current artifact and stage model-prep work.",
      },
      {
        id: "domains",
        label: "Domains",
        subtitle: "Registry: domains, allowed goals, scoped tasks, and scenarios used by sessions.",
      },
    ],
  },
  lab: {
    label: "Lab",
    title: "Lab",
    subtitle: "Input-model playground, the SELECT-stage flywheel, and grounding models.",
    sections: [
      { id: "playground", label: "Movement Playground", subtitle: "Record real cursor paths, compare against generated motion, and grow the input-model corpus." },
      { id: "test", label: "Model Test", subtitle: "Run the live SELECT cascade against a capture + goal." },
      { id: "eval", label: "Select Metrics", subtitle: "Flywheel metrics: cache-hit rate, escalation rate, cost-per-task." },
      { id: "training-space", label: "Training Space", subtitle: "Keyboard-driven AX confirm/correct — turn the model's picks into golden labels." },
      { id: "scorecard", label: "Corpus Scorecard", subtitle: "The quality gate: train-eligible vs quarantined states, by confidence/verify/human-review." },
      { id: "state-graph", label: "State Graph", subtitle: "The agent's map of the world: page-states as nodes, transitions as edges (intended vs observed)." },
      { id: "visualization", label: "Visualization", subtitle: "Cost/day, selections/day, layer mix, and reason codes over the corpus." },
      { id: "models", label: "Models", subtitle: "Grounding models registered against each training target, with last-eval summary." },
      { id: "eval-runs", label: "Eval Runs", subtitle: "Recent eval runs across all models, ordered by recency." },
      { id: "run-detail", label: "Run Detail", subtitle: "Per-scenario metrics and a sample of predictions for one eval run." },
    ],
  },
  system: {
    label: "System",
    title: "System",
    subtitle: "Operational readiness, service topology, and training prerequisites.",
    sections: [
      {
        id: "status",
        label: "Status",
        subtitle: "Live health checks for APIs, browser connectivity, storage, and infrastructure.",
      },
      {
        id: "topology",
        label: "Topology",
        subtitle: "How the control plane, capture flow, browser, and storage fit together.",
      },
      {
        id: "training-readiness",
        label: "Training Readiness",
        subtitle: "Gate model-training work on the dependencies that must be online first.",
      },
      {
        id: "api-usage",
        label: "API Usage",
        subtitle: "Claude API spend and token usage, tagged by purpose, with links to the Anthropic Console.",
      },
    ],
  },
};

export const DEFAULT_SECTION_VIEW = Object.fromEntries(
  Object.entries(CONTROL_PLANE_NAV).map(([key, value]) => [key, value.sections[0]?.id]),
);
