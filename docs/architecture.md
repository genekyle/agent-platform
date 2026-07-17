# Architecture Notes

This document is the shared anchor for what we are building, why, and what stays the same as the implementation evolves. It exists so that contradicting recommendations from any tool, model, or contributor can be evaluated against a written intention instead of relitigating from scratch each time.

If you are about to amend this doc, **also study the git history and the corresponding code change** — the *why* lives in the conjunction of this file, the diff, and the commit message. Updating the file without the diff is opinion; updating the diff without the file is drift.

> **Amended 2026-07-16.** This file sat unamended from April through three architectural eras (the
> SELECT cascade, the live-drive domains, the Interaction API) and had drifted from the system as
> built — which is exactly the failure mode it exists to prevent. The amendments below are marked
> inline and logged in the Amendment log at the bottom. Justifying commits: `553e682` (Execution =
> API), `35cbfff` (Interaction API plan), `13c0293` (the Interaction contract), `7b7c94c` (the intent
> journal), `95ad87a` (PRINCIPLES §8 correction). See `docs/LEARNINGS.md` 2026-07-16 entries.

---

## Intention

We are building **a Platform FOR Agents** — not a platform OR an agent, and not a thin wrapper around a frontier API. The platform and the agentic system are co-built. The platform is the *home* of the agents: the place from which they are wielded, steered, observed, diagnosed, and improved.

The thesis rests on one observation about the world the agents act on: **the way humans interact with web/UI applications is relatively stable.** Login flows, search flows, checkout flows, dashboards — the interaction patterns of an application change slowly. That stability is what makes a platform viable. It means the *scenarios, tasks, domains, and training examples* that describe "how this application works and how a user accomplishes things on it" are durable assets — they outlive any particular model or agent implementation built on top of them.

> **Amendment (2026-07-16) — the durable substrate gained two members.** Three months of live driving
> confirmed the thesis and sharpened it: alongside scenarios/tasks/domains, the durable assets are
> **interaction protocols** (the staged-commit popup generalized Indeed → Workday → Greenhouse
> unchanged in shape) and **recipes** (per-site data: selectors, vocabularies, quirks). Protocols are
> CODE in endpoints; site specifics are DATA in recipes; both outlive any model. See
> `PLAN_interaction_api.md` §1 and PRINCIPLES §8.

From that, the architectural principle: **only parts of the agentic system should change at a time, never the whole thing.** A new vision-grounding model swaps in without rebuilding the agent. A new page-state classifier swaps in without re-labeling scenarios. A new domain gets added by writing scenarios, not by rewriting the agent. The platform's job is to make this separation of concerns *real* — to make each part of the agentic system independently observable, replaceable, and evaluable.

In short: **the scenarios/tasks/domains/protocols/recipes are the durable substrate; the models are the swappable parts; the platform is what makes that swappability legible and safe.**

---

## Invariants — what does NOT change when the model changes

These are the load-bearing contracts. They survive every model swap. Treat them as facts, not opinions. Any change to one of these is a structural change and warrants explicit discussion.

1. **Every observation event carries a screenshot.** ~~Screenshot is the primary signal.~~
   > **Amended 2026-07-16.** As written in April this said the screenshot is the *primary* signal and
   > DOM/AX "structures and supervises" it. The system as built inverted that for **operation**: the
   > CDP-AX tree (role + accessible-name → `backend_node_id`) is the primary *operating* signal for
   > perception and actuation (PRINCIPLES §6), it proved sufficient on every domain driven live
   > (Facebook, Indeed, Workday, Greenhouse), and vision's earned niche is **protocol discovery**
   > (the Indeed `Update`-footer was found by looking at a picture — AX listed the parts but not the
   > contract; `interaction-layers.md`), **AX-blind surfaces** (canvas), and **supervision/labeling**.
   > What survives as invariant: every observation persists a screenshot alongside the AX sidecar, so
   > the vision track (and human labelers) never lose their raw material. The April wording was a
   > ceiling-vs-floor bet on vision-first grounding; the floor (AX) turned out to carry the building.
