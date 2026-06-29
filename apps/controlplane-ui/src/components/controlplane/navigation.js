// Top-level nav, slimmed to 4 keys: Home · Training · Lab · System.
// Folded in: Domains → Training; Models → Lab. Hidden for now (components kept
// in code, just off the menu): Workers, Chat — restore by re-adding entries here.
export const CONTROL_PLANE_NAV = {
  home: {
    label: "Home",
    title: "Home",
    subtitle: "Platform overview, health, and operating posture.",
    sections: [
      {
        id: "overview",
        label: "Overview",
        subtitle: "Top-level platform summary and current operating posture.",
      },
      {
        id: "system-status",
        label: "System Status",
        subtitle: "API connectivity, environment status, and readiness signals.",
      },
    ],
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
  indeed: {
    label: "Indeed",
    title: "Indeed Workspace",
    subtitle: "The job-seeking workspace: stored application answers, coverage, and (soon) the jobs dashboard.",
    sections: [
      {
        id: "indeed-overview",
        label: "Overview",
        subtitle: "Indeed workspace at a glance: states covered, stored answers, capture totals.",
      },
      {
        id: "application-answers",
        label: "Application Profile",
        subtitle: "Your reusable autofill profile — identity/EEO, eligibility, compensation, logistics, and acknowledgments. Editable.",
      },
      {
        id: "jobs-dashboard",
        label: "Jobs Dashboard",
        subtitle: "Jobs found across searches — counts, duplicates, and applied status. Drives what to apply to next.",
      },
      {
        id: "apply-state",
        label: "Apply State",
        subtitle: "The live apply blackboard: where we are in the recipe, per-field form state, the code-enforced submit gate, and blockers — so nobody holds tab/step/field state in their head.",
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
