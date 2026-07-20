# Product and design direction

## Product name

The interface name is **AI Ops**.

AI Ops is a humane operations console for agents that handle coordination-heavy parts of daily life. It should feel capable and technical without making the person feel like an infrastructure operator. The agents do the background work; the interface translates that work into calm, legible choices.

Recommended product line: **“Your agents, coordinated.”**

Avoid “control plane,” “pilot,” “mission control,” and theatrical AI language in the primary interface. Technical vocabulary remains available in advanced views where it is accurate and useful.

## Experience thesis: quiet operations

The console should feel like a well-run desk at the end of a busy day:

- calm enough to trust;
- dense enough to be useful;
- warm enough to feel made for a person;
- explicit about uncertainty and handoffs;
- visibly productive without performing busyness.

This is not a neon cyberpunk terminal and not a soft consumer to-do app. It is a dark, warm operational environment with precise typography, restrained status color, real progress, and clear human handoffs.

## Core experience principles

### 1. Human attention is the scarce resource

Every overview starts with the smallest set of decisions only the person can make. The default state should be reassuring: “Nothing needs you.” When attention is required, state why, what will happen next, and whether the agent is paused.

### 2. Show outcomes before machinery

Lead with completed applications, live listings, resolved handoffs, time returned, and work in progress. Model rungs, captures, protocols, and services live one level deeper unless the user is in Learning or System.

### 3. Keep the agent legible

For active work, show a compact sequence: current step, last verified step, next expected step, and the reason for any pause. The detailed reasoning record is available without forcing the entire explanation into the dashboard.

### 4. Be honest about state

Unknown, stale, disconnected, queued, active, waiting for human, blocked, complete, and failed are distinct states. Do not collapse them into generic “healthy/unhealthy” or use optimistic empty states when data is unavailable.

### 5. Calm by default, loud by exception

Neutral surfaces carry most of the UI. Semantic colors appear only on state, selection, focus, alerts, and data. Urgent attention earns contrast; routine success does not paint the whole page green.

### 6. Progressive disclosure over explanation walls

Sidebar items are short. Cards carry one sentence at most. Technical rationale, metadata, and diagnostics expand in drawers, inspectors, or detail routes.

### 7. The console remembers where you are

Routes are deep-linkable. Global navigation remains stable. Domain tabs do not replace the global rail. Filters, density, and the last domain can persist locally when safe.

## Voice and language

Use concise, direct, human language:

- “Needs you” instead of “human-required escalations.”
- “Working now” instead of “active runtime execution.”
- “Waiting for sign-in” instead of “not_authenticated blocker.”
- “Learning” instead of “Lab” for the product-facing flywheel.
- “Advanced” for model tests, registries, eval runs, and low-level corpus tools.
- “Connections” for providers and accounts shared across domains.

Technical labels are appropriate in inspectors and logs. Pair them with human meaning rather than deleting precision.

## Emotional qualities

The intended qualities are composed, observant, dependable, humane, and quietly intelligent. Avoid playful emoji, gratuitous glow, gradients that imply magic, AI sparkles, robot imagery, and excessive motion.
