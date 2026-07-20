# AI Ops UI redesign

Status: active implementation. The AI Ops shell and reference dashboards are live; the remaining work is legacy-component extraction and automated hardening. This directory is the UI source of truth for the redesign from **Ops Pilot / Control Plane** to **AI Ops**.

The redesign is intentionally split by concern. Visual styling must not be used to hide unresolved navigation or product-language problems, and feature work must not reintroduce one-off component patterns.

## Documents

1. [`01-current-state-audit.md`](01-current-state-audit.md) — what was observed in the live UI and codebase.
2. [`02-product-and-design-direction.md`](02-product-and-design-direction.md) — brand, experience principles, voice, and the visual thesis.
3. [`03-information-architecture.md`](03-information-architecture.md) — global navigation, routes, domain hierarchy, and naming.
4. [`04-design-system.md`](04-design-system.md) — color, typography, spacing, iconography, motion, accessibility, and visualization rules.
5. [`05-dashboard-patterns.md`](05-dashboard-patterns.md) — the shared dashboard grammar and the visuals each domain needs.
6. [`06-frontend-architecture.md`](06-frontend-architecture.md) — separation of app shell, features, data access, domain configuration, primitives, and styles.
7. [`07-implementation-roadmap.md`](07-implementation-roadmap.md) — phased delivery, acceptance criteria, and validation.
8. [`08-implementation-notes.md`](08-implementation-notes.md) — what shipped in the first redesign pass, file ownership, validation evidence, and remaining debt.

## Non-negotiable decisions

- Product name: **AI Ops**. Do not display “Ops Pilot” or “Control Plane” in the product shell.
- No emoji as interface icons, labels, status markers, or decoration.
- Icons come from one monochrome stroke system, inherit `currentColor`, and appear only when they improve recognition or interaction.
- Blue is removed from the product palette, including active navigation, focus, controls, charts, and status colors.
- The console uses warm graphite neutrals with restrained sage, amber, coral, and plum semantic accents.
- Global navigation never transforms into a different menu. Domain navigation and page tabs live in the content hierarchy.
- The default page answers: **What needs me? What is working? What finished? What is blocked?**
- “Lab” is not a primary product destination. Learning, models, controller, and corpus tools are organized under **Learning** with an advanced-tools boundary.
- Every route must be deep-linkable, refresh-safe, keyboard reachable, and usable without depending on color alone.
- New UI code follows the feature and primitive boundaries in `06-frontend-architecture.md`; do not add new sections to the root `App.jsx` or new global rules to the current monolithic `App.css`.

## Change-note ritual

When implementation begins, record material UI decisions here or in the relevant numbered document before closing the change. If a decision changes navigation, tokens, dashboard grammar, or component ownership, update the corresponding source-of-truth document in the same commit.
