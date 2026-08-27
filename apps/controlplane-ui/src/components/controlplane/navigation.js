// AI Ops navigation is split into a stable global rail and local section tabs.
// Product navigation is intentionally short; advanced implementation tools remain reachable
// inside Learning without competing with the daily operator workflow.
export const CONTROL_PLANE_NAV = {
  command: {
    label: "Overview",
    title: "Overview",
    subtitle: "What needs you, what is working, and what your agents finished.",
    sections: [],
  },
  cockpit: {
    label: "Cockpit",
    title: "Cockpit",
    subtitle: "Operate the current moment, inspect local perception, and trace what happened.",
    sections: [],
  },
  domains: {
    label: "Domains",
    title: "Domains",
    subtitle: "The parts of daily life your agents coordinate.",
    sections: [],
  },
  activity: {
    label: "Activity",
    title: "Activity",
    subtitle: "A live, inspectable record of actions, reasoning, handoffs, and failures.",
    sections: [],
  },
  learning: {
    label: "Learning",
    title: "Learning",
    subtitle: "Capture, label, evaluate, and graduate repeatable work into cheaper local intelligence.",
    sections: [
      { id: "overview", label: "Overview", subtitle: "Learning health, flow, coverage, and graduation readiness." },
      { id: "label", label: "Label", subtitle: "Confirm or correct the next queued training example." },
      { id: "queue", label: "Queue", subtitle: "The teacher's ranked worklist — the transition rows self-supervision cannot claim." },
      { id: "naming", label: "Naming", subtitle: "Screens we keep meeting without really knowing their name — ranked, with the evidence." },
      { id: "session-scorecard", label: "Scorecard", subtitle: "Rows banked, labels written, parks answered, and the road to the promotion gate." },
      { id: "coverage", label: "Coverage", subtitle: "See which page states need more examples." },
      { id: "session-setup", label: "Sessions", subtitle: "Create and manage structured capture sessions." },
      { id: "session-capture", label: "Capture", subtitle: "Capture from the active session browser." },
      { id: "dataset-browser", label: "Dataset", subtitle: "Browse and curate captured artifacts." },
      { id: "review-label", label: "Inspect", subtitle: "Inspect one capture, its proposals, and candidate set." },
      { id: "page-states", label: "Page States", subtitle: "Manage the page-state taxonomy." },
      { id: "world-facts", label: "World Facts", subtitle: "Dated claims about sites, ranked by how far the world has been driven past them." },
      { id: "transitions", label: "Transitions", subtitle: "Review each step's thinking: believed → predicted → did → saw → settled; correct verdicts." },
      { id: "controller", label: "Controller", subtitle: "Inspect the teachable decision layer and reasoning feed." },
      { id: "models", label: "Models", subtitle: "View registered models and their latest evaluation." },
      { id: "scorecard", label: "Corpus", subtitle: "Review train-eligible and quarantined examples." },
      { id: "advanced", label: "Advanced", subtitle: "Engineering, model, and corpus utilities." },
      { id: "state-graph", label: "State Graph", subtitle: "Explore learned states and transitions." },
      { id: "playground", label: "Movement", subtitle: "Compare recorded and generated pointer motion." },
      { id: "test", label: "Model Test", subtitle: "Run the SELECT cascade against a capture and goal." },
      { id: "eval", label: "Select Metrics", subtitle: "Inspect cache, escalation, and cost metrics." },
      { id: "visualization", label: "Visualization", subtitle: "Explore cost, layer mix, and reason codes." },
      { id: "eval-runs", label: "Eval Runs", subtitle: "Browse recent evaluation runs." },
      { id: "run-detail", label: "Run Detail", subtitle: "Inspect one evaluation run in detail." },
      { id: "training-space", label: "Legacy Labeler", subtitle: "Open the older AX correction surface." },
      { id: "export-model-prep", label: "Export", subtitle: "Export reviewed labels and prepare model data." },
      { id: "domains", label: "Registry", subtitle: "Manage training domains, goals, tasks, and scenarios." },
    ],
  },
  system: {
    label: "System",
    title: "System",
    subtitle: "Service health, connections, usage, and operational readiness.",
    sections: [
      { id: "status", label: "Services", subtitle: "Live health checks and their human impact." },
      { id: "topology", label: "Topology", subtitle: "How the control API, capture flow, browser, and storage connect." },
      { id: "training-readiness", label: "Readiness", subtitle: "Dependencies required for capture and learning." },
      { id: "api-usage", label: "Usage", subtitle: "Model spend and token usage by purpose." },
      { id: "workday-accounts", label: "Connections", subtitle: "Encrypted accounts used by cross-site applications." },
    ],
  },
};

export const LEARNING_PRIMARY_TABS = [
  { id: "overview", label: "Overview" },
  { id: "label", label: "Label" },
  { id: "queue", label: "Queue" },
  { id: "naming", label: "Naming" },
  { id: "session-scorecard", label: "Scorecard" },
  { id: "transitions", label: "Transitions" },
  { id: "coverage", label: "Coverage" },
  { id: "session-setup", label: "Sessions" },
  { id: "controller", label: "Controller" },
  { id: "models", label: "Models" },
  { id: "scorecard", label: "Corpus" },
  { id: "advanced", label: "Advanced" },
];

export const LEARNING_ADVANCED_IDS = new Set([
  "session-capture",
  "dataset-browser",
  "review-label",
  "page-states",
  "world-facts",
  "state-graph",
  "playground",
  "test",
  "eval",
  "visualization",
  "eval-runs",
  "run-detail",
  "training-space",
  "export-model-prep",
  "domains",
]);

export const SYSTEM_TABS = CONTROL_PLANE_NAV.system.sections.map(({ id, label }) => ({ id, label }));

export const DEFAULT_SECTION_VIEW = Object.fromEntries(
  Object.entries(CONTROL_PLANE_NAV).map(([key, value]) => [key, value.sections[0]?.id]),
);
