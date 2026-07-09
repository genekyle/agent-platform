# Project Status — Supervised Browser Agent

_Last updated: 2026-06-15_

## What we're building (one paragraph)

A **supervised browser agent** that runs a per-step loop — classify → propose →
select → act → verify — where each decision is made by the **cheapest tool that's
confident**, a human catches anything that reaches the top, and every escalation/
correction is logged as training data. Over time the logged data trains **cheaper
local models** that take work off the expensive LLM, so the same task gets cheaper
and more autonomous the longer it runs. This is the "flywheel." Hard constraint:
resource-efficient (solo founder) — a **$5/week autonomous spend cap** is enforced.

---

## The per-step loop — status of each stage

| Stage | What it does | Status | Where |
|---|---|---|---|
| **classify** | Is this a STOP screen (captcha/2FA/checkpoint)? → escalate to human, $0 | ✅ built | `escalation_rules.py` |
| **propose** | CDP accessibility tree → candidate elements (role+name+bbox+backend_node_id) | ✅ built | `mcp/app/observer/ax_proposer.py` |
| **select** | Pick the target element, cheapest-first: cache → Haiku SoM | ✅ built | `select_stage/` |
| **act** | Move + click in the live browser (pluggable cursor drivers) | ✅ built (not yet fired live) | `mcp/app/executor/` |
| **verify** | Did the page change as predicted? retry once → escalate | ✅ built | `select_stage/verifier.py` |

**Guardrails (all built):** $5/week budget cap (`anthropic_usage.enforce_budget`),
human escalation on stop-state / over-budget / low-confidence / no-match /
verifier-fail, and per-selection telemetry logging.

---

## The inner loop (the SELECT cascade) — cheapest-first

The "inner loop" is how a candidate gets selected. Layers, cheapest first:

| Layer | Tier | Status |
|---|---|---|
| 1 | Deterministic state machine (known url+state+template) | ⬜ not built → falls through |
| 2 | **Cache / fingerprint** (reuse a prior pick, FREE) | ✅ built |
| 3 | Tiny local page-state classifier (no API cost) | ⬜ not built → falls through |
| 4 | Micro-model candidate selector (cheap) | ⬜ not built → falls through |
| 5 | **Claude Haiku SoM** (budget-gated catchall) | ✅ built |
| 6 | Vision-native / human (canvas, AX-blind, low-conf) | ⬜ escalate to human for now |

