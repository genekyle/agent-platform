# Session 01 — the Decision contract + the Bundle builder (Controller M1)

**Read first, in order:** `CLAUDE.md`, `docs/PLAN_controller_v1.md` (§1 is this session's spec),
`packages/interaction/interaction/contract.py`, `packages/interaction/interaction/journal.py`,
`apps/controlplane-api/apply_recipe.py` (`describe_tab`, `recipe_spec`),
`apps/controlplane-api/task_spec.py`, `apps/controlplane-api/resolve_answer.py`
(`SemanticQuestion` — the pattern Bundle copies), `docs/LEARNINGS.md` (recent entries).

## Objective

Freeze the controller's I/O contract and build its input. At the end of this session the repo has a
versioned `Bundle`/`Decision`/`DecisionRecord` vocabulary and a `build_bundle()` that composes the
existing observation surfaces into one frozen object, live-verified against an open Career Search
tab. No decisions are made this session; we are building the eyes and the sentence they speak in.

## Scope — in

1. **`packages/interaction/interaction/decision.py`** — `DECISION_SCHEMA_VERSION = "v1"`;
   frozen dataclasses `Bundle`, `Decision`, `DecisionRecord` exactly as specified in
   `PLAN_controller_v1.md` §1. Include `bundle_to_prompt(bundle) -> str` — the STABLE serialization
   that is the model prompt surface today and the L4 feature set tomorrow (same dual-use trick as
   `SemanticQuestion`; treat its format as part of the frozen contract).
2. **`packages/interaction/interaction/decision_journal.py`** (or extend `journal.py` — your call,
   but same rules): `log_decision(record)` — append-only `cache/decision_journal.jsonl`,
   best-effort, never raises into the hot path, **no row without a fingerprint**, values redacted
   with the existing `redact()`.
3. **`apps/controlplane-api/controller/`** — new package. `bundle.py::build_bundle(task, url,
   page_text, *, scan=None, journal_tail=None) -> Bundle`, composing:
   `task_spec.spec_for` + `is_complete` (goal half); the owning recipe module's
   `map_url_to_state`/`describe_tab` (state + recipe half — dispatch by domain, Career Search ATS
   modules only for now); `scan_required` output passed in verbatim (form half — the caller fetches
   it via the MCP endpoint; keep `build_bundle` pure/no-I/O so it is replayable from journaled
   inputs); last-5 decision-journal rows (history half); `route_template` for the fingerprint.
4. **Tests** — `packages/interaction/tests/test_decision.py` (shape, version, serialization
   stability — snapshot the prompt format), `apps/controlplane-api/test_controller_bundle.py`
   (offline fixtures for each Career Search ATS: Indeed, Workday, Greenhouse; branch state sets
   `human_required`; done state sets `done`).
5. **Live smoke** — with the operator's tab open on any Career Search page, build a Bundle
   read-only and print it. Read-only CDP against an open tab is a local socket — free, allowed in
   low-data mode.

## Scope — out (do not touch)

`decide()`, the loop, programs, any live *driving*, `runtime/loop.py`, any non-Career-Search
domain. No new endpoints.

## Definition of done

- 119+ existing tests still green; new tests green.
- A real Bundle from a live tab pasted into the session log (values redacted).
- `decision_journal.jsonl` round-trips one synthetic record with a fingerprint.
- LEARNINGS.md appended: anything the Bundle needed that the existing surfaces didn't cleanly give
  (those gaps are the first Bundle-shape suspects per PLAN §6).

## Non-negotiables (repo rules — repeated because sessions have clobbered them)

- Stage explicit paths only; `git status` before commit; you own every staged path. Commit to `main`.
- No selectors/JS/backend_node_id anywhere in `Bundle` or `Decision` (invariant #10).
- Secrets/PII never in committed files or journal values (`redact()` everywhere a value lands).
- If the operator is on limited data: everything here is read-only/local — proceed, but no page
  loads beyond the already-open tab (`make data-check` first).
