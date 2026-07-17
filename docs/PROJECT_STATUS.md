# Project Status — Supervised Browser Agent

_Last updated: 2026-07-16 — **full rewrite**. The previous version (2026-06-15) described the
SELECT-cascade era and no longer matched the system; it's in git history. This version describes the
Interaction-API era and, unlike its predecessor, states the corpus numbers instead of the corpus
intentions._

## What we're building (one paragraph)

A **supervised browser agent** that runs a per-step loop — classify → propose → select → act →
verify — where each decision is made by the **cheapest tool that's confident**, a human catches
anything that reaches the top, and every action is **journaled** as training data. The model emits
**intents from a closed vocabulary** through the Interaction API (the model says WHAT, the recipe
says WHERE, the API says HOW, the journal says WHAT HAPPENED). Claude is the **teacher**: it drives
novel domains, and its journaled work distills into local models (L3 perception, L4 intent policy)
that graduate learned scenarios off the expensive models entirely. Hard constraint: resource-efficient
(solo founder) — a **$5/week autonomous spend cap** is enforced.

## Terminology (one overload, fixed here)

- **Cascade layers L1–L6** — the SELECT cascade rungs (rules / cache / tiny classifier / micro
  selector / Haiku / vision+human).
- **L3 and L4 as model names** — L3 = the **page-state (perception) model**; L4 = the **intent
  (policy) model**. Historically these named the cascade layers they were to occupy; since the
  Interaction API, **L4's job changed**: it emits *intents* (trained from the journal), not raw
  element picks (trained from selection telemetry). When a doc says "L3/L4" it means the models.
- **API tiers 1–3** — the Interaction API namespace: tier 1 primitives, tier 2 site-agnostic
  protocols, tier 3 domain skills (`PLAN_interaction_api.md` §7).
- **Rungs** — the resolve_answer cascade (exact → normalised → alias → Haiku → ask).

## What changed since the last status (2026-06-15 → 2026-07-16)

1. **The agent did real work.** Indeed smartapply submitted end-to-end (07-11); FB create-listing
   driven live (07-10); cross-domain Gmail login-code errand (07-10); **first full Workday
   application submitted** (Wellington, 07-15); Greenhouse (KKR) driven to its last field (07-15).
   Career Search became a parent domain with an ATS taxonomy (Workday, Greenhouse, AppVault, iCIMS…),
   an accounts system, and an apply epilogue.
2. **The widget protocol layer was discovered** (the Indeed distance pill): AX finds *elements*, but
   widgets are elements + a *protocol* (open → stage → confirm → commit → confirm outside). See
   `interaction-layers.md`; PRINCIPLES gained §6–§8.
3. **The corpus reckoning (07-16).** The event log was never a training corpus (1000-line ring
   buffer, no fingerprint/session/outcome, no trainer reads it), and the real corpora are written
   only by `runtime/loop.py`, which live drives never touch — so three months of live work produced
   **zero** training rows. This is the finding that forced the journal-first Interaction API and the
   spine decision. See LEARNINGS 2026-07-16 and `DECISION_two-stacks-one-spine.md`.
4. **Interaction API Phase 1 shipped (07-16), journal-first**: `packages/interaction` (contract +
   fingerprint + append-only journal), `intent_api.journaled` route decorator,
   `resolve(ats, field)` over one recipe schema (`apply_fields.py`, 32 fields / 3 ATS),
   `/describe_widget` (12 widget types), `/select_option`, `/set_date`, `/check_group`,
   `/scan_required`, `/probe` (journaled discovery). 119 tests green.
   **Phase 1's Definition of Done is NOT met**: no live drive has run through the new endpoints;
   the page-side JS is unvalidated (PRINCIPLES §5). That is the next session's first job.

## The per-step loop — status

| Stage | Status | Where | Note |
|---|---|---|---|
| classify | ✅ built | `escalation_rules.py` | verified on real reCAPTCHA / 2FA |
| propose | ✅ built | `mcp/app/observer/ax_proposer.py` | AX sidecars emitted unconditionally on every capture |
| select | ✅ built | `select_stage/` | cache + Haiku SoM live; L1/L3/L4 cascade slots still empty by design |
| act | ✅ built, **fired live extensively** | `mcp/app/executor/` + tier-2 protocols | via teacher drives; the autonomous loop remains record-only/`run_live`-limited |
| verify | ✅ built (element-level) | `select_stage/verifier.py` | protocol-level verification now lives in tier-2 outcomes (`ok` = verified at commit) |

