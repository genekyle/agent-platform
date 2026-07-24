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


def _install(monkeypatch, responses, *, blackboard=None, frames=None):
    """Wire the seams, an in-memory blackboard, and the DB override. Returns the harness plus a
    one-element list holding the persisted blackboard so tests can read the ledger back.

    `frames` are raw CDP targets (iframes included) for the challenge pre-gate — a SEPARATE seam
    from /list_tabs, which only ever returns type == "page"."""
    harness = _Harness(responses)
    monkeypatch.setattr(sc, "_capture_post", harness)

    async def _targets(_browser_url):
        return list(frames or [])
    monkeypatch.setattr(sc, "_list_targets", _targets)

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


#: The AX Indeed actually serves, measured live 2026-07-24 on session 19. The first version of the
#: drive assumed "What" / "Where" / "Find jobs"; none of those exist here, so nothing was typed and
#: nothing was clicked. Tests use the real shape so a hard-coded matcher cannot pass again.
_SEARCH_PAGE_AX = {"ok": True, "page_text": "", "candidates": [
    {"role": "button", "name": "Skip to main content", "backend_node_id": 1},
    {"role": "combobox", "name": "search: Job title, keywords, or company", "backend_node_id": 2},
    {"role": "combobox", "name": "Edit location", "backend_node_id": 3},
    {"role": "button", "name": "Clear location input", "backend_node_id": 4},
    {"role": "button", "name": "Search", "backend_node_id": 5},
    {"role": "button", "name": "Account", "backend_node_id": 6},
]}


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


def test_a_session_that_already_swept_refuses_a_new_query(monkeypatch):
    """THE live find, end to end. Session 16 had swept 'data analyst' via /api/search/sweep —
    which never wrote the ledger — so the panel would have fired a second query on it. Loading
    now adopts the prior cadence run, and the once-only rule covers history it did not witness."""
    bb = store.new_blackboard(1)
    bb.search_state.query = "data analyst"
    bb.search_state.location = "Nashua, NH"
    bb.search_state.cadence_run_id = "66a01e57c2e7"
    bb.search_state.run_started_at = "2026-07-17T00:45:29+00:00"
    bb.search_state.gathered_authenticated = True
    # note: NO checkpoints — the sweep predates the ledger entirely
    assert bb.checkpoints == {}
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/initialize", json={"query": "reporting analyst"})
    finally:
        _teardown()
    assert r.status_code == 409
    assert "already ran 'data analyst'" in r.json()["detail"]


def test_the_sweep_path_now_records_the_query_it_spends(monkeypatch):
    """The durable half of the fix: one spend, one record. `start_cadence_run` is the sweep's
    version of `query_entered`, so it marks the same rung the panel would."""
    bb = store.new_blackboard(1)
    store.start_cadence_run(bb, query="data analyst", location="Nashua, NH", authed=True)
    led = cps.Ledger.from_dict(bb.checkpoints)
    assert led.holds("query_entered")
    assert "data analyst" in led.reached["query_entered"].evidence


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


