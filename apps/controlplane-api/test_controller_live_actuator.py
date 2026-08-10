"""Offline tests for the LiveActuator — proving the dispatch table is correct against a FAKE
capture server, so the seam is trustworthy before it ever drives a real job application.

The one seam that makes this possible: `transport` is injected. The real one POSTs to the mcp
capture server; here it's a recorder that returns canned responses and lets us assert exactly
which endpoint each Decision hits and with what payload.
"""

from __future__ import annotations

import pytest

from controller.live_actuator import LiveActuator
from interaction.contract import Outcome
from interaction.decision import Decision

_INDEED = "https://smartapply.indeed.com/questions"
_GREENHOUSE = "https://job-boards.greenhouse.io/acme/jobs/123"


class FakeTransport:
    """Records (path, payload) and returns canned responses; can be told to raise on a path."""

    def __init__(self, *, url, unanswered=None, logged_in=True, responses=None, raise_on=None,
                 page_text="", ax_candidates=None, ax_errors=None):
        self.calls = []
        self._url = url
        self._unanswered = unanswered or []
        self._logged_in = logged_in
        self._responses = responses or {}
        self._raise_on = set(raise_on or ())
        self._page_text = page_text
        # A control set that is present but unremarkable, so observe() is not "blind" by default.
        self._ax = [{"role": "button", "name": "Continue"}] if ax_candidates is None \
            else ax_candidates
        self._ax_errors = list(ax_errors or ())

    def __call__(self, path, payload):
        self.calls.append((path, dict(payload)))
        if path in self._raise_on:
            raise RuntimeError("transport boom")
        # An explicit `responses` entry wins over the canned defaults — that is how a test says
        # "this probe FAILED" for the observe-path probes, not just the act-path endpoints.
        if path in self._responses:
            return self._responses[path]
        if path == "/auth_state":
            return {"ok": True, "logged_in": self._logged_in, "url": self._url,
                    "page_text": self._page_text}
        if path == "/scan_required":
            return {"ok": True, "outcome": "ok", "unanswered": self._unanswered}
        if path == "/ax_scan":
            return {"ok": True, "count": len(self._ax), "candidates": self._ax,
                    "errors": self._ax_errors}
        return self._responses.get(path, {"ok": True, "outcome": "ok"})

    def paths(self):
        return [p for p, _ in self.calls]

    def payload_for(self, path):
        return next(pl for p, pl in self.calls if p == path)


def _actuator(fake, *, task="indeed_apply"):
    return LiveActuator(base_url="http://x", browser_url="http://localhost:9328",
                        tab_id="TAB", task=task, transport=fake)


