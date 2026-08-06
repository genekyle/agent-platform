"""Checkpoints — the open-ended alternative to "end flags".

WHY THIS EXISTS (operator-directed 2026-07-23). We kept trying to give each task a terminal
flag ("the search is done when ..."), and every attempt was wrong in the same way: these tasks
have no natural end. Worse, the start-stop shape an end-flag implies is actively HARMFUL on
Indeed — repeat the same query too often and Indeed caches/collapses it, so results we already
saw stop coming back. **Re-running a search is not free and not idempotent.** A design that
restarts a task to "finish it properly" burns the very thing it is trying to gather.

So we stop asking "is it done?" and ask "**how far up the ladder are we, and what's next?**"

    A CHECKPOINT is a milestone that, once reached, is HELD for the life of its SCOPE
    and is never re-executed just because we came back around to it.

THERE ARE TWO SCOPES, and getting them wrong was expensive (operator-corrected 2026-08-06):

  * **the session** — the browser and the sign-in. `provisioned`, `authenticated`. These outlive
    every search inside the session and are the expensive things to re-establish.
  * **the search** — one query and what it means. `query_entered`, `radius_set`, and the
    `page:N`/`select:N` rungs walked through its results.

`query_entered` used to say it "runs ONCE per session", and that sentence made abandoning a query
mean abandoning the *session*: closing a signed-in browser and provisioning another, to change a
word in a search box. The once-only rule protects a QUERY from being re-run, not a session from
holding a second one. **Sessions end when the operator ends them; searches come and go inside
them.**

Two kinds, and the distinction is the whole point:

  * **standing**  — must be true CONTINUOUSLY (signed in, browser reachable). Cheap to verify
    every step, and safe to re-run when it lapses. Losing it is a regression we fix in place.
  * **consuming** — reaching it SPENT something that cannot be spent twice (submitting the
    query, applying the distance filter — each one hits Indeed's search backend). Once held it
    is NEVER re-run, even when the page no longer looks like it. If the effect is gone we
    RECOVER (get back to the results we already have), we do not re-search.

THE LADDER IS OPEN-ENDED BY CONSTRUCTION. Four fixed rungs get us to the start line, and then
it keeps growing one rung per results page reviewed — `page:1`, `page:2`, ... There is no last
rung to flag, which is exactly the shape the operator asked for: the session holds its shape
and advances a cursor instead of stopping and restarting. "Task complete" is not a flag we
invent; it is the emergent fact that **the ladder cannot grow** (no next page), and even then
stopping is the operator's call, not ours.

Pure module: no I/O, no browser, no DB. `routers/session_control.py` executes what this decides
and persists the ledger on the session blackboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

STANDING = "standing"
CONSUMING = "consuming"

# Per-checkpoint status as the panel shows it.
HELD = "held"              # reached, and still true (or a consuming rung we don't re-check)
NEXT = "next"              # the one rung the next step will work on
PENDING = "pending"        # not reached yet, and not next
REGRESSED = "regressed"    # standing rung that was held and has gone false — re-run it
LAPSED = "lapsed"          # consuming rung whose EFFECT is gone — recover, never re-run


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Checkpoint:
    """One rung. `action` is the verb the step executor dispatches on; `recovery` is what to do
    instead when a CONSUMING rung's effect is gone (it must never say "do it again")."""

    id: str
    label: str
    kind: str                 # STANDING | CONSUMING
    why: str                  # why it is standing / why it must not be repeated
    action: str               # what advancing it does — the executor's dispatch key
    recovery: str = ""        # CONSUMING only: how to get back without re-spending


