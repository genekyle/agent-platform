"""Session state store — the blackboard + invariant gates (Layers 2 & 3).

Session-spanning: one blackboard per training session tracks the WHOLE flow — the SEARCH
phase (which query/location, which page, what's been observed/shortlisted) and the APPLY
phase (the per-field form gates) — so the "where are we / what's left / is there a captcha"
memory lives written-down in the store, not in a Claude/Haiku context that resets between
sessions. `phase` flips deterministically off the active tab; the plan swaps with it
(search spine vs apply spine). Captcha/human blockers are computed every reconcile and apply
in EVERY phase, so a challenge during search halts proceed exactly like one at submit.

This is the piece that kills the "making it up as I go / forgetting the empty field"
problem, and it is deliberately NOT a model. It's a plain, serializable data structure
(the blackboard) plus deterministic code that keeps it honest (the gates).

The split that matters:

  * The STORE (this module's `Blackboard`) is just data: the goal, the plan (ordered
    subtasks with status), the current subtask, the world (tabs / active tab / page
    state), the form_state (per-field required/filled/valid), the blockers, the last
    action + result, and an append-only event log. Nothing here "remembers" by being
    smart — it remembers by being written down.

  * The GATES (`form_complete_gate`, `blockers_for`) convert "things the model must
    remember" into "checks the code enforces". A subtask cannot advance to `done`
    while any required field is empty or invalid, because the gate is code, not memory.
    That is what lets a cheap model (or a human under supervision) drive the loop
    without silently skipping a hidden required field — e.g. the second `State` select
    that the single-field assumption used to miss.

Reconciliation (`reconcile`) is deterministic on purpose: world is overwritten from
the observation (ground truth), form_state is recomputed, blockers are recomputed, and
the current subtask only advances when its gate passes. A learned reconciliation model
is explicitly NOT built here — when the observation is ground truth, reconciliation is
code. The decoder is only worth it for the ambiguous residue, and you can't measure
that residue until this explicit representation exists. The clean (state, action,
next_state) rows this store produces are exactly that training substrate.

States are `page_state_registry` ids and the plan spine comes straight from
`apply_recipe.INDEED_APPLY_RECIPE`, so the store is teachable by the same loop as
everything else; it does not invent a parallel state vocabulary.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import apply_recipe

SCHEMA_VERSION = 1
_MAX_EVENTS = 200  # keep the log bounded; it's a rolling window, not an archive

#: What `apply_recipe.map_url_to_state` returns when no recipe pattern matched — i.e. "we looked
#: and did not recognise this page". Distinct from None ("nothing observed"), which must not gate.
UNKNOWN_STATE = "unknown"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Layer 1 data: per-field form state -------------------------------------------
# `kind` is the element family the scanner saw (radio/select/text/checkbox/combobox).
# `required` / `filled` / `valid` are read from the page deterministically (aria-required,
# value-empty, aria-invalid / :invalid) — no model. `valid` defaults to "not invalid".
@dataclass
class FieldState:
    field_id: str
    label: str
    kind: str = "unknown"
    required: bool = False
    filled: bool = False
    valid: bool = True
    value_preview: str = ""

    @property
    def satisfied(self) -> bool:
        """A field blocks completion only if it's required and not (filled and valid)."""
        return (not self.required) or (self.filled and self.valid)


def build_form_state(fields: list[dict[str, Any]]) -> list[FieldState]:
    """Deterministic form scanner reducer: raw field descriptors -> FieldState list.

    Each descriptor is whatever the live scanner reports for one field:
        {field_id, label, kind, required, filled, valid?, value_preview?}
    `valid` is optional and defaults to True unless the page flagged the field invalid
    (aria-invalid / :invalid). This is pure so it is unit-testable without a browser."""
    out: list[FieldState] = []
    for i, f in enumerate(fields or []):
        out.append(FieldState(
            field_id=str(f.get("field_id") or f.get("id") or f"field_{i}"),
            label=str(f.get("label") or "")[:120],
            kind=str(f.get("kind") or "unknown"),
            required=bool(f.get("required")),
            filled=bool(f.get("filled")),
            valid=bool(f.get("valid", True)),
            value_preview=str(f.get("value_preview") or "")[:40],
        ))
    return out


