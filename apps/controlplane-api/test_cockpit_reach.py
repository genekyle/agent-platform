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


def _driven_queue(job_id="indeed:driven", title="Driven One"):
    """A pick somebody has actually worked — one recorded mini is what makes it real. `record`
    flips QUEUED to OPEN, which is the same transition a live drive makes."""
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": job_id, "title": title}])
    q.steps[0].record("open_pane", "ok", "detail pane loaded")
    return q


def test_an_unopened_pick_does_not_block_a_new_search_and_is_named_in_the_journal(monkeypatch):
    """A PICK NOBODY HAS OPENED COSTS NOTHING TO RELEASE, and the old guard could not tell it from
    a half-finished application.

    `queue.current()` is "the first step with no terminal flag", which is true of a freshly queued
    card — so a session holding four untouched candidates refused a new search by naming a job that
    had never been driven. Releasing them is right; releasing them SILENTLY is what put four
    candidates outside the record, so the journal names each one and says what happens next.
    """
    bb = _at_start_line(query="data analytics")
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:untouched", "title": "Untouched One"}])
    bb.world["apply_queue"] = q.as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "reporting analyst"})
    finally:
        _teardown()
    assert r.status_code == 200
    step_back = [e for e in saved["bb"].events if e.kind == "search_step_back"]
    assert len(step_back) == 1
    assert "Untouched One" in step_back[0].detail          # named, not merely counted
    assert step_back[0].why                                 # why we stepped back
    assert "reporting analyst" in step_back[0].next_up      # and what happens now
    assert "FRESH selection" in step_back[0].next_up
    assert aps.Queue.from_dict(saved["bb"].world.get("apply_queue")).steps == []


def test_a_driven_application_blocks_a_new_search_until_a_reason_is_given(monkeypatch):
    """The refusal that keeps the harvest honest — now priced on WORK DONE rather than on the
    absence of a terminal flag. And it names the way out, because a truthful refusal the operator
    cannot act on is still a dead end."""
    bb = _at_start_line(query="data analytics")
    bb.world["apply_queue"] = _driven_queue().as_dict()
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
    detail = r.json()["detail"]
    assert "Driven One" in detail
    assert "release_open" in detail          # the field that carries the way out
    assert "resumable" in detail             # and the promise that makes it safe to press


def test_the_reason_releases_the_driven_application_and_parks_it_resumable(monkeypatch):
    """Given the reason, the step back happens — and the driven application is PARKED with that
    reason rather than discarded, so `apply_reopen` can bring it back. `parked:operator` is exactly
    "your call — not now, come back to it", which is what stepping back actually means."""
    bb = _at_start_line(query="data analytics")
    bb.world["apply_queue"] = _driven_queue().as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "reporting analyst",
                              "release_open": "wrong candidates for this query"})
    finally:
        _teardown()
    assert r.status_code == 200
    survivors = saved["bb"].world.get("parked_apps") or []
    assert [p["job_id"] for p in survivors] == ["indeed:driven"]
    assert survivors[0]["terminal"] == aps.PARKED_OPERATOR
    # The operator's own words ride onto the step, not just into the log line.
    assert "wrong candidates for this query" in survivors[0]["terminal_detail"]
    step_back = [e for e in saved["bb"].events if e.kind == "search_step_back"][0]
    # The `why` names WHICH refusal was paid, because two of them can be paid in one press.
    assert step_back.why == "released open work: wrong candidates for this query"
    assert aps.Queue.from_dict(saved["bb"].world.get("apply_queue")).steps == []


# --- running the same terms again, on purpose --------------------------------------------------

def test_the_same_query_is_a_no_op_without_a_reason(monkeypatch):
    """The once-only guard's real job: refusing the ACCIDENTAL repeat. Re-declaring the terms this
    search is already running changes nothing and spends nothing — it must not quietly burn a
    search ordinal, and it must not re-submit."""
    bb = _at_start_line(query="report analyst", location="Manchester, NH")
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "report analyst", "location": "Manchester, NH"})
    finally:
        _teardown()
    assert r.status_code == 200
    assert r.json()["search"]["n"] == 1                      # still the same search
    assert [e for e in saved["bb"].events if e.kind == "search_step_back"] == []


