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

## 2026-07-11 — First full Indeed smartapply flow driven end-to-end (Brigham Sr Data Analyst SUBMITTED)

**What we did.** Drove a complete Indeed "Apply with Indeed" (smartapply) application to SUBMIT, live,
humanized, on session #16. The module sequence (each its own URL under `smartapply.indeed.com/beta/indeedapply/form/`):
`contact-info` (auto-prefilled) → `commute-check` ("Continue applying") → `resume-selection-module`
(the user's uploaded **GM_Res.pdf** was pre-selected — chosen over the auto-generated Indeed résumé) →
`questions-module` (employer screening) → `demographic-questions/1` (EEO self-ID) → `demographic-questions/2`
(ADA disability) → `review-module` → `post-apply` ("Your application was submitted…"). Captured + labeled
every state (rows 247–256).

**Interaction findings that will save the next session a lot of pain.**
- **`/scan_form` is the right tool to READ an apply form** — returns every field's `{label, kind, required,
  filled, value_preview}` in one call, no scrolling. Use it before touching anything; re-call it to VERIFY
  each field after you set it. Far more reliable than screenshot-scrolling (which the reload churn keeps resetting).
- **Multi-question radio groups: target by `backend_node_id`, never by name.** Every question's options are
  just "Yes"/"No" (or "Declined"), so `target_name` collapses to the FIRST group. Get fresh node-ids from an
  `/ax_scan`, sort by bbox `y` to map DOM order → questions, click the specific node. Node-ids CHURN on
  Go-back/re-render, so re-scan after navigating.
- **Prefills can silently DISCLOSE against preference.** The EEO module came prefilled from a past
  application with real values (Gender=Male, Race=Asian, Veteran=Not-a-veteran, Disability="No, I do not
  have a disability"). Per the user's decline preference we OVERRODE each to its decline option
  ("Declined" / "Decline to Disclose" combobox / "I do not wish to self-identify" / "I do not want to
  answer"). ALWAYS read `value_preview` and override — don't trust `filled=True` as "handled correctly."
- **A required field may have NO decline option.** "Are you Hispanic or Latino? *" was Yes/No only and
  BLOCKED submit ("Choose an option to continue") — escalate to the human (their factual call), don't guess.
- **There's a required Terms **certify** radio at the very bottom of the EEO module** ("I certify that I have
  read…") with no alternative — easy to miss; it's a real gate.
- **The `/execute` empty-response quirk is EVERYWHERE in this flow** — nearly every click returned an empty
  body; ~half were genuine no-ops. Pattern that worked every time: fire → verify (scan_form/url/screenshot)
  → retry until it takes. Budget 1–2 retries per click.
- **Two-tab flow:** "Apply with Indeed" opens smartapply in a NEW tab; pin `tab_url="smartapply.indeed.com"`
  on every call. `/screenshot` (Page.bringToFront) DISMISSES open dropdowns — never screenshot between
  opening a filter/select and acting on it.
- **Humanized scroll shipped** — `driver.py` `parse_scroll_value` + base `_do_scroll` (CDP mouseWheel) +
  `humanized.py` `_scroll_plan` (eased, jittered notches + read-pauses). NB: running a venv script that
  imports MCP modules writes `.pyc` into the reload-watched dir → bounces the MCP worker → resets in-flight
  HTTP (`HTTP 000`); don't do that mid-drive.
- **Resume asset for cross-site apply** — `assets.py` now has a `documents/` area + `resume_key()`/`resume_path()`
  + `GET /api/assets/documents`; canonical resume `documents/GM_Resume.pdf` for Workday/ATS file uploads
  (Indeed's own flow uses the profile résumé, not this file).

**Search-filter findings (same session).** Indeed's Distance filter is a 2-step apply (pick radius → click
**Update**); applying mutates the URL (`&radius=50`) and re-navigates the SERP (`from=searchOnHP` →
`searchOnDesktopSerp`, new `vjk`). Canonical order: search first, THEN set radius.

**Where it's encoded.** Captures 247–256; `apps/mcp/app/executor/driver.py` + `humanized.py` (scroll);
`apps/controlplane-api/assets.py` (documents/resume). Still a live teacher drive, not yet a codified apply recipe.

**Cross-site (Workday) apply recipe — operator-directed strategy (2026-07-11).** "Apply on company site"
routes to the employer's own Workday tenant (`<employer>.wd5.myworkdayjobs.com`). Recipe facts to bake in:
- **Workday needs a per-employer ACCOUNT + a résumé FILE.** The first step is always `Create Account/Sign In`
  (the wall). The agent CANNOT do this — creating accounts and entering passwords to authenticate are hard
  prohibitions, and that holds even when the operator has saved the password in the Workday Accounts vault
  (saving ≠ logging into the live site). The operator does the site login by hand; then the agent drives.
- **Workday steps:** Create Account/Sign In → Autofill with Resume → My Information → My Experience →
  Application Questions → Voluntary Disclosures → Self Identify → Review.
- **ALWAYS try Autofill-with-Resume, then CHECK (don't hand-input then check).** Operator's efficiency rule:
  autofill from the résumé file, then VERIFY each parsed field lines up + fill only the gaps — fewer steps
  than typing everything and then verifying. Résumé file = `assets.resume_path()` (GM_Resume.pdf).
- **Secure per-employer credential store shipped:** `accounts.py` gained `kind="workday"` + `login_url`;
  `WorkdayAccountsPanel.jsx` under System→Workday Accounts (operator types password → encrypted into the
  secrets vault; only a masked hint returns; agent never handles plaintext). Point32Health shell seeded.

## 2026-07-10 — FB create-listing driven live for the first time + per-item OWNED photo uploads

**Context.** A Facebook Marketplace training/selling session run *concurrently* with the live Indeed
session. Isolation held by pinning `browser_url=http://127.0.0.1:9326` (the selling profile) on every
MCP call — Indeed (`:9322`) + Gmail (`:9325`) never touched. Backend runs `--reload`, so each edit
bounces the shared API briefly; fine while the other session is human-login-idle, but **batch backend
edits** so it reloads once, not per-file.

**The selling profile was already authed — don't assume "log in" is the task.** Session #15
(`facebook_alt` / `business_chrome_profile`, "John Carl") was already logged in and sitting on
`/marketplace/create/item`. Per PRINCIPLES §7 we confirmed via screenshot, not the URL. The user's
stated goal ("get logged in") was already satisfied — surface that instead of re-driving a login.

**The create-listing recipe went from "seeded, not live-verified" to DRIVEN live.** Drove the whole
`fb_create_listing_form` per-action with the humanized driver, re-resolving each node by role+name at
act time (`/execute` `target_role`+`target_name`) — zero node-id staleness. Captured + teacher-labeled
5 distinct states (rows 243–246: empty form→Title, title+price→category-suggestion, condition-picker
→Used-Good, complete-form→Next). **FB domain findings that change the recipe:**
- **Category suggestion PILLS** appear under the Category box the moment you type a Title (e.g.
  "Men's clothing & shoes" / "Women's clothing & shoes"). The human path is to **click the pill**, not
  open the combobox and scroll. Add these as the preferred `category` selector in `facebook_recipe.py`.