# --- Layer 3: invariant gates ------------------------------------------------------
@dataclass
class GateResult:
    ok: bool
    satisfied: list[str]      # field_ids that pass
    unsatisfied: list[dict]   # [{field_id, label, reason}] that block completion

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "satisfied": self.satisfied, "unsatisfied": self.unsatisfied}


def form_complete_gate(form_state: list[FieldState]) -> GateResult:
    """The core invariant: a form subtask cannot be `done` until EVERY required field is
    filled and valid. The model is structurally unable to forget an empty required field
    because this check is code, not recall."""
    satisfied: list[str] = []
    unsatisfied: list[dict] = []
    for fs in form_state:
        if fs.satisfied:
            if fs.required:
                satisfied.append(fs.field_id)
            continue
        reason = "empty" if not fs.filled else "invalid"
        unsatisfied.append({"field_id": fs.field_id, "label": fs.label, "reason": reason})
    return GateResult(ok=not unsatisfied, satisfied=satisfied, unsatisfied=unsatisfied)


# --- Layer 2 data: the blackboard --------------------------------------------------
@dataclass
class Subtask:
    id: str            # recipe state id (page_state_registry indeed_apply_*)
    label: str
    step: int
    status: str = "pending"   # pending | active | blocked | done
    gate: Optional[str] = None  # "form_complete" if a code gate guards its completion


@dataclass
class Blocker:
    kind: str             # "human_branch" | "required_field" | ...
    note: str
    human_required: bool
    source: str           # where it came from (e.g. "captcha", field_id)


@dataclass
class Event:
    """One line of the session's timeline: WHAT happened, WHY, and WHAT COMES NEXT.

    The first two fields were the whole event for a long time, and the timeline they built could
    only ever be read forwards by someone who already knew the story. Operator, asking for the
    step-back that motivated this (2026-08-13): *"the journal needs to know why and what's going to
    happen next so make sure our system doesn't get confused."*

    That is two distinct gaps, and `detail` was silently carrying neither:

      * **why** — the REASON a state changed, as opposed to the change itself. "search 2:
        'report analyst' -> 'data analyst'" says what moved; it does not say the operator wanted a
        fresh set of candidates, which is the only thing that makes the next four events legible.
      * **next_up** — the DECLARED consequence. Between two states there is an intention, and a
        journal that records only arrivals leaves every gap to be re-inferred. Writing down what we
        expect to happen next is also what makes the record falsifiable: the following event either
        matches it or does not, and a mismatch is a finding rather than a shrug.

    Both default to empty and both are additive, so every event already on a blackboard reads back
    unchanged (`Event(**e)` simply supplies the defaults).
    """

    ts: str
    kind: str
    detail: str
    why: str = ""
    next_up: str = ""


# --- Search-phase memory: what we're searching + how far we've gotten --------------
# The search analogue of form_state: the "written down" facts the search/triage phase would
# otherwise hold in a model's head — the active query/location, which results page we're on,
# how many jobs we've seen, and the shortlist/approved/in-flight job refs.
#
# PROVENANCE INVARIANT (context-bound validity): a blackboard value is not self-validating — it
# only means something relative to the CONTEXT it was produced in. `approved`/`shortlist` are valid
# to act on ONLY if they were gathered in the CURRENT cadence run AND while authenticated. The same
# session is not the same cadence run, and data gathered logged-out is provenance-invalid for an
# apply decision. So a run id + auth-at-gather + a `stale` flag travel WITH the data; see
# `start_cadence_run` and `search_data_actionable`. This stops "looks valid but isn't" thought-bubbles.
@dataclass
class SearchState:
    query: str = ""
    location: str = ""
    page: int = 1
    observed_count: int = 0
    shortlist: list[str] = field(default_factory=list)   # job refs the operator shortlisted
    approved: list[str] = field(default_factory=list)    # refs approved to apply to
    current_job: Optional[str] = None                    # the ref currently in the apply flow
    # provenance — what context produced this data (without it the data isn't actionable):
    cadence_run_id: str = ""                              # which search-cadence run gathered it
    run_started_at: str = ""
    gathered_authenticated: bool = False                 # was the session logged in when gathered?
    stale: bool = False                                  # provenance invalidated (e.g. auth flipped)