def test_an_empty_chrome_is_not_provisioned(monkeypatch):
    """Found live 2026-07-23: both session Chromes were up with ZERO tabs, and this rung marked
    itself held on the evidence "0 tabs answering" — a string that says its own opposite. A
    browser with nothing open has nothing to look at, type into, or sign in on."""
    _, saved = _install(monkeypatch,
                        {"/list_tabs": {"ok": True, "tabs": []},
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_browser"
    assert "no tabs open" in r["last_step"]["detail"]
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("provisioned")


APPLY_TAB = "https://smartapply.indeed.com/beta/indeedapply/form/resume-module"
STALE_SEARCH = "https://www.indeed.com/jobs?q=reporting+analyst&l=Manchester%2C+NH"


def test_a_window_inherited_from_a_previous_session_is_not_provisioned(monkeypatch):
    """Found live 2026-07-23: a persistent profile restores its old window, so a 'fresh' session
    opened onto a half-finished smartapply form and a stale Manchester search. Ready means the
    window is clean AND ours."""
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs("about:blank", APPLY_TAB, STALE_SEARCH),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_clean_start"
    fs = r["last_step"]["fresh_start"]
    assert len(fs["to_close"]) == 2
    assert fs["keeper"]["role"] == "blank"
    assert [t["role"] for t in fs["holds_work"]] == ["apply"]
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("provisioned")


def test_a_clean_window_provisions_straight_away(monkeypatch):
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs("https://www.indeed.com/"),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is True and "clean window" in r["last_step"]["detail"]
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("provisioned")


def test_clean_start_refuses_to_silently_discard_an_application(monkeypatch):
    """A provisioning step must not throw away someone's half-finished apply flow."""
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("about:blank", APPLY_TAB),
                           "/auth_state": {"ok": True, "logged_in": True}},
                          blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/clean_start", json={})
    finally:
        _teardown()
    assert r.status_code == 409 and "real work in progress" in r.json()["detail"]
    assert "/close_tab" not in harness.paths()


def test_clean_start_closes_inherited_tabs_once_confirmed(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("about:blank", APPLY_TAB, STALE_SEARCH),
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/close_tab": {"ok": True}},
                          blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/clean_start",
                        json={"confirm_discards_work": True}).json()
    finally:
        _teardown()
    closes = [p for p in harness.calls if p[0] == "/close_tab"]
    assert len(closes) == 2                      # both inherited tabs, never the keeper
    assert r["last_step"]["ok"] is True and "Closed 2" in r["last_step"]["detail"]


def test_clean_start_needs_no_confirmation_when_nothing_holds_work(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("about:blank", STALE_SEARCH),
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/close_tab": {"ok": True}},
                          blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/clean_start", json={}).json()
    finally:
        _teardown()
    assert len([p for p in harness.calls if p[0] == "/close_tab"]) == 1
    assert r["last_step"]["ok"] is True


def test_a_held_provisioned_rung_stops_relitigating_the_window(monkeypatch):
    """`provisioned` is STANDING, so it is re-checked every step. Mid-drive the window rightly
    holds a search tab and the apply it opened — re-running the inherited-tabs test then would
    call our own working tabs junk and jam the loop. Once held, hygiene owns the window."""
    bb = store.new_blackboard(1, query="reporting analyst")
    led = cps.Ledger()
    led.mark("provisioned", evidence="clean window")
    bb.checkpoints = led.as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, APPLY_TAB),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] != "operator_clean_start"
    # it got past the window entirely and worked the next real rung
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("authenticated")


def test_clean_start_on_an_already_clean_window_does_nothing(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.indeed.com/"),
                           "/auth_state": {"ok": True, "logged_in": True}},
                          blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/clean_start", json={}).json()
    finally:
        _teardown()
    assert "already clean" in r["last_step"]["detail"]
    assert "/close_tab" not in harness.paths()


def test_an_unreachable_chrome_reads_differently_from_an_empty_one(monkeypatch):
    """Both leave us unprovisioned, but the operator has to do different things about them."""
    _install(monkeypatch,
             {"/list_tabs": {"ok": False, "detail": "connection refused"}},
             blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_browser"
    assert "not answering" in r["last_step"]["detail"]


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
    # The rung is not held and nothing was typed. The system now also OFFERS ways in (see the
    # login-step tests below) — but offering a click must never become entering a credential.
    assert "/execute" not in harness.paths()
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("authenticated")


#: A live reCAPTCHA as CDP actually reports it: an IFRAME target, never a page. /list_tabs filters
#: to type == "page", so a block check fed that list could not see this — the 2026-07-23 bug.
_RECAPTCHA_FRAME = {"type": "iframe", "url": "https://www.google.com/recaptcha/api2/bframe?k=x"}


def test_active_captcha_stops_the_crank_before_anything_is_decided(monkeypatch):
    bb = store.new_blackboard(1, query="reporting analyst")
    harness, _ = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://www.indeed.com/"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/challenge_visibility": {"ok": True, "blocking": True, "checkbox_visible": True}},
        frames=[{"type": "page", "url": "https://www.indeed.com/"}, _RECAPTCHA_FRAME],
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_challenge"
    assert "never auto-solve" in r["last_step"]["detail"]
    assert "/execute" not in harness.paths() and "/set_distance" not in harness.paths()


def test_the_challenge_gate_sees_iframes_that_list_tabs_filters_out(monkeypatch):
    """THE regression. A reCAPTCHA is an iframe; /list_tabs returns only pages. Feeding the page
    list to detect_block_frames made this gate structurally unable to fire on the one thing it
    exists to catch — silently, because 'no block found' looks identical to 'all clear'."""
    harness, _ = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://www.indeed.com/"),      # no captcha visible here at all
         "/auth_state": {"ok": True, "logged_in": True},
         "/challenge_visibility": {"ok": True, "blocking": True}},
        frames=[_RECAPTCHA_FRAME],
        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_challenge"
    assert "/execute" not in harness.paths()


