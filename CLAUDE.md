# Ops Pilot — orientation for a new session

A **supervised browser agent** that runs a per-step loop (classify → propose → select → act → verify)
where each decision is made by the cheapest confident tool, a human catches anything uncertain, and
every correction becomes training data for local models that take work off the expensive LLM.

## Read these first, in order

1. **`docs/LEARNINGS.md`** — the append-only cross-session log: assumptions we corrected, non-obvious
   behavior, and where each fix landed. **Read it before assuming anything about how the system
   works**, and **append to it** whenever you learn something load-bearing this session. This is our
   cross-session memory; a lesson that lives only in chat or a one-off endpoint gets paid for again.
2. **`docs/PRINCIPLES.md`** — the durable invariants (each ideally backed by an enforcement point).
3. **`docs/PROJECT_STATUS.md`** — current state of the loop and the open gaps.
4. **`docs/interaction-layers.md`** — the AX/node driver vs. bespoke DOM case study (the FB-login saga).

## The few rules most likely to bite you (details in PRINCIPLES.md)

- **Drive through the CDP-AX / node interaction layer** (role + accessible-name → `backend_node_id`),
  never a bespoke `querySelector`/coordinate workaround. When a flow breaks, first ask *which layer
  it's on*. Domain quirks go in the distilled recipe (e.g. `facebook_recipe.py`), not an endpoint. §6
- **Reach states by clicking like a human**; URL-forcing is last-ditch (flag-raising on real accounts). §3
- **Never auto-solve captchas / 2FA / checkpoints** — classify → escalate to the human, $0.
- **Capture per meaningful page *state*, never secrets** (state identity only in credential flows). §4
- **Resource-efficiency is a hard constraint** (solo founder; $5/week autonomous spend cap enforced).
- **Solo dev: commit directly to `main`, no feature branches**, unless told otherwise.
- **Low-data mode** — when the operator says they're on roaming/tethered data (hard cap), run
  `make data-check` first and **defer anything that downloads to wifi**. The short version: once the
  machine is warm, almost nothing here costs data *except live browser driving* (a page load is
  ~5–20M; react-select typing fetches per keystroke). Read-only CDP against an already-open tab is a
  local socket and free — so audit, plan and write code freely; defer the live drive.
  See `docs/LOW_DATA_MODE.md`.

## Working alongside other sessions

One active session on `main` at a time is fine. But multiple sessions have shared this one working
tree and clobbered each other via broad `git add -A` / `commit -am` (a scoped edit got swept into an
unrelated commit — 2026-07-08). So:
- **Stage explicit paths, never `git add -A` / `commit -am`** for a scoped change.
- **`git status` before committing — confirm you own every staged path.** A file you didn't touch
  means another session staged it; unstage it.
- **Running sessions concurrently? Isolate each in its own git worktree** (`.claude/worktrees/`) on a
  short-lived per-session branch, fast-forward-merge to `main` when done. (Ephemeral ≠ the long-lived
  feature branches we avoid.) See `docs/PLAN_main-split.md` Part 2.

## Layout

`apps/controlplane-api/` — FastAPI control plane (recipes, runtime loop, training, DB models).
`apps/mcp/` — the capture server + AX/vision proposers + the browser executor/driver.
`apps/controlplane-ui/` — the operator cockpit (Vite/React).
