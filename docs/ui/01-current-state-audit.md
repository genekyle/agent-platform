# Current-state UI audit

Audit date: 2026-07-19. The app was reviewed live at `http://localhost:5173` across Command Center, Activity, Domains, Career Search, Indeed, Facebook Marketplace, Training, Lab, and System, plus the existing `900px` responsive breakpoint.

## Executive finding

The product has useful operational data and several strong individual tools, but it reads as a collection of admin screens rather than one coherent operations console. The largest issue is information architecture: global destinations, domain hierarchy, local tabs, and developer tools compete in one sidebar. The bright blue active state and emoji-heavy labeling amplify the feeling of a generated template, but neither is the root cause.

The redesign should preserve the operational truth already present—attention, actions, outcomes, reasoning, health, and learning—and give those signals a stable hierarchy.

## What is working

- The Command Center already prioritizes attention, domain readiness, labeling backlog, and system health.
- Domain workspaces share useful concepts: connection status, automation posture, attention, goals, tasks, activity, and training.
- The Activity view unifies reasoning, actions, escalations, errors, and events. This is the strongest starting point for the console metaphor.
- The Training labeler has a clear primary workflow and useful before/current/after visual context.
- The Controller view exposes rung mix, verified rate, escalation rate, reasoning coverage, and the teacher’s rationale. This is meaningful product differentiation.
- Status pages expose real service targets, health, latency, and failure detail instead of hiding degraded state.

## Observed UX problems

### 1. The sidebar carries four different jobs

The same rail currently acts as:

1. global product navigation;
2. a replacement section menu for Training, Lab, and System;
3. a hierarchical domain browser;
4. a domain and account shortcut list.

Opening a primary destination slides the rail to a second menu with “All Sections.” Entering Career Search replaces that with a breadcrumb, subdomains, and Accounts. Descriptive copy is embedded in every item, so a five-item menu becomes a long reading task and the eleven-item Lab menu extends well below the viewport.

Impact: users lose global orientation, cannot build a stable spatial memory, and must scan repeated descriptions before moving.

### 2. Navigation and content repeat each other

- Domain names and summaries appear in the sidebar, page header, domain hero, tabs, and cards.
- Career Search renders a page title and responsibility, then repeats the same title and responsibility in a large panel.
- The Domains rail repeats the same domain cards visible in the main content.
- Refresh and system state controls appear in both the global header and several page panels.

Impact: vertical space is consumed without increasing confidence. Important work moves below the fold.

### 3. The visual hierarchy is “card soup”

Most content is placed in a white, rounded, shadowed container regardless of importance. Status, navigation, configuration, primary actions, logs, and explanatory prose receive similar treatment. Large gaps and soft shadows make dense operational pages feel longer while still not making priority obvious.

Impact: the eye cannot immediately distinguish urgent work, active work, supporting context, and reference data.

### 4. Color and iconography lack a governing system

- The shell is navy and the active state is bright blue.
- Blue appears in buttons, focus rings, tabs, chips, chart segments, and accents.
- The codebase contains many unrelated hard-coded colors; the most frequent accent families include green, red, blue, amber, purple, and cyan.
- Emojis are used for domains, headings, statuses, sections, alerts, actions, and decoration.
- Icons often repeat adjacent text instead of improving recognition.

Impact: the product feels visually assembled rather than intentionally designed. Emoji rendering also varies by operating system and clashes with the serious console content.

### 5. Domain dashboards are structurally consistent but not decision-first

Indeed and Facebook Marketplace share connection, automation, tabs, attention, goals, tasks, and recent activity. This consistency is valuable, but the dashboard foregrounds configuration (“Goals,” automation mode) over the user’s day: what the agents finished, what they are handling now, what needs a human, and how much coordination was avoided.

Impact: users see the machinery before the outcome.

### 6. Activity is informative but overwhelming

The live Activity screen rendered 217 entries in one continuous feed. Long rationales, low-level probes, actions, and errors share nearly equal weight. Filters exist, but there is no session grouping, summary strip, details drawer, compact table mode, or bounded initial window.

Impact: the most transparent part of the product becomes difficult to scan during a real incident or handoff.

### 7. Product and developer tools are mixed

Training contains nine sections and Lab contains eleven, including controller, playground, model test, metrics, labeler, corpus scorecard, state graph, visualization, registry, eval runs, and run detail. Several are alternate views of the same learning system; others are advanced engineering tools.

Impact: the primary navigation exposes implementation structure instead of user intent.

### 8. The responsive breakpoint removes navigation

At `880px`, the sidebar disappears and no replacement menu is exposed in the DOM. System content stacks into large single-column cards, but the user cannot navigate to another destination.

Impact: tablet and narrow-window use is a dead end. Responsive work must cover navigation and task completion, not only grid stacking.

## Codebase findings

- The UI contains approximately 18,679 lines across JavaScript, JSX, and CSS.
- `App.jsx` is 1,384 lines and owns global navigation state, domain routing state, data orchestration, and section rendering.
- `App.css` is 4,412 lines with roughly 811 top-level class selectors.
- There are approximately 609 inline style objects across the JSX files.
- No client-side router is installed; visible location is held in React state and cannot be deep-linked or restored after refresh.
- `navigation.js` and `workspace/domains.js` define overlapping product hierarchy while `App.jsx` implements the transition behavior.
- The current UI has no icon dependency; emojis and text glyphs fill that role.
- Responsive rules are distributed throughout the global stylesheet rather than owned by layout or feature components.

These facts make visual change risky unless the architecture is separated first. A new theme applied directly to the current global stylesheet would preserve the navigation and ownership problems.

## Priority order

1. Stabilize the product model and navigation.
2. Create tokens, icon rules, primitives, and the new shell.
3. Redesign Overview and Activity as the reference vertical slice.
4. Apply the dashboard grammar to Career Search and Marketplace.
5. Consolidate Learning and advanced tools.
6. Migrate remaining data-heavy pages and forms.
7. Complete responsive, accessibility, performance, and deletion of legacy styles.