- **Conditional fields live under "More details"** and only render per category — apparel reveals
  **Color** (portal combobox) + **Material** (free-text) + SKU. Matches `facebook_listing_schema.py`.
- **Condition** is a 4-option portal picker (New / Used - Like New / Used - Good / Used - Fair). Our
  driver's `select` (click-open → click option) and a granular click-open→capture→click both work; the
  portal survives an `/ax_scan` + `/capture` in between (no premature close).

**The executor ALREADY does file upload — the old "no setFileInputFiles" note was STALE.**
`apps/mcp/app/executor/driver.py` `_element_act` handles `action_id="upload"` via `DOM.setFileInputFiles`
(+ `selector` re-resolution for a hidden `<input type=file>`). So a real post is not blocked by system
capability — only by having a real product photo. Corrected [[project_create_listing_drive_gaps]].

**New feature — per-item OWNED photo uploads (assets belong to ONE post, never shared).** The old
model was a flat *shared* pool (`assets/marketplace/*.jpg`) picked by toggle, so every item's picker
showed every asset. Now: direct upload stored under `marketplace/items/<item_id>/<file>` with a
`<file>.meta.json` sidecar (owner, original name, uploaded_at, size, content_type). Ownership is encoded
in the key path; `list_assets` **excludes** the `items/` subtree so owned photos never leak into another
item's library. Endpoints `POST|DELETE /api/inventory/items/{id}/photos` (multipart up / unassign+delete
down); UI upload tile in **both** create (staged, flushed on save) and edit (owned thumbnails + remove).
Item hard-delete drops the owned folder (no orphans). Encoded: `assets.py`
(`save_item_photo`/`asset_meta`/`delete_asset`/`delete_item_assets`, `ITEM_PREFIX`), `inventory.py`
(`add_item_photo`/`remove_item_photo`, delete cleanup), `routers/inventory.py` (the two endpoints),
`FacebookMarketplaceSection.jsx` (`ItemForm`). Verified end-to-end (upload→owned path→assign→delete,
no shared-pool leak). NB: FastAPI multipart works though `import multipart` looks missing — newer
`python-multipart` imports as `python_multipart`; trust the live upload, not the import probe.

**A real listing was driven to the publish gate (Kith x Wilson polo, $90, 7 real photos) — two more
domain facts.** (a) **FB auto-detects Color from the photos.** After swapping the placeholder for the
real navy photos, the Color field flipped Black→**Blue** on its own — FB's image analysis fills the
attribute. Don't fight it; verify it landed right. (b) **New accounts have a daily Marketplace-listing
cap.** At the final "List in more places" step, `facebook_alt` (a young account) showed *"You can't add
a listing to Marketplace right now because you reached your daily limit as a new Facebook account"* with
Publish disabled. This is a **human-required stop-state — do NOT force Publish** (errors + flags the
account). Retry after the ~24h reset or use an established account; FB retains the prepared listing as a
draft, and the inventory item (`internal_status=ready_to_post` + a note) is the source of truth to
re-drive. Encoded as `fb_listing_publish_blocked_new_account_limit` in
`facebook_recipe.py`'s `FACEBOOK_CREATE_LISTING_BRANCHES`. Also relevant: driving overwrote a stale draft
in place (remove placeholder photo → `upload` 7 owned files via one `DOM.setFileInputFiles` → `clear`
then `type` each text field, since `type` APPENDS). The empty-first-execute quirk hit every `Next` click
— always retry + verify by screenshot.

**Two things worth fixing (noted, not yet done).** (1) `/execute` requires `target_bbox` even when
`target_name`/`selector` re-resolves the node and the bbox is ignored — pass a zero bbox for now; make
it Optional. (2) **Modeling nuance:** within ONE page-state (`fb_create_listing_form`) the correct
golden action depends on form-fill PROGRESS, not the visual (empty→type Title vs complete→click Next).
Labeled the finished form as a distinct state `fb_create_listing_form_complete` so the classifier isn't
handed the same picture with two different goldens; a cleaner fix is a completion feature in the state.

## 2026-07-10 — The cross-domain login-code errand works end-to-end (Indeed authed via a code read from Gmail)

