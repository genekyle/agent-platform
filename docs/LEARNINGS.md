# Learnings — the running cross-session log

**If you are a new session, read this first.** This is the append-only log of things we *discovered*
the hard way — mistaken assumptions we corrected, non-obvious facts about how the system actually
behaves, and where the durable fix landed. It exists because the same lessons kept getting re-derived
(and re-lost) session after session, buried in one-off endpoint patches and chat scrollback that the
next session can't see.

**How this relates to the other docs:**
- **`LEARNINGS.md`** (this file) — a *dated running log* of what we found out. Newest first. Some
  entries graduate into a principle or an invariant; when they do, they say so and link the code.
- **`PRINCIPLES.md`** — the *durable invariants* the system is built to embody, ideally each backed
  by an enforcement point in code.
- **`PROJECT_STATUS.md`** — the *current state* of the per-step loop and the open gaps.
- **`interaction-layers.md`** — the deep-dive on the AX/node driver vs. bespoke DOM (the FB-login saga).

**The ritual:** every session, when you learn something load-bearing — an assumption that was wrong,
a behavior that surprised you, a fix and where it went — **append an entry here**. Prefer encoding it
as code/recipe/invariant *and* logging the pointer here. A lesson that lives only in a 60-line endpoint
or a chat transcript is a lesson the next session will pay for again.

Entry format: `## YYYY-MM-DD — <title>`, then *what we believed*, *what's actually true*, and
*where it's encoded now* (link the code/recipe/doc, not just prose).

---

## 2026-07-09 — Training-UI flywheel overhaul + teacher-auto-labeling proven live + Indeed pre-auth setup

