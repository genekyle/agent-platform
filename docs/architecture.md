# Architecture Notes

This document is the shared anchor for what we are building, why, and what stays the same as the implementation evolves. It exists so that contradicting recommendations from any tool, model, or contributor can be evaluated against a written intention instead of relitigating from scratch each time.

If you are about to amend this doc, **also study the git history and the corresponding code change** — the *why* lives in the conjunction of this file, the diff, and the commit message. Updating the file without the diff is opinion; updating the diff without the file is drift.

---

## Intention

We are building **a Platform FOR Agents** — not a platform OR an agent, and not a thin wrapper around a frontier API. The platform and the agentic system are co-built. The platform is the *home* of the agents: the place from which they are wielded, steered, observed, diagnosed, and improved.

The thesis rests on one observation about the world the agents act on: **the way humans interact with web/UI applications is relatively stable.** Login flows, search flows, checkout flows, dashboards — the interaction patterns of an application change slowly. That stability is what makes a platform viable. It means the *scenarios, tasks, domains, and training examples* that describe "how this application works and how a user accomplishes things on it" are durable assets — they outlive any particular model or agent implementation built on top of them.

From that, the architectural principle: **only parts of the agentic system should change at a time, never the whole thing.** A new vision-grounding model swaps in without rebuilding the agent. A new page-state classifier swaps in without re-labeling scenarios. A new domain gets added by writing scenarios, not by rewriting the agent. The platform's job is to make this separation of concerns *real* — to make each part of the agentic system independently observable, replaceable, and evaluable.

In short: **the scenarios/tasks/domains are the durable substrate; the models are the swappable parts; the platform is what makes that swappability legible and safe.**

---

## Invariants — what does NOT change when the model changes

These are the load-bearing contracts. They survive every model swap. Treat them as facts, not opinions. Any change to one of these is a structural change and warrants explicit discussion.

1. **Screenshot is the primary signal.** Every observation event has a screenshot, captured and persisted alongside the artifact. DOM/MCP/accessibility data structures and supervises the visual signal — it does not replace it.
2. **One raw artifact per observation event.** Manifests and indexes are layered on top, not in place of, raw artifacts.
3. **Curated training-example layer is separate from raw artifacts.** Models train on curated, reviewed examples — not on raw captures.
4. **Conditioning context schema is durable.** A training record carries `(domain_id, scenario_id, goal_id, task_id, action_type_hint, capture_profile, viewport, url)`. New conditioning fields can be added; existing fields are not silently removed.
5. **Dataset record shape is the swap point.** The record `(screenshot, query, bbox, context)` (`grounding_vision_v1`) is the contract between the labeling pipeline and any model. Models change. The record shape doesn't, except by deliberate version bump.
6. **Models are served behind an HTTP endpoint contract.** Even when a "model" is a script wrapping a frontier API, it is called as if it were a served model. This makes model swaps a deployment change, not a code-rewrite.
7. **Scenario is the unit of train/eval split.** No scenario appears in both train and eval. This protects against the model memorizing a single page and inflating metrics.
8. **Targets compose, not compete.** The four `TRAINING_TARGETS` in [apps/controlplane-api/training.py](../apps/controlplane-api/training.py) — `vision_element_grounding`, `page_state_classifier`, `state_transition`, `task_outcome` — are layered. Grounding feeds state, both feed transition and outcome. We do not retarget this stack to compete with itself.

---

## Chosen path

**First model:** `vision_element_grounding`. Screenshot + element_query (with context) → bbox.

**Trajectory:** v0 (zero-shot frontier VLM as baseline) → v1 (fine-tuned grounding model served, low-confidence predictions auto-route to annotation queue) → v2 (production agent traffic generates captures, outcome-supervised auto-labels close the loop, all four targets live).

Full reasoning and stage-by-stage breakdown live in [docs/training.md](training.md). This document only carries the architectural commitment, not the rollout detail.

---

## Alternatives considered

Listing these explicitly so the rejection is visible — not so that they are settled forever, but so that anyone proposing a return to one of them has to engage with the stated reason rather than argue from a blank slate.

### A. "Skip custom training — use a frontier VLM end-to-end as the agent."

