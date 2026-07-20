# PLAN — The Supervisor: a per-turn observer that names what went wrong

**Status: adopted 2026-07-20 (operator-directed) as priority #1.** Supersedes nothing — it
*rides on* the live drives Controller v1 already owed (M2 teacher-compile + replay), which is why
it does not compete with them for operator time. Career Search only, same as v1/v2.

**One sentence:** `decide()` answers *"what next?"* **before** an action; the **supervisor**
answers *"what just happened, and is it what we meant?"* **after** it — replacing today's boolean
`verified: true|false` with a named, cited diagnosis drawn from a closed failure taxonomy, and
selecting a recovery from a small playbook rather than reasoning open-endedly.

Its three products, in the order they pay off:
1. **Legibility** — a live commentary of *why*, on every turn, without the supervisor acting.
2. **Corpus** — every turn is a pre-labeled example; every operator override is a correction, free.
3. **Autonomy** — per-*failure-class* promotion: shadow → propose-approve → autonomous on the
   classes that have earned it. The long tail stays human, by design.

---

## 0. What the audit found (2026-07-20) — three corrections to the incoming design

**(a) The AX-tree diff does not exist.** The design assumes an always-on cheap sense that we
"already compute". We do not. What exists is:

| Surface | What it gives | Why it is not a diff |
|---|---|---|
| `fingerprint.compute` | one sha256 of the whole screen | **equality only** — says *different*, never *how* |
| `fingerprint.ax_summary` | sorted `role\|normalized-name` identities | the right *ingredients*, never differenced |
| `loop.progress_signature` | `(url, state, unanswered-fields)` | a 3-tuple; blind to a modal, an error banner, a disabled button |

So the supervisor's sense organ is **the first thing to build**, and it is small: a set
difference over `ax_summary`, reusing the same name-normalization so the diff and the fingerprint
can never disagree about what "the same control" means. Everything downstream is inert without it
— **S11 blocks S12–S16.**

**(b) Most of the staging machinery already exists — do not rebuild it.**

| Incoming ask | Already built | Verdict |
|---|---|---|
| "wrap the driver in an event bus" | `decision_journal.jsonl` (88 rows, written every controller step) + `intent_journal.jsonl` (223) | **Do not build a bus.** The append-only journal *is* the bus, and a second channel repeats the 2026-07-16 corpus reckoning (`event_log.jsonl` is a ring buffer no trainer reads). Supervision journals as **added optional columns on `DecisionRecord`**, which `decision.py:47` explicitly declares backwards-compatible. |
| shadow mode | `controller/shadow.py`, `controller/metrics.py`, `make controller-evals` | reuse verbatim, one altitude over |
| propose-and-approve | `controller/teach.py` (`ReviewAction`, golden rows carrying both halves) | reuse verbatim |
| staged handoff to autonomous | `docs/CONTROLLER_PROMOTION.md` | reuse — but gate **per failure class**, not globally |
| rationale + citations | `Decision.rationale` + `Decision.evidence`, `is_real_rationale` (PRINCIPLES §10) | the verdict inherits the same discipline |
| escalate on stuck | `controller/unexpected.py` (RE_OBSERVE \| ESCALATE \| CONTINUE) | **keep as the policy; the supervisor supplies its input.** `unexpected.respond` decides *what to do*; it has never known *what went wrong*. |

**(c) The taxonomy does not need to be guessed — it is already in the logs, and it is a power
law.** Mined 2026-07-20 from `decision_journal.jsonl` (88), `intent_journal.jsonl` (223),
`handoffs.jsonl` (34), and the ~30 hand-written incidents in `LEARNINGS.md`. Machine-readable
stuck moments: **9 `verified=False` decisions, 23 non-`ok` intent outcomes, 34 handoffs.**

> _Corrected 2026-07-20 (2): these first read 13 / 88 rows. The test suite had been writing into
> the live journal — 237 of 282 rows were fixture traffic on fake routes. The real corpus is
> **45 rows**. Plugged with a `conftest.py`; the classes below are unchanged, because they were
> drawn mostly from the hand-written LEARNINGS incidents, which no test could forge._

