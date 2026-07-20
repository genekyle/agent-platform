# Implementation roadmap

## Current status — 2026-07-19

- Phase 0: complete for the audited reference routes.
- Phase 1: complete for shell, routes, tokens, icons, responsive navigation, and global states.
- Phase 2: complete for the Overview and Activity reference slice.
- Phase 3: complete for domain discovery and the shared domain workspace shell; deep Marketplace and Career Search data tools still carry legacy component internals.
- Phase 4: complete for Learning information architecture, overview, advanced boundary, and visual migration; deeper component extraction remains.
- Phase 5: complete for System navigation, status styling, and topology visualization; repeated form/table primitives remain a follow-up.
- Phase 6: partially complete. Desktop, tablet, mobile, reduced motion, build, route, and console-error checks passed. Automated accessibility/visual regression and legacy `App.css` deletion remain open.

Implementation evidence and exact ownership live in `08-implementation-notes.md`.

## Strategy

Deliver the redesign as vertical slices behind one stable direction. The first slice—AI Ops shell + Overview + Activity—proves navigation, tokens, primitives, console density, responsive behavior, and accessibility before every feature is migrated.

## Phase 0 — Baseline and guardrails

Scope:

- Capture reference screenshots for the audited routes at desktop and tablet widths.
- Record route/component ownership and the legacy CSS sections each route uses.
- Add a lightweight UI smoke-test setup and a visual reference route list.
- Freeze the canonical names and information architecture in these docs.

Exit criteria:

- Every live top-level route has a baseline.
- No redesign work needs to guess whether a regression was pre-existing.

## Phase 1 — Foundations and AI Ops shell

Scope:

- Rename product shell to AI Ops.
- Add tokens and base styles from `04-design-system.md`.
- Add the monochrome icon library and remove emoji from the shell.
- Add real client-side routes.
- Build stable global navigation, page header, breadcrumbs, local tabs, responsive drawer/rail, drive-lock banner, and global status control.
- Build core buttons, fields, badges, status, panels, empty/error/stale/loading states, and tooltips.
- Establish the feature folder and API client boundaries.

Exit criteria:

- Browser history and deep links work.
- Global navigation never changes into a section menu.
- Narrow screens retain full navigation.
- No blue or emoji appears in the new shell.
- Keyboard focus and reduced motion pass.

## Phase 2 — Overview and Activity reference slice

Scope:

- Rebuild Command Center as Overview using attention, working now, outcome cards, domain flow summaries, recent wins, and compact health.
- Rebuild Activity as a compact console with bounded rows, sticky filters, session/domain grouping, and a detail drawer.
- Normalize activity states and outcomes in a feature adapter without altering corpus semantics.

Exit criteria:

- A user can identify required attention and active work in under five seconds.
- Long reasoning and error text are available without dominating the feed.
- Activity remains transparent and can filter to one run/session.
- This slice becomes the visual and component reference for the rest of the product.

## Phase 3 — Domain shell and live domain dashboards

Scope:

- Build the shared DomainShell and local tab model.
- Remove domain descriptions and subdomains from the global sidebar.
- Recast Indeed, Workday, Greenhouse, and LinkedIn as Career Search channels.
- Rebuild Career Search overview, pipeline, current run, account blockers, and channel readiness.
- Rebuild Marketplace overview, inventory/listing pipeline, reply queue, and outcomes.
- Move automation mode, goals, schedules, and low-frequency configuration into Settings.

Exit criteria:

- Career Search and Marketplace share a recognizable grammar but have genuinely domain-specific visuals.
- All existing operational actions remain reachable.
- Accounts and human gates remain explicit and safe.
- Domain routes are deep-linkable and preserve local tab state in the URL.

## Phase 4 — Learning consolidation

Scope:

- Merge Training and product-facing Lab concepts under Learning.
- Create Learning overview with the flywheel, queue, coverage, quality, rung mix, and graduation readiness.
- Redesign the labeler with the new shell while preserving its efficient keyboard workflow.
- Group session setup/capture under Sessions.
- Put controller, model test, scorecard, state graph, registry, eval runs, run detail, visualization, and movement playground behind clear Overview / Models / Corpus / Advanced boundaries.
- Remove emoji and legacy color assignments from all learning views and charts.

Exit criteria:

- The primary Learning flow is Capture → Label → Train → Evaluate → Promote.
- Advanced tools remain available without crowding product navigation.
- The teacher/student/backstop/human colors follow one semantic mapping.

## Phase 5 — System, forms, and remaining data views

Scope:

- Rebuild System health, topology, usage, connections, and account readiness.
- Migrate remaining tables, inspectors, forms, dialogs, capture review, and model detail views to primitives.
- Replace repeated refresh controls and repeated page/hero headings.
- Normalize loading, error, stale, empty, degraded, and disconnected states.

Exit criteria:

- No migrated feature depends on legacy global card/button/status styles.
- Technical detail is available without obscuring human impact.
- Dense tables and inspectors are keyboard usable and have an intentional narrow-screen state.

## Phase 6 — Hardening and legacy removal

Scope:

- Complete responsive coverage at desktop, compact desktop/tablet, and review-oriented mobile sizes.
- Run accessibility, contrast, keyboard, reduced-motion, and screen-reader checks.
- Add virtualization/pagination where large feeds or tables need it.
- Measure initial load and interaction performance; split heavy feature bundles by route if needed.
- Delete dead components, unused navigation state, legacy CSS, emoji, and old product copy.
- Update README screenshots only after the new routes are stable.

Exit criteria:

- No “Ops Pilot” or “Control Plane” shell copy remains.
- No emoji or blue interface tokens remain.
- `App.jsx` is a small composition/root file.
- Legacy `App.css` is removed or reduced to zero migrated responsibility.
- Desktop and tablet navigation are complete; mobile review/approval flows are usable.
- Visual regression and accessibility checks cover the reference routes.

## Recommended implementation order inside each phase

1. Define the data/view-model contract.
2. Build or reuse primitives.
3. Implement the route at all target widths.
4. Verify loading, stale, error, empty, active, blocked, and complete states.
5. Run keyboard and screen-reader checks.
6. Remove the replaced legacy code and styles.
7. Update the relevant UI document with any changed decision.

## Risk controls

- Preserve every consequential gate and approval. This project’s UI is safety infrastructure, not decoration.
- Do not change backend semantics as part of a visual migration unless separately planned and tested.
- Treat “unknown” as a first-class state; never make missing data look healthy.
- Do not let the new dashboard invent metrics. Time saved, revenue, conversion, and trends must identify their source and limitations.
- Avoid a theme-only pass over the current architecture; it would increase CSS debt and make the second redesign harder.
- Keep each vertical slice releasable and remove its legacy path before starting the next large slice.

## Success measures

- Time to identify the next human action.
- Time to locate active work and its current step.
- Number of primary navigation choices.
- Navigation depth to common tasks: label next, review handoff, start domain run, inspect failure.
- Percentage of routes that are deep-linkable and refresh-safe.
- Keyboard-only completion of common operator workflows.
- Reduction in duplicated product copy, inline styles, and legacy global CSS.
- Zero emoji and zero blue tokens in migrated UI.
