"""Apply steps — one pick, one step, and a ladder whose shape is discovered while climbing it.

Operator, after picking jobs off page 1: *"if i check off 11 jobs that's 11 steps and i don't
continue until i fully apply."* So choosing does not complete a page — it **enqueues work**, and
`page:N` stays open until every pick has reached a terminal flag.

This is the search ladder's idea (`session_checkpoints`) at a second scale, with one genuinely new
property. The search preamble is four rungs known before you start. An apply is not:

    open pane -> verify identity -> enter apply -> [ ? ] -> ... -> submit
                                                    ^
                                    the platform is only knowable HERE

You cannot tell from a results card whether a job is an Indeed in-app apply or a hop to Workday,
Greenhouse, or something nobody has driven. So the ladder has a known PREFIX, a **discovery
point**, and a tail that does not exist until the discovery happens. A step is a ladder that grows
while you are on it.

**Why every mini-step carries a flag.** Operator: *"each mini-step in each step will need a flag
because this is some uncharted territory especially for newer ats."* Exactly so — and the flag that
earns this module is `UNKNOWN`. A mini-step we cannot classify must record that it could not be
classified, because the alternative is a gap that reads identically to a step that went fine. This
is the same failure the challenge pre-gate had: "found nothing" and "could not look" are not the
same answer, and only one of them is safe to continue from.

Pure: no I/O, no browser, no DB. `routers/session_control.py` executes what this decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

# --- mini-step outcome flags -------------------------------------------------------------------
OK = "ok"                        # did what it set out to do
BLOCKED = "blocked"              # captcha / challenge — escalate, never solve
HUMAN_REQUIRED = "human_required"  # a branch only a person may take (2FA, AI recruiter, password)
UNKNOWN = "unknown"              # WE DO NOT RECOGNISE THIS. Not a failure — an admission.
FAILED = "failed"                # tried, did not work, and we know why
SKIPPED = "skipped"              # NOT NEEDED HERE, and that is a real answer. Greenhouse takes an
                                 # application without an account, so `account` is skipped rather
                                 # than left undone — an unwalked rung stalls the ladder forever.

#: Flags that mean the step cannot proceed on its own. `UNKNOWN` is here deliberately: not
#: recognising a screen is a reason to stop and ask, never a reason to press on hopefully.
NEEDS_OPERATOR = frozenset({BLOCKED, HUMAN_REQUIRED, UNKNOWN})

# --- terminal flags: how a step ENDS ------------------------------------------------------------
#: The only success. An application is done when it is SUBMITTED and confirmed — not when the form
#: is full, not when we reached the last page.
SUBMITTED = "submitted"

#: PARKED — genuinely unfinishable right now, by a cause the operator owns. The step is over for
#: this session and stops holding the page, but it is not "done" and it is not forgotten.
PARKED_ACCOUNT_WALL = "parked:account_wall"      # an ATS account only the operator may create
PARKED_AI_RECRUITER = "parked:ai_recruiter"      # a video/audio interview gate
PARKED_ASSESSMENT = "parked:assessment"          # a survey / skills test
PARKED_UNKNOWN_ATS = "parked:unknown_ats"        # nobody has driven this platform yet
PARKED_OPERATOR = "parked:operator"              # the operator's own call, reason recorded

#: ABANDONED — nothing to apply to. Distinct from parked: parked means "not now", abandoned means
#: "not ever", and conflating them puts dead requisitions back in the queue forever.
ABANDONED_GONE = "abandoned:ats_unavailable"     # the posting outlived its requisition
ABANDONED_OPERATOR = "abandoned:operator"        # the operator does not want it after seeing it

TERMINAL_FLAGS = frozenset({
    SUBMITTED, PARKED_ACCOUNT_WALL, PARKED_AI_RECRUITER, PARKED_ASSESSMENT,
    PARKED_UNKNOWN_ATS, PARKED_OPERATOR, ABANDONED_GONE, ABANDONED_OPERATOR,
})

#: Terminal flags the operator must choose deliberately. `ABANDONED_GONE` is absent because a 404
#: requisition is an observed fact, not a judgement call — asking about it would be theatre.
OPERATOR_FLAGS = frozenset(TERMINAL_FLAGS - {SUBMITTED, ABANDONED_GONE})

STATUS_QUEUED, STATUS_OPEN, STATUS_DONE = "queued", "open", "done"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- the ladder's known prefix ------------------------------------------------------------------
@dataclass(frozen=True)
class MiniRung:
    id: str
    label: str
    why: str


#: Every apply starts the same way, whatever it turns into.
PREFIX: tuple[MiniRung, ...] = (
    MiniRung("open_pane", "Open the posting",
             "Click the card so the detail pane loads — never a viewjob URL jump."),
    MiniRung("verify_identity", "Confirm it is the intended job",
             "The near-miss guard. A pane can show a different posting than the card you clicked, "
             "and an application to the wrong job cannot be taken back."),
    MiniRung("enter_apply", "Enter the application",
             "Click Apply. Approval for this specific job came from the operator's pick."),
    MiniRung("classify", "Work out where we landed",
             "THE DISCOVERY POINT. Indeed in-app, a known ATS, or somewhere new — the rungs after "
             "this one do not exist until this is answered."),
    MiniRung("account", "Get past the account wall",
             "Most ATS want an identity before they will take an application — an email, then a "
             "profile. It was happening OFF the ladder: an account handoff appeared beside the "
             "queue and the step itself showed nothing between 'we landed' and 'submit', so the "
             "one part with a credential in it was the one part that left no trace. Skipped "
             "cleanly on platforms that need no account (Greenhouse)."),
)

#: The last rung of every apply, whatever the middle turned out to be. Never automatic.
SUBMIT_RUNG = MiniRung(
    "submit", "Submit",
    "The irreversible one. The operator confirms every submission, on every platform, always.")

#: Platforms that take an application WITHOUT an identity of their own. Indeed's quick apply runs
#: inside the session we are already signed into; Greenhouse asks for nothing.
#:
#: THIS LIVES BESIDE THE LADDER because it decides whether a RUNG EXISTS, and it was living in a
#: router while a second surface answered the same question differently. Live 2026-07-30: classify
#: correctly said `platform=indeed`, the account rung would have skipped cleanly on execution — and
#: the cockpit still rendered a "Create Account automatically" handoff for BRISTOL COUNTY SAVINGS
#: BANK over a page that was the finished review module with the resume already uploaded. The rung
#: knew and the read model did not. One authority, consulted by both.
NO_ACCOUNT_PLATFORMS = frozenset({"greenhouse", "indeed", "indeed_quick_apply"})


def rung_applies(rung_id: str, *, platform: Optional[str]) -> tuple[bool, str]:
    """Does this rung EXIST for the landing we discovered? -> (applies, why-not).

    `classify` is documented as the discovery point — "the rungs after this one do not exist until
    this is answered" — and then the ladder walked a fixed tuple regardless of the answer, which is
    what the operator named as falling back into recipe mode the moment the reasoner had spoken.
    This is the seam where the discovery is allowed to change the ladder.

    A rung that does not apply is SKIPPED WITH A REASON, never silently dropped: the panel still
    shows it, greyed, saying why. A ladder that quietly loses a step reads as a ladder that never
    had one.
    """
    if rung_id == "account" and (platform or "") in NO_ACCOUNT_PLATFORMS:
        return False, f"{platform} takes an application without an account of its own"
    return True, ""


PREFIX_IDS = tuple(r.id for r in PREFIX)


@dataclass
class MiniStep:
    """One recorded mini-step: what we tried, and what came of it. The flag is the point."""

    rung: str
    outcome: str
    detail: str = ""
    at: str = field(default_factory=_utcnow)
    initiator: str = "operator"
    #: Did THIS mini-step put input into the page? The rung alone cannot say: `account` types
    #: credentials in "auto"/"fill" and types nothing at all in "handoff", and the two are
    #: distinguishable only by prose in `detail` — which is not something a reader may depend on.
    #: None means UNSTATED, which is every mini-step written before this field existed and every
    #: rung whose answer is the same every time; readers fall back to judging by rung there.
    staged: Optional[bool] = None

    def as_dict(self) -> dict[str, Any]:
        return {"rung": self.rung, "outcome": self.outcome, "detail": self.detail,
                "at": self.at, "initiator": self.initiator, "staged": self.staged}


@dataclass
class ApplyStep:
    """One pick, from the results card to a terminal flag."""

    job_id: str
    title: str = ""
    company: str = ""
    status: str = STATUS_QUEUED
    platform: Optional[str] = None      # None until `classify` answers it
    #: WHERE we landed, as `platform_kind` (`icims_job_posting`). The platform says whose software
    #: it is; this says what was on the screen. Kept on the step so a re-orientation is recorded
    #: rather than re-derived, and so the corpus gets a state name per landing.
    landing_state: Optional[str] = None
    terminal: Optional[str] = None      # one of TERMINAL_FLAGS once done
    terminal_detail: str = ""
    minis: list[MiniStep] = field(default_factory=list)
    #: Previous attempts, moved aside by `reopen`. A parked step that comes back re-walks the
    #: ladder, and the run that parked it is kept here rather than deleted — the first attempt is
    #: what makes the second one legible as a correction.
    archived_minis: list[dict[str, Any]] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return self.terminal is not None

    @property
    def last_flag(self) -> Optional[str]:
        return self.minis[-1].outcome if self.minis else None

    def needs_operator(self) -> bool:
        """True when the step is stuck on something only a person may resolve."""
        return not self.done and self.last_flag in NEEDS_OPERATOR

    def next_rung(self) -> Optional[MiniRung]:
        """The next prefix rung, or None once the prefix is walked (the tail is discovered).

        A rung is also considered walked when it was explicitly SKIPPED — `account` on a platform
        that needs no account is not an omission, and a ladder that kept asking for it would stall
        every Greenhouse application forever.

        THE LATEST VERDICT WINS, NOT THE BEST ONE EVER RECORDED. This read `any OK ever`, so a rung
        settled the first time anything said ok and could never be reopened by a later correction.
        Live 2026-07-30: `account` was recorded ok while the browser was still on a careers-front
        job posting; the guard that now re-reads the page recorded `account unknown` afterwards —
        and the ladder went on reporting the prefix as walked, so the panel sat on the wrong step
        showing a sign-in card for a wall that was not on screen. The operator's words: "our ui is
        on the wrong step".

        Every mini is still kept, both sides of every correction (PRINCIPLES §10) — what changes is
        which one the LADDER reads. A record that cannot be corrected is not a record of the world,
        it is a record of the first thing we believed about it.
        """
        settled = self._settled_rungs()
        for rung in PREFIX:
            if rung.id not in settled:
                return rung
        return None

    def _settled_rungs(self) -> set[str]:
        """The prefix rungs this step counts as walked — the LATEST verdict for each, never the
        best one ever recorded. `next_rung` explains why that distinction is load-bearing."""
        latest: dict[str, str] = {}
        for m in self.minis:
            latest[m.rung] = m.outcome
        return {rung for rung, outcome in latest.items() if outcome in (OK, SKIPPED)}

    def walk_to_next_rung(self) -> tuple[Optional[MiniRung], list[tuple[str, str]]]:
        """The rung to PRESENT next, and the ruled-out rungs passed on the way — each (id, why).

        `next_rung` says where the ladder stands; this says what the crank will actually hand the
        operator, and they differ exactly when the discovery has ruled a rung out. The crank walks
        past those, RECORDING a skip for each (the skip is an event, not an absence); a read model
        must reach the same answer without writing anything. So the walk lives here once and only
        the recording stays with the caller — two copies of this walk is how the read model and the
        rung came to disagree about the account wall in the first place (2026-07-30).
        """
        passed: list[tuple[str, str]] = []
        settled = self._settled_rungs()
        for rung in PREFIX:
            if rung.id in settled:
                continue
            applies, why_not = rung_applies(rung.id, platform=self.platform)
            if applies:
                return rung, passed
            passed.append((rung.id, why_not))
        return None, passed

    def inapplicable_rungs(self) -> list[dict[str, str]]:
        """Prefix rungs the discovery ruled out, and why — so the panel can show them greyed
        rather than have them vanish. Silently dropping a rung reads as never having had one."""
        out: list[dict[str, str]] = []
        for rung in PREFIX:
            applies, why = rung_applies(rung.id, platform=self.platform)
            if not applies:
                out.append({"id": rung.id, "label": rung.label, "why": why})
        return out

    def record(self, rung: str, outcome: str, detail: str = "",
               initiator: str = "operator", staged: Optional[bool] = None) -> MiniStep:
        mini = MiniStep(rung=rung, outcome=outcome, detail=detail, initiator=initiator,
                        staged=staged)
        self.minis.append(mini)
        if self.status == STATUS_QUEUED:
            self.status = STATUS_OPEN
        return mini

    def finish(self, flag: str, detail: str = "") -> None:
        if flag not in TERMINAL_FLAGS:
            raise ValueError(f"{flag!r} is not a terminal flag; have {sorted(TERMINAL_FLAGS)}")
        self.terminal = flag
        self.terminal_detail = detail
        self.status = STATUS_DONE

    def reopen(self, reason: str, initiator: str = "operator") -> None:
        """Bring a PARKED step back into the queue. The other half of the parked/abandoned split.

        This module has always said parked means "not now" and abandoned means "not ever" — but
        nothing could act on the difference. `enqueue` refuses to re-add a known job_id (rightly:
        it must not double the work), `done` is true for any terminal flag, and there was no way
        back. So the two flags behaved identically and the distinction was decoration. Found when
        the operator's TOP-PRIORITY pick sat parked with its own note saying "re-queue after the
        matcher fix" — and the matcher fix had landed (2026-07-27).

        The walked rungs are ARCHIVED, not kept. A step comes back in a later session where the
        pane is not open and the page has moved on, so the prefix must be re-walked from the top;
        and the specific reason this one parked was an `enter_apply` that recorded OK for the wrong
        company's card. **Carrying forward a rung whose answer we no longer trust is worse than
        re-walking it.** The history stays on the step so the second attempt can be compared with
        the first — that is the pair a corrected mistake is worth.

        Refuses an ABANDONED step: "not ever" is a decision, and quietly reversing it is how dead
        requisitions come back forever.
        """
        if self.terminal is None:
            raise ValueError("this step is not finished, so there is nothing to reopen")
        if not self.terminal.startswith("parked:"):
            raise ValueError(f"only a PARKED step can be reopened; this one is {self.terminal!r}. "
                             f"Abandoned means not ever — re-pick the job if that has changed.")
        self.archived_minis.append({
            "parked_as": self.terminal, "parked_detail": self.terminal_detail,
            "reopened_at": _utcnow(), "reason": reason,
            "minis": [m.as_dict() for m in self.minis],
        })
        self.minis = []
        self.terminal = None
        self.terminal_detail = ""
        self.status = STATUS_QUEUED
        self.platform = None
        self.landing_state = None
        # `staged=False` because a reopened step has just had its rungs archived away: it is back
        # at the top of the ladder with nothing of ours in any page. Left unstated it would read as
        # a rung that typed (the fallback's default for anything not obviously read-only), and a
        # step that has typed nothing must not suppress the panel's reload remedy.
        self.record("reopened", OK, reason, initiator=initiator, staged=False)

    def as_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "title": self.title, "company": self.company,
                "status": self.status, "platform": self.platform,
                "landing_state": self.landing_state, "terminal": self.terminal,
                "terminal_detail": self.terminal_detail, "done": self.done,
                "archived_minis": self.archived_minis,
                "needs_operator": self.needs_operator(),
                # THE RUNG THE CRANK WILL ACTUALLY HAND YOU, not merely where the ladder stands:
                # naming a rung the discovery has ruled out is a read model promising work that
                # will be skipped the moment the button is pressed. The skip stays visible below.
                "next_rung": (nr.id if (nr := self.walk_to_next_rung()[0]) else None),
                # Shown greyed rather than dropped — see inapplicable_rungs.
                "inapplicable_rungs": self.inapplicable_rungs(),
                "minis": [m.as_dict() for m in self.minis]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApplyStep":
        step = cls(job_id=d["job_id"], title=d.get("title", ""), company=d.get("company", ""),
                   status=d.get("status", STATUS_QUEUED), platform=d.get("platform"),
                   landing_state=d.get("landing_state"),
                   terminal=d.get("terminal"), terminal_detail=d.get("terminal_detail", ""))
        step.minis = [MiniStep(**m) for m in d.get("minis", [])]
        step.archived_minis = list(d.get("archived_minis") or [])
        return step


@dataclass
class Queue:
    """The picks from ONE page, as work. Order is the operator's pick order."""

    page: int = 1
    steps: list[ApplyStep] = field(default_factory=list)

    def enqueue(self, picks: list[dict[str, Any]]) -> int:
        """Add picks as steps. Idempotent by job_id — pressing Choose twice must not double the
        work, and must never re-open a step that already finished."""
        known = {s.job_id for s in self.steps}
        added = 0
        for p in picks:
            jid = p.get("job_id")
            if not jid or jid in known:
                continue
            self.steps.append(ApplyStep(job_id=jid, title=p.get("title", ""),
                                        company=p.get("company", "")))
            known.add(jid)
            added += 1
        return added

    def current(self) -> Optional[ApplyStep]:
        """The one step being worked: the first that has not reached a terminal flag.

        Strictly one at a time. Two half-finished applications in one window is the duplicate-
        application fault the tab manager already had to learn to spot.
        """
        for step in self.steps:
            if not step.done:
                return step
        return None

    def blocks_page(self) -> bool:
        """Whether `page:N` may still not be marked. This is the operator's rule in one line:
        do not move on while an application is unfinished."""
        return self.current() is not None

    def summary(self) -> dict[str, Any]:
        by_flag: dict[str, int] = {}
        for s in self.steps:
            if s.terminal:
                by_flag[s.terminal] = by_flag.get(s.terminal, 0) + 1
        return {"page": self.page, "total": len(self.steps),
                "done": sum(1 for s in self.steps if s.done),
                "submitted": sum(1 for s in self.steps if s.terminal == SUBMITTED),
                "remaining": sum(1 for s in self.steps if not s.done),
                "blocks_page": self.blocks_page(), "by_flag": by_flag}

    def as_dict(self) -> dict[str, Any]:
        return {"page": self.page, "steps": [s.as_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, d: Optional[dict[str, Any]]) -> "Queue":
        d = d or {}
        q = cls(page=int(d.get("page", 1)))
        q.steps = [ApplyStep.from_dict(s) for s in d.get("steps", []) if s.get("job_id")]
        return q


# --- is this action even well-formed? ---------------------------------------------------------
#: The params each intent actually needs, mirroring `interaction.contract.Intent` and the keys
#: `controller.live_actuator` reads. Alternatives are given because the actuator accepts several
#: names for the same slot (`control`/`name`/`value` all address a click's target).
#:
#: This exists because of a live failure on 2026-07-24: a proposal was authored as
#: `click {"field": "Apply now"}` — `field` is the SET_TEXT key, not the click key — and nothing
#: checked it. It was stored, rendered as a confident proposal, approved by the operator, and only
#: then discovered at act time as "click with no control name". **Asking someone to approve an
#: action nobody has checked is well-formed makes their approval meaningless**, and it spends their
#: attention on a failure we could have caught for free.
_INTENT_PARAMS: dict[str, tuple[tuple[str, ...], ...]] = {
    "click": (("control", "name", "value"),),
    "submit": (("control", "name"),),
    "set_text": (("field",), ("value",)),
    "select_option": (("field",), ("value",)),
    "set_date": (("field",), ("month",), ("year",)),
    "check_group": (("field",), ("values",)),
    "upload": (("field",), ("path", "files")),
    "scroll": (("direction", "amount", "value"),),
    "describe": (("field",),),
    "observe": (),
    "scan_required": (),
    "resolve_answer": (("question",),),
}


def validate_action(intent: str, params: dict[str, Any]) -> Optional[str]:
    """Why this action cannot be performed as written, or None if it is well-formed.

    Deliberately NOT a guess-and-fix: silently rewriting `field` to `control` would paper over a
    teacher that has the vocabulary wrong, and the vocabulary is the thing the students learn.
    Say what is missing and let it be corrected on the record.
    """
    intent = (intent or "").strip()
    if not intent:
        return "no intent given"
    if intent not in _INTENT_PARAMS:
        return (f"{intent!r} is not in the intent vocabulary. Have: "
                f"{', '.join(sorted(_INTENT_PARAMS))}")
    params = params or {}
    for slot in _INTENT_PARAMS[intent]:
        if not any(str(params.get(k, "")).strip() for k in slot):
            got = ", ".join(f"{k}={v!r}" for k, v in params.items()) or "nothing"
            return (f"{intent!r} needs {' or '.join(slot)}, but got {got}. "
                    f"(`field` addresses a form field; `control` addresses a button.)")
    return None


# --- the discovery point --------------------------------------------------------------------
@dataclass
class Discovery:
    """What the classify rung concluded, and whether we can proceed on it."""

    platform: str
    known: bool          # is there a recipe for this platform?
    outcome: str         # OK when we can proceed; UNKNOWN when a human must teach it
    detail: str
    #: WHAT KIND of page, read from the CONTENT rather than the URL, and the state id the two
    #: compose into (`icims_job_posting`). The platform axis alone could name the vendor and say
    #: nothing about the screen — which is exactly where a real landing stopped, 2026-07-26.
    kind: str = ""
    state: str = ""
    evidence: tuple = ()
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "known": self.known,
                "outcome": self.outcome, "detail": self.detail,
                "kind": self.kind, "state": self.state,
                "evidence": list(self.evidence), "source": self.source}


