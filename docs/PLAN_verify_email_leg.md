# PLAN — the `verify_email` leg, and the four-session tandem

_Adopted 2026-08-22, out of the reflection audit (LEARNINGS 2026-08-22). Part 1 is the design the
verify-leg session builds to; Part 2 is the coordination contract for the four workstreams running
in parallel worktrees today. Coach/originator: the reflection session; this doc is the play card._

## Part 1 — the `verify_email` leg

### What it is

A third leg in `ACCOUNT_FORMS`, beside `create_account` and `sign_in`. It exists because email
verification is a wall with its own shape: it can appear right after the create submit, on a later
sign-in from a fresh session, or not at all — and it is the one wall on the operator's real-gate
list (captcha / email code / 2FA / honeypot) that the built `fetch_login_code` errand was designed
to remove.

### Due-ness is MEASURED, never stored

The leg is offered when the live scan matches the verification wall (`_ACCOUNT_VERIFY_MARKERS` +
what is actually on screen), the same way the page-picker chooses a page. No stored flag or counter
makes it due (a flag survives a refresh, an abandoned attempt, and a wall the ATS decided not to
show this time — all the 08-20 counter sins). The 08-21 lesson binds here too: "which leg is due"
is volatile and derived at read, never snapshotted.

### Two mechanisms, classified from the screen

- **`code`** — a code-entry field is present. Automatable NOW, end to end: errand fetch → stage →
  submit → re-classify. This is the v1 deliverable.
- **`link`** — no code field; the copy says "click the link in your email." Stays `human_required`
  in v1, honestly: the errand deliberately never opens a thread (no read receipt), and a link-click
  errand is a new spine. The cockpit says exactly that, with a truthful exit. Every `link` sighting
  is RECORDED (see "what it writes") so the ledger accumulates the case for building the link spine
  — data first, then the build, never the reverse.

Classification is from the measured page: presence of a code input decides `code`; absence plus
link-language decides `link`; neither → scan-and-refuse like an unmapped ATS page.

### The `code` drive shape

1. Wall matched, mechanism `code` → call `errands.resolve_login_code` with sender hints from
   `gmail_senders.senders_for(ats_id, company, instance_domain)` (contract below). The errand's
   own rules stand unmodified: freshness is proof, ambiguous/blocked ESCALATE, a `not_found` costs
   one human glance and never a guess.
2. `ok` → stage the code into the field and submit via the page's declared submit control. The
   code is `sensitive=True` forced (never inferred), masked in journal, response, and UI alike —
   the evidence never cites the secret.
3. **Re-classify after submit.** `expect_followup_factor` is true by default for a reason (the
   2026-07-10 Indeed lesson: the code got past the email wall and the site then demanded phone
   2FA). Getting the code is not getting in; the next state is measured, not assumed.
4. Escalations park with the wall OPEN in the live tab and the handoff beside it — the 08-20
   account-rung shape: one action wide, named, staffed.

### `completes_leg` discipline (unchanged, restated because it is the point)

Only a page declaring `completes_leg` may end the leg, and no page claims it until someone has SEEN
the screen that would prove it (post-verify signed-in signal, an explicit "verified" confirmation,
or a redirect into the authed app). The default is the safe answer. The leg never writes
`mark_created` — creation was the create leg's completing page's claim; verification is its own
fact.

### What it writes

- The transition rows and journal like every other leg — this is also a NEW state family for the
  corpus (verification walls per ATS), which is the capture-is-the-work rule paying again.
- An instance-scoped, measured `ats_characteristics` row: `verification_mechanism` (`code` |
  `link`) with provenance — the characteristics table's second live writer, after 08-21's `auth`
  row. On a successful `code` match, also write the measured sender domain (feeds hints; see
  below).