# --- The Indeed search ladder ---------------------------------------------------------------
# The four fixed rungs that reach the start line. Everything after them is a page rung.
PREAMBLE: tuple[Checkpoint, ...] = (
    Checkpoint(
        id="provisioned",
        label="Browser ready",
        kind=STANDING,
        why="A session is one focused Chrome instance; without a reachable one there is nothing "
            "to drive. Cheap to re-probe, safe to re-establish.",
        action="probe_browser",
    ),
    Checkpoint(
        id="authenticated",
        label="Signed in to {engine}",
        kind=STANDING,
        why="Logged-out data is provenance-invalid, so this has to hold CONTINUOUSLY, not once. "
            "Re-running it is safe — it stops at any credential/2FA wall for the operator.",
        action="auth_probe",
    ),
    Checkpoint(
        id="query_entered",
        label="Query run",
        kind=CONSUMING,
        why="Submitting the query hits {engine}'s search backend. Repeat THE SAME query too often "
            "and {engine} caches/collapses it — results we already saw stop coming back. So it "
            "runs once per SEARCH: a different query is new work and starts a new search inside "
            "this session, which keeps the browser and the sign-in we already have.",
        action="run_query",
        recovery="Return to the results we already have — refocus the existing search tab, or go "
                 "back — never re-submit the same query. To search for something ELSE, start a "
                 "new search; that is not a repeat and costs nothing already spent.",
    ),
    Checkpoint(
        id="radius_set",
        label="Distance filter applied",
        kind=CONSUMING,
        why="Clicking the distance pill re-queries the backend, same cost as the search itself. "
            "It also defines what this SEARCH's results MEAN, so changing it mid-search would "
            "invalidate the pages already reviewed. A new search sets its own radius.",
        action="set_distance",
        recovery="Trust the radius already applied to this result set; re-clicking the pill "
                 "re-queries and resets the pagination we have walked.",
    ),
)

PREAMBLE_IDS: tuple[str, ...] = tuple(c.id for c in PREAMBLE)

#: Rungs that belong to the SESSION and outlive every search inside it. A browser stays reachable
#: and an account stays signed in across as many searches as the operator wants to run.
SESSION_RUNGS: frozenset[str] = frozenset({"provisioned", "authenticated"})

#: Rungs that belong to ONE SEARCH. Everything the query defines: the query itself, the radius that
#: says what its results mean, and the pages walked through them.
#:
#: THE DISTINCTION THIS MODULE ORIGINALLY MISSED (operator-corrected 2026-08-06). `query_entered`
#: said it "runs ONCE per session", which conflated two different facts:
#:
#:   * **Do not re-run THIS query** — true, and the reason `consuming` exists. Indeed caches and
#:     collapses a repeated search, so results we already saw stop coming back.
#:   * **This session gets one query, ever** — never true, and expensive. It made abandoning a
#:     query mean abandoning the SESSION: closing a signed-in browser, provisioning another,
#:     signing in again, re-establishing everything that had nothing to do with the query. The
#:     operator's words: *"we aren't exiting Indeed and we shouldn't… sessions are active until we
#:     end them, querying and re-querying shouldn't end a session."*
#:
#: So a search is a scope INSIDE a session. Starting a new one advances the scope; the old search's
#: rungs stay in the ledger under their old ids, which is what keeps the once-only promise real —
#: the guard becomes "have we spent THIS query", not "have we spent any query".
SEARCH_SCOPED: frozenset[str] = frozenset({"query_entered", "radius_set"})

#: Search 1 is UNSCOPED so every ledger written before searches existed reads back unchanged.
SEARCH_PREFIX = "s"
FIRST_SEARCH = 1

PAGE_PREFIX = "page:"


def page_rung(page: int) -> Checkpoint:
    """The rolling rung: 'we have reviewed page N'. CONSUMING because paging back and forth to
    re-review is exactly the repeat-traffic pattern Indeed penalises — the page's cards are
    already recorded, so re-walking it buys nothing and costs bot-safety budget."""
    return Checkpoint(
        id=f"{PAGE_PREFIX}{page}",
        label=f"Page {page} reviewed",
        kind=CONSUMING,
        why="The page's cards are recorded and the operator has made their picks. Paging back to "
            "re-review is repeat traffic that buys nothing.",
        action="review_page",
        recovery="Read the recorded results for this page instead of navigating back to it.",
    )


SELECT_PREFIX = "select:"

#: WHO (or what) chose which jobs to apply to. The selection is a real decision with a real
#: decider, and the decider is part of the recipe — today it is always the operator, and the
#: point of naming it is that a classifier can take the same rung later WITHOUT the step changing
#: shape. A row that does not say who chose cannot train anything, and cannot be audited either.
DECIDER_OPERATOR = "operator"
DECIDER_PREFIXES = ("classifier:", "rule:")


def valid_decider(decided_by: str) -> bool:
    return decided_by == DECIDER_OPERATOR or decided_by.startswith(DECIDER_PREFIXES)