| # | Class | Evidence in our logs |
|---|---|---|
| 1 | `no_progress` — the action reported success and the page is byte-identical | 6 of 9 `verified=False` rows are `click → ok` landing back on `indeed_apply_questions`; live 2026-07-19 a blocked Continue was clicked **8×** scoring 100% verified (commit `c3d2904`) |
| 2 | `control_not_found` — the semantic reference did not resolve here | 7 `not_found` decisions + 9 `not_found` / 8 `not_opened` intents = **24**, the single largest bucket |
| 3 | `staged_not_committed` — the widget shows the value, `scan_required` still calls it unanswered | Ethnicity react-single-select (LEARNINGS 07-18); the distance pill (07-15); Greenhouse date month (07-15) |
| 4 | `race_settle` — we acted or classified before the page settled | Indeed location combobox `clear`+`type` → `Manchester, NHu` (07-18); `_current_state()` classified the *old* state after a Continue (07-18) |
| 5 | `stale_tab` — the CDP target is gone; the scan returns a **successful empty result** | 07-19, the whole unexpected-state pass |
| 6 | `unrecognized_state` — the page is not in the registry | 6 handoffs |
| 7 | `auth_wall` — signed out / session expired mid-drive | 12 handoffs (`not_authenticated`); "the SESSION is the real enemy" (07-15) |
| 8 | `missed_required_control` — Continue is blocked by a required control the scanner never saw | the lone required acknowledgment checkbox (07-18) |

Eight classes account for **every** machine-readable stuck moment we have and every incident in
`LEARNINGS.md` since 07-12. That is the power law, confirmed with numbers rather than predicted —
and it is why the supervisor's core skill is *classification + playbook selection*, not
open-ended reasoning.

Notably absent from our data (present in generic web-agent taxonomies): unexpected
modals/interstitials, layout drift breaking selectors. The first is plausible-but-unobserved; the
second **cannot** happen here because we address by role + accessible name, not selectors. Do not
seed the taxonomy with either — extend it the way `Outcome` members were earned.

---

## 1. Contracts (`packages/interaction/interaction/delta.py`, `supervision.py`)

Frozen dataclasses, versioned like `contract.py` and `decision.py`.

```python
# --- delta.py: the always-on cheap sense -----------------------------------------
@dataclass(frozen=True)
class StateDelta:
    """What changed between two consecutive observations. Pure, cheap, no model."""
    appeared:    tuple[str, ...]   # role|normalized-name identities now present
    disappeared: tuple[str, ...]   # …now absent
    route_changed: bool
    state_changed: bool
    unanswered_delta: int          # signed change in the unanswered required-field count
    moved: bool                    # any of the above — the treadmill predicate
    # NB: names come from fingerprint._normalize_ax_name so the diff and the fingerprint
    # cannot disagree about what "the same control" is.
```

```python
# --- supervision.py: the verdict ------------------------------------------------
SUPERVISION_SCHEMA_VERSION = "v1"

class FailureClass(str, Enum):        # closed; extend the way Outcome was extended, never rename
    NONE = "none"                     # nominal — the action did what it meant to
    NO_PROGRESS = "no_progress"
    CONTROL_NOT_FOUND = "control_not_found"
    STAGED_NOT_COMMITTED = "staged_not_committed"
    RACE_SETTLE = "race_settle"
    STALE_TAB = "stale_tab"
    UNRECOGNIZED_STATE = "unrecognized_state"
    AUTH_WALL = "auth_wall"
    MISSED_REQUIRED_CONTROL = "missed_required_control"
    UNKNOWN = "unknown"               # the honest bucket — never a guess dressed as a class

class RecoveryPlay(str, Enum):        # the playbook — a CLOSED vocabulary, not prose
    NONE = "none"
    RE_OBSERVE = "re_observe"
    SETTLE_AND_RETRY = "settle_and_retry"
    RE_RESOLVE_TAB = "re_resolve_tab"
    COMMIT_WIDGET = "commit_widget"       # run the stage→commit protocol on the staged control
    RESCAN_REQUIRED = "rescan_required"
    ESCALATE = "escalate"

@dataclass(frozen=True)
class SupervisorVerdict:
    state_hypothesis: str          # one sentence: where we believe we are
    expectation_delta: str         # one sentence: expected X, observed Y
    stuck_signal: float            # [0,1]
    failure_class: str             # a FailureClass value
    diagnostic_request: str        # "none" | "screenshot"
    proposed_recovery: str         # a RecoveryPlay value — NOT prose (invariant #10)
    recovery_params: dict          # semantic refs only; selector-guarded like Decision.params
    rationale: str                 # is_real_rationale() applies (§10)
    evidence: tuple[str, ...]      # Bundle/StateDelta keys cited — the receipts
    confidence: float              # [0,1]
    rung: str                      # deterministic | model | teacher
```

