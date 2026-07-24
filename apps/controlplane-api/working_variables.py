"""Working variables — application answers that are COMPUTED at fill-time, not stored.

Operator, 2026-07-24: *"some of the variables like when i can start and today's date should be a
'working variable' meaning it will always be the variable 'today's date' so whatever today is that
is what we input."*

Most application answers are static strings the operator saved once (their name, their salary
floor). A few are not answers at all but FUNCTIONS OF NOW: "today's date" on a signature line is
wrong the moment you store it, and "when can you start" for someone available immediately is just
today. Freezing those into the answer store means every application after the day you saved them
carries a stale date. So these keys resolve when the form is filled, never before.

The resolution is deliberately tiny and dependency-free: a working variable maps to a function of
`today`. `today` is injectable so the behaviour is testable without waiting for tomorrow — the
runtime passes nothing and gets the real current date.

Format note: US Workday tenants render MM/DD/YYYY, which is the default here. A variable can carry
its own format if a site wants ISO; the resolver honours it.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Optional


def _today_str(today: date, fmt: str) -> str:
    return today.strftime(fmt)


#: The working-variable registry. Each entry: how to compute the value from `today`, a one-line
#: description for the profile UI (so the operator can SEE it is dynamic), and the default format.
#: Both of the operator's examples resolve to today — "available now" and "today's date" are the
#: same value, which is exactly their point.
_VARIABLES: dict[str, dict[str, Any]] = {
    "todays_date": {
        "compute": lambda today, fmt: _today_str(today, fmt),
        "fmt": "%m/%d/%Y",
        "desc": "Always today's date — resolved when the form is filled, never stored.",
    },
    "availability_date": {
        "compute": lambda today, fmt: _today_str(today, fmt),
        "fmt": "%m/%d/%Y",
        "desc": "When you can start: today (available now). Resolved at fill-time.",
    },
}

WORKING_VARIABLE_KEYS = frozenset(_VARIABLES)


def is_working_variable(answer_key: str) -> bool:
    return answer_key in _VARIABLES


def resolve(answer_key: str, *, today: Optional[date] = None,
            fmt: Optional[str] = None) -> Optional[str]:
    """The value of a working variable RIGHT NOW, or None if the key is not one.

    `today` is injectable for tests; the runtime omits it and gets the real current date. Passing
    `fmt` overrides the variable's default format (a site that wants ISO, say)."""
    spec = _VARIABLES.get(answer_key)
    if spec is None:
        return None
    compute: Callable[[date, str], str] = spec["compute"]
    return compute(today or date.today(), fmt or spec["fmt"])


def describe(answer_key: str, *, today: Optional[date] = None) -> Optional[dict[str, Any]]:
    """What the profile UI shows for a working variable: that it IS one, what it resolves to now,
    and why. None for a normal stored answer."""
    if not is_working_variable(answer_key):
        return None
    return {"working_variable": True, "resolves_to": resolve(answer_key, today=today),
            "description": _VARIABLES[answer_key]["desc"]}


def effective_value(answer_key: str, stored_value: Any, *,
                    today: Optional[date] = None) -> Any:
    """The value to actually USE for a key: the computed one for a working variable, otherwise the
    stored value. This is the single call the form-fill path makes so a working variable can never
    be filled from a stale stored string."""
    if is_working_variable(answer_key):
        return resolve(answer_key, today=today)
    return stored_value