def select_rung(page: int) -> Checkpoint:
    """'We have decided what to apply to on page N.'

    STANDING, not consuming: choosing again costs nothing and spends nothing — it is a decision
    held in our own records, not traffic against Indeed. That is the whole difference between
    this rung and the page rung it sits beside, and it is why adding to your picks is safe while
    re-running the query is not.

    It exists because the decision was previously INVISIBLE. Reviewing a page was a rung and
    working an application was a rung, but the choice between them — the one part a human
    actually makes — was an `awaiting` flag and a log line. Operator, 2026-07-26: *"it feels like
    it's not an actual step."* It was not.
    """
    return Checkpoint(
        id=f"{SELECT_PREFIX}{page}",
        label=f"Page {page} picks made",
        kind=STANDING,
        why="Choosing what to apply to is a decision with a decider, and both belong on the "
            "record. Re-choosing is free — this rung spends nothing, unlike the page it sits on.",
        action="select_page",
    )


def select_of(checkpoint_id: str) -> Optional[int]:
    """The page number in a selection rung id, or None if it isn't one."""
    if not checkpoint_id.startswith(SELECT_PREFIX):
        return None
    try:
        return int(checkpoint_id[len(SELECT_PREFIX):])
    except ValueError:
        return None


def page_of(checkpoint_id: str) -> Optional[int]:
    """The page number in a rolling rung id, or None if it isn't one."""
    bare = unscoped(checkpoint_id)
    if not bare.startswith(PAGE_PREFIX):
        return None
    try:
        return int(bare[len(PAGE_PREFIX):])
    except ValueError:
        return None


# --- search scoping ---------------------------------------------------------------------------
def is_search_scoped(checkpoint_id: str) -> bool:
    """Does this rung belong to one search rather than to the session?"""
    bare = unscoped(checkpoint_id)
    return (bare in SEARCH_SCOPED
            or bare.startswith(PAGE_PREFIX) or bare.startswith(SELECT_PREFIX))


def scoped(checkpoint_id: str, search: int) -> str:
    """`page:1` in search 3 -> `s3/page:1`. Search 1 and session rungs are left bare.

    Session rungs are NEVER scoped: scoping `authenticated` per search would make a new search
    look like a fresh sign-in, which is the exact waste this change exists to remove.
    """
    if search <= FIRST_SEARCH or not is_search_scoped(checkpoint_id):
        return checkpoint_id
    return f"{SEARCH_PREFIX}{search}/{checkpoint_id}"


def unscoped(checkpoint_id: str) -> str:
    """`s3/page:1` -> `page:1`. Idempotent on ids that carry no scope."""
    head, sep, tail = checkpoint_id.partition("/")
    if sep and head.startswith(SEARCH_PREFIX) and head[len(SEARCH_PREFIX):].isdigit():
        return tail
    return checkpoint_id


def search_of(checkpoint_id: str) -> int:
    """Which search a rung id belongs to. Bare ids are search 1."""
    head, sep, _ = checkpoint_id.partition("/")
    if sep and head.startswith(SEARCH_PREFIX) and head[len(SEARCH_PREFIX):].isdigit():
        return int(head[len(SEARCH_PREFIX):])
    return FIRST_SEARCH


# --- The ledger: what this session has actually reached --------------------------------------
@dataclass
class Reached:
    """The record that a rung was reached — WHEN, on what evidence, and who turned the crank.
    Provenance travels with the fact (PRINCIPLES §1): 'reached' with no evidence is a claim."""

    at: str
    evidence: str = ""
    initiator: str = "operator"   # operator | auto | teacher

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at, "evidence": self.evidence, "initiator": self.initiator}


