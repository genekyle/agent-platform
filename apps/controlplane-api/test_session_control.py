"""Tests for the session control panel — the crank the local side turns.

The cadence talks to a session Chrome and the capture server, neither of which exists in a unit
test. Both go through the `_capture_post` seam plus the dependency-injected DB, so the whole
lifecycle (initialize -> climb -> review -> choose -> page forward) is exercised with fakes.

What is being pinned, in order of how expensive it is to get wrong:

  1. A session runs ONE query. Re-pointing it at a different one is refused.
  2. A lapsed consuming rung RECOVERS; `/execute` is never called a second time for the query.
  3. An unproven query submission leaves the rung UNMARKED rather than claiming it.
  4. The credential wall hands to the operator — we never type a password.
  5. An active captcha stops the crank before anything else is decided.
"""

import apply_state_store as store
import main
import session_checkpoints as cps
from db import get_db
from fastapi.testclient import TestClient
from routers import session_control as sc

client = TestClient(main.app)

SEARCH_URL = "https://www.indeed.com/jobs?q=reporting+analyst&l=Nashua%2C+NH"


class _FakeSession:
    id = 1
    chrome_debug_port = 9222


class _FakeDB:
    """Enough SQLAlchemy Session for the panel: TrainingSession lookup, ObservedJob get/add."""

    def __init__(self):
        self.rows = {}
        self.added = []

    def get(self, model, key):
        if model is main.TrainingSession:
            return _FakeSession()
        return self.rows.get(key)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        pass