def test_preloaded_hidden_recaptcha_does_not_stop_the_crank(monkeypatch):
    """Indeed preloads reCAPTCHA Enterprise invisibly on EVERY page, so a URL-only detector calls
    it active constantly. The visibility probe downgrades it to advisory — otherwise the panel
    would be permanently jammed on a challenge nobody is being shown."""
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://www.indeed.com/"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/challenge_visibility": {"ok": True, "blocking": False}},
        frames=[{"type": "page", "url": "https://www.indeed.com/"}, _RECAPTCHA_FRAME],
        blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] != "operator_challenge"
    assert r["block"]["strength"] == "passive"
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("provisioned")


# --- run_query: the one consuming act ---------------------------------------------------------
def _ready_for_provisioned():
    """Provisioned held, so a step goes straight to the auth rung."""
    bb = store.new_blackboard(1, query="reporting analyst", location="Nashua, NH")
    led = cps.Ledger()
    led.mark("provisioned", evidence="clean window")
    bb.checkpoints = led.as_dict()
    bb.world = {"radius_miles": 50}
    return bb


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
                               "/ax_scan": _SEARCH_PAGE_AX,
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
    # driven through the AX layer by the names DISCOVERED on the page, not assumed ones
    execs = [p for p in harness.calls if p[0] == "/execute"]
    assert [e[1]["target_name"] for e in execs] == [
        "search: Job title, keywords, or company", "Edit location", "Search"]
    assert all(e[1].get("driver") == "humanized" for e in execs)
    # ExecuteRequest requires target_bbox even on the act-by-name path; omitting it is a 422
    assert all("target_bbox" in e[1] for e in execs)


def test_unproven_query_submission_is_left_unmarked(monkeypatch):
    """No results URL carrying our query = no proof. Marking it anyway would be the expensive
    kind of wrong: we would believe we searched when we had not."""
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs("https://www.indeed.com/"),
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/ax_scan": _SEARCH_PAGE_AX,
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
                           "/ax_scan": _SEARCH_PAGE_AX,
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


# --- login is a step the system owns, up to the secret --------------------------------------
def _ax(*controls, page_text=""):
    return {"ok": True, "page_text": page_text,
            "candidates": [{"role": r, "name": n, "backend_node_id": i}
                           for i, (r, n) in enumerate(controls, start=50)]}


def test_the_auth_rung_offers_ways_in_instead_of_a_dead_end(monkeypatch):
    """The operator's complaint, live 2026-07-24: the ladder said 'signed in' was next and gave
    them nothing to press, so login was the one rung the system did not own."""
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.indeed.com/"),
              "/auth_state": {"ok": True, "logged_in": False},
              "/ax_scan": _ax(("button", "Account"), ("link", "Sign in with a code"))},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert login["can_drive"] is True
    names = [o["name"] for o in login["options"]]
    assert "Sign in with a code" in names and "Account" in names
    assert names.index("Sign in with a code") < names.index("Account")   # most-direct first


def test_an_account_menu_alone_still_counts_as_a_way_in(monkeypatch):
    """Indeed's logged-out home exposes NO 'Sign in' to AX — 173 candidates, only an 'Account'
    button (measured live). A matcher looking solely for 'sign in' would report no way to log in
    on the page whose whole job is logging you in."""
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.indeed.com/"),
              "/auth_state": {"ok": True, "logged_in": False},
              "/ax_scan": _ax(("button", "Account"), ("link", "Post a job"))},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert [o["name"] for o in r["last_step"]["login"]["options"]] == ["Account"]


def test_a_password_screen_offers_nothing_to_drive(monkeypatch):
    """The boundary, stated as behaviour: once the next action IS the secret, we have no options."""
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://secure.indeed.com/auth"),
              "/auth_state": {"ok": True, "logged_in": False},
              "/ax_scan": _ax(("textbox", "Email"), ("textbox", "Password"),
                              ("button", "Sign in"))},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert login["state"] == "signin_form"
    assert login["can_drive"] is False and login["options"] == []
    assert "you type it, not us" in login["detail"]


