# Plan — split `main.py` into routers + one-worktree-per-session

Status: **planned, not started** (2026-07-08). Two structural fixes for a clean, collision-free
workflow. Both came out of the "why can't we commit cleanly" review — see `LEARNINGS.md`.

---

## Part 1 — carve `apps/controlplane-api/main.py` into `routers/`

### Why
`main.py` is **5,742 lines / 144 `/api` routes** in one file. Costs:
- **Collisions.** Every session edits the same file → concurrent commits step on each other (this is
  what mislabeled commit `a57a180` did to the faucet work), and even reading it, line numbers shift
  mid-edit.
- **Fights the north star.** The whole design is *swappable, independently-trainable loop stages*
  (classify / propose / select / act / verify). Those stages have module homes, but their HTTP
  surface is fused into one file, so the seams aren't real at the API layer.
- **Review + navigation** of a 5.7k-line file is slow and error-prone.

### Target structure
```
apps/controlplane-api/
  main.py          # thin: create app, run _ensure_columns/startup, include_router(...) x N
  deps.py          # shared: get_db, settings, common helpers (_artifacts_dir,
                   #   _capture_metadata_from_artifact, utcnow, ...) — imported BY routers,
                   #   never imports routers (breaks the circular-import hazard)
  routers/
    __init__.py
    training.py            # 43 routes: coverage, page-states, label_queue, promote, ...
    inventory.py           # 19
    runtime.py             # 15 + runs(2): run, run_live, run_batch, session_state
    observations.py        # 10 + capture(1): /api/capture, /api/observations
    models.py              # 10 + select(3): model eval/training endpoints
    accounts.py            # 7
    search.py              # 5 + jobs(4): sweep, targets, observed jobs
    apply.py               # application-answers(5)
    facebook.py            # 3
    domains.py             # domains(3) + command-center(1) + domain_settings
    channels.py            # 3
    sessions.py            # sessions(2) + workers(2)
    system.py              # singletons: health, system, usage, tabs, steps, dashboards, assets
```
Each file is a `fastapi.APIRouter()` with the same paths as today; `main.py` does
`app.include_router(training.router)` etc. **No path changes, no behavior changes** — pure move.

### Sequencing (one router per commit, tests green after each)
Smallest / most self-contained first, to build confidence and keep each diff revertible:
1. `deps.py` — extract the shared helpers `main.py` routes rely on. Get the app importing it and
   still green. (This de-risks every later move.)
2. `system.py` (singletons) → 3. `facebook.py` → `accounts.py` → `sessions.py` → `channels.py`
   (self-contained domains).
4. `observations.py` / `capture` → 5. `runtime.py` → 6. `search.py` / `apply.py` / `inventory.py`
   → 7. `models.py` → 8. `training.py` (biggest; may sub-split later).
9. `main.py` is now app-setup + `include_router` calls only.

Commit message per step: `refactor: extract <domain> routes into routers/<domain>.py (no behavior change)`.

### Risks & mitigations
- **Circular imports** (router → main → router). *Mitigation:* shared state lives in `deps.py` /
  `models.py` / existing modules; routers import those; `main.py` imports routers **last**.
- **Module-level singletons in main.py** (e.g. the app, migration list, startup hooks). *Mitigation:*
  keep app creation + `_ensure_columns` + `@app.on_event` in `main.py` (or an `app_setup.py`); routers
  never touch them.
- **Concurrent edits during the refactor.** *Mitigation:* run it in **one dedicated session with no
  other session touching `main.py`** — ideally in its own git worktree (Part 2). A half-moved
  `main.py` + a concurrent feature edit = the worst merge.
- **Silent route drops.** *Mitigation:* assert route count before/after: `len(app.routes)` unchanged;
  keep the full suite green at every step.

### Definition of done
`main.py` < ~400 lines (app + startup + includes); every route lives in a `routers/*.py`; `len(app.routes)`
identical to today; full suite green; one clean commit per extracted router.

---

## Part 2 — one worktree per concurrent session (stop the commit collisions)

### The problem (observed 2026-07-08)
Multiple Claude sessions share the **one** `main` working tree and do broad `git add -A` /
`git commit -am`. Result: a session's `commit -a` sweeps up **another** session's in-progress edits
into a commit with the wrong message (that's how the faucet's `main_server.py` edit landed inside
"executor file-upload"). Work isn't lost, but history lies and diffs are unreviewable.

### The norm
- **One active session on `main` at a time is fine** — that's the simple solo-dev default
  (commit directly to `main`, no long-lived feature branches).
- **The moment you run sessions concurrently, isolate them.** Each concurrent session works in its own
  **git worktree** on a short-lived, per-session branch (Claude Code's `.claude/worktrees/` isolation,
  or `git worktree add`). Fast-forward-merge to `main` when the unit of work is done and delete the
  branch. Ephemeral per-session branches are **not** the "no feature branches" rule this repo avoids —
  they live minutes-to-hours, not across features.
- **Never `git add -A` / `git commit -am` for a scoped change.** Stage explicit paths (what this
  session did). This is the backstop even inside a worktree.
- **Before committing, `git status` and confirm you own every staged path.** If a file you didn't
  touch is staged, another session put it there — unstage it.

See `CLAUDE.md` ("Working alongside other sessions") for the short version every session loads.