- **Naming: retire the collision at this seam.** `awaiting="operator_verify"` already means
  "search submitted-not-confirmed" elsewhere (session_control.py:2469; lifecycle.js:71 renders
  that meaning's copy). This seam returns `awaiting="account_verify_email"` with its own copy —
  the `operator_2fa` precedent (LEARNINGS 2026-07-xx, cited at :2912) applied before it bites
  instead of after.

### Cockpit UX — right the first time

The `account_handoff` focus pattern extends; nothing new is invented:

- A **Verify email** card appears when (and only when) the wall is measured. It names the account
  row, the mailbox it will read (the shared Gmail identity), and the mechanism it measured.
- **`code`**: primary button **"Fetch code from Gmail & continue"** (drives the leg). Exits:
  **"I entered the code"** (operator did it by hand) and escalate. The code itself never renders.
- **`link`**: instruction text ("the site sent a verification LINK — press it in Gmail, then
  continue"), primary exit **"I clicked the link — continue"**. No automation button pretends to
  exist.
- Button labels interpolate the page's real control names where the form table knows them — never
  a hardcoded pair (the 08-20 two-labels-that-lied lesson).
- Every state has a truthful exit; a card that cannot act says so instead of showing a dead
  button. The card derives from live account state at render (the 08-21 snapshot-split rule); the
  wall identity (which account, which ATS) is the stored half, the leg/mechanism the derived half.

### The `gmail_senders.py` shared-module contract (tandem seam #1)

New small module `apps/controlplane-api/gmail_senders.py`, ONE table read both directions:

- `senders_for(ats_id, company=None, instance_domain=None) -> list[str]` — hint domains for the
  errand. Cold-start from the registry hosts catalogue + per-ATS conventions (e.g. workday tenants
  mail from `myworkday.com`); prefer a MEASURED instance-scoped sender characteristic when one
  exists over the registry constant (the 08-21 consult-side rule, now with a consumer).
- `classify_sender(from_address) -> ats_id | None` — the same knowledge backwards, for the
  outcome matcher.

**Ownership:** the verify-leg session CREATES the module (it needs `senders_for` at the seam) with
`classify_sender` present but minimal; the outcome-matcher session EXTENDS `classify_sender` after
rebasing. Neither builds a second copy. `errands._sender_hints` (domain_id suffix-stripping)
becomes a fallback inside `senders_for`, not a competing path.

### Safety invariants (unchanged, listed so nobody re-litigates them)

Never guess an ambiguous match; freshness is proof; the code is never journaled, logged, echoed,
or rendered raw; subject lines only, threads never opened; Google's password/2FA/consent pages are
never driven; credential flows collect state identity only (PRINCIPLES §4). The operator's
boundary stands: the agent stages and continues — it never types a PASSWORD; the one-time code is
the errand's charter, granted 2026-07-24 with account automation.

## Part 2 — the tandem: lanes, seams, merge order

Four sessions run today. The goal is compounding, not four parallel monologues: **verify wire
(fewer human stalls per application) + outcome matcher (flows finally close; the ledger answers
"did it work?") + click↔observe calibration (fewer teacher escalations on learned ground) + the
scorecard (the flywheel's throughput made visible and therefore sustainable).**

### Lanes (files a session owns; touch outside your lane = coordinate first)

| Session | Owns | Does NOT touch |
|---|---|---|
| **verify-leg** | `session_control.py` verify seam (~:4123), `account_forms.py` (`verify_email` leg + pages), `gmail_senders.py` (creates), `errands.py` hint fallback, `lifecycle.js` + cockpit verify card, `apply_recipe.py` verify states | the matcher, controller/, Learning UI |
| **outcome matcher** | new matcher module, `application_events` writers, `submission_verifier` hint additions, `gmail_senders.classify_sender` (extends), drive-end sweep AS ITS OWN MODULE with a one-line hook call | `account_forms.py`, the verify seam body, lifecycle.js |
| **shadow mining** | `controller/` (rails/thresholds/metrics), transition labels via the API | `session_control.py`, UI |
| **UI scorecard** | `controlplane-ui` Learning/Overview, one additive read-only scorecard endpoint (own router or `routers/transitions.py` read side) | `lifecycle.js` cockpit derive, write paths |

Known shared files and their rule: `session_control.py` belongs to verify-leg this round — the
matcher's drive-end sweep enters through one hook line added AFTER verify merges;
`routers/transitions.py` — shadow labels via HTTP (no file edit), UI adds read-only only.

### Merge order (rebase on main before merging; scoped `git add` paths, never `-A`)

1. **verify-leg** (deepest in shared files — lands first, creates `gmail_senders.py`)
2. **outcome matcher** (rebases, extends `classify_sender`, adds its hook line)
3. **shadow mining** (independent; may land any time its diff is controller-only)
4. **UI scorecard** (last — reads the others' shapes once they are stable; render event KINDS
   generically so the matcher's new kinds appear without a UI change)

### Standing rules for all four

- Additive, never a new required field; failures loud; every drive through the system.
- Each session appends its LEARNINGS entry and runs the suites from its worktree with import
  provenance verified (the venv and node_modules live in MAIN — the wrong-module trap).
- The reflection audit (fresh numbers: shadow 59.5%/294, queue 373, click↔observe 106/119, zero
  post-submit events) is the 2026-08-22 LEARNINGS entry on main as of today — rebase early.
- Throughput is measured where the docs already say it is: rows banked, labels written, parks
  answered — plus, once the matcher lands, **flows closed** and **outcomes recorded**. The
  scorecard session gives all five a screen.

### Seam rulings (2026-08-22, coach — binding for this round)

1. **`packages/interaction/interaction/decision.py` is the shadow lane's this round**, additive
   only: `Bundle.phase` appended last and defaulted, not rendered by `bundle_to_prompt` (no schema
   bump), `bundle_digest` includes phase only-when-set so old digests are unchanged. No other lane
   touches this file.
2. **The two-line live wire in `_shadow_the_crank`** (session_control.py ~:6998 — pass
   `phase=rung.id` and the candidate-names `page_text`) is **NOT part of the verify-leg diff**.
   The shadow session lands it as its own scoped follow-up commit after verify-leg merges — a
   narrow, named exception to the session_control ownership, exactly those two lines. Verify-leg:
   do not apply it for them; a transcription by a lane that didn't design it is invisible until
   re-measured.
3. **Replay numbers are HYPOTHESIS, not the gate.** The phase-rail backtest (0.595→0.776 overall;
   indeed_quick_apply:indeed_job_posting 0.657→0.985/67) was designed on and evaluated against the
   same 294 historical pairs. The promotion gate fills with FRESH shadow rows from post-fix drives
   (≥90% over ≥25 per scenario, per-state per-ATS, fall-through intact) — nobody promotes off the
   backtest. The misfiled workday/company_site scenario clocks restart honestly under the
   corrected state facet.
4. **`classify_sender` keeps the doc's shape**: `classify_sender(from_address) -> ats_id | None`,
   one table read backwards. The matcher session's interim `ATS_MAIL_DOMAINS` folds INTO that
   table at its rebase (its local copy carries a marked TANDEM SEAM comment until then); richer
   attribution, if ever needed, is a second function, not a wider signature. Verify-leg: build the
   table so mail domains sit beside site domains — one row per ATS, both readers.
5. **The matcher session owns `JobDatabaseSection.jsx` this round** — its needs-review inbox
   queue gets an "Inbox" tab there (career_search Database), which nobody else listed. Scorecard
   keeps Learning/Overview. Reach-parity terms: every review row has truthful exits (approve
   writes the event, dismiss records why, ambiguous shows its candidates), and event KINDS render
   generically. The drive-end hook stays ONE line calling `inbox_sweep.sweep`, placed by the
   matcher session in its rebase commit after verify-leg merges — same pattern as ruling 2.