**What we proved.** The `fetch_login_code` errand ran live, end-to-end, and got Indeed authenticated
WITHOUT ever driving Google's password page:
1. Stood up the dedicated **`google` profile** (session #17, port 9325) via `POST /api/training/sessions`
   + `/start`. Needed a gmail scenario first — `create_training_session` REQUIRES a domain-bound
   scenario, so added `gmail_login_google_signin` to the registry.
2. The human did the one-time Google login in that window (passwordless **passkey** — even cleaner than
   a password). A **per-instance auto-capture watcher** (poll page target → capture on settle) recorded
   each state as gmail-domain training data: `google_signin_email`, `google_signin_2fa` (passkey), `inbox`.
3. On Indeed (session #16), clicked **"Sign in with a code instead"** → Indeed emailed a code. The authed
   `google` profile read it **straight from the Gmail inbox subject line** ("Sign in to Indeed with code:
   NNNNNN") — no need to open the email. Typed it into Indeed → accepted.
4. Indeed then required **phone 2FA** ("confirm it's you", SMS to …67) — a real 2FA gate, so we ESCALATED
   to the human (never auto-solve), they supplied the SMS code, we submitted it → `logged_in: true` on
   `secure.indeed.com/settings/account`. Skipped the "set up a passkey" post-auth funnel with **Not now**.

**Lessons that will bite the next session.**
- **A login code can require a second, out-of-band factor.** The email-code path is NOT sufficient alone
  when the account has phone 2FA — the errand gets you PAST the email wall, then hands off. Build the code
  errand to expect a follow-on human-gated factor, not to assume email-code ⇒ done.
- **The `/execute` "empty-first-execute" quirk is real and recurring** — the first action after an idle
  gap often returns an empty body / no-op (sometimes it silently worked, sometimes not). ALWAYS verify by
  screenshot/AX and retry; never trust a single execute's return value. (Confirmed again here on type + click.)
- **Read a login code from the Gmail SUBJECT, not the body.** Indeed (and most senders) put the code in the
  subject/snippet, so the inbox list is enough — no need to open the thread (fewer steps, less churn).
- **Two live sessions, two ports, target explicitly.** Indeed on :9322, google/Gmail on :9325 — every
  MCP call pins `browser_url` to the right one. The errand is literally "hop from :9322 to :9325 and back."
- **`auth_state` is Indeed-specific.** On `myaccount.google.com` it reported `logged_in:false` — meaningless
  there; being on a signed-in-only URL is the real signal. Don't reuse the Indeed detector for Google.

**Where it's encoded now.** Captures/labels: rows 232–242 (gmail SSO states + Indeed code-entry / phone-2FA
/ logged-in). Watcher prototype: `scratchpad/gmail_login_watch.py` (graduate into a permanent per-session
auto-capture endpoint — the "state detector within each Chrome instance"). Registry: `gmail_login_google_signin`
scenario in `seed.py`. STILL TODO: codify the errand as a reusable recipe/endpoint (today it was a live
teacher drive), and give Gmail an operator workspace.

---

## 2026-07-09 — Provider groups (Google bucket) + Gmail is a real domain; Google login is an errand, not a page to drive

**The trigger.** Trying to log Indeed in via Google SSO from the training session (#16, Chrome on
`:9322`, persistent `indeed` profile). Clicking Indeed's **Continue** hands off to Google — and the
Google sign-in surfaces in a **separate window/popup**, plus Indeed's auth page already carries a
**reCAPTCHA enterprise** iframe. Two lessons fell out.

**1 — Don't drive Google's password page; make login an ERRAND.** Everything up to and after Google's
auth we drive on the CDP-AX layer with the humanized driver (verified: typed the email into Indeed's
box by role+name `textbox/"Email address"`, clicked `button/"Continue"`). But the Google
*password + 2FA* keystrokes are a deliberate hand-off to the human — same class as never auto-solving
a captcha. Reasons: (a) it's the user's **crown-jewel Google credential** (a locked Google account
cascades everywhere), (b) `accounts.google.com` is the most bot-fingerprinted page on the web, (c) the
training value is in **capturing the states**, not in who typed. The cleaner design the operator
chose: use Indeed's **"sign in with a code instead"** path and fetch the code from Gmail — i.e. login
becomes a cross-domain **errand** (`gmail ▸ fetch_login_code`), not a page we drive.

**2 — Multi-window SSO IS reachable over CDP; target it explicitly.** The popup is its own
`type=="page"` target on the SAME `:9322` debugging port (visible in `/json/list`). `_discover_target`
(`apps/mcp/app/observer/ax_proposer.py`) matches **`tab_id` first, then `tab_url` substring, else the
first page**, so pin every `/capture|/ax_scan|/execute|/screenshot` call to the popup with
`tab_url="accounts.google.com"` (or its exact `tab_id`) — don't let discovery default to the Indeed
tab underneath. This is the concrete fix for the long-standing "multi-window captures carry no window
identity" gap.

**What we built — the PROVIDER GROUP, the bucket above domains.** A *provider* is one company whose
many surfaces we drive as separate domains but which share **one identity/login**. Google is the
first: `gmail` (built) + `google_calendar`/`google_docs`/`google_sheets` (planned) all authenticate
through **one** Google sign-in (one persistent pre-authed profile) and the shared SSO flow is what
other domains hand off to. Kept as a small **backend constant** (`providers.py`, like
`command_center.DOMAINS`), NOT a DB table — it's config, membership is derived from the live
`DomainRegistry`. `GET /api/providers` resolves each group's live vs. planned members. **Gmail is now
a real domain** in `REGISTRY_SEED` (seed.py) with the shared `google_signin_*` page-states as its home
for SSO training data + the `fetch_login_code` errand goal.

**Gotcha — the base registry seeder only runs on an EMPTY registry.** `seed_training_registry`
early-returns if any domain exists, so adding Gmail to `REGISTRY_SEED` did nothing on the live DB;
worse, a barebones `gmail` row already existed (added by hand/UI, empty `page_states`). The fix is an
idempotent **top-up + reconcile** seeder (`seed_gmail_domain`, mirroring `seed_facebook_extras`) that
**merges** the canonical page-states/hosts into the existing row without removing anything.

**Bug fixed in passing — `/screenshot` always returned "no screenshot data".** `_CDPSession.send()`
returns the **unwrapped** CDP result (`msg["result"]`), so `Page.captureScreenshot`'s base64 is at
`res["data"]`, but the handler read `res["result"]["data"]` (always `None`) — a regression from the
`main.py → main_server.py` split. Now `res.get("data")`. The driver's "eyes" work again.

**Where it's encoded now.** `apps/controlplane-api/providers.py` (new group constant + helpers),
`apps/controlplane-api/routers/providers.py` (`GET /api/providers`), `apps/controlplane-api/seed.py`
(gmail domain + goals + `seed_gmail_domain`), `apps/controlplane-api/main.py` (startup call + router
include), `apps/controlplane-ui/src/components/controlplane/workspace/domains.js` (`PROVIDER_GROUPS`,
gmail `provider:"google"`) + `DomainsHub.jsx` (renders the bucket),
`apps/mcp/app/main_server.py` (`/screenshot` unwrap fix). Verified live: `GET /api/providers` returns
google↦{members:[gmail], planned:[calendar,docs,sheets]}, gmail carries all 7 page-states, and the
"🌐 Google" bucket renders in the cockpit.

**Still open (deliberately).** Gmail has no operator *workspace* UI yet (tile stays non-clickable —
"training live, workspace soon"); the `fetch_login_code` errand + the shared `google` browser profile
are declared but not yet wired to a live run; provider is a constant, not a DB column (promote only if
operators need to edit groups at runtime).

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

---

## 2026-07-12 — The apply cadence has an EPILOGUE: close the finished apply tab, refocus search

**What was missing.** The `targeted_search_and_apply` / `apply_triage` cadences drove a pick through
the apply flow and then jumped straight to "click pagination to the next page" — leaving the
newly-opened apply tab (smartapply for quick-apply, or the ATS host for cross-site) open. Over a
session that orphans a stack of apply tabs, and the loop never cleanly "returns to the search." There
was also no capability to close a tab at all; the bounds only said "never churn tabs."

**What's true / what we did.** Indeed opens the apply in a NEW tab. The human-natural epilogue —
finish (submit) OR abandon at a human-required wall (e.g. a Workday **account-creation gate** we
cannot create), record the outcome, then CLOSE that one apply tab and return to the search tab — is
now a first-class step:
- New MCP capability **`POST /close_tab`** (`apps/mcp/app/main_server.py`): closes a tab by id/url via
  the CDP HTTP endpoint (`/json/close/<id>`), optionally activates `focus_tab_url` (the search).
  SAFETY: refuses to close the control panel (`localhost:5173`) or the last remaining page tab.
- `search_cadence.py`: `BOUNDS.tab_hygiene` carves the single intentional close OUT of the "no tab
  churn" rule; the epilogue step added to both apply modes.
- `apply_recipe.py`: terminal `indeed_apply_submitted` action + new `APPLY_EPILOGUE` + the
  `account_creation` branch note now say "record → close apply tab → refocus search."

**The distinction that matters.** "No tab churn" forbids scraper-like opening/closing of many tabs to
browse. Closing the ONE finished apply tab to return to search is expected cleanup, not churn — a
human does exactly that. The bounds now say so explicitly.

**Verified.** Live on the Indeed session (port 9322): closed a completed smartapply `post-apply`
confirmation tab AND a Point32Health Workday `userHome` (account-wall, prospect #32) tab via
`/close_tab`, each refocusing `indeed.com/jobs` — ended on the single search tab, focused, where
triage left off.

**Where it's encoded now.** `apps/mcp/app/main_server.py` (`/close_tab`),
`apps/controlplane-api/search_cadence.py` (`BOUNDS.tab_hygiene` + both apply modes),
`apps/controlplane-api/apply_recipe.py` (`APPLY_EPILOGUE`, terminal step, `account_creation` note).

---

## 2026-07-12 — Applying is organized as Career-Search domain → ATS group (each ATS domain-like)

**The structure (defined live with the operator).** Applying is cross-site and was an unorganized
pile. It's now a taxonomy:
- **Career Search** = the domain CATEGORY for job engines (Indeed, LinkedIn, ZipRecruiter, …). "Indeed"
  isn't the domain; "career-search engine" is, and Indeed/LinkedIn/… are members. Where we SEARCH.
- **ATS group** = the third-party apply portals you hand off TO (Workday, iCIMS, Taleo, Greenhouse,
  Lever, SuccessFactors, …). Each ATS is treated like its OWN domain: its own recipe AND its own
  training-data bucket (captures tagged `domain_id=<ats_id>`, so rollups accrue per-ATS not per-company).

**Why per-ATS.** An ATS renders the same component library across every tenant (Workday's
`data-automation-id`s are identical for State Street / Takeda / Point32Health). So training
GENERALIZES across companies sharing an ATS. The **company→ATS map** (`ats_for_company` /
`record_company_ats`, persisted `cache/company_ats.json`) is the hook: the first time we drive
Company X's Workday we already reuse everything learned on every other Workday.

**Never auto-create an account.** ATSs with `auth: "account"` (Workday, iCIMS, Taleo, …) gate the
apply behind a per-employer candidate account — escalate to the operator (persistent pre-authed
profile), never sign up. Point32Health's Workday `userHome` account-wall (prospect #32) is the case
that motivated this; recorded as `Point32Health → workday`.

**Application preferences** are operator-owned notes attached to the career-search domain
(`application_preferences.py`, `cache/application_preferences.json`): a `structured` block (comp
target $130k, no sponsorship, 1–2 onsite days, decline demographics) + append-only `notes` (why a
role was skipped). The apply shortlister/filler reads these.

**Where it's encoded now.** `ats_registry.py` (CAREER_SEARCH + ATS_PLATFORMS + company→ATS store +
`classify_ats`), `application_preferences.py`, `routers/career_search.py`
(GET `/api/career_search/ats`, GET/POST `/application_preferences`, POST `/ats/company`),
`search_cadence.classify_apply_platform` now delegates to `ats_registry.classify_ats` (one source of
truth). Verified live: endpoints return the registry; Point32Health shows under Workday; both
session exclusions (Knipper Sr BI, Fidelity Alt-Investments) recorded as preference notes.

---

## 2026-07-12 — Account-walled ATS jobs: build the accounts system, pause at CREATION (don't skip)

**What was wrong.** Account-gated ATS applications (Workday/Phenom/iCIMS/… candidate-account walls)
were being SKIPPED as "can't, unsafe." The operator was right that this is wrong — it drops jobs they
want. The safety rule only forbids a narrow act (the agent typing a password into a site or submitting
an account creation/login), not organizing accounts or generating credentials.

**The workaround (built + verified end-to-end).** Company-first ATS accounts:
- `ats_accounts.py` on top of the existing `accounts.py` vault. `derive_password("U.S. Bank National
  Association")` → INITIALS "USBNA" (first letter of each token, splits on spaces AND punctuation) +
  a shared suffix in gitignored `.env` (`ATS_ACCOUNT_PW_SUFFIX`); username `ATS_ACCOUNT_USERNAME`
  (genomags@gmail.com). `ensure_account(company, ats_id)` registers a company↔ATS login as `pending`.
- Endpoints: `/api/career_search/accounts{,/ensure,/credentials}`. New top-level **Accounts** UI tab
  (`AccountsSection.jsx`), company→ATS, reveal generated login, Save login (→vault), operator ▶ Login.
- `accounts.py`: `_STATUSES` += "pending"; `_EDITABLE_KEYS` += company/ats_id/username_hint.
- New ATS registered from live intake: **Phenom** (careers.<co>.com; U.S. Bank → careers.usbank.com).

**The boundary (unchanged, load-bearing).** The agent GENERATES + ORGANIZES credentials and drives up
to the signup/login form. The agent does NOT type a password into a site or submit account
creation/login — the OPERATOR does that one step (the "pause at the creation point"), then automation
resumes. This is the honest line: build everything, pause at the keystroke, never refuse-and-skip.

**Where it's encoded now.** `apps/controlplane-api/ats_accounts.py`, `accounts.py` (pending status +
keys), `routers/career_search.py`, `apps/controlplane-ui/.../AccountsSection.jsx` + `navigation.js` +
`App.jsx`, `.env` (ATS_ACCOUNT_USERNAME / ATS_ACCOUNT_PW_SUFFIX, gitignored).

---

## 2026-07-12 — Workday account lifecycle: create-account recipe + sign-in leg = one loop

**What.** A per-employer Workday login is CREATED before it can sign in, so the account has a
lifecycle STATE and the button differs by state: `needs_creation`/`pending` → "Create Account";
`active` → "Sign In". Built both legs as DATA recipes (by accessible name, churn-immune AX layer),
verified against U.S. Bank's live Workday tenant:
- `WORKDAY_CREATE_ACCOUNT_RECIPE` — fields Email Address / Password / Verify New Password /
  acknowledge-checkbox → "Create Account"; honeypot ("Enter website… for robots only") NEVER filled.
- `WORKDAY_SIGN_IN_RECIPE` — Email + Password → "Sign In".
- `WORKDAY_ACCOUNT_LOOP` + `ats_accounts.next_account_action()` pick the leg from the account status;
  then hand to `WORKDAY_APPLY_RECIPE`. Endpoints: `/api/career_search/accounts/next-action`,
  `/mark-created`; recipes on `/api/runtime/apply_recipe`.

**The point.** create-account → sign-in → apply is ONE loop the (future, operator-run) **Account
Manager** executes so the operator doesn't manage it. BOUNDARY unchanged: these recipes are DATA;
they're run by the operator-triggered Account Manager / the operator, NEVER the agent's own loop —
the agent never types passwords into a site or submits account creation/sign-in.

**Also fixed:** `close_tab` now refuses to close a different tab when a specific tab_id/tab_url was
given but doesn't match (a truncated id fell through to closing the wrong tab live).

---

## 2026-07-12 — Career Search parent domain + Accounts moved in + operator "Create account"

**UI/domain restructure.** Domains is now hierarchical: **Career Search** (`kind: "group"`) is the
parent domain; the job engines + ATS (Indeed, LinkedIn, Workday) are its `children` and declare
`parent: "career_search"` so they nest (hidden from the top-level hub, shown inside Career Search's
"Sub-domains" tab). The top-level **Accounts** nav was REMOVED — the company-first `AccountsSection`
now lives in Career Search's **Accounts** tab (`GroupWorkspace` in DomainWorkspace.jsx renders the
group: no Status/Automation shell, just Sub-domains + Accounts). Files: `workspace/domains.js`,
`DomainWorkspace.jsx`, `DomainsHub.jsx` (filter out `parent`), `App.jsx` (pass onOpenDomain, drop
accounts route), `navigation.js`.

