"""The MCP's Interaction-API layer: journal every intent, uniformly.

    The model says WHAT. The recipe says WHERE. The API says HOW. The journal says
    WHAT HAPPENED.

This module is the "journal says what happened" half. It exists as a DECORATOR rather
than a helper each endpoint calls, because the failure mode we are designing against is
forgetting: `/execute` already logs an event on its success path and returns silently on
both of its not-found early-returns (main_server.py:226 and :234) — so a failed resolve,
the single most useful row in the corpus, is exactly the row that never gets written.
A route-level decorator cannot be forgotten by a new endpoint or skipped by an early
return.

Contract for a journaled endpoint: return a dict carrying `outcome` (an `Outcome`).
Everything else is read off the request body (ats/field/value/tab_url) and the returned
dict (steps/target/widget_type/driver/...). An endpoint that returns no `outcome` is
journaled as ERROR — loudly, on purpose: an un-declared outcome is a silent success, the
bug class the whole taxonomy exists to prevent.

The response the caller sees is DERIVED from the journaled record, so the corpus and the
HTTP response can never disagree about what happened.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, Optional

from interaction.contract import Intent, Outcome, intent_expands_to
from interaction.fingerprint import route_template
from interaction.journal import log_intent

logger = logging.getLogger("mcp.intent")

#: Keys the DECORATOR owns. An endpoint may return them to inform the journal, but they are
#: never passed through verbatim — the response is rebuilt from the journaled record.
#:
#: `ok` is in here for a reason worth keeping: it was originally left out, so an endpoint's
#: own `ok: True` landed in `passthrough` and — because passthrough spreads last — silently
#: overwrote the derived `ok`. The anti-silent-success guard was itself silently overridable.
#: Caught by test_ok_is_derived_from_the_outcome_not_asserted_by_the_endpoint.
_JOURNAL_KEYS = (
    "ok", "outcome", "detail", "steps", "actions", "target", "addressed_by",
    "widget_type", "driver", "executed", "fingerprint", "cost_usd",
)


def _body_attr(body: Any, *names: str) -> Optional[Any]:
    """First present, non-None attribute among `names` — bodies are not uniform yet.

    (They will be after the recipe becomes a resolver and every endpoint takes `field`.
    Until then this bridges the five addressing modes without forcing a big-bang change.)
    """
    for n in names:
        v = getattr(body, n, None)
        if v is not None:
            return v
    return None


def journaled(intent: Intent | Callable[[Any], Intent], *,
              sensitive: Optional[bool] = None) -> Callable:
    """Journal this endpoint's intent — on every path, including exceptions.

    `intent` may be a callable `(body) -> Intent` for endpoints that are polymorphic over
    their request: `/execute` is tier-1 and takes an `action_id`, so its intent is only
    known per-call (see `contract.intent_for_action`).

    `sensitive` forces value redaction on/off for endpoints where the field name can't be
    inferred from — notably `/execute type`, which is how a credential flow is driven.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(body: Any, *args: Any, **kwargs: Any) -> dict:
            started = time.perf_counter()
            this_intent = intent(body) if callable(intent) else intent
            url = _body_attr(body, "tab_url", "url")
            ctx = {
                "ats": _body_attr(body, "ats"),
                "field": _body_attr(body, "field", "field_name", "target_name"),
                "value": _body_attr(body, "value", "option_label"),
                "url": url,
                "route": route_template(url) if url else "",
                "session_id": _body_attr(body, "session_id"),
            }
            try:
                result = await fn(body, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                # An endpoint that raises is a MECHANISM failure (websocket drop, CDP
                # teardown), not a protocol outcome. Journaling it as ERROR keeps it
                # distinguishable from `not_found`, which would send us re-mapping
                # selectors that were fine.
                logger.warning("%s raised: %s", this_intent.value, exc)
                # Keep the caller's own description of what it was doing. For /probe that
                # `note` IS the training signal — the question being asked, of which the
                # expression is only the artifact — and losing it precisely when the probe
                # fails would keep the most interesting rows in the corpus mute.
                note = _body_attr(body, "note")
                detail = f"{type(exc).__name__}: {exc}" + (f" · {note}" if note else "")
                log_intent(intent=this_intent, outcome=Outcome.ERROR, sensitive=sensitive,
                           detail=detail,
                           # An exception means nothing VERIFIABLY reached the page. Letting
                           # `executed` default to True would mark a connection-refused as a
                           # real action on a real page — the same rehearsal/performance
                           # confusion the event log has. (An exception AFTER the action
                           # fired is the staged-commit case, which has its own outcome:
                           # COMMITTED_UNCONFIRMED, returned normally, never raised.)
                           executed=False,
                           duration_ms=int((time.perf_counter() - started) * 1000), **ctx)
                return {"ok": False, "outcome": Outcome.ERROR.value, "detail": detail}

            if not isinstance(result, dict):
                raise TypeError(f"{fn.__name__} must return a dict, got {type(result).__name__}")

            outcome = result.get("outcome")
            if outcome is None:
                # Loud, not lenient. A missing outcome means the endpoint has an
                # un-audited path, and the corpus must show that rather than guess `ok`.
                logger.error("%s returned no outcome — journaling as ERROR: %r",
                             fn.__name__, result.get("detail", ""))
                outcome = Outcome.ERROR
                result = {**result, "detail": f"endpoint declared no outcome; {result.get('detail', '')}"}

            rec = log_intent(
                intent=this_intent,
                outcome=outcome,
                sensitive=sensitive,
                widget_type=result.get("widget_type"),
                addressed_by=result.get("addressed_by"),
                target=result.get("target"),
                steps=result.get("steps") or [],
                # Fall back to the vocabulary's expectation when the endpoint didn't say
                # what it actually did — a row that claims nothing is worse than one that
                # claims the documented expansion.
                actions=result.get("actions") or list(intent_expands_to(this_intent)),
                detail=str(result.get("detail") or ""),
                driver=result.get("driver"),
                executed=bool(result.get("executed", True)),
                fingerprint=result.get("fingerprint"),
                cost_usd=float(result.get("cost_usd") or 0.0),
                duration_ms=int((time.perf_counter() - started) * 1000),
                **ctx,
            )
            # `ok` is derived, never asserted: it is true iff the outcome is a VERIFIED ok.
            # This is the anti-silent-success contract made mechanical — an endpoint cannot
            # report success while journaling a failure.
            passthrough = {k: v for k, v in result.items() if k not in _JOURNAL_KEYS}
            return {
                "ok": rec.outcome == Outcome.OK.value,
                "outcome": rec.outcome,
                "detail": rec.detail,
                "steps": rec.steps,
                **({"widget_type": rec.widget_type} if rec.widget_type else {}),
                **({"target": rec.target} if rec.target else {}),
                **({"driver": rec.driver} if rec.driver else {}),
                **passthrough,
            }

        return wrapper

    return decorator