**Two contract rules, both inherited rather than invented.** `proposed_recovery` is a
`RecoveryPlay` member, never the free text the incoming design sketched (`"dismiss modal via
close button"`) — a policy that emits prose has learned the wrong altitude, and the *whole
insight* of this plan is that recovery is playbook **selection**. And `recovery_params` passes
through `looks_like_selector` exactly like `Decision.params`.

---

## 2. The supervision cascade — legibility on every turn, spend on almost none

The incoming design puts a Sonnet-class call on every turn. At ~1k in / 200 out that is
~$0.005/turn → **~$0.20 per 40-step drive → over the $5/week cap at five drives a day.** It is
also unnecessary: most turns are not ambiguous. Same principle as everywhere else — cheapest
confident tool:

- **Rung 0 `deterministic` ($0, every turn).** `(StateDelta, Outcome, verified, expected_next)` →
  a verdict, by table. `outcome=not_found` → `CONTROL_NOT_FOUND`. `verified and delta.moved` →
  `NONE`. `verified and not delta.moved` → `NO_PROGRESS` (this is the 07-19 treadmill, finally
  *named* instead of merely counted). Empty scan + `ok` → `STALE_TAB`. This rung still emits a
  **full, readable verdict** — legibility never required a model, and it will cover the large
  majority of turns.
- **Rung 1 `model` (Haiku, behind `/api/controller/supervise` — invariant #6).** Only when rung 0
  lands on `UNKNOWN`, or `stuck_signal` is high, or the class repeats. ~$0.0018/turn.
- **Rung 2 `teacher` (Claude).** Repeated `UNKNOWN`, or a class with no playbook entry. This is
  where the taxonomy grows — a new class is *earned* by a teacher naming it twice.

**Vision stays gated, exactly as argued.** `diagnostic_request: "screenshot"` may only be emitted
by rung 1+; the loop then calls the existing MCP `/screenshot` and attaches it to the escalating
call. Rung 0 may never request one. Vision is a diagnostic instrument the reasoner reaches for,
never a firehose.

### Amendments earned in the build (2026-07-20)

Recorded rather than silently absorbed, per the extend-don't-rename discipline:

- **A tenth class, `CHALLENGE`.** Not in the mined eight, because a captcha escalates at classify
  and so never produces a `verified=False` row — but `Outcome.BLOCKED` is real and verified live
  on reCAPTCHA, and forcing the loop's loudest stop into `UNKNOWN` would make the commentary go
  silent exactly where the operator needs it. Checked FIRST, before any mechanical class, so a
  challenge page's missing controls are never filed as `control_not_found`.
- **Four classes come straight off the `Outcome` taxonomy instead of being inferred.**
  `not_staged` / `not_committed` ARE `STAGED_NOT_COMMITTED` — and the endpoint knows *which half*
  broke, which no inference does. `no_option` folds into `CONTROL_NOT_FOUND` (the control
  resolved, its option vocabulary did not — same miss, one level in). `committed_unconfirmed` maps
  to `UNKNOWN` with its own rationale: it is honest uncertainty, and precisely the case where
  rung 1 spending a screenshot will earn its keep.
- **`MISSED_REQUIRED_CONTROL` outranks `NO_PROGRESS`** when the form scans complete. Discovered by
  a test: the Longroad treadmill has `unanswered == 0`, so the sharper class fires and the play is
  `RESCAN_REQUIRED` rather than a blind retry. Generic `no_progress` is what remains when no
  sharper explanation is available.

## 3. Where it runs

Inside `run_controller`, immediately after `actuator.act()`, at the seam where `_verify()`'s
boolean currently is. `_verify` **stays** — it is rung 0's cheapest input, not a competitor.

**The timing correction (found in the build).** A verdict needs the delta the action *caused*, and
the loop's between-observations delta at the top of the iteration describes the PREVIOUS action —
so a verdict built from it would always arrive one turn late and could never be journaled on the
row of the action it judges. Fixed by having `Actuator.act()` report the after-picture at the
moment of acting (`ActOutcome.ax_identities` + `unanswered_after`, from one extra read-only CDP
eval each); `loop.action_effect` differences that against the Bundle we decided on. Cause and
effect land on the same record, which is what makes the verdict a training label rather than a
note. `observation_delta` stays as the independent second net for the treadmill guard.

**`unanswered_after` is not optional.** A react-select that stages *does* change the AX tree, so
control churn alone reads a staged-but-uncommitted value as success. The form's own answer to "is
this field filled?" is the only signal that disagrees — and it is the one that is right (the
Ethnicity select, LEARNINGS 2026-07-18).

