"""Pins for the 2026-08-10 cockpit-reach build — the operator audit's fixes.

The audit's finding, in one line: the backend was a complete driving system and the cockpit could
reach almost none of it. These tests pin the seams that closed the gap, in order of how expensive
each would be to lose again:

  1. A NEW SEARCH MUST NOT ORPHAN A PARKED APPLICATION. `_reset_for_new_search` used to drop the
     whole queue; parked steps now survive at session level and `apply_reopen` resurrects them.
  2. THE FORM CENSUS IS §4-CLEAN. Answered previews now leave the page — a secret-named field's
     value must never ride one.
  3. A REFUSAL CARRIES THE FORM IT REFUSED OVER (`last.form_scan`), not just prose about it.
  4. A TAUGHT ACT RETURNS ITS OWN ACCOUNT (`taught.detail`) — the enumerate-and-translate move
     depends on the option list riding back.
"""

import apply_state_store as store
import apply_steps as aps
import main
import session_checkpoints as cps
from db import get_db
from fastapi.testclient import TestClient
from routers import session_control as sc

from test_session_control import (  # the shared harness — one fake seam, not two
    SEARCH_URL, _install, _tabs, _teardown, _at_start_line,
)

client = TestClient(main.app)


def _parked_queue(job_id="indeed:nhbb", title="Continuous Improvement Engineer"):
    q = aps.Queue(page=4)
    q.enqueue([{"job_id": job_id, "title": title, "company": "NHBB"}])
    q.steps[0].platform = "smartapply"
    q.steps[0].finish(aps.PARKED_OPERATOR, "a gate only a human can pass")
    return q


# --- 1. parked applications survive the search that ends ---------------------------------------

def test_a_new_search_carries_parked_applications_into_the_session_level_list(monkeypatch):
    """Declaring the next query harvests the old queue's parked steps into `parked_apps` and the
    panel serves them — parked is attention for the SESSION, not a casualty of its search."""
    bb = _at_start_line(query="data analytics", location="Boston, MA")
    bb.world["apply_queue"] = _parked_queue().as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "reporting analyst", "location": "Nashua, NH",
                              "radius_miles": 50})
    finally:
        _teardown()
    assert r.status_code == 200
    view = r.json()
    survivors = saved["bb"].world.get("parked_apps") or []
    assert [p["job_id"] for p in survivors] == ["indeed:nhbb"]
    assert survivors[0]["terminal"] == aps.PARKED_OPERATOR
    assert survivors[0]["from_search"] == 1
    parked = view.get("parked") or []
    assert [p["job_id"] for p in parked] == ["indeed:nhbb"]
    assert parked[0]["in_current_queue"] is False
    # The new search's own queue is genuinely fresh.
    assert aps.Queue.from_dict(saved["bb"].world.get("apply_queue")).steps == []


def test_an_open_application_still_blocks_a_new_search(monkeypatch):
    """The refusal that keeps the harvest honest: IN-FLIGHT work is never dropped silently —
    parked survives a new search precisely because open work cannot reach that code path."""
    bb = _at_start_line(query="data analytics")
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:open", "title": "Open One"}])
    bb.world["apply_queue"] = q.as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "reporting analyst"})
    finally:
        _teardown()
    assert r.status_code == 409
    assert "still open" in r.json()["detail"]


def test_step_back_in_resurrects_a_parked_application_from_a_finished_search(monkeypatch):
    """`apply_reopen` on a job the current queue has never heard of finds it among the harvested
    survivors, re-enqueues it, and reopens it — parked means "not now", never "not reachable"."""
    bb = _at_start_line(query="reporting analyst", location="Nashua, NH")
    held = {**_parked_queue().steps[0].as_dict(), "from_search": 1, "from_page": 4}
    bb.world["parked_apps"] = [held]
    bb.world["apply_queue"] = aps.Queue(page=1).as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_reopen",
                        json={"job_id": "indeed:nhbb",
                              "reason": "operator stepped back in from the parked strip"})
    finally:
        _teardown()
    assert r.status_code == 200
    back = aps.Queue.from_dict(saved["bb"].world["apply_queue"])
    assert [s.job_id for s in back.steps] == ["indeed:nhbb"]
    assert not back.steps[0].done                      # reopened: in flight again
    assert saved["bb"].world.get("parked_apps") == []  # no longer a survivor — it is the work


def test_reopening_a_job_nobody_parked_is_a_404(monkeypatch):
    bb = _at_start_line()
    bb.world["apply_queue"] = aps.Queue(page=1).as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_reopen",
                        json={"job_id": "indeed:ghost", "reason": "operator asked"})
    finally:
        _teardown()
    assert r.status_code == 404
    assert "nor parked" in r.json()["detail"]


# --- 2. the census is §4-clean ------------------------------------------------------------------