@dataclass
class Ledger:
    """Which rungs this session holds. Ordered by insertion, which is reach order."""

    reached: dict[str, Reached] = field(default_factory=dict)
    #: Which search inside this session we are working. Callers pass BARE rung ids and this
    #: applies the scope, so nothing outside this class has to know searches exist.
    search: int = FIRST_SEARCH
    #: `{search ordinal: the query it spent}`. STRUCTURED, not parsed back out of the rung's
    #: evidence prose — the first cut read `query_entered`'s evidence looking for the query, which
    #: makes the once-only guard depend on the wording of a sentence written for a human. That is
    #: the same defect as scoring a metric off a detail string, and it fails silently the first
    #: time somebody rewords the evidence.
    queries: dict[int, str] = field(default_factory=dict)

    def _key(self, checkpoint_id: str) -> str:
        return scoped(checkpoint_id, self.search)

    def holds(self, checkpoint_id: str) -> bool:
        return self._key(checkpoint_id) in self.reached

    def mark(self, checkpoint_id: str, *, evidence: str = "",
             initiator: str = "operator") -> Reached:
        """Record a rung as reached. Idempotent by design — marking a held rung KEEPS the
        original timestamp and evidence, because the first reaching is the one that spent the
        cost. Silently overwriting it would erase the record that we already paid."""
        key = self._key(checkpoint_id)
        existing = self.reached.get(key)
        if existing is not None:
            return existing
        row = Reached(at=_utcnow(), evidence=evidence, initiator=initiator)
        self.reached[key] = row
        return row

    def row_for(self, checkpoint_id: str) -> Optional[Reached]:
        """The provenance of a rung in the CURRENT search, or None. Readers must go through this
        rather than indexing `reached` with a bare id, which silently reads search 1's row."""
        return self.reached.get(self._key(checkpoint_id))

    def release(self, checkpoint_id: str) -> None:
        """Drop a rung so it will be worked again. Only ever valid for STANDING rungs — the
        executor enforces that; consuming rungs stay held for the session's life."""
        self.reached.pop(self._key(checkpoint_id), None)

    def pages_reviewed(self) -> list[int]:
        """Pages walked IN THE CURRENT SEARCH. Search 2's page 1 is a different page of different
        results from search 1's, and mixing them would report a walk nobody took."""
        pages = [page_of(cid) for cid in self.reached if search_of(cid) == self.search]
        return sorted(p for p in pages if p is not None)

    # --- searches: several per session, and the record of each one kept ------------------------
    def note_query(self, query: str) -> None:
        """Record which query the CURRENT search is spending. Called when the search is declared,
        so the once-only guard has a fact to check rather than a sentence to parse."""
        q = " ".join((query or "").split())
        if q:
            self.queries[self.search] = q

    def spent_queries(self) -> dict[int, str]:
        """`{search ordinal: the query it spent}` for every search that actually ran one.

        This is what makes the once-only promise mean the RIGHT thing: not "one query per session"
        but "this query has been spent, and re-running it is what gets it collapsed".

        Only searches whose `query_entered` rung is HELD count — a declared-but-never-run query
        has spent nothing and must not block itself.
        """
        out: dict[int, str] = {}
        for cid in self.reached:
            if unscoped(cid) != "query_entered":
                continue
            n = search_of(cid)
            noted = self.queries.get(n)
            if noted:
                out[n] = noted
            else:
                # Ledgers written before `queries` existed: fall back to the evidence, which has
                # always carried the query. Best-effort and clearly labelled as the old path.
                out[n] = (self.reached[cid].evidence or "").strip()
        return out

    def has_spent(self, query: str) -> bool:
        """Has THIS query already been run in this session?

        Exact on the structured record; containment only against legacy evidence prose, where the
        query is embedded in a sentence ("results URL carries q='data engineer'").
        """
        wanted = " ".join((query or "").split()).lower()
        if not wanted:
            return False
        for n, spent in self.spent_queries().items():
            if not spent:
                continue
            if self.queries.get(n):
                if spent.lower() == wanted:
                    return True
            elif wanted in spent.lower():
                return True
        return False

    def start_new_search(self) -> int:
        """Move to the next search in this session. Returns the new ordinal.

        NOTHING IS DELETED. The previous search's rungs stay in the ledger under their own scope,
        which is the whole reason this is safe: the record of what was spent survives, so the
        once-only guard still refuses to re-run a query this session already ran. Advancing the
        scope only changes which rungs the LADDER is currently asking about.

        The session rungs are untouched by construction — `scoped()` refuses to scope them — so a
        new search starts already signed in, on the browser that is already open. That is the
        point: abandoning a query must not cost an authenticated session.
        """
        self.search = max([search_of(cid) for cid in self.reached] + [self.search]) + 1
        return self.search

    def searches(self) -> list[int]:
        """Every search ordinal this session has touched, in order."""
        seen = {search_of(cid) for cid in self.reached if is_search_scoped(cid)}
        seen.add(self.search)
        return sorted(seen)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {cid: r.as_dict() for cid, r in self.reached.items()}
        # A RESERVED KEY rather than a wrapper object, so every ledger written before searches
        # existed still loads as-is. `from_dict` skips anything dunder-shaped, and no rung id can
        # collide with it.
        if self.search != FIRST_SEARCH or self.queries:
            out["__search__"] = {"n": self.search,
                                 "queries": {str(k): v for k, v in self.queries.items()}}
        return out

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "Ledger":
        led = cls()
        for cid, row in (data or {}).items():
            if cid.startswith("__"):
                continue
            if isinstance(row, dict) and row.get("at"):
                led.reached[cid] = Reached(at=row["at"], evidence=row.get("evidence", ""),
                                           initiator=row.get("initiator", "operator"))
        meta = (data or {}).get("__search__")
        if isinstance(meta, dict):
            if isinstance(meta.get("n"), int):
                led.search = max(FIRST_SEARCH, meta["n"])
            for k, v in (meta.get("queries") or {}).items():
                try:
                    led.queries[int(k)] = str(v)
                except (TypeError, ValueError):
                    continue
        return led