```
observe → decide → [gate] → act → SUPERVISE → unexpected.respond → journal → next
```

Three wiring rules:

1. **`unexpected.respond` keeps the final say.** The supervisor supplies the diagnosis; the
   existing policy converts it to RE_OBSERVE | ESCALATE | CONTINUE. One place decides "not where
   we assumed", still.
2. **The verdict feeds the *next* `decide()`**, as a compact line in `Bundle.recent[-1]`. This is
   the cheap alternative to a second reasoning stack: the controller gets its own post-mortem for
   free, one turn later.
3. **In shadow mode the verdict influences nothing.** It journals and it renders. That is the
   whole of stage 1.

**The treadmill guard gets rebuilt on the delta.** `progress_signature`'s 3-tuple becomes
`StateDelta.moved`, so `NO_PROGRESS` fires on a page that is truly unchanged rather than one whose
url/state/unanswered-set happen to match — strictly more sensitive, and it stops being a mystery
counter and starts being a named class with a playbook entry.

## 4. Postconditions: `expected_next` is one, and it is state-shaped

`expected_next` **already is** the postcondition, inherited from recipe `expect` edges
(`reason.py:145` — a model may narrow it, never erase it). Its gap is expressivity: it can say
"we should be on `indeed_apply_review`", never "the result grid is present and no overlay is".

So a postcondition v1 = `expected_next` **+** `expect_present` / `expect_absent`: tuples of
`role|name` identities checked against `StateDelta`. Same normalization, no new machinery.

**Retrofit exactly three states, chosen from the data, not taste** — the three that dominate the
decision journal: `indeed_apply_questions` (36 rows), `workday_sign_in` (37), and
`indeed_apply_resume_selection`. Do not boil the ocean; the taxonomy will say where assertions pay.

## 5. Sessions (briefs in `docs/sessions/`)

