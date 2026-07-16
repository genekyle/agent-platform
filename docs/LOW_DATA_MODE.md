# Low-data mode — working on a hard-capped connection

**When this applies:** the operator is tethered to a phone on roaming data with a hard cap. Anything
that downloads waits for wifi. The operator will say so; assume normal conditions unless told.

**Run `make data-check` first.** It answers the only question that matters — *"if I work now, will
anything download?"* — and exits non-zero if something would. Everything below is why.

    Warm. Safe to work on cellular.        -> go
    Wait for wifi — something would download. -> stop, tell the operator what and how big

---

## The one-line version

**Nothing this repo does is heavy once the machine is warm — the danger is a RE-TRIGGER, and the one
real ongoing cost is live browser driving.**

Measured 2026-07-16: `.venv` 1.3G, HF cache 5.6G, `node_modules` 71M, docker images 855M. All already
on disk. None of it re-downloads unless something invalidates a cache.

---

## Safe on cellular (KB, or nothing at all)

| What | Cost | Why |
|---|---|---|
| Running the test suites | **0** | all local |
| Reading/writing code, `git` on local branches | **0** | |
| `git push` / `pull` of source | ~KB | text diffs |
| Anthropic API calls (Haiku selector, `/resolve_answer` rung 4) | ~KB, ~$0.002/call | a few KB of JSON |
| **Read-only CDP against an ALREADY-OPEN tab** | **0** | `/scan_required`, `/describe_widget`, `/probe`, `/ax_scan`, `/screenshot` are a **local socket** to Chrome. The page is already loaded; observing it is free |
| The intent journal, captures, artifacts | 0 | local disk |
| `make dev` **when `data-check` is green** | ~0 | services boot from cache |

## Wait for wifi

| What | Cost | Trigger |
|---|---|---|
| `make setup` / `scripts/bootstrap-python.sh` | up to ~1.3G | any requirements file **newer than `.venv/.requirements.stamp`**. `make dev` calls bootstrap **unconditionally** |
| `npm ci` | ~71M | `apps/controlplane-ui/node_modules` missing |
| `docker compose up` (first time) | ~855M | `postgres:16` / `redis:7` not in the local image cache |
| Loading a vision model | 444M–**4.1G** | `from_pretrained` / `hf_hub_download` on an **uncached** id. Cached today: Florence-2-base (444M), OmniParser-v2.0 (1.0G), UGround-V1-2B (4.1G). A **new** model id downloads in full |
| `WebFetch` / `WebSearch` / browsing docs | varies | agent tool use |

### The pip stamp is the live landmine

`bootstrap-python.sh` re-runs `pip install` whenever any of its `REQUIREMENTS_FILES` is newer than
`.venv/.requirements.stamp`, and **`make dev` calls it every time**. The stamp had been stale since
Jul 2, so every `make dev` was already round-tripping PyPI; adding `packages/interaction/pyproject.toml`
to the watch list made that permanent. Refreshed 2026-07-16 after verifying the env is genuinely
current (309 tests pass, `pip install --dry-run` reports nothing to install).

**If you edit a requirements file or `packages/interaction/pyproject.toml`, the next `make dev` will
pip-install.** On wifi that's fine. On cellular: make the edit, but don't run `make dev` — or, if the
deps really are already installed, `touch .venv/.requirements.stamp` and re-run `make data-check`.
Only do that when it's true; a stamp that lies is worse than a slow boot.

---

## The cost `data-check` can't see: live driving

This is the one that actually eats a cap, and it's invisible to every cache check above.

- **A page load is real MB.** An Indeed results page or a KKR posting is ~5–20M with images and JS.
  A dozen page loads is a couple hundred MB.
- **`/navigate` and reloads cost.** PRINCIPLES §1 says to reload a tab before driving it because live
  state goes stale — that advice is correct and it is not free. On a cap, prefer working the tab
  that's already open, which is also what the bot-safety rule wants (reach states by clicking, and
  don't churn tabs).
- **react-select typing fetches per keystroke.** `/select_option` on a react-select sends TRUSTED
  per-char keys precisely because the widget fetches its options server-side on real input — that's
  the whole mechanism. Typing "United States" is ~13 round-trips. Each is small; it adds up across a
  form.
- **Observing is free; acting is not.** The asymmetry is useful: you can `/scan_required`,
  `/describe_widget` and `/probe` an already-open form all day for nothing. It's `/navigate`,
  `/select_option` and `/set_date` that talk to the network.

**So on a cap: audit, plan and write code freely. Defer the live drive to wifi.**

---

## What this means for the current work

The read-only half of the Interaction API is live-validated. The **write** half
(`/select_option`, `/set_date`, `/check_group`) has never touched a live page, and that test is a
driving session — KKR's form is one field from Submit (`#question_17811150004`).

That test is **wifi work**: it loads pages, and the attestation is a react-select, so it fetches per
keystroke. Everything else in the backlog — the `/api/interact/*` intent surface, wiring a reasoner
into `resolve_answer`'s rung 4, more tests — is local and safe on cellular.