# Phases. search→triage share the search plan (triage is just "on a posting, deciding"); apply
# is the form spine. `phase` is a label off the active tab; `_plan_family` is what plan to use.
PHASES = ("search", "triage", "apply")
_APPLY_STATE_HINTS = ("interview_review", "post_submit_feedback", "captcha")


def _phase_for(role: Optional[str], state: Optional[str]) -> str:
    """Deterministic phase off the active tab — no model. Apply tab/state → apply; a job
    posting → triage (the handoff point); anything else (home/results) → search."""
    s = state or ""
    if role == "apply" or "apply" in s or s in _APPLY_STATE_HINTS:
        return "apply"
    if s == "indeed_job_posting":
        return "triage"
    return "search"


def _plan_family(phase: str) -> str:
    """Which plan spine a phase uses: apply has its own; search+triage share the search spine."""
    return "apply" if phase == "apply" else "search"


@dataclass
class Blackboard:
    session_id: int
    goal: str
    schema_version: int = SCHEMA_VERSION
    phase: str = "search"
    plan: list[Subtask] = field(default_factory=list)
    current_subtask_id: Optional[str] = None
    search_state: SearchState = field(default_factory=SearchState)
    # The checkpoint ledger (session_checkpoints.Ledger, serialized): which rungs of the
    # open-ended ladder this session has reached. Lives here because a checkpoint is exactly the
    # kind of "written down, not re-derived" fact the blackboard exists for — and because a rung
    # that cost a real Indeed query must survive a process restart, or we'd pay for it twice.
    checkpoints: dict[str, Any] = field(default_factory=dict)
    world: dict[str, Any] = field(default_factory=dict)   # tabs, active_tab_index, page_state, role
    form_state: list[FieldState] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    last_action: Optional[str] = None
    last_result: Optional[str] = None
    events: list[Event] = field(default_factory=list)
    updated_at: str = field(default_factory=_utcnow)

    # -- serialization ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "schema_version": self.schema_version,
            "phase": self.phase,
            "plan": [asdict(s) for s in self.plan],
            "current_subtask_id": self.current_subtask_id,
            "search_state": asdict(self.search_state),
            "checkpoints": self.checkpoints,
            "world": self.world,
            "form_state": [asdict(f) for f in self.form_state],
            "blockers": [asdict(b) for b in self.blockers],
            "last_action": self.last_action,
            "last_result": self.last_result,
            "events": [asdict(e) for e in self.events],
            "updated_at": self.updated_at,
            # convenience views (derived, not source of truth) for the readout/UI:
            "needs_human": any(b.human_required for b in self.blockers),
            "gate_ok": not any(b.kind == "required_field" for b in self.blockers),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Blackboard":
        bb = cls(session_id=int(d["session_id"]), goal=d.get("goal", ""))
        bb.schema_version = d.get("schema_version", SCHEMA_VERSION)
        bb.phase = d.get("phase", "search")
        bb.plan = [Subtask(**s) for s in d.get("plan", [])]
        bb.current_subtask_id = d.get("current_subtask_id")
        bb.search_state = SearchState(**d.get("search_state", {}))
        bb.checkpoints = d.get("checkpoints", {}) or {}
        bb.world = d.get("world", {})
        bb.form_state = [FieldState(**f) for f in d.get("form_state", [])]
        bb.blockers = [Blocker(**b) for b in d.get("blockers", [])]
        bb.last_action = d.get("last_action")
        bb.last_result = d.get("last_result")
        bb.events = [Event(**e) for e in d.get("events", [])]
        bb.updated_at = d.get("updated_at", _utcnow())
        return bb

    def log(self, kind: str, detail: str, *, why: str = "", next_up: str = "") -> None:
        """Append to the session's timeline. `why` and `next_up` are keyword-only and optional —
        an event that genuinely has no reason beyond itself ("2 tabs closed") must not be made to
        invent one, and a fabricated rationale is worse than a missing one (PRINCIPLES §10)."""
        self.events.append(Event(ts=_utcnow(), kind=kind, detail=detail,
                                 why=why, next_up=next_up))
        if len(self.events) > _MAX_EVENTS:
            self.events = self.events[-_MAX_EVENTS:]