def test_the_form_census_redacts_secret_named_fields(monkeypatch):
    """A password's value never reaches the panel, answered or not; an ordinary screener answer
    stays readable for the human judging it."""
    import asyncio

    async def _fake_post(path, payload, timeout=30.0):
        assert path == "/scan_required"
        return {"ok": True,
                "unanswered": [{"field": "Work authorization *", "kind": "radio_group",
                                "answered": False, "valid": True, "value_preview": "",
                                "options": ["Yes", "No"]}],
                "answered": [{"field": "Password *", "kind": "input",
                              "answered": True, "valid": True, "value_preview": "hunter22"},
                             {"field": "How did you hear about us?", "kind": "select",
                              "answered": True, "valid": True, "value_preview": "Job Board"}],
                "steps": [{"step": "scan", "url": "https://smartapply.indeed.com/x"}]}

    monkeypatch.setattr(sc, "_capture_post", _fake_post)
    census = asyncio.run(sc._form_census("http://127.0.0.1:9222", "t1"))
    answered = {r["field"]: r["value_preview"] for r in census["answered"]}
    assert answered["Password *"] == "[redacted:8]"
    assert answered["How did you hear about us?"] == "Job Board"
    assert census["unanswered"][0]["options"] == ["Yes", "No"]


def test_census_story_tells_an_answered_form_from_an_empty_page():
    """"Planned 0 of 0" beside a fully-answered screener and beside a formless page are two
    different mornings — the story must not let them read alike (the audit's 0-of-0 finding)."""
    empty = sc._census_story({"unanswered": [], "answered": []})
    full = sc._census_story({"unanswered": [], "answered": [{"field": "Q1"}, {"field": "Q2"}]})
    open_ = sc._census_story({"unanswered": [{"field": "Q3"}], "answered": [{"field": "Q1"}]})
    assert "no required form fields" in empty
    assert "all 2 field(s) answered" in full
    assert "1 unanswered, 1 answered" in open_
    assert sc._census_story(None) == ""


def test_unanswered_required_hands_the_census_to_its_caller(monkeypatch):
    """The refusal's structured half: the same look that counts the blockers carries the full
    form out through `census_out`, so `/apply_step`'s refusal can render fields, not prose."""
    import asyncio

    async def _fake_post(path, payload, timeout=30.0):
        return {"ok": True,
                "unanswered": [{"field": "Highest education *", "kind": "react_select",
                                "answered": False, "valid": True, "value_preview": ""}],
                "answered": [],
                "steps": [{"step": "scan", "url": "https://smartapply.indeed.com/x"}]}

    monkeypatch.setattr(sc, "_capture_post", _fake_post)
    out = {}
    pending = asyncio.run(sc._unanswered_required("http://127.0.0.1:9222", "t1",
                                                  census_out=out))
    assert pending == ["Highest education *"]
    assert out["form_scan"]["unanswered"][0]["field"] == "Highest education *"


# --- 3/4. the fill serves the census; a taught act returns its account --------------------------

def test_apply_fill_serves_the_form_as_it_stands(monkeypatch):
    """`execute:false` returns `form_scan` beside the plan: the planner only speaks to fields it
    recognises, and the census is what keeps an unrecognised-but-real form visible."""
    bb = _at_start_line()
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:a1", "title": "One"}])
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t0", "url": "https://smartapply.indeed.com/x"}
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://smartapply.indeed.com/x"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/scan_ax": {"ok": True, "candidates": []},
              "/scan_required": {"ok": True,
                                 "unanswered": [{"field": "Are you an Active Employee? *",
                                                 "kind": "radio_group", "answered": False,
                                                 "valid": True, "value_preview": "",
                                                 "options": ["Yes", "No"]}],
                                 "answered": [],
                                 "steps": [{"step": "scan",
                                            "url": "https://smartapply.indeed.com/x"}]}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_fill", json={"execute": False}).json()
    finally:
        _teardown()
    scan = r["last_step"]["form_scan"]
    assert scan["unanswered"][0]["field"] == "Are you an Active Employee? *"
    assert scan["unanswered"][0]["options"] == ["Yes", "No"]
    assert "1 unanswered, 0 answered" in r["last_step"]["detail"]


def test_a_taught_act_returns_its_own_account(monkeypatch):
    """`taught.detail` carries the act's result — a `no_option` select's enumerated choices ride
    it back, so the teacher never re-probes for evidence the drive already fetched."""
    from routers import controller as controller_router

    bb = _at_start_line()
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:a1", "title": "One"}])
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t0", "url": "https://smartapply.indeed.com/x"}

    def _fake_commit(body):
        return {"held": False, "outcome": "no_option", "journaled": True,
                "landed_state": "indeed_apply_questions", "verified": None,
                "detail": "no option matched; the widget offers: Job Board, Referral, Other"}

    monkeypatch.setattr(controller_router, "teach_commit", _fake_commit)
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://smartapply.indeed.com/x"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_teach",
                        json={"intent": "select_option",
                              "params": {"field": "How did you hear about us?",
                                         "value": "(list the options)"},
                              "rationale": "probe to enumerate the closed listbox's choices"}).json()
    finally:
        _teardown()
    taught = r["last_step"]["taught"]
    assert taught["detail"].endswith("Job Board, Referral, Other")
    assert taught["intent"] == "select_option"
    assert taught["field"] == "How did you hear about us?"


# --- the cleanup protocol -----------------------------------------------------------------------