2. **One raw artifact per observation event.** Manifests and indexes are layered on top, not in place of, raw artifacts.
3. **Curated training-example layer is separate from raw artifacts.** Models train on curated, reviewed examples — not on raw captures.
4. **Conditioning context schema is durable.** A training record carries `(domain_id, scenario_id, goal_id, task_id, action_type_hint, capture_profile, viewport, url)`. New conditioning fields can be added; existing fields are not silently removed.
5. **Dataset record shape is the swap point.** The record `(screenshot, query, bbox, context)` (`grounding_vision_v1`) is the contract between the labeling pipeline and any model. Models change. The record shape doesn't, except by deliberate version bump.
   > **Amendment (2026-07-16):** this invariant gained a sibling contract of equal rank — the **intent
   > journal record** (`packages/interaction/interaction/journal.py`): append-only, fingerprint-joined,
   > carrying `(intent, field, resolved addressing, outcome, per-step log, executed)`. It is the swap
   > point between the acting system and the L4/intent-policy trainer, exactly as `grounding_vision_v1`
   > is for grounding. Same rule: deliberate version bump only.
6. **Models are served behind an HTTP endpoint contract.** Even when a "model" is a script wrapping a frontier API, it is called as if it were a served model. This makes model swaps a deployment change, not a code-rewrite.
7. **Scenario is the unit of train/eval split.** No scenario appears in both train and eval. This protects against the model memorizing a single page and inflating metrics.
8. **Targets compose, not compete.** The four `TRAINING_TARGETS` in [apps/controlplane-api/training.py](../apps/controlplane-api/training.py) — `vision_element_grounding`, `page_state_classifier`, `state_transition`, `task_outcome` — are layered. Grounding feeds state, both feed transition and outcome. We do not retarget this stack to compete with itself.
   > **Amendment (2026-07-16):** a fifth target layers on top without competing: the **intent policy**
   > (L4) — `(state, goal, field) → intent from the closed vocabulary` — trained from the journal.
   > It sits *above* the frozen `ActionId` contract (one intent expands to 1..N ActionIds;
   > `contract.intent_expands_to`), so it does not replace the selector stack; it decides at a higher
   > altitude. See LEARNINGS 2026-07-16 ("Intent sits ABOVE the frozen ActionId").
9. **Every action the system takes is journaled.** *(New invariant, 2026-07-16.)* An action the
   system can't see is an action it can never learn — this is the premise of teacher → distill, and
   it was violated for three months without anyone noticing (350+ live actions on 2026-07-15,
   zero corpus rows). Enforcement is mechanical, not disciplinary: `apps/mcp/app/intent_api.py::journaled`
   is a route decorator; the HTTP response is *derived from* the journaled record so corpus and
   response cannot disagree. The event log is an operator wall display, **never** a corpus. See
   PRINCIPLES §8 and LEARNINGS 2026-07-16.
10. **The model emits intents from a closed vocabulary.** *(New invariant, 2026-07-16.)* No selectors,
    no JS, no `backend_node_id` in any call the model makes. Site specifics are recipe DATA
    (`apply_fields.resolve`); protocol specifics are endpoint CODE (tier 2); discovery stays inline
    (`/probe`) forever but is journaled and must end in an endpoint + recipe entry + labeled states.
    **The model says WHAT. The recipe says WHERE. The API says HOW. The journal says WHAT HAPPENED.**

---

## Chosen path

~~**First model:** `vision_element_grounding`. Screenshot + element_query (with context) → bbox.~~