**Training UI was the flywheel's hidden blocker; now surfaced (4 commits `6d6478d`..`8fe4759`).**
The good **queue labeler already existed but was buried in Lab** (`TrainingSpaceSection`), while the
Training section routed you through a 6-level Dataset Browser dig. Fixes: (#1) Command Center
`🏷️ To label` KPI + per-domain backlog rows, fed by `command_center.build_summary`'s new `flywheel`
block + per-tile `training`; (#2) the "To label" tile is one-click into the queue labeler
(`openLabeler`); (#4) promoted the queue labeler to **Training → 🏷️ Label** (first in nav), demoted the
nested path to "Inspect capture", Dataset Browser to "browse+curate"; (#3) `label_queue?domain=` filter
+ Domain pills in the labeler. Also added a **🗑 Delete** action (DELETE `/api/observations/{fn}`) for
coarse/bad captures, and gmail `email_entered`/`password_entered` substates.

**The action model (the mental unblock).** The system is `(before_state) → [act on ONE element] →
(after_state)`. A label yields TWO signals from one golden pick: SELECT (which element → AX-CDP selector)
+ TRANSITION (post_action_state → planner). A capture is bad when driving was too COARSE and skipped
actions (the classic "sign-in page → inbox" that really did type-email→Next→type-password→Sign-in). No
clean single-action transition → **delete it**. The real cure is **capture PER-ACTION when driving**.

**Teacher-auto-labeling — PROVEN LIVE.** Claude drives → captures a clean state → labels it ITSELF,
zero human. Mechanism: `POST /api/capture {training_session_id, tab_id}` → `PATCH
/api/observations/{fn} {training_annotation:{positive_candidate_id, review_status:"reviewed"},
observed_page_state, post_action_state}`. Because Claude knows what the screen IS + which element it
would act on + where it leads, the labels come free. (label_source becomes "human" = teacher-trust;
no separate "teacher" tier yet — a possible refinement.)

**Indeed pre-auth login setup (in progress).** The persistent `indeed` profile had **no cookies** →
that's why fresh Indeed sessions hit Google's wall (only `facebook`/`business_chrome_profile` were
pre-authed). Persistent profiles live at `/tmp/agent-platform-training-chrome/persistent/<name>` (NOT
reboot-durable — move out of /tmp is a follow-up). Setup = create a session bound to the `indeed_default`
account (→ `persistent_profile=indeed`) + start it (launches Chrome `--user-data-dir=.../indeed`) + do a
**supervised login ONCE** (human clears Google/2FA/code; Claude never auto-solves auth) → profile
persists. That one supervised login IS the per-action login-capture opportunity.

**KEY (2026-07-09, user): Indeed FORCES Google login when the email is already a Google account** — the
email-code fallback won't apply; it redirects to Google SSO. The **human does the Google login** (safe:
human clicks, no automation-flagging). Cross-domain auth (Google login for Indeed, Gmail code as an
errand) is a candidate for an explicit **errand section/flow** — see
[[project_planner_and_cross_domain]].

**Live handoff at compaction:** session **#16** (indeed_jobs, account indeed_default, persistent
`indeed`) is ACTIVE, Chrome on **:9322**, tab was on `secure.indeed.com/auth`. Already captured +
teacher-labeled the entry state (`indeed_login_email` → golden=Email field `cdp-ax-1170c306b0` →
`email_sso_or_code_choice`). Next: user completes the (Google) login; capture + teacher-label each
subsequent state; then the profile is pre-authed for all future Indeed drives.

## 2026-07-09 — Training works today; the grounding/vision datasets were BLIND to AX-sidecar golden labels

**What we believed.** That the flywheel was blocked by the backend / concurrency / missing trainers,
and that the grounding model was hopelessly data-starved (only 4 usable records).

**What's actually true.** Training already works: `POST /api/training/train_stage_observer` (the L3 v0
"am I logged in?" auth classifier) trains to **94% held-out accuracy on 98 labeled captures** —
a real local model that offloads Haiku at classify. And the grounding "4 records" was a **plumbing
bug**, not a data shortage: **15 of 19 golden labels (`positive_candidate_id`) point to `cdp-ax-*`
candidates that live only in the `.ax.json` sidecar**, but both dataset builders searched only the
trace's `ranked_candidates` (grounding) / required an explicit `approved_bbox` (vision) — so AX-labeled
captures were silently skipped. Since the AX faucet, **the sidecar IS the candidate pool the labeler
labels against**; any consumer reading `ranked_candidates` for candidates is stale.

**Fix.** `build_grounding_dataset` + `build_vision_dataset` now load the sidecar (`_load_ax_candidates`),
search the union `ranked_candidates + ax_candidates` for the golden id, and derive the bbox from the AX
candidate (which carries `bbox` at top level, screenshot-px) when `approved_bbox` is absent. **Both
datasets 4 → 19 records**, across both `facebook_marketplace` and `indeed` scenarios. Tests green.
Encoded in `apps/controlplane-api/training.py` (`_load_ax_candidates`, `_build_dataset_record`,
`_build_vision_record`, `_candidate_bbox`).

**Still the real bottleneck (unchanged north star).** Model *accuracy* is still 0% on grounding — 19
records is tiny and the v0 linear grounder is weak. So the lever remains **golden-label VOLUME**
(drive → capture → review/label → retrain), now that the labels we already have actually reach the
trainer. "Concurrency-hardening for training" is premature — nothing to harden until many per-domain
trainers run at once. See [[project_backend_refactor_for_concurrency]].

## 2026-07-08 — Concurrent sessions in one working tree clobber each other via broad commits

**What happened.** While one session did the faucet work, a *second* Claude session working in the
**same** `main` working tree ran a broad `git add -A` / `commit -am` and swept the first session's
in-progress `main_server.py` edit into a commit titled "executor file-upload" (`a57a180`). Work wasn't
lost, but the history lies and the diff is unreviewable. This — plus a 5,742-line `main.py` everyone
edits — is the real reason "we can't commit cleanly."

**The norms now (see `CLAUDE.md` + `docs/PLAN_main-split.md`).** Stage **explicit paths**, never
`git add -A`/`commit -am` for a scoped change; `git status` before committing and confirm you own every
staged path; and if running sessions **concurrently**, give each its own **git worktree** on a
short-lived branch (ephemeral ≠ the long-lived feature branches this repo avoids).

**Fresh-start cleanup done same day.** Deleted 3 merged branches; env-gated SQLAlchemy `echo` (was
hardcoded `True`, flooding a 25 MB dev log — `settings.sql_echo`, default off); regenerated the two
stale `apps/mcp` golden observer fixtures (they lacked the now-always-emitted
`acquisition.training_metadata` — the *only* drift, not a regression) so the suite is green again;
adopted an orphaned passing `classify_apply_outcome` test; pruned dead `.gitignore` worktree lines.
**Planned, not done:** split `main.py` into `routers/` (see `docs/PLAN_main-split.md`).

---

## 2026-07-08 — The AX "data faucet" is already open; "3/175" is history, not a gate

**What we believed.** That AX-sidecar emission was *gated* — conditional on a request field (an
`ax_tree` payload, a "sidecar file arg") — and mostly off, which is why only **3 of 175** captures had
sidecars. The plan was "flip the gate on."

**What's actually true.** There is no such gate and no `ax_tree` field anywhere in the repo. There is
exactly **one** emission site — `_write_ax_sidecar(...)` in `apps/mcp/app/main_server.py` inside
`POST /capture` — and it already fires **unconditionally** (best-effort, inside a `try/except` so a
failure can't fail the capture). Both real capture paths funnel through it:
- control plane `POST /api/capture` → capture server `POST /capture`;
- the runtime live loop (`LiveProposer`, `apps/controlplane-api/runtime/live.py`) → same `POST /capture`.

The capture server fetches the accessibility tree **itself** over CDP (`propose_ax_candidates` in
`apps/mcp/app/observer/ax_proposer.py`); the caller never passes AX data in. So the faucet is
structurally *on* for every path you actually drive through.

The **"3/175"** (from `PROJECT_STATUS.md`) is a **stale snapshot**, not the current state. The emission
block was added **2026-06-15** (commit `80dd253b`); captures from before that have no sidecar. But the
live DB today has **157 tracked captures, and after the v16 backfill all 157 carry AX candidates**
(yields 1–628, `dry_captures: 0`). The faucet has, in fact, been flowing.

**Two different meanings of "backfill" — don't conflate them:**
- *Sidecar files from a saved screenshot/trace* = **impossible.** AX candidates can only be produced
  against the *live* page at capture time (`propose_ax_candidates` needs a CDP connection). A dead
  session can't be re-scanned. This is the real dead end (`PROJECT_STATUS.md` "Corpus can't be
  backfilled").
- *The `ax_candidate_count` column from sidecar files that already exist* = **done, and easy.** The
  sidecar's `proposal_count` is ground truth for a past capture; `scripts/backfill_ax_candidate_count.py`
  re-derives the column from it (idempotent). Run once after the v16 migration so `dry_captures` reflects
  reality instead of the migration default (0-for-all).

**The two real leaks (and what we did about them).**
1. *The faucet's per-drive yield wasn't recorded as durable exhaust.* `/capture` returns
   `ax_candidate_count`, but the control plane was **dropping it** — storing only `candidate_count`
   (the trace's ranked candidates, *not* AX). Fixed: `TrainingCapture.ax_candidate_count` column (v16
   migration) populated straight from the `/capture` response in `trigger_capture`, surfaced in
   `GET /api/observations`, and aggregated as `total_captures` / `dry_captures` in
   `GET /api/training/coverage`. Now "did this drive teach us anything?" is queryable without statting
   `.ax.json` files.
2. *An empty sidecar was silent.* When the tab is unreachable / node-ids are stale,
   `propose_ax_candidates` returns `[]` (it doesn't raise), so a sidecar with `proposal_count: 0` is
   still written — it **passes** the downstream `only_with_sidecar` existence check yet carries zero
   Select-training data (~15 of the 216 on-disk trace sidecars were like this — those are mostly
   runtime-loop artifacts, not DB rows). Fixed: emission now logs a **WARNING** (not INFO) on a
   0-candidate capture, and `dry_captures` counts them so the operator sees the real yield.

**Where it's encoded now.** `apps/controlplane-api/models.py` (`ax_candidate_count`),
`apps/controlplane-api/main.py` (`trigger_capture`, `training_coverage`, `list_observations`, v16
migration), `apps/mcp/app/main_server.py` (`POST /capture` empty-yield WARNING),
`apps/controlplane-api/scripts/backfill_ax_candidate_count.py` (one-time column backfill).

**Still open (deliberately not done).** The autonomous `run_live` loop writes on-disk artifacts +
sidecars but **no DB rows** — only `/api/capture` (with an active `TrainingSession`) creates queryable
`TrainingCapture` rows. So "every supervised task produces telemetry rows as exhaust" is only true for
the training-capture path today, not the autonomous loop. Wiring the runtime loop to auto-emit rows is
a real feature, deferred on purpose. Two dev/CLI paths (`debug_runner.py`, `run_observer`) also bypass
`/capture` and emit no sidecar — they're offline debug tools, left as-is.

---

## 2026-07-08 — Facebook login is fixed and lives on the AX layer; do not re-patch an endpoint

**What we believed / kept doing.** FB login broke ~weekly and each session reactively patched a bespoke
`/facebook_login` endpoint (hardcoded `querySelector` + coordinate click). `button[name=login]` broke
when FB shipped Log In as a `<div role=button>`; React-controlled inputs silently reset because a
per-char `dispatchKeyEvent` + native `.value` set didn't update React state. Each patch bought one more
week.

**What's actually true / what we did.** The bespoke endpoint was **deleted** (commit `6775499`,
2026-07-08). FB login now runs on the resilient **CDP-AX interaction layer** like everything else:
`/ax_scan` → `facebook_recipe.match_login_fields` (finds email/password/submit by **role +
accessible-name**, immune to `<div role=button>` because the AX tree normalises it to `button`) →
drive each node by `backend_node_id` via the humanized driver. The hard-won domain quirks
(button-is-a-div, React inputs need `Input.insertText`) are now **comments + logic in
`apps/controlplane-api/facebook_recipe.py`**, where the next session can see them — not re-litigated in
an endpoint.

**The meta-lesson (this is the important one).** Cross-session memory lives in **recipes and `docs/`**,
not in imperative endpoints. When a flow breaks, **first ask which interaction layer it's on** before
diagnosing fields or writing a one-off CDP script. See `PRINCIPLES.md` §6 and `interaction-layers.md`.

**Verified.** Live on `facebook_alt`: creds accepted → real 2FA gate; Marketplace reached via the
recorded `run_live` loop.

**Where it's encoded now.** `apps/controlplane-api/facebook_recipe.py` (`match_login_fields` + the
login-controls comment block), `apps/controlplane-api/channel_browser.py` (no more `login_path`),
`PRINCIPLES.md` §6, `interaction-layers.md`.
