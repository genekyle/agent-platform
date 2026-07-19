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
  activity: {
    label: "🩺 Activity",
    title: "Session Activity",
    subtitle: "One live timeline of what the system is doing and WHY — reasoning, actions, escalations, errors, API touches.",
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
    subtitle: "The flywheel: collect → label → train. Label is the crank.",
    sections: [
      // Organized around the loop, most-used first. Label (the queue crank) leads — it used to be
      // buried in Lab while the Dataset Browser dig masqueraded as the labeler.
      {
        id: "label",
        label: "🏷️ Label",
        subtitle: "The queue crank: confirm/correct the model's pick, Save, auto-advance to the next.",
      },
      {
        id: "coverage",
        label: "Coverage",
        subtitle: "Per-page-state capture coverage — drive to the gaps.",
      },
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
        id: "dataset-browser",
        label: "Dataset Browser",
        subtitle: "Browse + curate captured artifacts (metadata, status). For labeling, use Label.",
      },
      {
        id: "review-label",
        label: "Inspect capture",
        subtitle: "Deep-dive one selected artifact: screenshot, proposals, and candidate set.",
      },
      {
        id: "page-states",
        label: "Page States",
        subtitle: "Organize the state taxonomy: global, per-domain, and per-scenario states by category.",
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
    subtitle: "The decide-stage reasoner, input-model playground, the SELECT-stage flywheel, and grounding models.",
    sections: [
      { id: "controller", label: "🧠 Controller", subtitle: "The teachable decide(): observe → decide → act. Watch it reason on a tab, the rung mix, the intent programs, and the decision corpus." },
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
      {
        id: "workday-accounts",
        label: "Workday Accounts",
        subtitle: "Per-employer Workday/ATS logins for cross-site applications — encrypted into the local vault.",
      },
    ],
  },
};

export const DEFAULT_SECTION_VIEW = Object.fromEntries(
  Object.entries(CONTROL_PLANE_NAV).map(([key, value]) => [key, value.sections[0]?.id]),
);