**Operator "Create account" executor.** `POST /api/career_search/accounts/create-account` — the
create leg of the account loop, built on the SAME operator-triggered pattern as the existing
`/api/accounts/{id}/login`: resolves the GENERATED credential server-side (never returned), scans the
live Workday Create-Account form, fills Email/Password/Verify + the acknowledge checkbox (SKIPS the
bot honeypot), clicks Create Account, stores creds in the vault + marks the account active. UI: a
"+ Create account" button on pending accounts. BOUNDARY: runs ONLY on the operator's button press —
the AGENT must never call it from its own tool-loop (never creates accounts / enters creds itself).
Also: deleted the stale U.S. Bank→Phenom account (Workday is the real apply backend).

**Rail nesting refinement.** The Domains SIDEBAR rail (not just the hub) is now hierarchical: only
top-level domains show at the "All Domains" level; Career Search always expands to its nested
Indeed / LinkedIn / Workday + a 🔐 Accounts item (`App.jsx` flatMap over `!d.parent`, `openDomainTab`).
Each sub-domain's own Accounts tab shows the company-first accounts filtered to THAT ATS
(`AccountsSection atsFilter=domain.id`); Workday now has an Overview + Accounts tab.

---

## 2026-07-12 — Event console: a cross-process feed of what the system did