def test_close_out_refuses_silently_discarding_work(monkeypatch):
    """Half-finished applications never die silently: without confirm_discards_work the close-out
    409s and NAMES them — the clean-start rule, applied to the session's whole life."""
    bb = _at_start_line()
    bb.world["apply_queue"] = _parked_queue().as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/close_out", json={})
    finally:
        _teardown()
    assert r.status_code == 409
    assert "Continuous Improvement Engineer" in r.json()["detail"]


def test_close_out_flags_the_work_and_reports_everything(monkeypatch):
    """Confirmed close-out: parked and in-flight steps end abandoned WITH the reason, the
    session-level parked survivors clear, and the report says what happened — while the Chrome
    stop path is exercised through the one seam that knows how."""
    import main as main_mod

    bb = _at_start_line()
    q = _parked_queue()
    q.enqueue([{"job_id": "indeed:open1", "title": "Open One"}])   # an in-flight one too
    bb.world["apply_queue"] = q.as_dict()
    bb.world["parked_apps"] = [{"job_id": "indeed:old", "title": "Old Survivor",
                                "terminal": aps.PARKED_OPERATOR, "from_search": 1}]
    stopped = {}

    def _fake_stop(session_id, force=False, db=None):
        stopped["id"] = session_id
        return None

    monkeypatch.setattr(main_mod, "stop_training_session", _fake_stop)
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/close_out",
                        json={"confirm_discards_work": True,
                              "reason": "tabs mixed old work into the new search"})
    finally:
        _teardown()
    assert r.status_code == 200
    body = r.json()
    assert body["closed"] is True
    assert stopped["id"] == 1
    assert {d["job_id"] for d in body["discarded"]} == {"indeed:nhbb", "indeed:open1",
                                                        "indeed:old"}
    back = aps.Queue.from_dict(saved["bb"].world["apply_queue"])
    assert all(s.terminal == aps.ABANDONED_OPERATOR for s in back.steps)
    assert "tabs mixed old work" in back.steps[0].terminal_detail
    assert saved["bb"].world.get("parked_apps") is None
    assert body["profile_kept"] is not None or body["chrome"]["stopped"]


def test_close_out_of_a_clean_session_needs_no_confirmation(monkeypatch):
    import main as main_mod
    monkeypatch.setattr(main_mod, "stop_training_session",
                        lambda session_id, force=False, db=None: None)
    bb = _at_start_line()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/close_out", json={})
    finally:
        _teardown()
    assert r.status_code == 200
    assert r.json()["discarded"] == []


# --- tab claims: the window knows whose tab each is ---------------------------------------------

def test_tab_claims_survive_the_step_and_cleanup_closes_them(monkeypatch):
    """A tab watched appearing during an application is claimed by it durably — the census dies
    with the step, the claim does not — and the application's terminal cleanup closes every tab
    it still claims (operator, 2026-08-10: associate tabs to the task so finishing it knows
    exactly what to clean)."""
    import asyncio

    bb = _at_start_line()
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:j1", "title": "One"}])
    step = q.steps[0]

    # Crank 1: baseline census (search tab only), then the ATS tab appears on crank 2.
    sc._note_tab_drift(bb, {"tabs": [{"tab_id": "T-search", "url": "https://www.indeed.com/jobs?q=x"}]}, step)
    sc._note_tab_drift(bb, {"tabs": [
        {"tab_id": "T-search", "url": "https://www.indeed.com/jobs?q=x"},
        {"tab_id": "T-ats", "url": "https://careers.example.com/apply/1"}]}, step)
    assert bb.world["tab_claims"]["T-ats"]["job_id"] == "indeed:j1"

    # The queue moves on (census re-keys to another job); the claim survives.
    other = aps.ApplyStep(job_id="indeed:j2", title="Two")
    sc._note_tab_drift(bb, {"tabs": [
        {"tab_id": "T-search", "url": "https://www.indeed.com/jobs?q=x"},
        {"tab_id": "T-ats", "url": "https://careers.example.com/apply/1"}]}, other)
    assert bb.world["tab_claims"]["T-ats"]["job_id"] == "indeed:j1"

    # j1 finishes: cleanup closes the claimed tab (never the search tab) and drops the claim.
    closed_calls = []

    async def _fake_post(path, payload, timeout=30.0):
        if path == "/close_tab":
            closed_calls.append(payload["tab_id"])
            return {"ok": True}
        return {"ok": True, "tabs": []}

    monkeypatch.setattr(sc, "_capture_post", _fake_post)
    step.finish(aps.SUBMITTED, "test")
    obs = {"tabs": [{"tab_id": "T-search", "url": "https://www.indeed.com/jobs?q=x"},
                    {"tab_id": "T-ats", "url": "https://careers.example.com/apply/1"}],
           "search_tab": {"tab_id": "T-search", "url": "https://www.indeed.com/jobs?q=x"}}
    report = asyncio.run(sc._apply_cleanup(bb, obs, "http://127.0.0.1:9222", step))
    assert "T-ats" in closed_calls and "T-search" not in closed_calls
    assert "T-ats" not in bb.world["tab_claims"]
    assert any(c["tab_id"] == "T-ats" for c in report["closed"])