# --- The decision: what the next crank should work on ----------------------------------------
# `observed` is a tri-state map {checkpoint_id: True | False | None}. None means "we didn't
# check" and is NOT the same as False — an unknown must never be read as a regression, or a
# flaky probe would send us re-running a consuming rung.

ADVANCE = "advance"        # work this rung (not yet reached, or a standing rung that lapsed)
RECOVER = "recover"        # a consuming rung's effect is gone: get back, don't re-spend
REVIEW = "review"          # the preamble is topped out — surface this page for the operator


@dataclass
class NextStep:
    kind: str                       # ADVANCE | RECOVER | REVIEW
    checkpoint: Checkpoint
    reason: str
    page: Optional[int] = None      # set for REVIEW / page rungs

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "checkpoint_id": self.checkpoint.id,
            "label": self.checkpoint.label,
            "action": self.checkpoint.action,
            "checkpoint_kind": self.checkpoint.kind,
            "why": self.checkpoint.why,
            "recovery": self.checkpoint.recovery,
            "reason": self.reason,
            "page": self.page,
        }


def adopt_prior_run(ledger: Ledger, *, query: str = "", cadence_run_id: str = "",
                    run_started_at: str = "", authed: bool = False) -> Optional[str]:
    """Teach the ledger about a query this session spent BEFORE the ledger was watching.

    THE HOLE THIS PLUGS, found live 2026-07-23. The once-only guarantee is only as strong as the
    ledger's memory, and that memory starts empty on any session older than the ledger — or on
    any query spent through a *different code path*. `/api/search/sweep` opens a cadence run via
    `apply_state_store.start_cadence_run` and never touches the ledger, so a session that had
    already swept 'data analyst' to exhaustion looked, to the ladder, like one that had never
    searched. The panel would have let us fire a second query on it.

    Two code paths spending the same unrepeatable resource, and only one recording it, is the
    failure mode — not the specific migration. `cadence_run_id` is the provenance stamp that a
    real search ran, so we adopt it as the `query_entered` rung instead of searching again.

    Returns the rung id if one was adopted, else None. Never overwrites a rung already held.
    """
    if not cadence_run_id or ledger.holds("query_entered"):
        return None
    detail = f"run {cadence_run_id}" + (f" for {query!r}" if query else "")
    row = ledger.mark("query_entered",
                      evidence=f"adopted from a prior cadence run — {detail}"
                               + ("" if authed else " (gathered logged-out)"),
                      initiator="auto")
    if run_started_at:
        row.at = run_started_at   # the cost was paid then, not now
    return "query_entered"


#: The engine a ladder is climbing, filled into the rung text at RENDER time. Rungs are keyed by
#: ID and the ledger stores IDs, so the operator-facing strings are display-only and free to vary
#: per session — which is exactly what a LinkedIn session needs, having been told for one release
#: that its next step was to sign in to Indeed.
DEFAULT_ENGINE = "Indeed"


def for_engine(cp: Checkpoint, engine: str = DEFAULT_ENGINE) -> Checkpoint:
    """The same rung, worded for the engine actually being driven."""
    def _f(text: str) -> str:
        return text.replace("{engine}", engine or DEFAULT_ENGINE)
    return Checkpoint(id=cp.id, label=_f(cp.label), kind=cp.kind, why=_f(cp.why),
                      action=cp.action, recovery=_f(cp.recovery))


def preamble(engine: str = DEFAULT_ENGINE) -> tuple[Checkpoint, ...]:
    return tuple(for_engine(cp, engine) for cp in PREAMBLE)