class _Harness:
    """Captures every capture-server call so a test can assert what was NOT driven."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def __call__(self, path, payload, timeout=30.0):
        self.calls.append((path, payload))
        res = self.responses.get(path, {"ok": True})
        return res(payload) if callable(res) else res

    def paths(self):
        return [p for p, _ in self.calls]


def _install(monkeypatch, responses, *, blackboard=None):
    """Wire the seam, an in-memory blackboard, and the DB override. Returns the harness plus a
    one-element list holding the persisted blackboard so tests can read the ledger back."""
    harness = _Harness(responses)
    monkeypatch.setattr(sc, "_capture_post", harness)

    saved = {"bb": blackboard}

    def _load_or_create(session_id, goal=None, query="", location=""):
        if saved["bb"] is None:
            saved["bb"] = store.new_blackboard(session_id, goal, query=query, location=location)
        return saved["bb"]

    monkeypatch.setattr(store, "load_or_create", _load_or_create)
    monkeypatch.setattr(store, "save", lambda bb: saved.update(bb=bb))
    monkeypatch.setattr("job_search_targets.add_target",
                        lambda *a, **k: {"query": a[0] if a else ""})

    async def _nosleep(*_a, **_k):
        return None
    monkeypatch.setattr(sc.asyncio, "sleep", _nosleep)

    def _override_db():
        yield _FakeDB()
    main.app.dependency_overrides[get_db] = _override_db
    return harness, saved


def _teardown():
    main.app.dependency_overrides.pop(get_db, None)


def _tabs(*urls):
    return {"ok": True, "tabs": [{"tab_id": f"t{i}", "url": u} for i, u in enumerate(urls)]}


def _at_start_line(query="reporting analyst", location="Nashua, NH", page_url=SEARCH_URL):
    """A blackboard whose preamble is fully held — the stop-and-go phase."""
    bb = store.new_blackboard(1, query=query, location=location)
    led = cps.Ledger()
    for cp in cps.PREAMBLE:
        led.mark(cp.id, evidence="test")
    bb.checkpoints = led.as_dict()
    bb.world = {"radius_miles": 50}
    assert page_url  # the caller's tab is what makes query_entered observable
    return bb


# --- initialize: the query is a session-setup input ------------------------------------------
def test_initialize_declares_the_query_and_seeds_the_ladder(monkeypatch):
    _install(monkeypatch, {"/list_tabs": _tabs("https://www.indeed.com/"),
                           "/auth_state": {"ok": True, "logged_in": True}})
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "reporting analyst", "location": "Nashua, NH"})
    finally:
        _teardown()
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "reporting analyst" and body["location"] == "Nashua, NH"
    assert body["progress"]["phase"] == "climbing"
    assert body["next"]["checkpoint_id"] == "provisioned"
    # declaring is NOT driving — nothing was typed
    assert body["radius_miles"] == 50


def test_initialize_requires_a_query(monkeypatch):
    _install(monkeypatch, {})
    try:
        r = client.post("/api/session_control/1/initialize", json={"query": "   "})
    finally:
        _teardown()
    assert r.status_code == 422


def test_initialize_refuses_a_second_query_once_the_first_was_spent(monkeypatch):
    """THE rule. The session already hit Indeed's backend for 'reporting analyst'; re-pointing it
    is how you get results collapsed. A different query means a different session."""
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_at_start_line())
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "data engineer"})
    finally:
        _teardown()
    assert r.status_code == 409
    assert "already ran" in r.json()["detail"]


def test_initialize_is_idempotent_for_the_same_query(monkeypatch):
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_at_start_line())
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "Reporting  Analyst"})   # same query, sloppier spacing
    finally:
        _teardown()
    assert r.status_code == 200


# --- climbing the preamble --------------------------------------------------------------------
def test_step_marks_provisioned_when_chrome_answers(monkeypatch):
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs("https://www.indeed.com/"),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={"initiator": "operator"}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is True
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("provisioned")
    assert r["next"]["checkpoint_id"] == "authenticated"


def test_step_without_a_query_refuses(monkeypatch):
    _install(monkeypatch, {}, blackboard=store.new_blackboard(1))
    try:
        r = client.post("/api/session_control/1/step", json={})
    finally:
        _teardown()
    assert r.status_code == 409 and "Initialize" in r.json()["detail"]


def test_logged_out_hands_the_credential_wall_to_the_operator(monkeypatch):
    """HARD BOUNDARY: the agent never types a password. It stops and says so."""
    bb = store.new_blackboard(1, query="reporting analyst")
    led = cps.Ledger()
    led.mark("provisioned")
    bb.checkpoints = led.as_dict()
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs("https://www.indeed.com/"),
                               "/auth_state": {"ok": True, "logged_in": False}},
                              blackboard=bb)
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_login"
    assert r["last_step"]["ok"] is False
    assert "never type passwords" in r["last_step"]["detail"]
    assert "/execute" not in harness.paths()
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("authenticated")


def test_active_captcha_stops_the_crank_before_anything_is_decided(monkeypatch):
    bb = store.new_blackboard(1, query="reporting analyst")
    harness, _ = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://www.indeed.com/",
                             "https://www.google.com/recaptcha/api2/bframe?k=x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/challenge_visibility": {"ok": True, "blocking": True, "checkbox_visible": True}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_challenge"
    assert "never auto-solve" in r["last_step"]["detail"]
    assert "/execute" not in harness.paths() and "/set_distance" not in harness.paths()


def test_preloaded_hidden_recaptcha_does_not_stop_the_crank(monkeypatch):
    """Indeed preloads reCAPTCHA Enterprise invisibly on EVERY page, so a URL-only detector calls
    it active constantly. The visibility probe downgrades it to advisory — otherwise the panel
    would be permanently jammed on a challenge nobody is being shown."""
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://www.indeed.com/",
                             "https://www.google.com/recaptcha/api2/bframe?k=x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/challenge_visibility": {"ok": True, "blocking": False}},
        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] != "operator_challenge"
    assert r["block"]["strength"] == "passive"
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("provisioned")


# --- run_query: the one consuming act ---------------------------------------------------------
def _ready_for_query(query="reporting analyst"):
    bb = store.new_blackboard(1, query=query, location="Nashua, NH")
    led = cps.Ledger()
    led.mark("provisioned")
    led.mark("authenticated")
    bb.checkpoints = led.as_dict()
    bb.world = {"radius_miles": 50}
    return bb


def test_run_query_marks_the_rung_only_on_proof(monkeypatch):
    calls = {"n": 0}

    def _list_tabs(_payload):
        calls["n"] += 1
        # first probe: on the home page. after submitting: a results URL carrying our query.
        return _tabs("https://www.indeed.com/") if calls["n"] == 1 else _tabs(SEARCH_URL)

    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _list_tabs,
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/execute": {"outcome": "ok"}},
                              blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is True
    led = cps.Ledger.from_dict(saved["bb"].checkpoints)
    assert led.holds("query_entered")
    assert "q='reporting analyst'" in led.reached["query_entered"].evidence
    # driven through the AX layer by role + accessible name, not a selector
    execs = [p for p in harness.calls if p[0] == "/execute"]
    assert [e[1]["target_name"] for e in execs] == ["What", "Where", "Find jobs"]
    assert all(e[1].get("driver") == "humanized" for e in execs)


def test_unproven_query_submission_is_left_unmarked(monkeypatch):
    """No results URL carrying our query = no proof. Marking it anyway would be the expensive
    kind of wrong: we would believe we searched when we had not."""
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs("https://www.indeed.com/"),
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/execute": {"outcome": "ok"}},
                              blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_verify"
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")


def test_missing_search_box_asks_the_operator_rather_than_flailing(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.indeed.com/"),
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/execute": {"outcome": "not_found"}},
                          blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_search_box"
    assert len([p for p in harness.paths() if p == "/execute"]) == 1  # stopped, didn't keep typing


# --- the recover branch: the whole point ------------------------------------------------------
def test_lapsed_query_recovers_and_never_re_searches(monkeypatch):
    """We drifted off the results page. The query rung is HELD, so the crank must tell us how to
    get back — and must not touch /execute."""
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs("https://www.linkedin.com/feed"),
                               "/auth_state": {"ok": True, "logged_in": True}},
                              blackboard=_at_start_line())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "recover"
    assert r["last_step"]["checkpoint"] == "query_entered"
    assert "never re-submit" in r["last_step"]["recovery"]
    assert "/execute" not in harness.paths()
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")


def test_a_search_tab_for_a_different_query_does_not_count_as_ours(monkeypatch):
    """Somebody else's search is not our results page — context-bound validity, not URL shape."""
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.indeed.com/jobs?q=welder"),
                           "/auth_state": {"ok": True, "logged_in": True}},
                          blackboard=_at_start_line())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "recover"
    assert "/execute" not in harness.paths()


