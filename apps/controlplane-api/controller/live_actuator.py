"""LiveActuator — the seam that lets the controller loop drive a real browser tab.

`run_controller` (controller/loop.py) observes through an `Actuator.observe()` and acts through
`Actuator.act()`; in tests those are fakes. This is the LIVE implementation. It talks to the mcp
capture server (the Interaction API) over HTTP, exactly like `runtime/live.py` does for the
SELECT-cascade loop — reused, not reinvented — and it is the piece that turns a controller drive
from "offline-proven" into "takes live turns."

  observe() -> Bundle : /auth_state (current url + login) + /scan_required (unanswered, RAW) ->
                        build_bundle. The RAW scan (with selectors) is KEPT for act(); the Bundle
                        gets the sanitized copy (no selectors/PII) that decide() reads. This split
                        is the whole "the model never sees a selector" invariant, made mechanical.
  act(Decision)       : resolve (ats, field) -> addressing (apply_fields.resolve FIRST — the static
                        Workday/Greenhouse recipe — then the LIVE scan, for Indeed's dynamic
                        questions whose selectors only exist at runtime) -> dispatch to the
                        JOURNALED tier-2 endpoint (/execute type|click, /select_option, /set_date,
                        /check_group). SUBMIT is REFUSED here: the loop's consequential gate holds
                        it for the operator, so a Submit reaching act() is a bug, not an action.

Two disciplines carried from the hard-won lessons:
  * Address by TAB_ID, never tab_url. A tab_url handle breaks the instant a Continue click
    navigates; the tab id is stable across navigation (project_terminal_states_and_multiwindow).
  * Never raise into the loop. A transport failure becomes an ActOutcome(outcome=ERROR) or an
    escalating Bundle (human_required), which the loop turns into a clean handoff, never a crash
    (the same contract as runtime/live.py's adapters).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Callable, Optional

import apply_recipe
import ats_registry
import apply_fields
from controller.bundle import build_bundle
from controller.loop import ActOutcome
from interaction import decision_journal
from interaction.contract import Intent, Outcome
from interaction.decision import Bundle, Decision

logger = logging.getLogger("controller.live_actuator")

#: A (path, payload) -> response-dict callable. The one seam that makes this testable offline:
#: the real transport POSTs to the capture server; a test injects a fake that records calls.
Transport = Callable[[str, dict], dict]

#: Read-only intents mean "look again" in a drive — they act on nothing, just re-observe.
_READ_INTENTS = frozenset({
    Intent.OBSERVE.value, Intent.SCAN_REQUIRED.value, Intent.DESCRIBE.value,
    Intent.PROBE.value, Intent.RESOLVE_ANSWER.value,
})


def _httpx_transport(base_url: str, *, timeout: float = 60.0) -> Transport:
    """The live transport: a blocking POST to the capture server (sync, like runtime/live.py —
    run_controller is sync and runs in a threadpool, so a blocking client is correct)."""
    import httpx

    base = base_url.rstrip("/")

    def _post(path: str, payload: dict) -> dict:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(f"{base}{path}", json=payload)
            r.raise_for_status()
            return r.json() or {}

    return _post


def _outcome_of(res: dict) -> str:
    """The tier-2 endpoints return a verified `outcome`; the tier-1 /execute primitive returns
    only `ok`. Prefer the declared outcome; fall back to ok -> OK/ERROR."""
    o = res.get("outcome")
    if o:
        return str(o)
    return Outcome.OK.value if res.get("ok") else Outcome.ERROR.value


class LiveActuator:
    """Drive one live tab for the controller loop. One instance per drive."""

    def __init__(self, *, base_url: str, browser_url: str, tab_id: str,
                 task: str = "indeed_apply", goal_text: str = "",
                 driver: str = "direct", transport: Optional[Transport] = None) -> None:
        self._browser_url = browser_url
        self._tab_id = tab_id
        self._task = task
        self._goal = goal_text
        self._driver = driver                       # 'direct' (real) | 'record_only' (dry run)
        self._post_fn: Transport = transport or _httpx_transport(base_url)
        # Carried between observe() and act(): the RAW scan (field -> selector, for Indeed's
        # dynamic fields), the current url, the current ats + state.
        self._last_scan: list[dict] = []
        self._last_url: str = ""
        self._ats: str = ""
        self._last_state: Optional[str] = None

    # --- transport (never raises into the loop) --------------------------------------
    def _addr(self) -> dict:
        # tab_id only: stable across navigation. A stale tab_url would mis-address after a
        # Continue click navigates the page.
        return {"browser_url": self._browser_url, "tab_id": self._tab_id}

    def _post(self, path: str, payload: dict) -> dict:
        try:
            return self._post_fn(path, payload)
        except Exception as exc:  # noqa: BLE001 — a transport failure is a handoff, not a crash
            logger.warning("LiveActuator %s failed: %s", path, exc)
            return {"ok": False, "outcome": Outcome.ERROR.value,
                    "detail": f"{type(exc).__name__}: {exc}"}

    # --- OBSERVE ---------------------------------------------------------------------
    def observe(self) -> Bundle:
        """Read the live tab into a Bundle. Keeps the RAW scan for act(); the Bundle carries the
        sanitized copy decide() reads. A logged-out tab returns an escalating (human_required)
        Bundle — the agent never re-authenticates on its own."""
        auth = self._post("/auth_state", self._addr())
        url = auth.get("url") or self._last_url or ""
        logged_in = auth.get("logged_in", True)

        scan = self._post("/scan_required", self._addr())
        raw = scan.get("unanswered") or []
        self._last_scan = raw
        self._last_url = url

        tail = decision_journal.read_rows(limit=5)
        bundle = build_bundle(self._task, url, "", goal_text=self._goal,
                              scan=raw, journal_tail=tail)
        self._ats = bundle.ats or ats_registry.classify_ats(url)
        self._last_state = bundle.state

        if not logged_in:
            return replace(bundle, human_required=True,
                           branch_note="not logged in — operator must re-authenticate (the agent "
                                       "never types a password / creates an account)")
        return bundle

    # --- ACT -------------------------------------------------------------------------
    def act(self, decision: Decision) -> ActOutcome:
        """Drive ONE Decision through the Interaction API. Returns an ActOutcome the loop verifies.
        SUBMIT is refused (the gate holds it); an unaddressable field or unknown intent is an
        honest NOT_FOUND that the loop escalates rather than a guess."""
        intent = decision.intent
        p = dict(decision.params or {})

        if intent == Intent.SUBMIT.value:
            # Defensive: loop.CONSEQUENTIAL_INTENTS should have held this before act().
            return self._out(Outcome.BLOCKED.value, self._last_state,
                             detail="SUBMIT held for the operator — must not reach act()")
        if intent in _READ_INTENTS:
            return self._out(Outcome.OK.value, self._current_state(), detail="re-observed")

        if intent == Intent.CLICK.value:
            control = p.get("control") or p.get("name") or p.get("value")
            if not control:
                return self._out(Outcome.NOT_FOUND.value, self._last_state,
                                 detail="click with no control name")
            res = self._post("/execute", {**self._addr(), "action_id": "click", "target_bbox": {},
                                          "target_role": p.get("role", "button"),
                                          "target_name": control, "driver": self._driver})
            # A click usually navigates — re-classify the current url into the landed state.
            return ActOutcome(outcome=_outcome_of(res), landed_state=self._current_state(),
                              detail=res.get("detail", ""))

        # Field intents need addressing (selector, or role+name).
        field = p.get("field")
        addr = self._address(field) if field else None
        if field and addr is None:
            return self._out(Outcome.NOT_FOUND.value, self._last_state,
                             detail=f"cannot address field {field!r} — not in apply_fields nor the "
                                    f"live scan (stale recipe or the form changed)")

        if intent == Intent.SET_TEXT.value:
            exe: dict[str, Any] = {**self._addr(), "action_id": "type", "target_bbox": {},
                                   "value": p.get("value", ""), "driver": self._driver}
            if addr.get("selector"):
                exe["selector"] = addr["selector"]
            else:
                exe["target_role"], exe["target_name"] = addr.get("role"), addr.get("name")
            return self._field_result(self._post("/execute", exe))

        if intent == Intent.SELECT_OPTION.value:
            res = self._post("/select_option", {
                **self._addr(), "selector": addr.get("selector"), "value": p.get("value", ""),
                "ats": self._ats, "field": field, "commit": addr.get("commit"),
                "widget_type": addr.get("widget_type")})
            return self._field_result(res)

        if intent == Intent.SET_DATE.value:
            res = self._post("/set_date", {
                **self._addr(), "selector": addr.get("selector"),
                "month": int(p.get("month") or 0), "year": int(p.get("year") or 0),
                "day": p.get("day"), "ats": self._ats, "field": field})
            return self._field_result(res)

        if intent == Intent.CHECK_GROUP.value:
            values = p.get("values") or ([p["value"]] if p.get("value") else [])
            res = self._post("/check_group", {
                **self._addr(), "selector": addr.get("selector"), "values": values,
                "ats": self._ats, "field": field})
            return self._field_result(res)

        # SCROLL / UPLOAD / anything else: no dispatch yet — escalate honestly, don't guess.
        return self._out(Outcome.NOT_FOUND.value, self._last_state,
                         detail=f"LiveActuator has no dispatch for intent {intent!r} yet")

    # --- helpers ---------------------------------------------------------------------
    def _address(self, field: str) -> Optional[dict]:
        """(ats, field) -> addressing. The static recipe (apply_fields.resolve) FIRST — Workday /
        Greenhouse fields whose selectors are stable — then the LIVE scan, for Indeed's dynamic
        questions whose selectors only exist at runtime. None -> NOT_FOUND (never a guess)."""
        try:
            e = apply_fields.resolve(self._ats, field)
            return {"selector": e["selector"], "role": e["role"], "name": e["name"],
                    "widget_type": e["widget_type"], "commit": e["commit"]}
        except apply_fields.FieldNotFound:
            for u in (self._last_scan or []):
                if u.get("field") == field and u.get("selector"):
                    return {"selector": u["selector"], "role": None, "name": None,
                            "widget_type": u.get("kind"), "commit": None}
        return None

    def _field_result(self, res: dict) -> ActOutcome:
        """A field-fill stays on the same page — its landed state is where we were; the endpoint's
        verified outcome (it re-reads the DOM) says whether the value took."""
        return ActOutcome(outcome=_outcome_of(res), landed_state=self._last_state,
                          cost_usd=float(res.get("cost_usd") or 0.0), detail=res.get("detail", ""))

    def _current_state(self) -> Optional[str]:
        """Re-classify the CURRENT url into a state (used after a click that may have navigated)."""
        auth = self._post("/auth_state", self._addr())
        url = auth.get("url") or self._last_url or ""
        self._last_url = url
        ats = ats_registry.classify_ats(url)
        desc = apply_recipe.describe_for_ats(ats, url, "")
        st = desc.get("state")
        self._last_state = st if st and st != "unknown" else None
        return self._last_state

    def _out(self, outcome: str, landed: Optional[str], *, detail: str = "") -> ActOutcome:
        return ActOutcome(outcome=outcome, landed_state=landed, detail=detail)


def run_live_apply(*, browser_url: str, tab_id: str, task: str = "indeed_apply",
                   capture_server_url: Optional[str] = None, use_model: bool = True,
                   budget_limit: Optional[float] = None, reviewer=None, record_only: bool = False,
                   session_id: str = "", max_steps: int = 40, transport: Optional[Transport] = None):
    """Wire a live controller drive: LiveActuator + Haiku (rung 1, proposes) + a reviewer (you/me,
    corrects) + the program store, then run_controller. Haiku's early calls will be wrong; the
    reviewer corrects at the point of disagreement -> golden rows. SUBMIT is held by the loop."""
    from settings import settings
    from controller import programs as programs_mod
    from controller.loop import run_controller

    base = capture_server_url or settings.capture_server_url
    actuator = LiveActuator(base_url=base, browser_url=browser_url, tab_id=tab_id, task=task,
                            driver=("record_only" if record_only else "direct"), transport=transport)
    model = None
    if use_model:
        from controller.reason import HaikuReasoner
        model = HaikuReasoner(budget_limit=budget_limit)
    return run_controller(actuator, programs=programs_mod.ProgramStore(), model=model,
                          reviewer=reviewer, session_id=session_id, max_steps=max_steps)
