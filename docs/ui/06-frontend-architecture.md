# Frontend architecture

## Objective

Separate product navigation, visual primitives, feature behavior, domain configuration, and API access so a change in one concern does not force edits across the whole console.

## Target structure

```text
src/
├─ app/
│  ├─ App.jsx
│  ├─ AppShell.jsx
│  ├─ router.jsx
│  └─ navigation.js
├─ styles/
│  ├─ tokens.css
│  ├─ reset.css
│  └─ global.css
├─ ui/
│  ├─ button/
│  ├─ forms/
│  ├─ feedback/
│  ├─ layout/
│  ├─ navigation/
│  ├─ data/
│  └─ visualization/
├─ services/
│  ├─ apiClient.js
│  ├─ queryKeys.js
│  └─ errors.js
├─ features/
│  ├─ overview/
│  ├─ activity/
│  ├─ domains/
│  │  ├─ catalog.js
│  │  ├─ DomainShell.jsx
│  │  ├─ career-search/
│  │  └─ marketplace/
│  ├─ learning/
│  └─ system/
└─ main.jsx
```

## Implemented foundation

The first redesign pass established these boundaries without changing backend behavior:

- `src/app/AppShell.jsx` owns global navigation, responsive drawer behavior, global health, page headings, breadcrumbs, and the drive-lock banner.
- `src/app/routes.js` owns URL parsing and path generation for Overview, Domains, Activity, Learning, and System.
- `src/ui/Icon.jsx` is the only icon resolver. Domain metadata contains no icon glyphs or colors.
- `src/ui/SectionTabs.jsx` owns scroll-safe local navigation for Learning and System.
- `src/styles/tokens.css` owns the warm graphite, sage, amber, coral, and plum palette.
- `src/styles/ai-ops.css` owns the redesign shell and reference dashboard layouts while legacy CSS is extracted.
- `LearningOverview.jsx` and `LearningAdvancedHub.jsx` separate the product learning loop from engineering utilities.

`src/App.jsx` still coordinates legacy API state and feature rendering. It is no longer responsible for shell markup or local navigation UI, but shrinking it into route-owned feature controllers is the next architecture migration. `App.css` remains a compatibility layer and must not receive new redesign features.

Each feature owns its routes, page components, hooks, API adapter, feature-specific styles, and tests. Cross-feature UI moves to `ui/` only after at least two features share the same contract.

## App shell responsibilities

`AppShell` owns only:

- global navigation;
- responsive navigation drawer/rail;
- page outlet;
- global connection and drive-lock surfaces;
- global toasts and dialogs;
- error boundary.

It does not fetch domain data, render feature pages, interpret training artifacts, or own per-domain tabs.

## Routing

Add a client-side router and replace view-state routing in `App.jsx`. Route params are the source of truth for destination and local tab. The browser back button must work through domain and feature navigation.

Keep API state separate from navigation state. Filters that benefit from sharing or restore may use URL search params; ephemeral selections remain local.

## Data access

Create one API client that handles:

- base URL;
- JSON parsing;
- normalized errors;
- abort signals;
- request timing where useful;
- stale/unreachable distinction.

Feature adapters translate backend payloads into UI view models. Components should not repeatedly call `fetch` or infer fallback shapes. Polling belongs in reusable hooks with visibility awareness and explicit intervals.

The redesign does not require a state-management framework on day one. Start with route state, feature hooks, and localized React state. Introduce a query/cache library only if repeated polling and invalidation remain difficult after the client boundary exists.

## Domain configuration

The domain catalog contains stable product metadata only: id, name, description, supported capabilities, routes, and channel relationships. Live status, counts, readiness, and coming-soon state come from the backend or a feature-owned adapter.

Do not store React elements, emoji, colors, or feature behavior in the catalog. Icons are library references resolved by the domain shell.

## Styling

- `tokens.css` contains all design tokens.
- `global.css` contains only document defaults and truly global utilities.
- Primitives own their styles.
- Features own layout styles that are not reusable primitives.
- Use CSS Modules or an equivalent locally scoped strategy for migrated components.
- Inline style objects are reserved for truly dynamic values such as a calculated chart width, and those values should usually become CSS custom properties.
- Do not append redesign rules to legacy `App.css`; migrate and delete sections as each feature moves.

## Component contracts

- Primitives accept semantic variants (`primary`, `danger`, `quiet`) rather than raw colors.
- Status components accept canonical states (`idle`, `running`, `waiting_human`, `blocked`, `complete`, `failed`, `unknown`).
- Dashboard visuals accept labeled data and expose an accessible text summary.
- Empty, error, stale, and loading states are explicit components, not scattered ternaries.
- Tables define columns as data and render one consistent responsive/overflow strategy.

## Testing and quality gates

Add frontend tests with the shell migration:

- route and browser-history tests;
- global nav and responsive drawer keyboard tests;
- domain shell contract tests;
- loading/error/stale/empty states;
- visual regression screenshots for reference routes;
- accessibility checks for landmarks, names, focus, contrast, and reduced motion.

CI gates for the redesign: lint, build, component/unit tests, route smoke tests, and targeted visual snapshots. Maintain the backend route inventory independently; frontend restructuring must not change API behavior unless explicitly scoped.

## Migration rule

Migrate by vertical slice. A route is “new” only when its shell, primitives, feature ownership, responsive behavior, and state handling have moved. Avoid an extended hybrid where legacy and new button/card/status systems coexist on the same page.
