# The whole-system audit: what lapses, what is dead, and why the teacher is over-billed

_2026-08-20, operator-directed ("step back and look at our system as a whole"). Method: four
parallel read-only audits over the full tree — dead code / unwired organs, teacher-reliance and
self-teaching, runtime-loop lapses, data plumbing — plus direct verification against the live DB
(`localhost:5433/agentos`), the real corpus in the MAIN checkout's `apps/mcp/output/`, and the
cited source lines. Every severe claim below was spot-checked at its line before being written
down. Companion docs: `ANALYSIS_ats_corpus.md`, `ANALYSIS_data_silos.md` (both 2026-08-20)._

## First: corrections to our own records

The audit falsified four things our own docs state. Recorded here so nobody re-derives the wrong
numbers:

| We said | Measured |
|---|---|
| "Zero golden state labels" (`ANALYSIS_data_silos.md:66`, PROJECT_STATUS) | **16 labeled edges exist** — all 16 teacher corrections carry `before_state` AND `after_state`. What is zero is *element-level* golden labels from drives. |
| "Shadow 47% over 15 pairs" (PROJECT_STATUS, from 08-06) | **62.3% loose over 239 paired rows** now; shadow-only 65.2%/224. Golden-only is 20%/15 — corrections are disagreements by definition, so that slice is not an agreement metric. |
| "`Application.ats` populated on 6 of 22" | 13 of 22 (written at `application_events.py:172,184`). The 6/569 figure is `jobs.ats` — and that IS still the column `job_database.py:292` analyses through. |
| "Teacher inbox sat empty since 07-23" | Re-wired and healthy: 45 asks + 45 answers through `controller/inbox.py`; cockpit reads and answers it. (`TeacherParks.jsx:8` carries a stale "zero UI callers" comment.) |

## The main finding, in one paragraph