**Gap.** Direct MCP drives (my bash curls to :8082) left no visible trace — the operator couldn't
tell at a glance that the system worked or was being used/trained. Nothing tied the two processes
together.

**Built.** A shared append-only JSONL event log in the dir both processes already share
(`observer_artifacts_dir` == `../mcp/output`): `apps/mcp/output/cache/event_log.jsonl`. Both write it
best-effort (never raises into a caller); the control plane serves it.
- `controlplane-api/event_log.py` + `mcp/app/event_log.py` (parallel writers, same file),
  `routers/events.py` (GET/POST `/api/events`).
- Instrumented: MCP `/capture` `/execute` `/close_tab` `/navigate`; control-plane account
  create/ensure/mark-created. Events: `{ts, source, kind, summary, detail, domain}`.
- UI: `EventsConsole.jsx` — live feed (source/kind badges, relative time, filter, pause) added as the
  **Activity** tab in the Career Search domain. Verified: MCP captures + control-plane apply/account
  events show together, auto-refreshing.

---

## 2026-07-13 — Driving a Workday PII form + the capture/label loop (live, U.S. Bank)

Drove Workday "My Information" for a FRESH account (not prefilled). What worked / bit us:

- **Capture→label loop:** control-plane `POST /api/capture {training_session_id, tab_id, tab_url}` makes
  a labelable `TrainingCapture` (MCP-direct `/capture` does NOT — its artifact 404s the label PATCH).
  Then `PATCH /api/observations/{filename}` `{observed_page_state}`. GOTCHA: the ISO-timestamp filename
  contains `+`; it MUST be URL-encoded (`%2B`) in the PATCH path or you get 404 / silent no-op.
  Labeled 2× `workday_my_information` (empty + filled) via session 16.
- **Use the `direct` driver for ATS form fills, not `humanized`:** humanized (wiggly mouse + cadence
  typing) is ~15-20s/field → times out at ~5 fields, and it TRUNCATED a value ("46 Canterbury Rd" →
  "46 Cante"). direct (Input.insertText) is ~1-2s and reliable. Bot-safety matters far less on a
  signed-in ATS than on the engine.
- **CLEAR before (re)typing** — a re-fill without clearing DOUBLED the postal code ("0330103301").
  Always verify text fields by value after filling (screenshot / `/locate` returns the value).
- **Act-by-name substring pitfall:** `target_name="State"` matched the **Country** field
  ("United **State**s…"); use the fuller name ("State Select One"). Single-select listbox dropdowns
  work (click field → click the option by exact name, e.g. "New Hampshire").
- **`How Did You Hear About Us?` nested prompt = confirmed gap** — clicking it surfaces no CDP-visible
  options; route to the operator (Online Source → Indeed), as WORKDAY_LESSONS said.

---

## 2026-07-13 — Reusable action for Workday prompts (`/select_prompt`) + stale-session validation

The nested-prompt gap ("How Did You Hear About Us?") is now a reusable atomic action, the prompt
analogue of human_click/human_type. `POST /select_prompt {field_name, value}` (apps/mcp):
1. **Open** the field with a NATIVE node-click (same path /execute uses) — a trusted-mouse-at-box-
   center did NOT reliably open the popup.
2. If the popup has a `input[data-automation-id=searchBox]`, type the value with **TRUSTED per-char
   key events** — Workday fetches prompt results SERVER-SIDE on real keystrokes; a react-safe
   value-set or `Input.insertText` does NOT trigger the fetch (confirmed via the new `/eval` debug
   endpoint: value became "Indeed" but zero options loaded).
3. **NATIVE-click** the matched option by accessible name — coordinate/`_trusted_click` on the
   option mis-fires on long/virtualized lists (picked "American Samoa" for "New Hampshire").

**Validated live on the State field** (State → New Hampshire, verified "State New Hampshire Required").
Caveats baked into WORKDAY_LESSONS.prompt_action: pass a PRECISE field_name (the accessible name
embeds the current value, and short names collide — "State" matches "United States"); a STALE session
silently returns NO options (the whole reason this looked broken for an hour — the Workday tab had
logged out; reload + re-auth first, per PRINCIPLES §1).

Also: added `POST /eval` (run JS in the tab, return value) as a dev tool for building/tuning actions.

## 2026-07-14 — Drove the U.S. Bank Workday apply through Application Questions; date-widget + transient-error + relogin lessons

Continued the live U.S. Bank (`usbank.wd1.myworkdayjobs.com`) Trust Reporting Analyst application on
session 16 (port 9322). Signed in (operator ▶ Login), then drove **My Information → My Experience →
Application Questions** to completion. What bit us / what to bake in:

- **Workday DATE fields (`dateSectionMonth-input`/`dateSectionYear-input`, role=spinbutton) do NOT accept
  typed input.** Both drivers failed: DirectDriver's `Input.insertText` and HumanizedDriver's per-char
  `char` events + react-safe set BOTH left the field *displaying* the value ("03/2026") while Workday's
  validation model stayed empty → "The field From is required and must have a value" on Save. Same class
  as the `/select_prompt` finding: Workday commits only on **trusted events**. **The reliable path is the
  CALENDAR PICKER**: click the field's "Calendar" button → it opens a **month grid** (year nav `< 2026 >`,
  Jan–Dec buttons) → click the month (trusted click registers). Verified: picking "Mar" set 03/2026 and
  cleared the required-error. Bake into the Workday recipe: dates = calendar-picker clicks, never typed.
  (Plain text inputs — Job Title/Company/Location, and free-text `textarea` like the "N/A" discharge box —
  DO accept DirectDriver `type`; only the segmented date/prompt widgets need trusted events.)
- **Workday throws a transient "Something went wrong — Please refresh the page and then try again."** at
  step transitions (hit it after sign-in→My Information AND after Application-Questions-save→Voluntary-
  Disclosures). It's a real, recurring page STATE with a deterministic recovery: **refresh**. Labeled it
  `workday_error_retry` (2 examples; post_action = whatever step was pending). Recognize→refresh, don't
  treat as a dead end.
- **A hard-navigate refresh can DROP the Workday session** (log you out). The `/navigate` (Page.navigate to
  the same URL) recovery worked the first time but the SECOND refresh returned us to "Start Your
  Application" **logged out** (top-right flipped account-email → "Sign In"). So Workday sessions here are
  short-lived / fragile: **completed steps persist on the candidate account** (My Info/Experience/Questions
  stayed ✓ and "Use My Last Application" is offered), but you must **re-auth** to continue. Next time try a
  SOFT reload (`location.reload()` via `/eval`) instead of a hard `Page.navigate` for the "Something went
  wrong" recovery — it may preserve the SPA session. Two logouts in one session also hints the rapid
  automated activity may be shortening the session; pace it.
