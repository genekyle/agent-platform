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
import re
import time
from dataclasses import replace
from typing import Any, Callable, Optional

import apply_recipe
import ats_registry
import apply_fields
from controller.bundle import build_bundle
from perception import live as perception_live
from controller.loop import ActOutcome
from interaction import decision_journal
from interaction.contract import Intent, Outcome
from interaction.decision import Bundle, Decision
from interaction.delta import identities_from_ax

logger = logging.getLogger("controller.live_actuator")

#: A (path, payload) -> response-dict callable. The one seam that makes this testable offline:
#: the real transport POSTs to the capture server; a test injects a fake that records calls.
Transport = Callable[[str, dict], dict]

#: Read-only intents mean "look again" in a drive — they act on nothing, just re-observe.
_READ_INTENTS = frozenset({
    Intent.OBSERVE.value, Intent.SCAN_REQUIRED.value, Intent.DESCRIBE.value,
    Intent.PROBE.value, Intent.RESOLVE_ANSWER.value,
})

#: Native single-choice controls the tier-2 endpoints CANNOT drive (found live 2026-07-18):
#: /check_group errors on native radios and /select_option only handles react-select/listbox.
#: The only mechanism that works is /autofill_form's native input.click()-by-question-text.
_RADIO_WIDGETS = frozenset({"radio_group", "radio"})

#: An affirmation value marks a consent/acknowledgment CHECKBOX ("I have read and accept" -> Accept):
#: checking it is a native click, same mechanism as a radio — not a labelled multi-select group.
_AFFIRM_PREFIXES = ("accept", "agree", "yes", "true", "i accept", "i agree", "i acknowledge",
                    "i certify", "i consent", "i have read")

#: AX roles a MISSED required control plausibly wears. `rescan_required` looks for these and only
#: these: a checkbox or radio the page exposes that `/scan_required` never mentioned is the exact
#: shape of the miss we have on record (the lone acknowledgment checkbox, 07-18). Widening this to
#: every role would return the whole page and diagnose nothing.
_CONSENT_ROLES = frozenset({"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"})


def _question_of(name: Optional[str]) -> str:
    """The question half of a field label, normalised for comparison.

    `/scan_required` labels a radio group with its question AND its option text, truncated —
    'Do you … require sponsorship for a work visa? * No, I do not r'. The ' *' required-marker is
    the boundary between the two, so cutting there recovers the stable half that answer keys,
    recipe fields and program steps actually refer to.
    """
    n = re.sub(r"\s+", " ", (name or "").strip())
    for marker in (" * ", " *"):
        i = n.find(marker)
        if i >= 0:
            n = n[:i]
            break
    return n.strip().strip("*").strip().lower()