def next_step(ledger: Ledger, observed: dict[str, Any], *, page: int = 1,
              engine: str = DEFAULT_ENGINE) -> NextStep:
    """The one decision this module exists to make: what does the next crank work on?

    Walks the preamble in order. The first rung that isn't satisfied wins — and HOW it is
    unsatisfied decides ADVANCE vs RECOVER, which is the whole safety property:

      * not reached yet                     -> ADVANCE (do it for the first time)
      * standing, reached, now observed False -> ADVANCE (re-run; standing rungs are idempotent)
      * consuming, reached, observed False    -> RECOVER (the effect is gone; get back to it)
      * observed None (unchecked)             -> held stays held; an unknown is not a regression

    Past the preamble the ladder is open-ended, so we always land on REVIEW for the current
    page: there is no terminal rung and therefore nothing to flag as "done".
    """
    for cp in preamble(engine):
        if not ledger.holds(cp.id):
            return NextStep(ADVANCE, cp, reason=f"{cp.label} has not been reached yet.")
        if observed.get(cp.id) is False:
            if cp.kind == STANDING:
                return NextStep(ADVANCE, cp,
                                reason=f"{cp.label} was held but is no longer true — re-running "
                                       f"it is safe.")
            return NextStep(RECOVER, cp,
                            reason=f"{cp.label} was already spent this session but its effect is "
                                   f"gone. Recovering instead of repeating it.")
    return NextStep(REVIEW, page_rung(page), page=page,
                    reason=f"At the start line. Page {page} is ready for the operator to review.")


def status_rows(ledger: Ledger, observed: dict[str, Any], *,
                page: int = 1, has_results: bool = False,
                engine: str = DEFAULT_ENGINE) -> list[dict[str, Any]]:
    """The ladder as the panel renders it: every preamble rung plus the page rungs walked so
    far, each with its status and provenance. Read-only view over `next_step`.

    `has_results` says whether this page's cards have actually been read, which is what decides
    whether the SELECTION rung is the operator's next move or still pending. Without it the panel
    would invite a choice between fifteen jobs nobody has looked at yet.
    """
    nxt = next_step(ledger, observed, page=page, engine=engine)
    rows: list[dict[str, Any]] = []
    for cp in preamble(engine):
        held = ledger.holds(cp.id)
        ok = observed.get(cp.id)
        if cp.id == nxt.checkpoint.id:
            state = REGRESSED if (held and cp.kind == STANDING) else (
                LAPSED if held else NEXT)
        elif held:
            state = HELD
        else:
            state = PENDING
        row = {"id": cp.id, "label": cp.label, "kind": cp.kind, "status": state,
               "why": cp.why, "observed": ok}
        if held:
            row["reached"] = ledger.row_for(cp.id).as_dict()
        rows.append(row)

    for p in ledger.pages_reviewed():
        cp = page_rung(p)
        rows.append({"id": cp.id, "label": cp.label, "kind": cp.kind, "status": HELD,
                     "why": cp.why, "observed": None,
                     "reached": ledger.row_for(cp.id).as_dict()})

    if nxt.kind == REVIEW and not ledger.holds(nxt.checkpoint.id):
        rows.append({"id": nxt.checkpoint.id, "label": nxt.checkpoint.label,
                     "kind": nxt.checkpoint.kind, "status": NEXT,
                     "why": nxt.checkpoint.why, "observed": None})

        # THE SELECTION, as its own step. It sits under the page it belongs to and is only
        # actionable once the cards have been read — otherwise it is a choice between jobs nobody
        # has seen. HELD carries who decided and what they picked, so the ladder can be read back
        # as "6 of 15, by operator" long after the fact.
        sel = select_rung(page)
        held = ledger.holds(sel.id)
        row = {"id": sel.id, "label": sel.label, "kind": sel.kind,
               "status": HELD if held else (NEXT if has_results else PENDING),
               "why": sel.why, "observed": None}
        if held:
            row["reached"] = ledger.row_for(sel.id).as_dict()
        rows.append(row)
    return rows


def at_start_line(ledger: Ledger, observed: dict[str, Any]) -> bool:
    """True once every preamble rung is satisfied — the moment the session stops running
    continuously and becomes stop-and-go. This is the phase boundary the operator described:
    get yourself to the start line unattended, then ask me about every page."""
    return next_step(ledger, observed).kind == REVIEW


def progress(ledger: Ledger, observed: dict[str, Any], *, page: int = 1) -> dict[str, Any]:
    """The header summary: how far up, how many pages walked, and the honest answer to 'are we
    done'. `can_grow` is None until we know whether a next page exists — we never guess it."""
    preamble_held = sum(1 for cp in PREAMBLE if ledger.holds(cp.id))
    pages = ledger.pages_reviewed()
    return {
        "preamble_held": preamble_held,
        "preamble_total": len(PREAMBLE),
        "at_start_line": at_start_line(ledger, observed),
        "pages_reviewed": pages,
        "page": page,
        "phase": "start_line" if at_start_line(ledger, observed) else "climbing",
    }
