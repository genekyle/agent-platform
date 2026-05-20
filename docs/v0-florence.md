# v0 Implementation Spec — Florence-2 Zero-Shot Grounding

This is the implementation contract for v0 of `vision_element_grounding`. Treat it as the single source of truth while v0 is being built. Background and rationale live in [training.md](training.md) and [architecture.md](architecture.md).

**v0 goal:** put a measurable, ownable, swappable model on the platform — visible in the UI, callable from an HTTP endpoint, evaluated against reviewed captures with one number per scenario.

**Model:** `microsoft/Florence-2-base` (~230M params). Zero-shot via the `<CAPTION_TO_PHRASE_GROUNDING>` task. Open weights, no API keys, runs on CPU/MPS/CUDA. v1 will be a fine-tune of the same base.

---

## File layout

```
apps/controlplane-api/
  models/                              ← NEW
    __init__.py
    registry.py                        ← model registry: register, list, get
    eval.py                            ← eval runner contract; provider-agnostic
    v0_florence.py                     ← Florence-2 wrapper; lazy-loaded
  main.py                              ← +5 endpoints under /api/models
  models.py                            ← +ModelRegistry, ModelEvalRun ORM
  schemas.py                           ← +Model* pydantic schemas
  requirements.txt                     ← +transformers, torch, pillow, einops, timm

output/                                 ← (under settings.observer_artifacts_dir)
  models/
    {model_id}/
      eval_runs/
        {iso_timestamp}/
          metrics.json
          predictions.jsonl
          run.log

apps/controlplane-ui/src/components/controlplane/
  ModelsSection.jsx                    ← NEW container
  ModelsRegistry.jsx                   ← NEW
  ModelEvalRuns.jsx                    ← NEW
  ModelRunDetail.jsx                   ← NEW
  navigation.js                        ← +models nav entry
App.jsx                                ← +models view wiring
```

---

## Backend

### Inference contract (`models/v0_florence.py`)

```python
class FlorenceHandle:
    """Lazily-loaded Florence-2 model + processor. Cached at module level."""
    model: Any           # AutoModelForCausalLM
    processor: Any       # AutoProcessor
    device: str          # "cuda" | "mps" | "cpu"
    model_name: str      # "microsoft/Florence-2-base"

def load_florence(model_name: str = "microsoft/Florence-2-base") -> FlorenceHandle: ...

def predict_bbox(
    *,
    handle: FlorenceHandle,
    screenshot_path: str,
    element_query: str,
) -> PredictBboxResult: ...
```

`PredictBboxResult`:
```python
{
  "bbox": {"x": float, "y": float, "width": float, "height": float} | None,
  "all_bboxes": [...],          # full list returned by Florence, in case we want top-k later
  "raw_response": str,           # whatever Florence's text decoder produced, for debugging
  "confidence": float | None,    # Florence does not emit a calibrated confidence; None for v0
  "latency_ms": int,
}
```