def default_plan() -> list[Subtask]:
    """The apply plan spine, instantiated from the recipe. Form-bearing steps get the
    `form_complete` gate so their completion is code-guarded; navigation steps don't."""
    form_states = {
        "indeed_apply_questions", "indeed_apply_contact_info", "indeed_apply_demographics",
    }
    plan: list[Subtask] = []
    for entry in apply_recipe.INDEED_APPLY_RECIPE:
        state = entry["state"]
        plan.append(Subtask(
            id=state,
            label=state.replace("indeed_apply_", "").replace("_", " "),
            step=entry["step"],
            gate="form_complete" if state in form_states else None,
        ))
    return plan


def search_plan() -> list[Subtask]:
    """The search/triage spine, instantiated from search_cadence.SEARCH_RECIPE. No form gates —
    search has no required-field invariants; its blockers are bot-safety + captchas."""
    import search_cadence
    plan: list[Subtask] = []
    for entry in search_cadence.SEARCH_RECIPE:
        state = entry["state"]
        plan.append(Subtask(
            id=state,
            label=state.replace("indeed_", "").replace("_", " "),
            step=entry["step"],
            gate=None,
        ))
    return plan


def _plan_for_family(family: str) -> list[Subtask]:
    return default_plan() if family == "apply" else search_plan()


def new_blackboard(session_id: int, goal: Optional[str] = None,
                   query: str = "", location: str = "") -> Blackboard:
    """A fresh session blackboard. Starts in the SEARCH phase with the search spine; the active
    query/location are written into search_state up front (seeded from the active job-search
    target) so the search phase has its target without re-deriving it each step."""
    if goal is None:
        goal = (f"Search Indeed for '{query}'" + (f" in {location}" if location else "")
                + " then triage → apply") if query else "Apply to the open Indeed posting"
    bb = Blackboard(session_id=session_id, goal=goal, phase="search", plan=search_plan(),
                    search_state=SearchState(query=query, location=location))
    bb.log("init", f"blackboard created for session {session_id} (query={query!r}, location={location!r})")
    return bb


def start_cadence_run(bb: Blackboard, *, query: str = "", location: str = "",
                      authed: bool = False) -> Blackboard:
    """Open a NEW search-cadence run and stamp its provenance onto search_state. A run is the
    context that makes gathered data actionable: it resets observed/shortlist/approved (last run's
    findings don't carry into this one) and records WHEN it started and WHETHER the session was
    authenticated. Triage/approve are only valid within a current, authenticated run — see
    `search_data_actionable`. This is what stops a logged-out / prior-run shortlist from being
    treated as a valid apply decision."""
    import uuid
    ss = bb.search_state
    ss.cadence_run_id = uuid.uuid4().hex[:12]
    ss.run_started_at = _utcnow()
    if query:
        ss.query = query
    if location:
        ss.location = location
    ss.page = 1
    ss.observed_count = 0
    ss.shortlist = []
    ss.approved = []
    ss.current_job = None
    ss.gathered_authenticated = bool(authed)
    ss.stale = False
    bb.log("cadence_run_start",
           f"run {ss.cadence_run_id} query={ss.query!r} location={ss.location!r} authed={authed}")

    # Opening a cadence run IS spending the query — it is the sweep's version of the control
    # panel's `query_entered` rung. Record it on the checkpoint ledger too, or the two paths keep
    # separate memories of the same unrepeatable act and the panel offers to search a session that
    # already has (found live 2026-07-23 on session 16). One spend, one record.
    import session_checkpoints as cps
    ledger = cps.Ledger.from_dict(bb.checkpoints)
    ledger.mark("query_entered",
                evidence=f"cadence run {ss.cadence_run_id} for {ss.query!r}", initiator="auto")
    bb.checkpoints = ledger.as_dict()
    return bb