**The system already produces the supervision it escalates for — and throws it away at four
one-line read points.** 146 of 356 transition rows are `verdict=confirmed` (the act declared where
it would land and the world agreed), but the witness trainer reads *only* `teacher_correction`
(`perception/dataset.py:123`). 69% of all teacher parks (31/45) cite "this page is unlike anywhere
we have been" — the novelty axis, a direct function of how few labeled states the witnesses hold.
More than half of all parks (26/45) are a single state, `indeed_apply_questions`, which HAS a
compiled $0 program — marked stale, and nothing recompiles programs automatically
(`compile_all_from_journal`'s only caller is a manual endpoint that defaults `save=false`).
Meanwhile a proven, browser-free auto-labeling pipeline (`suggest_page_state`;
`run_batch → verify_replay → promote_auto`) sits unbatched, and all 633 artifacts the transition
corpus references are on disk. This is not a data-scarcity problem. It is a plumbing problem: the
signals exist, and the consumers were never attached.

---

## Tier 0 — bugs that corrupt data or safety (fix before anything else)

All verified at the cited line.

1. **The upload-failure gate has been dead since it was written.**
   `apps/mcp/app/main_server.py:707` — `_mode = str(result.action_id or "")` then
   `startswith("upload:not_staged")`. But `action_id` is always the literal `"upload"`; the
   failure mode lives in `result.extra["mode"]` (`driver.py:495-497`), which is discarded and
   never surfaced. **Every upload returns OK, including rejected and unstaged ones** — the exact
   "required upload read as done" incident (live 2026-08-11, Workday) the comment above the line
   cites. Fix: read `extra["mode"]`, and include `mode` in the response body. The only test
   asserts on the driver, not the endpoint, so it passes today.
2. **`apply_flag` seeds the flow ledger with the stale pre-submit URL.**
   `routers/session_control.py:7167` — `step.tab_url = bb.world["apply_tab"]["url"]`, the recorded
   hint, written five lines after the 08-19 fix that deliberately re-observes live because that
   hint was fatally stale. It then feeds `record_flow(url=step.tab_url…)`, so `ats_flows` is being
   poisoned at the source on day two of its existence. Fix: `_apply_tab(bb, _obs).get("url")`.
3. **`record_flow` writes a job key that joins to nothing.** Same call site passes
   `job_key=step.job_id` — the `ObservedJob` id (`platform:external_id`), not the canonical
   `job_<hash>`. Measured: `ats_flows ⋈ applications` on job_key = **0 rows**. The mapping is one
   column away (`observed_jobs.canonical_job_key`, 586/586 populated). Live rows also skip
   `started_at`/`ended_at`/counters, contributing zero to every denominator the brief computes.
4. **The backfill deletes the live flows.** `ats_backfill.py:318-321` deletes `AtsFlow` rows for
   every session in the corpus unfiltered by provenance, then re-adds them with
   `job_key=None, terminal=None`. The next `POST /api/ats/backfill` erases the only row in the
   table carrying an outcome (`peopleadmin:une · parked:account_wall`). Gap-1's fix destroys
   gap-2's. Fix: delete only rows with `terminal IS NULL AND job_key IS NULL`, or add a
   `source` column and delete only `source='backfill'`.
5. **The inert cockpit Submitted button, explained.** `WorkSurface.jsx:261` posts
   `onFlag("submitted", "")` with no jobId; when the focus is parked, `cycle.application` is null,
   so the body carries `job_id: undefined` — dropped by `JSON.stringify` → FastAPI 422 **before
   the handler** (nothing journals, no request recorded), and `api.js:6-9` can't render FastAPI's
   list-shaped 422 detail, so the operator sees nothing. This is the second sighting of a shape
   `lifecycle.js:802-805` already documents from 2026-08-10. Fix both ends: refuse the press
   client-side when `job_id` is falsy and say so; teach `unwrap()` to flatten list-shaped detail —
   **that second fix removes a blindfold across every cockpit call, not just this button.**
6. **`ats_backfill` is the one reader that ignores the data-root envs.** It computes its root from
   `__file__` (`ats_backfill.py:37-41`); served from a worktree it globs a missing directory and
   returns a successful-looking no-op (`{"rows": 0, … "written": true}`). Every other reader
   resolves through `settings.observer_artifacts_dir` / `MCP_OUTPUT_DIR`. Fix: resolve through
   `deps._artifacts_dir()` and return `traces_dir_missing: true` instead of silence.

## Tier 1 — self-teaching: attach consumers to the signals we already produce

Ranked by how cheaply each becomes training data. None of these needs a new drive; the corpus on
disk funds all of them.

1. **Teach `transition_label_rows()` to read `verdict`, not just `teacher_correction`**
   (`perception/dataset.py:123`). A row where `verdict == CONFIRMED`, both halves carry a belief,
   and before-uncertainty is under `MAX_TRAIN_UNCERTAINTY` is a self-consistent (state, artifact,
   screenshot) triple — three independent sources concurring without a human. Tag it
   `state_label_source="self"`, weight below teacher labels, census as `from_self_supervision` so
   the two corpora can be A/B'd. Same edit fixes Tier-B eligibility in
   `routers/transitions.py:333-346`, which today trains edges from confident witnesses while
   **ignoring the verdict entirely** (a mismatched act trains as hard as a confirmed one).
   Payoff: ~146 usable rows against 16 teacher labels — a 6–10× corpus with zero new drives,
   aimed directly at the novelty axis that caused 31/45 parks.
2. **Give program recompilation an automatic caller.** `mark_stale` fires automatically
   (`loop.py:552`); `compile_all_from_journal` (`programs.py:254`) has never had a non-manual
   caller — staleness is a one-way door. Recompile at end-of-drive and on label-write (extend
   `train_after_label`, `routers/transitions.py:375-396`, which currently has exactly one call
   site). `indeed_apply_questions`: 6 compiled steps, 15 verified journal rows at 80% agreement,
   26 teacher parks paid because nothing un-condemned it.
3. **Batch the offline state auto-labeler.** `POST /api/training/suggest_page_state`
   (`main.py:4945`) already classifies from disk, writes `state_label_source="auto"` only at
   ≥0.9 confidence, respects `human_owned`, banks novel states as registry candidates — and has
   no batch runner. Wrap it over the 633 banked artifacts (all present on disk) with `dry_run`
   and a per-run cap, exactly as `promote_auto` does. ~$0.002/call ≈ $1.30 for the whole corpus —
   inside the weekly cap.
4. **Close the verify→cache loop.** `telemetry.log_selection()` has an unused `verifier=` kwarg;
   `ReasonCode.VERIFIER_FAILED` is defined with no producer; the selection cache has no eviction
   and serves a repeatedly-failing pick at confidence 1.0 forever (`selector.py:72`). Pass the
   verdict, emit the code, demote/evict on repeated failure.
5. **Make the maturity ladder reachable.** `CERTIFIED` needs `control_mode == "yellow"` rows
   (journal has **0**) and `supervisor_class == "none"` tails (journal has **6 of 427** —
   the supervisor, pure and free, simply doesn't run per step). Stamp `control_mode` on every
   turn, run `supervision.classify()` on every step, and stash the page's own refusal text
   (`_refusal_text()`, `session_control.py:6280` — currently rendered once and discarded) on the
   transition row so a mismatch carries the site's explanation. Until then `GREEN_AT = CERTIFIED`
   is unreachable **by construction** and no turn can ever run unwatched, regardless of evidence.
6. **Reconcile the two promotion gates.** `CONTROLLER_PROMOTION.md` gates on shadow agreement
   (computed, displayed, **enforced nowhere** — no `>= 0.90` branch exists in the repo);
   `maturity.py` gates on journal columns and claims to be that document made mechanical. They
   share no units. Note also: the only two scenarios clearing the N≥25 bar are both
   `*_job_posting` states that `TARGET_PARAMETERISED_STATES` hard-caps below autonomy anyway —
   so under the code's gate they are permanently ineligible. Decide which gate is real, then
   tune its numbers.

## Tier 2 — loop lapses: rules we paid for that still have no enforcement point

| Lapse | State | Where | Smallest enforcement point |
|---|---|---|---|
| Census misses `(required)` labels | bug | `protocols.py:657` | widen the end-anchor: `/\brequired\s*[)\]\.:*]?\s*$/i` |
| Validator testimony discarded | bug | `protocols.py:1090,1124` | route "X is required" into `declared_required[]`, merge as `required_via:"validator"` (outranks the census both directions) |
| Press-Next-and-read-errors is an operator ritual | prose-only | nowhere | `/probe_step` on MCP: click a named advance control + re-scan; step boundaries only, hard-refuse on final Submit |
| `not_staged` false negatives | not fixed | `protocols.py:66-98` | add opener accessible-name as third witness; drop `, div` from the `closest()` list (the nearest-ancestor collapse manufactures false negatives on well-formed react-selects) |
| `set_text` ceiling leaves partial text | not fixed | `humanized.py:206-230` | the authoritative `_set_value_react_safe` is *after* the per-char loop, so the timeout skips exactly the write that guarantees correctness — type a bounded prefix for timing, then set the full value; cost becomes constant |
| Resolver clicks containers | partial | `main_server.py:220-268` | prefer the leaf when one candidate's box contains another's; exclude non-`INTERACTIVE_ROLES` from click targets (the PeopleAdmin heading-swallowed-three-links case) |
| Retry discipline (2→screenshot, 3→operator) | prose-only | nowhere | per-`(field, rung)` FAILED counter in `apply_steps.record()` (~15 lines) |
| `workday_error_retry` burns a human every time (n=36, 4th most common state) | inert | `facets.py:125`, `recovery.py:49` | add `FailureClass.PLATFORM_ERROR` → `RE_OBSERVE` play; promote that one class out of the empty `AUTONOMOUS_CLASSES` |
| Census caps 3 lists silently | bug | `protocols.py:659,945,1132-1133` | `*_truncated` booleans — same file already argues for exactly this (`options_truncated`) |
| Silent humanized→direct driver downgrade | latent bug | `driver.py:518-527` | journal the downgrade as an event, or raise — bot-safety posture must not vanish on an ImportError |
| Dropped mouse acks swallowed | latent | `driver.py:104-115` | count them into `ExecResult.extra` |
| `apply_teach` drives the recorded tab id, falls back to `tabs[0]` | partial | `session_control.py:6039` | route through `_apply_tab(bb, obs)` |
| Window identity | open | no `windowId` anywhere | unchanged from the known gap; tab-diff ledger is honest but windows are indistinguishable |
| Pre-flight signals unread at decision time | unwired | `apply_steps.py:288-296` | prefer a measured `ats_characteristics` auth row over `_BY_ID[...]["auth"]`; consult `ats_brief.blockers` / `apply_requirements.blockers()` before entering a flow (the RED takeovers it would have predicted all happened) |
| Observation profiles | prose-only | nowhere | attach as a structured `observe:[…]` list per `(platform, state)` — `ats_registry` notes or the `apply_requirements` ledger shape; ordering input to `observer/pipeline.py` |

## Tier 3 — dead code: ~7,000 lines + a 1.3 GB dependency

Full inventory in the audit transcripts; the masses, in removal order (lines-removed per unit of
risk):

1. **Zero-risk scripts and stubs (~890 lines, zero importers):** the three Florence/OmniParser
   smoke tests, `scripts/autopilot_step.py`, `train_grounding.py`, `apps/mcp/run.py`,
   `debug_runner.py`, `dev_worker.py` (a 3-endpoint island), `apps/mcp/app/skills/` (two empty
   files).
2. **Unreachable React (~780 lines):** `HomeSection`, `SectionSidebar`, `SessionJournal` (its
   `/windows` fetch is duplicated live in `SessionTrace`), `ChatSection`,
   `WorkersSection`+`RunsSection`+`WorkerHealthSection`, three dead `App.jsx` view branches,
   `mockWorkers` — plus the live `GET /api/runs` poll on every refresh feeding a view that cannot
   render.
3. **~40 genuinely orphaned endpoints** (zero references outside their router file; the curl
   seams the teacher/operator actually uses are inventoried separately and are NOT in this set).
4. **Superseded controller organs (~620 lines):** `controller/teach_session.py` (duplicate teach
   loop; the wired one is `/teach/observe`+`/teach/commit`), `controller/local_reasoner.py`
   ("THE SEAT IS EMPTY"), and `resolve_answer.py` — which is *worse* than dead: the capture
   server advertises "Vocabulary miss -> /resolve_answer" at three call sites and **no such
   endpoint exists**. Either wire it or stop advertising it.
5. **The pre-StepRunner `runtime/` loop (~740 lines + 8 endpoints):** three loop implementations
   coexist (`runtime/loop.py`, `step_runner.py`, `controller/loop.py`); the documented
   convergence never happened. Keep `handoff.py` (live). *Caveat: `run_batch`/`verify_replay`/
   `promote_auto` ride on these endpoints — Tier-1 item 3 wants that pipeline's pattern, so
   either port it to StepRunner replay first or keep those three endpoints when the rest goes.*
6. **The Florence/OmniParser/UGround stack (~3,700 lines + `torch`/`transformers`/`einops`/
   `timm` ≈ 1.3 GB):** import-time dependency of the live capture server via `vision_proposer`;
   gated off by `VISION_CATCHALL_ENABLED=false` and `AGENT_MODEL_DOWNLOAD` guards (three copies);
   `_backfill_vision_candidates()` defined and never called; UI panels render for a corpus with
   5 approved bboxes. Biggest win, biggest blast radius — schedule it deliberately, and low-data
   mode gets materially cheaper when it lands.

## Data plumbing — state of the six ranked gaps (from `ANALYSIS_data_silos.md`)

| Gap | State | Note |
|---|---|---|
| 1. Traces unjoined | **closed as capability, open as habit** | fold runs and has run (39 instances in DB); nothing calls it automatically — silo re-opens as new captures land. Call `backfill()` where captures are recorded; skip files older than `max(last_seen_at)`. |
| 2. Flow ↔ job identity | **partial + bugged** | written at terminal-flag time (not creation), in the wrong namespace (Tier-0 #3), and the next backfill deletes it (Tier-0 #4). 63/64 historical flows NULL. |
| 3. Screenshot queryability | **open** | 271 of 1,129 PNGs reachable via `training_captures`; 635 only by scanning JSONL; **223 by nothing at all**. Smallest: a `screenshot_index` table filled from the pass `read_corpus` already makes. |
| 4. Sidecar convention | **partial** | named once (`_SIDECAR_MARKERS`) but unenforced; **7 orphan sidecars already on disk** — the predicted silent break, now observed. One shared `sidecar_path()` helper + an orphan count. |
| 5. Golden state labels | **doc was wrong — thinly closed** | 16 labeled edges exist, written by API only; no UI field for them. Put `before_state`/`after_state` on the transitions review panel. |
| 6. `Application.ats` | **partial** | 13/22 now; but analyses still read `jobs.ats` (6/569). After gap-2's key fix, group `by_ats` over `ats_flows ⋈ jobs`. |

**New silos forming, cheapest to close before they grow:** `apply_requirements` observations (a
ledger with **no store and no callers at all** — zero rows ever produced; close before it
accumulates); submission verdicts (computed on every `submitted` flag, persisted only inside a
prose string — `ApplicationEvent.evidence` documents the exact shape and holds **0 of 22**
non-empty); the `cache/*.jsonl` journals (decision journal is the substrate of program
compilation and maturity, joined to nothing, on a third env-var root).

## The Gmail reframe — the tracker needs a reader, not a conquered domain

The inner application-tracking system ("read replies, keep track of each application") does not
depend on driving Gmail. It depends on **reading + matching**, and most of that exists:

- `ApplicationEvent` designed `gmail {message_id, from_address, subject}` in as a source on day
  one (`models.py:674`); "nothing about this table changes when Gmail starts writing to it."
- A structured inbox reader exists: `_GMAIL_INBOX_JS` (`main_server.py:5449`), built for
  `fetch_login_code` — CDP against an already-open tab, local socket, free, no new auth surface.
- `job_key` was minted as "the key Gmail will join on" (`models.py:532`), with merge tombstones.
- The registry's `hosts` catalogue doubles as a sender classifier: employer replies come *from*
  ATS domains (`@myworkday.com`, `@paylocity.com`, …), so `classify_ats` logic nearly answers
  "which application is this email about" before any content matching.

The missing piece is one matcher: inbox row → (sender-domain → ats_id, subject/company →
`canonical_job_key`) → `ApplicationEvent(kind, source="gmail", evidence={…})`, with the
unmatched residue escalated, never guessed. Driving Gmail as a full domain (composing, replying,
archiving) is a later, separate problem that gates *actions*, not tracking — and per-message
polling belongs on the same crank as gap-1's backfill, not on a live drive.

## What this audit deliberately does not recommend

Gathering more data before wiring the above. The corpus argument runs the other way: 356
transition rows, 1,093 joinable traces, 633 artifacts, 150 cache entries, 248 orientation rows,
and 45 answered parks are already banked, and every Tier-1 item converts banked evidence into
labels, programs, or evictions **offline**. New drives keep landing on the same unwired seams and
re-billing the teacher for pages the system has already paid for. Wire first; the next drive then
tests the wiring instead of re-purchasing the lesson.