# --- observe ---------------------------------------------------------------------------
def test_observe_builds_bundle_and_keeps_raw_scan():
    raw = [{"field": "work_authorization", "selector": "#q1", "kind": "radio"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw)
    act = _actuator(fake)

    bundle = act.observe()

    # smartapply classifies as `indeed_quick_apply` — which is exactly why Indeed's dynamic
    # fields fall through apply_fields (keyed under `indeed`) to the live scan below.
    assert bundle.ats == "indeed_quick_apply"
    assert bundle.url == _INDEED
    # The bundle carries the SANITIZED unanswered (no selector leaks to decide()).
    assert all("selector" not in u for u in bundle.unanswered)
    assert any(u.get("field") == "work_authorization" for u in bundle.unanswered)
    # ...but the actuator kept the RAW scan (with selectors) for act().
    assert act._last_scan == raw
    assert "/auth_state" in fake.paths() and "/scan_required" in fake.paths()


def test_observe_logged_out_escalates_to_human():
    fake = FakeTransport(url=_INDEED, logged_in=False)
    bundle = _actuator(fake).observe()
    assert bundle.human_required is True
    assert "logged in" in bundle.branch_note or "authenticate" in bundle.branch_note


# --- observe: perception (added 2026-07-20 — PLAN_supervisor §0a) -----------------------
def test_observe_scans_ax_and_carries_the_identities_for_the_delta():
    """The supervisor's sense organ. Without these, `StateDelta` is empty on every live turn and
    the loop is back to comparing url+state — blind to a modal, a banner, a disabled button."""
    fake = FakeTransport(url=_INDEED, ax_candidates=[
        {"role": "button", "name": "Continue"},
        {"role": "textbox", "name": "Why do you want this role?"},
        {"role": "generic", "name": "$1,299.00"},        # purely volatile — carries no identity
    ])
    bundle = _actuator(fake).observe()

    assert "/ax_scan" in fake.paths()
    assert bundle.ax_identities == ("button|continue", "textbox|why do you want this role?")


def test_observe_passes_page_text_so_workday_state_is_classifiable_at_all():
    """Workday and Greenhouse are single-origin SPAs whose step lives in the PAGE, not the URL —
    `map_workday_state` reads markers out of page_text and nothing else. Until 2026-07-20 observe()
    passed "", so every Workday step collapsed to `workday_job_posting`/`unknown`."""
    fake = FakeTransport(url="https://acme.wd1.myworkdayjobs.com/External/apply",
                         page_text="My Information\nLegal Name\nCountry")
    bundle = _actuator(fake, task="workday_apply").observe()
    assert bundle.state == "workday_my_information"

    blind = FakeTransport(url="https://acme.wd1.myworkdayjobs.com/External/apply", page_text="")
    assert _actuator(blind, task="workday_apply").observe().state == "workday_job_posting"


def test_observe_can_see_a_captcha_now_that_page_text_flows():
    """The safety-relevant half. `_CHALLENGE_MARKERS` are page-text-only, so with page_text=""
    the controller was structurally unable to notice a challenge — the one thing it must always
    escalate and never auto-solve."""
    fake = FakeTransport(url="https://acme.wd1.myworkdayjobs.com/External/apply",
                         page_text="Verify you are human before continuing")
    bundle = _actuator(fake, task="workday_apply").observe()
    assert bundle.state == "captcha"
    assert bundle.human_required is True


# --- observe: a failed probe is a handoff, never a benign reading -----------------------
def test_a_failed_auth_probe_does_not_read_as_signed_in():
    """`auth.get("logged_in", True)` defaulted a DEAD PROBE to "we're signed in" — the 2026-07-19
    silence bug, still live in this path until today."""
    fake = FakeTransport(url=_INDEED, responses={"/auth_state": {"ok": False, "detail": "no target"}})
    bundle = _actuator(fake).observe()
    assert bundle.human_required is True
    assert "cannot observe" in bundle.branch_note and "auth probe failed" in bundle.branch_note


def test_a_failed_required_scan_does_not_read_as_a_completed_form():
    fake = FakeTransport(url=_INDEED,
                         responses={"/scan_required": {"ok": False, "detail": "eval failed"}})
    bundle = _actuator(fake).observe()
    assert bundle.human_required is True
    assert "required-field scan failed" in bundle.branch_note


def test_an_empty_ax_scan_WITH_errors_is_a_stale_tab_not_an_empty_page():
    """The exact signature from LEARNINGS 2026-07-19: `propose_ax_candidates` swallows a
    target-discovery failure into `errors[]` and returns HTTP 200 with candidates=[], so a dead
    target and a page with no controls are indistinguishable unless you read `errors`."""
    fake = FakeTransport(url=_INDEED, ax_candidates=[],
                         ax_errors=["target_discovery: No target with id TAB"])
    bundle = _actuator(fake).observe()
    assert bundle.human_required is True
    assert "stale tab" in bundle.branch_note
    assert bundle.state is None          # we do not claim to know where we are


def test_an_empty_ax_scan_WITHOUT_errors_is_a_real_reading():
    """A genuinely control-less page is a real observation — the supervisor classifies it, this
    method must not pre-empt that by crying stale tab on every quiet page."""
    fake = FakeTransport(url=_INDEED, ax_candidates=[], ax_errors=[])
    bundle = _actuator(fake).observe()
    assert bundle.human_required is False
    assert bundle.ax_identities == ()


# --- act: field addressing -------------------------------------------------------------
def test_set_text_uses_the_live_scan_selector_for_indeed_dynamic_field():
    # Indeed apply questions aren't in apply_fields (INDEED_FIELDS is ~empty) — their selector
    # comes from the live scan. This is the fallback path the resolver can't cover.
    raw = [{"field": "years_experience", "selector": "#input-42", "kind": "text"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw)
    act = _actuator(fake)
    act.observe()

    out = act.act(Decision(intent="set_text", params={"field": "years_experience", "value": "5"},
                           confidence=1.0, rung="model", rationale="", expected_next=()))

    exe = fake.payload_for("/execute")
    assert exe["action_id"] == "type"
    assert exe["selector"] == "#input-42"      # from the live scan, not a guess
    assert exe["value"] == "5"
    assert exe["tab_id"] == "TAB"              # addressed by stable tab id, not tab_url
    assert out.outcome == Outcome.OK.value


def test_select_option_resolves_static_greenhouse_selector():
    fake = FakeTransport(url=_GREENHOUSE, responses={"/select_option": {"ok": True, "outcome": "ok"}})
    act = _actuator(fake, task="greenhouse_apply")
    act.observe()
    assert act._ats == "greenhouse"

    act.act(Decision(intent="select_option", params={"field": "school", "value": "Other"},
                     confidence=1.0, rung="model", rationale="", expected_next=()))

    body = fake.payload_for("/select_option")
    assert body["selector"] == "#school--0"    # from apply_fields.resolve, the static recipe
    assert body["value"] == "Other"
    assert body["ats"] == "greenhouse" and body["field"] == "school"


def test_click_drives_execute_by_control_name():
    fake = FakeTransport(url=_INDEED)
    act = _actuator(fake)
    act.observe()

    act.act(Decision(intent="click", params={"control": "Continue"},
                     confidence=1.0, rung="recipe", rationale="", expected_next=("next",)))

    exe = fake.payload_for("/execute")
    assert exe["action_id"] == "click"
    assert exe["target_name"] == "Continue"


def test_unaddressable_field_is_not_found_not_a_guess():
    fake = FakeTransport(url=_INDEED, unanswered=[])   # empty scan, no static entry
    act = _actuator(fake)
    act.observe()
    out = act.act(Decision(intent="set_text", params={"field": "ghost_field", "value": "x"},
                           confidence=1.0, rung="model", rationale="", expected_next=()))
    assert out.outcome == Outcome.NOT_FOUND.value
    assert "/execute" not in fake.paths()          # never fired a guessed action


# --- act: the SUBMIT gate --------------------------------------------------------------
def test_submit_is_refused_never_driven():
    fake = FakeTransport(url=_INDEED)
    act = _actuator(fake)
    act.observe()
    n_before = len(fake.calls)
    out = act.act(Decision(intent="submit", params={}, confidence=1.0, rung="model",
                           rationale="", expected_next=()))
    assert out.outcome == Outcome.BLOCKED.value
    # Nothing was posted for a submit — the actuator refuses before any endpoint call.
    assert len(fake.calls) == n_before


# --- resilience: never raise into the loop ---------------------------------------------
def test_transport_error_becomes_error_outcome_not_a_crash():
    raw = [{"field": "years_experience", "selector": "#input-42", "kind": "text"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw, raise_on={"/execute"})
    act = _actuator(fake)
    act.observe()
    out = act.act(Decision(intent="set_text", params={"field": "years_experience", "value": "5"},
                           confidence=1.0, rung="model", rationale="", expected_next=()))
    assert out.outcome == Outcome.ERROR.value       # a clean handoff, not an exception


def test_observe_survives_a_scan_transport_failure():
    fake = FakeTransport(url=_INDEED, raise_on={"/scan_required"})
    bundle = _actuator(fake).observe()              # must not raise
    assert bundle.url == _INDEED
    assert bundle.unanswered == ()                  # degraded to no known unanswered fields


# --- native radios / acknowledgments: the routing found live 2026-07-18 -----------------
# The tier-2 endpoints can't drive native radios; only /autofill_form's native input.click() can.
_RADIO_Q = "Are you at least 18 years of age? * Yes No"
_FILLED = {"/autofill_form": {"ok": True, "report": [{"status": "filled"}]}}


def _dec(intent, **params):
    return Decision(intent=intent, params=params, confidence=1.0, rung="teacher",
                    rationale="", expected_next=())


def test_select_option_on_radio_group_routes_to_native_autofill():
    raw = [{"field": _RADIO_Q, "selector": None, "kind": "radio_group"}]  # note: NO selector
    fake = FakeTransport(url=_INDEED, unanswered=raw, responses=_FILLED)
    act = _actuator(fake)
    act.observe()
    out = act.act(_dec("select_option", field=_RADIO_Q, value="Yes"))

    assert "/autofill_form" in fake.paths() and "/select_option" not in fake.paths()
    ans = fake.payload_for("/autofill_form")["answers"][0]
    assert ans["value"] == "Yes"
    # at least one pattern is the QUESTION text stripped of the appended "* Yes No", so autofill
    # can find it as a substring of the DOM question (the full field is kept only as a fallback)
    assert any("at least 18 years of age" in p.lower() and "yes no" not in p.lower()
               for p in ans["patterns"])
    assert out.outcome == Outcome.OK.value


def test_check_group_on_radio_routes_to_native_autofill():
    raw = [{"field": _RADIO_Q, "selector": None, "kind": "radio_group"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw, responses=_FILLED)
    act = _actuator(fake)
    act.observe()
    act.act(_dec("check_group", field=_RADIO_Q, values=["Yes"]))
    assert "/autofill_form" in fake.paths() and "/check_group" not in fake.paths()


def test_affirmation_checkbox_routes_to_native_autofill():
    q = "I have read and accept the above acknowledgement"
    raw = [{"field": q, "selector": None, "kind": "checkbox_group"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw, responses=_FILLED)
    act = _actuator(fake)
    act.observe()
    act.act(_dec("check_group", field=q, value="Accept"))       # an acknowledgment = a native click
    assert "/autofill_form" in fake.paths() and "/check_group" not in fake.paths()


def test_multi_checkbox_group_still_uses_the_check_group_endpoint():
    q = "Which restrictions apply?"
    raw = [{"field": q, "selector": "#restrictions", "kind": "checkbox_group"}]
    fake = FakeTransport(url=_INDEED, unanswered=raw)
    act = _actuator(fake)
    act.observe()
    act.act(_dec("check_group", field=q, values=["No non-compete"]))  # labelled, not an affirmation
    assert "/check_group" in fake.paths() and "/autofill_form" not in fake.paths()


def test_react_select_still_uses_the_select_option_endpoint():
    # greenhouse 'school' resolves statically to a react_select — must NOT be routed to autofill
    fake = FakeTransport(url=_GREENHOUSE, responses={"/select_option": {"ok": True, "outcome": "ok"}})
    act = _actuator(fake, task="greenhouse_apply")
    act.observe()
    act.act(_dec("select_option", field="school", value="Other"))
    assert "/select_option" in fake.paths() and "/autofill_form" not in fake.paths()


# --- _current_state settles before classifying (a stale read reported the wrong state live) ----
class SeqAuthTransport:
    """Returns a SEQUENCE of urls for /auth_state — simulates a navigation in progress."""

    def __init__(self, urls):
        self._urls = list(urls)
        self._i = 0
        self.calls = []

    def __call__(self, path, payload):
        self.calls.append((path, dict(payload)))
        if path == "/auth_state":
            u = self._urls[min(self._i, len(self._urls) - 1)]
            self._i += 1
            return {"ok": True, "logged_in": True, "url": u}
        if path == "/scan_required":
            return {"ok": True, "unanswered": []}
        return {"ok": True, "outcome": "ok"}


def test_current_state_settles_to_the_landed_url_not_a_stale_read():
    old = "https://smartapply.indeed.com/beta/indeedapply/form/resume-selection"
    new = "https://smartapply.indeed.com/beta/indeedapply/form/questions-module/questions/1"
    # observe reads `old`; the first _current_state read is STILL `old` (stale), then settles to `new`
    fake = SeqAuthTransport([old, old, new, new])
    act = LiveActuator(base_url="http://x", browser_url="http://localhost:9328", tab_id="T",
                       transport=fake, sleep_fn=lambda _: None)
    act.observe()
    out = act.act(_dec("click", control="Continue"))
    # settled to the NEW url's state, not the stale first read (would have been resume_selection)
    assert out.landed_state == "indeed_apply_questions"


# --- field addressing vs the scan's mangled labels (found live 2026-07-19, Longroad) ---------
def test_question_of_recovers_the_stable_half():
    from controller.live_actuator import _question_of
    scan = "Do you now or will you in the future require sponsorship for a work visa? * No, I do not r"
    assert _question_of(scan) == (
        "do you now or will you in the future require sponsorship for a work visa?")
    assert _question_of("Gender *") == "gender"
    assert _question_of(None) == ""


def test_same_field_matches_the_scans_truncated_option_padded_label():
    """/scan_required labels a radio group with its question PLUS its first option, truncated. No
    answer key or program step will ever EQUAL that, so exact matching made every Indeed question
    page unaddressable — the drive stalled at NOT_FOUND on all four fields."""
    same = LiveActuator._same_field
    scan = "Do you now or will you in the future require sponsorship for a work visa? * No, I do not r"
    asked = "Do you now or will you in the future require sponsorship for a work visa?"
    assert same(scan, asked)

    # truncated mid-question (a very long question) still matches from the other direction
    assert same("Do you have at least 1-2 years of demonstrated experience in Treasury, Fin",
                "Do you have at least 1-2 years of demonstrated experience in Treasury, Finance, "
                "or Accounting?")


def test_same_field_does_not_collide_across_different_questions():
    same = LiveActuator._same_field
    assert not same("Are you currently legally authorized to work in the United States? * Yes",
                    "Do you now or will you in the future require sponsorship for a work visa?")
    assert not same("", "anything")
    # a short shared prefix must not be enough to call two questions the same
    assert not same("Do you have a car? * Yes", "Do you have a degree? * Yes")


def test_ambiguous_field_refuses_to_pick_rather_than_answering_the_wrong_question():
    """Two scan rows answering to one name means we cannot tell them apart. Choosing the first
    would fill the WRONG radio group on a real application, so addressing returns None and the
    step escalates — the same refusal _discover_target makes for tabs."""
    act = LiveActuator(base_url="http://x", browser_url="http://b", tab_id="t",
                       task="indeed_apply", transport=FakeTransport(url=_INDEED))
    act._ats = "indeed_quick_apply"
    act._last_scan = [
        {"field": "Are you authorized to work? * Yes", "kind": "radio_group", "selector": "#a"},
        {"field": "Are you authorized to work? * No", "kind": "radio_group", "selector": "#b"},
    ]
    assert act._address("Are you authorized to work?") is None

    act._last_scan = act._last_scan[:1]          # unambiguous again -> addressable
    assert act._address("Are you authorized to work?") is not None


# --- S12b: the RecoveryActuator plays, live-side ---------------------------------------
def test_re_resolve_tab_adopts_the_fresh_target():
    """The 07-19 discipline: the wrong-tab refusal is CORRECT, so the fix is to re-discover the
    right tab, never to loosen the guard. `re_resolve` is injected so this module stays DB-free."""
    fake = FakeTransport(url=_INDEED)
    act = LiveActuator(base_url="http://x", browser_url="http://localhost:9328", tab_id="OLD",
                       transport=fake,
                       re_resolve=lambda: {"tab_id": "NEW", "browser_url": "http://localhost:9999"})
    assert act.re_resolve_tab() is True
    act.observe()
    assert fake.payload_for("/auth_state")["tab_id"] == "NEW"


def test_re_resolve_tab_reports_failure_rather_than_pretending():
    fake = FakeTransport(url=_INDEED)
    assert _actuator(fake).re_resolve_tab() is False               # no re_resolve wired
    act = LiveActuator(base_url="http://x", browser_url="b", tab_id="OLD", transport=fake,
                       re_resolve=lambda: None)
    assert act.re_resolve_tab() is False                            # nothing found


def test_rescan_required_finds_the_consent_checkbox_the_scan_missed():
    """The Lactalis miss (07-18): `/scan_required` reported 0 unanswered while Continue was
    blocked by a lone required acknowledgment checkbox. `/scan_form` was deleted in July, so the
    second instrument is the AX tree."""
    fake = FakeTransport(url=_INDEED, unanswered=[], ax_candidates=[
        {"role": "button", "name": "Continue"},                     # not a consent control
        {"role": "checkbox", "name": "I have read and accept the terms"},
        {"role": "textbox", "name": "Salary"},                       # wrong role, ignored
    ])
    missed = _actuator(fake).rescan_required()
    assert missed == ({"role": "checkbox", "name": "I have read and accept the terms"},)


def test_rescan_required_ignores_controls_the_scan_already_knows_about():
    """A control the ordinary scan already reports is not a miss — returning it would send the
    loop chasing a field it is already handling."""
    fake = FakeTransport(url=_INDEED,
                         unanswered=[{"field": "Work authorization *  Yes", "kind": "radio"}],
                         ax_candidates=[{"role": "radio", "name": "Work authorization"}])
    assert _actuator(fake).rescan_required() == ()


def test_rescan_required_returns_semantic_rows_only():
    """It feeds a verdict that gets journaled — no selectors, no node ids (invariant #10)."""
    fake = FakeTransport(url=_INDEED, unanswered=[], ax_candidates=[
        {"role": "checkbox", "name": "I acknowledge", "backend_node_id": 4821,
         "bbox": {"x": 1, "y": 2}}])
    assert _actuator(fake).rescan_required() == ({"role": "checkbox", "name": "I acknowledge"},)


def test_settle_waits_then_reclassifies():
    slept = []
    fake = FakeTransport(url=_INDEED)
    act = LiveActuator(base_url="http://x", browser_url="b", tab_id="TAB", transport=fake,
                       sleep_fn=slept.append)
    act.settle()
    assert slept                                                    # it actually waited
    assert "/auth_state" in fake.paths()                            # and looked again


# --- the submit release, at the actuator (operator-authorised 2026-07-22) --------------
def _submit_actuator(calls, *, allow_submit):
    from controller.live_actuator import LiveActuator

    def _transport(path, payload):
        calls.append((path, payload))
        if path == "/auth_state":
            return {"ok": True, "logged_in": True, "url": "https://smartapply.indeed.com/x",
                    "title": "Review"}
        if path in ("/scan_required", "/ax_scan"):
            return {"ok": True, "unanswered": [], "candidates": [{"role": "button", "name": "b"}]}
        return {"ok": True, "outcome": "ok"}

    return LiveActuator(base_url="http://x", browser_url="http://b", tab_id="t",
                        transport=_transport, collect=False, allow_submit=allow_submit)


def test_the_actuator_refuses_submit_unless_the_run_was_authorised():
    from interaction.decision import Decision
    calls = []
    out = _submit_actuator(calls, allow_submit=False).act(
        Decision(intent="submit", params={"control": "Submit your application"},
                 confidence=1.0, rung="teacher", rationale="operator-authorised submit"))
    assert out.outcome == "blocked"
    assert not [p for p, _ in calls if p == "/execute"], "a held submit still reached the page"


def test_an_authorised_submit_clicks_the_named_control():
    from interaction.decision import Decision
    calls = []
    out = _submit_actuator(calls, allow_submit=True).act(
        Decision(intent="submit", params={"control": "Submit your application"},
                 confidence=1.0, rung="teacher", rationale="operator-authorised submit"))
    assert out.outcome == "ok"
    execs = [p for path, p in calls if path == "/execute"]
    assert execs and execs[0]["target_name"] == "Submit your application"


def test_an_authorised_submit_still_refuses_to_guess_which_button_ends_the_application():
    """Every other intent may fall back to a lookup. The irreversible one does not."""
    from interaction.decision import Decision
    calls = []
    out = _submit_actuator(calls, allow_submit=True).act(Decision(intent="submit", params={}, confidence=1.0, rung="teacher",
                                   rationale="operator-authorised submit"))
    assert out.outcome == "not_found"
    assert not [p for p, _ in calls if p == "/execute"]


# --- staleness: the datapoint that rides with every observation -------------------------
# PROTOTYPE (2026-07-26). These pin the WIRING and the safety gate, not the thresholds — the
# thresholds are guesses and `test_staleness.py` owns their shape. What must hold here is that no
# observation can leave without the datapoint, and that a drive holding typed work never has a
# destructive remedy proposed for it.
def test_every_observation_carries_a_staleness_datapoint():
    fake = FakeTransport(url=_INDEED)
    bundle = _actuator(fake).observe()

    assert bundle.staleness is not None, "staleness must ride along with every observation"
    assert bundle.staleness["level"] in ("fresh", "yellow", "orange", "red")
    assert bundle.staleness["verdict"] in ("continue", "refresh", "renew", "handoff")
    # The raw ages are the point of the prototype — the level is today's guess, the seconds are
    # what will fit tomorrow's thresholds.
    names = {s["name"] for s in bundle.staleness["signals"]}
    assert {"idle_s", "page_age_s"} <= names
    assert bundle.staleness["rules_version"]


def test_a_fresh_drive_is_operable_and_says_what_it_could_not_measure():
    fake = FakeTransport(url=_INDEED)
    bundle = _actuator(fake).observe()

    assert bundle.staleness["verdict"] == "continue"
    # Nothing has acted yet and no cookie read exists, so those are UNMEASURED — named, not
    # silently scored as fresh.
    assert "idle_s" in bundle.staleness["unmeasured"]
    assert "cookie_ttl_s" in bundle.staleness["unmeasured"]


def test_a_blind_observation_asks_for_a_human_not_a_refresh():
    """The AX stale-tab signature. Refreshing a page nobody could observe is guessing with a
    page load, so the remedy must be a handoff."""
    fake = FakeTransport(url=_INDEED, ax_candidates=[], ax_errors=["target closed"])
    bundle = _actuator(fake).observe()

    assert bundle.staleness["level"] == "red"
    assert bundle.staleness["verdict"] == "handoff"


def test_a_logged_out_tab_wants_a_fresh_state_not_a_reload():
    fake = FakeTransport(url=_INDEED, logged_in=False)
    bundle = _actuator(fake).observe()

    assert bundle.staleness["verdict"] == "renew"


def test_typing_marks_the_view_as_holding_unsaved_work():
    """A successful write means a reload would discard real effort — the flag the destructive
    remedies are gated on."""
    raw = [{"field": "first_name", "selector": "#fn", "kind": "text"}]
    fake = FakeTransport(url=_GREENHOUSE, unanswered=raw)
    act = _actuator(fake, task="greenhouse_apply")
    act.observe()
    assert act._unsaved_work is False

    act.act(Decision(intent="set_text", params={"field": "first_name", "value": "Gene"},
                     confidence=0.9, rung="recipe", rationale="staleness wiring test"))
    assert act._unsaved_work is True, "a landed write leaves work on the page"


def test_navigating_clears_the_unsaved_work_flag():
    """The work belonged to the page we left. Carrying the flag forward would suppress a
    legitimate refresh on every page after the first form."""
    fake = FakeTransport(url=_GREENHOUSE,
                         unanswered=[{"field": "first_name", "selector": "#fn", "kind": "text"}])
    act = _actuator(fake, task="greenhouse_apply")
    act.observe()
    act.act(Decision(intent="set_text", params={"field": "first_name", "value": "Gene"},
                     confidence=0.9, rung="recipe", rationale="staleness wiring test"))
    assert act._unsaved_work is True

    fake._url = _GREENHOUSE + "/review"      # the drive moved on
    act.observe()
    assert act._unsaved_work is False


def test_a_read_intent_does_not_count_as_unsaved_work():
    fake = FakeTransport(url=_INDEED)
    act = _actuator(fake)
    act.observe()
    act.act(Decision(intent="scan_required", params={}, confidence=0.9, rung="recipe", rationale="staleness wiring test"))
    assert act._unsaved_work is False


# --- capture posture + refs (2026-08-09/10: the teacher's eyes, and where they must close) -----
def test_a_signed_in_turn_captures_and_the_bundle_carries_the_refs():
    """The 0/117 fix end-to-end at this seam: /capture fires, and its durable name rides
    Bundle.capture so record_for can journal what the decision saw."""
    fake = FakeTransport(url=_INDEED)
    fake._responses["/capture"] = {"ok": True, "filename": "a__live_mcp__controller_turn.json"}
    bundle = _actuator(fake).observe()
    assert "/capture" in fake.paths()
    assert bundle.capture is not None
    assert bundle.capture["artifact"] == "a__live_mcp__controller_turn.json"


def test_a_logged_out_turn_captures_nothing_at_all():
    """§4 posture, tightened after review (2026-08-10): an auth surface never enters the corpus.
    The wall right after a takeover can hold a typed e-mail or a password-manager dropdown —
    not visibly signed in means no capture, no screenshot, no refs, structurally."""
    fake = FakeTransport(url=_INDEED, logged_in=False)
    bundle = _actuator(fake).observe()
    assert "/capture" not in fake.paths()
    assert bundle.capture is None


def test_transition_rows_keep_param_names_never_answer_content(monkeypatch):
    """The closed vocabulary carries answers under `value`, `values`, `month` AND `year` — the
    old one-key strip leaked multi-select and date answers into the corpus. Allowlist now."""
    import step_runner as sr_mod
    from controller.live_actuator import transition_recorder
    from interaction.decision import Decision

    recorded = {}
    monkeypatch.setattr(sr_mod, "record_transition",
                        lambda **kw: recorded.update(kw) or "path")
    on_supervise, on_step = transition_recorder("t-allowlist")

    class R:
        outcome = "ok"
        landed_state = "indeed_apply_review"
        unanswered_after = []
        ax_identities = ()

    from test_controller_modes import a_bundle
    decision = Decision("select_option", {"field": "veteran_status", "control": "combo",
                                          "value": "Prefer not to say",
                                          "values": ["Asian", "Veteran"],
                                          "month": 5, "year": 2020},
                        0.9, "recipe", "why not")
    on_step(a_bundle(capture={"artifact": "a.json", "screenshot": "/shots/s.png",
                              "screenshot_filename": "s.png"}), decision, R())
    assert recorded["action"]["params"] == {"field": "veteran_status", "control": "combo"}
    # and the row's before-half carries the capture, field-for-field like step_runner rows
    assert recorded["before"].artifact == "a.json"
    assert recorded["before"].screenshot == "/shots/s.png"
