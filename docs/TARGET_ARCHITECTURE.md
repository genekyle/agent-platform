# Target architecture — the clean break from monolith to AI-platform v1

The router split (`docs/PLAN_main-split.md`) is **layer 1 of 4**. Carving `main.py` into `routers/`
is necessary but it's *file organization*, not architecture. This doc defines what a genuine clean
break looks like, so we know when we've actually arrived — and don't mistake "main.py is smaller" for
"the architecture is solid."

The single litmus test for "clean": **the import graph is an acyclic DAG pointing inward.**
`routers → services → domain/data → kernel`. **Nothing imports `main`.** Every layer is testable
without the one above it. If that holds, the rest is detail; if it doesn't, no amount of file-splitting
made it clean.

---

## The four layers

### Layer 1 — Transport (`routers/`)  ·  *in progress*
Thin HTTP handlers grouped by domain. A handler validates input, calls a service, shapes the response
— **no business logic**. `main.py` shrinks to an **app factory** (`create_app()`), middleware/mounts,
and `include_router(...)` calls.
- **Done when:** every route lives in `routers/*.py`; `main.py` < ~300 lines and holds no handlers;
  the route-inventory guardrail still shows all 149 routes.

### Layer 2 — Bootstrap / composition  ·  *not started*
Everything that is "wire up the app," pulled out of `main.py`: the schema migrations (`_ensure_columns`
— ~60 `ALTER TABLE` tuples → a `migrations.py`, and eventually Alembic), the `REGISTRY_SEED` +
seeding logic (→ `seed.py`), and startup hooks. `main.py` orchestrates these; it doesn't *contain* them.
- **Done when:** migrations, seed, and app creation are their own modules; `main.py` calls them.

### Layer 3 — Domain / service logic  ·  *partly there*
Business logic lives in per-domain service modules, not in handlers. Many already exist and are good
models — `inventory.py` (the inventory routes are pure delegators — copy that pattern), `accounts.py`,
`apply_state_store.py`, `search_cadence.py`, `command_center.py`, `session_manager.py`. Others still
have **fat handlers** with logic inline (training, runtime, capture). The rule: a handler is ~5–15 lines.
- **Done when:** no route handler contains domain logic; each service is unit-testable without HTTP.

### Layer 4 — The agent core  ·  *the actual product; partly there*
This is what makes it *AI-as-a-platform*, not a CRUD app: the per-step loop
**classify → propose → select → act → verify** as **swappable, independently-trainable stages behind
stable interfaces**, with the **cheapest-confident cascade** explicit and the telemetry **"faucet"** a
first-class pipeline feeding training. Stage homes today: `escalation_rules` (classify),
`mcp/observer/ax_proposer` (propose), `select_stage/` (select), `mcp/executor` (act),
`select_stage/verifier` (verify).
- **Done when:** each stage is a defined interface with pluggable impls (heuristic / local-model / LLM);
  swapping an impl touches **no caller**; the cascade + budget guard + telemetry are explicit seams.
  This is the moat — it's what "gets cheaper the longer it runs" actually requires.

---

## Cross-cutting kernel
`deps` · `settings` · `schemas` · `models`/`db` · `secrets_vault` · `anthropic_usage` (budget guard) ·
telemetry/logging. Shared by all layers; imports **none** of them. This is the base of the DAG.

---

## What "v1 clean break" means (the pragmatic bar for a solo founder)
Not the full grand version — the point where the architecture stops fighting us:
1. **Layer 1 complete** — `main.py` = app factory + routers (transport split done).
2. **Layer 2 done** — migrations/seed/bootstrap extracted; `main.py` is wiring only.
3. **Layer 3: handlers thin** — logic in services; each domain testable without HTTP.
4. **Layer 4: stage interfaces clean enough to swap a model without touching callers** (the cascade is
   already cheapest-first; formalize the seam). Full stage-plugin registry is v1.5.
5. **The litmus holds:** acyclic import DAG, nothing imports `main`, each layer independently testable.

Items 1–3 are mechanical and mostly de-risked (the guardrail makes 1 safe). Item 4 is the one that
turns "a tidy FastAPI app" into "a platform." Deployment split (control-plane / agent-runtime / UI as
separate services), config management, and observability are **v2** — real, but not what "clean break"
hinges on.
