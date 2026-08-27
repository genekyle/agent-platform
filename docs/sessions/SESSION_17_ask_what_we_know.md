# SESSION 17 — ask what we already know: consultation becomes an input, not an archive

_Written 2026-08-26. Pick this up cold; read `docs/PLAN_generalization_v1.md` §0 class 1 and §2
P4 first. Depends on nothing — S14–S16 are parallel-safe, but if unstarted, do them first (they
are lower-numbered for a reason)._

## The problem in one paragraph

The log counted this class to **eight explicit instances**: the registry note that predicted
Paylocity's upload modal, on file and unread at the moment it mattered (08-19); Cornerstone's
"two Apply Now buttons — drive the VISIBLE one," written 08-11, rediscovered by screenshot on
08-24; credentials sitting in the vault while the flow opened a second account row for the same
employer (08-24, operator: *"we had the creds on file, should've checked there first"*);
`tab_claims` never consulted while classify read a parked tab and poisoned a step's platform for
hours (08-24); "Continue Application" missing from a matcher while the registry knew the tenant
(08-25). The producers all exist — registry notes, `apply_requirements.blockers()`, the accounts
vault, `tab_claims`, `ats_brief` — and **the deciding seam never asks**. A note nothing reads at
decision time is not memory, it is an archive.

## The work

**1. One composed read, no new tables.** `orientation_context(platform, kind, rung)` — a pure
composition over the existing authorities (§15: compose reads, never copy facts out of their
authority): the registry note scoped to what this rung is about to do, `blockers()` for the
platform, a vault hit for this ATS/company (canonical-company tier, the 08-24 lesson), the tab
claim for this step, the brief headline, and world-fact freshness flags where S16 has them.
Absence of any piece is a `None`, never a fabricated empty.

**2. Wire it at the seams that paid for it, in this order:**
   a. **classify** — brief + `blockers()`: *can this flow finish, and which wall is coming?* The
      UNE drive needed exactly this on 08-19 and could not ask.
   b. **the entering rungs** (`open_pane`, `enter_apply`, advance) — control quirks from the
      registry note surface **inside the resolution attempt** (duplicate names, renamed
      controls), not in a side panel.
   c. **the account rung** — the vault check runs BEFORE the wall renders, keyed through the
      canonical company, so an existing credential is found before a new row is opened.
   d. **verify/flag** — platform hints resolved from the step's own claimed tab, never from
      "the newest apply-ish tab."

**3. The enforcement point: consultation is visible in the trail.** A rung that consulted
journals what it learned in its rationale (*"registry: two Apply Now buttons — drove the visible
one at y=411"*). A consulted fact that changed nothing journals nothing — the trail shows use,
not ceremony.

## Then drive, and let the drive prove it

Pick the next fresh prospect (a platform with a registry note is ideal — Cornerstone's MACOM park
is sitting there with a known upload mechanism and the double-button note). The measure is a
drive where a fact that would previously have been rediscovered is instead **cited in the step
trail before the act**. If the drive hits ANY fact-existed-nothing-asked instance, record it —
that is the §6 falsifier firing, and the answer is the choke-point design, not more wiring.

## Definition of done

* `orientation_context` exists as a composed read with tests per source (each source absent →
  `None`, never invented).
* Wired at classify, entering rungs, account rung, verify/flag — each with a pinned test that the
  consultation reaches the rationale.
* One live drive whose trail cites at least one consulted fact, and zero rediscoveries.
* `docs/LEARNINGS.md` entry, including any instance the falsifier caught.

## What NOT to do

* **Do not build a knowledge base, embeddings, or retrieval.** Every source already has an owner
  and a query; this session is wiring, and the whole point is that wiring was the missing part.
* **Do not copy facts into the context.** Compose reads at call time (§15 — one authority per
  identity question); a cached copy is tomorrow's stale snapshot (the `account_handoff` lesson).
* **Do not block on absence.** No note is not a stop; it renders as "never driven — capture
  everything," the `ats_brief` rule.