def _is_affirmation(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(_AFFIRM_PREFIXES)


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
                 driver: str = "direct", transport: Optional[Transport] = None,
                 settle_tries: int = 3, settle_delay: float = 0.7,
                 sleep_fn: Optional[Callable[[float], None]] = None,
                 re_resolve: Optional[Callable[[], Optional[dict]]] = None,
                 collect: bool = True) -> None:
        self._browser_url = browser_url
        self._tab_id = tab_id
        self._task = task
        self._goal = goal_text
        self._driver = driver                       # 'direct' (real) | 'record_only' (dry run)
        self._post_fn: Transport = transport or _httpx_transport(base_url)
        # Settling after a navigating click: how many times to re-read the url waiting for it to
        # stabilise, and how long between reads. sleep_fn is injectable so offline tests don't wait.
        self._settle_tries = settle_tries
        self._settle_delay = settle_delay
        self._sleep: Callable[[float], None] = sleep_fn or time.sleep
        # The RE_RESOLVE_TAB play's one dependency, injected (never imported) so this module stays
        # DB-free — the same seam `login_reasoner.run_login` takes.
        self._re_resolve = re_resolve
        # Capture every observation into the corpus. Defaults ON (operator-directed 2026-07-22:
        # a drive should always be collecting); turn it off only for a probe you do not want in
        # the training data, never to "save time" — the collecting IS the work.
        self._collect = collect
        # Carried between observe() and act(): the RAW scan (field -> selector, for Indeed's
        # dynamic fields), the current url, the current ats + state.
        self._last_scan: list[dict] = []
        self._last_url: str = ""
        self._last_page_text: str = ""
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
        Bundle — the agent never re-authenticates on its own.

        THREE probes, each read for its failure as well as its value (LEARNINGS 2026-07-19 — "a
        stale tab was SILENCE, not an error"; this path had the same bug three times over and it
        is why `page_text` was empty and `ax_identities` did not exist):

          /auth_state    -> url, logged_in, AND page_text. Workday/Greenhouse states and the
                            anti-bot CHALLENGE markers are classified from page text alone
                            (`apply_recipe._CHALLENGE_MARKERS`), so passing "" here — which this
                            method did until 2026-07-20 — made the controller structurally unable
                            to see a captcha. Transient: the Bundle keeps the derived state, never
                            the text (PRINCIPLES §4).
          /scan_required -> the unanswered required fields.
          /ax_scan       -> the control set, reduced to `role|name` identities. This is the
                            supervisor's sense organ (PLAN_supervisor §0a): a delta over it is the
                            only thing that can tell real progress from a treadmill on Indeed's
                            questions module, where route and state are both invariant.
        """
        auth = self._post("/auth_state", self._addr())
        scan = self._post("/scan_required", self._addr())
        ax = self._post("/ax_scan", self._addr())

        url = auth.get("url") or self._last_url or ""
        page_text = str(auth.get("page_text") or "")
        raw = scan.get("unanswered") or []
        self._last_scan = raw
        self._last_url = url

        tail = decision_journal.read_rows(limit=5)
        candidates = ax.get("candidates") or []

        # --- collect, then perceive. Operator-directed 2026-07-22: a drive should ALWAYS be
        # building the corpus. Every turn is a free, recipe-labeled example for every model in
        # the stack, and three months of drives produced none of them (the 2026-07-16 reckoning).
        # The capture also yields the screenshot the visual witness needs, so collecting and
        # perceiving are one round trip rather than two. Both are best-effort: perception is an
        # aid, never a dependency, and a drive must run exactly as before without it.
        shot = None
        if self._collect:
            shot = perception_live.capture_now(
                self._post, self._addr(), task=self._task,
                state=None, domain_id=ats_registry.classify_ats(url))
        belief = perception_live.sense(
            url=url, page_text=page_text, ax_candidates=candidates, screenshot_path=shot,
            domain_id=ats_registry.classify_ats(url))

        bundle = build_bundle(self._task, url, page_text, goal_text=self._goal,
                              scan=raw, journal_tail=tail,
                              ax_candidates=candidates, belief=belief)
        self._ats = bundle.ats or ats_registry.classify_ats(url)
        self._last_state = bundle.state

        # --- a failed probe is NOT a benign reading. Each of these defaults used to launder a
        # dead tab into a confident, wrong observation:
        #   auth ok:false  -> `logged_in` absent -> defaulted True  -> "we're signed in"
        #   scan ok:false  -> `unanswered` absent -> []             -> "the form is complete"
        #   ax   errors[]  -> candidates []                         -> "there are no controls"
        # Any of them means we cannot see the page, which is a HANDOFF, never an inference.
        blind = self._blind_reason(auth, scan, ax)
        if blind:
            return replace(bundle, human_required=True, state=None,
                           branch_note=f"cannot observe the tab — {blind}")

        if not auth.get("logged_in", False):
            return replace(bundle, human_required=True,
                           branch_note="not logged in — operator must re-authenticate (the agent "
                                       "never types a password / creates an account)")
        return bundle

    @staticmethod
    def _blind_reason(auth: dict, scan: dict, ax: dict) -> str:
        """Why this observation cannot be trusted, or "" when it can.

        An `/ax_scan` that returns candidates=[] WITH `errors` is the stale-tab signature: the
        proposer swallows a target-discovery failure into `errors` and replies HTTP 200, so a dead
        target and an empty page are indistinguishable unless you read `errors` (ax_proposer.py
        :304-309). A genuinely empty page with no errors is left alone — that is a real reading,
        and the supervisor classifies it, not this method.
        """
        if auth.get("ok") is False:
            return f"auth probe failed ({auth.get('detail') or 'no detail'})"
        if scan.get("ok") is False:
            return f"required-field scan failed ({scan.get('detail') or 'no detail'})"
        if ax.get("ok") is False:
            return f"AX scan failed ({ax.get('detail') or 'no detail'})"
        errors = ax.get("errors") or ()
        if not (ax.get("candidates") or ()) and errors:
            return f"AX scan returned no controls with errors — likely a stale tab: {errors[0]}"
        return ""

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
            before = self._read_url()      # the baseline the settle waits to LEAVE
            res = self._post("/execute", {**self._addr(), "action_id": "click", "target_bbox": {},
                                          "target_role": p.get("role", "button"),
                                          "target_name": control, "driver": self._driver})
            # A click usually navigates — re-classify the current url into the landed state, THEN
            # take the after-picture (order matters: looking before the settle would photograph
            # the page we were leaving).
            landed = self._current_state(changed_from=before)
            identities, unanswered = self._after_look()
            return ActOutcome(outcome=_outcome_of(res), landed_state=landed,
                              detail=res.get("detail", ""),
                              ax_identities=identities, unanswered_after=unanswered)

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

        wt = (addr.get("widget_type") or "").lower()

        if intent == Intent.SELECT_OPTION.value:
            # A native radio group is not driveable by /select_option (react-select/listbox only) —
            # route it to the native-click mechanism. react-select / listbox stay on the endpoint.
            if wt in _RADIO_WIDGETS:
                return self._field_result(self._autofill_native(field, p.get("value", "")))
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
            first = values[0] if values else ""
            # A radio group mislabelled check_group, OR a single consent/acknowledgment checkbox
            # (affirmation value): both are a native click, not a labelled multi-select group.
            if wt in _RADIO_WIDGETS or _is_affirmation(first):
                return self._field_result(self._autofill_native(field, first))
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
            # Match the live scan by field. Do NOT require a selector: a native radio group is
            # addressed by its QUESTION TEXT (autofill), not a selector — Indeed gives every radio
            # the same id, so the group often has no usable selector at all. widget_type carries
            # the routing signal; a null selector is fine for the native-click path.
            # AMBIGUITY IS NOT A PICK. If two rows answer to the same name we cannot tell them
            # apart, and choosing the first would answer the WRONG question on someone's real
            # application — the same reason _discover_target refuses to fall back to another tab.
            hits = [u for u in (self._last_scan or [])
                    if self._same_field(u.get("field"), field)]
            if len(hits) == 1:
                u = hits[0]
                return {"selector": u.get("selector"), "role": None, "name": None,
                        "widget_type": u.get("kind"), "commit": None}
            if len(hits) > 1:
                logger.warning("field %r matches %d scan rows — refusing to guess which",
                               field, len(hits))
        return None

    @staticmethod
    def _same_field(scan_name: Optional[str], wanted: str) -> bool:
        """Is this scan row the field we asked for?

        Exact equality is NOT usable here, and assuming it cost a live drive (2026-07-19, Longroad):
        `/scan_required` names a radio group by its question PLUS its first option text and then
        truncates (~90 chars) — 'Do you … require sponsorship for a work visa? * No, I do not r'.
        No stored answer key, recipe field, or program step will ever equal that string, so every
        Indeed question page was unaddressable and the drive stalled at NOT_FOUND.

        So compare the QUESTION halves: everything before the ' *' required-marker, normalised.
        Prefix matching both ways covers the truncation (the scan's copy may be cut mid-question).
        The length floor keeps a short prefix from colliding with a different question.
        """
        a, b = _question_of(scan_name), _question_of(wanted)
        if not a or not b:
            return False
        if a == b:
            return True
        return len(min(a, b, key=len)) >= 12 and (a.startswith(b) or b.startswith(a))

    @staticmethod
    def _question_patterns(field: str) -> list[str]:
        """Substrings of the question text the autofill matcher can find. The scan's `field` often
        carries the option labels appended ('… ? * Yes No'), which are NOT in the DOM question
        text — so strip to the question itself, or a pattern would never match."""
        field = (field or "").strip()
        out = {field}
        for cut in ("?", "*"):
            if cut in field:
                head = field.split(cut)[0].strip()
                if len(head) > 4:
                    out.add(head + ("?" if cut == "?" else ""))
        return [p for p in out if len(p) > 4]

    def _autofill_native(self, field: str, value: str) -> dict:
        """Drive a native single-choice control (radio group, consent checkbox) via /autofill_form's
        native input.click()-by-question — the ONLY mechanism that works on them (the tier-2
        endpoints don't; found live 2026-07-18). One inline single-answer autofill, matched to the
        question by text. The caller journals the SEMANTIC intent (select_option/check_group); this
        is just the HOW."""
        answers = [{"key": "_teach_native", "value": value, "options": [value],
                    "patterns": self._question_patterns(field)}]
        res = self._post("/autofill_form", {**self._addr(), "answers": answers})
        filled = any((r or {}).get("status") == "filled" for r in (res.get("report") or []))
        return {"ok": filled, "outcome": Outcome.OK.value if filled else Outcome.NOT_FOUND.value,
                "detail": f"native autofill by question text (filled={filled})"}

    def _field_result(self, res: dict) -> ActOutcome:
        """A field-fill stays on the same page — its landed state is where we were; the endpoint's
        verified outcome (it re-reads the DOM) says whether the value took."""
        identities, unanswered = self._after_look()
        return ActOutcome(outcome=_outcome_of(res), landed_state=self._last_state,
                          cost_usd=float(res.get("cost_usd") or 0.0), detail=res.get("detail", ""),
                          ax_identities=identities, unanswered_after=unanswered)

    def _read_url(self) -> str:
        auth = self._post("/auth_state", self._addr())
        # Keep the page text the probe already returned: `_current_state` classifies from it, and
        # for Workday/Greenhouse it is the ONLY state signal there is (the URL never changes).
        self._last_page_text = str(auth.get("page_text") or "")
        return auth.get("url") or ""

    def _after_look(self) -> tuple[tuple[str, ...], Optional[int]]:
        """The after-picture of ONE action: the control identities and the unanswered count.

        This is what makes the supervisor's verdict about the action that just ran rather than one
        turn late (`controller/loop.action_effect`). It costs two extra read-only CDP evals per
        action — a local socket, free on a metered connection, and cheap next to being unable to
        tell a staged widget from a committed one.

        `unanswered_after` is the half that catches STAGED_NOT_COMMITTED: a react-select that
        stages DOES change the AX tree, so control churn alone reads it as success. The form's own
        answer to "is this field filled?" is the only thing that disagrees — and it is right
        (the Ethnicity select, LEARNINGS 2026-07-18).
        """
        ax = self._post("/ax_scan", self._addr())
        identities = identities_from_ax(ax.get("candidates") or [])
        scan = self._post("/scan_required", self._addr())
        if scan.get("ok") is False:
            return identities, None          # unknown, not zero — zero would read as "complete"
        rows = scan.get("unanswered") or []
        self._last_scan = rows               # act() addresses off the freshest scan
        return identities, len(rows)

    def _current_state(self, *, changed_from: Optional[str] = None) -> Optional[str]:
        """Re-classify the CURRENT url into a state (used after a click that may have navigated).

        SETTLE FIRST: /auth_state can return the OLD url before a navigation completes — observed
        live reporting `resume_selection` right after a Continue that had already reached
        `questions/1`.

        "Two consecutive reads agree" is NOT sufficient on its own, and that bug bit us live
        (2026-07-19, the Longroad drive): a SPA transition that hasn't STARTED yet is also
        perfectly stable, so the first two reads agreed on the OLD url, the loop verified against
        the wrong state, and a working program got marked stale for a click that had actually
        succeeded. A false stale-mark is expensive — it evicts a good program from rung 0.

        So when the caller knows the url the action started from (`changed_from`), we wait for the
        url to actually LEAVE it before confirming stability. A click that legitimately doesn't
        navigate just spends the settle budget and returns the same state, which is correct.
        """
        url = self._read_url()
        for _ in range(max(0, self._settle_tries)):
            if changed_from is not None and url == changed_from:
                self._sleep(self._settle_delay)     # hasn't moved yet — not "settled"
                url = self._read_url()
                continue
            self._sleep(self._settle_delay)
            nxt = self._read_url()
            if nxt == url:          # stable AND (if asked) already off the starting url
                break
            url = nxt               # still moving — keep following it
        url = url or self._last_url or ""
        self._last_url = url
        ats = ats_registry.classify_ats(url)
        # `_read_url` kept the page text from the same probe. Passing it is what lets a Workday /
        # Greenhouse step — whose URL is invariant — be classified at all after a click, and what
        # lets a challenge page be recognised here rather than driven into.
        desc = apply_recipe.describe_for_ats(ats, url, self._last_page_text)
        st = desc.get("state")
        self._last_state = st if st and st != "unknown" else None
        return self._last_state

    def _out(self, outcome: str, landed: Optional[str], *, detail: str = "") -> ActOutcome:
        return ActOutcome(outcome=outcome, landed_state=landed, detail=detail)

    # --- RecoveryActuator (S12b): the supervisor's plays, filled ---------------------
    # Each reuses machinery that already existed; none is a new mechanism. Every one is
    # best-effort and returns rather than raising — a failed recovery is a handoff.

    def settle(self) -> None:
        """Wait for the page to stop moving, then re-read where we are. The remedy for a race
        (the location combobox on clear+type; classify-before-navigation-settles — 07-18)."""
        self._sleep(self._settle_delay)
        self._last_state = self._current_state()

    def re_resolve_tab(self) -> bool:
        """Re-discover the live CDP target after the pinned one died. Injected as `re_resolve`
        exactly like `login_reasoner.run_login` takes it, so this module stays DB-free and
        unit-testable with a fake (LEARNINGS 2026-07-19 — the wrong-tab refusal is correct, so
        the fix is to re-DISCOVER the right tab, never to loosen the guard)."""
        if self._re_resolve is None:
            return False
        fresh = self._re_resolve()
        if not (fresh and fresh.get("tab_id")):
            return False
        self._browser_url = fresh.get("browser_url") or self._browser_url
        self._tab_id = fresh["tab_id"]
        return True

    def rescan_required(self) -> tuple[dict, ...]:
        """Find required controls the ordinary scan missed, with a DIFFERENT instrument.

        `/scan_form` (the old second opinion) was deleted 2026-07-16 for labelling every control
        required. So the second instrument is the AX tree: a checkbox or radio the page exposes
        that `/scan_required` does not mention is exactly the shape of the miss we know about —
        the lone required acknowledgment checkbox that scanned as complete while Continue was
        blocked with "Choose an option to continue" (07-18).

        Returns SEMANTIC rows only (role + name), never selectors — this feeds a verdict that
        gets journaled.
        """
        scan = self._post("/scan_required", self._addr())
        rows = scan.get("unanswered") or []
        known = {_question_of(r.get("field") or r.get("name")) for r in rows if isinstance(r, dict)}

        ax = self._post("/ax_scan", self._addr())
        missed: list[dict] = []
        for c in (ax.get("candidates") or []):
            role = str(c.get("role") or "").lower()
            if role not in _CONSENT_ROLES:
                continue
            name = str(c.get("name") or "").strip()
            if name and _question_of(name) not in known:
                missed.append({"role": role, "name": name})
        return tuple(missed)

    def commit_widget(self, field: str, value: str) -> bool:
        """Run the open->stage->commit protocol on a composite widget that staged without
        committing. `/select_option` already implements the protocol; what it needs and the
        plain click never supplied is the widget's `commit` descriptor, so ask
        `/describe_widget` for it first rather than re-clicking and hoping (widget-protocol
        §6/§7 — the Ethnicity react-select, 07-18)."""
        addr = self._address(field)
        if addr is None:
            return False
        described = self._post("/describe_widget", {**self._addr(),
                                                    "selector": addr.get("selector"),
                                                    "ats": self._ats, "field": field})
        res = self._post("/select_option", {
            **self._addr(), "selector": addr.get("selector"), "value": value,
            "ats": self._ats, "field": field,
            "commit": described.get("commit") or addr.get("commit"),
            "widget_type": described.get("widget_type") or addr.get("widget_type")})
        return _outcome_of(res) == Outcome.OK.value


def run_live_apply(*, browser_url: str, tab_id: str, task: str = "indeed_apply",
                   capture_server_url: Optional[str] = None, use_model: bool = False,
                   budget_limit: Optional[float] = None, reviewer=None, record_only: bool = False,
                   session_id: str = "", max_steps: int = 40, transport: Optional[Transport] = None):
    """Wire a run_controller drive over the live tab (LiveActuator + program store), then run it.

    NOTE (PRINCIPLES §9): for a live *teaching* drive, prefer `teach_session.open_live_teach_session`
    — the teacher demonstrates each step and Haiku SHADOWS. This entrypoint runs the autonomous loop,
    and `use_model=True` puts Haiku in the ACTING seat as the DAgger stand-in (proposes under the
    `reviewer`) — that is off by default, because Haiku is the cheap backstop, not the acting brain.
    Use it only when a reviewer (or, later, a trained L4) is in the seat. SUBMIT is held by the loop."""
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