# --- Reconciliation: deterministic prev + observation -> next ----------------------
def blockers_for(world: dict[str, Any], gate: GateResult,
                 block: Optional[dict[str, Any]] = None) -> list[Blocker]:
    """Lift the blocker sources into one list: an in-page challenge frame (captcha / anti-bot,
    detected from the browser's frame list — the iframe url/text checks miss), human-required
    branches seen in the world (AI-recruiter / company-site / ...), and unsatisfied required
    fields. An ACTIVE challenge is human_required; a PASSIVE widget is a slow-down advisory.

    The LOGIN GATE: if the session is known-unauthenticated (world['authed'] is False), task
    automation is blocked until a human logs in — search/triage/apply must not run on a logged-out
    session. `authed is None` means 'not probed / not applicable' and does NOT gate (avoids
    false-blocking non-Indeed or unprobed states)."""
    blockers: list[Blocker] = []
    if world.get("authed") is False:
        blockers.append(Blocker(
            kind="auth_required",
            note="session is not logged in — automation gated until authenticated (log in first).",
            human_required=True, source="auth"))
    if block:
        active = block.get("strength") == "active"
        blockers.append(Blocker(
            kind="captcha" if active else "captcha_passive",
            note=block.get("reason") or f"{block.get('provider')} frame present",
            human_required=active, source=block.get("provider", "challenge_frame")))
    for tab in world.get("tabs", []):
        if tab.get("human_required"):
            blockers.append(Blocker(
                kind="human_branch", note=tab.get("branch_note") or tab.get("state", ""),
                human_required=True, source=tab.get("state", "unknown")))
    # An UNRECOGNISED page halts exactly like a captcha does. `map_url_to_state` returns the
    # literal "unknown" when nothing matched — meaning we looked and did not recognise it, which
    # is different from `None` (nothing observed at all, e.g. no tabs) and must not gate.
    # Acting on a page we can't name is the "making it up as I go" failure this store exists to
    # kill, so it stops and asks instead of guessing.
    if world.get("page_state") == UNKNOWN_STATE:
        blockers.append(Blocker(
            kind="unexpected_state",
            note="the active tab is on a page the recipe doesn't recognise — stopping rather than "
                 "acting blind. If it's a legitimate new page, approve it as a state to teach it.",
            human_required=True, source=UNKNOWN_STATE))
    for u in gate.unsatisfied:
        blockers.append(Blocker(
            kind="required_field", note=f"{u['label']} ({u['reason']})",
            human_required=False, source=u["field_id"]))
    return blockers


def _advance_plan(plan: list[Subtask], active_state: Optional[str], gate_ok: bool) -> Optional[str]:
    """Set subtask statuses from the active page state. Steps before the active one are
    `done`; the active one is `blocked` if its gate fails else `active`; the rest `pending`.
    Returns the current subtask id. Deterministic — no model decides progress."""
    active_idx = next((i for i, s in enumerate(plan) if s.id == active_state), None)
    for i, s in enumerate(plan):
        if active_idx is None:
            s.status = "pending"
        elif i < active_idx:
            s.status = "done"
        elif i == active_idx:
            s.status = "blocked" if (s.gate == "form_complete" and not gate_ok) else "active"
        else:
            s.status = "pending"
    return plan[active_idx].id if active_idx is not None else None


def _merge_search(bb: Blackboard, update: dict[str, Any]) -> None:
    """Fold a search-progress update into search_state (page/observed/shortlist/...). The caller
    (the live readout) supplies what it knows; absent keys are left untouched. Written-down, so
    the search phase remembers where it was instead of re-deriving it."""
    ss = bb.search_state
    for k in ("query", "location", "current_job"):
        if update.get(k) is not None:
            setattr(ss, k, update[k])
    if update.get("page") is not None:
        ss.page = int(update["page"])
    if update.get("observed_count") is not None:
        ss.observed_count = int(update["observed_count"])
    for k in ("shortlist", "approved"):
        if update.get(k) is not None:
            setattr(ss, k, list(update[k]))


