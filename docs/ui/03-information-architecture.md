# Information architecture

## Global navigation

The global sidebar remains stable on every route:

```text
AI Ops
├─ Overview
├─ Domains
├─ Activity
├─ Learning
└─ System
```

The sidebar contains icon + label only. On wider screens it can expand to approximately `220px`; a compact `64px` mode is optional after the full navigation is stable. Descriptions do not appear in navigation items.

“Command Center” becomes **Overview**. “Training” and the product-facing parts of “Lab” become **Learning**. Advanced controller/model/corpus utilities remain reachable inside Learning, but “Lab” disappears from primary navigation.

The API URL moves out of the sidebar footer. Connection state belongs in System and a compact global status control.

## Route model

The first implementation pass should establish real routes:

```text
/
/domains
/domains/:domainId
/domains/:domainId/:tabId
/activity
/learning
/learning/label
/learning/coverage
/learning/sessions
/learning/models
/learning/advanced/:toolId
/system
/system/services
/system/connections
/system/usage
```

Routes make page refresh, browser history, direct links, and test setup deterministic. Invalid or unavailable routes render an honest local state rather than silently falling back to Overview.

## Domain structure

Domains are user outcomes, not every website the agent encounters.

Top-level domains for the current product:

- Career Search
- Marketplace
- Google errands (when the workspace is ready)
- Future daily-life domains as they become operational

Indeed, LinkedIn, Workday, and Greenhouse are channels or execution surfaces inside Career Search. They should not occupy the global sidebar. Career Search can show them in a **Channels** or **Connections** area with readiness, account state, training coverage, and recent use.

Google is a provider/connection group. Gmail can be an errand surface inside a future Communications or Google domain, but an unfinished provider should not appear as a disabled primary domain card beside live work.

## Domain workspace navigation

Every domain uses the same local frame:

```text
AI Ops / Domains / Career Search

Career Search                              [Manual ▾] [Run]
Jobs found, applications, and handoffs

Overview  Work  Accounts  Activity  Learning  Settings
```

Local tabs are horizontal under the page header on desktop and become an overflowable tab row or a labeled select on narrow screens. They never replace the global sidebar.

Recommended shared tabs:

- **Overview** — outcomes, attention, current work, pipeline, and recent wins.
- **Work** — the domain’s primary objects and queues: jobs/applications or inventory/listings.
- **Accounts** — only when the domain requires accounts.
- **Activity** — domain-filtered activity.
- **Learning** — coverage and readiness scoped to the domain.
- **Settings** — automation posture, goals, schedules, and connections.

Domain-specific secondary tabs can exist inside Work, not in global navigation.

## Page header

The page header has one title, one concise state line, breadcrumbs when nested, and no repeated hero card. Global connection health becomes a small status control, not a large green pill on every page.

Primary actions are contextual:

- Overview: “Review 3 items” or “Run now.”
- Career Search: “Start search.”
- Marketplace: “Add item” or “Post queue.”
- Activity: “Pause live feed.”
- Learning: “Label next.”
- System: “Run checks.”

Generic “Refresh” is available through a compact icon button, keyboard shortcut, or contextual action. Do not repeat it in multiple panels.

## Narrow-screen behavior

At tablet widths, global navigation becomes a persistent compact rail or an accessible menu button with a labeled drawer. The current behavior—removing navigation entirely—is not acceptable.

At mobile widths, the product prioritizes review, attention, activity, and approval. Dense authoring tools such as the capture inspector may explicitly require a larger viewport, but that state must explain the limitation and preserve navigation.
