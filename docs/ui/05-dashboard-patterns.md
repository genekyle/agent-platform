# Dashboard patterns

## Shared dashboard grammar

Every overview uses the same order:

1. **Needs you** — only human decisions, approvals, sign-ins, stop-states, and failures.
2. **Working now** — active agents, current step, elapsed time, and next expected event.
3. **Outcome snapshot** — three to five domain-specific measures.
4. **Flow visual** — the domain’s work moving through a pipeline or sequence.
5. **Recent wins** — completed outcomes in human language.
6. **Quiet context** — settings, goals, automation posture, and learning links.

When a section is empty, collapse it to a concise reassurance rather than leaving a large blank card.

## Global Overview

Primary questions: What needs me across life? What are the agents handling? What finished? Is anything unsafe or offline?

Recommended composition:

- Attention inbox grouped by urgency, not domain card.
- “Working now” strip with one compact row per active run.
- Domain outcome cards showing a meaningful result, trend, and next action.
- Today/this-week activity sparkline or stacked strip: completed, human handoffs, blocked.
- Time-returned estimate, labeled as an estimate and based on transparent rules.
- Recent wins timeline.
- System health becomes one compact footer/status item unless degraded.

Do not show disabled future domains at equal weight with live domains. Put planned work in a small roadmap/coming-soon section.

## Career Search

Primary questions: Which opportunities are moving? Where is a person needed? Which channel/account blocks the next application?

Visuals:

- Pipeline: Found → Shortlisted → Applying → Submitted → Follow-up.
- Current run stepper with verified last step and human gate.
- Applications over seven days sparkline.
- ATS/channel readiness grid for Indeed, LinkedIn, Workday, and Greenhouse.
- Account blockers list with direct action.
- Fit/compensation distribution only after the underlying data is reliable.

Outcome metrics:

- submitted this week;
- waiting for human;
- ready to apply;
- follow-ups due;
- estimated coordination time saved.

Settings—not the overview—owns automation mode, goals, schedule, and detailed search preferences.

## Marketplace

Primary questions: What can be posted? What needs a reply? What sold? What is blocked by platform/account state?

Visuals:

- Pipeline: Draft → Ready → Queued → Live → Conversation → Sold.
- Inventory readiness bar: missing photos/details vs ready.
- Listing performance trend when reliable data exists.
- Conversation response queue ordered by urgency.
- Revenue/sold summary only when the source is authoritative.

Outcome metrics:

- live listings;
- ready to post;
- replies needed;
- sold this period;
- estimated coordination time saved.

## Activity console

Primary questions: What happened, why, and where did it fail?

Use a compact console/table hybrid:

- sticky toolbar with live/pause, session, domain, severity, source, and search;
- bounded initial window with virtualization or pagination;
- rows with timestamp, kind, summary, domain/session, and outcome;
- thin semantic left rule or status icon;
- expandable detail drawer for full rationale, evidence, raw error, API point, and related records;
- session grouping and a “show only this run” action;
- summary strip for the selected window: actions, verified, escalations, failures, cost.

Reasoning remains first-class, but long rationale never expands every row by default.

## Learning

Primary questions: What is the system learning? Where is coverage thin? What is ready to graduate?

Visuals:

- flywheel sequence: Capture → Label → Train → Evaluate → Promote.
- coverage heatmap by domain/state.
- labeling queue and throughput trend.
- quality split: train-eligible vs quarantined with reasons.
- rung mix stacked bar.
- graduation/readiness cards for student models and programs.
- reasoning coverage and teacher/backstop agreement trend.

Primary actions: Label next, start capture session, review coverage gap, inspect promotion blocker.

Advanced tools—model test, registry, eval runs, run detail, movement playground, raw state graph—live behind an Advanced tab or section.

## System

Primary questions: Can agents work safely right now? What is degraded? Are cost and storage within bounds?

Visuals:

- service health list with compact status history.
- topology diagram for Control API → Capture → Browser → Storage, with current health on nodes.
- autonomous-spend guardrail with current week usage and remaining budget.
- connection/account readiness grouped by provider.
- storage/corpus growth trend if data is available.

System diagnostics can use monospaced targets and errors, but the default row leads with human impact: “Training capture unavailable” before the refused socket detail.