> **Amended 2026-07-16.** The April sequencing put grounding first. Reality re-ordered it: the AX
> path made element-finding nearly free, so grounding never accumulated data (0% on 19 records —
> data-starved, not disproven), while live ATS driving created immediate demand and immediate data
> for **state classification**. The first trained-and-promoted model is now **L3, the page-state
> classifier** (v0 stage-observer: 94% held-out on 98 labels, 2026-07-09), trained on the ATS state
> taxonomy the drives are already labeling. **Second:** L4, the intent policy, from the journal.
> Grounding remains a real target (invariant #8's stack is unchanged) — it is demoted from "first,"
> not deleted; the vision track's near-term earned work is protocol discovery and AX-blind pages.

**Trajectory (amended):** v0 (zero-shot frontier models as baselines for each part) → v1 (L3 promoted into the loop for the states it owns; low-confidence falls through the cascade) → v2 (L4 emits intents in shadow, then owns learned scenarios end-to-end with the verifier gate + escalation ladder; all targets live; production traffic auto-labels).

Full rollout detail lives in [docs/PLAN_flywheel_first_revolution.md](PLAN_flywheel_first_revolution.md) (supersedes the stage detail in [training.md](training.md) where they disagree). The endgame and the escalation ladder live in [docs/DECISION_two-stacks-one-spine.md](DECISION_two-stacks-one-spine.md). This document only carries the architectural commitment.

---

## Alternatives considered

Listing these explicitly so the rejection is visible — not so that they are settled forever, but so that anyone proposing a return to one of them has to engage with the stated reason rather than argue from a blank slate.

### A. "Skip custom training — use a frontier VLM end-to-end as the agent."

Wrap Claude Computer Use, GPT-4o, or similar; let the frontier model both observe and act directly. No custom models trained.

- **Pro:** works today; zero training infra; scales with frontier improvements.
- **Con:** every action is an API call (cost, latency, vendor lock-in); the agentic system becomes a black box we cannot diagnose, decompose, or improve a single part of; no path to specialize on our domains; no way to compound value in our captured data.
- **Why rejected:** this loses against the intention. A monolithic frontier-VLM agent is the opposite of a "platform for agents" — the whole agentic system is one opaque call, so you cannot swap parts of it, you can only swap the whole thing. That violates the core architectural principle. **However:** we deliberately use frontier models *as components*: Haiku as the budget-gated select/resolve catchall, and Claude as the **teacher** — the driver of novel domains whose journaled work is the distillation corpus. Using a frontier model as a component (or a teacher) is not using it as the agent.
  > **Note (2026-07-16):** the teacher role is now permanent by design, not a scaffold to be removed.
  > The endgame (operator-stated) is per-scenario graduation: learned/cached scenarios run on the
  > inner layers without Claude; Claude keeps teaching whatever is novel. The door stays open. See
  > `DECISION_two-stacks-one-spine.md`.

### B. "Skip vision — use DOM/accessibility tree as the primary signal."

Train on `(DOM snapshot, query) → element selector`. Possibly with classical ML or rules over DOM.

- **Pro:** DOM is structured and small; fast to train and run; cheap.
- **Con:** breaks on canvas-rendered apps, custom widgets, iframes, shadow DOM, DOM obfuscation; doesn't generalize to non-web surfaces (mobile, desktop, embedded UIs eventually); ceiling is lower than vision.
- **Why rejected:** invariant #1. Screenshot-first is a deliberate ceiling-vs-floor trade. DOM remains in the loop as supervision and structure, not as the primary signal.
  > **Status change (2026-07-16): partially adopted, with the con-list validated in detail.** The
  > operating layer *is* AX-primary now (PRINCIPLES §6) — that part of B won on evidence: it drove
  > four domains live, including hostile ones. But B's cons all showed up too, each with a named
  > countermeasure instead of a vision fallback-in-general: custom widgets broke element-driving and
  > produced the **widget protocol layer** (`interaction-layers.md`); cross-origin iframes are their
  > own CDP targets (`_discover_target`); AX lies (`#country` is the phone code) and the **recipe**
  > is where the truth lands. Vision keeps the jobs AX genuinely can't do: discovering a widget's
  > *contract* (not its parts), canvas/AX-blind pages, and supervision. So: not "DOM-primary, vision
  > fallback" (alternative C's shape) but **AX-primary for operation, vision for discovery and
  > supervision, screenshot always captured** (amended invariant #1).

### C. "Hybrid — DOM primary, vision fallback."

Cheap DOM path for easy cases, vision for the rest.

- **Pro:** likely best short-term cost/perf in production.
- **Con:** two pipelines to maintain; harder to reason about evaluation; ambiguous training signal.
- **Why rejected (for now):** premature optimization for v0/v1. A reasonable thing to revisit at v2 if cost/latency on the vision path is the binding constraint.
  > **Note (2026-07-16):** what we converged on resembles C but with a crucial difference — vision is
  > not a *fallback for the same job* (finding elements); it has *different jobs* (contract
  > discovery, AX-blind rendering, labeling). That keeps the training signal unambiguous: grounding
  > trains on grounding records, state models on captures, the intent policy on the journal.

### D. "Action-first — skip the grounding intermediate."

Train `(screenshot, task) → (action_type, target_description)` end-to-end. No bbox in the middle.

- **Pro:** closer to what we actually want the agent to do.
- **Con:** harder to label, harder to evaluate (binary success vs. continuous IoU), the prediction can't be reused by the page-state, transition, or outcome models. Couples the platform to a single model architecture.
- **Why rejected:** the explicit grounding intermediate is what makes invariant #8 (target composition) work. Killing the intermediate kills the layered roadmap.
  > **Note (2026-07-16):** the Interaction API is *not* D returning through the side door. The intent
  > vocabulary is a semantic layer **above** ActionId with an explicit expansion map
  > (`contract.intent_expands_to`) — the intermediate representations survive and stay individually
  > evaluable. D remains rejected for the same reason it was.

---

## Falsifying conditions — what would make us change our mind

Each alternative has a condition under which we would seriously revisit it. Naming them up front means future debate happens against criteria, not vibes.

| Alternative | Revisit if... |
|---|---|
| A. Frontier VLM end-to-end | v0 zero-shot already hits production-acceptable accuracy AND the cost/latency of API-driven action is acceptable for the use case. In that case the platform becomes "frontier API harness with a labeling backstop for the few domains where the gap is real." |
| B. DOM-primary | ~~Vision baseline plateaus well below useful...~~ **Resolved 2026-07-16, in B's partial favor — but not via this condition.** The condition anticipated vision *failing*; what actually happened is AX *succeeding* before vision ever got data. Lesson for future conditions: name the "a cheaper path wins outright" branch, not just the "our path fails" branch. |
| C. Hybrid | At v2, vision-path serving cost or latency is the binding constraint on production traffic, and DOM-path solves >50% of cases at materially lower cost. |
| D. Action-first | The composition payoff (downstream targets reusing grounding outputs) does not materialize after a real attempt — i.e. page_state and transition models do not measurably benefit from grounding's outputs. |
| **Intent vocabulary (new)** | A whole class of real work systematically cannot be expressed as intents and lives permanently in `/probe` — i.e. discovery stops converging into endpoints for some domain family. Then the closed-vocabulary bet is wrong for that family and it needs its own surface. |
| **Teacher → distill (new)** | After the first two flywheel revolutions, L3/L4 shadow agreement stays flat despite label volume growing — i.e. the inner layers are not learning what the teacher does. Then the distillation target is mis-specified and the journal record shape (invariant #5-sibling) is the first suspect. |

If a contradicting recommendation surfaces and *none* of the falsifying conditions are met, the recommendation is noise — file it, don't act on it.

---

## Change discipline

- **What this doc carries:** intention, invariants, the chosen path, the rejected alternatives and their falsifying conditions.
- **What this doc does NOT carry:** rollout plans (those live in [PLAN_flywheel_first_revolution.md](PLAN_flywheel_first_revolution.md) and the PLAN_* docs), code-level design, sprint planning, or architectural choices that haven't survived a real evaluation.
- **How to amend it:** propose the change in the same commit as the code or evaluation result that justifies it. Reference the commit hash from this doc when it lands. The git log of this file is the project's architectural changelog.
- **How to disagree with it:** point at an invariant being wrong, or a falsifying condition being met. Both are real arguments. "I think a different model would be better" without one of those is not.

---

## Amendment log

| Date | What changed | Justified by |
|---|---|---|
| 2026-07-16 | Invariant #1 reworded (screenshot always captured; AX is the primary operating signal). Invariants #9 (journal) and #10 (closed intent vocabulary) added. Invariant #5 gained the journal record as sibling contract; #8 gained the intent-policy target. Chosen path re-sequenced: L3 first, L4 second, grounding demoted from "first." Alternative B marked partially adopted; two falsifying conditions added. Durable substrate extended with protocols + recipes. Teacher role made permanent. | Commits `553e682`, `35cbfff`, `13c0293`, `7b7c94c`, `95ad87a`; LEARNINGS 2026-07-16 (all five entries); the live-drive record of 2026-07-10 → 07-15; `DECISION_two-stacks-one-spine.md`. |
