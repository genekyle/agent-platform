"""Actuation reach — CAN the local executor operate this page?

The operator's caveat, made computable:

    "the observer is great until we can't do anything about it"

Perception answers *where are we*. Nothing answered *can we touch it*, and the two are genuinely
different questions with different remedies. A page we can name but cannot operate is not a
perception problem and no amount of witness accuracy fixes it; a page we cannot name but CAN
operate only needs someone to say what the fields mean.

That is the whole ORANGE/RED split, and it is why `authority()` checks reach FIRST:

    reachable + unsure   -> ORANGE : the teacher supplies the meaning, the LOCAL executor acts,
                                     and the step lands in the journal like every other step.
    unreachable          -> RED    : the teacher drives — and the `gaps` list is the SPEC FOR AN
                                     ENDPOINT, because §8 says discovery's output is an endpoint,
                                     never a script that worked once.

--------------------------------------------------------------------------------------
It costs nothing on the common path
--------------------------------------------------------------------------------------
Every input is material `LiveActuator.observe()` has already fetched for this turn — the AX
identity set, `/scan_required`'s rows, and the static `apply_fields` table. No extra page hit, no
model call. A probe that cost a round trip would get skipped on exactly the turns it matters.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from interaction.authority import ActuationReach
from interaction.contract import Intent, WidgetType

#: Intents that address a FIELD through the recipe table. These are the ones that need
#: `apply_fields.resolve` to succeed; a click addresses a control by its visible name and needs
#: nothing from the table at all — which is why an unknown ATS blocks form-filling but not
#: navigation, and why reach has to be judged against the action rather than the page alone.
_FIELD_INTENTS = frozenset({
    Intent.SET_TEXT.value, Intent.SELECT_OPTION.value, Intent.SET_DATE.value,
    Intent.CHECK_GROUP.value, Intent.UPLOAD.value, Intent.DESCRIBE.value,
})

#: Intents that need no addressing from us at all. They are always in reach.
_NO_TARGET_INTENTS = frozenset({
    Intent.OBSERVE.value, Intent.SCAN_REQUIRED.value, Intent.PROBE.value,
    Intent.SCROLL.value, Intent.RESOLVE_ANSWER.value,
})

#: Widget shapes a tier-2 protocol knows how to drive. `UNKNOWN` is deliberately absent: it is
#: the enum's own "route to PROBE" marker, and a turn that needs an unknown widget is precisely a
#: capability gap rather than a step to attempt blind.
OPERABLE_WIDGETS = frozenset(w.value for w in WidgetType if w is not WidgetType.UNKNOWN)

#: Gap prefixes that mean the executor genuinely CANNOT ACT — the ones that make a turn RED.
#:
#: The distinction this set draws is the ORANGE/RED line itself, and getting it wrong in the
#: permissive direction sends workable pages to a full takeover. Found by an end-to-end smoke run
#: (2026-07-22): a Workday page with a visible combobox graded RED purely because the field was
#: missing from `apply_fields`, which would route every unmapped field on every ATS to
#: "teacher drives" — the opposite of the point.
#:
#:   page:    nothing addressable at all (also the dead-tab signature). No action is possible.
#:   widget:  a shape no tier-2 protocol drives. Needs a PROBE and then an endpoint (§8).
#:   intent:  a verb we have no reach rule for. Fail closed on the unknown.
#:
#: NOT blocking, and deliberately:
#:
#:   field:   the recipe table has no entry for this field. The PAGE is fine — we simply lack one
#:            addressing entry, and a bounded teacher instruction can route around it (click the
#:            control by its accessible name; tier-1 click needs no table). The gap still travels
#:            in the ticket, so the real fix — an `apply_fields` entry, or a `field_alias` Lesson —
#:            is still specified. Worst case the instruction fails once as `not_found`, the
#:            supervisor names it, and the next turn asks again. That is far cheaper than
#:            permanently promoting every unmapped field to a takeover.
#:   ats:     same argument, one level up.
BLOCKING_GAP_PREFIXES = ("page:", "widget:", "intent:")


def blocks(gaps: tuple[str, ...]) -> tuple[str, ...]:
    """The subset of `gaps` that makes a turn RED rather than ORANGE."""
    return tuple(g for g in gaps if g.startswith(BLOCKING_GAP_PREFIXES))


def _verdict(gaps: list[str]) -> ActuationReach:
    """One place decides can_operate, so the ORANGE/RED line cannot drift between call sites."""
    return ActuationReach(can_operate=not blocks(tuple(gaps)), gaps=tuple(gaps))


def _identity_names(identities: tuple[str, ...]) -> set[str]:
    """The normalized accessible NAMES out of `role|name` identities, lowercased for matching."""
    out: set[str] = set()
    for ident in identities or ():
        _, _, name = ident.partition("|")
        if name:
            out.add(name.strip().lower())
    return out


def _control_present(control: str, identities: tuple[str, ...]) -> bool:
    """Is a control with this accessible name on the page?

    Substring rather than equality, in BOTH directions: `ax_summary` normalizes names (stripping
    volatile tokens), and a recipe's control text is routinely a prefix of the rendered label
    ("Continue" vs "Continue to next step"). A false "present" costs one honest `not_found`
    outcome the supervisor already names; a false "absent" would send a working page to the
    teacher, which is the expensive mistake.
    """
    needle = (control or "").strip().lower()
    if not needle:
        return True
    return any(needle in name or name in needle for name in _identity_names(identities))


def probe(bundle: Any, decision: Any = None, *,
          resolve: Optional[Callable[[str, str], dict]] = None) -> ActuationReach:
    """Can we perform THIS action on THIS page? Pure apart from the injected table lookup.

    `decision=None` asks the page-level question only ("is there anything addressable here"),
    which is what the loop wants before it has decided anything.
    """
    if resolve is None:
        import apply_fields
        resolve = apply_fields.resolve

    identities = tuple(getattr(bundle, "ax_identities", ()) or ())
    gaps: list[str] = []

    # --- page level: is there anything to address at all? -----------------------------
    # An empty identity set is the signature of a page we cannot work, and — importantly — also
    # of a DEAD TAB, whose failure mode here is a SUCCESSFUL EMPTY result rather than an error
    # (LEARNINGS 2026-07-19). Both mean "do not act"; the supervisor tells them apart afterwards.
    if not identities:
        return ActuationReach(can_operate=False, gaps=("page:no-addressable-controls",))

    if decision is None:
        return ActuationReach(can_operate=True, gaps=())

    intent = getattr(decision, "intent", "") or ""
    params = getattr(decision, "params", None) or {}

    if intent in _NO_TARGET_INTENTS:
        return ActuationReach(can_operate=True, gaps=())

    # --- a click / submit addresses a control by its visible name ---------------------
    if intent in (Intent.CLICK.value, Intent.SUBMIT.value):
        control = str(params.get("control") or "")
        if control and not _control_present(control, identities):
            # A control we cannot see is a `page:`-class problem for THIS action: there is nothing
            # to click, and no instruction naming the same control would fare better.
            gaps.append(f"page:control-not-found:{control}")
        return _verdict(gaps)

    # --- a field intent needs the recipe table to know WHERE and WHAT SHAPE -----------
    if intent in _FIELD_INTENTS:
        ats = str(getattr(bundle, "ats", "") or "")
        field = str(params.get("field") or "")
        if not ats:
            gaps.append("ats:unknown")
        elif field:
            try:
                entry = resolve(ats, field)
            except Exception:  # noqa: BLE001 — FieldNotFound, or an unknown ATS table
                gaps.append(f"field:{ats}/{field}")
            else:
                widget = str((entry or {}).get("widget_type") or WidgetType.UNKNOWN.value)
                if widget not in OPERABLE_WIDGETS:
                    # Not a failure — a DISCOVERY. `/probe` is the sanctioned next move and its
                    # output is a new WidgetType member plus the protocol that drives it.
                    gaps.append(f"widget:{widget}@{field}")
        return _verdict(gaps)

    # An intent we have no reach rule for. Say so rather than assuming yes: an unknown verb is
    # exactly where a silent "sure, go ahead" would be most expensive.
    return _verdict([f"intent:{intent or 'unnamed'}"])


def survey(bundle: Any, *, resolve: Optional[Callable[[str, str], dict]] = None) -> tuple[str, ...]:
    """Everything on this page the executor could NOT operate, whether or not we need it now.

    Distinct from `probe`, which judges one action. This is the capability-gap inventory: what an
    escalation should carry so the teacher's answer becomes an endpoint rather than a one-off, and
    what a `capability_gap` Lesson is written from.
    """
    if resolve is None:
        import apply_fields
        resolve = apply_fields.resolve

    ats = str(getattr(bundle, "ats", "") or "")
    gaps: list[str] = []
    if not tuple(getattr(bundle, "ax_identities", ()) or ()):
        gaps.append("page:no-addressable-controls")
    for row in getattr(bundle, "unanswered", ()) or ():
        field = str((row or {}).get("field") or "")
        if not field:
            continue
        if not ats:
            gaps.append(f"field:unknown-ats/{field}")
            continue
        try:
            entry = resolve(ats, field)
        except Exception:  # noqa: BLE001
            gaps.append(f"field:{ats}/{field}")
            continue
        widget = str((entry or {}).get("widget_type") or WidgetType.UNKNOWN.value)
        if widget not in OPERABLE_WIDGETS:
            gaps.append(f"widget:{widget}@{field}")
    return tuple(dict.fromkeys(gaps))
