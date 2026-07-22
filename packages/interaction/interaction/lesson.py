"""The Lesson — what an escalation must RETURN, so it is paid for once.

An escalation that returns a click is a session paid for twice. Claude teaching the same Workday
page across ten tenants is the failure this contract exists to prevent
(`PLAN_perception_v1.md` §3.5, absorbed here).

Two ideas, and they are the whole file:

**Scope.** A lesson says how far it generalises — `universal`, `platform:workday`, `tenant:acme`.
Lookups walk the chain from most specific to least, so a brand-new Workday tenant inherits
everything already learned about Workday and only pays for what is genuinely its own. Without a
scope, every lesson is either over-applied (a tenant quirk breaks nine other tenants) or
under-applied (the same page taught ten times), and there is no third option.

**Verification before acceptance.** A lesson is a PREDICTION until the step it describes actually
works. `accept()` refuses an unverified one, so the corpus cannot fill with confident teaching
that never landed — the supervisor already decides what "worked" means, and this defers to it
rather than inventing a second answer.

This is PRINCIPLES §10 given a delivery address: §10 says the teacher reasons on the record; the
lesson says *where the record goes so it is reused instead of re-derived*.

Frozen and versioned like `contract.py` / `decision.py` / `belief.py` / `supervision.py`.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

LESSON_SCHEMA_VERSION = "v1"


class LessonKind(str, Enum):
    """WHAT was learned — a CLOSED set, so a lesson has a delivery address rather than being prose
    in a rationale field. Each member names the artifact it is destined for."""

    #: "this page is `workday_questions`" -> the page-state registry / candidate promotion.
    STATE_LABEL = "state_label"
    #: "this form's 'Mobile phone' is our `phone`" -> the alias table `resolve_answer` reads.
    FIELD_ALIAS = "field_alias"
    #: "from here, Continue lands on voluntary disclosures" -> the recipe's `expect` edges.
    RECIPE_EDGE = "recipe_edge"
    #: "when THIS fails this way, do THAT" -> a `RecoveryPlay` binding for a failure class.
    RECOVERY_RULE = "recovery_rule"
    #: "this tenant renames / reorders / adds a step" -> an override on a (platform, phase).
    TENANT_PATCH = "tenant_patch"
    #: "the executor cannot operate this widget" -> the ENDPOINT BACKLOG. The kind this plan adds,
    #: and the one that turns "the observer is great until we can't do anything about it" from a
    #: complaint into a work item. Its payload is a `reach` gap, and its resolution is code.
    CAPABILITY_GAP = "capability_gap"


#: Scope prefixes. `universal` has no value; the other two carry one.
SCOPE_UNIVERSAL = "universal"
SCOPE_PLATFORM = "platform"
SCOPE_TENANT = "tenant"


def platform_scope(platform: str) -> str:
    return f"{SCOPE_PLATFORM}:{platform.strip().lower()}"


def tenant_scope(tenant: str) -> str:
    return f"{SCOPE_TENANT}:{tenant.strip().lower()}"


def parse_scope(scope: str) -> tuple[str, str]:
    """`"platform:workday"` -> `("platform", "workday")`; `"universal"` -> `("universal", "")`."""
    kind, _, value = (scope or SCOPE_UNIVERSAL).partition(":")
    return kind.strip().lower(), value.strip().lower()


def scope_chain(*, platform: str = "", tenant: str = "") -> tuple[str, ...]:
    """The scopes to consult, MOST SPECIFIC FIRST.

    This ordering is the generalisation lever. A new Workday tenant arrives carrying
    `("tenant:acme", "platform:workday", "universal")`, so it inherits every Workday lesson for
    free and only pays for what is actually different about it. Reversing the order would let a
    universal default silently beat a tenant's own correction.
    """
    chain: list[str] = []
    if tenant:
        chain.append(tenant_scope(tenant))
    if platform:
        chain.append(platform_scope(platform))
    chain.append(SCOPE_UNIVERSAL)
    return tuple(chain)


def scope_rank(scope: str) -> int:
    """Specificity, higher = more specific. Used to pick a winner among matching lessons."""
    kind, _ = parse_scope(scope)
    return {SCOPE_UNIVERSAL: 0, SCOPE_PLATFORM: 1, SCOPE_TENANT: 2}.get(kind, 0)


@dataclass(frozen=True)
class Lesson:
    """One reusable thing learned from one escalation."""

    kind: str                              # a LessonKind value
    scope: str                             # universal | platform:<x> | tenant:<y>
    subject: str                           # WHAT it is about — a state id, a field name, a widget
    payload: dict = field(default_factory=dict)   # the content, shaped by `kind`
    rationale: str = ""                    # the teacher's why (§10) — held to is_real_rationale
    evidence: tuple[str, ...] = ()         # the receipts it cites
    #: Set only by `accept()`, and only once the prediction it made actually verified. An
    #: unverified lesson is a hypothesis; treating one as knowledge is how a corpus fills with
    #: confident teaching that never worked.
    verified_at: str = ""
    source: str = ""                       # session id / handoff id — provenance travels (§1)
    schema_version: str = LESSON_SCHEMA_VERSION

    @property
    def accepted(self) -> bool:
        return bool(self.verified_at)

    def cache_key(self) -> str:
        """What makes ten tenants teach the same page ONCE: `(kind, subject)` without the scope.

        The scope is deliberately absent. It decides *who inherits* the answer, not *what question
        was asked* — and the question "what is this Workday sponsorship field?" is the same
        question at every tenant. Including the scope here would give each tenant its own cache
        entry and re-buy the same lesson, which is the exact cost this contract exists to remove.
        """
        return f"{self.kind}|{self.subject.strip().lower()}"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence"] = list(self.evidence)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Lesson":
        return cls(kind=d.get("kind", ""), scope=d.get("scope", SCOPE_UNIVERSAL),
                   subject=d.get("subject", ""), payload=dict(d.get("payload") or {}),
                   rationale=d.get("rationale", ""),
                   evidence=tuple(d.get("evidence") or ()),
                   verified_at=d.get("verified_at", ""), source=d.get("source", ""),
                   schema_version=d.get("schema_version", LESSON_SCHEMA_VERSION))


class LessonRejected(ValueError):
    """A lesson that may not be accepted, and why. Loud rather than silently dropped: a lesson
    that vanishes is an escalation paid for and lost, which is the thing we are fixing."""


def accept(lesson: Lesson, *, verified: bool, when: Optional[str] = None) -> Lesson:
    """Stamp a lesson as knowledge — ONLY if the step it describes actually worked.

    `verified` comes from the supervisor's verdict on the action the lesson produced, never from
    the teacher's own confidence. The teacher is the source of the hypothesis; the page is the
    judge of it. Raises rather than returning None so a caller cannot ignore the refusal.
    """
    if not verified:
        raise LessonRejected(
            f"{lesson.kind}:{lesson.subject!r} did not verify — a lesson is a prediction until "
            f"the step it describes works, and unverified teaching is how a corpus fills with "
            f"confident wrong answers")
    if lesson.kind not in {k.value for k in LessonKind}:
        raise LessonRejected(f"unknown lesson kind {lesson.kind!r}")
    if not lesson.subject.strip():
        raise LessonRejected("a lesson with no subject has no delivery address")
    from interaction.decision import is_real_rationale
    if not is_real_rationale(lesson.rationale):
        raise LessonRejected(
            "a lesson must carry real reasoning (§10) — a placeholder 'why' teaches WHAT with no "
            "rule to generalise from, which is a label rather than a lesson")
    return replace(lesson, verified_at=when or datetime.now(timezone.utc).isoformat())


# --- the store: append-only, beside the journals ------------------------------------
_lock = threading.Lock()
_LESSONS_NAME = "lessons.jsonl"


def _default_artifacts_dir() -> Path:
    env = os.environ.get("INTERACTION_ARTIFACTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "apps" / "mcp" / "output"


def _path() -> Path:
    p = _default_artifacts_dir() / "cache" / _LESSONS_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write(lesson: Lesson) -> Lesson:
    """Append an ACCEPTED lesson. Refuses an unaccepted one rather than storing a hypothesis."""
    if not lesson.accepted:
        raise LessonRejected("write() takes an accepted lesson — call accept() first")
    with _lock:
        with _path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(lesson.as_dict(), ensure_ascii=False) + "\n")
    return lesson


def read_all() -> list[Lesson]:
    p = _path()
    if not p.exists():
        return []
    out: list[Lesson] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Lesson.from_dict(json.loads(line)))
        except Exception:  # noqa: BLE001 — one bad line must not blind the whole store
            continue
    return out


def lookup(kind: str, subject: str, *, platform: str = "", tenant: str = "",
           lessons: Optional[list[Lesson]] = None) -> Optional[Lesson]:
    """The lesson that applies here, or None — MOST SPECIFIC scope wins, newest breaks a tie.

    This is the "teach once" half of the contract. `known()` below is what an escalation path
    should call BEFORE paying the teacher; a hit means the answer is already bought.
    """
    chain = scope_chain(platform=platform, tenant=tenant)
    key = f"{kind}|{subject.strip().lower()}"
    candidates = [l for l in (lessons if lessons is not None else read_all())
                  if l.cache_key() == key and l.scope in chain]
    if not candidates:
        return None
    return max(candidates, key=lambda l: (scope_rank(l.scope), l.verified_at))


def known(kind: str, subject: str, *, platform: str = "", tenant: str = "",
          lessons: Optional[list[Lesson]] = None) -> bool:
    """Have we already been taught this? The check that turns "teacher calls per submitted
    application" — the one number this plan has to bend — into something that can fall."""
    return lookup(kind, subject, platform=platform, tenant=tenant, lessons=lessons) is not None


def summarize(lessons: Optional[list[Lesson]] = None) -> dict[str, Any]:
    """Counts by kind and scope — the reuse scoreboard. If lessons accumulate while teacher calls
    per application stay flat, the scope or the cache key is wrong, not the models
    (`PLAN_perception_v1` §8's falsifier, now measurable)."""
    rows = lessons if lessons is not None else read_all()
    by_kind: dict[str, int] = {}
    by_scope_kind: dict[str, int] = {}
    for l in rows:
        by_kind[l.kind] = by_kind.get(l.kind, 0) + 1
        sk, _ = parse_scope(l.scope)
        by_scope_kind[sk] = by_scope_kind.get(sk, 0) + 1
    return {"total": len(rows), "by_kind": by_kind, "by_scope": by_scope_kind,
            "distinct_subjects": len({l.cache_key() for l in rows})}
