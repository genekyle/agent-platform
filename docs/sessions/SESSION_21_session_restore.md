# SESSION 21 — restore the session exactly, so recovery becomes an experiment

_Written 2026-08-27, operator-directed. Pick this up cold. Read `docs/PLAN_staleness.md` §0–§1a
and §4 first — this session gives that detector the remedy it has never had._

## The problem, and it is worse than "we have no restore"

**The signed-in sessions live in `/tmp`.** Measured 2026-08-27: the two running browsers use
`user-data-dir=/tmp/agent-platform-training-chrome/persistent/{indeed,linkedin}` — the default in
`settings.training_chrome_profiles_dir`, never overridden. `/tmp` is `/private/tmp` on macOS and
is cleared on reboot. So the Indeed and LinkedIn logins — the things that cost a **human** to
re-create, 2FA and checkpoints included — are sitting in a directory the operating system deletes,
with no copy anywhere. That is the whole justification for this session, and it was found by
looking rather than by losing them.

And there is no snapshot/restore of any kind: nothing reads or writes cookies anywhere in the
repo (`PLAN_staleness.md` §4 says so in as many words — *"Cookie TTL is not read anywhere …
`cookie_expires_at` is always `None` today"*).

## Why this is a LEARNING feature, not an ops convenience

The operator's framing, and it is the load-bearing idea: *"a way to help the system fight its way
out of stale sessions and learn how to fight back rather than succeed."*

Today a stale session is an **incident** — it happens once, someone improvises, and whatever they
learned is prose in LEARNINGS. With a restore it becomes an **experiment**: freeze the broken
state, try a recovery, measure it, restore, try a different one. Three things fall out that the
repo has been unable to reach:

1. **`RENEW` finally has a remedy.** The staleness detector's four verdicts are CONTINUE /
   REFRESH / RENEW / HANDOFF, and RENEW means *"reloading cannot repair this (logged out, session
   gone)"*. Today that verdict's only answer is a human re-login. Restore is the first thing the
   system can DO about it.
2. **`cookie_ttl_s` stops being inert.** It is a declared signal that always reads `None` because
   nothing reads cookies — and `PLAN_staleness.md` §4 says landing it *"is a one-line change at
   the call site"*. **The same CDP cookie read powers the snapshot and the dead signal.** One
   piece of plumbing, two payoffs — and the operator predicted exactly this on 2026-07-26:
   *"staleness conditions can depend on cookie timing."*
3. **It makes the staleness research answerable.** §3 of that plan asks *"for each signal, at what
   value does the next action's failure rate rise?"* and forbids promoting staleness to a gate
   before that is measured. Waiting for natural decay makes that research take months. Restoring a
   dated snapshot and re-driving it makes it a bench experiment.

This is also the recorded principle **"stale sessions are fixtures"** given a mechanism: a
snapshot of a BROKEN state is a regression test for recovery.

## What to build

**1. Two tiers, because they answer different questions. Both measured, 2026-08-27:**

| tier | what | size | when |
|---|---|---|---|
| **identity** | Cookies, Local/Session Storage, Login Data, IndexedDB, Service Worker | **~2.3 MB** of a 117 MB profile | cheap enough for many generations |
| **cold full** | the whole `user-data-dir` | 117 MB (433 MB across profiles) | rare; the belt-and-braces copy |

The 2% figure is the design: **everything that carries identity is ~2.3 MB and the other 115 MB is
cache and code-cache that rebuilds itself.** Fifty identity snapshots cost less than one full copy.

**2. The warm path is CDP, the cold path is a file copy, and they are not interchangeable.**
Chrome holds Cookies in a WAL-backed SQLite file, so copying it under a running browser can read
torn state — take cookies live with `Network.getAllCookies` (complete, consistent, no downtime)
and per-origin storage through the `DOMStorage`/`IndexedDB` domains or an evaluate on an open tab.
The cold copy is for when Chrome is already stopped, which is exactly the restart case below. Say
in the code which tier a snapshot is, because a restore from the warm tier is *not* claiming
byte-fidelity and must not pretend to.

**3. A restore VERIFIES and reports (SESSION 20's rule, applied here).** A perfect local restore
does **not** mean the server still honours the session — `PLAN_staleness.md` already warns that
`idle_s` *"says nothing about what the SITE did"*, and a server-side expiry is invisible to us.
So: restore → probe `/auth_state` → report `restored_and_authenticated` vs
`restored_but_logged_out`, honestly and distinctly. A restore that silently lands on a login wall
is exactly the false success this repo keeps paying for.

**4. THE SECRET BOUNDARY, AND IT IS THE STRICTEST CONSTRAINT HERE.** A cookie jar is a **bearer
credential, strictly more powerful than the vault password it bypasses** — it carries 2FA and
checkpoint state with it. PRINCIPLES §4 says capture per state, never secrets. So a snapshot:
- lives **outside the repo**, the way `.env` does, referenced by an id — the `secret_ref` pattern
  `accounts.py` already uses (`env:INDEED_PRIMARY`), never the value itself;
- **never** enters the transition corpus, a capture artifact, a screenshot path, an intent journal
  row, a log line, or LEARNINGS;
- is journaled as *a snapshot was taken/restored*, with its id and verification verdict and
  nothing else — the `errand.login_code` precedent, where the journal knows a code was read and
  never the code.
Get this wrong and the corpus becomes a credential store. Write the test that proves a snapshot id
can appear in a journal row and its contents cannot.

**5. Retention, stated rather than discovered.** Snapshots of a live login do not expire on their
own and 433 MB of profiles is already on disk. Decide a policy (keep N per profile, plus any
explicitly pinned as a recovery fixture) and enforce it in code, because an unbounded store of
bearer credentials is the worst possible thing to leave growing quietly.

## Then prove it — and the first test is the thing that motivated it

**The merge-and-restart is the experiment.** Snapshot both live profiles, do the merge and the API
restart (which also fires the armed `search_queries` column drop), and restore. Then:

* the LinkedIn and Indeed sessions come back **signed in**, verified by `/auth_state`, not by
  assumption;
* take a second snapshot, deliberately break it (clear cookies for one origin), restore the first,
  and confirm the recovery — that is the fixture loop working;
* read `cookie_ttl_s` for real and put a number where `None` has always been.

## Definition of done

* Both tiers implemented, with the tier named in every snapshot's record.
* Snapshots stored outside the repo, referenced by id; a test proves the contents cannot reach a
  journal row, a capture, or a log.
* Restore verifies against `/auth_state` and distinguishes restored-and-authenticated from
  restored-but-logged-out.
* Retention enforced in code.
* `cookie_ttl_s` reads a real number; `PLAN_staleness.md` §4's first bullet is retired in place.
* The merge+restart was survived and the sessions came back signed in.
* `docs/LEARNINGS.md` entry, including what the warm tier turns out NOT to capture — that gap is
  the finding, and the next reader should not have to rediscover it.

## What NOT to do

* **Do not put the snapshot anywhere the corpus can reach**, however convenient. See §4 above.
* **Do not move the profiles out of `/tmp` as "the fix" and stop there.** Relocating them is worth
  doing and is a *different, smaller* fix — it makes loss less likely and gives you nothing to
  restore FROM, and nothing to practise recovery with.
* **Do not auto-restore on a stale verdict.** `PLAN_staleness.md` is explicit that nothing acts on
  the verdict while the thresholds are guesses, and restoring an old cookie jar unbidden is
  bot-safety-relevant besides. Offer the remedy; the operator presses.
* **Do not claim byte-fidelity for the warm tier.** Name what it captured.