def test_a_reason_reruns_spent_terms_as_a_brand_new_search(monkeypatch):
    """SAME TERMS, ASKED AGAIN — a decision, not an accident (operator-directed 2026-08-14).

    `query_entered` is consuming because repeating a query TOO OFTEN gets it collapsed; that is a
    rule about frequency, and enforcing it as a rule about EVER meant the most ordinary thing a job
    search does — the same terms tomorrow, when the postings have turned over — had two answers,
    both wrong: a silent no-op, or a flat 409. With a reason it starts a genuinely new search, and
    the reason is on the record.
    """
    bb = _at_start_line(query="report analyst", location="Manchester, NH")
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:stale", "title": "Yesterday's Pick"}])
    bb.world["apply_queue"] = q.as_dict()
    bb.search_state.approved = ["indeed:stale"]
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "report analyst", "location": "Manchester, NH",
                              "radius_miles": 100,
                              "rerun_spent": "a day on, the postings have turned over"})
    finally:
        _teardown()
    assert r.status_code == 200
    view = r.json()
    assert view["search"]["n"] == 2                          # a genuinely new search
    assert view["picks"] == []                               # chosen off a page that is being re-read
    assert aps.Queue.from_dict(saved["bb"].world.get("apply_queue")).steps == []

    step = [e for e in saved["bb"].events if e.kind == "search_step_back"][0]
    # The arrow notation would read `'x' -> 'x'` here, which is not what happened.
    assert "re-running 'report analyst'" in step.detail
    assert step.why == ("re-running spent terms deliberately: a day on, the postings have "
                        "turned over")
    assert "FRESH selection" in step.next_up

    # AND THE ONCE-ONLY PROMISE IS STILL REAL: search 1's spend stays on the ledger, so an
    # unreasoned repeat is still refused rather than quietly allowed from here on.
    ledger = cps.Ledger.from_dict(saved["bb"].checkpoints)
    assert ledger.has_spent("report analyst")


def test_an_unreasoned_repeat_of_another_searchs_query_is_still_refused(monkeypatch):
    """Going BACK to terms a previous search spent, with no reason given, still refuses — and now
    names the way through instead of only the way back."""
    bb = _at_start_line(query="data analytics")
    ledger = cps.Ledger.from_dict(bb.checkpoints)
    ledger.note_query("data analytics")
    ledger.start_new_search()
    ledger.note_query("report analyst")
    ledger.mark("query_entered", evidence="ran")
    bb.checkpoints = ledger.as_dict()
    bb.search_state.query = "report analyst"
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "data analytics"})
    finally:
        _teardown()
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "already ran" in detail
    assert "rerun_spent" in detail            # the refusal carries its own way through


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


def test_picking_a_job_that_is_already_parked_restores_it_instead_of_queueing_a_blank(monkeypatch):
    """THE REPICK'S OWN TRAP, found live 2026-08-14. Re-running the same terms surfaces the same
    jobs, and one of them was parked one screen from Submit with five fields filled. `enqueue` is
    idempotent by job_id, but a parked SURVIVOR lives at session level (that is the whole point of
    the harvest) — so picking it would have created a fresh empty step beside the real one, and the
    surface would have offered "Open the posting" over an application already most of the way
    done."""
    # The fixture's own query, so `query_entered` observes true against SEARCH_URL and `/choose`
    # gets past the start-line gate — the thing under test is the pick, not the ladder.
    bb = _at_start_line()
    # Real progress on it — the whole point is that a blank duplicate would have HIDDEN this.
    parked = _parked_queue(job_id="indeed:bch", title="Analyst I, Healthcare Data")
    parked.steps[0].minis.append(
        aps.MiniStep(rung="fill_form", outcome="ok", detail="name/email/phone/zip filled"))
    held = {**parked.steps[0].as_dict(), "from_search": 1, "from_page": 1}
    bb.world["parked_apps"] = [held]
    bb.world["apply_queue"] = aps.Queue(page=1).as_dict()
    bb.world["page_results"] = [{"job_id": "indeed:bch", "title": "Analyst I, Healthcare Data"},
                                {"job_id": "indeed:fresh", "title": "Someone New"}]
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:bch", "indeed:fresh"], "advance": False})
    finally:
        _teardown()
    assert r.status_code == 200

    queue = aps.Queue.from_dict(saved["bb"].world.get("apply_queue"))
    assert [s.job_id for s in queue.steps] == ["indeed:bch", "indeed:fresh"]
    bch = queue.steps[0]
    # It came back AS the parked step — flag intact, walked rungs intact — not as a blank one.
    assert bch.terminal == aps.PARKED_OPERATOR
    assert len(bch.minis) > 0
    # And it is no longer double-booked at session level.
    assert saved["bb"].world.get("parked_apps") == []
    # The genuinely new pick is a fresh step, as it should be.
    assert queue.steps[1].terminal is None and queue.steps[1].minis == []

    restored = [e for e in saved["bb"].events if e.kind == "choose_restored"]
    assert len(restored) == 1
    assert "Analyst I, Healthcare Data" in restored[0].detail
    assert restored[0].why and restored[0].next_up