def reconcile(bb: Blackboard, *, tabs: list[dict[str, Any]],
              form_fields: Optional[list[dict[str, Any]]] = None,
              block: Optional[dict[str, Any]] = None,
              search_update: Optional[dict[str, Any]] = None,
              authed: Optional[bool] = None,
              last_action: Optional[str] = None,
              last_result: Optional[str] = None) -> Blackboard:
    """Fold a fresh observation into the blackboard. World is overwritten from ground truth;
    the PHASE is re-derived off the active tab and the plan spine swaps with it (search↔apply);
    form_state, gate, blockers, and subtask statuses are recomputed; the active subtask only
    advances when its gate passes. `block` is a detected challenge frame
    (escalation_rules.detect_block_frames) — surfaced as a human-required captcha blocker in
    EVERY phase, even when no tab URL maps to a captcha. `search_update` folds search progress
    (page/observed/shortlist). Appends events for the deltas."""
    if last_action is not None:
        bb.last_action = last_action
    if last_result is not None:
        bb.last_result = last_result
        bb.log("action_result", f"{last_action or '?'} -> {last_result}")
    if search_update:
        _merge_search(bb, search_update)
    prev_block = bb.world.get("block")
    if block and not prev_block:
        bb.log("blocked", f"{block.get('provider')} ({block.get('strength')})")

    apply_tabs = [t for t in tabs if t.get("role") == "apply"]
    active = apply_tabs[0] if apply_tabs else (tabs[0] if tabs else None)
    prev_state = bb.world.get("page_state")
    prev_authed = bb.world.get("authed")
    # PRESERVE what this reconcile does not own. `world` is a shared dict: reconcile writes the
    # OBSERVATION (tabs, page_state, role, block, authed), but the session control panel stores
    # its own facts here too — the apply queue, the open pane, a pending proposal, the radius.
    # Replacing the whole dict wiped an operator's 11-step apply queue when they opened the Apply
    # State tab (which calls reconcile) mid-drive (found live 2026-07-24). reconcile owns a fixed
    # set of keys; everything else in world belongs to another writer and is carried forward
    # untouched. This is the same two-writers-one-memory hazard as the sweep/ledger and
    # teach/journal seams — here it cost real work, so the fix is to never clobber, only update.
    _RECONCILE_OWNS = {"tabs", "active_tab_index", "page_state", "role", "block", "authed"}
    preserved = {k: v for k, v in (bb.world or {}).items() if k not in _RECONCILE_OWNS}
    bb.world = {
        **preserved,
        "tabs": tabs,
        "active_tab_index": tabs.index(active) if active in tabs else None,
        "page_state": active.get("state") if active else None,
        "role": active.get("role") if active else None,
        "block": block,
        # Login gate: carry the known auth state forward; only overwrite when a fresh probe
        # supplied one (None = not probed this cycle → keep the last known value).
        "authed": authed if authed is not None else prev_authed,
    }
    if authed is not None and authed != prev_authed:
        bb.log("auth_change", f"authed {prev_authed} -> {authed}")
        # Provenance: data gathered while logged-out is invalid once we authenticate — it must be
        # re-gathered in an authenticated run before it can drive an apply decision.
        ss = bb.search_state
        if authed and not ss.gathered_authenticated and not ss.stale and (
                ss.shortlist or ss.approved or ss.observed_count):
            ss.stale = True
            bb.log("provenance_stale",
                   "search data gathered logged-out; invalid after login — re-gather authenticated")
    if active and active.get("state") != prev_state:
        bb.log("state_change", f"{prev_state} -> {active.get('state')}")
        # Edge-triggered (on the transition, not every reconcile) so the record is one row per
        # occurrence rather than a flood.
        if active.get("state") == UNKNOWN_STATE:
            bb.log("unexpected_state",
                   f"unrecognised page: {str(active.get('url') or '')[:120]}")

    # Re-derive the phase off the active tab; swap the plan spine when the family changes
    # (search/triage share the search spine; apply has its own). Statuses are recomputed from
    # the active state below, so rebuilding the plan loses nothing.
    new_phase = _phase_for(bb.world.get("role"), bb.world.get("page_state"))
    if new_phase != bb.phase:
        bb.log("phase_change", f"{bb.phase} -> {new_phase}")
    if _plan_family(new_phase) != _plan_family(bb.phase) or not bb.plan:
        bb.plan = _plan_for_family(_plan_family(new_phase))
    bb.phase = new_phase

    if form_fields is not None:
        bb.form_state = build_form_state(form_fields)

    gate = form_complete_gate(bb.form_state)
    bb.blockers = blockers_for(bb.world, gate, block)
    bb.current_subtask_id = _advance_plan(bb.plan, bb.world.get("page_state"), gate.ok)
    bb.updated_at = _utcnow()
    return bb