Today work is done by **Layer 2 (cache)** and **Layer 5 (Haiku)**. Layers 1/3/4
are deliberately empty — they get **earned from data** once the logs show Haiku is
being reached too often (don't build ahead of evidence).

---

## Phases completed (the SELECT-stage V1 build)

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Frozen schema (enums, dataclasses, Haiku output schema, version) | ✅ |
| 1 | StateFingerprintV1 (route template + viewport + AX/DOM summary) | ✅ |
| 2 | SelectionCacheV1 (exact-match, versioned key) | ✅ |
| 3 | HaikuSelectorV1 (frozen schema, budget-gated, prompt-cached) | ✅ |
| 4 | Selector orchestrator + SelectionTelemetry | ✅ |
| 5 | TrajectoryDriver + DirectDriver + RecordOnlyDriver | ✅ |
| 6 | MinimumJerkDriver (feature-flag off) | ✅ |
| 7 | ActionVerifierV1 (AX/DOM delta + bounded retry → escalate) | ✅ |

**Also built along the way:** CDP-AX proposer; $5/wk budget cap + API Usage
panel; captcha stop-state (verified on a real reCAPTCHA); session purpose flag
(data_collection vs production); Lab UI (Model Test, Select Metrics, Visualization,
**Movement Playground**); menu slimmed 8→4 top keys; OmniParser demoted to parked
super-fallback. Test coverage: 24 select_stage tests + 6 executor tests, all green.

---

## The models — what exists vs what needs training

**Nothing in the loop is a trained model yet.** Everything is currently zero-shot
(Haiku), deterministic (min-jerk, cache, rules), or not-yet-built. That's by
design — we collect data first, train later. Here's the full model roster:

| Model | Role | State | Trained from |
|---|---|---|---|
| Haiku SoM selector | select Layer 5 | zero-shot (no training) | n/a — it's the API |
| **Tiny page-state classifier** | select Layer 3 | ⬜ not built | `selection_telemetry.jsonl` |
| **Micro-model selector** | select Layer 4 | ⬜ not built | `selection_telemetry.jsonl` |
| **Diffusion input model** | act / cursor motion | ⬜ not built (min-jerk placeholder) | `cursor_trajectories.jsonl` |
| vision_element_grounding | grounding (legacy track) | zero-shot baseline; has train+eval scaffolding | training captures + labels |
| page_state_classifier | perception | ⬜ planned | training captures + labels |
| state_transition | look-ahead | ⬜ planned | run/trajectory labels |
| task_outcome | per-task success | ⬜ planned | run/trajectory labels |
| Vision-native grounder | select Layer 6 super-fallback | ⬜ later (Modal GPU) | distilled from corpus |

### The corpora are being collected NOW (this is the key part)

| Corpus | File | Feeds |
|---|---|---|
| Loop steps | `cache/loop_steps.jsonl` | L4 selector, state_transition (per-step trajectory) |
| Selection telemetry | `cache/selection_telemetry.jsonl` | tiny classifier (L3), micro-model (L4) |
| Cursor trajectories | `cache/cursor_trajectories.jsonl` | diffusion input model |
| Training captures + labels | `observer-traces/` + `.ax.json` + annotations | grounding, page_state_classifier |
| Escalation examples | labeled stop-state captures | the classifier's "STOP" class |

Every time you run a selection, record a path in the Playground, or label a
capture, the relevant corpus grows. **The data plumbing is done; the trainers are
the remaining work.**

---

## "Are we building toward constantly training all parts of the loop?"

**Yes — and here's exactly where that stands.** The vision is a multi-model
flywheel: each layer/model has (a) its own corpus, (b) its own trainer, (c) a
shared eval contract + model registry, so they can each be retrained
independently and repeatedly as data accumulates.

- ✅ **Data collection** for every model — built (the corpora above).
- ✅ **Eval + registry substrate** — built (`model_lib`, eval-runs, Lab metrics,
  the telemetry that IS the feature set).
- ⬜ **The trainers themselves** — not built for the new layers (L3/L4, diffusion).
  The grounding track has training scaffolding; the select-stage + input models do not.
- ⬜ **Continuous/scheduled retraining** — not built. Today retraining would be
  manual. The end state is periodic auto-retrain of each layer from its corpus.

So: **we have built the foundation that makes continuous multi-model training
possible** (data + eval + registry + the cheapest-first architecture that lets a
trained layer slot in without touching the rest), but the **training jobs and the
retrain scheduler are still ahead.**

---

## What still needs help (remaining work)

1. ✅ **Runtime loop orchestrator wired (record-only)** — `runtime/loop.py` drives
   classify→propose→select→act→verify→repeat as a pure, port-based engine
   (`Proposer`/`Actor` injected, so it's unit-tested without a browser; 7 tests).
   `RecordOnlyActor` is the default: it logs the decided intent and executes
   nothing. Endpoint `POST /api/runtime/run` runs it against a real capture, safely.
   Every step appends to a new corpus, `cache/loop_steps.jsonl` (feeds L4 / state-
   transition). **Next increment:** the live multi-step driver — a `Proposer` that
   re-captures each step + a real executor driver for autonomous action. Held pending
   go-ahead (still record-only until then).
   - ✅ **Batch corpus replay** — `POST /api/runtime/run_batch` replays every stored
     capture through the record-only loop, filling `loop_steps.jsonl` +
     `selection_telemetry.jsonl` with **no inputs fired**. Idempotent (skips states
     whose fingerprint is already in the corpus); `force=True` refreshes cache/
     telemetry at ~$0 (cache hits) without duplicating trajectory rows; `limit=N`
     caps spend on a cold run. This is the mechanism that turns accumulated captures
     into training rows for L3/L4.
   - ⚠️ **Corpus can't be backfilled from history — but the faucet is open and flowing.**
     Select needs AX candidates, which come only from a capture's `.ax.json` sidecar
     (live CDP at capture time), so captures from *before* emission began (2026-06-15,
     commit `80dd253b`) can never get one — a dead session can't be re-scanned. That's
     the real dead end. But emission is **unconditional** on every live path
     (`_write_ax_sidecar` in `POST /capture`), and the current DB has **157 captures,
     all carrying AX candidates** (the old "3 of 175" was an early snapshot, now stale).
     Per-capture yield is recorded on `TrainingCapture.ax_candidate_count` (v16) and
     summarised as `dry_captures` in `/api/training/coverage`. See `docs/LEARNINGS.md`
     (2026-07-08 faucet entry) for the full picture and the two senses of "backfill".
2. **Fire real inputs** — DirectDriver can click the live browser; held pending go-ahead.
3. **Build the trainers** — diffusion input model (#7), tiny classifier (L3),
   micro-model (L4), and the planned brain models (page_state/transition/outcome).
4. **Continuous retraining** — a mechanism to retrain each layer from its corpus on
   a cadence and promote it into the cascade when it beats the current tier.
5. **Cleanup** — full OmniParser removal (UI vision code + dropped eval runner).

---

## Short-term vs long-term goals

**Short term (next):**
- Wire the runtime loop (record-only/safe), so the agent runs a real task and the
  corpora fill from real usage, not hand-tests.
- Run the 30-day n=1 and watch the Lab flywheel metrics (cache-hit↑, escalation↓,
  cost/task↓).

**Long term:**
- Train each cheap local layer from its corpus → push work down the cascade →
  Haiku reached less → cost falls.
- Train the diffusion input model on Playground recordings → human-like motion
  replaces min-jerk.
- Add the vision-native super-fallback on Modal GPU for canvas/AX-blind pages.
- Stand up continuous retraining so all layers improve repeatedly as data grows —
  the full flywheel.
