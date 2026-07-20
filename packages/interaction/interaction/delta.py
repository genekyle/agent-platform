"""StateDelta — what CHANGED between two consecutive observations. The supervisor's sense organ.

Why this module exists (audited 2026-07-20, `PLAN_supervisor.md` §0a). The system could compare
two page states for *equality* — `fingerprint.compute` hashes the screen, so a mismatch says
"different" — but nothing could say **how** they differed. The one place that needed to know
(`controller/loop.py`'s treadmill guard) approximated it with a 3-tuple of
`(url, state, unanswered-fields)`, which is blind to everything that actually goes wrong: a modal
that opened, an error banner that appeared, a Continue button that went disabled, a widget that
staged a value without committing it.

So: same ingredients, differenced instead of hashed. `fingerprint.ax_summary` already reduces an
AX candidate set to sorted, de-duplicated, volatility-stripped `role|name` identities; a
StateDelta is the set difference over exactly that, plus the cheap scalars the loop already had.

Two rules this module exists to keep:

  * **One definition of "the same control."** Identities come from `fingerprint._normalize_ax_name`
    via `ax_summary`, so the delta and the fingerprint can never disagree about whether
    "Messages (3)" and "Messages (7)" are the same thing. If they could disagree, a state could
    be simultaneously unchanged (fingerprint) and changed (delta), and no diagnosis built on top
    would be trustworthy.
  * **Pure, no IO, no model.** This runs on EVERY turn and must cost nothing — it is rung 0's
    input, and rung 0's whole purpose is to name most failures without spending. Set differences
    over a few hundred short strings.

Not in scope: judging whether the change was GOOD. That is the supervisor's job
(`supervision.py`); this module only reports what moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from interaction.fingerprint import ax_summary, route_template

#: Bump on any change to the MEANING of a field or to `delta_to_prompt`'s FORMAT — the verdict
#: rows journaled under an old shape stop being comparable otherwise. Same discipline as
#: DECISION_SCHEMA_VERSION.
DELTA_SCHEMA_VERSION = "v1"

#: How many identities `delta_to_prompt` names before summarising the rest as a count. The delta
#: is a PROMPT SURFACE for rung 1 and a feature set for a distilled supervisor, so it must stay
#: small and stable — an unbounded list of 200 appeared controls is the raw-AX-dump this
#: architecture forbids inline (PLAN_reasoner_v2 §4).
PROMPT_IDENTITY_CAP = 8


@dataclass(frozen=True)
class StateDelta:
    """The difference between two consecutive observations of one tab.

    `appeared` / `disappeared` are `role|normalized-name` identities — semantic, never selectors
    or node ids, so the delta is safe to journal and safe to show a policy (invariant #10).
    """

    appeared: tuple[str, ...] = ()
    disappeared: tuple[str, ...] = ()
    route_changed: bool = False
    state_changed: bool = False
    #: Signed change in the count of unanswered required fields. Negative = fields got answered
    #: (progress); positive = a new step's fields appeared or an answer was invalidated.
    unanswered_delta: int = 0
    #: True when there was nothing to compare against (the first turn of a run). Distinct from
    #: "compared and found identical" — a first turn must never be read as a stall.
    first_observation: bool = False

    @property
    def moved(self) -> bool:
        """The treadmill predicate: did ANYTHING about this page change?

        A first observation counts as moved — there is no prior to have failed to move from, and
        calling it a stall would escalate every run on step one.
        """
        return bool(
            self.first_observation
            or self.appeared
            or self.disappeared
            or self.route_changed
            or self.state_changed
            or self.unanswered_delta
        )

    @property
    def churn(self) -> int:
        """How much of the control set turned over. A large churn with no route/state change is
        the signature of an in-page transition (a modal opening, a step swapping in)."""
        return len(self.appeared) + len(self.disappeared)


#: The empty first-turn delta — nothing to compare against yet.
FIRST_OBSERVATION = StateDelta(first_observation=True)


def identities_from_ax(candidates: Optional[Sequence[dict[str, Any]]]) -> tuple[str, ...]:
    """AX candidates -> the stable identity set. Thin wrapper over `fingerprint.ax_summary` so
    every caller goes through ONE normalization (see the module docstring's first rule)."""
    return tuple(ax_summary(list(candidates or [])))


def identities_from_scan(unanswered: Optional[Sequence[dict[str, Any]]]) -> tuple[str, ...]:
    """`/scan_required` rows -> identities, as a FALLBACK when no AX scan ran.

    Deliberately impoverished and marked as such: the scan only sees required form fields, so a
    delta built from it is blind to buttons, banners and overlays — precisely the things the
    supervisor most needs to see. Use it only to keep the delta non-empty on turns where an AX
    scan was not affordable; prefer `identities_from_ax` always.
    """
    out: set[str] = set()
    for row in unanswered or ():
        if not isinstance(row, dict):
            continue
        field = str(row.get("field") or "").strip().lower()
        if not field:
            continue
        # `answered` is part of the identity on purpose: a field flipping unanswered -> answered
        # then shows up as one disappeared + one appeared, which is exactly the change we want a
        # scan-only delta to be able to see.
        out.add(f"field|{field}|answered={bool(row.get('answered'))}")
    return tuple(sorted(out))


def compute(
    *,
    before: Optional[Sequence[str]],
    after: Sequence[str],
    url_before: Optional[str] = None,
    url_after: Optional[str] = None,
    state_before: Optional[str] = None,
    state_after: Optional[str] = None,
    unanswered_before: Optional[int] = None,
    unanswered_after: Optional[int] = None,
) -> StateDelta:
    """Difference two observations. PURE.

    `before=None` means "no prior observation" and yields `FIRST_OBSERVATION` — the caller must
    not synthesise an empty list for the first turn, or step one reads as a stall.

    Routes are compared TEMPLATED (`route_template`), not raw: a url whose only change is an id
    segment or a query string is the same screen, and treating it as movement would let a
    treadmill on a paginated form look like progress.
    """
    if before is None:
        return FIRST_OBSERVATION

    before_set, after_set = set(before), set(after)
    route_changed = False
    if url_before is not None and url_after is not None:
        route_changed = route_template(url_before) != route_template(url_after)

    unanswered_delta = 0
    if unanswered_before is not None and unanswered_after is not None:
        unanswered_delta = int(unanswered_after) - int(unanswered_before)

    return StateDelta(
        appeared=tuple(sorted(after_set - before_set)),
        disappeared=tuple(sorted(before_set - after_set)),
        route_changed=route_changed,
        # A None state ("unknown") on either side is not a change by itself — an unrecognised page
        # is its own failure class (UNRECOGNIZED_STATE), not movement.
        state_changed=bool(state_before and state_after and state_before != state_after),
        unanswered_delta=unanswered_delta,
    )


def _fmt_identities(items: tuple[str, ...]) -> str:
    if not items:
        return "(none)"
    head = ", ".join(items[:PROMPT_IDENTITY_CAP])
    extra = len(items) - PROMPT_IDENTITY_CAP
    return f"{head} (+{extra} more)" if extra > 0 else head


def delta_to_prompt(delta: StateDelta) -> str:
    """The STABLE serialization of a delta — rung 1's prompt surface and a distilled supervisor's
    feature set. FROZEN FORMAT, same contract discipline as `bundle_to_prompt`."""
    if delta.first_observation:
        return "# DELTA\n(first observation this run — nothing to compare against)"
    return "\n".join([
        "# DELTA",
        f"moved: {'yes' if delta.moved else 'NO — the page is unchanged'}",
        f"route_changed: {'yes' if delta.route_changed else 'no'}",
        f"state_changed: {'yes' if delta.state_changed else 'no'}",
        f"unanswered_delta: {delta.unanswered_delta:+d}",
        f"appeared: {_fmt_identities(delta.appeared)}",
        f"disappeared: {_fmt_identities(delta.disappeared)}",
    ])