# --- the stop-and-go phase --------------------------------------------------------------------
_CARDS = {"ok": True, "jobs": [
    {"external_id": "a1", "title": "Reporting Analyst", "company": "Acme", "location": "Nashua, NH"},
    {"external_id": "a2", "title": "BI Analyst", "company": "Globex", "location": "Nashua, NH"},
]}


def test_at_the_start_line_the_step_surfaces_the_page_for_choosing(monkeypatch):
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/extract_jobs": _CARDS},
                        blackboard=_at_start_line())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "choose"
    assert r["page"] == 1
    assert [x["title"] for x in r["results"]] == ["Reporting Analyst", "BI Analyst"]
    # reading a page is free; the page rung is the OPERATOR's to mark
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("page:1")


def test_page_number_is_read_off_the_live_tab_not_memory(monkeypatch):
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL + "&start=20"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/extract_jobs": _CARDS},
             blackboard=_at_start_line())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["page"] == 3


def test_choose_records_picks_marks_the_page_and_advances(monkeypatch):
    bb = _at_start_line()
    bb.world["page_results"] = [{"job_id": "indeed:a1"}, {"job_id": "indeed:a2"}]
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/next_page": {"ok": True, "has_next": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:a1"], "note": "good fit"}).json()
    finally:
        _teardown()
    led = cps.Ledger.from_dict(saved["bb"].checkpoints)
    assert led.holds("page:1")
    assert "1 picked of 2" in led.reached["page:1"].evidence
    assert saved["bb"].search_state.approved == ["indeed:a1"]
    assert saved["bb"].search_state.page == 2


def test_choosing_nothing_still_counts_the_page_as_reviewed(monkeypatch):
    bb = _at_start_line()
    bb.world["page_results"] = [{"job_id": "indeed:a1"}]
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/next_page": {"ok": True, "has_next": True}},
                        blackboard=bb)
    try:
        client.post("/api/session_control/1/choose", json={"picks": []})
    finally:
        _teardown()
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("page:1")


def test_choose_rejects_a_pick_that_is_not_on_the_page_under_review(monkeypatch):
    bb = _at_start_line()
    bb.world["page_results"] = [{"job_id": "indeed:a1"}]
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose", json={"picks": ["indeed:ghost"]})
    finally:
        _teardown()
    assert r.status_code == 422 and "indeed:ghost" in r.json()["detail"]


def test_choose_refuses_before_the_start_line(monkeypatch):
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.indeed.com/"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/choose", json={"picks": []})
    finally:
        _teardown()
    assert r.status_code == 409 and "start line" in r.json()["detail"]


def test_no_next_page_is_an_observed_fact_not_an_invented_end_flag(monkeypatch):
    bb = _at_start_line()
    bb.world["page_results"] = [{"job_id": "indeed:a1"}]
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/next_page": {"ok": True, "has_next": False}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose", json={"picks": []}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_end"
    assert "walked out" in r["last_step"]["detail"]
    assert saved["bb"].world["exhausted"] is True
    # still not "ended" by us — the session stays alive for the operator to close
    assert r["progress"]["at_start_line"] is True


# --- provenance ---------------------------------------------------------------------------------
def test_the_initiator_is_recorded_on_every_rung(monkeypatch):
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs("https://www.indeed.com/"),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        client.post("/api/session_control/1/step", json={"initiator": "auto"})
    finally:
        _teardown()
    led = cps.Ledger.from_dict(saved["bb"].checkpoints)
    assert led.reached["provisioned"].initiator == "auto"


def test_an_unknown_initiator_is_refused(monkeypatch):
    _install(monkeypatch, {}, blackboard=store.new_blackboard(1, query="x"))
    try:
        r = client.post("/api/session_control/1/step", json={"initiator": "somebody"})
    finally:
        _teardown()
    assert r.status_code == 422


# --- the read model -------------------------------------------------------------------------------
def test_get_panel_is_read_only(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
                          blackboard=_at_start_line())
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["query"] == "reporting analyst"
    assert r["progress"]["at_start_line"] is True
    assert set(harness.paths()) <= {"/list_tabs", "/auth_state"}   # nothing driven
