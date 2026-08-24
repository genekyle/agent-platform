"""The transition maturity registry — what each transition has EARNED, derived from the corpus.

`docs/CONTROLLER_PROMOTION.md` has described this gate since M5 and nothing has ever enforced it:
promotion was a paragraph an operator was expected to apply by hand. This is that paragraph made
mechanical, so `authority()` can read it on every turn.

--------------------------------------------------------------------------------------
It is a VIEW, not a corpus
--------------------------------------------------------------------------------------
`derive()` is a pure function of `(journal rows, programs)`. Nothing is stored, nothing is
incremented, there is no maturity file to fall out of sync with the evidence. That is deliberate
and it is the 2026-07-16 lesson applied one level up: this repo has already been burned by a
second write-path (`event_log.jsonl`) that looked like a corpus, drifted from the real one, and
was believed anyway. A registry that can disagree with the journal will eventually disagree with
the journal. This one cannot — delete the cache and it recomputes identically.

--------------------------------------------------------------------------------------
Absence of evidence is the strictest answer, never permission
--------------------------------------------------------------------------------------
Every threshold below is a floor to CLIMB. A transition with no rows is UNSEEN, which
`authority()` turns into ORANGE — the teacher supplies the action. Measured 2026-07-22 on the
real corpus (45 rows, 3 compiled programs, effectively one platform), that means most of Workday
starts out asking. That is the honest state of the system, and the plan's whole point is that the
number is now visible and improvable rather than assumed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from interaction.authority import (
    ControlMode,
    Maturity,
    PromotionStanding,
    TransitionKey,
    maturity_rank,
)
from interaction.supervision import FailureClass

from controller import programs as programs_mod

# --- thresholds. Named, greppable, and matched to CONTROLLER_PROMOTION.md -----------
#: Verified-ok rows before a transition may be REPLAYABLE without a compiled program.
REPLAYABLE_OKS = 2
#: …before TESTING.
TESTING_OKS = 3
#: …before CERTIFIED.
CERTIFIED_OKS = 5
#: How many consecutive recent rows must be supervisor-nominal to certify. Uses the SUPERVISOR's
#: verdict, never `verified`: the 2026-07-19 treadmill scored 8/8 verified while the page never
#: moved, so `verified` alone would have certified a transition that achieves nothing.
CERTIFIED_CLEAN_TAIL = 3
#: States whose action is chosen by the TASK TARGET rather than by the page — which job to open
#: comes from the approved prospect, not from a fixed control. Reused from the compiler rather than
#: re-listed, so the two cannot drift: if a state is too target-dependent to compile a program
#: from, it is too target-dependent to certify a transition on.
TARGET_PARAMETERISED_STATES = programs_mod.NON_COMPILABLE_STATES


@dataclass
class TransitionStat:
    """One transition's track record. Everything `derive` learned, kept so the coverage table can
    explain a verdict instead of just asserting it."""

    key: TransitionKey
    task: str = ""                    # reporting only — never identity (see TransitionKey)
    attempts: int = 0
    oks: int = 0
    failures: int = 0
    teacher_oks: int = 0
    local_oks: int = 0                # the LOCAL system did it and it worked (any non-teacher rung)
    clean_reviews: int = 0            # proposed under review (YELLOW) and approved uncorrected
    corrections: int = 0              # golden rows — the teacher had to fix it
    supervised_nominal_tail: int = 0  # consecutive most-recent rows the supervisor called fine
    last_failed: bool = False
    has_program: bool = False
    program_stale: bool = False
    landings: tuple[str, ...] = ()    # observed destinations — reporting, never identity
    platform: str = ""                # from the ATS column, for grouping the coverage table
    maturity: str = Maturity.UNSEEN.value
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {**self.key.as_dict(), "id": self.key.as_str(), "task": self.task,
                "platform": self.platform,
                "maturity": self.maturity, "reason": self.reason,
                "attempts": self.attempts, "oks": self.oks, "failures": self.failures,
                "teacher_oks": self.teacher_oks, "clean_reviews": self.clean_reviews,
                "corrections": self.corrections, "has_program": self.has_program,
                "program_stale": self.program_stale, "landings": list(self.landings)}


# --- reading one journal row --------------------------------------------------------
#: Intents that change nothing on the page. They are not transitions, and counting them would let
#: a state certify itself by being looked at — the same trap `programs._NO_OP_INTENTS` guards.
NO_OP_INTENTS = frozenset({"observe", "describe", "scan_required", "resolve_answer"})


def key_for_row(row: dict[str, Any]) -> Optional[TransitionKey]:
    """The transition a journal row is evidence about, or None if it is not evidence at all.

    Skipped: escalations (nothing was attempted), shadow rows (nothing happened), no-op intents,
    and rows missing task or state — a row we cannot place cannot teach us about a place.
    """
    if row.get("escalate") or row.get("shadow"):
        return None
    state, intent = row.get("state"), row.get("intent")
    if not state or not intent or intent in NO_OP_INTENTS:
        return None
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    ref = params.get("field") or params.get("control") or ""
    return TransitionKey(from_state=state, intent=intent, ref=str(ref))


def _is_ok(row: dict[str, Any]) -> bool:
    """A row that did what it meant to.

    `verified is not False` rather than `verified is True`: a field fill that stays on the same
    page journals `verified=None` when no expectation was given, and refusing those would make
    every form-filling transition permanently unprovable.
    """
    return row.get("outcome") == "ok" and row.get("verified") is not False


def _is_supervised_nominal(row: dict[str, Any]) -> bool:
    """The supervisor looked and said nothing went wrong.

    `None` — the supervisor did not run — is deliberately NOT nominal. Certification requires
    positive evidence, and every row journaled before S12 (2026-07-20) has no verdict at all. This
    is why history alone certifies nothing: the drives have to be re-run under the supervisor.
    """
    return row.get("supervisor_class") == FailureClass.NONE.value


# --- programs as corroborating evidence ---------------------------------------------
def _program_index(programs: Optional[Iterable[programs_mod.IntentProgram]] = None
                   ) -> dict[str, programs_mod.IntentProgram]:
    """Keyed by STATE, matching `TransitionKey` — a program is about a page, and the task label it
    happened to be compiled under is not part of what it proves."""
    progs = programs if programs is not None else programs_mod.list_programs()
    return {p.state: p for p in progs}


def _program_covers(program: programs_mod.IntentProgram, key: TransitionKey) -> bool:
    """Does this compiled program actually contain this step? A program for the state is not
    evidence for every action on the state — only for the ones it replays."""
    for step in program.steps:
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        ref = params.get("field") or params.get("control") or ""
        if step.get("intent") == key.intent and str(ref) == key.ref:
            return True
    return False


# --- the ladder ---------------------------------------------------------------------
def grade(stat: TransitionStat) -> tuple[str, str]:
    """`TransitionStat -> (maturity, why)`. PURE, and the single place the rules live.

    Checked strongest-first so the reason names the highest bar actually cleared. REGRESSED is an
    overlay applied afterwards, not a rung competing with the others.
    """
    if stat.attempts == 0:
        return Maturity.UNSEEN.value, "never attempted"

    # A target-parameterised page's action is chosen by WHICH ITEM we are pursuing, not by the
    # page — `programs.NON_COMPILABLE_STATES` already refuses to compile these for exactly that
    # reason, and the registry has to refuse to certify them for the same one. Left uncapped, the
    # corpus grows one "transition" per job title ("click full details of Analyst - Actuarial
    # Financial Reporting", verified once) and each would climb the ladder on evidence that can
    # never recur. Capping at DEMONSTRATED also lands the right policy on the transition that
    # matters most here: entering an ATS stays reviewed, which is the standing rule that clicking
    # Apply needs per-prospect approval.
    if stat.key.from_state in TARGET_PARAMETERISED_STATES:
        top = Maturity.DEMONSTRATED.value if stat.oks >= 1 else Maturity.UNSEEN.value
        return top, ("the action on this page depends on which item is being pursued, so it is "
                     "capped below autonomy no matter how often it works")

    if (stat.oks >= CERTIFIED_OKS
            and stat.supervised_nominal_tail >= CERTIFIED_CLEAN_TAIL
            and stat.clean_reviews >= 1):
        return (Maturity.CERTIFIED.value,
                f"{stat.oks} verified, last {stat.supervised_nominal_tail} supervisor-clean, "
                f"{stat.clean_reviews} approved without correction")

    if stat.oks >= TESTING_OKS and stat.local_oks >= 1 and not stat.last_failed:
        missing = ("no supervisor-clean run yet"
                   if stat.supervised_nominal_tail < CERTIFIED_CLEAN_TAIL
                   else "not yet approved unmodified under review")
        return (Maturity.TESTING.value,
                f"{stat.oks} verified, {stat.local_oks} driven locally — but {missing}")

    if stat.has_program and not stat.program_stale:
        return Maturity.REPLAYABLE.value, "a compiled program covers this step"
    if stat.oks >= REPLAYABLE_OKS:
        return Maturity.REPLAYABLE.value, f"{stat.oks} verified attempts — enough to try locally"
    if stat.teacher_oks >= 1 or stat.oks >= 1:
        return Maturity.DEMONSTRATED.value, "completed on the record, but only once"

    return Maturity.UNSEEN.value, f"{stat.attempts} attempts, none verified"


def _regressed(stat: TransitionStat, graded: str) -> bool:
    """Was working, just stopped. Distinct from UNSEEN: we hold prior knowledge that is now
    suspect, which calls for inspecting a change rather than teaching from scratch."""
    if stat.program_stale and stat.oks >= 1:
        return True
    return stat.last_failed and maturity_rank(graded) >= maturity_rank(Maturity.REPLAYABLE.value)


# --- derive -------------------------------------------------------------------------
def derive(rows: Iterable[dict[str, Any]],
           programs: Optional[Iterable[programs_mod.IntentProgram]] = None
           ) -> dict[str, TransitionStat]:
    """`(journal rows, programs) -> {transition id: TransitionStat}`. PURE, offline-testable.

    Rows arrive oldest-first (journal order), which the tail-sensitive counters rely on.
    """
    index = _program_index(programs)
    stats: dict[str, TransitionStat] = {}

    for row in rows:
        key = key_for_row(row)
        if key is None:
            continue
        stat = stats.get(key.as_str())
        if stat is None:
            stat = TransitionStat(key=key)
            stats[key.as_str()] = stat

        stat.attempts += 1
        stat.task = stat.task or (row.get("task") or "")
        stat.platform = stat.platform or (row.get("ats") or "")
        ok = _is_ok(row)
        corrected = bool(row.get("golden"))
        if corrected:
            # A golden row is a CORRECTION: the teacher had to rewrite the proposal. It is
            # re-stamped rung="teacher" by `teach.correct()`, so it must be counted here rather
            # than inferred from the rung, or every correction would read as a teacher success.
            stat.corrections += 1
        if ok:
            stat.oks += 1
            stat.last_failed = False
            if row.get("rung") == "teacher":
                stat.teacher_oks += 1
            else:
                stat.local_oks += 1
            # A clean propose-approve run: reviewed BECAUSE the transition was not yet certified,
            # and acted without the teacher rewriting it. Keyed on the journaled control mode
            # rather than the rung, because under mode gating a rung-0 program step is reviewed
            # too when its transition has not earned GREEN — the review is a property of the
            # TURN, not of who proposed. Rows predating the column contribute nothing, which is
            # why history alone certifies nothing.
            if row.get("control_mode") == ControlMode.YELLOW.value and not corrected:
                stat.clean_reviews += 1
            landed = row.get("landed_state")
            if landed and landed not in stat.landings:
                stat.landings = stat.landings + (landed,)
        else:
            stat.failures += 1
            stat.last_failed = True

        # The clean tail is CONSECUTIVE and most-recent: one non-nominal row resets it, which is
        # what stops a transition certifying on an old good streak it has since broken.
        if _is_supervised_nominal(row):
            stat.supervised_nominal_tail += 1
        else:
            stat.supervised_nominal_tail = 0

    for stat in stats.values():
        program = index.get(stat.key.from_state)
        if program is not None and _program_covers(program, stat.key):
            stat.has_program = True
            stat.program_stale = bool(program.stale)
        graded, why = grade(stat)
        if _regressed(stat, graded):
            stat.maturity = Maturity.REGRESSED.value
            if stat.program_stale:
                stat.reason = ("the compiled program covering this step was marked stale — the "
                               "page changed under it")
            elif stat.oks:
                stat.reason = (f"{stat.oks} verified attempts, then the most recent one failed")
            else:
                stat.reason = ("a compiled program covers this step but the most recent attempt "
                               "failed")
        else:
            stat.maturity, stat.reason = graded, why

    # A compiled program is evidence even for a step the journal never recorded under this key —
    # rows predating a column, or a program hand-compiled from an older corpus. Surface those as
    # REPLAYABLE rather than UNSEEN, so a proven program is not silently ignored.
    for state, program in index.items():
        for step in program.steps:
            params = step.get("params") if isinstance(step.get("params"), dict) else {}
            ref = str(params.get("field") or params.get("control") or "")
            intent = step.get("intent")
            if not intent or intent in NO_OP_INTENTS:
                continue
            key = TransitionKey(from_state=state, intent=intent, ref=ref)
            if key.as_str() in stats:
                continue
            stats[key.as_str()] = TransitionStat(
                key=key, task=program.task, has_program=True,
                program_stale=bool(program.stale),
                landings=tuple(program.expected_exit),
                maturity=(Maturity.REGRESSED.value if program.stale
                          else Maturity.REPLAYABLE.value),
                reason=("the compiled program covering this step is stale" if program.stale
                        else "a compiled program covers this step (no rows under this key)"),
            )
    return stats


# --- the live registry: a cached view, invalidated by mtime -------------------------
class MaturityRegistry:
    """A `derive()` result the loop can query per turn without re-reading the corpus every step.

    Invalidated by the journal's and the programs dir's mtime, so a drive that journals a row sees
    its own progress on the next turn — a transition really can be promoted mid-drive, which is
    what "re-evaluate local control after every teacher action" requires.
    """

    def __init__(self, *, ttl_rows: Optional[int] = None) -> None:
        self._lock = threading.Lock()
        self._stats: dict[str, TransitionStat] = {}
        #: scenario id -> the by_scenario entry from `metrics.shadow_agreement`. Computed in the
        #: SAME refresh, off the SAME rows, so the promotion standing can never disagree with the
        #: maturity view about what the corpus says — the module header's "a registry that can
        #: disagree with the journal will eventually disagree with the journal", applied to the
        #: second evidence source rather than re-learned later.
        self._scenarios: dict[str, dict[str, Any]] = {}
        self._stamp: Optional[tuple[float, float]] = None
        self._loaded = False
        self._ttl_rows = ttl_rows

    # -- internals
    @staticmethod
    def _mtimes() -> tuple[float, float]:
        from interaction import decision_journal
        try:
            journal = decision_journal._path().stat().st_mtime
        except OSError:
            journal = 0.0
        try:
            progs = max((p.stat().st_mtime
                         for p in programs_mod._programs_dir().glob("*.json")), default=0.0)
        except OSError:
            progs = 0.0
        return journal, progs

    def refresh(self, *, force: bool = False) -> None:
        from interaction import decision_journal
        stamp = self._mtimes()
        with self._lock:
            if not force and self._loaded and stamp == self._stamp:
                return
            rows = decision_journal.read_rows(limit=self._ttl_rows)
            self._stats = derive(rows)
            # Free: the same rows, already in memory. Imported here rather than at module scope
            # to keep `maturity` importable by the offline suites without pulling the metric in.
            from controller import metrics as metrics_mod
            report = metrics_mod.shadow_agreement(rows)
            self._scenarios = {s["scenario"]: s for s in report.get("by_scenario", ())}
            self._stamp = stamp
            self._loaded = True

    # -- the API the loop uses
    def get(self, key: TransitionKey) -> str:
        """This transition's maturity. UNSEEN for anything unknown — the safe default, and the
        one the `authority()` truth table is built around."""
        self.refresh()
        stat = self._stats.get(key.as_str())
        return stat.maturity if stat else Maturity.UNSEEN.value

    def stat(self, key: TransitionKey) -> Optional[TransitionStat]:
        self.refresh()
        return self._stats.get(key.as_str())

    def all(self) -> dict[str, TransitionStat]:
        self.refresh()
        return dict(self._stats)

    def standing_for(self, ats: Optional[str], state: Optional[str]) -> PromotionStanding:
        """Has this scenario cleared the two-bar promotion gate? The unit is per-state per-ATS,
        which is what CONTROLLER_PROMOTION.md has always specified the promotion unit to be.

        A scenario nobody has measured returns `unmeasured()`, which BLOCKS autonomy — the
        registry's own "absence of evidence is the strictest answer" rule. The detail string is
        written to be readable in an operator-facing refusal, naming the failing bar AND its n,
        because "not promoted" without a number is not actionable.
        """
        from controller import metrics as metrics_mod

        self.refresh()
        row = self._scenarios.get(metrics_mod.scenario_key(ats, state))
        if row is None:
            return PromotionStanding.unmeasured()

        n, ex_n = row.get("n", 0), row.get("exact_n", 0)
        loose, exact = row.get("loose_agreement", 0.0), row.get("exact_agreement", 0.0)
        if row.get("eligible"):
            return PromotionStanding(
                measured=True, eligible=True,
                detail=(f"loose {loose:.0%} over {n}, exact {exact:.0%} over {ex_n}"))

        # Name the FIRST unmet requirement, with its number. Windows before rates: "not enough
        # evidence yet" and "measured and failing" are different problems with different fixes.
        if n < metrics_mod.PROMOTION_MIN_N:
            why = f"only {n} paired rows, needs {metrics_mod.PROMOTION_MIN_N}"
        elif ex_n < metrics_mod.PROMOTION_MIN_EXACT_N:
            why = (f"only {ex_n} of {n} rows can testify about which control was chosen, "
                   f"needs {metrics_mod.PROMOTION_MIN_EXACT_N}")
        elif loose < metrics_mod.PROMOTION_LOOSE_BAR:
            why = (f"loose agreement {loose:.0%} over {n}, "
                   f"needs {metrics_mod.PROMOTION_LOOSE_BAR:.0%}")
        else:
            why = (f"exact agreement {exact:.0%} over {ex_n}, "
                   f"needs {metrics_mod.PROMOTION_EXACT_BAR:.0%} — the controller agrees on the "
                   f"verb but not on which control")
        return PromotionStanding(measured=True, eligible=False, detail=why)


#: The process-wide registry. One instance so the mtime check is shared; it holds no state a
#: restart would lose, because there is nothing to lose — it is a view.
_REGISTRY = MaturityRegistry()


def registry() -> MaturityRegistry:
    return _REGISTRY


def key_for(state: str, intent: str, params: Optional[dict] = None) -> TransitionKey:
    """The key for a decision about to be made — the same derivation `key_for_row` applies to a
    journaled one, so the lookup and the evidence can never disagree about identity."""
    p = params or {}
    return TransitionKey(from_state=state, intent=intent,
                         ref=str(p.get("field") or p.get("control") or ""))


# --- the coverage table -------------------------------------------------------------
def coverage(rows: Optional[Iterable[dict[str, Any]]] = None,
             programs: Optional[Iterable[programs_mod.IntentProgram]] = None) -> dict[str, Any]:
    """The operator-facing map: every transition, grouped by platform, with its maturity.

    This is the artifact that makes "ten Workday sections are proven and one is not" a number
    instead of a feeling — and the reason the system can be USED while construction continues.
    """
    if rows is None and programs is None:
        stats = registry().all()
    else:
        from interaction import decision_journal
        stats = derive(rows if rows is not None else decision_journal.read_rows(), programs)

    by_platform: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {m: 0 for m in (x.value for x in Maturity)}
    for stat in stats.values():
        counts[stat.maturity] = counts.get(stat.maturity, 0) + 1
        platform = _platform_of(stat.key.from_state, stat.platform)
        by_platform.setdefault(platform, []).append(stat.as_dict())

    for entries in by_platform.values():
        entries.sort(key=lambda e: (-maturity_rank(e["maturity"]), e["id"]))

    total = len(stats)
    certified = counts.get(Maturity.CERTIFIED.value, 0)
    return {
        "total": total,
        "counts": counts,
        "certified_share": round(certified / total, 3) if total else 0.0,
        "by_platform": dict(sorted(by_platform.items())),
    }


def _platform_of(state: str, ats: str = "") -> str:
    """Which vendor this transition belongs to, for grouping the coverage table.

    Derived from the STATE first and the `ats` column only as a fallback, which is the opposite of
    the obvious order and is deliberate: the real corpus carries `ats="indeed_quick_apply"` on some
    rows and `ats="indeed"` on others, so trusting that column splits one vendor across two
    headings — the same free-text drift that used to split the transition keys themselves.
    `perception.facets.platform_for` owns the derivation; this is not a second copy of it.
    """
    try:
        from perception.facets import platform_for
        derived = platform_for(state or "")
    except Exception:  # noqa: BLE001 — grouping a table must never break a drive
        derived = ""
    if derived and derived != "unknown":
        return derived
    return ats or (state or "").split("_", 1)[0] or "unknown"