Guardrails all live: $5/week cap (`anthropic_usage.enforce_budget`), human escalation on
stop-state / over-budget / low-confidence / no-match / verifier-fail, never auto-solve
captcha/2FA, secrets never captured.

## The two execution stacks — and the corpus reality (measured 2026-07-16)

The **runtime loop** (`runtime/loop.py` + `select_stage/`) is the flywheel machine and the only
writer of the June-era corpora; the **live-drive path** (teacher driving the MCP/API endpoints) is
where all real work happened. Until the journal, they didn't share a corpus. Numbers as of today:

| Corpus | Rows | Writer | Read by a trainer? |
|---|---|---|---|
| `intent_journal.jsonl` | **6** | `journaled` endpoints (live drives) | will feed L4 — the spine going forward |
| `loop_steps.jsonl` | 43 | `runtime/loop.py` only (mostly `run_batch`) | L4/state-transition (legacy framing) |
| `selection_telemetry.jsonl` | 101 | `select_stage` via the loop | L3/L4 (legacy framing) |
| `event_log.jsonl` | 416 (ring) | everything | **NO — operator wall display only, never a corpus** |
| Captures + AX sidecars + labels | 157+ captures, all with AX candidates | `/capture` | grounding + L3 |

**Nothing in the loop is a trained model yet, and no model has ever been trained-and-promoted from
live-work data.** Baselines: L3 v0 stage-observer **94%** held-out on 98 labels (2026-07-09);
grounding **0%** on 19 records (data-starved). The flywheel has never completed one revolution —
that is the project's single most important open item.

## Priorities (ordered — everything else queues behind these)

1. **Live-validate Phase 1** — finish KKR using only the new endpoints, zero `/eval` in model-made
   calls. This is Phase 1's DoD and it doubles as the start of #2 (the drive fills the journal).
2. **The first flywheel revolution** — drive → journal → label → train L3 v1 → shadow → promote →
   measure. The full plan with gates and metrics: `PLAN_flywheel_first_revolution.md`.
3. **Spine convergence** — one corpus spine (the journal), one action surface for teacher and loop
   alike. Decision + component dispositions: `DECISION_two-stacks-one-spine.md`. (The loop emitting
   intents is Phase 4 of `PLAN_interaction_api.md` — decided direction, not current work.)
4. **Interaction API Phase 2** — the intent surface (`/api/interact/*`, `{ats, field, value}`),
   `/resolve_answer` rungs + alias-table writeback.
5. **Parked** (do not resume until the wheel turns once, unless one blocks a drive): `main.py`
   split resumption (5,061 lines, 170 routes — it's growing again; the route-inventory guardrail
   still holds), movement playground / diffusion input model, OmniParser removal, Account Manager
   build-out (`PLAN_account_manager_and_l3.md` — its capture/label directive is *absorbed into* #2),
   `/scan_form` retirement (gated on a live diff vs `/scan_required`), FB Marketplace expansion.

## Endgame (recorded 2026-07-16 so every session aims the same direction)

Operator-stated: the inner system (L3/L4, and whatever inner layers come later) becomes strong
enough that **learned, cached scenarios — the recipes Claude taught — run without Claude at all**,
and generalize across similar scenarios. Claude remains the **teacher for novel work indefinitely**
— that door stays open by design. When the inner system gets stuck, or an intent does not land on
the state it expected (the verifier/outcome taxonomy is the trigger), it escalates up the ladder:
protocol retry → Haiku (bounded decisions) → Claude (teaching: discovery → endpoint + recipe +
labels) → human (stop-states, credentials, irreversibles — always). "Claude-free" is a
**per-scenario graduation**, never a global switch. The ladder is specified in
`DECISION_two-stacks-one-spine.md`.

## Short term vs long term

**Short term:** priorities 1–2 above — validate live, then spin the wheel on the existing 13-job
apply backlog (the crank is the work itself, not extra work).

**Long term:** L3 owns state recognition per ATS; L4 emits intents for learned scenarios (shadow →
gated promotion); the teacher's journaled drives keep expanding the recipe/protocol library; cost
per submitted application falls as scenarios graduate; the vision catchall earns its keep on
protocol discovery and AX-blind pages; continuous retraining on a cadence once the manual crank has
proven the loop.
