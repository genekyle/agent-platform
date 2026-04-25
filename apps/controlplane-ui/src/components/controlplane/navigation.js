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
    subtitle: "Structured training sessions, capture, review, and model-prep for grounding data.",
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
        id: "dataset-browser",
        label: "Dataset Browser",
        subtitle: "Browse captured artifacts, curate metadata, and select records for review.",
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
    ],
  },
  workers: {
    label: "Workers",
    title: "Workers",
    subtitle: "Operational execution surfaces for workers, runs, and raw observer debugging.",
    sections: [
      {
        id: "runs",
        label: "Runs",
        subtitle: "Monitor execution history, run state, and step progress.",
      },
      {
        id: "worker-health",
        label: "Worker Health",
        subtitle: "Track worker status, seat assignments, and execution readiness.",
      },
      {
        id: "worker-observations",
        label: "Worker Observations",
        subtitle: "Inspect raw observer artifacts used for worker debugging and grounding analysis.",
      },
    ],
  },
  chat: {
    label: "Chat",
    title: "Chat",
    subtitle: "Command-center entry point for intent-driven task orchestration.",
    sections: [
      {
        id: "command-center",
        label: "Command Center",
        subtitle: "Translate operator intent into future domain workflows and task plans.",
      },
    ],
  },
  domains: {
    label: "Domains",
    title: "Domains",
    subtitle: "Configure domains, goals, tasks, and scenarios used by training sessions.",
    sections: [
      {
        id: "registry",
        label: "Registry",
        subtitle: "CRUD configuration for training domains, allowed goals, scoped tasks, and scenarios.",
      },
    ],
  },
};

export const DEFAULT_SECTION_VIEW = Object.fromEntries(
  Object.entries(CONTROL_PLANE_NAV).map(([key, value]) => [key, value.sections[0]?.id]),
);
