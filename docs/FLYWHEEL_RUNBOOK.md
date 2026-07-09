# Flywheel runbook — drive → label → train

The operator's loop for turning driving into cheaper local models. Every piece below is wired and
verified (2026-07-09). The flywheel's bottleneck is **label volume**, not infra — so the highest-
leverage action is almost always "label the backlog."

## Current state (check before you start)
```
GET /api/training/coverage        # totals.total_captures / dry_captures + per-state gaps
GET /api/training/label_queue     # prioritized captures needing a golden label
```
As of 2026-07-09: **86 draft captures already waiting to label** (83 indeed, 3 FB), 138 with no golden
label. You do NOT need to drive first — there's a labeling backlog ready now.

## 1. Bring up the stack
```
scripts/dev-up.sh
# control-plane API  -> :8081   (uvicorn main:app, the app factory create_app())
# capture server     -> :8082   (writes the .ax.json sidecars)
# UI (labeler)       -> :5173
```
A training session launches its own Chrome on a per-session debug port (9322+), isolated per account.

## 2. Label the backlog (the flywheel's real crank)
In the UI labeler, work the queue. Per capture the loop is:
1. `GET /api/training/label_queue` → next capture (has `priority`, `has_golden`, `suggestion_confidence`).
2. `GET /api/observations/{filename}` → screenshot + `ax_candidates` (the pool to pick from).
3. `PATCH /api/observations/{filename}` with
   `training_annotation: {positive_candidate_id, approved_bbox?, review_status: "reviewed", ...}`
   → writes the golden label (`label_source="human"`, `verified_at` stamped).

That golden label now **reaches the trainers** — the grounding/vision dataset builders read the
`.ax.json` sidecar (fixed 2026-07-09), so a `cdp-ax-*` pick is trainable even without an explicit
`approved_bbox`.

## 3. Drive for more captures (both modes now produce labelable rows)
- **Deliberate capture:** create/start a training session, drive the humanized driver to a meaningful
  state, `POST /api/capture`, PATCH `observed_page_state`. Always produced healthy sidecars (38–166
  candidates).
- **Autonomous loop:** `POST /api/runtime/run_live` now persists each **non-empty** capture as a
  **draft** `TrainingCapture` (skip-empty + fingerprint-deduped within the run). The response's
  `recorded_captures {captures, skipped_empty, skipped_duplicate}` is your live signal. Empty sidecars
  are now rarer — `_discover_target` retries mid-navigation target gaps.

Both land as `review_status="draft"` → straight into the label queue (step 2).

## 4. Retrain + check the model
```
POST /api/training/train                 # grounding/select model on reviewed captures
POST /api/training/train_stage_observer  # L3 v0 auth/page-state classifier
```
Read the returned `metrics`. Baselines (2026-07-09): L3 stage observer **94% held-out** on 98 labels;
grounding **0%** on 19 records (data-starved — this is what labeling lifts). Models write to the
gitignored `apps/mcp/output/models/<ts>__<name>/`.

## The one number to watch
Grounding accuracy climbing off 0% as `has_golden` rises. That's the flywheel proving it turns:
more labels → better local model → less Haiku. Don't chase concurrency/infra until this is moving —
it's premature (see `docs/TARGET_ARCHITECTURE.md`).
