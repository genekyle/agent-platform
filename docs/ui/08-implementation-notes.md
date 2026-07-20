# First implementation pass

Date: 2026-07-19

## Outcome

The UI now presents one product—**AI Ops**—with a stable global model:

1. Overview
2. Domains
3. Activity
4. Learning
5. System

The old transforming sidebar is gone. Domain and section navigation live in the content hierarchy, URLs are refresh-safe, and narrow screens use a complete navigation drawer instead of hiding navigation.

## Implemented by concern

### Product shell and navigation

- Renamed the shell and browser title to AI Ops.
- Added real browser routes with `react-router-dom`.
- Added stable global navigation, domain breadcrumbs, page headings, local section tabs, responsive drawer/scrim, global connection status, refresh, and the browser drive-lock banner.
- Consolidated Training and Lab into Learning. Product-facing tools are primary tabs; engineering/corpus utilities live behind Advanced.

Owned by:

- `apps/controlplane-ui/src/app/AppShell.jsx`
- `apps/controlplane-ui/src/app/routes.js`
- `apps/controlplane-ui/src/components/controlplane/navigation.js`
- `apps/controlplane-ui/src/ui/SectionTabs.jsx`

### Design system

- Replaced the blue/light administration theme with warm graphite surfaces and restrained sage, amber, coral, plum, and human-gold accents.
- Added a single monochrome Lucide icon resolver. Icons inherit `currentColor`; the domain catalog no longer stores emoji.
- Removed rendered emoji and known blue/cyan interface values from the UI source.
- Added coherent focus, hover, status, reduced-motion, desktop, tablet, and mobile behavior.

Owned by:

- `apps/controlplane-ui/src/styles/tokens.css`
- `apps/controlplane-ui/src/styles/ai-ops.css`
- `apps/controlplane-ui/src/ui/Icon.jsx`

### Overview

- Reframed the landing page as a daily briefing: human attention, agent presence, readiness, learning queue, latest outcomes, and domain state.
- Kept the interface calm when nothing needs the operator instead of manufacturing urgency.
- Made domain cards communicate identity, primary work, supporting signals, readiness, and handoffs consistently.

Owned by:

- `apps/controlplane-ui/src/components/controlplane/workspace/CommandCenter.jsx`
- `apps/controlplane-ui/src/components/controlplane/workspace/DomainsHub.jsx`
- `apps/controlplane-ui/src/components/controlplane/workspace/AttentionInbox.jsx`
- `apps/controlplane-ui/src/components/controlplane/workspace/ActivityFeed.jsx`

### Activity console

- Rebuilt Activity as a bounded console with compact filters, live/paused state, search, semantic event rails, monospaced operational content, and a persistent inspector.
- Reasoning uses plum, actions sage, handoffs amber, and errors coral. Color is reinforced by text labels.
- Long details and metadata remain inspectable without expanding every row.

Owned by:

- `apps/controlplane-ui/src/components/controlplane/SessionActivitySection.jsx`

### Domains

- Separated live domains, provider ecosystems, and planned domains instead of presenting every item as an equal disabled card.
- Removed the duplicate domain hero; the shell breadcrumb and heading are the single identity source.
- Kept Overview, Goals, Tasks, Attention, Activity, Automation, Accounts, Training, and domain-specific data tabs reachable through URL-backed local tabs.

Owned by:

- `apps/controlplane-ui/src/components/controlplane/workspace/DomainsHub.jsx`
- `apps/controlplane-ui/src/components/controlplane/workspace/DomainWorkspace.jsx`
- `apps/controlplane-ui/src/components/controlplane/workspace/domains.js`

### Learning

- Added a Learning overview that makes the loop explicit: Capture → Label → Train → Evaluate → Promote.
- Added queue, labeled corpus, grounding accuracy, intent programs, verified decisions, and reasoning-coverage metrics.
- Added an Advanced hub so model tests, state graph, dataset browser, movement, evaluation, export, and registries remain available without crowding daily work.
- Migrated legacy learning visual semantics away from emoji and blue while preserving behavior and backend contracts.

Owned by:

- `apps/controlplane-ui/src/components/controlplane/LearningOverview.jsx`
- `apps/controlplane-ui/src/components/controlplane/LearningAdvancedHub.jsx`
- existing learning components reached through the Learning route boundary.

### System

- Consolidated Services, Topology, Readiness, Usage, and Connections under scroll-safe local tabs.
- Replaced the topology link list with an actual relationship map: AI Ops UI → Control Plane API → browser, capture, storage, database, and coordination dependencies.
- Preserved detailed targets and failure information below the visual map.

Owned by:

- `apps/controlplane-ui/src/components/controlplane/SystemSection.jsx`
- `apps/controlplane-ui/src/components/controlplane/ApiUsageSection.jsx`
- `apps/controlplane-ui/src/components/controlplane/workspace/WorkdayAccountsPanel.jsx`

## Validation performed

- Production build passes with Vite.
- Browser route checks passed for Overview, Domains, a live domain workspace, Activity, Learning overview, Learning labeler, and System topology.
- No runtime console errors were observed on the checked reference routes.
- Tablet validation passed at 820 × 900, including opening and closing the complete navigation drawer.
- Mobile Learning validation passed at 390 × 844, including horizontal local tabs and single-column metric cards.
- The explicit viewport override was reset after testing.
- Source audits found no `Extended_Pictographic` emoji in `apps/controlplane-ui/src` and no remaining values from the retired blue/cyan palette list.
- A follow-up contrast sweep corrected legacy light attention cards and dark System text that bypassed the new tokens. Automated checks across Overview, Marketplace, Activity, Learning overview, Learning labeler, and System found no remaining visible-text WCAG AA failures in the sampled routes.

## Known follow-up debt

- `App.jsx` still coordinates a large amount of legacy feature state. Route-owned feature controllers should continue reducing it.
- `App.css` remains a large compatibility sheet. The redesign owns no new feature behavior there, but migrated feature blocks should move to scoped styles and then be deleted.
- Deep legacy tables and inspectors are visually governed by the new tokens but have not all been rebuilt as shared primitives.
- The current ESLint run reports pre-existing hook/compiler errors in legacy feature files. The production build passes; lint baseline cleanup should be handled separately from this visual migration.
- The Vite bundle warns that the main chunk is over 500 kB. Route-level lazy loading should follow once feature boundaries are extracted.
- Automated accessibility checks, keyboard workflow tests, and visual regression snapshots are not yet in CI.

## Guardrail for future work

New UI work must use the shell, routes, tokens, icon resolver, and section tabs above. Do not add product navigation to `App.jsx`, emoji or raw feature colors to domain metadata, or new redesign rules to `App.css`.
