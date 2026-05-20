# Training Plan

## First model: `vision_element_grounding` — locked

The first model this platform trains is **vision element grounding**.

- **Input:** a screenshot + a natural-language element query (e.g. *"click the Apply Now button"*), conditioned on context (`domain_id`, `goal_id`, `task_id`, `action_type_hint`, `capture_profile`, viewport, url).
- **Output:** a bounding box for the target element on the screenshot.
- **Source of truth in code:** [apps/controlplane-api/training.py](../apps/controlplane-api/training.py) — `TRAINING_TARGETS[0]` is `vision_element_grounding`, `stage: "primary"`, marked as the model that *"feeds all downstream tasks."* Dataset format: `grounding_vision_v1` (`build_vision_dataset`).

## Why this is the first model

1. **It's the bottleneck.** Page state classification, state transitions, and task outcome all consume "where is the element on the screen?" If grounding is wrong, every downstream model trains on noise.
2. **Architecture-agnostic data spec.** A `grounding_vision_v1` record is just `(screenshot, query, bbox, context)`. The model architecture can be swapped — linear ranker → frontier VLM zero-shot → fine-tuned grounding head — without re-labeling. The training target is the durable decision; the architecture is replaceable.
3. **Cheapest labeling unit per signal.** A single human bbox annotation produces one usable training example. Page state, transitions, and outcomes need richer multi-step annotation flows.

## Status of other targets

The other targets in [training.py](../apps/controlplane-api/training.py) (`page_state_classifier`, `state_transition`, `task_outcome`) remain the eventual roadmap, but they are **not** training targets yet. They depend on grounding working first.

The legacy linear ranker (`grounding_v1` / `train_grounding_model`) is kept for backwards compatibility with existing reviews. It is **not** the first model. New work targets `grounding_vision_v1`.

## Next step

Get `vision_element_grounding` good enough to run a full pipeline including training. That means, in order:

1. **Pipeline completeness, end to end:** capture → review with `approved_bbox` → `build_vision_dataset` → train → metrics. Every stage must run on real data without manual hand-holding.
2. **Enough labeled data to train a baseline:** ~50 reviewed captures across 2–3 scenarios is the threshold to make architecture choice empirical instead of theoretical.
3. **Baseline model run:** at that point, evaluate which model architecture (zero-shot VLM vs. fine-tuned grounding head vs. something else) produces usable bboxes on held-out captures.

Architecture debate is **deferred** until the pipeline is complete and there is real labeled data to evaluate against.

## What this document is for

The first model decision keeps drifting under contradicting outside opinions. This file is the anchor: when a contradicting recommendation surfaces, point at this doc instead of relitigating. Updating the first-model decision requires updating this file.