Wrap Claude Computer Use, GPT-4o, or similar; let the frontier model both observe and act directly. No custom models trained.

- **Pro:** works today; zero training infra; scales with frontier improvements.
- **Con:** every action is an API call (cost, latency, vendor lock-in); the agentic system becomes a black box we cannot diagnose, decompose, or improve a single part of; no path to specialize on our domains; no way to compound value in our captured data.
- **Why rejected:** this loses against the intention. A monolithic frontier-VLM agent is the opposite of a "platform for agents" — the whole agentic system is one opaque call, so you cannot swap parts of it, you can only swap the whole thing. That violates the core architectural principle. **However:** v0 of our own path uses a frontier VLM zero-shot as the *baseline for one swappable part* (grounding) — the comparison is real and quantitative, and we may legitimately keep a frontier VLM as the implementation of that part for as long as it beats our trained alternative. That is using a frontier model *as a component*, not *as the agent*.

### B. "Skip vision — use DOM/accessibility tree as the primary signal."

Train on `(DOM snapshot, query) → element selector`. Possibly with classical ML or rules over DOM.

- **Pro:** DOM is structured and small; fast to train and run; cheap.
- **Con:** breaks on canvas-rendered apps, custom widgets, iframes, shadow DOM, DOM obfuscation; doesn't generalize to non-web surfaces (mobile, desktop, embedded UIs eventually); ceiling is lower than vision.
- **Why rejected:** invariant #1. Screenshot-first is a deliberate ceiling-vs-floor trade. DOM remains in the loop as supervision and structure, not as the primary signal.

### C. "Hybrid — DOM primary, vision fallback."

Cheap DOM path for easy cases, vision for the rest.

- **Pro:** likely best short-term cost/perf in production.
- **Con:** two pipelines to maintain; harder to reason about evaluation; ambiguous training signal.
- **Why rejected (for now):** premature optimization for v0/v1. A reasonable thing to revisit at v2 if cost/latency on the vision path is the binding constraint.

### D. "Action-first — skip the grounding intermediate."

Train `(screenshot, task) → (action_type, target_description)` end-to-end. No bbox in the middle.

- **Pro:** closer to what we actually want the agent to do.
- **Con:** harder to label, harder to evaluate (binary success vs. continuous IoU), the prediction can't be reused by the page-state, transition, or outcome models. Couples the platform to a single model architecture.
- **Why rejected:** the explicit grounding intermediate is what makes invariant #8 (target composition) work. Killing the intermediate kills the layered roadmap.

---

## Falsifying conditions — what would make us change our mind

Each alternative has a condition under which we would seriously revisit it. Naming them up front means future debate happens against criteria, not vibes.

| Alternative | Revisit if... |
|---|---|
| A. Frontier VLM end-to-end | v0 zero-shot already hits production-acceptable accuracy AND the cost/latency of API-driven action is acceptable for the use case. In that case the platform becomes "frontier API harness with a labeling backstop for the few domains where the gap is real." |
| B. DOM-primary | Vision baseline plateaus well below useful, AND the failure modes are dominated by *grounding* errors (wrong element on a clean DOM page) rather than perception errors (canvas, custom widgets). |
| C. Hybrid | At v2, vision-path serving cost or latency is the binding constraint on production traffic, and DOM-path solves >50% of cases at materially lower cost. |
| D. Action-first | The composition payoff (downstream targets reusing grounding outputs) does not materialize after a real attempt — i.e. page_state and transition models do not measurably benefit from grounding's outputs. |

If a contradicting recommendation surfaces and *none* of the falsifying conditions are met, the recommendation is noise — file it, don't act on it.

---

## Change discipline

- **What this doc carries:** intention, invariants, the chosen path, the rejected alternatives and their falsifying conditions.
- **What this doc does NOT carry:** rollout plans (those live in [training.md](training.md)), code-level design, sprint planning, or architectural choices that haven't survived a real evaluation.
- **How to amend it:** propose the change in the same commit as the code or evaluation result that justifies it. Reference the commit hash from this doc when it lands. The git log of this file is the project's architectural changelog.
- **How to disagree with it:** point at an invariant being wrong, or a falsifying condition being met. Both are real arguments. "I think a different model would be better" without one of those is not.