| # | Session | Gated on | Live? |
|---|---|---|---|
| S11 | ✅ **done 2026-07-20** — `interaction/delta.py` (`StateDelta`, `identities_from_ax`, `delta_to_prompt`), `Bundle.ax_identities`, the treadmill guard rebuilt on the delta as `loop.observation_delta`. 25 tests. Built inline, so it has no brief; LEARNINGS 2026-07-20 is its record. | — | no |
| S12 | ✅ **done 2026-07-20** — `interaction/supervision.py` (`FailureClass`, `RecoveryPlay`, `SupervisorVerdict`, rung-0 `classify`), `supervisor_*` + `delta_*` columns on `DecisionRecord`, the `on_supervise` seam in `run_controller`, and `LiveActuator` perception (AX scan, page text, post-action look). 74 tests. | S11 | no |
| **S12b** | **THE PLAY EXECUTOR — `RecoveryPlay` -> an actual action.** A gap in this plan's first draft, found by building it: S12 gives the supervisor a *name* and a *prescription*, and nothing that can fill the prescription. This is the critical path to any authority at all; S13–S15 are refinements that do not unblock it. Four plays need an executor and every one has existing machinery to reuse — `RE_RESOLVE_TAB` -> `tab_finder.resolve_target` (already the login drive's `re_resolve` seam), `SETTLE_AND_RETRY` -> `_current_state`'s settle loop, `RESCAN_REQUIRED` -> `/scan_required` vs `/scan_form`, `COMMIT_WIDGET` -> the tier-2 select protocol. `RE_OBSERVE` and `ESCALATE` need nothing: they are what `unexpected.respond` already does, which is why they are the two whose agreement can be measured from replay before any live drive. | S12 | no |
| S13 | Rung 1 (Haiku behind the HTTP seam) + the gated screenshot path | S12 | no |
| S14 | The commentary pane (Activity console — reasoning is already a source there) | S12 | no |
| S15 | Postconditions on the three states | S12 | no |
| S16 | **Shadow drives** → first agreement numbers → per-class promotion gate | S13, S15 + an operator-present drive | **yes** |

S11–S15 are all offline. The only operator-present work is S16, and it is the drive Controller v1
already owed — the supervisor rides along rather than asking for a separate session.

## 6. Promotion — per class, never global

Reuses `CONTROLLER_PROMOTION.md`'s discipline at supervision altitude. A failure class graduates
independently:

- **Stage 1 — shadow.** The supervisor runs, journals, renders. Influences nothing. Exit gate:
  ≥ 70% agreement between `proposed_recovery` and what the human driver actually did, measured
  **per class**, over ≥ 20 instances of that class.
- **Stage 2 — propose-and-approve.** Through the existing `Reviewer` seam; a correction writes a
  golden row carrying both halves (`proposed_*` columns already exist). Exit gate: ≥ 90%
  approval-without-correction over ≥ 20 instances.
- **Stage 3 — autonomous, that class only.** The playbook entry fires without asking. Every other
  class stays at its own stage. `UNKNOWN` never graduates.

## 7. Deliberately NOT in v1

- **No event bus.** The journal is the spine (§0b).
- **No second reasoning stack.** The supervisor is a contract + a cascade inside the existing loop.
- **No trained supervisor** until the prompt ceilings — same rule as the planner (`PLAN_reasoner_v2.md` §7).
- **No open-ended recovery.** If the right recovery is not in `RecoveryPlay`, that is an escalation
  and a candidate playbook entry, not an improvisation.
- **No new domain, no FB port, no `runtime/loop.py` rewrite.**
- **No always-on vision.**

## 8. Falsifying conditions (architecture.md discipline)

- **Rung-0 coverage below ~60% of turns** → the deterministic table is too timid, or `StateDelta`
  is too coarse to separate the classes. Fix the delta before spending on a model.
- **`UNKNOWN` share not falling as drives accumulate** → the taxonomy is wrong, not incomplete;
  re-mine the logs rather than adding classes by intuition.
- **Per-class agreement flat while instances accumulate** → the playbook entry is mis-specified
  for that class; suspect `RecoveryPlay` granularity before the model.
- **Verdicts systematically cite nothing** (`evidence` empty) → the serialization isn't legible;
  fix it before adding any input, same as v2's rule for `pack_to_prompt`.
- **`no_progress` incidence not falling once `SETTLE_AND_RETRY` / `COMMIT_WIDGET` are autonomous**
  → we named the symptom, not the cause; classes 1, 3 and 4 are probably one class wearing three
  hats.

---

*One-line summary: build the cheap always-on sense we assumed we had (the AX delta), turn today's
boolean `verified` into a named verdict from a taxonomy mined from our own logs, make recovery a
selection from a closed playbook rather than prose, spend a model only when the deterministic
table can't name the class, keep vision as a requested instrument — and graduate one failure class
at a time.*