def test_login_action_clicks_a_way_in_through_the_ax_layer(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.indeed.com/"),
                           "/auth_state": {"ok": True, "logged_in": False},
                           "/ax_scan": _ax(("button", "Account")),
                           "/execute": {"outcome": "ok"}},
                          blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/login_action",
                        json={"control_name": "Account", "role": "button"}).json()
    finally:
        _teardown()
    ex = next(p for p in harness.calls if p[0] == "/execute")
    assert ex[1]["action_id"] == "click" and ex[1]["target_name"] == "Account"
    assert ex[1]["driver"] == "humanized"
    assert r["last_step"]["ok"] is True


def test_login_action_refuses_a_control_it_cannot_see(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.indeed.com/"),
                           "/auth_state": {"ok": True, "logged_in": False},
                           "/ax_scan": _ax(("button", "Account"))},
                          blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/login_action",
                        json={"control_name": "Continue with Google"})
    finally:
        _teardown()
    assert r.status_code == 422
    assert "/execute" not in harness.paths()


def test_login_action_refuses_to_touch_a_credential_screen(monkeypatch):
    """The boundary again, from the other side: no request can talk the system into driving a
    password screen, however the option was named."""
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://secure.indeed.com/auth"),
                           "/auth_state": {"ok": True, "logged_in": False},
                           "/ax_scan": _ax(("textbox", "Password"), ("button", "Sign in"))},
                          blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/login_action",
                        json={"control_name": "Sign in"})
    finally:
        _teardown()
    assert r.status_code == 409
    assert "/execute" not in harness.paths()


def test_login_action_is_refused_once_already_signed_in(monkeypatch):
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.indeed.com/"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/login_action", json={"control_name": "Account"})
    finally:
        _teardown()
    assert r.status_code == 409 and "Already signed in" in r.json()["detail"]


def test_a_page_with_no_visible_signin_says_so_plainly(monkeypatch):
    """AX has missed Indeed's sign-in link before and only a screenshot found it. 'I cannot see
    one' must not be reported as 'there is not one'."""
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.indeed.com/"),
              "/auth_state": {"ok": True, "logged_in": False},
              "/ax_scan": _ax(("link", "Post a job"), ("link", "Help"))},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert login["can_drive"] is False and login["options"] == []
    assert "hidden it before" in login["detail"] and "2 elements seen" in login["detail"]


def test_a_validation_error_from_execute_is_not_a_successful_action(monkeypatch):
    """THE bug that wasted a live drive (2026-07-24). Every /execute call was returning a FastAPI
    422 body because `target_bbox` was omitted — required even on the act-by-name path. The code
    asked `outcome != "not_found"`, and a reply with no `outcome` AT ALL sailed through as
    success, so the panel reported "submitted the query" having typed nothing and clicked
    nothing. An unrecognised reply is a failure, loudly."""
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://www.indeed.com/"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": _SEARCH_PAGE_AX,
         "/execute": {"detail": [{"type": "missing", "loc": ["body", "target_bbox"]}]}},
        blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_search_box"
    assert "no outcome back" in r["last_step"]["detail"]
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")
    # and it stopped at the first failure rather than blindly typing the location and submitting
    assert len([p for p in harness.paths() if p == "/execute"]) == 1


def test_a_failed_submit_click_does_not_claim_the_query_ran(monkeypatch):
    """Typing succeeded, submitting did not. Marking the rung here would record a spend that
    never happened and refuse the session the search it still needs."""
    calls = {"n": 0}

    def _execute(_payload):
        calls["n"] += 1
        return {"outcome": "ok"} if calls["n"] < 3 else {"outcome": "not_found"}

    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs("https://www.indeed.com/"),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/ax_scan": _SEARCH_PAGE_AX, "/execute": _execute},
                        blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_search_box"
    assert "could not submit" in r["last_step"]["detail"]
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")


def test_no_search_controls_on_the_page_stops_before_acting(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.indeed.com/"),
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/ax_scan": {"ok": True, "candidates": [
                               {"role": "button", "name": "Account", "backend_node_id": 1}]}},
                          blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_search_box"
    assert "1 elements scanned" in r["last_step"]["detail"]
    assert "/execute" not in harness.paths()