Florence-2 specifics: build the prompt as `f"<CAPTION_TO_PHRASE_GROUNDING> {element_query}"`, run `processor → model.generate → processor.post_process_generation` with `task="<CAPTION_TO_PHRASE_GROUNDING>"`. Take the highest-area or first-returned bbox if multiple are returned (we'll iterate on selection later — for v0, take the first).

### Eval runner (`models/eval.py`)

```python
def run_eval(
    *,
    db: Session,
    artifacts_root: Path,
    model_id: str,
) -> ModelEvalRunResult:
    """
    1. Load model_registry row by model_id.
    2. Pull all TrainingCapture where review_status in {reviewed, approved}
       AND approved_bbox is not null AND element_query is not null.
    3. Filter to eval split using the same _stable_split hash logic as training.py.
    4. For each capture: load screenshot, call predict_bbox, compute IoU + center_in_target.
    5. Aggregate overall + per-scenario metrics (same shape as train_grounding_model output).
    6. Write metrics.json + predictions.jsonl + run.log under
       output/models/{model_id}/eval_runs/{iso_timestamp}/.
    7. Insert ModelEvalRun row with status='success', metrics, artifact_dir.
    """
```

Metrics shape (matches `train_grounding_model` for UI reuse):
```python
{
  "record_count": int,
  "mean_bbox_iou": float,
  "iou_at_50_accuracy": float,         # fraction of predictions with IoU >= 0.5
  "center_in_target_accuracy": float,  # predicted bbox center inside approved bbox
  "mean_latency_ms": int,
  "per_scenario": {
    "{scenario_id}": {
      "record_count": int,
      "mean_bbox_iou": float,
      "iou_at_50_accuracy": float,
      "center_in_target_accuracy": float,
    }
  },
}
```

Center-in-target is a deliberately permissive metric — useful for UI grounding because clicking the bbox center usually triggers the right action even with a loose bbox.

Eval is **synchronous for v0**. With a small number of records and CPU inference, it'll take seconds to a few minutes. v1 will move to a job queue.

### Registry (`models/registry.py`)

```python
def register_model(db: Session, *, target_id: str, implementation: str,
                   model_name: str, config: dict | None = None) -> ModelRegistry: ...
def list_models(db: Session) -> list[ModelRegistry]: ...
def get_model(db: Session, model_id: str) -> ModelRegistry | None: ...
def get_last_eval(db: Session, model_id: str) -> ModelEvalRun | None: ...
```

The "model_id" is a stable string: `{target_id}__{implementation}`, e.g. `vision_element_grounding__v0_zero_shot_florence2_base`. Implementations dispatch by string in `eval.py`:

```python
IMPLEMENTATIONS = {
    "v0_zero_shot_florence2_base": run_eval_florence_zero_shot,
}
```

Adding a new model means adding an entry here and a wrapper module. No core changes. **This is the swap point that makes the platform thesis real.**

### DB schema additions (`models.py`)

```python
class ModelRegistry(Base):
    __tablename__ = "model_registry"
    id = Column(String, primary_key=True)              # e.g. vision_element_grounding__v0_zero_shot_florence2_base
    target_id = Column(String, nullable=False)         # vision_element_grounding
    implementation = Column(String, nullable=False)    # v0_zero_shot_florence2_base
    model_name = Column(String, nullable=True)         # microsoft/Florence-2-base
    config = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    archived_at = Column(DateTime(timezone=True), nullable=True)

class ModelEvalRun(Base):
    __tablename__ = "model_eval_run"
    id = Column(String, primary_key=True)              # uuid
    model_id = Column(String, ForeignKey("model_registry.id"), nullable=False)
    dataset_id = Column(String, nullable=True)         # snapshot of dataset_id used (or null for live DB read)
    status = Column(String, nullable=False)            # pending | running | success | failed
    started_at = Column(DateTime(timezone=True), default=utcnow)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    record_count = Column(Integer, nullable=False, default=0)
    metrics = Column(JSON, nullable=True)
    artifact_dir = Column(String, nullable=True)
    error = Column(String, nullable=True)
```

`Base.metadata.create_all` already runs on app startup, so no migration tooling required for v0.

### API endpoints

```
GET    /api/models                             → list registered models, each with last_eval summary
POST   /api/models/seed                        → seed the v0 Florence baseline (idempotent)
GET    /api/models/{model_id}                  → model detail + last 10 eval runs
POST   /api/models/{model_id}/eval             → run eval synchronously, return ModelEvalRun
GET    /api/models/eval-runs                   → list all eval runs across models, recent first
GET    /api/models/eval-runs/{run_id}          → eval run detail with sample of predictions
```

`POST /api/models/seed` registers `vision_element_grounding__v0_zero_shot_florence2_base` if it doesn't exist yet. This is the "register baseline" button on the UI.

### Dependencies

Add to `apps/controlplane-api/requirements.txt`:
```
torch>=2.2
transformers>=4.45
pillow>=10.0
einops>=0.8
timm>=1.0
```

Notes:
- On Mac, `torch` ships with MPS support out of the box; Florence will run on `mps` device.
- First call to `load_florence()` downloads ~460MB of weights to the HuggingFace cache (`~/.cache/huggingface/`). One-time cost.
- Florence-2 ships with custom code, so the `from_pretrained(...)` call needs `trust_remote_code=True`.

---

## Frontend

### Navigation (`navigation.js`)

Add a `models` entry to `CONTROL_PLANE_NAV`:

```js
models: {
  label: "Models",
  title: "Models",
  subtitle: "Registered models for each training target, eval runs, and per-scenario metrics.",
  sections: [
    {
      id: "registry",
      label: "Registry",
      subtitle: "Models registered against each training target with their last-eval summary.",
    },
    {
      id: "eval-runs",
      label: "Eval Runs",
      subtitle: "Recent eval runs across all models, ordered by recency.",
    },
    {
      id: "run-detail",
      label: "Run Detail",
      subtitle: "Per-scenario metrics and a sample of predictions for one selected eval run.",
    },
  ],
},
```

Add `models` to the `canEnterSecondary` set in `App.jsx`.

### Components

- **`ModelsSection.jsx`** — top-level container, switches by `section` prop. Owns `models`, `selectedModelId`, `evalRuns`, `selectedRunId`, `runDetail` state. Loads `/api/models` and `/api/models/eval-runs` on mount.
- **`ModelsRegistry.jsx`** — table of models. Columns: model_id, target, implementation, last_eval (timestamp + mean IoU), actions (Run Eval). Empty state with a "Register v0 Florence Baseline" button that hits `POST /api/models/seed`.
- **`ModelEvalRuns.jsx`** — table of eval runs across all models. Columns: run id, model, started_at, status, record_count, mean IoU, IoU@50, click → run detail.
- **`ModelRunDetail.jsx`** — picked run. Top: overall metrics card. Middle: per-scenario breakdown table. Bottom: sample of predictions with `predicted_bbox` / `approved_bbox` shown side-by-side as JSON for v0 (overlay rendering deferred to v1).

### What v0 UI does NOT include (deliberately)

- No bbox overlay on screenshots — JSON view only. Overlay is real work; defer.
- No live progress bar during eval — synchronous request, spinner is enough.
- No model comparison view — single-model focus. Comparison comes when there are 2+ models.
- No fine-tune controls — v0 is zero-shot only. Training UI lands with v1.

---

## Eval contract (the durable part)

This is the part that survives every model swap. The model changes; the eval contract does not.

**Input to a model implementation:** `(screenshot_path, element_query, viewport)` — the "prediction request."

**Output from a model implementation:** `PredictBboxResult` shape above. Bbox in **screenshot pixel coordinates** (same as `approved_bbox`).

**Eval split:** stable hash on `artifact_filename`, modulo 10, bucket < 2 → eval. Identical to `_stable_split` in `training.py`. Do not duplicate the function — import it.

**Metrics:** `mean_bbox_iou`, `iou_at_50_accuracy`, `center_in_target_accuracy`, `mean_latency_ms`. Per-scenario breakdown of the same fields.

**Persistence:** one directory per eval run under `output/models/{model_id}/eval_runs/{iso_timestamp}/` containing `metrics.json` + `predictions.jsonl`. One DB row in `model_eval_run` pointing at it.

When v1 ships a fine-tuned Florence model, only `IMPLEMENTATIONS` and a new wrapper module change. The eval runner, the metrics shape, the storage layout, the UI — none of it moves.

---

## What ships in v0 vs. what comes next

**v0 (this spec):**
- Florence-2-base zero-shot wrapper
- Eval runner against existing `approved_bbox` captures
- Models tab with registry + eval runs + run detail (JSON view)
- Seed endpoint to register the baseline

**v1 (next):**
- Fine-tune Florence-2-base on curated dataset; second registry entry
- Background job queue for eval runs
- Bbox overlay rendering in run detail
- Model comparison view (v0 vs. v1 on same eval split)
- HTTP serving endpoint that the agent/observer layer can call

**v2:** see [training.md](training.md).