# --- Layer 3 made ACTIVE: the proceed/submit decision + the loop gate --------------
def current_subtask(bb: Blackboard) -> Optional[Subtask]:
    return next((s for s in bb.plan if s.id == bb.current_subtask_id), None)


def search_data_actionable(bb: Blackboard) -> dict[str, Any]:
    """Is the current search/triage data valid to ACT on (approve a job / start applying)? Context-
    bound validity: yes only if there's a current cadence run, the data was gathered while
    authenticated, the session is authenticated NOW, and the data hasn't been marked stale. Names the
    failing reason so the readout/operator sees exactly why a shortlist can't be acted on yet."""
    ss = bb.search_state
    authed_now = bb.world.get("authed") is True
    if not ss.cadence_run_id:
        reason = "no_cadence_run"
    elif ss.stale:
        reason = "provenance_stale"
    elif not ss.gathered_authenticated:
        reason = "gathered_unauthenticated"
    elif not authed_now:
        reason = "not_authenticated_now"
    else:
        reason = "ok"
    return {
        "ok": reason == "ok",
        "reason": reason,
        "cadence_run_id": ss.cadence_run_id,
        "gathered_authenticated": ss.gathered_authenticated,
        "authed_now": authed_now,
        "stale": ss.stale,
    }


def proceed_decision(bb: Blackboard) -> dict[str, Any]:
    """The operator/agent's "is it safe to move forward from here?" answer, straight off
    the live blackboard. NOT ok if a human-required branch is up (captcha / AI-recruiter)
    or the current subtask is form-gated and a required field is still empty/invalid.
    Names the blockers so the readout/UI can show exactly what's missing."""
    # Login gate first: a logged-out session must not run task automation at all.
    auth = [b for b in bb.blockers if b.kind == "auth_required"]
    if auth:
        return {"ok": False, "reason": "auth_required",
                "blockers": [{"kind": b.kind, "note": b.note, "source": b.source} for b in auth]}
    human = [b for b in bb.blockers if b.human_required]
    if human:
        return {"ok": False, "reason": "human_required",
                "blockers": [{"kind": b.kind, "note": b.note, "source": b.source} for b in human]}
    cur = current_subtask(bb)
    if cur and cur.gate == "form_complete":
        gate = form_complete_gate(bb.form_state)
        if not gate.ok:
            return {"ok": False, "reason": "form_incomplete", "subtask": cur.id,
                    "blockers": gate.unsatisfied}
    return {"ok": True, "reason": "clear", "subtask": cur.id if cur else None, "blockers": []}


def make_proceed_gate(bb: Blackboard):
    """A gate for `runtime.run_loop`: refuse to fire (or even record) an action while the
    blackboard says we can't safely proceed. Signature is (selection_result, observation)
    -> block-dict|None, so the loop escalates to a human instead of submitting blind. The
    decision is purely the live blackboard — the action is structurally unable to skip a
    blocker, because the check is code, not the model's memory."""
    def gate(result, observation):  # noqa: ANN001 — loop port, types kept loose on purpose
        decision = proceed_decision(bb)
        return None if decision["ok"] else decision
    return gate


# --- Persistence: one JSON file per session (matches the cache/ convention) --------
def _store_dir() -> Path:
    from settings import settings
    base = Path(settings.observer_artifacts_dir)
    if not base.is_absolute():
        base = (Path(__file__).resolve().parent / base).resolve()
    p = base / "cache" / "apply_blackboards"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path_for(session_id: int) -> Path:
    return _store_dir() / f"session_{session_id}.json"


def load(session_id: int) -> Optional[Blackboard]:
    p = _path_for(session_id)
    if not p.exists():
        return None
    try:
        return Blackboard.from_dict(json.loads(p.read_text()))
    except Exception:
        return None


def save(bb: Blackboard) -> None:
    _path_for(bb.session_id).write_text(json.dumps(bb.to_dict(), indent=2), encoding="utf-8")


def load_or_create(session_id: int, goal: Optional[str] = None,
                   query: str = "", location: str = "") -> Blackboard:
    """Load the persisted blackboard or create a fresh one seeded with the search target. The
    query/location only seed a BRAND-NEW blackboard — an existing one keeps its written-down
    target, so reloading mid-session never clobbers progress."""
    return load(session_id) or new_blackboard(session_id, goal, query=query, location=location)