- **Application Questions is a long compliance questionnaire (~16 listbox dropdowns + 2 textareas), all
  dropdowns share accessible name " Select One Required"** (can't target by name — target by
  `backend_node_id` from a fresh `ax_scan`, in DOM/y order = question order). Node ids were STABLE across
  single selects here (the earlier "reset" scare was a bad `textContent` verify read — **verify dropdown
  state by SCREENSHOT, not `button.textContent`**, which doesn't reflect the selection). DirectDriver
  `action_id:"select"` with an exact-substring `value` ("Yes"/"No"/"$75,000-$89,999") is reliable
  one-call-per-dropdown. A batch loop over ~14 selects TIMED OUT the bash tool — do them in batches of ~5.
- **L3 states captured+labeled this session** (all via control-plane `/api/capture` → PATCH, filename `+`→
  `%2B`): `workday_sign_in` (new — recipe-referenced but 0 examples), `company_careers_job_posting` (new —
  the Phenom careers.usbank.com posting w/ AI chatbot, the funnel step before the ATS), `workday_error_retry`
  (×2, new), `workday_my_information` (signed-in prefilled variant), `workday_my_experience` (empty + filled),
  `workday_questions` (empty + filled). Answers were operator-confirmed: work-auth Yes / sponsorship No /
  background-check + bonding acks Yes / willing-to-work-location Yes / desired comp **$75k placeholder**
  (operator will clarify the real number; their standing target is $130k) / all other screening = No / the
  "ever discharged/terminated" required free-text = "N/A".

## 2026-07-15 — Indeed's hidden decoy job cards + a job count that was really a filter badge

Ran a fresh `reporting analyst` / Nashua NH / 50mi search on session 16 and found two bugs in
`extract_jobs` (`apps/mcp/app/main_server.py`) — both fixed + verified live (18 rows → 17 real ones).

- **Indeed plants HIDDEN 0x0 decoy job cards.** A `[data-jk]` anchor (`id=job_fedcba9876543210`,
  `offsetParent === null`, 0x0 rect) sits alongside the real cards and extracted as a phantom job with
  an empty company/location. The hex jk looks like fixture data but is **Indeed's own** — it shows up in
  a real 07-14 capture trace. This is the SAME trap as smartapply's width-0 duplicate Continue button:
  **only ever trust the VISIBLE node**, in extraction as well as in driving. The extractor now filters
  on `offsetParent !== null && rect.width/height > 0`. A phantom in the shortlist is not cosmetic — it
  could burn an apply on a job that doesn't exist.
- **`meta.total_results` was reporting `1` for a full page.** Indeed no longer renders a job-count
  element at all (every count selector returns null), so the regex fallback ran against
  `body.innerText` — and `\s` spans newlines, so the filter chips "Distance\n**1**" + "**Job** Type"
  matched `/[\d,]+\+?\s+jobs?\b/i` as "1 Job". The count was the Distance badge. Fallback now uses
  `[^\S\n]` (no newline crossing) and requires plural "jobs"; **null is the honest answer** when the
  page shows no count — don't scrape a number off a badge. Anything recording `total_results` per query
  (targeted_search_and_apply) was recording a 1.

Also: **re-running a search via the Search button DROPS `radius` from the URL** — the distance filter
does not survive a re-query, so re-apply it after every search. `/set_distance` reported
`method: "url_fallback"`, i.e. the human widget path (trusted-mouse open of the distance pill) did NOT
open the menu and it fell back to the same-tab `radius=` rewrite. The cascade did its job, but the
preferred human path is silently degrading and is worth a look before it's the only thing left.

## 2026-07-15 — The distance pill: a STAGED-COMMIT widget, and why "the human path" kept losing

`/set_distance` had been silently reporting `method: "url_fallback"` — the human widget path never
worked. Root-caused and rebuilt on the live Indeed session; it now reports `method: "widget"`,
verified across 25 / 35 / 15 / 100 / 50. **Five rules, each of which cost a failed attempt, and all
of which should generalize to Workday + the unknown ATS popups:**

1. **A popup will NOT render in a hidden tab.** `document.visibilityState` must be `visible`
   (`Page.bringToFront`) or the opener click no-ops. This alone explains why the same code "worked"
   from a probe (which fronted the tab) and failed from `/eval` (which doesn't). A human's tab is
   visible when they click — foregrounding IS the humane path, not a trick.
2. **`.click()` does not FOCUS.** A real mousedown focuses; the synthetic one doesn't. Without focus
   the widget's keyboard protocol is dead — `activeElement` stayed `BODY` and arrows did nothing.
   `focus()` THEN `click()`, and the listbox takes focus + `aria-activedescendant` moves properly.
3. **The popup dismisses on BLUR → it cannot survive HTTP round-trips.** Every separate `/eval` call
   lost the menu. open→select→commit must run page-side in ONE evaluation.
4. **Selecting only STAGES the value — the footer's `Update` button commits it.** This was the actual
   bug, and nothing in the DOM/AX said so: the popup has Reset/Update buttons that only a SCREENSHOT
   revealed. It's why the old fiber-prop hack looked "invoked" yet nothing ever applied.
   (Cf. [[feedback_confirm_state_with_screenshot]] — the DOM lied by omission; the picture didn't.)
5. **The commit DESTROYS ITS OWN OBSERVER.** `Update` triggers a full navigation, so page-side code
   can't see its own result — `"Inspected target navigated"` IS the success signal. Confirm from
   OUTSIDE (read radius off `/json/list`).

**What was wrong before, and the general lesson.** The old path did a trusted-mouse click at the
pill's box centre (coordinates go stale the instant the menu re-renders — it landed outside and
*dismissed* the popup) and then invoked the option's React fiber props (Indeed's internals moved on).
Both are the two failure modes we already reject: coordinates, and reaching into a framework's guts.
Neither is needed. **Identify by ARIA/CSS semantics, drive natively, confirm every step** — that IS
the in-betweener layer, and it's now `_POPUP_SELECT_JS` + `_popup_select()`, config-driven
(`opener_selector` / `option_selector` / `option_label` / `commit_names`) so Workday and unknown-ATS
popups can reuse it instead of growing another bespoke path.

**The url_fallback is now OFF by default** (`allow_url_fallback=false`). A silent fallback is exactly
how a fully broken widget path went unnoticed for weeks: every caller still got its radius, so nobody
learned. A widget break must be LOUD. The URL is CONFIRMATION, never the mechanism.

Also worth knowing: `set_distance` is a FLOOR (`min_miles`), so it short-circuits `already` when the
current radius is larger — to exercise the widget you must start below the target.

## 2026-07-15 — Multi-tab capture was silently capturing the WRONG page (corpus-poisoning)

Captured the Wellington Workday create-account wall (tab 2) while Indeed (tab 1) was `[selected]`,
and got back a perfectly healthy-looking artifact **of the Indeed page** — which I then labelled
`workday_create_account`. A confidently mislabelled example is worse than no example: it teaches L3
that the Indeed SERP *is* a Workday account wall. Deleted it (`DELETE /api/observations/{filename}`),
root-caused, fixed, verified. **`tab_id` had never worked.**

**Root cause.** `list_pages` (chrome-devtools-mcp) returns a **dict**, not a list:
`{"raw_text": "## Pages\n1: <url> [selected]\n2: <url>"}` — a 1-based INDEX + URL, and **no CDP
targetId at all**. `_select_tab` did `pages = payload if isinstance(payload, list) else []`, so it
always parsed to `[]`, hit `if not pages: return`, and silently captured whatever page was
`[selected]`. Then `_verify_target_tab` saw the URL mismatch and — by design — "warned but didn't
block". Two silent failures in a row produced confident garbage. (This is the *same shape* as the
`set_distance` url_fallback: a fallback that always yields a plausible answer hides a dead path
forever. See the staged-commit entry above.)