#: Platforms we have actually driven end to end. Anything else is uncharted, and says so.
#: Deliberately a SHORT list: `ats_registry` recognises far more hosts than we have ever driven,
#: and "we can name it" is not "we can complete it". Claiming a recipe we do not have is how you
#: half-fill a real application.
DRIVEN_PLATFORMS = frozenset({"indeed", "workday", "greenhouse"})


def classify_landing(url: str, page_text: str = "",
                     frames: Optional[list[dict]] = None,
                     apply_hrefs: Optional[list[str]] = None) -> Discovery:
    """Answer the discovery rung: what kind of apply did we land in?

    Delegates identification to `ats_registry` (one source of truth for "which ATS is this") and
    then asks a second, different question that the registry does not answer: **have we ever
    driven it?** An unknown platform is reported as UNKNOWN so the step halts and the operator
    teaches it live, rather than a generic form-filler guessing its way through somebody's real
    job application.

    `apply_hrefs` are where the page's own apply controls POINT. On a company careers FRONT the
    landing host is the employer's and says nothing, while the apply link names the ATS outright —
    measured 2026-07-30: `aholddelhaizeusa.careerswithus.com` halted as UNKNOWN while its APPLY NOW
    pointed at `aholddelhaizeapply.appvault.com`, an ATS the registry already knew. Reading the
    signpost turns a halt into a recognised platform, without loosening what "recognised" means.
    """
    import apply_landing as al
    from ats_registry import classify_ats

    platform = classify_ats(url, {"apply_hrefs": list(apply_hrefs or [])}) or "unknown"

    # WHERE ARE WE, not just whose software is it. Read from the content — and from the FRAME that
    # holds it, because a branded ATS wrapper puts the job somewhere the top document never
    # mentions (iCIMS, live 2026-07-26). The kind is attached to every verdict below, so even a
    # halt says what it was looking at rather than only which vendor it belonged to.
    text, source = al.pick_content(page_text, frames)
    landing = al.classify_kind(text, source=source)
    state = al.landing_state(platform, landing.kind)

    def _d(known: bool, outcome: str, detail: str, *, as_platform: str = "") -> Discovery:
        # `as_platform` keeps smartapply reported as plain "indeed": classify_ats calls it
        # `indeed_quick_apply`, and the caller-facing name has always been the shorter one.
        p = as_platform or platform
        return Discovery(p, known, outcome, detail, kind=landing.kind,
                         state=al.landing_state(p, landing.kind),
                         evidence=landing.evidence, source=landing.source)
    where = f" We are on a {landing.kind.replace('_', ' ')} ({state})." if landing.kind not in (
        al.UNKNOWN, al.UNREADABLE) else ""

    if "smartapply.indeed.com" in (url or "") or platform == "indeed":
        return _d(True, OK, "Indeed's in-app application (smartapply) — the recipe we know best."
                            + where, as_platform="indeed")
    if platform in DRIVEN_PLATFORMS:
        return _d(True, OK, f"{platform}: a platform we have driven before." + where)
    if platform in ("company_site", "unknown", ""):
        return _d(False, UNKNOWN,
                  "This does not match any ATS we recognise. Halting so it can be driven and "
                  "captured rather than guessed at." + where)
    return _d(False, UNKNOWN,
              f"{platform} is recognised by the registry but has never been driven end to end "
              f"here. Naming a platform is not knowing how to finish it." + where)
