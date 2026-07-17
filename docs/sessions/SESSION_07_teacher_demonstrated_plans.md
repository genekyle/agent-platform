# Session 07 — teacher-demonstrated plans, graded live (Reasoner v2)

**Gate: controller Session 02 complete** (journal flowing, Indeed programs exist, loop harness
live). **Read first:** `CLAUDE.md`, `docs/PLAN_reasoner_v2.md` (§2, §3 loops 1–2, §5),
`docs/sessions/SESSION_06` output + its LEARNINGS seam list, `controller/loop.py` as it now
exists (respect the v1 sessions' ownership — coordinate via LEARNINGS if it must change).

## Objective

Put the first real `PlanRecord`s in the journal with YOU as the planning rung. You (Claude, the
teacher) author plans through the frozen contract before driving; the v1 loop executes each hop;
the grader grades every step against reality; every divergence gets a structured post-mortem.
At the end, plan-altitude teaching data exists and loops 1–2 are demonstrably closed on a real
Career Search drive.

## Scope — in

1. **Wire the seam** (minimal, additive): the loop harness accepts an optional `plan` and reports
   per-leg results to `plan_grader`; a graded `PlanRecord` is journaled per plan. If Session 06's
   LEARNINGS listed missing fields in the loop's leg records, add them additively (new keys, no
   renames).
2. **Teacher planning protocol.** Before driving each task: assemble the tier-0 `ContextPack`,
   author a `Plan` (3–5 hops, state granularity, rationale + evidence keys on every step,
   `means="exploration"` wherever the validator can't ground a hop), run `plan_validate`, journal,
   then execute via the loop. Replan-from-landed-state on any failed verdict — never resume a
   voided plan.
3. **Post-mortems.** Every `ok=False` verdict gets ONE `PostMortem` (taxonomy per §3 loop 2),
   journaled AND appended to the state-family's lessons corpus. This session defines where that
   corpus lives concretely (extend the recipe module's LESSONS pattern mechanically — keyed by
   `(task, state_family)`), so Session 08's tier-1 context can retrieve it.
4. **Live drives.** Two backlog applications, different ATS (one Indeed — mostly grounded hops,
   programs carry the legs; one Workday or Greenhouse — expect exploration hops and real
   divergences; that contrast is the point). Final Submits held for the operator.
5. **First numbers.** From the plan journal: hops grounded vs exploration, verdict ok-rate,
   divergence tags histogram, citation rate (evidence keys per rationale, and which tier-0 items
   were never cited). Paste into the session log — these are v2's baseline metrics.

## Scope — out

Model planner rung, plan caching, propose-approve at plan level, critic, ablation, context tiers
beyond 0.

## Definition of done

- ≥2 live drives fully planned-then-executed through the contract; every plan validated,
  journaled, graded.
- ≥1 real divergence with its post-mortem retrievable by `(task, state_family)` key.
- Baseline metrics pasted; LEARNINGS.md appended (expect: which tier-0 items you actually cited
  while planning — the first entry in the citation ledger, and the seed of §3 loop 5).

## Non-negotiables

- No private path: every plan you author goes through the contract/validator/journal even when
  you're confident — an unjournaled teacher plan is the event-log mistake at plan altitude.
- `make data-check` first; never auto-solve captcha/2FA; consequential gate on submits; Account
  Manager boundary holds.
- Stage explicit paths; `git status` before commit; concurrent session active — own every staged
  path. Commit to `main`.