**Fixed** in `apps/mcp/app/main.py`:
- `_parse_pages()` parses the real `raw_text` format (still tolerates a list payload).
- `_select_tab(session, tab_id, tab_url)` pins by **URL** — the only handle list_pages gives us —
  and returns whether it actually pinned. Ambiguous (>1 URL match) → refuse, don't guess.
- `_verify_target_tab(..., tab_pinned)` now **RAISES** on a URL mismatch when the tab wasn't pinned.
  A mismatch is only tolerable when we positively pinned by id/URL (then it's just a redirect).
- Verified: capturing tab 2 while tab 1 is `[selected]` now yields
  `page_identity.title = "Financial Reporting Analyst, US Funds"` @ wellington.wd5.myworkdayjobs.com.

**Consequences.** This closes the standing "multi-window/tab captures carry no window identity"
gap — but note **`tab_id` is decorative for capture**: pass a `tab_url` distinctive enough to match
exactly one page (a bare `indeed.com` will match several once an ATS tab is open). Any capture taken
of an ATS/second tab BEFORE this fix should be treated as suspect — it is probably a picture of
whichever tab was selected, not the one requested.

**The rule this keeps re-teaching:** *never let a fallback quietly substitute a plausible result for
the real one.* Fail loud, or the flywheel eats the lie.

## 2026-07-15 — The Workday create-account leg timed out mid-fill (a lesson that never propagated)

Operator pressed "+ Create account" for Wellington: the creds went in, then the UI said the driver
was unreachable and **Create Account was never clicked**. Event log tells it exactly:

```
17:58:40 type node 578  ok      <- email
17:58:42 type node 579  ok      <- password   (2s)
17:59:53 type node 580  ok      <- verify     (71s!)
(nothing — no click on submit)
```

**Root cause: `"driver": "humanized"` in `create_account_on_site`.** Humanized cadence-types at
~15-20s/field on Workday, so the third field blew the `httpx.AsyncClient(timeout=60.0)` → `HTTPError`
→ 502 "Create-account driver unreachable" — *after* filling, *before* submitting. The
**2026-07-13 entry already said this** ("use the `direct` driver for ATS form fills, not humanized —
~15-20s/field, times out at ~5 fields, and it TRUNCATED a value"), and the AppVault leg written the
next day used `direct` *with a comment explaining why*. Nobody went back and fixed the Workday leg.

**This is the third time the same shape has bitten us in two days:**
- `/select_prompt` learned "open with a NATIVE node-click, not trusted-mouse" → never reached
  `set_distance`, whose widget path was dead for weeks.
- AppVault learned "direct, not humanized" → never reached the Workday leg.
- AppVault learned "VERIFY it advanced before marking created" → never reached the Workday leg, which
  called `mark_created()` purely because the click returned. A stalled form (bad password, unticked
  ack) would have left a phantom `active` account with no login behind it.

**A lesson recorded in ONE call-site is not a lesson learned.** When you fix a leg, grep for its
siblings *in the same file* and fix them together, or the next session pays for it again.

**Fixed** (`routers/career_search.py`): Workday leg now uses `direct`, CLEARs before typing (`type`
appends — it once doubled a postal code to "0330103301"), and **verifies the form actually advanced
before `mark_created`**, returning `not_advanced` + leaving the account PENDING otherwise.

Also: **the signed-in signal on Workday is the account email in the header** ("Settings
genomags@gmail.com" / "Candidate Home"), NOT a "Sign Out" button — a `sign out` text probe returns
false while signed in.

UI: the accounts table rendered a separate `<table>` per company, each auto-sizing its own columns,
so no two company cards lined up, and five buttons on one `white-space: nowrap` row pushed the
actions clean off the right edge (the operator couldn't reach "+ Create account" without scrolling
sideways). Now `table-layout: fixed` + a shared `<colgroup>`, and the actions stack
primary → utilities → destructive. Verified: both tables measure identically, no horizontal scroll.

## 2026-07-15 — Workday widget layer generalizes; the SESSION is the real enemy (Wellington)

Drove Wellington's Workday My Information end-to-end (all 7 text fields + 4 widgets, verified by
value), hit Save and Continue → **4× opaque `Error - Page Error / VPS|<uuid>`** and a DISABLED Save
button. `/challenge_visibility` first (per the rule): no captcha. Then the recovery bit us.

- **A SOFT reload does NOT preserve the Workday session — this DISPROVES the hypothesis in the
  2026-07-14 entry** ("try `location.reload()` instead of Page.navigate — it may preserve the SPA
  session"). It doesn't. Both hard and soft refresh drop you to logged-out. **Refreshing to "recover"
  made things strictly worse**: we lost the whole fill AND the session. Do NOT reflexively refresh a
  Workday error — re-auth is the recovery, and the fill should be re-done after.
- **The 4 page errors were almost certainly an EXPIRED SESSION, not field validation.** No field-level
  errors, all 4 generic + server-side, and the reload revealed we were already logged out. Read
  `Page Error VPS|…` on save as "session is gone", not "your data is bad".
- **The step-count tell WORKS and caught it**: `current step 1 of 7` = the account step is back = you
  are LOGGED OUT; `1 of 6` = signed in, apply spine only. Cheap, deterministic, no screenshot needed.
  (Signed-in signal is the account email in the header — NOT a "Sign Out" button.)
- **Workday sessions are short under rapid automated activity — PACE IT.** Third logout in two
  sessions. Fill fewer fields per unit time and Save EARLY (each saved step persists on the candidate
  account, so a drop costs one step, not the whole application).

**The widget layer generalized — one protocol, three widget kinds** (`/widget_select`, new):
- Indeed distance pill: staged-commit (footer `Update` commits) — `commit_names:["Update"]`.
- Workday listbox (State, Phone Device Type): applies on select, no footer → `commit: found:false`.
- **Scope options via `aria-controls`/`aria-owns` on the opener.** Workday pages carry stray
  `[role=option]`s from OTHER fields (63 document-wide vs 5 scoped) — matching option text globally
  can click the wrong widget's option. `aria-controls` is the widget telling you which popup it owns.
- **BUG found + fixed: never infer "already open" from an option count.** Pre-open there is no
  `aria-controls`, so the scope falls back to document, counts another widget's strays, concludes the
  popup is open, and SKIPS THE CLICK. Open-state must come from the opener's `aria-expanded`.
- **Confirm staged via EITHER `aria-selected` OR the opener's label changing** — neither is universal
  (Indeed uses the first, Workday's dropdowns the second; and per 2026-07-14 some Workday dropdowns'
  textContent lies, so keep both signals).
- Workday's real field handle is its own `data-automation-id` (`formField-legalName--firstName`,
  `formField-addressLine1`, …) via `/execute`'s `selector` — stable, semantic, Workday's own contract.
  `scan_form` is NOT usable here: every field returns the whole fieldset's text as its label, so
  First/Middle/Last are indistinguishable.

**Per-TENANT variation is real — do not hardcode a prompt's options:**
- "How Did You Hear About Us?" on Wellington is a FLAT list with **no Indeed and no "Online Source"**
  (Career Site - eFinancial/Glassdoor/Wellington, Diversity Association, Other, Previous Employee or
  Consultant, Recruiting Agency). U.S. Bank had the hierarchical Online Source → Indeed. Operator
  chose **Other** (the only truthful option — we came from Indeed, which isn't offered).
- Its options are plain `div`s with **no `role=option`**, and `/select_prompt` found no `searchBox`
  here, so the searchbox-typing path is tenant-specific too. `/select_prompt`'s `field_role` defaults
  to `textbox`, but Wellington's prompt field is a **button** — pass `field_role` explicitly.

## 2026-07-15 — FIRST FULL WELLINGTON WORKDAY APPLICATION SUBMITTED (end-to-end, agent-driven)

**Financial Reporting Analyst, US Funds · Req R94007 · "Under Review" · submitted July 15 2026** —
confirmed in Workday's own My Applications (Active 1). Operator created the account + pressed Login;
the agent drove every step after. All 6 steps captured + labelled (`workday_my_information`,
`workday_my_experience`, `workday_questions`, `workday_voluntary_disclosures`, `workday_self_identify`,
`workday_review`, plus `workday_error_retry`).

**The re-fill took 1.5s for 11 fields** (7 text + 4 widgets) vs the 71s/field that blew the timeout
before. Identical data, saved clean on the first try — which retroactively **confirms the 4×
`Page Error VPS|…` was a DEAD SESSION, not bad data**. Read it that way next time: re-auth, don't
refresh, don't re-diagnose the fields.

**Workday widget protocols that now work (all via `/widget_select` or the same shape):**
- **Text fields** → `[data-automation-id="formField-<name>"] input` + `/execute`'s `selector`, CLEAR
  then `direct` type. Workday's `data-automation-id` is its own stable contract — use it, not labels.
  (`scan_form` is useless here: every field returns the whole fieldset's text, so First/Middle/Last
  are indistinguishable.)
- **Listbox dropdowns** (State, Phone Device Type, veteran, gender) → `/widget_select`, scoped by
  `aria-controls`. Applies on select, no footer commit.
- **MONTH picker** (`formField-startDate`, MM/YYYY) → typed input NEVER commits. Open `dateIcon`
  (aria=Calendar) → tiles are `monthPickerTileLabel` whose **aria-label carries "March 2026"**
  (month AND year — so year nav is self-verifying via `monthPicker{Left,Right}Spinner`) → click the
  tile → confirm from `dateSection{Month,Year}-display`. **Proof it committed: Save produced no
  "field is required" error**, which is exactly how typed input fails.
- **DAY picker** (`formField-dateSignedOn`, MM/DD/YYYY) → same opener; today's tile carries
  `data-automation-id*=datePickerSelectedToday` and aria-label `"Selected Today Wednesday 15 July 2026"`.
- **Checkbox groups** (ethnicity, disability, consent) → click the LABEL by its exact text, then
  verify `input.checked`.
- **Resume upload** → `/execute` `action_id:"upload"` + `selector:"input[type=file]"` +
  absolute path (DOM.setFileInputFiles; a click would open an OS dialog CDP can't drive). Confirmed
  by the page's own "GM_Resume.pdf successfully uploaded".
- **Conditional fields are real**: ticking "I currently work here" REMOVES the End Date field.

**Per-TENANT variation is the rule, not the exception** — the same logical field differs across
Workday tenants, so recipes must describe SHAPE, not fixed options/ids:
- Application Questions' `data-automation-id`s are **opaque hashes** (`formField-c6b3456dfd0a…`) —
  no semantic handle at all. Target by QUESTION TEXT.
- "How Did You Hear About Us?" — U.S. Bank: hierarchical, searchBox, Online Source → Indeed.
  Wellington: FLAT, plain `div` options (no `role=option`), **no searchBox, and no Indeed at all**.
  `/select_prompt`'s `field_role` defaults to `textbox`; Wellington's is a **button**.
- Gender offered **only Female/Male — no decline option**, while veteran ("I do not wish to
  self-identify") and ethnicity ("Choose Not to Disclose") both had one. A blanket "decline
  demographics" preference CANNOT be honoured everywhere — surface the constraint and ask.
- Ethnicity granularity: tenants split Asian into Central/East/South/Southeast/West/Other, so a
  stored `race_ethnicity=Asian` is too coarse to map. Saved `ethnicity_detail` to stop re-asking.

**Answers reused from the store** (full_name, street_address, city, state, postal_code, phone,
phone_device_type, resume_job_1_*) — the store IS the reuse mechanism; four new answers were written
back (primary_language, additional_languages, ethnicity_detail, current_employer). NOTE `todays_date`
in the store was stale (06/28) — a signature date must be computed, never read from the store.

## 2026-07-15 — The apply EPILOGUE is now a real step, not prose (tab hygiene + recording)

`APPLY_EPILOGUE` described "record the outcome, close the apply tab, refocus search" and MCP
`/close_tab` was the primitive — but **nothing ever wired them**. So every finished apply left an
orphan ATS tab and an unrecorded outcome: the loop couldn't distinguish "applied" from "still open",
and tab cleanup was manual. Now **one call**: `POST /api/career_search/apply/epilogue`.

- **RECORD before CLOSE.** A failed close still leaves the outcome known; a closed tab with no record
  is unrecoverable. The endpoint commits the prospect first, then does tab hygiene, and reports both.
- **Upserts the prospect**, so an epilogue works even for a job that was never extracted into
  `observed_jobs` (our Wellington apply was driven straight off a card click).
- Runs on EVERY terminal, same shape: `applied` (CONFIRMED submitted — the only status that stamps
  `applied_at`) / `abandoned` (stopped at a human-required wall; stays resumable) / `skipped`.
- Records the ATS provenance the corpus needs: `application_platform=workday`,
  `apply_type=company_site`, `tenant_id=<req id>` — so "which ATS did this actually go through" is
  answerable later, per company.
- **Verified live**: Wellington · req R94007 → recorded `applied` @ 19:39Z, closed the Workday tab,
  refocused the Indeed search, `remaining_tab_count: 1`.

Standing rule this encodes: **the loop must end each prospect on a clean single-tab search.** Tab
hygiene isn't tidiness — orphan ATS tabs are what made `tab_url` matching ambiguous for capture
(a bare `indeed.com` matches several pages once an ATS tab is open; see the capture entry above).
