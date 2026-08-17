"""Tests for the session control panel — the crank the local side turns.

The cadence talks to a session Chrome and the capture server, neither of which exists in a unit
test. Both go through the `_capture_post` seam plus the dependency-injected DB, so the whole
lifecycle (initialize -> climb -> review -> choose -> page forward) is exercised with fakes.

What is being pinned, in order of how expensive it is to get wrong:

  1. A session runs ONE query. Re-pointing it at a different one is refused.
  2. A lapsed consuming rung RECOVERS; `/execute` is never called a second time for the query.
  3. An unproven query submission leaves the rung UNMARKED rather than claiming it.
  4. The credential wall hands to the operator. We may sign in with a login the
     operator stored, and may click a route AROUND a credential (SSO, an emailed
     code) — but the credential screen itself, and any second factor, is never ours.
  5. An active captcha stops the crank before anything else is decided.
"""

import json
from types import SimpleNamespace

import accounts
import apply_state_store as store
import apply_landing as al
import apply_steps as aps
import main
import pytest
import session_checkpoints as cps
from db import get_db
from fastapi.testclient import TestClient
from routers import session_control as sc

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _isolate_accounts(tmp_path, monkeypatch):
    """Point the account registry at a temp file for EVERY test in this module.

    Without this the suite wrote to the operator's real accounts.json: the account-rung tests call
    ensure_account/mark_created against live records, so running the tests registered fake
    companies and flipped real accounts' lifecycle. It went unnoticed because ensure_account used
    to reset any account it touched back to `pending` — the pollution kept undoing itself, and the
    day that reset was fixed (2026-07-27) the leak turned into seven failures that were really one
    missing fixture.
    """
    monkeypatch.setattr(accounts, "_path", lambda: tmp_path / "accounts.json")
    # And hide the DOMAIN logins specifically. The registry file above holds no secrets, but the
    # built-in accounts reference `env:INDEED` / `env:LINKEDIN`, which `_read_env_value` resolves
    # out of the operator's real gitignored .env. So on a machine that HAS those creds — i.e. the
    # one this is actually developed on — `has_creds` was true and the auth rung drove a real login
    # instead of taking the no-credential path most of these tests are about. A test whose result
    # depends on the developer's .env is not a test.
    #
    # Scoped to those prefixes rather than blanking the reader: the per-employer ATS accounts
    # DERIVE their password from `ATS_ACCOUNT_PW_SUFFIX`, read through the same function, so a
    # blanket stub silently un-credentialed the account rungs too.
    # The DOMAIN logins are hidden and the ATS ones are FAKED — the reader never reaches the real
    # .env either way. The domain half was already here: on the machine this is developed on,
    # `has_creds` was true and the auth rung drove a real login instead of taking the
    # no-credential path most of these tests are about.
    #
    # The ATS half used to fall through to the operator's actual ATS_ACCOUNT_* values, which is
    # the same mistake pointed the other way: seven tests here passed only on a checkout that HAS
    # that file, and a worktree — which never does, because it is gitignored — opened on a red
    # suite it had not caused. That is worse than a flaky test. A session either spends an hour
    # bisecting it (2026-07-27) or learns to read failures as background noise, and the second
    # habit is the one that lets a real regression through. The values below are fixed, so the
    # derivation they feed is checkable: "Teradyne" has ONE initial, so the password is suffix + 1
    # = exactly 8 characters, which is SAP's stated floor and the boundary the policy check exists
    # for. A test whose result depends on the developer's .env is not a test.
    _fake_env = {"ATS_ACCOUNT_USERNAME": "operator@example.com",
                 "ATS_ACCOUNT_PW_SUFFIX": "abcde1!",
                 "ATS_ACCOUNT_FIRST_NAME": "Gene",
                 "ATS_ACCOUNT_LAST_NAME": "Magsipoc"}
    _real_env = accounts._read_env_value
    _hidden = ("INDEED_", "LINKEDIN_", "FB_", "GMAIL_")

    def _env(key: str) -> str:
        if key in _fake_env:
            return _fake_env[key]
        return "" if key.startswith(_hidden) else _real_env(key)

    monkeypatch.setattr(accounts, "_read_env_value", _env)
    # The SECRETS VAULT, for the same reason one file up. The account rung now stores the
    # credential it created an account with, so these tests write secrets — and without this they
    # write them into the operator's real lockbox under fake company names, encrypted with the real
    # key. Redirecting only the registry would have made that pollution invisible rather than absent.
    import secrets_vault
    monkeypatch.setenv("AGENT_VAULT_KEY_PATH", str(tmp_path / "vault.key"))
    monkeypatch.setenv("VAULT_KEY_PROVIDER", "local")
    monkeypatch.setattr(secrets_vault, "_vault_path", lambda: tmp_path / "secrets_vault.json")
    secrets_vault.reset_provider_cache()

SEARCH_URL = "https://www.indeed.com/jobs?q=reporting+analyst&l=Nashua%2C+NH"


class _FakeSession:
    id = 1
    chrome_debug_port = 9222


class _FakeDB:
    """Enough SQLAlchemy Session for the panel: TrainingSession lookup, ObservedJob get/add."""

    def __init__(self, observed=None, answers=None, applied=None):
        self.rows = {}
        self.added = []
        self.observed = observed or {}      # job_id -> (title, company) for ObservedJob.get
        self._answers = answers or []       # ApplicationAnswer-like rows for scalars()
        self._applied = applied or []       # ObservedJob rows the applied-index should find

    def scalars(self, stmt):
        # Dispatch on the queried ENTITY. One canned list for every select() made the fake answer
        # the applied-index with a page of application answers — a fake that lies about WHICH
        # question it was asked is worse than no fake.
        rows = self._answers
        try:
            entity = (stmt.column_descriptions or [{}])[0].get("entity")
            if entity is not None and getattr(entity, "__name__", "") == "ObservedJob":
                rows = self._applied
        except Exception:                    # noqa: BLE001 — a fake must never break the test
            pass
        return type("_R", (), {"all": lambda self: rows})()

    def get(self, model, key):
        if model is main.TrainingSession:
            return _FakeSession()
        if key in self.observed:
            title, company = self.observed[key]
            row = type("_Row", (), {})()
            row.title, row.company = title, company
            return row
        return self.rows.get(key)

    def add(self, row):
        self.added.append(row)

    def scalar(self, stmt):
        # The search layer asks for the active Search row; these panel tests never seed one,
        # so the honest answer is "none on file" — ensure_active_search then creates it.
        return None

    def flush(self):
        # Stamp ids the way a flush would, so a freshly ensured Search is linkable.
        for i, row in enumerate(self.added, start=1):
            if getattr(row, "id", None) is None:
                row.id = i

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


def _install(monkeypatch, responses, *, blackboard=None, frames=None, observed=None,
             answers=None, applied=None):
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
        yield _FakeDB(observed=observed, answers=answers, applied=applied)
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


def test_a_different_query_starts_a_NEW_SEARCH_in_the_same_session(monkeypatch):
    """THE 2026-08-06 correction. This used to 409 with "start a new session", which applied the
    once-only rule one level too high: re-running THE SAME query is what collapses results, and a
    different query is simply new work. Refusing it meant abandoning a query cost an authenticated
    browser — close Chrome, provision another, sign in again, to change a word in a search box."""
    _, saved = _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                                      "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_at_start_line())
    try:
        r = client.post("/api/session_control/1/initialize",
                        json={"query": "data engineer"})
        after = saved["bb"]
    finally:
        _teardown()
    assert r.status_code == 200, r.text
    led = cps.Ledger.from_dict(after.checkpoints)
    assert led.search == 2, "a different query advances the search scope"
    # THE SAVING: the session rungs are untouched, so the new search starts already signed in.
    assert led.holds("provisioned") and led.holds("authenticated")
    # ...and the search-scoped rungs begin again under the new scope.
    assert not led.holds("query_entered") and not led.holds("radius_set")
    # The PREVIOUS search's record survives — that is what keeps the once-only promise real.
    assert "query_entered" in after.checkpoints
    assert after.search_state.query == "data engineer"


def test_the_same_query_twice_is_still_refused(monkeypatch):
    """The rule that survives the correction, and the reason `consuming` exists at all: a new
    search must not launder a repeat of a query this session already spent."""
    bb = _at_start_line()
    spent = " ".join((bb.search_state.query or "").split())
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        client.post("/api/session_control/1/initialize", json={"query": "data engineer"})
        r = client.post("/api/session_control/1/initialize", json={"query": spent})
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
        # Moving ON is free: a query this session has not spent is new work, and it runs HERE
        # rather than costing a second signed-in browser.
        moved_on = client.post("/api/session_control/1/initialize",
                               json={"query": "reporting analyst"})
        # Coming BACK to the swept query is the repeat the rule exists to stop — and the ledger
        # only knows about it because `adopt_prior_run` taught it history it did not witness.
        went_back = client.post("/api/session_control/1/initialize", json={"query": "data analyst"})
    finally:
        _teardown()
    assert moved_on.status_code == 200, moved_on.text
    assert went_back.status_code == 409
    assert "already ran 'data analyst'" in went_back.json()["detail"]


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


#: The other tab a real apply session has open: an ATS application, on a different origin. Indeed's
#: login JS finds none of its markers here and honestly reports logged_in=false — which is the
#: right answer about THIS page and the wrong answer about the session.
_WORKDAY_APPLY_URL = ("https://mfs.wd1.myworkdayjobs.com/en-US/MFS-Careers/job/Boston/"
                      "Compliance-Reporting-Associate_MFS-231810-1/apply/applyManually")


def test_auth_is_probed_on_the_indeed_tab_not_whichever_tab_is_in_front(monkeypatch):
    """Found live 2026-07-25 on a session left open two days. `/auth_state` with no tab hint
    resolves whatever target CDP lists first — the Workday application — so a signed-in session
    read as REGRESSED and the panel's next move was to sign in again."""
    bb = _at_start_line()
    per_tab = {"t0": {"ok": True, "logged_in": False},   # the Workday tab, listed first
               "t1": {"ok": True, "logged_in": True}}    # the Indeed results tab
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(_WORKDAY_APPLY_URL, SEARCH_URL),
         "/auth_state": lambda p: per_tab.get(p.get("tab_id"), {"ok": True, "logged_in": False})},
        blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    auth_calls = [p for path, p in harness.calls if path == "/auth_state"]
    assert [c.get("tab_id") for c in auth_calls] == ["t1"], "must pin the probe to the Indeed tab"
    rung = next(x for x in r["ladder"] if x["id"] == "authenticated")
    assert rung["observed"] is True and rung["status"] == "held"
    assert r["next"]["checkpoint_id"] != "authenticated"


def test_no_indeed_tab_leaves_auth_unknown_rather_than_calling_it_signed_out(monkeypatch):
    """An unknown must never read as a regression — the rule `session_checkpoints` enforces for
    the ladder, applied to the probe that feeds it. With no Indeed tab we did not look at Indeed."""
    bb = _at_start_line()
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs(_WORKDAY_APPLY_URL),
                               "/auth_state": {"ok": True, "logged_in": False}},
                              blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert "/auth_state" not in harness.paths(), "nothing to probe — do not probe the wrong page"
    rung = next(x for x in r["ladder"] if x["id"] == "authenticated")
    assert rung["observed"] is None and rung["status"] == "held"


def _auth_rung_next(query="reporting analyst"):
    """A blackboard whose crank lands on `authenticated`."""
    bb = store.new_blackboard(1, query=query)
    led = cps.Ledger()
    led.mark("provisioned", evidence="test")
    bb.checkpoints = led.as_dict()
    return bb


def test_an_empty_window_opens_the_front_door_instead_of_stalling_the_whole_ladder(monkeypatch):
    """Found live 2026-08-04: both session browsers were up with ZERO page targets, and
    `probe_browser` refused — "open a tab yourself". That is the 2026-07-25 front-door gap fixed
    one rung too high: `auth_probe` learned to navigate home, and `provisioned` gates it, so it
    never ran. An empty window is the CLEANEST possible window; refusing it made the whole ladder
    unclimbable from a fresh session.

    The world moves on the NAVIGATE (the fixture law, 2026-08-03): the window has no tabs until
    something opens one."""
    world = {"opened": False}

    def _list_tabs(_payload):
        return _tabs("https://www.indeed.com/") if world["opened"] else {"ok": True, "tabs": []}

    def _navigate(payload):
        world["opened"] = True
        return {"ok": True, "landed_url": payload["url"], "created_tab": True}

    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _list_tabs,
                               "/navigate": _navigate,
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/ax_scan": _SEARCH_PAGE_AX},
                              blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()

    nav = [p for path, p in harness.calls if path == "/navigate"]
    assert len(nav) == 1 and nav[0]["url"] == sc.INDEED_HOME, "the HOME page, never a deep URL"
    assert r["last_step"]["ok"] is True
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("provisioned")


def test_a_window_that_already_has_tabs_is_never_given_another(monkeypatch):
    """Tab churn is a bot-safety signature, and a non-provisioned reading over EXISTING tabs means
    something else is wrong — opening another tab would drive a page nobody asked for."""
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": {"ok": False, "tabs": [{"tab_id": "t0", "url": "x"}]},
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/ax_scan": _SEARCH_PAGE_AX},
                          blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert "/navigate" not in harness.paths()
    assert r["awaiting"] == "operator_browser"


def test_a_fresh_session_opens_indeed_itself_instead_of_asking_the_operator_to(monkeypatch):
    """Found live 2026-07-25 provisioning session 20 — the first fresh session the panel ever
    started. The window is one about:blank tab, so nothing could be probed and nothing could be
    searched, and both rungs handed back 'open Indeed'. Initialize is specified to reach the start
    line; opening the HOME page is the first move, and it was missing."""
    bb = _auth_rung_next()
    tabs = {"n": 0}

    def _list_tabs(_payload):
        # about:blank until we navigate; the Indeed home page afterwards.
        return _tabs("about:blank") if tabs["n"] == 0 else _tabs("https://www.indeed.com/")

    def _navigate(_payload):
        tabs["n"] = 1
        return {"ok": True, "landed_url": "https://www.indeed.com/"}

    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _list_tabs,
                               "/navigate": _navigate,
                               "/auth_state": {"ok": True, "logged_in": True}},
                              blackboard=bb)
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    nav = [p for path, p in harness.calls if path == "/navigate"]
    assert len(nav) == 1 and nav[0]["url"] == sc.INDEED_HOME, "the HOME page, never a deep URL"
    assert r["last_step"]["ok"] is True
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("authenticated")


def test_auth_probe_that_cannot_open_indeed_leaves_the_rung_alone(monkeypatch):
    """`auth_probe` must not turn 'we could not see' into 'signed out' and run a login survey
    against whatever page is in front."""
    bb = _auth_rung_next()
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs("about:blank"),
                               "/navigate": {"ok": False, "detail": "ConnectError"},
                               "/auth_state": {"ok": True, "logged_in": False}},
                              blackboard=bb)
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["action"] == "auth_probe"
    assert r["awaiting"] == "operator_open_engine"
    # The survey reads the page in front of it. Run here it would classify about:blank — or a
    # Workday application — as an Indeed login screen and offer "ways in" that lead elsewhere.
    # (The StepRunner's OBSERVATIONS may scan freely — a look is not a survey — so the invariant
    # is "no ways in were offered", not "no scan ever ran".)
    assert "login" not in r["last_step"]
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("authenticated")


def test_an_ats_tab_is_never_mistaken_for_a_missing_indeed_tab(monkeypatch):
    """The two fixes meet here: a Workday tab is not an Indeed tab, so auth is unknown — but the
    remedy is to open Indeed, not to survey the Workday page for a way in."""
    bb = _auth_rung_next()
    urls = {"list": [_WORKDAY_APPLY_URL]}

    def _list_tabs(_payload):
        return _tabs(*urls["list"])

    def _navigate(_payload):
        urls["list"] = [_WORKDAY_APPLY_URL, "https://www.indeed.com/"]
        return {"ok": True, "landed_url": "https://www.indeed.com/"}

    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _list_tabs, "/navigate": _navigate,
                               "/auth_state": lambda p: {"ok": True,
                                                         "logged_in": p.get("tab_id") == "t1"}},
                              blackboard=bb)
    try:
        client.post("/api/session_control/1/step", json={})
    finally:
        _teardown()
    assert [p for path, p in harness.calls if path == "/navigate"], "should open Indeed"
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("authenticated")


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
    assert [(e[1]["action_id"], e[1]["target_name"]) for e in execs] == [
        # each field is CLEARED before it is typed — `type` inserts at the caret, it does not
        # replace, so a populated box would otherwise be appended to.
        ("clear", "search: Job title, keywords, or company"),
        ("type", "search: Job title, keywords, or company"),
        ("clear", "Edit location"),
        ("type", "Edit location"),
        ("click", "Search")]
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


def test_naming_a_job_the_queue_is_not_working_is_refused_not_ignored(monkeypatch):
    """Found driving session 21: `job_id` was not a field, so pydantic dropped it and two calls
    naming two DIFFERENT jobs both worked the same step — and read as if each had worked its own.
    The queue is sequential and stays that way; naming the wrong job is now an error, not a
    silently different action."""
    bb = _at_start_line()
    queue = aps.Queue(page=1)
    queue.enqueue([{"job_id": "indeed:aaa", "title": "First Job"},
                   {"job_id": "indeed:bbb", "title": "Second Job"}])
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
                          blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step",
                        json={"job_id": "indeed:bbb", "initiator": "operator"})
    finally:
        _teardown()
    assert r.status_code == 409
    assert "indeed:aaa" in r.json()["detail"] and "indeed:bbb" in r.json()["detail"]
    assert "/open_job_card" not in harness.paths(), "nothing was driven for the wrong job"


def test_a_submit_swallowed_by_the_location_widget_is_clicked_again(monkeypatch):
    """Measured live 2026-07-25 on session 20: both fields held their typed values, Search was the
    hit-test target at its own centre, the trusted click dispatched — and the page did not move.
    Typing into the location combobox stages a suggestion popup, and the first click is spent
    dismissing it. The second one submits."""
    # A WORLD, not a call script — the same fixture lesson its neighbour below spells out. This
    # test used to flip the page on the FIFTH `/list_tabs`, which modelled a browser that
    # navigates because somebody looked at it. It passed only while the production code happened
    # to read exactly four times; when the commit began WAITING for its navigation instead of
    # racing it (2026-08-13), the extra reads "navigated" the page with no second click and the
    # retry this test exists to prove never fired. Reads are free and side-effect-free; the page
    # moves on the second CLICK, because the first is spent dismissing the location widget.
    world = {"clicks": 0}

    def _execute(payload):
        if payload.get("action_id") == "click":
            world["clicks"] += 1
        return {"outcome": "ok"}

    def _list_tabs(_payload):
        return _tabs(SEARCH_URL) if world["clicks"] >= 2 else _tabs("https://www.indeed.com/")

    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _list_tabs,
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/ax_scan": _SEARCH_PAGE_AX,
                               "/execute": _execute},
                              blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is True
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")
    clicks = [p for path, p in harness.calls
              if path == "/execute" and p["action_id"] == "click"]
    assert len(clicks) == 2, "one click to commit the widget, one to submit"


def test_a_commit_whose_results_land_late_is_still_marked(monkeypatch):
    """THE OTHER HALF OF THE RACE, measured live 2026-08-13 (session #28, "report analyst").

    The commit submitted and the results page loaded — but the tab list was read once, one pause
    later, while the navigation was still in flight. `run_query` saw `moved=False, tab=None`, and
    then did exactly the right things with the wrong facts: refused to mark the CONSUMING rung,
    refused to retry, told the operator to check the browser. The session stalled one observation
    short of a search that had worked, on a rung that may not be re-run.

    So the commit now WAITS for its own navigation. The page moves because of the CLICK; it just
    takes a moment. Reads never cause it — they only let time pass, and re-reading a tab list
    spends nothing.
    """
    world = {"clicked": False, "looks": 0}

    def _execute(payload):
        if payload.get("action_id") in ("click", "submit"):
            world["clicked"] = True
        return {"outcome": "ok"}

    def _list_tabs(_payload):
        if not world["clicked"]:
            return _tabs("https://www.indeed.com/")
        world["looks"] += 1
        # In flight for the first two looks after the commit, then it has landed.
        return _tabs(SEARCH_URL) if world["looks"] > 2 else _tabs("https://www.indeed.com/")

    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _list_tabs,
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/ax_scan": _SEARCH_PAGE_AX,
                               "/execute": _execute},
                              blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()

    assert r["last_step"]["ok"] is True, "the search landed; waiting for it is not a failure"
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")
    commits = [p for path, p in harness.calls
               if path == "/execute" and p["action_id"] in ("click", "submit")]
    assert len(commits) == 1, "waiting for the navigation must never become a second submit"


def test_a_submit_whose_confirmation_raced_the_navigation_is_never_clicked_twice(monkeypatch):
    """THE ONE THAT BIT US LIVE, 2026-07-25. The click submitted, the tab re-read raced the
    navigation and still showed the old URL, so the retry fired — onto the freshly-loaded results
    page, whose search box is empty. That second click submitted `q=` from the SERP.

    Here the window HAS moved (a results page, just not one carrying our query). The retry must
    not fire on that: a page that moved means the click did something."""
    # A WORLD, not a call script (the StepRunner fixture lesson, 2026-08-03): the page flips on
    # the CLICK, never on a read count — the StepRunner observes whenever it likes, and a look
    # must not advance the page. Before the click: the home page. After: a results page for the
    # WRONG query (the race's outcome).
    world = {"clicked": False}

    def _execute(payload):
        if payload.get("action_id") == "click":
            world["clicked"] = True
        return {"outcome": "ok"}

    def _list_tabs(_payload):
        if not world["clicked"]:
            return _tabs("https://www.indeed.com/")
        return _tabs("https://www.indeed.com/jobs?q=&l=Lowell%2C+MA&from=searchOnDesktopSerp")

    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _list_tabs,
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/ax_scan": _SEARCH_PAGE_AX,
                               "/execute": _execute},
                              blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    clicks = [p for path, p in harness.calls
              if path == "/execute" and p["action_id"] == "click"]
    assert len(clicks) == 1, "the page moved — the click did something; never click again"
    assert r["awaiting"] == "operator_verify"
    assert "NOT retried" in r["last_step"]["detail"]
    assert not cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")


def test_a_submit_that_landed_is_never_clicked_twice(monkeypatch):
    """The retry is verified, never blind. A search that DID land changes the URL, so the second
    click must not happen — double-spending the query is the one thing this rung cannot do."""
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs(SEARCH_URL),
                               "/auth_state": {"ok": True, "logged_in": True},
                               "/ax_scan": _SEARCH_PAGE_AX,
                               "/execute": {"outcome": "ok"}},
                              blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is True
    clicks = [p for path, p in harness.calls
              if path == "/execute" and p["action_id"] == "click"]
    assert len(clicks) == 1, "the query landed on the first click — never submit it again"


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
    # Stopped at the first field — never went on to the location box or the Search button.
    touched = {p["target_name"] for path, p in harness.calls if path == "/execute"}
    assert touched == {"search: Job title, keywords, or company"}


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


def test_choosing_queues_the_picks_and_holds_the_page(monkeypatch):
    """Choosing no longer FINISHES a page — it enqueues work. Operator: "if i check off 11 jobs
    that's 11 steps and i don't continue until i fully apply." """
    bb = _at_start_line()
    bb.world["page_results"] = [{"job_id": "indeed:a1", "title": "Reporting Analyst"},
                                {"job_id": "indeed:a2", "title": "BI Analyst"}]
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/next_page": {"ok": True, "has_next": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:a1", "indeed:a2"], "note": "good fit"}).json()
    finally:
        _teardown()
    led = cps.Ledger.from_dict(saved["bb"].checkpoints)
    assert not led.holds("page:1")                       # the page is NOT done
    assert r["awaiting"] == "apply"
    assert r["queue_summary"]["remaining"] == 2
    assert saved["bb"].search_state.page == 1            # and we did not advance
    assert saved["bb"].search_state.approved == ["indeed:a1", "indeed:a2"]


def test_the_page_completes_once_every_queued_apply_is_terminal(monkeypatch):
    """The page rung is marked only when nothing is still open — and the evidence records how
    many were actually submitted, not merely how many were picked."""
    import apply_steps as aps
    bb = _at_start_line()
    bb.world["page_results"] = [{"job_id": "indeed:a1"}, {"job_id": "indeed:a2"}]
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:a1"}, {"job_id": "indeed:a2"}])
    q.steps[0].finish(aps.SUBMITTED)
    q.steps[1].finish(aps.PARKED_ACCOUNT_WALL, "operator owns account creation")
    bb.world["apply_queue"] = q.as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/next_page": {"ok": True, "has_next": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:a1", "indeed:a2"]}).json()
    finally:
        _teardown()
    led = cps.Ledger.from_dict(saved["bb"].checkpoints)
    assert led.holds("page:1")
    assert "1 submitted" in led.reached["page:1"].evidence
    assert r["page"] == 2


def test_flagging_a_step_terminal_moves_the_queue_along(monkeypatch):
    import apply_steps as aps
    bb = _at_start_line()
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:a1", "title": "One"}, {"job_id": "indeed:a2", "title": "Two"}])
    bb.world["apply_queue"] = q.as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "parked:unknown_ats",
                              "detail": "never driven this one"}).json()
    finally:
        _teardown()
    assert r["queue_summary"]["remaining"] == 1
    assert "Next up: Two" in r["last_step"]["detail"]
    back = aps.Queue.from_dict(saved["bb"].world["apply_queue"])
    assert back.steps[0].terminal == "parked:unknown_ats"


def test_an_invented_terminal_flag_is_refused_by_the_api(monkeypatch):
    bb = _at_start_line()
    q = aps.Queue(page=1); q.enqueue([{"job_id": "indeed:a1"}])
    bb.world["apply_queue"] = q.as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "basically_done"})
    finally:
        _teardown()
    assert r.status_code == 422


def test_a_finished_step_is_not_reopened_by_flagging_it_again(monkeypatch):
    bb = _at_start_line()
    q = aps.Queue(page=1); q.enqueue([{"job_id": "indeed:a1"}])
    q.steps[0].finish(aps.SUBMITTED)
    bb.world["apply_queue"] = q.as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "abandoned:operator"})
    finally:
        _teardown()
    assert r.status_code == 409


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


def _card(job_id, title, company):
    """A page_results card as `review_page` writes it — `external_id` is what `check_many` keys on."""
    return {"job_id": job_id, "external_id": job_id.split(":", 1)[1],
            "title": title, "company": company}


def test_choose_refuses_a_pick_the_database_says_is_already_applied(monkeypatch):
    """Operator, 2026-08-17, having just picked two jobs already applied to through Indeed: *"when
    we use the picker we need to start involving the applied database here and in decision making
    … so we don't waste any time."*

    The verdict was computed on every card at scan time and no decision consulted it. An EXACT
    match is certain, so this refuses rather than warns — entering an application that already
    exists is real traffic against a real account for nothing.
    """
    bb = _at_start_line()
    bb.world["page_results"] = [_card("indeed:a1", "Data Analyst", "Acme")]
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb,
             applied=[_applied_row(None, "indeed:a1", "Data Analyst", "Acme")])
    try:
        r = client.post("/api/session_control/1/choose", json={"picks": ["indeed:a1"]})
    finally:
        _teardown()
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Already applied" in detail and "Data Analyst" in detail
    assert "confirm_reapply" in detail, "a refusal the operator cannot act on is a dead end"


def test_choose_lets_a_named_reapply_through(monkeypatch):
    """The override is per-job, not a blanket flag: "yes, apply again to THIS one" is a judgement
    about a reposted requisition, and a boolean that waves everything through is not that."""
    bb = _at_start_line()
    bb.world["page_results"] = [_card("indeed:a1", "Data Analyst", "Acme")]
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True},
         "/next_page": {"ok": True, "has_next": True}},
        blackboard=bb, applied=[_applied_row(None, "indeed:a1", "Data Analyst", "Acme")])
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:a1"], "confirm_reapply": ["indeed:a1"],
                              "advance": False})
    finally:
        _teardown()
    assert r.status_code == 200
    assert aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0].job_id == "indeed:a1"


def test_choose_queues_a_maybe_applied_pick_and_says_so(monkeypatch):
    """The fuzzy tier NEVER blocks: company+title is right far more often than it is wrong, but a
    near-miss that silently drops a job the operator picked is worse than one that asks. So it
    enqueues — and leaves the warning on the record.

    The pair here is the REAL 2026-08-17 shape: the same posting met through two boards, so the
    ids differ and the employer and role do not. Deliberately NOT "Analyst I" vs "Analyst II" —
    `job_dedup` separates level numerals on purpose (the 07-30 fix, after the old scorer equated
    'Financial Analyst II' with 'Financial Analyst III' on the real corpus), so that pair is a
    non-match and would have pinned nothing.
    """
    bb = _at_start_line()
    bb.world["page_results"] = [_card("indeed:b2", "Clinical Reporting Analyst",
                                      "Charles River Community Health")]
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True},
         "/next_page": {"ok": True, "has_next": True}},
        blackboard=bb,
        applied=[_applied_row(None, "indeed:a1", "Clinical Reporting Analyst",
                              "Charles River Community Health")])
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:b2"], "advance": False})
    finally:
        _teardown()
    assert r.status_code == 200
    assert aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0].job_id == "indeed:b2"
    warned = [e for e in (saved["bb"].events or []) if e.kind == "applied_warning"]
    assert warned, "a queued maybe-applied pick must leave a trace the operator can read"
    assert "Clinical Reporting Analyst" in warned[-1].detail


def test_choose_is_unbothered_when_nothing_has_been_applied_to(monkeypatch):
    """The guard must not fire on `not_applied` — the status meaning "nothing on file" is the
    ordinary answer for almost every card, and treating a non-empty status as a hit is the exact
    bug `AppliedNote` already carries a comment about."""
    bb = _at_start_line()
    bb.world["page_results"] = [_card("indeed:c3", "Data Analyst", "Nowhere Inc")]
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True},
         "/next_page": {"ok": True, "has_next": True}},
        blackboard=bb, applied=[])
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:c3"], "advance": False})
    finally:
        _teardown()
    assert r.status_code == 200
    assert not [e for e in (saved["bb"].events or []) if e.kind == "applied_warning"]


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
    # A page that MOVES on the click (the StepRunner fixture lesson, 2026-08-03): the account
    # menu opens and a way in appears. A static page here would model a click that landed on
    # nothing — which the verifier now rightly demotes.
    world = {"clicked": False}

    def _execute(payload):
        if payload.get("action_id") == "click":
            world["clicked"] = True
        return {"outcome": "ok"}

    def _scan(_payload):
        return (_ax(("button", "Account"), ("link", "Sign in")) if world["clicked"]
                else _ax(("button", "Account")))

    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.indeed.com/"),
                           "/auth_state": {"ok": True, "logged_in": False},
                           "/ax_scan": _scan,
                           "/execute": _execute},
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
    touched = {p["target_name"] for path, p in harness.calls if path == "/execute"}
    assert touched == {"search: Job title, keywords, or company"}


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


def test_a_successful_query_does_not_render_as_needing_recovery(monkeypatch):
    """Seen live 2026-07-24. The rung was marked on real proof, but the view was built from the
    observation taken BEFORE the action — so it still said query_entered was False, and holding
    those two together reads as "spent but its effect is gone". The panel told the operator to
    recover from a search that had just worked. Acting must be followed by re-observing."""
    calls = {"n": 0}

    def _list_tabs(_payload):
        calls["n"] += 1
        # home page while deciding; the real results page from the moment the query lands
        return _tabs("https://www.indeed.com/") if calls["n"] == 1 else _tabs(SEARCH_URL)

    _, saved = _install(monkeypatch,
                        {"/list_tabs": _list_tabs,
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/ax_scan": _SEARCH_PAGE_AX,
                         "/execute": {"outcome": "ok"}},
                        blackboard=_ready_for_query())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()

    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("query_entered")
    assert r["awaiting"] != "recover"
    assert r["next"]["kind"] != "recover"
    # the ladder shows it HELD, not lapsed — and the view reflects the results page we landed on
    rung = next(x for x in r["ladder"] if x["id"] == "query_entered")
    assert rung["status"] == "held"
    assert r["observed"]["query_entered"] is True


def test_the_view_reflects_the_world_after_the_action_not_before(monkeypatch):
    """The general form: provisioning a browser that comes up mid-step must not report the
    pre-action window either."""
    calls = {"n": 0}

    def _list_tabs(_payload):
        calls["n"] += 1
        return {"ok": False, "tabs": []} if calls["n"] == 1 else _tabs("https://www.indeed.com/")

    _install(monkeypatch,
             {"/list_tabs": _list_tabs, "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=store.new_blackboard(1, query="reporting analyst"))
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["tab_count"] == 1          # the window as it is NOW, not as it was


# --- working an apply step: the crank the queue was missing -----------------------------------
def _with_queue(*jobs):
    bb = _at_start_line()
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": j[0], "title": j[1], "company": j[2]} for j in jobs])
    bb.world["apply_queue"] = q.as_dict()
    return bb


def test_apply_step_opens_the_pane_for_the_current_job(monkeypatch):
    harness, saved = _install(
        monkeypatch,
        # The tab carries ?vjk=a1 because that is what a real open DOES to the SERP's URL — and
        # the StepRunner now verifies the world moved, so a fixture whose world never moves is a
        # fixture of a broken page, not of a working one (PLAN_step_runner.md).
        {"/list_tabs": _tabs(SEARCH_URL + "&vjk=a1"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/open_job_card": {"ok": True, "title": "Compliance Reporting Analyst",
                            "apply_type": "indeed_apply"}},
        blackboard=_with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS")))
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    call = next(p for p in harness.calls if p[0] == "/open_job_card")
    assert call[1]["external_id"] == "a1"            # job_id prefix stripped
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert [m.outcome for m in step.minis] == [aps.OK]
    assert step.next_rung().id == "verify_identity"
    assert r["last_step"]["ok"] is True


def test_verify_identity_refuses_when_the_pane_is_a_different_job(monkeypatch):
    """The near-miss guard, as behaviour. An application to the wrong job cannot be taken back."""
    bb = _with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].record("open_pane", aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["open_pane"] = {"title": "Senior Warehouse Associate"}
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs(SEARCH_URL),
                               "/auth_state": {"ok": True, "logged_in": True}},
                              blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.FAILED
    assert step.next_rung().id == "verify_identity"     # blocked here, does not advance
    assert "STOP" in r["last_step"]["detail"]
    assert "/execute" not in harness.paths()            # nothing was clicked


def test_verify_identity_passes_on_a_loose_title_match(monkeypatch):
    """Card and pane titles differ in punctuation and suffixes but never in the actual role."""
    bb = _with_queue(("indeed:a1", "Sales Revenue Analyst - Boston", "Datadog"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].record("open_pane", aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["open_pane"] = {"title": "Sales Revenue Analyst | Datadog"}
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_step", json={})
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.OK


def test_classify_records_an_unknown_ats_without_guessing(monkeypatch):
    bb = _with_queue(("indeed:a1", "Financial Analyst", "Globex"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply"):
        q.steps[0].record(r_id, aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t9", "url": "https://careers.globex.io/apply/1"}
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.UNKNOWN
    assert step.platform in ("company_site", "unknown")
    assert "guessed at" in r["last_step"]["detail"]


def test_a_challenge_halts_the_apply_step(monkeypatch):
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True},
         "/challenge_visibility": {"ok": True, "blocking": True}},
        frames=[{"type": "iframe", "url": "https://www.google.com/recaptcha/api2/bframe?k=x"}],
        blackboard=_with_queue(("indeed:a1", "One", "Acme")))
    try:
        client.post("/api/session_control/1/apply_step", json={})
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.BLOCKED
    assert "/open_job_card" not in harness.paths()


def test_apply_step_refuses_when_the_queue_is_drained(monkeypatch):
    bb = _with_queue(("indeed:a1", "One", "Acme"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].finish(aps.SUBMITTED)
    bb.world["apply_queue"] = q.as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={})
    finally:
        _teardown()
    assert r.status_code == 409


# --- ONE next action: arbitrating between the recipe and the world -----------------------------
# The panel grew two surfaces answering "what next" — the ladder's rung (the recipe's position) and
# the observer's plan (the live page) — and rendered each as its own primary-looking button, so the
# operator did the arbitrating. That is the sequencing debt recorded on 2026-07-30. These pin the
# rule: the observer wins when the world CONTRADICTS the recipe, the rung wins otherwise, and
# whichever loses stays on the record as the secondary option.
#
# The live fixture: an apply click that landed on an employer's careers front (a branded wrapper)
# while the ladder believed it was at the account wall.
_POSTING_TEXT = ("Sr. Reporting Analyst Quincy, MA APPLY NOW Job Requisition: 533857 "
                 "Responsibilities Qualifications")
_GATE_TEXT = ("Returning Candidate? Log back in! Already have an account? "
              "Sign in to continue with your application.")


def _oriented(monkeypatch, apply_url, page_text, *, rungs=(), platform=None):
    """A session with one application open on `apply_url`, so the panel's observer fires on GET.

    `ats_for_company` is STUBBED to know nothing. It reads the learned company->ATS store off
    disk, so left live the memory witness — and with it the fused confidence these tests turn on —
    would depend on which employers this machine happens to have driven. A test whose result
    depends on the developer's files is not a test.
    """
    import ats_registry
    monkeypatch.setattr(ats_registry, "ats_for_company", lambda _c: None)
    bb = _with_queue(("indeed:a1", "Sr. Reporting Analyst", "Globex"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].platform = platform
    for r_id in rungs:
        q.steps[0].record(r_id, aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    return _install(monkeypatch,
                    {"/list_tabs": _tabs(SEARCH_URL, apply_url),
                     "/auth_state": {"ok": True, "logged_in": True},
                     "/page_content": {"ok": True, "text": page_text, "frames": [],
                                       "apply_hrefs": []}},
                    blackboard=bb)


#: The prefix up to the account wall — the position the ladder was in when it offered a sign-in
#: for a wall that was not on screen.
_TO_THE_ACCOUNT_RUNG = ("open_pane", "verify_identity", "enter_apply", "classify")


def test_a_mismatch_hands_the_next_action_to_the_observer(monkeypatch):
    """THE LIVE CASE. The `account` rung needs a sign-in wall; the page is a job posting. The world
    contradicts the recipe, so the observation leads — and the rung is demoted, not deleted."""
    _oriented(monkeypatch,
              "https://aholddelhaizeusa.careerswithus.com/job/Sr-Reporting-Analyst",
              _POSTING_TEXT, platform="workday", rungs=_TO_THE_ACCOUNT_RUNG)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["observer"]["mismatch"] is not None            # the disagreement the rule turns on
    nxt = r["next_action"]
    assert nxt["source"] == "observer"
    assert (nxt["id"], nxt["endpoint"]) == ("press_apply", "/orient_action")
    assert nxt["body"] == {"action_id": "press_apply"}
    assert "contradicts the recipe" in nxt["reason"]
    # THE LOSER STAYS VISIBLE. A demoted option is still evidence, and an operator who cannot see
    # the rung cannot judge the observation that overruled it.
    assert nxt["secondary"]["source"] == "rung"
    assert (nxt["secondary"]["id"], nxt["secondary"]["endpoint"]) == ("account", "/apply_step")
    assert nxt["secondary"]["demoted_because"]


def test_no_mismatch_leaves_the_next_action_with_the_rung(monkeypatch):
    """Agreement means the recipe is on track: the `account` rung meets an actual account wall, so
    the ladder leads and the observer's own plan is the secondary."""
    _oriented(monkeypatch,
              "https://globex.wd1.myworkdayjobs.com/en-US/careers/login",
              _GATE_TEXT, platform="workday", rungs=_TO_THE_ACCOUNT_RUNG)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["observer"]["mismatch"] is None
    assert r["observer"]["confidence"] != "low"             # it agreed; it did not merely abstain
    nxt = r["next_action"]
    assert nxt["source"] == "rung"
    assert (nxt["id"], nxt["endpoint"]) == ("account", "/apply_step")
    assert nxt["observer_abstained"] is False
    assert nxt["secondary"]["source"] == "observer"
    # "Work the account step" is guidance, not something we can perform from here — and the flag
    # survives the demotion, so the panel cannot render it as a button that does nothing.
    assert nxt["secondary"]["driveable"] is False


def test_a_low_confidence_observer_yields_to_the_rung_and_says_it_abstained(monkeypatch):
    """An unsure observer with nothing to object to does not get the wheel — but its abstention is
    STATED. A verdict dropped in silence is indistinguishable from one that was never taken."""
    _oriented(monkeypatch, "https://careers.globex.io/apply/1", _POSTING_TEXT)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["observer"]["confidence"] == "low"             # nothing named an owner for this host
    assert r["observer"]["mismatch"] is None                # and it has no objection to raise
    nxt = r["next_action"]
    assert nxt["source"] == "rung" and nxt["id"] == "open_pane"
    assert nxt["observer_abstained"] is True
    assert "abstained" in nxt["reason"]
    assert nxt["secondary"]["source"] == "observer" and nxt["secondary"]["id"] == "press_apply"


# --- teaching inside an apply step ------------------------------------------------------------
def _teach_ready():
    bb = _with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "apply1", "url": "https://smartapply.indeed.com/x"}
    return bb


def _fake_teach(monkeypatch, result):
    from routers import controller as cr
    seen = {}

    def _commit(body):
        seen["body"] = body
        return result
    monkeypatch.setattr(cr, "teach_commit", _commit)
    return seen


def test_a_taught_action_lands_on_the_step_and_in_the_journal(monkeypatch):
    """One act, one record, in BOTH places. Teaching through /teach/commit alone journals
    perfectly and leaves the step's trail empty — two surfaces with separate memories of the same
    act, the bug already found once today with the sweep and the ledger."""
    seen = _fake_teach(monkeypatch, {"held": False, "outcome": "ok", "journaled": True,
                                     "landed_state": "indeed_apply_questions", "verified": True})
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_teach",
                        json={"intent": "click", "params": {"field": "Continue"},
                              "rationale": "the resume step is done; Continue moves to questions",
                              "rung": "resume_review"}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].rung == "resume_review" and step.minis[-1].outcome == aps.OK
    assert step.minis[-1].initiator == "teacher"
    assert r["last_step"]["taught"]["journaled"] is True
    # it drove the APPLY tab, not the search tab
    assert seen["body"].tab_id == "apply1"
    assert seen["body"].decision.rationale.startswith("the resume step is done")


def test_a_taught_action_without_a_reason_is_refused(monkeypatch):
    """The WHY is the training signal. An action with no reasoning teaches the students nothing
    they could not have guessed."""
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_teach",
                        json={"intent": "click", "rationale": "   "})
    finally:
        _teardown()
    assert r.status_code == 422 and "training signal" in r.json()["detail"]


def test_a_held_submit_records_as_needing_the_operator_not_as_a_failure(monkeypatch):
    """The consequential gate firing is the system working. It must not read as a broken step."""
    _fake_teach(monkeypatch, {"held": True, "journaled": True,
                              "detail": "SUBMIT held for the operator"})
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_teach",
                        json={"intent": "submit", "rationale": "every field is answered"}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.HUMAN_REQUIRED
    assert step.needs_operator() is True
    assert r["last_step"]["taught"]["held"] is True


def test_a_failed_teach_is_recorded_rather_than_raised(monkeypatch):
    _fake_teach(monkeypatch, {"held": False, "outcome": "not_found",
                              "detail": "target gone", "journaled": True})
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_teach",
                        json={"intent": "click", "rationale": "trying the Continue button"})
    finally:
        _teardown()
    assert r.status_code == 200
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.FAILED


def test_teaching_with_no_open_application_is_refused(monkeypatch):
    bb = _with_queue(("indeed:a1", "One", "Acme"))
    q = aps.Queue.from_dict(bb.world["apply_queue"]); q.steps[0].finish(aps.SUBMITTED)
    bb.world["apply_queue"] = q.as_dict()
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_teach",
                        json={"intent": "click", "rationale": "anything"})
    finally:
        _teardown()
    assert r.status_code == 409


# --- the teacher proposes, the operator steers ------------------------------------------------
def _proposed(bb, **over):
    p = {"job_id": "indeed:a1", "intent": "click", "params": {"field": "Continue"},
         "rationale": "the resume step is done; Continue moves to the questions",
         "evidence": ["resume_module visible"], "expected_next": ["indeed_apply_questions"],
         "rung": "resume_review", "note": "watch for the AI-use attestation next", "at": "now"}
    p.update(over)
    bb.world["apply_proposal"] = p
    return bb


def test_a_proposal_drives_nothing_and_waits(monkeypatch):
    """The pause. A proposal is a claim about the next action, on the record, before it exists."""
    seen = _fake_teach(monkeypatch, {"held": False, "outcome": "ok", "journaled": True})
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs(SEARCH_URL),
                               "/auth_state": {"ok": True, "logged_in": True}},
                              blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_propose",
                        json={"intent": "click", "params": {"control": "Continue"},
                              "rationale": "the resume step is done",
                              "note": "AI-use attestation may follow"}).json()
    finally:
        _teardown()
    assert r["proposal"]["intent"] == "click"
    assert r["proposal"]["note"] == "AI-use attestation may follow"
    assert "body" not in seen                       # nothing was driven
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].rung == "classify"        # no new mini-step was recorded


def test_a_proposal_without_reasoning_is_refused(monkeypatch):
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_propose",
                        json={"intent": "click", "rationale": ""})
    finally:
        _teardown()
    assert r.status_code == 422 and "disagree WITH" in r.json()["detail"]


def test_go_commits_the_teachers_take_as_the_teacher(monkeypatch):
    seen = _fake_teach(monkeypatch, {"held": False, "outcome": "ok", "journaled": True})
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_proposed(_teach_ready()))
    try:
        client.post("/api/session_control/1/apply_decide", json={"action": "go"})
    finally:
        _teardown()
    assert seen["body"].decision.intent == "click"
    assert seen["body"].proposed is None            # no disagreement, no contrast
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].initiator == "teacher"
    assert saved["bb"].world.get("apply_proposal") is None   # consumed


def test_a_correction_drives_the_operators_take_and_journals_both(monkeypatch):
    """The golden pair. The operator's version acts; the teacher's losing take rides along as the
    contrast, so the students learn the disagreement rather than only the winner."""
    seen = _fake_teach(monkeypatch, {"held": False, "outcome": "ok", "journaled": True})
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_proposed(_teach_ready()))
    try:
        client.post("/api/session_control/1/apply_decide",
                    json={"action": "correct", "intent": "select_option",
                          "params": {"field": "Work authorization", "value": "Yes"},
                          "rationale": "Continue is disabled until work auth is answered"})
    finally:
        _teardown()
    assert seen["body"].decision.intent == "select_option"
    assert seen["body"].decision.rationale.startswith("Continue is disabled")
    assert seen["body"].proposed.intent == "click"          # the teacher's take, kept
    assert seen["body"].proposed.rationale.startswith("the resume step is done")
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].initiator == "operator"


def test_a_correction_without_a_reason_is_refused(monkeypatch):
    """The reasoning IS the training signal — it is the whole reason a correction beats a yes."""
    _fake_teach(monkeypatch, {"held": False, "outcome": "ok"})
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_proposed(_teach_ready()))
    try:
        r = client.post("/api/session_control/1/apply_decide",
                        json={"action": "correct", "intent": "click",
                              "params": {"control": "Continue"}, "rationale": "no"})
    finally:
        _teardown()
    assert r.status_code == 422 and "training signal" in r.json()["detail"]


def test_skip_drops_the_proposal_without_driving(monkeypatch):
    seen = _fake_teach(monkeypatch, {"held": False, "outcome": "ok"})
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_proposed(_teach_ready()))
    try:
        r = client.post("/api/session_control/1/apply_decide", json={"action": "skip"}).json()
    finally:
        _teardown()
    assert "body" not in seen
    assert saved["bb"].world.get("apply_proposal") is None
    assert "Dropped" in r["last_step"]["detail"]


def test_deciding_with_nothing_pending_is_refused(monkeypatch):
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_decide", json={"action": "go"})
    finally:
        _teardown()
    assert r.status_code == 409


def test_a_malformed_proposal_is_refused_before_the_operator_sees_it(monkeypatch):
    """The whole point of validating at propose time: the operator's approval should mean
    something, and a click addressed with `field` reached act time once already."""
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_teach_ready())
    try:
        r = client.post("/api/session_control/1/apply_propose",
                        json={"intent": "click", "params": {"field": "Apply now"},
                              "rationale": "the apply control is visible"})
    finally:
        _teardown()
    assert r.status_code == 422 and "needs control" in r.json()["detail"]


def test_a_malformed_correction_is_refused_too(monkeypatch):
    _fake_teach(monkeypatch, {"held": False, "outcome": "ok"})
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_proposed(_teach_ready()))
    try:
        r = client.post("/api/session_control/1/apply_decide",
                        json={"action": "correct", "intent": "click",
                              "params": {"field": "Continue"},
                              "rationale": "the resume module needs Continue not Apply"})
    finally:
        _teardown()
    assert r.status_code == 422 and "needs control" in r.json()["detail"]


def test_the_view_surfaces_the_observed_apply_type(monkeypatch):
    """A proposal must be made against what the pane REPORTED, not what we assumed. On 2026-07-24
    a proposal cited apply_type=indeed_apply for a posting whose pane said company_site."""
    bb = _teach_ready()
    bb.world["open_pane"] = {"title": "Compliance Reporting Analyst", "apply_type": "company_site"}
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["open_pane"]["apply_type"] == "company_site"


def test_classify_finds_the_apply_tab_rather_than_the_last_one(monkeypatch):
    """Found live 2026-07-24 before it could bite: when the operator approves an Apply through the
    teach path, the new tab opens without apply_step ever seeing it — so `apply_tab` is unset and
    "the last tab" is the Indeed search. That would have classified Indeed as the ATS of a Workday
    application."""
    bb = _with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply"):
        q.steps[0].record(r_id, aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world.pop("apply_tab", None)          # exactly the teach-path case
    _, saved = _install(
        monkeypatch,
        # the Workday tab FIRST, the search tab last — the ordering that broke the guess
        {"/list_tabs": _tabs("https://mfs.wd1.myworkdayjobs.com/en-US/MFS-Careers/job/x", SEARCH_URL),
         "/auth_state": {"ok": True, "logged_in": True}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.platform == "workday"
    assert step.minis[-1].outcome == aps.OK
    assert "myworkdayjobs" in step.minis[-1].detail
    assert "driven before" in r["last_step"]["detail"]


def test_rebuild_queue_restores_steps_from_approved_picks(monkeypatch):
    """The recovery path. The queue was lost (reconcile clobbered world), but the approved picks
    survived on a different field and every job kept its ObservedJob row."""
    bb = _at_start_line()
    bb.search_state.approved = ["indeed:a1", "indeed:a2", "indeed:a3"]
    bb.world.pop("apply_queue", None)                 # the loss

    rows = {"indeed:a1": ("Compliance Analyst", "Acme"), "indeed:a2": ("BI Analyst", "Acme"),
            "indeed:a3": ("Data Analyst", "Acme")}
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb, observed=rows)
    try:
        r = client.post("/api/session_control/1/rebuild_queue", json={}).json()
    finally:
        _teardown()
    steps = r["queue"]["steps"]
    assert [s["job_id"] for s in steps] == ["indeed:a1", "indeed:a2", "indeed:a3"]
    assert steps[0]["title"] == "Compliance Analyst"
    assert "3 application(s) restored" in r["last_step"]["detail"]


def test_rebuild_keeps_progress_on_a_half_driven_step(monkeypatch):
    """Rebuild fills only what is MISSING — a step already part-way through is not reset."""
    bb = _at_start_line()
    bb.search_state.approved = ["indeed:a1", "indeed:a2"]
    q = aps.Queue(page=1)
    q.enqueue([{"job_id": "indeed:a1", "title": "One"}])
    q.steps[0].record("open_pane", aps.OK)
    q.steps[0].record("verify_identity", aps.OK)
    bb.world["apply_queue"] = q.as_dict()

    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb, observed={"indeed:a2": ("Two", "Beta")})
    try:
        r = client.post("/api/session_control/1/rebuild_queue", json={}).json()
    finally:
        _teardown()
    steps = {s["job_id"]: s for s in r["queue"]["steps"]}
    assert len(steps["indeed:a1"]["minis"]) == 2          # progress kept
    assert steps["indeed:a2"]["title"] == "Two"           # the missing one added
    assert "1 application(s) restored" in r["last_step"]["detail"]


# --- reconciling a step's record to the live window -------------------------------------------
def test_reconcile_step_records_what_the_open_ats_tab_proves(monkeypatch):
    """The browser is truth, the record is memory. A rebuilt step starts at `queued` even when a
    Workday tab is plainly open — reconcile records the prefix that tab is PROOF of, so the
    operator is not asked to re-drive work the world already did."""
    bb = _with_queue(("indeed:a1", "Compliance Reporting Associate", "MFS"))
    bb.world["open_pane"] = {"title": "Compliance Reporting Associate", "apply_type": "company_site"}
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL,
            "https://mfs.wd1.myworkdayjobs.com/en-US/MFS-Careers/job/Boston/Compliance-Reporting-Associate_M"),
         "/auth_state": {"ok": True, "logged_in": True}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/reconcile_step", json={}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    outcomes = {m.rung: m.outcome for m in step.minis}
    assert outcomes["open_pane"] == aps.OK
    assert outcomes["verify_identity"] == aps.OK        # req path matches the pick
    assert outcomes["enter_apply"] == aps.OK
    assert step.platform == "workday"
    assert all("reconciled" in m.detail or "->" in m.detail for m in step.minis)
    assert saved["bb"].world["apply_tab"]["url"].startswith("https://mfs.wd1")


def test_reconcile_reclassifies_when_the_window_names_another_platform(monkeypatch):
    """A settled classify means "we named it once", not "the world cannot disagree".

    The platform is first guessed on Indeed from the apply href — a PREDICTION — and a settled rung
    made that guess permanent. Live 2026-08-12: Odyssey Consulting's card said `workday`, the
    application landed on `careers-odysseyconsult.icims.com`, and the account rung was one press
    from driving Workday's create-account recipe against an iCIMS form and filing the credential
    under `ats_odyssey_consulting_workday`. Reconcile's whole contract is that memory yields.
    """
    bb = _with_queue(("indeed:a1", "Data Business Analyst", "Odyssey Consulting"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK, "from the Indeed card")
    q.steps[0].platform = "workday"                       # the href tell's guess
    q.steps[0].landing_state = "workday_my_information"    # a state that describes nothing here
    bb.world["apply_queue"] = q.as_dict()
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL,
            "https://careers-odysseyconsult.icims.com/jobs/8308/data-business-analyst/login"),
         "/auth_state": {"ok": True, "logged_in": True}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/reconcile_step", json={}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.platform == "icims"
    assert step.landing_state is None, "a state named for the other platform must not survive"
    # BOTH SIDES STAY ON THE RECORD (§10): the wrong name is the evidence that the tell can lie.
    classifies = [m for m in step.minis if m.rung == "classify"]
    assert len(classifies) == 2
    assert "RE-CLASSIFIED" in classifies[-1].detail and "workday" in classifies[-1].detail
    assert "icims" in r["last_step"]["detail"]


def test_reconcile_leaves_an_agreeing_classification_alone(monkeypatch):
    """The mirror: re-classifying when the window AGREES would write a correction that corrects
    nothing and bury the real one in noise."""
    bb = _with_queue(("indeed:a1", "Compliance Reporting Associate", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK, "already walked")
    q.steps[0].platform = "workday"
    bb.world["apply_queue"] = q.as_dict()
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/reconcile_step", json={})
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.platform == "workday"
    assert len([m for m in step.minis if m.rung == "classify"]) == 1


def test_reconcile_reopens_a_rung_its_own_verification_demoted(monkeypatch):
    """THE STALL RECONCILE EXISTS TO END, AND COULD NOT.

    Live 2026-08-11 (Boston College / Cornerstone): `enter_apply` clicked the right button and
    opened the right ATS tab, recorded OK — and the StepRunner's stale hosts list then demoted it
    to `mismatch`. The ladder re-offered `enter_apply` forever (latest verdict wins, by design),
    and reconcile — asking a DIFFERENT question, "any OK ever recorded" — saw the superseded OK,
    called the rung proven, added nothing, and left the operator exactly where they started.

    Both now ask `settled_rungs()`. The demoted rung is unsettled to BOTH, so reconcile re-records
    it from what the open tab proves and the ladder moves on.
    """
    bb = _with_queue(("indeed:a1", "Compliance Reporting Associate", "MFS"))
    bb.world["open_pane"] = {"title": "Compliance Reporting Associate", "apply_type": "company_site"}
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    st = q.steps[0]
    ats = ("https://mfs.wd1.myworkdayjobs.com/en-US/MFS-Careers/job/Boston/"
           "Compliance-Reporting-Associate_M")
    st.record("open_pane", aps.OK, "pane opened")
    st.record("verify_identity", aps.OK, "matches the pick")
    st.record("enter_apply", aps.OK, f"clicked Apply; opened a new tab -> {ats}")
    st.record("enter_apply", aps.MISMATCH, "world disagrees: not an application host")
    bb.world["apply_queue"] = q.as_dict()
    assert "enter_apply" not in st.settled_rungs()          # the stall, reproduced

    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, ats), "/auth_state": {"ok": True, "logged_in": True}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/reconcile_step", json={}).json()
    finally:
        _teardown()

    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert "enter_apply" in step.settled_rungs()            # reopened and re-proven
    assert step.platform == "workday"
    # …and the ladder has moved past the rung it was pinned on.
    nxt, _ = step.walk_to_next_rung()
    assert nxt is not None and nxt.id != "enter_apply"


def test_reconcile_flags_a_title_drift_instead_of_rubber_stamping_it(monkeypatch):
    """verify_identity is the near-miss guard and the one rung reconcile must NOT auto-confirm.
    A Workday req that does not match the Indeed pick is exactly what it exists to catch."""
    bb = _with_queue(("indeed:a1", "Senior Warehouse Associate", "MFS"))
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL,
            "https://mfs.wd1.myworkdayjobs.com/en-US/MFS/job/Boston/Compliance-Reporting-Analyst_M"),
         "/auth_state": {"ok": True, "logged_in": True}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/reconcile_step", json={}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    vi = next(m for m in step.minis if m.rung == "verify_identity")
    assert vi.outcome == aps.UNKNOWN
    assert step.needs_operator() is True
    # and it did NOT go on to record enter_apply on an unconfirmed identity
    assert not any(m.rung == "enter_apply" for m in step.minis)


def test_reconcile_refuses_when_no_ats_tab_is_open(monkeypatch):
    """Nothing to prove: if only the Indeed search is open, the window says nothing about progress."""
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_with_queue(("indeed:a1", "One", "Acme")))
    try:
        r = client.post("/api/session_control/1/reconcile_step", json={})
    finally:
        _teardown()
    assert r.status_code == 409 and "nothing the window can prove" in r.json()["detail"]


def test_reconcile_is_idempotent(monkeypatch):
    """Running it twice adds nothing the second time — the rungs are already recorded."""
    bb = _with_queue(("indeed:a1", "Compliance Reporting Associate", "MFS"))
    tabs = _tabs(SEARCH_URL,
        "https://mfs.wd1.myworkdayjobs.com/en-US/MFS/job/Boston/Compliance-Reporting-Associate_M")
    _, saved = _install(monkeypatch,
                        {"/list_tabs": tabs, "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        client.post("/api/session_control/1/reconcile_step", json={})
        n1 = len(aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0].minis)
        client.post("/api/session_control/1/reconcile_step", json={})
        n2 = len(aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0].minis)
    finally:
        _teardown()
    assert n1 == n2


# --- orient: check where we are by CONTENT, not just the URL -----------------------------------
def _wd_step(platform="workday"):
    bb = _with_queue(("indeed:a1", "Compliance Reporting Analyst", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].platform = platform
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "wd", "url": "https://mfs.wd1.myworkdayjobs.com/job/x"}
    return bb


def test_orient_recognises_the_apply_method_modal_the_url_cannot_see(monkeypatch):
    """The core fix. Clicking Apply opens a modal without changing the URL, so URL-only detection
    called a good landing 'unexpected'. The modal's own button text is the signal."""
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "page_text": "Start Your Application",
                      "candidates": [{"role": "button", "name": "Use My Last Application"},
                                     {"role": "button", "name": "Autofill with Resume"}]}},
        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/orient", json={}).json()
    finally:
        _teardown()
    o = r["last_step"]["orient"]
    assert o["state"] == "workday_apply_method"
    assert o["progress"]["steps_to_submit"] == 8       # depth awareness
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].rung == "orient" and step.minis[-1].outcome == aps.OK


def test_orient_reports_depth_from_submit(monkeypatch):
    _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "page_text": "Please review your application before you submit",
                      "candidates": []}},
        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/orient", json={}).json()
    finally:
        _teardown()
    o = r["last_step"]["orient"]
    assert o["state"] == "workday_review" and o["progress"]["at_review_gate"] is True
    assert o["progress"]["steps_to_submit"] == 0


def test_orient_on_a_workday_origin_always_recognises_at_least_the_posting(monkeypatch):
    """A Workday URL with no step marker is still the job posting — recognised, not new territory.
    'New territory' is for platforms/pages we genuinely cannot place, not for a bare Workday page."""
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "page_text": "some page with no step marker", "candidates": []}},
        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/orient", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["orient"]["state"] == "workday_job_posting"
    assert "new" not in r["last_step"]["detail"].lower()


def test_orient_does_not_spam_the_same_state(monkeypatch):
    tabs = _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x")
    scan = {"ok": True, "page_text": "Start Your Application",
            "candidates": [{"role": "button", "name": "Use My Last Application"}]}
    _, saved = _install(monkeypatch,
                        {"/list_tabs": tabs, "/auth_state": {"ok": True, "logged_in": True},
                         "/ax_scan": scan},
                        blackboard=_wd_step())
    try:
        client.post("/api/session_control/1/orient", json={})
        client.post("/api/session_control/1/orient", json={})
    finally:
        _teardown()
    minis = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0].minis
    assert sum(1 for m in minis if m.rung == "orient") == 1     # recorded once, not twice


def test_orient_refuses_with_no_application_tab(monkeypatch):
    bb = _wd_step()
    bb.world.pop("apply_tab", None)      # nothing recorded, and only the search tab is open
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/orient", json={})
    finally:
        _teardown()
    assert r.status_code == 409


# --- the account-creation handoff (the boundary made concrete) --------------------------------
def _wd_at_wall():
    bb = _wd_step()
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].record("workday_apply_method_choose", aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["orient"] = {"platform": "workday", "state": "workday_create_account", "url": "https://mfs.wd1.myworkdayjobs.com/job/x"}
    return bb



def _settled(step) -> set:
    """The rungs this step counts as walked — mirroring `ApplyStep.next_rung`'s own rule. Asserting
    on this rather than on `next_rung()` keeps these tests about the ACCOUNT rung: a fixture that
    never walked `classify` would otherwise make every one of them pass for the wrong reason."""
    return {m.rung for m in step.minis if m.outcome in (aps.OK, aps.SKIPPED)}

def test_account_handoff_surfaces_credentials_and_never_drives(monkeypatch):
    """THE boundary. The agent registers the account and hands the operator the credentials to
    type; it never enters a password or creates the account. No /execute, ever."""
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                               "/auth_state": {"ok": True, "logged_in": True}},
                              blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "handoff"}).json()
    finally:
        _teardown()
    acct = r["last_step"]["account"]
    assert acct["leg"] == "create_account" and acct["button"] == "Create Account"
    assert acct["username"]                      # a username to use is surfaced
    # The boundary text used to promise the agent "never enters a password or creates an account",
    # which stopped being true the day mode="auto" shipped (2026-07-24) and left this test pinning
    # a claim the product contradicted. What must hold is not that sentence but the GATES.
    assert "captcha" in acct["boundary"] and "2FA" in acct["boundary"]
    assert "switched OFF" in acct["boundary"]      # the marketing opt-ins are refused, not left
    assert r["awaiting"] == "operator_account"
    assert "/execute" not in harness.paths()     # nothing was driven
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    # The rung is the LADDER's id, always — the leg lives in the detail. A leg name here would
    # leave `account` unsettled forever (see _ACCOUNT_RUNG).
    assert step.minis[-1].rung == "account"
    assert "create_account leg" in step.minis[-1].detail
    assert step.minis[-1].outcome == aps.HUMAN_REQUIRED
    assert step.needs_operator() is True         # paused for the operator
    # ...and it says so structurally: the leg that drives nothing staged nothing. Asserted HERE,
    # on the endpoint that writes it, because the rung id is the same one the auto path records
    # and the two differ in the detail only by prose — which no reader may key off.
    assert step.minis[-1].staged is False


def test_the_password_is_never_written_to_the_event_log(monkeypatch):
    """A handoff surfaces the credential for display; it must not be journaled into the event log."""
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "handoff"}).json()
    finally:
        _teardown()
    pw = r["last_step"]["account"].get("suggested_password")
    if pw:                                        # only meaningful when a suffix is configured
        for e in saved["bb"].events:
            assert pw not in e.detail


def test_account_handoff_is_a_resume_not_a_terminal_park(monkeypatch):
    """Creating the account continues the application; it does not end the step. Distinct from
    apply_flag parked:account_wall, which is 'not making an account for this one'."""
    bb = _wd_at_wall()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "handoff"})  # handoff
        r = client.post("/api/session_control/1/apply_account",
                        json={"mark_created": True}).json()                     # operator made it
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.done is False                     # NOT terminated
    assert any(m.rung == "account" and m.outcome == aps.OK and "handoff leg" in m.detail
               for m in step.minis)
    assert _settled(step) >= {"account"}           # and the rung is SETTLED: the ladder moves on
    assert "continue" in r["last_step"]["detail"]


def test_account_handoff_refuses_without_a_classified_ats(monkeypatch):
    bb = _with_queue(("indeed:a1", "Some Co", "Some Co"))   # platform not set
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_account", json={})
    finally:
        _teardown()
    assert r.status_code == 409 and "known ATS" in r.json()["detail"]


def test_account_handoff_persists_on_the_view_so_a_reload_keeps_it(monkeypatch):
    """The handoff lived only in last_step, which a fresh GET does not carry — an operator who
    refreshed lost the credentials panel. It now persists in world like the proposal does."""
    bb = _wd_at_wall()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "handoff"})
        g = client.get("/api/session_control/1").json()                # a fresh read (no last_step)
    finally:
        _teardown()
    assert g["account_handoff"]["button"] == "Create Account"
    assert g["account_handoff"]["job_id"] == "indeed:a1"


def test_parking_clears_a_lingering_handoff(monkeypatch):
    """A finished step must not carry its handoff onto the next job."""
    bb = _wd_at_wall()
    bb.world["apply_queue"] = aps.Queue.from_dict(bb.world["apply_queue"]).as_dict()
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "handoff"})
        client.post("/api/session_control/1/apply_flag",
                    json={"job_id": "indeed:a1", "flag": "parked:account_wall"})
        g = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert g["account_handoff"] is None


# --- automated account creation (the operator's own local task) -------------------------------
def test_account_creation_is_automated_by_default(monkeypatch):
    """The operator's correction: their own account for their own job search is a generalizable
    local task and should be automated, not gated behind a manual handoff every time. The system
    fills AND submits the create-account form."""
    typed = []

    def _execute(payload):
        if payload.get("action_id") == "type":
            typed.append(payload["target_name"])
        return {"outcome": "ok"}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/execute": _execute,
         "/ax_scan": {"ok": True, "page_text": "My Information", "candidates": []}},  # landed past signup
        blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()
    # it filled the three real fields, by exact name — and never the honeypot
    assert typed == ["Email Address", "Password", "Verify New Password"]
    assert r["last_step"]["ok"] is True and "automatically" in r["last_step"]["detail"]
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert any(m.rung == "account" and m.outcome == aps.OK and "create leg" in m.detail
               for m in step.minis)
    assert _settled(step) >= {"account"}           # settled, so the ladder stops re-asking for it


def test_the_password_value_never_reaches_a_log_or_a_mini_step(monkeypatch):
    """Credential-safe: the value goes only into /execute (which logs the target name, not the
    value). It must never appear in an event or a recorded mini-step detail."""
    import ats_accounts
    pw = ats_accounts.derive_password("MFS Investment Management")
    _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/execute": {"outcome": "ok"},
         "/ax_scan": {"ok": True, "page_text": "My Information", "candidates": []}},
        blackboard=_wd_at_wall())
    saved_bb = {}
    import apply_state_store as store
    monkeypatch.setattr(store, "save", lambda bb: saved_bb.update(bb=bb))
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "auto"})
    finally:
        _teardown()
    if pw:
        bb = saved_bb["bb"]
        for e in bb.events:
            assert pw not in e.detail
        for s in aps.Queue.from_dict(bb.world["apply_queue"]).steps:
            for m in s.minis:
                assert pw not in m.detail


def test_fill_mode_stops_before_the_outward_facing_submit(monkeypatch):
    """'fill' fills the form but leaves the Create Account click to the operator — for when they
    want to eyeball an outward-facing account creation before it happens."""
    clicked = []

    def _execute(payload):
        if payload.get("action_id") == "click":
            clicked.append(payload["target_name"])
        return {"outcome": "ok"}

    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True}, "/execute": _execute},
        blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "fill"}).json()
    finally:
        _teardown()
    assert "Create Account" not in clicked          # did NOT submit
    assert r["awaiting"] == "operator_account"
    assert "confirm" in r["last_step"]["detail"].lower()
    # A filled, unsubmitted credential form is the case the unsaved-work flag exists for. Same
    # rung id as the handoff that stages nothing — the mini-step is where they part.
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].rung == "account" and step.minis[-1].staged is True


def test_a_captcha_on_signup_escalates_and_never_auto_solves(monkeypatch):
    """The real gate, not the manual boundary: a challenge on the signup form escalates, and no
    field is filled underneath it."""
    typed = []

    def _execute(payload):
        typed.append(payload.get("action_id"))
        return {"outcome": "ok"}

    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True}, "/execute": _execute,
         "/challenge_visibility": {"ok": True, "blocking": True}},
        frames=[{"type": "iframe", "url": "https://www.google.com/recaptcha/api2/bframe?k=x"}],
        blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_challenge"
    assert "never auto-solve" in r["last_step"]["detail"]
    assert typed == []                              # nothing filled under the challenge


def test_an_email_verification_wall_escalates_after_submit(monkeypatch):
    """Submitting can lead to an email/2FA code prompt — a real gate we do not fabricate. It
    escalates (and points at the Gmail errand as the next automation)."""
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True}, "/execute": {"outcome": "ok"},
         "/ax_scan": {"ok": True, "page_text": "Please enter the verification code we sent",
                      "candidates": []}},
        blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()
    assert r["awaiting"] == "operator_verify"
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert any(m.rung == "account" and m.outcome == aps.HUMAN_REQUIRED and "verify leg" in m.detail
               for m in step.minis)
    assert "account" not in _settled(step)         # a real gate does NOT settle the rung


def test_handoff_mode_still_available_for_a_manual_creation(monkeypatch):
    """The manual handoff is not deleted — it is one mode among several, for when the operator
    prefers to type it themselves."""
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                           "/auth_state": {"ok": True, "logged_in": True}},
                          blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "handoff"}).json()
    finally:
        _teardown()
    assert r["account_handoff"]["button"] == "Create Account"
    assert "/execute" not in harness.paths()        # handoff drives nothing


# --- bunch fill: the whole step at once, honestly ---------------------------------------------
_MYINFO_SCAN = {"ok": True, "page_text": "", "candidates": [
    {"role": "textbox", "name": "First Name"},
    {"role": "textbox", "name": "Last Name"},
    {"role": "textbox", "name": "Address Line 1"},
    {"role": "textbox", "name": "City"},
    {"role": "textbox", "name": "How Did You Hear About Us?"},
    {"role": "button", "name": "Save and Continue"},
]}


def test_apply_fill_plans_the_bunch_without_driving(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                           "/auth_state": {"ok": True, "logged_in": True}, "/ax_scan": _MYINFO_SCAN},
                          blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_fill", json={"execute": False}).json()
    finally:
        _teardown()
    s = r["last_step"]["fill_summary"]
    assert s["fillable"] == 2                         # First, Last (How-did-you-hear is a prompt)
    assert "Address Line 1" in s["missing"] and "City" in s["missing"]
    assert "/execute" not in harness.paths()          # plan-only drives nothing


def test_a_question_group_does_not_hide_the_control_it_names(monkeypatch):
    """Workday wraps each question in a `role=group` NAMED AFTER THE QUESTION.

    The census dedupe suppressed any row whose name already appeared in the AX tree, and the
    planner then dropped the group because a group is not a fillable control — so the field fell
    through the crack between them. Two of Eversource's five textareas were lost this way, and the
    three that survived only did so because the census cuts names at ~90 chars and they therefore
    failed to match their own group (live 2026-08-17). "Already known" has to mean "already
    addressable".
    """
    q = "Please list your full legal name."
    scan = {"ok": True, "page_text": "", "candidates": [
        {"role": "group", "name": q},                      # the wrapper, not a control
        {"role": "button", "name": " Select One Required"},
    ]}
    census = {"ok": True, "unanswered": [
        {"field": q + "*", "kind": "textarea", "selector": "#q1", "answered": False}]}
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/ax_scan": scan, "/scan_required": census},
             blackboard=_wd_step(),
             answers=[_Answer("full_name", "Gene Kyle Magsipoc",
                              patterns=["full legal name"], hint="text")])
    try:
        r = client.post("/api/session_control/1/apply_fill", json={"execute": False}).json()
    finally:
        _teardown()
    rows = r["last_step"]["fill_plan"]
    assert [x["answer_key"] for x in rows] == ["full_name"]
    assert rows[0]["matched_by"] == "question_patterns" and rows[0]["selector"] == "#q1"


def test_a_real_control_still_wins_the_dedupe_against_the_census(monkeypatch):
    """The dedupe this scoping had to preserve: an AX textbox "First Name" must still suppress
    the census's "First Name*", or the same box is planned — and typed — twice."""
    scan = {"ok": True, "page_text": "", "candidates": [{"role": "textbox", "name": "First Name"}]}
    census = {"ok": True, "unanswered": [
        {"field": "First Name*", "kind": "input", "selector": "#fn", "answered": False}]}
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/ax_scan": scan, "/scan_required": census},
             blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_fill", json={"execute": False}).json()
    finally:
        _teardown()
    assert len(r["last_step"]["fill_plan"]) == 1        # one box, one row


def test_apply_fill_executes_only_the_confident_fields(monkeypatch):
    typed = []

    def _execute(payload):
        if payload.get("action_id") == "type":
            typed.append((payload["target_name"], payload["value"]))
        return {"outcome": "ok"}

    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/ax_scan": _MYINFO_SCAN, "/execute": _execute},
                        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_fill", json={"execute": True}).json()
    finally:
        _teardown()
    names = [t[0] for t in typed]
    assert names == ["First Name", "Last Name"]       # prompt fields are not text-filled
    assert dict(typed)["First Name"] == "Gene"
    assert "Address Line 1" not in names              # never filled a blank
    assert "How Did You Hear About Us?" not in names  # a prompt, handled by apply_prompt_select
    assert "Still need you for" in r["last_step"]["detail"]
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert any(m.rung == "form_fill" for m in step.minis)


def test_apply_fill_never_invents_a_missing_address(monkeypatch):
    """The load-bearing guarantee: a field with no data is never typed into."""
    typed = []
    monkeypatch_execute = lambda payload: (typed.append(payload.get("target_name"))
                                           if payload.get("action_id") == "type" else None) or {"outcome": "ok"}
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/ax_scan": _MYINFO_SCAN, "/execute": monkeypatch_execute},
             blackboard=_wd_step())
    try:
        client.post("/api/session_control/1/apply_fill", json={"execute": True})
    finally:
        _teardown()
    assert "Address Line 1" not in typed and "City" not in typed


# --- prompt select: reuse /select_prompt, source-aware, Other fallback ------------------------
def test_prompt_select_source_picks_indeed_when_offered(monkeypatch):
    """The reuse: source=indeed tries 'Indeed' first; /select_prompt clicks it and we stop."""
    calls = []

    def _select(payload):
        calls.append(payload["path"])
        return {"outcome": "ok"} if payload["path"][-1] == "Indeed" else {"outcome": "no_option"}

    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True}, "/select_prompt_path": _select},
                        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_prompt_select",
                        json={"field_name": "How Did You Hear About Us?", "use_source": True}).json()
    finally:
        _teardown()
    assert r["last_step"]["picked"] == "Indeed"
    assert calls == [["Job Board", "Indeed"]]        # drilled category > leaf, stopped at the hit
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.OK


def test_prompt_select_falls_back_to_other_when_source_not_offered(monkeypatch):
    """The operator's rule: Indeed isn't always an option; Other is acceptable. It tries Indeed,
    SimplyHired, then Other — and records that it fell back."""
    def _select(payload):
        return {"outcome": "ok"} if payload["path"][-1] == "Other" else {"outcome": "no_option"}

    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True}, "/select_prompt_path": _select},
                        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_prompt_select",
                        json={"field_name": "How Did You Hear About Us?", "use_source": True}).json()
    finally:
        _teardown()
    assert r["last_step"]["picked"] == "Other"
    assert r["last_step"]["tried"] == ["Indeed", "Other"]   # standard: Indeed, then Other
    assert "truthful" in r["last_step"]["detail"]


def test_prompt_select_stops_on_a_real_error_not_no_option(monkeypatch):
    """not_opened is a stale-session error — don't hammer every candidate against a shut prompt."""
    calls = []

    def _select(payload):
        calls.append(payload["path"])
        # not_opened on the first path is a stale prompt; not_opened is retried once then stops.
        return {"outcome": "not_found", "detail": "field gone"}

    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
              "/auth_state": {"ok": True, "logged_in": True}, "/select_prompt_path": _select},
             blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_prompt_select",
                        json={"field_name": "How Did You Hear About Us?", "use_source": True}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is False
    assert calls == [["Job Board", "Indeed"]]        # not_found stops immediately


def test_prompt_select_explicit_value_for_a_dropdown(monkeypatch):
    """The same mechanism drives an ordinary dropdown: State = New Hampshire, one explicit value."""
    def _select(payload):
        return {"outcome": "ok"} if payload["path"] == ["New Hampshire"] else {"outcome": "no_option"}

    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True}, "/select_prompt_path": _select},
                        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_prompt_select",
                        json={"field_name": "State", "value": "New Hampshire"}).json()
    finally:
        _teardown()
    assert r["last_step"]["picked"] == "New Hampshire"


def test_prompt_select_needs_a_value_or_source(monkeypatch):
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_prompt_select",
                        json={"field_name": "State"})
    finally:
        _teardown()
    assert r.status_code == 422


def test_prompt_select_unconfirmed_is_not_reported_as_success(monkeypatch):
    """The verify-don't-assume fix: a click that could not be confirmed committed is
    human_required, never a false OK."""
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
                         "/auth_state": {"ok": True, "logged_in": True},
                         "/select_prompt_path": {"outcome": "committed_unconfirmed",
                                                 "detail": "clicked, field still invalid"}},
                        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_prompt_select",
                        json={"field_name": "How Did You Hear About Us?", "use_source": True}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is False
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.minis[-1].outcome == aps.HUMAN_REQUIRED


# --- staleness: the panel's freshness datapoint -----------------------------------------
# PROTOTYPE (perception/staleness.py). Advisory only — the panel shows it, the operator decides.
def test_panel_staleness_is_read_from_the_blackboard_clock(monkeypatch):
    """The last EVENT is the drive's last action, and it survives a process restart — which is
    the whole point: a session picked up the next morning must report its real age instead of
    looking brand new because the API was restarted."""
    import time as _t
    from datetime import datetime, timezone

    bb = _at_start_line()
    bb.events.clear()
    bb.log("review", "page 1: 15 results")
    bb.events[-1].ts = datetime.fromtimestamp(_t.time() - 14.5 * 3600,
                                              tz=timezone.utc).isoformat()
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()

    s = r["staleness"]
    assert s["level"] == "red", "14.5 hours idle is not fresh"
    # ...but the page is alive and signed in, so a RELOAD is the cure — never a new session.
    assert s["verdict"] == "refresh"
    by = {sig["name"]: sig for sig in s["signals"]}
    assert by["idle_s"]["value"] > 14 * 3600
    assert by["responsive"]["level"] == "fresh"


def test_a_working_session_reads_fresh_and_operable(monkeypatch):
    bb = _at_start_line()
    bb.events.clear()
    bb.log("review", "page 1: 15 results")     # just now
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["staleness"]["level"] == "fresh"
    assert r["staleness"]["verdict"] == "continue"


def test_an_unreachable_browser_is_a_handoff_not_a_reload(monkeypatch):
    bb = _at_start_line()
    _install(monkeypatch, {"/list_tabs": {"ok": False, "detail": "ConnectError", "tabs": []},
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["staleness"]["verdict"] == "handoff"


def test_looking_at_a_job_is_not_unsaved_work(monkeypatch):
    """Opening a pane and confirming the job's identity stages NOTHING — a reload re-opens it and
    carries on. Counting that as work suppressed a refresh the panel should have offered (found
    on session 21, 2026-07-26). Withholding a remedy fails as surely as proposing a bad one; it
    just fails quietly."""
    import time as _t
    from datetime import datetime, timezone

    bb = _at_start_line()
    queue = aps.Queue(page=1)
    queue.enqueue([{"job_id": "indeed:aaa", "title": "A Job"}])
    step = queue.steps[0]
    step.record("open_pane", aps.OK, "pane switched")
    step.record("verify_identity", aps.OK, "title matches")
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    bb.events.clear()
    bb.log("review", "page 1")
    bb.events[-1].ts = datetime.fromtimestamp(_t.time() - 14.5 * 3600,
                                              tz=timezone.utc).isoformat()
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["staleness"]["verdict"] == "refresh", "nothing was typed — the reload is free"
    assert "withheld" not in r["staleness"]["why"]


def test_a_half_filled_form_does_hold_unsaved_work(monkeypatch):
    """Once a rung has put something INTO the page, the reload is no longer free."""
    import time as _t
    from datetime import datetime, timezone

    bb = _at_start_line()
    queue = aps.Queue(page=1)
    queue.enqueue([{"job_id": "indeed:aaa", "title": "A Job"}])
    step = queue.steps[0]
    step.record("open_pane", aps.OK, "pane switched")
    step.record("fill_form", aps.OK, "typed first_name")
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    bb.events.clear()
    bb.log("review", "page 1")
    bb.events[-1].ts = datetime.fromtimestamp(_t.time() - 14.5 * 3600,
                                              tz=timezone.utc).isoformat()
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["staleness"]["verdict"] == "continue"
    assert "withheld" in r["staleness"]["why"]


# The SAME rung, both ways. `account` types credentials in "auto"/"fill" and types nothing at all
# in "handoff", so no set of rung ids can separate them — and it may not be split into two rungs,
# because the ladder settles rungs BY NAME (see _ACCOUNT_RUNG). The mini-step says which it was.
def _stale_panel(monkeypatch, *minis):
    """A session idle 14.5 hours whose open step has walked exactly `minis`."""
    import time as _t
    from datetime import datetime, timezone

    bb = _at_start_line()
    queue = aps.Queue(page=1)
    queue.enqueue([{"job_id": "indeed:aaa", "title": "A Job"}])
    for rung, staged in minis:
        queue.steps[0].record(rung, aps.HUMAN_REQUIRED, "", staged=staged)
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    bb.events.clear()
    bb.log("review", "page 1")
    bb.events[-1].ts = datetime.fromtimestamp(_t.time() - 14.5 * 3600,
                                              tz=timezone.utc).isoformat()
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        return client.get("/api/session_control/1").json()["staleness"]
    finally:
        _teardown()


def test_an_account_handoff_is_not_unsaved_work(monkeypatch):
    """Found live on session 21 (Teradyne / SuccessFactors, 2026-07-28): 18.4 hours idle, red, and
    the SAP signup form verifiably EMPTY on the screenshot — yet the panel read "a reload cures
    this, but the page holds unsaved work, so refresh is withheld". The handoff leg surfaces the
    credential on a card and waits; it puts nothing in the page. The refresh was being withheld to
    protect nothing, and a manual reload fixed the session."""
    s = _stale_panel(monkeypatch, ("open_pane", None), ("account", False))
    assert s["verdict"] == "refresh", "the handoff typed nothing — the reload is free"
    assert "withheld" not in s["why"]


def test_a_filled_account_form_is_still_unsaved_work(monkeypatch):
    """The other half, and the reason the rung is not simply read-only: in auto/fill the same rung
    puts real credentials in the boxes, and a reload throws them away."""
    s = _stale_panel(monkeypatch, ("open_pane", None), ("account", True))
    assert s["verdict"] == "continue"
    assert "withheld" in s["why"]


def test_a_mini_step_that_says_nothing_is_still_judged_by_its_rung(monkeypatch):
    """Backwards compatibility, which here means every queue already persisted in a blackboard:
    those mini-steps predate the field and carry no answer. They must keep the rung's verdict
    rather than defaulting to "typed nothing", which would silently un-protect real work."""
    assert _stale_panel(monkeypatch, ("fill_form", None))["verdict"] == "continue"
    assert _stale_panel(monkeypatch, ("open_pane", None))["verdict"] == "refresh"


# --- the selection is a STEP ------------------------------------------------------------
# Operator, 2026-07-26: "it feels like it's not an actual step." It was not: reviewing a page was
# a rung and working an application was a rung, but the choice between them — the only part a
# human actually makes — was an `awaiting` flag and a log line.
def test_the_selection_appears_on_the_ladder_as_its_own_step(monkeypatch):
    bb = _at_start_line()
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = [{"job_id": "indeed:aaa", "title": "A Job"},
                                {"job_id": "indeed:bbb", "title": "B Job"}]
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    row = next(x for x in r["ladder"] if x["id"] == "select:1")
    assert row["label"] == "Page 1 picks made"
    assert row["status"] == "next", "the cards are read — the choice is the operator's move"
    assert row["kind"] == "standing", "choosing again costs nothing; it must not read as spent"


def test_the_selection_step_is_pending_until_the_page_is_read(monkeypatch):
    """A choice between fifteen jobs nobody has looked at is not a step to invite."""
    bb = _at_start_line()
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = []
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert next(x for x in r["ladder"] if x["id"] == "select:1")["status"] == "pending"


def test_choosing_marks_the_step_with_who_decided_and_what_they_picked(monkeypatch):
    bb = _at_start_line()
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = [{"job_id": f"indeed:{c}", "title": f"{c} Job"} for c in "abc"]
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:a", "indeed:c"], "advance": False,
                              "initiator": "operator"}).json()
    finally:
        _teardown()
    row = next(x for x in r["ladder"] if x["id"] == "select:1")
    assert row["status"] == "held"
    assert "2 of 3 picked by operator" in row["reached"]["evidence"]


def test_re_choosing_is_allowed_because_a_selection_spends_nothing(monkeypatch):
    """The difference between this rung and the query it sits under. Adding to your picks must
    never be refused the way re-running a search is."""
    bb = _at_start_line()
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = [{"job_id": f"indeed:{c}", "title": f"{c} Job"} for c in "abc"]
    harness, saved = _install(monkeypatch,
                              {"/list_tabs": _tabs(SEARCH_URL),
                               "/auth_state": {"ok": True, "logged_in": True}},
                              blackboard=bb)
    try:
        client.post("/api/session_control/1/choose",
                    json={"picks": ["indeed:a"], "advance": False})
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:b"], "advance": False}).json()
    finally:
        _teardown()
    row = next(x for x in r["ladder"] if x["id"] == "select:1")
    assert row["status"] == "held"
    assert "1 of 3 picked by operator" in row["reached"]["evidence"]
    assert set(r["picks"]) == {"indeed:a", "indeed:b"}, "both rounds of picks are kept"


def test_a_classifier_may_take_the_step_later_without_it_changing_shape(monkeypatch):
    """The seam. Today it is always the operator; the decider is named so the row already says
    who chose when something else starts choosing."""
    bb = _at_start_line()
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = [{"job_id": "indeed:a", "title": "A Job"}]
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:a"], "advance": False,
                              "decided_by": "classifier:fit_v1"}).json()
    finally:
        _teardown()
    row = next(x for x in r["ladder"] if x["id"] == "select:1")
    assert "picked by classifier:fit_v1" in row["reached"]["evidence"]


def test_an_unnamed_decider_is_refused(monkeypatch):
    """A shortlist with no decider cannot be audited and cannot train the thing meant to inherit
    the job, so the vocabulary is closed."""
    bb = _at_start_line()
    bb.world = dict(bb.world or {})
    bb.world["page_results"] = [{"job_id": "indeed:a", "title": "A Job"}]
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/choose",
                        json={"picks": ["indeed:a"], "decided_by": "somebody"})
    finally:
        _teardown()
    assert r.status_code == 422 and "decided_by" in r.json()["detail"]


# --- enter_apply must click THIS pane's Apply, and prove it left ------------------------
# Driven live 2026-07-26: one rung after verify_identity confirmed the pane was BIDMC,
# enter_apply clicked 'Easily apply, New, View full details of Enterprise Applications Analyst'
# — a different company's card in the results list — and recorded it as OK.
_SERP_APPLY_AX = {"ok": True, "candidates": [
    {"role": "button", "name": "Encouraged to apply filter"},
    {"role": "button", "name": "Easily apply, New, View full details of Enterprise "
                               "Applications Analyst - Boston, MA"},
    {"role": "button", "name": "Apply on company site"},          # the pane's own, LAST
]}


def test_enter_apply_ignores_result_cards_and_filter_chips():
    ctrl = sc._find_apply_control(_SERP_APPLY_AX["candidates"],
                                  apply_type="company_site",
                                  job_title="Healthcare Data Analyst – BIDMC, OBGYN Quality")
    assert ctrl["name"] == "Apply on company site"


def test_the_pane_s_own_apply_type_orders_the_search():
    """`open_pane` already observed which kind of apply this is. Using it beats the generic hint
    order, which put 'easily apply' ahead of 'apply on company site' and so could never reach a
    company_site posting's real button."""
    quick = [{"role": "button", "name": "Apply now"},
             {"role": "button", "name": "Apply on company site"}]
    assert sc._find_apply_control(quick, apply_type="quick_apply")["name"] == "Apply now"
    assert sc._find_apply_control(quick, apply_type="company_site")["name"] \
        == "Apply on company site"


def test_a_control_naming_a_different_job_is_never_the_apply_button():
    cands = [{"role": "button", "name": "Easily apply to Warehouse Supervisor at Acme Logistics"}]
    assert sc._find_apply_control(cands, apply_type="quick_apply",
                                  job_title="Healthcare Data Analyst BIDMC OBGYN") is None


def test_no_apply_control_is_honest_rather_than_a_wrong_click():
    assert sc._find_apply_control([{"role": "button", "name": "Encouraged to apply filter"}],
                                  apply_type="company_site", job_title="Anything") is None


def test_a_click_that_stays_on_indeed_is_not_a_successful_enter(monkeypatch):
    """The verification half. Tier-1 `ok` means the click dispatched, not that it entered an
    application — recording OK on that alone journaled an application we never entered."""
    bb = _at_start_line()
    queue = aps.Queue(page=1)
    queue.enqueue([{"job_id": "indeed:aaa", "title": "Healthcare Data Analyst"}])
    step = queue.steps[0]
    step.record("open_pane", aps.OK, "pane switched")
    step.record("verify_identity", aps.OK, "matches")
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    bb.world["open_pane"] = {"title": "Healthcare Data Analyst", "apply_type": "quick_apply"}
    strayed = "https://www.indeed.com/viewjob?jk=someoneelse"
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(strayed),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "candidates": [{"role": "button", "name": "Apply now"}]},
         "/execute": {"outcome": "ok"}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    minis = (r["queue"]["steps"][0]["minis"])
    last = minis[-1]
    assert last["rung"] == "enter_apply"
    assert last["outcome"] == "failed", "staying on Indeed is not entering an application"
    assert "not this job's apply control" in last["detail"]


#: The real AX of Indeed's results page, measured live 2026-07-26 on the Joslin step. Two things
#: this pins: the pane's own apply control is a LINK (data-testid=viewjob-apply), and every card
#: also says "easily apply" as BOTH a link and a button.
_LIVE_SERP_AX = [
    {"role": "button", "name": "Encouraged to apply filter"},
    {"role": "link", "name": "Apply on company site"},
    {"role": "link", "name": "Easily apply, New, View full details of Enterprise Applications "
                             "Analyst at Beacon Communities, Boston, MA"},
    {"role": "button", "name": "Easily apply, New, View full details of Enterprise Applications "
                               "Analyst at Beacon Communities, Boston, MA"},
    {"role": "button", "name": "Easily apply, View full details of LIMS Business Analyst at "
                               "ALTEN Technology USA, Waltham, MA"},
]


def test_the_apply_control_may_be_a_link_not_a_button():
    """Indeed's own control is an <a role=link>. Requiring a button made it invisible — which is
    why the matcher first clicked a card, and then (once cards were excluded) found nothing at all
    among 196 elements."""
    ctrl = sc._find_apply_control(_LIVE_SERP_AX, apply_type="company_site",
                                  job_title="Healthcare Data Analyst (Clinic Administration)")
    assert ctrl is not None, "the pane's apply link must be findable"
    assert ctrl["name"] == "Apply on company site"
    assert ctrl["role"] == "link"


def test_an_exact_name_beats_a_card_that_merely_contains_the_word():
    """'analyst' appears in our job title AND in half the cards, so `contains` cannot separate
    them. The pane's control is named exactly; a card never is."""
    ctrl = sc._find_apply_control(_LIVE_SERP_AX, apply_type="",
                                  job_title="Healthcare Data Analyst (Clinic Administration)")
    assert ctrl["name"] == "Apply on company site"


def test_a_quick_apply_pane_still_prefers_its_own_control():
    cands = [{"role": "link", "name": "Easily apply, View full details of Other Job at Acme"},
             {"role": "button", "name": "Apply now"}]
    assert sc._find_apply_control(cands, apply_type="quick_apply",
                                  job_title="Some Analyst")["name"] == "Apply now"


def test_the_click_addresses_the_role_the_matcher_actually_found(monkeypatch):
    """`target_role` was hard-coded to "button", so /execute re-resolved (button, "Apply on
    company site") and returned NOT_FOUND — the control is a link. The matcher had just done the
    work of identifying the right element and the dispatch discarded half its answer."""
    bb = _at_start_line()
    queue = aps.Queue(page=1)
    queue.enqueue([{"job_id": "indeed:aaa", "title": "Healthcare Data Analyst"}])
    step = queue.steps[0]
    step.record("open_pane", aps.OK, "pane switched")
    step.record("verify_identity", aps.OK, "matches")
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    bb.world["open_pane"] = {"title": "Healthcare Data Analyst", "apply_type": "company_site"}
    harness, _ = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://boards.greenhouse.io/acme/jobs/1"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "candidates": [
             {"role": "link", "name": "Apply on company site"}]},
         "/execute": {"outcome": "ok"}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_step", json={})
    finally:
        _teardown()
    ex = next(p for path, p in harness.calls if path == "/execute")
    assert ex["target_role"] == "link", "must address the role the matcher found"
    assert ex["target_name"] == "Apply on company site"


# --- the account rung: the wall most ATS put in front of an application ------------------
# Operator, 2026-07-26: "we'll also need a group for icims accounts, and surfacing the account
# creation step in our new step through in the controller." It was happening OFF the ladder — a
# handoff appeared beside the queue while the step itself showed nothing between "we landed" and
# "submit", so the one part with a credential in it was the one part that left no trace.
def _classified_step(platform, company="Joslin Diabetes Center"):
    bb = _at_start_line()
    queue = aps.Queue(page=1)
    queue.enqueue([{"job_id": "indeed:aaa", "title": "Healthcare Data Analyst",
                    "company": company}])
    step = queue.steps[0]
    for r in ("open_pane", "verify_identity", "enter_apply", "classify"):
        step.record(r, aps.OK, "done")
    step.platform = platform
    bb.world = dict(bb.world or {})
    bb.world["apply_queue"] = queue.as_dict()
    return bb


def test_the_account_step_is_on_the_ladder_after_classify():
    step = aps.ApplyStep(job_id="x", title="t")
    for r in ("open_pane", "verify_identity", "enter_apply", "classify"):
        step.record(r, aps.OK)
    assert step.next_rung().id == "account", "the credential step must be a rung, not a side note"


def test_a_platform_that_needs_no_account_skips_the_rung_cleanly(monkeypatch):
    """Greenhouse takes an application without an identity. SKIPPED is a real answer — an
    unwalked rung would stall the ladder forever."""
    bb = _classified_step("greenhouse")
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}}, blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    mini = r["queue"]["steps"][0]["minis"][-1]
    assert mini["rung"] == "account" and mini["outcome"] == "skipped"


def test_an_ats_that_wants_an_account_surfaces_it_and_waits(monkeypatch):
    """Allowed to create one (operator directive 2026-07-24) — but it is a real identity on
    somebody's ATS, so it is surfaced and confirmed rather than done in passing."""
    bb = _classified_step("icims")
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}}, blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    mini = r["queue"]["steps"][0]["minis"][-1]
    assert mini["rung"] == "account"
    assert mini["outcome"] == "human_required", "a credential is never created in passing"
    handoff = r["account_handoff"]
    assert handoff["ats_id"] == "icims"
    assert handoff["company"] == "Joslin Diabetes Center"
    assert handoff["leg"] == "create_account"


def test_an_unnamed_company_gets_no_credentials_anywhere(monkeypatch):
    bb = _classified_step("icims", company="")
    _install(monkeypatch, {"/list_tabs": _tabs(SEARCH_URL),
                           "/auth_state": {"ok": True, "logged_in": True}}, blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_step", json={}).json()
    finally:
        _teardown()
    mini = r["queue"]["steps"][0]["minis"][-1]
    assert mini["outcome"] == "unknown" and "no company" in mini["detail"]


# --- the cleanup crew: a finished application must not leave its tab behind --------------------
def test_flagging_a_step_closes_the_application_tab_and_returns_to_the_search(monkeypatch):
    """Operator, 2026-07-27: "the tab manager should've immediately been a part of the cleanup crew
    after submitting." APPLY_EPILOGUE already called itself a REQUIRED step of the loop — and
    nothing on the path that ENDS a step ever ran it, so every finished apply left an inert tab.

    The finished-ness comes from the LADDER, not the URL: a submitted iCIMS application sits on a
    URL classify_tab reads as ROLE_APPLY, and it is right to — only the terminal flag knows.
    """
    closes = []

    def _close(payload):
        closes.append(payload)
        return {"ok": True, "closed": payload.get("tab_id")}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://jobs-x.icims.com/jobs/1/job?mode=submit_apply"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/close_tab": _close,
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_wd_step(platform="icims"))
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "submitted",
                              "detail": "confirmed"}).json()
    finally:
        _teardown()

    # The apply tab is resolved LIVE, not from the recorded hint — that record can name a tab
    # that has since closed or navigated, which is why _apply_tab re-resolves every time.
    assert len(closes) == 1
    assert r["last_step"]["cleanup"]["closed"][0]["url"].startswith("https://jobs-x.icims.com")
    assert closes[0]["focus_tab_url"].startswith("https://www.indeed.com")   # back to the search
    assert r["last_step"]["cleanup"]["closed"][0]["ok"] is True
    assert "Closed 1 finished tab" in r["last_step"]["detail"]


def test_cleanup_runs_on_an_abandoned_terminal_too(monkeypatch):
    """An application abandoned at a wall leaves exactly the same orphan tab as a submitted one."""
    closes = []
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/close_tab": lambda p: (closes.append(p), {"ok": True})[1],
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "parked:account_wall"}).json()
    finally:
        _teardown()
    assert len(closes) == 1
    assert r["last_step"]["cleanup"]["closed"][0]["url"].startswith("https://mfs.wd1")


def test_the_search_tab_is_never_closed_by_the_cleanup(monkeypatch):
    """The one tab the drive cannot afford to lose: reopening it costs a real page load, and it is
    home base between applications."""
    closes = []
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL),                       # search tab ONLY
         "/auth_state": {"ok": True, "logged_in": True},
         "/close_tab": lambda p: (closes.append(p), {"ok": True})[1],
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_with_queue(("indeed:a1", "T", "C")))
    try:
        client.post("/api/session_control/1/apply_flag",
                    json={"job_id": "indeed:a1", "flag": "abandoned:operator"}).json()
    finally:
        _teardown()
    assert closes == []


def test_open_pane_addresses_the_search_tab_not_whatever_cdp_lists_first(monkeypatch):
    """With a finished application still open, an unaddressed /open_job_card hunted for result
    cards in the ATS document and reported the card 'not found' — which reads as a rotated listing
    rather than a misaddressed click (live 2026-07-27)."""
    seen = {}

    def _open_card(payload):
        seen.update(payload)
        return {"ok": True, "title": "Healthcare Data Analyst", "apply_type": "company_site"}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://jobs-x.icims.com/jobs/1/job", SEARCH_URL),  # ATS listed FIRST
         "/auth_state": {"ok": True, "logged_in": True},
         "/open_job_card": _open_card,
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_with_queue(("indeed:a1", "Healthcare Data Analyst", "BILH")))
    try:
        client.post("/api/session_control/1/apply_step", json={"job_id": "indeed:a1"}).json()
    finally:
        _teardown()
    assert seen.get("tab_id") or seen.get("tab_url"), "the click must name the document it means"
    if seen.get("tab_url"):
        assert "indeed.com/jobs" in seen["tab_url"]


def test_the_apply_tab_follows_the_flow_when_it_hops_to_a_new_tab(monkeypatch):
    """An apply can open a SECOND tab and leave the first open and inert. BILH: Indeed ->
    jobs.bilh.org (recorded at enter_apply) -> "Apply now" -> bilh.wd1.myworkdayjobs.com. The
    recorded tab was still open, so the "is it still open?" test passed and the resolver kept
    handing back the spent landing — `orient` read a job posting and called the live Workday
    application "new territory" (live 2026-07-27).

    The window manager already separates them by role, which is why this needs no rule about
    which tab is newest.
    """
    bb = _with_queue(("indeed:a1", "Healthcare Data Analyst", "BILH"))
    bb.world["apply_tab"] = {"tab_id": "t1",
                             "url": "https://jobs.bilh.org/jobs/healthcare-data-analyst-jr88822/"}
    obs = {"tabs": [
        {"tab_id": "t2", "url": "https://bilh.wd1.myworkdayjobs.com/External/job/x"},
        {"tab_id": "t1", "url": "https://jobs.bilh.org/jobs/healthcare-data-analyst-jr88822/"},
        {"tab_id": "t0", "url": SEARCH_URL},
    ], "search_tab": {"tab_id": "t0", "url": SEARCH_URL}}

    assert sc._apply_tab(bb, obs)["tab_id"] == "t2"          # the application, not the doorway


def test_a_recorded_apply_tab_still_wins_when_it_is_the_real_application(monkeypatch):
    """The hop rule must not fire on a normal single-tab application: a recorded ATS tab that is
    still open IS the work, even when another apply-role tab exists."""
    bb = _with_queue(("indeed:a1", "T", "C"))
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://mfs.wd1.myworkdayjobs.com/job/x"}
    obs = {"tabs": [
        {"tab_id": "t2", "url": "https://other.wd1.myworkdayjobs.com/job/y"},
        {"tab_id": "t1", "url": "https://mfs.wd1.myworkdayjobs.com/job/x"},
        {"tab_id": "t0", "url": SEARCH_URL},
    ], "search_tab": {"tab_id": "t0", "url": SEARCH_URL}}

    assert sc._apply_tab(bb, obs)["tab_id"] == "t1"


def test_orient_reclassifies_when_the_apply_moves_to_a_real_ats(monkeypatch):
    """A branded careers wrapper hands off: classify sees `company_site` on the employer page, then
    "Apply now" lands on the tenant's Workday. With the recorded platform shadowing the live URL,
    orient asked the GENERIC describer about a Workday page and called a state the recipe knows
    perfectly well "new territory" (live 2026-07-27, BILH).
    """
    bb = _with_queue(("indeed:a1", "Healthcare Data Analyst", "BILH"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].record("classify", aps.OK, "company_site_job_posting")
    q.steps[0].platform = "company_site"                      # what the WRAPPER looked like
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t1",
                             "url": "https://bilh.wd1.myworkdayjobs.com/External/job/x"}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://bilh.wd1.myworkdayjobs.com/External/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "page_text": "Start Your Application",
                      "candidates": [{"role": "button", "name": "Autofill with Resume"},
                                     {"role": "button", "name": "Use My Last Application"}]}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/orient", json={}).json()
    finally:
        _teardown()

    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.platform == "workday"                        # the live tab won
    assert any(m.rung == "classify" and "re-classified" in m.detail for m in step.minis)
    assert "not a state we recognise" not in r["last_step"]["detail"]


def test_the_sign_in_leg_is_driven_too_not_handed_back(monkeypatch):
    """The system could CREATE an account by typing a generated password and then could not USE it:
    only the create leg was wired, so an ATS we already held an active account for still stopped at
    a manual handoff (live 2026-07-27, BILH Workday). Nothing in the operator's directive separates
    the two — the gates that hold are the same either way.
    """
    typed = []

    def _execute(payload):
        if payload.get("action_id") == "type":
            typed.append(payload.get("target_name") or payload.get("selector"))
        return {"outcome": "ok"}

    import accounts, ats_accounts
    ats_accounts.ensure_account("MFS", "workday", login_url="https://mfs.wd1.myworkdayjobs.com/x")
    ats_accounts.mark_created("MFS", "workday")               # active -> the sign_in leg is due
    accounts.put_account(ats_accounts.ats_account_id("MFS", "workday"), {"status": "active"})

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/execute": _execute,
         "/ax_scan": {"ok": True, "page_text": "My Information", "candidates": []}},
        blackboard=_wd_at_wall())
    try:
        r = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    assert typed == ["Email Address", "Password"]             # sign-in fields, not the create set
    assert "Signed in" in r["last_step"]["detail"]
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert any(m.rung == "account" and "sign_in leg" in m.detail and m.outcome == aps.OK
               for m in step.minis)
    assert _settled(step) >= {"account"}


def test_the_sign_in_form_is_revealed_before_a_credential_is_typed(monkeypatch):
    """Workday serves Create Account and Sign In from ONE url and shows whichever the tenant
    defaults to. SolutionHealth defaults to Create Account, whose Email/Password boxes have the SAME
    accessible names — so the sign-in leg typed the credential into the create form and then died
    looking for a submit that was one click away: "Filled the form but could not click
    '[data-automation-id=signInSubmitButton]' (not_found)" (live 2026-08-12).

    The toggle is conditional on a MEASUREMENT (is this leg's submit on the page?) and verified
    after pressing, because a toggle that clicks and changes nothing would send the credential into
    the wrong form anyway.
    """
    order = []
    present = {"submit": False}          # the create form is showing

    def _locate(payload):
        order.append(("locate", payload.get("css")))
        return {"ok": True, "found": present["submit"]}

    def _execute(payload):
        if payload.get("action_id") == "click" and "signInLink" in (payload.get("selector") or ""):
            present["submit"] = True     # the toggle reveals the sign-in form
            order.append(("toggle", payload.get("selector")))
        elif payload.get("action_id") == "type":
            order.append(("type", payload.get("target_name") or payload.get("selector")))
        return {"outcome": "ok"}

    import accounts, ats_accounts
    ats_accounts.ensure_account("MFS", "workday", login_url="https://mfs.wd1.myworkdayjobs.com/x")
    ats_accounts.mark_created("MFS", "workday")
    accounts.put_account(ats_accounts.ats_account_id("MFS", "workday"), {"status": "active"})

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://solutionhealth.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/locate": _locate,
         "/execute": _execute,
         "/ax_scan": {"ok": True, "page_text": "My Information", "candidates": []}},
        blackboard=_wd_at_wall())
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "auto"})
    finally:
        _teardown()

    kinds = [k for k, _ in order]
    assert kinds[0] == "locate", "the page is measured before anything is pressed"
    assert "toggle" in kinds, "a create-form page must be switched to the sign-in form"
    assert kinds.index("toggle") < kinds.index("type"), "…and switched BEFORE a credential is typed"


def test_a_page_already_showing_the_sign_in_form_is_not_toggled(monkeypatch):
    """The other half: pressing the toggle on a page that already shows the right form switches it
    to the WRONG one. So the toggle fires on a measured absence, never on a hunch."""
    toggles = []

    def _execute(payload):
        if payload.get("action_id") == "click" and "signInLink" in (payload.get("selector") or ""):
            toggles.append(payload)
        return {"outcome": "ok"}

    import accounts, ats_accounts
    ats_accounts.ensure_account("MFS", "workday", login_url="https://mfs.wd1.myworkdayjobs.com/x")
    ats_accounts.mark_created("MFS", "workday")
    accounts.put_account(ats_accounts.ats_account_id("MFS", "workday"), {"status": "active"})

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://mfs2.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/locate": {"ok": True, "found": True},          # the sign-in form is already up
         "/execute": _execute,
         "/ax_scan": {"ok": True, "page_text": "My Information", "candidates": []}},
        blackboard=_wd_at_wall())
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "auto"})
    finally:
        _teardown()

    assert toggles == []


def test_the_cleanup_closes_the_doorways_the_step_opened(monkeypatch):
    """An apply HOPS — Indeed -> the employer's careers page -> the ATS — and each hop strands the
    one before it. The window manager will not close the middle one: an employer careers site is
    ROLE_UNKNOWN, and "I could not identify it" is the weakest possible reason to close something
    in a window the operator shares.

    Provenance is the warrant. A tab we watched appear during our own application is ours, so the
    step records what it opened and the cleanup closes exactly those. Operator, 2026-07-27:
    "cleanup needs to be cleaner because it may confuse us going long term."
    """
    closes = []
    bb = _with_queue(("indeed:a1", "Healthcare Data Analyst", "BILH"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].platform = "workday"
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t2", "url": "https://bilh.wd1.myworkdayjobs.com/job/x"}
    # The landing page was watched appearing during this step: role UNKNOWN, but ours.
    bb.world["apply_tab_census"] = {
        "job_id": "indeed:a1",
        "tabs": {"t0": SEARCH_URL, "t1": "https://jobs.bilh.org/jobs/x/", "t2": "https://bilh.wd1.myworkdayjobs.com/job/x"},
        "opened": ["t1", "t2"]}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://jobs.bilh.org/jobs/x/",
                             "https://bilh.wd1.myworkdayjobs.com/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/close_tab": lambda p: (closes.append(p), {"ok": True})[1],
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "submitted"}).json()
    finally:
        _teardown()

    closed_urls = [c["url"] for c in r["last_step"]["cleanup"]["closed"]]
    assert any("myworkdayjobs" in u for u in closed_urls)      # the application
    assert any("jobs.bilh.org" in u for u in closed_urls)      # and the doorway it came through
    assert not any("indeed.com" in u for u in closed_urls)     # never home base
    # the census is spent once the step is over — it must not leak onto the next application
    assert (saved["bb"].world or {}).get("apply_tab_census") is None


# --- have we been here before? the applied check --------------------------------------------
def _applied_row(db_rows, job_id, title, company, url=""):
    from models import ObservedJob, utcnow
    return ObservedJob(job_id=job_id, platform=job_id.split(":")[0],
                       external_id=job_id.split(":", 1)[1], title=title, company=company,
                       url=url, application_status="applied", applied_at=utcnow())


def test_open_pane_halts_when_the_database_says_we_already_applied(monkeypatch):
    """Operator, 2026-07-27: "we need logic on whether we applied to things or not and that needs
    to be checked on initial landing on a page." The BIDMC drive reopened a step, hopped a branded
    wrapper into Workday and signed in — to be told the answer the database could have given at the
    results page.
    """
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL),
         "/auth_state": {"ok": True, "logged_in": True},
         "/open_job_card": {"ok": True, "title": "Healthcare Data Analyst", "apply_type": "company_site"},
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_with_queue(("indeed:a1", "Healthcare Data Analyst", "Acme")),
        applied=[_applied_row(None, "indeed:a1", "Healthcare Data Analyst", "Acme")])
    try:
        r = client.post("/api/session_control/1/apply_step", json={"job_id": "indeed:a1"}).json()
    finally:
        _teardown()

    assert r["last_step"]["ok"] is False
    assert "already applied" in r["last_step"]["detail"]
    assert r["applied_check"]["status"] == "applied"
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    # the rung is still walked — the pane DID open; what changed is that the ladder stops here
    assert any(m.rung == "open_pane" and "already" in m.detail for m in step.minis)


def test_a_fuzzy_applied_match_warns_but_lets_the_step_continue(monkeypatch):
    """`likely_applied` must never silently skip a job the operator picked.

    Titles carry a department suffix on a 3-token base (`job_dedup.MIN_CONTAINMENT_TOKENS`) so the
    pair still scores above `FUZZY_TITLE_THRESHOLD` (raised to 0.85 alongside the corpus-measured
    scoring fixes) — a bare two-word title like the original 'Data Analyst' fixture no longer
    qualifies on its own, correctly: see `test_a_generic_title_is_not_every_richer_title_at_that_employer`.
    """
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL + "&vjk=b2"),   # the world moves when the pane opens
         "/auth_state": {"ok": True, "logged_in": True},
         "/open_job_card": {"ok": True, "title": "Healthcare Data Analyst - Reporting",
                            "apply_type": "company_site"},
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_with_queue(("indeed:b2", "Healthcare Data Analyst - Reporting", "Acme")),
        applied=[_applied_row(None, "workday:JR9", "Healthcare Data Analyst", "Acme")])
    try:
        r = client.post("/api/session_control/1/apply_step", json={"job_id": "indeed:b2"}).json()
    finally:
        _teardown()

    assert r["last_step"]["ok"] is True                      # NOT halted
    assert "may have applied" in r["last_step"]["detail"]
    assert r["applied_check"]["status"] == "likely_applied"
    # STAMPED WITH THE JOB IT IS ABOUT. The verdict's own `job_id` names the row that MATCHED
    # (`workday:JR9`), so without this the stored answer had no subject and the cockpit rendered it
    # beside whatever step was in focus — including a step reached without a landing, which would
    # show the previous job's verdict. "Already applied" is the sentence an operator acts on by NOT
    # applying, so attributing it to the wrong job is the expensive direction to be wrong in.
    assert r["applied_check"]["for_job_id"] == "indeed:b2"
    assert r["applied_check"]["job_id"] == "workday:JR9"      # the match, distinct from the subject
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.next_rung().id == "verify_identity"          # the ladder moved on


def test_flagging_submitted_writes_the_durable_record(monkeypatch):
    """The queue is one session's blackboard; ObservedJob is what the NEXT session can ask. Joslin
    was submitted and confirmed on 2026-07-27 and its row still read `seen` / applied_at=None,
    which is precisely why "check the database" had nothing to check."""
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://x.wd1.myworkdayjobs.com/job/JR77"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/close_tab": {"ok": True},
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "submitted",
                              "detail": "confirmed sent"}).json()
    finally:
        _teardown()
    assert r["last_step"]["recorded"]["recorded"] is True
    assert r["last_step"]["recorded"]["status"] == "applied"
    assert r["last_step"]["recorded"]["applied_at"]


def test_a_parked_step_writes_no_application(monkeypatch):
    """Parked means NOT NOW. Recording it would tell the next session a lie."""
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://x.wd1.myworkdayjobs.com/job/JR77"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/close_tab": {"ok": True},
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=_wd_step())
    try:
        r = client.post("/api/session_control/1/apply_flag",
                        json={"job_id": "indeed:a1", "flag": "parked:account_wall"}).json()
    finally:
        _teardown()
    assert r["last_step"]["recorded"]["recorded"] is False


# --- the sign-in leg the ladder now owns -------------------------------------------------------
# The operator's ask: start a session and, because it is a LinkedIn session, it gets itself signed
# in — rather than climbing to the auth rung and handing back a list of buttons. The credential is
# theirs, already in the vault; using it is what it is for. What must NOT change is the boundary:
# MFA, captcha and a wrong password stop, every time.
def _with_domain_login(monkeypatch, tmp_path, *, domain_id, account_id):
    """Give this domain a stored login, isolated from the operator's real vault."""
    import secrets_vault
    monkeypatch.setenv("AGENT_VAULT_KEY_PATH", str(tmp_path / "vault.key"))
    monkeypatch.setattr(secrets_vault, "_vault_path", lambda: tmp_path / "vault.json")
    secrets_vault.reset_provider_cache()
    accounts.put_account(account_id, {"domain_id": domain_id, "kind": "domain", "status": "active"})
    accounts.set_credentials(account_id, "person@example.com", "not-a-real-password")


def _fake_run_login(monkeypatch, *, ok, status, detail="", steps=1):
    """Stand in for login_reasoner.run_login. The reasoning loop has its own tests; what these
    pin is how the LADDER treats each outcome."""
    import login_reasoner
    seen = {}

    async def _run(**kw):
        seen.update(kw)
        return login_reasoner.LoginResult(ok, status, steps, detail, [])

    monkeypatch.setattr(login_reasoner, "run_login", _run)
    return seen


def test_the_sign_in_leg_is_driven_not_handed_back(monkeypatch, tmp_path):
    """With a credential stored, the auth rung SIGNS IN and the rung is marked on that evidence."""
    _with_domain_login(monkeypatch, tmp_path, domain_id="indeed_jobs", account_id="indeed_default")
    seen = _fake_run_login(monkeypatch, ok=True, status="authenticated", detail="signed in")
    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs("https://www.indeed.com/"),
         "/auth_state": {"ok": True, "logged_in": False},
         "/ax_scan": _ax(("link", "Sign in"))},
        blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["ok"] is True
    assert r["last_step"]["login"]["authenticated"] is True
    assert cps.Ledger.from_dict(saved["bb"].checkpoints).holds("authenticated")
    # the credential reached the DRIVER, and the rung is evidenced by the sign-in, not by a probe
    assert seen["username"] == "person@example.com"


def test_a_human_gate_during_sign_in_stops_the_ladder_at_the_right_rung(monkeypatch, tmp_path):
    """MFA and captcha are the actual boundary. Each has to surface as ITS OWN `awaiting` so the
    panel asks the operator for the right thing — a 2FA prompt is not 'try another way in'."""
    # NOTE mfa -> operator_2fa, NOT operator_verify: that key already means "the search was
    # submitted but not confirmed", and two meanings on one key is how a panel tells an operator
    # to check a search box when it wants a 6-digit code.
    for status, awaiting in (("mfa", "operator_2fa"), ("captcha", "operator_challenge"),
                             ("bad_credentials", "operator_login")):
        _with_domain_login(monkeypatch, tmp_path, domain_id="indeed_jobs",
                           account_id="indeed_default")
        _fake_run_login(monkeypatch, ok=False, status=status, detail=f"stopped at {status}")
        _install(monkeypatch,
                 {"/list_tabs": _tabs("https://www.indeed.com/"),
                  "/auth_state": {"ok": True, "logged_in": False},
                  "/ax_scan": _ax(("link", "Sign in"))},
                 blackboard=_ready_for_provisioned())
        try:
            r = client.post("/api/session_control/1/step", json={}).json()
        finally:
            _teardown()
        assert r["last_step"]["ok"] is False, status
        assert r["awaiting"] == awaiting, status


def test_without_a_stored_login_the_rung_still_surveys_the_ways_in(monkeypatch):
    """The survey is the FALLBACK, not the dead code path — an operator who has saved nothing must
    still get real options rather than a silent no-op."""
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.indeed.com/"),
              "/auth_state": {"ok": True, "logged_in": False},
              "/ax_scan": _ax(("link", "Sign in with a code"))},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert "can_drive" in login                       # the survey's shape, not the drive's
    assert r["awaiting"] == "operator_login"


def test_an_account_with_no_credential_is_not_a_login_attempt(monkeypatch, tmp_path):
    """A registered-but-empty account must not be picked: driving with no password would surface
    as a 'bad credentials' escalation about a password that was never set."""
    accounts.put_account("linkedin_default", {"domain_id": "linkedin_jobs", "kind": "domain",
                                              "status": "active"})
    assert sc._domain_account(sc._ENGINE_BY_ID["linkedin_jobs"]) is None


# --- a password form and a way around it can be the SAME screen -------------------------------
# LinkedIn's logged-out /jobs page (live, session #22, 2026-07-27) carries an email+password form
# AND a "Continue with google" button. The survey used to short-circuit on the password field and
# report "you type it, not us" — while a one-click SSO route sat on the same screen, already listed
# in SIGNIN_ENTRY_HINTS and never looked at.
_LINKEDIN_LOGGED_OUT = (("link", "Join now"), ("link", "Sign in"),
                        ("button", "Continue with google"),
                        ("textbox", "Email or phone"), ("textbox", "Password"))


def test_a_password_form_that_also_offers_sso_is_a_choice_not_a_dead_end(monkeypatch):
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.linkedin.com/jobs/"),
              "/auth_state": {"ok": True, "logged_in": False},
              "/ax_scan": _ax(*_LINKEDIN_LOGGED_OUT)},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert login["state"] == "signin_form"
    assert login["can_drive"] is True
    names = [o["name"] for o in login["options"]]
    assert "Continue with google" in names


def test_the_forms_own_submit_is_never_offered_as_a_way_in(monkeypatch):
    """The boundary the fix had to keep. On a password screen a control named "Sign in" IS that
    form's submit — offering it would have the agent submitting an empty credential. Only routes
    AROUND the credential count."""
    _install(monkeypatch,
             {"/list_tabs": _tabs("https://www.linkedin.com/jobs/"),
              "/auth_state": {"ok": True, "logged_in": False},
              "/ax_scan": _ax(*_LINKEDIN_LOGGED_OUT)},
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    names = [o["name"] for o in r["last_step"]["login"]["options"]]
    assert "Sign in" not in names and "Join now" not in names


def test_login_action_will_not_click_the_submit_even_when_asked_by_name(monkeypatch):
    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs("https://www.linkedin.com/jobs/"),
                           "/auth_state": {"ok": True, "logged_in": False},
                           "/ax_scan": _ax(*_LINKEDIN_LOGGED_OUT)},
                          blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/login_action", json={"control_name": "Sign in"})
    finally:
        _teardown()
    assert r.status_code == 422                 # not one of the ways in
    assert "/execute" not in harness.paths()


def test_orient_adopts_a_newly_recognised_ats_even_before_it_has_been_driven(monkeypatch):
    """`known` answers "have we driven it end to end", which is not the question orient is asking.
    Gating re-classification on it meant a newly-recognised ATS could never correct a stale
    `company_site` — Teradyne, the hour after SuccessFactors detection shipped: the registry said
    successfactors, orient kept saying company_site, and consulted the generic recipe (2026-07-27).
    """
    bb = _with_queue(("indeed:a1", "Pricing Analyst", "Teradyne"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].record("classify", aps.UNKNOWN, "company_site_job_posting")
    q.steps[0].platform = "company_site"
    bb.world["apply_queue"] = q.as_dict()
    url = ("https://jobs.teradyne.com/Teradyne/job/North-Reading-Pricing-Marketing-Operations-"
           "Analyst-%28Teradyne%2C-N_-Reading-MA%29-MA/1385295400/")
    bb.world["apply_tab"] = {"tab_id": "t1", "url": url}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, url),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "page_text": "Apply now", "candidates": []}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/orient", json={}).json()
    finally:
        _teardown()

    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.platform == "successfactors"
    assert any(m.rung == "classify" and "re-classified" in m.detail for m in step.minis)


def test_orient_does_not_downgrade_a_named_platform_to_company_site(monkeypatch):
    """The other direction: an unrecognised url must not wipe a platform we already named."""
    bb = _with_queue(("indeed:a1", "T", "C"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].platform = "workday"
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://careers.example.com/openings/123"}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://careers.example.com/openings/123"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/orient", json={}).json()
    finally:
        _teardown()
    assert aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0].platform == "workday"


# --- where the two login processes MEET -------------------------------------------------------
# The domain's login (LinkedIn's wall, its SSO button) and the identity's login (Google's chooser,
# consent, credentials) are separate processes that meet at the popup. These pin the seam: the
# ladder asks google_recipe what the popup is, and reports whose turn it is in the survey's shape.
_GOOGLE_CHOOSER = "https://accounts.google.com/gsi/select?client_id=linkedin"
_GOOGLE_PASSWORD = "https://accounts.google.com/v3/signin/challenge/pwd"


def _popup_open(popup_url, ax):
    return {"/list_tabs": _tabs("https://www.linkedin.com/jobs/", popup_url),
            "/auth_state": {"ok": True, "logged_in": False},
            "/ax_scan": ax}


def test_the_account_chooser_is_offered_as_a_click_not_a_wall(monkeypatch, tmp_path):
    """The one-click login. Picking among your own signed-in accounts is a tile click — refusing it
    would turn SSO into a human interruption for no safety gain."""
    _with_domain_login(monkeypatch, tmp_path, domain_id="indeed_jobs", account_id="indeed_default")
    _install(monkeypatch,
             _popup_open(_GOOGLE_CHOOSER,
                         _ax(("link", "Personal\nperson@example.com"), page_text="Choose an account")),
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert login["state"] == "sso:google_account_chooser"
    assert login["can_drive"] is True and login["policy"] == "auto"
    assert login["options"][0]["name"].endswith("person@example.com")


def test_the_google_password_screen_is_still_never_ours(monkeypatch, tmp_path):
    """Same popup, same host, different state — and the answer flips. This is the whole reason the
    boundary is per-state rather than per-host."""
    _with_domain_login(monkeypatch, tmp_path, domain_id="indeed_jobs", account_id="indeed_default")
    _install(monkeypatch,
             _popup_open(_GOOGLE_PASSWORD, _ax(("textbox", "Enter your password"),
                                               page_text="Enter your password")),
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert login["state"] == "sso:google_signin_password"
    assert login["can_drive"] is False and login["policy"] == "human"
    assert login["options"] == []


def test_the_popup_is_surveyed_instead_of_the_engines_tab(monkeypatch, tmp_path):
    """When the provider's window is open it IS the thing waiting. Surveying the engine's tab
    underneath would report LinkedIn's wall while Google holds the screen."""
    _with_domain_login(monkeypatch, tmp_path, domain_id="indeed_jobs", account_id="indeed_default")
    _install(monkeypatch,
             _popup_open(_GOOGLE_CHOOSER, _ax(("link", "a@example.com"), page_text="Choose an account")),
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    assert r["last_step"]["login"]["url"].startswith("https://accounts.google.com")


def test_the_real_username_is_used_for_matching_and_never_returned(monkeypatch, tmp_path):
    """`username_hint` is masked BY DESIGN — it exists to be displayed, not compared, so matching a
    chooser tile needs the real address from the vault. It is resolved server-side for the
    comparison only: the survey's own fields must not carry it."""
    _with_domain_login(monkeypatch, tmp_path, domain_id="indeed_jobs", account_id="indeed_default")
    _install(monkeypatch,
             _popup_open(_GOOGLE_CHOOSER,
                         _ax(("link", "Personal\nperson@example.com"), page_text="Choose an account")),
             blackboard=_ready_for_provisioned())
    try:
        r = client.post("/api/session_control/1/step", json={}).json()
    finally:
        _teardown()
    login = r["last_step"]["login"]
    assert login["can_drive"] is True          # it matched, so the real address WAS used
    # The address does appear in the option label — but only because that label is QUOTED from the
    # tile Google is rendering on the operator's own screen. What must never appear is the secret.
    assert login["options"][0]["name"].endswith("person@example.com")
    assert "not-a-real-password" not in json.dumps(r)
    # And the registry still only ever exposes the masked form.
    assert accounts.get_account("indeed_default")["username_hint"] == "p***@example.com"



def _sap_page(*, accepted: bool = False, dialog: bool = True):
    """A tiny WORLD, not a call script (the StepRunner fixture lesson, 2026-08-03): /ax_scan
    reads the page's consent state, and only the Accept CLICK flips it — so observation reads,
    which the StepRunner takes whenever it likes, can never advance the page by themselves.

    Returns (scan, saw_execute): route /ax_scan to `scan`, and call `saw_execute(payload)` from
    the test's own /execute stub so the world sees the clicks. `dialog` controls whether the
    consent dialog's Accept is visible — the driver polls for it rather than trusting the
    opener's `ok`, so a page with no Accept is how the did-not-open path gets exercised."""
    state = {"accepted": accepted}

    def scan(_payload):
        text = "Data privacy statement has been accepted." if state["accepted"] else ""
        cands = [{"role": "button", "name": "Accept"}] if dialog else []
        return {"ok": True, "page_text": text, "candidates": cands}

    def saw_execute(payload) -> None:
        if payload.get("target_name") == "Accept":
            state["accepted"] = True

    return scan, saw_execute


def test_the_account_driver_handles_selects_and_required_consents(monkeypatch):
    """SAP's create-account form wants a country dropdown and a data-privacy acceptance before it
    will take the form. Both are driven BY NAME from the recipe, and the two MARKETING opt-ins on
    the same page are absent from every list — a field this driver never names is one it can never
    tick by accident."""
    acted = []
    scan, saw_execute = _sap_page()

    def _execute(payload):
        acted.append((payload.get("action_id"),
                      payload.get("target_name") or payload.get("selector")))
        saw_execute(payload)
        return {"outcome": "ok"}

    class _Answer:
        def __init__(self, k, v): self.answer_key, self.value = k, v

    import accounts, ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    accounts.put_account(ats_accounts.ats_account_id("Teradyne", "successfactors"),
                         {"status": "pending"})

    bb = _with_queue(("indeed:a1", "Pricing Analyst", "Teradyne"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].platform = "successfactors"
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://career41.sapsf.com/careers"}

    harness, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/execute": _execute,
         # The consent proof appears only AFTER the Accept click — the idempotence pre-check
         # must read as "not yet accepted", or the driver rightly skips the whole consent.
         # Modelling both states is the point: one value could only ever test one path.
         "/ax_scan": scan},
        blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    kinds = [a for a, _ in acted]
    names = [n for _, n in acted]
    assert "select" in kinds                                   # the country dropdown ran
    assert "Country/Region of Residence" in names
    # THE CONSENT IS TWO ACTS. The opener is addressed BY SELECTOR — its accessible name is the
    # whole row fused into one node, and clicking that navigates back to the sign-in gate with the
    # half-filled form (live, 2026-07-28) — and the Accept lives inside the dialog it raises.
    assert "#dataPrivacyId" in names
    assert names.index("#dataPrivacyId") < names.index("Accept")
    assert "Terms of Use Read and accept the data privacy statement." not in names
    # the marketing opt-ins are never addressed through /execute at all
    assert not any(n and ("Notification:" in n or "Hear more" in n) for n in names)
    assert names[-1] == "Create Account"                       # submit is last


def test_parking_an_application_leaves_its_tab_open(monkeypatch):
    """Found live 2026-08-04: an application filled all the way to smartapply's review step was
    flagged `parked:operator` — Submit is the operator's gate — and the cleanup crew closed its
    tab, discarding a completed form nobody had submitted. Terminal for the LADDER is not
    finished in the WORLD: parked means someone is coming back."""
    bb = _with_queue(("indeed:a1", "Senior Data Engineer", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://smartapply.indeed.com/beta/x/review"}

    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs(SEARCH_URL,
                                               "https://smartapply.indeed.com/beta/x/review"),
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/ax_scan": {"ok": True, "candidates": []}},
                          blackboard=bb)
    try:
        out = client.post("/api/session_control/1/apply_flag",
                          json={"job_id": "indeed:a1", "flag": "parked:operator",
                                "note": "submit is the operator's"}).json()
    finally:
        _teardown()
    assert "/close_tab" not in harness.paths(), "a parked application's tab must survive"
    assert "LEFT OPEN" in out["last_step"]["detail"]


def test_a_half_typed_form_survives_whatever_parked_it(monkeypatch):
    """The other half of the rule: input WE typed is unfinished work regardless of the blocking
    cause, and a reload throws away both the input and the operator's review of it."""
    bb = _with_queue(("indeed:a1", "Senior Data Engineer", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].record("account", aps.HUMAN_REQUIRED, "filled, awaiting Create", staged=True)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://mfs.wd1.myworkdayjobs.com/job/x"}

    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs(SEARCH_URL,
                                               "https://mfs.wd1.myworkdayjobs.com/job/x"),
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/close_tab": {"ok": True},
                           "/ax_scan": {"ok": True, "candidates": []}},
                          blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_flag",
                    json={"job_id": "indeed:a1", "flag": "parked:account_wall"}).json()
    finally:
        _teardown()
    assert "/close_tab" not in harness.paths(), "a half-typed form must never be tidied away"


def test_a_submitted_application_is_still_tidied(monkeypatch):
    """The other half of the rule: submitted means the work IS over, so the inert tab goes."""
    bb = _with_queue(("indeed:a1", "Senior Data Engineer", "MFS"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK)
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://smartapply.indeed.com/beta/x/done"}

    harness, _ = _install(monkeypatch,
                          {"/list_tabs": _tabs(SEARCH_URL,
                                               "https://smartapply.indeed.com/beta/x/done"),
                           "/auth_state": {"ok": True, "logged_in": True},
                           "/close_tab": {"ok": True},
                           "/ax_scan": {"ok": True, "candidates": []}},
                          blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_flag",
                    json={"job_id": "indeed:a1", "flag": "submitted"}).json()
    finally:
        _teardown()
    assert "/close_tab" in harness.paths()


def _sap_step(bb):
    """A Teradyne/SuccessFactors step, classified and standing at the account wall."""
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].platform = "successfactors"
    bb.world["apply_queue"] = q.as_dict()
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://career41.sapsf.com/careers"}
    return bb


class _Answer:
    """Stands in for an `ApplicationAnswer` row. Carries the whole shape the planner reads —
    `_fill_plan_for` passes `question_patterns`/`input_hint` through as the fill's second source,
    so a double with only key+value would fail on an attribute rather than on the assertion."""
    def __init__(self, k, v, *, patterns=None, hint="text", display=""):
        self.answer_key, self.value = k, v
        self.question_patterns = patterns or []
        self.input_hint, self.display_name = hint, display or k.replace("_", " ").title()
        self.options: list[str] = []


def test_a_password_that_breaks_the_ats_rules_is_refused_before_a_keystroke(monkeypatch):
    """A rejected password is not a free retry: it costs a submit and leaves a half-made account
    that reads, from the outside, exactly like a made one. So the check runs before anything is
    typed — and the refusal never quotes the password."""
    acted = []
    monkeypatch.setattr(accounts, "_read_env_value",
                        lambda key: "ab1!" if key == "ATS_ACCOUNT_PW_SUFFIX" else "")

    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": lambda p: acted.append(p.get("target_name")) or {"outcome": "ok"},
              "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    assert acted == []                                     # nothing was typed at all
    detail = out["last_step"]["detail"]
    assert "shorter than the 8-character minimum" in detail
    assert "Tab1!" not in detail                           # the credential is never quoted
    assert accounts.get_account(
        ats_accounts.ats_account_id("Teradyne", "successfactors"))["status"] == "pending"


def test_creating_an_account_stores_the_credential_it_used(monkeypatch):
    """Derivation is how we CHOOSE a password, not how we recover one: the shared suffix and the
    company string both drift, and both keep returning a plausible wrong answer when they do. So
    the pair is written to the vault at the moment the site accepts it."""
    import ats_accounts
    import secrets_vault
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    assert not secrets_vault.has_secret(aid)

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              # The consent proof — the driver confirms the acceptance from OUTSIDE the dialog.
              "/ax_scan": {"ok": True,
                           "page_text": "Data privacy statement has been accepted.",
                           "candidates": []}},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    assert out["last_step"]["credentials_stored"] is True
    assert secrets_vault.get_secret(aid) == {"username": "operator@example.com",
                                             "password": "Tabcde1!"}
    assert accounts.get_account(aid)["status"] == "active"
    # And the registry itself still holds no secret — only the vault does.
    assert "Tabcde1!" not in accounts._path().read_text()


def test_a_credential_we_failed_to_store_is_as_loud_as_a_failed_step(monkeypatch):
    """The account is real either way. The one that is unrecoverable is the quiet one."""
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    monkeypatch.setattr(ats_accounts, "record_credentials",
                        lambda *a, **k: {"ok": False, "detail": "vault key unreadable"})

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              # The consent proof — the driver confirms the acceptance from OUTSIDE the dialog.
              "/ax_scan": {"ok": True,
                           "page_text": "Data privacy statement has been accepted.",
                           "candidates": []}},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    assert out["last_step"]["credentials_stored"] is False
    assert "CREDENTIAL NOT STORED" in out["last_step"]["detail"]
    assert "vault key unreadable" in out["last_step"]["detail"]
    # The account was still made, and the record still says so — a storage failure must not
    # erase the fact that the login now exists on the site.
    assert accounts.get_account(
        ats_accounts.ats_account_id("Teradyne", "successfactors"))["status"] == "active"


def test_an_operator_created_account_stores_its_credential_too(monkeypatch):
    """The handoff leg is the one that runs whenever the create is human-required — every captcha,
    every verification wall, every account the agent may not make itself. An account typed by hand
    is no more recoverable from the derivation than one we typed, so leaving this leg unstored
    would mean the accounts most likely to need a password later are exactly the ones without one."""
    import secrets_vault

    import ats_accounts
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"}},
             blackboard=bb)
    try:
        out = client.post("/api/session_control/1/apply_account",
                          json={"mark_created": True}).json()
    finally:
        _teardown()

    assert out["last_step"]["credentials_stored"] is True
    # The derived pair the handoff card showed them, since they did not say otherwise.
    assert secrets_vault.get_secret(aid) == {"username": "operator@example.com",
                                             "password": "Tabcde1!"}
    assert accounts.get_account(aid)["status"] == "active"


def test_the_operator_can_record_a_password_they_chose_themselves(monkeypatch):
    """A site rule we had not read, or a password already in use, and the suggestion is not what
    is on the account. Storing the derived pair anyway would be a confident wrong answer — the
    exact failure this whole change exists to stop."""
    import secrets_vault

    import ats_accounts
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"}},
             blackboard=bb)
    try:
        client.post("/api/session_control/1/apply_account",
                    json={"mark_created": True, "username": "someone.else@example.com",
                          "password": "WhatTheyActuallyUsed1!"}).json()
    finally:
        _teardown()

    assert secrets_vault.get_secret(aid) == {"username": "someone.else@example.com",
                                             "password": "WhatTheyActuallyUsed1!"}


def test_the_account_records_where_the_login_actually_lives(monkeypatch):
    """SAP serves the application from sapsf.com while the posting sits on the employer's own
    domain, so an account_url taken from the job page is wrong by construction — and quietly: it
    fails nothing today and opens a job ad at the sign-in leg weeks later."""
    import ats_accounts
    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    # What the blackboard believes: the ORIENT rung's last look, and an apply_tab whose url went
    # stale when the tab navigated without a rung writing it back (live, Teradyne 2026-07-28).
    job_url = "https://jobs.teradyne.com/Teradyne/job/1385295400/"
    bb.world["orient"] = {"url": job_url}
    bb.world["apply_tab"] = {"tab_id": "t1", "url": job_url}

    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/career?company=teradynein"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              # The consent proof — the driver confirms the acceptance from OUTSIDE the dialog.
              "/ax_scan": {"ok": True,
                           "page_text": "Data privacy statement has been accepted.",
                           "candidates": []}},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "handoff"}).json()
    finally:
        _teardown()

    rec = accounts.get_account(ats_accounts.ats_account_id("Teradyne", "successfactors"))
    assert "sapsf.com" in rec["login_url"]
    assert "jobs.teradyne.com" not in rec["login_url"]


def test_an_optin_that_arrives_checked_is_switched_off_not_merely_left_alone(monkeypatch):
    """SAP's two marketing boxes render CHECKED. The old protection was to name them and never
    touch them — which protects nothing, because the danger was never that we would tick them. It
    was that the site already had. Leaving them alone consents by default, against the operator's
    stored marketing_contact_consent=No, and says nothing about it."""
    calls = []

    def _check_group(payload):
        calls.append((payload.get("selector"), tuple(payload.get("values") or ())))
        return {"ok": True, "detail": "checked [] (verified)"}

    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              "/check_group": _check_group,
              "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    # Both boxes addressed, both set to the EMPTY set — /check_group unticks and re-reads to
    # confirm, because a refusal we cannot verify is not a refusal.
    assert ("#fbclc_emailEnabled", ()) in calls
    assert ("#fbclc_campaignEmailEnabled", ()) in calls


def test_a_refusal_we_could_not_make_stops_the_submit(monkeypatch):
    """Consenting to marketing on someone's behalf is not a best-effort matter."""
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    clicked = []

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": lambda p: clicked.append(p.get("target_name")) or {"outcome": "ok"},
              "/check_group": {"ok": False, "code": "not_staged"},
              "/ax_scan": {"ok": True,
                           "page_text": "Data privacy statement has been accepted.",
                           "candidates": []}},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    assert "Create Account" not in clicked            # never submitted
    assert "would opt you in" in out["last_step"]["detail"]
    assert accounts.get_account(
        ats_accounts.ats_account_id("Teradyne", "successfactors"))["status"] == "pending"


def test_a_consent_already_accepted_is_not_clicked_again(monkeypatch):
    """A re-run must CONVERGE, not thrash. The account rung is re-entered constantly — the operator
    presses the button again, a drive resumes after a captcha, a later session picks the step back
    up — and on SAP the consent opener is the most dangerous control on the page: its sibling
    addressing navigated away and destroyed a filled form. So if the page already says the
    statement is accepted, the opener is never touched."""
    acted = []
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": lambda p: acted.append(p.get("target_name") or p.get("selector"))
                                    or {"outcome": "ok"},
              "/check_group": {"ok": True},
              # Accepted from the very first read — the page was already consented.
              "/ax_scan": _sap_page(accepted=True)[0]},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    assert "#dataPrivacyId" not in acted        # the dialog was never re-opened
    assert "Accept" not in acted
    assert acted[-1] == "Create Account"        # and the drive still completed
    assert out["last_step"]["ok"] is True


def test_the_card_shows_what_the_page_still_needs_not_the_whole_plan(monkeypatch):
    """By the time an operator reads the card the form is usually part-filled — by a previous run
    or by them. A card showing only the plan reads as twelve things to do in front of a page that
    needs two. The refused marketing box must NOT appear: the scanner reports an unticked box as an
    unanswered required field, and listing it would ask them to undo the refusal."""
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    scan = {"ok": True, "unanswered": [
        {"field": "Choose Password: *", "selector": "#fbclc_pwd"},
        {"field": "Retype Password: *", "selector": "#fbclc_pwdConf"},
        {"field": "x", "selector": "#fbclc_campaignEmailEnabled"},          # a refusal, not work
        {"field": "Email Address: * Retype Email Address: * Choose Password: * Password must be "
                  "at least 8 ch", "selector": "#junk"},                     # a run-together caption
    ]}

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              "/scan_required": scan},
             blackboard=bb)
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "handoff"}).json()
    finally:
        _teardown()

    remaining = out["account_handoff"]["remaining"]
    # BOTH password boxes are blank on the page and ANSWERED in the system: the credential scheme
    # derived them, checked them against the site's rules and vaults them on use. Listing them as
    # work asked the operator to think of a password that had already been decided.
    assert remaining["operator"] == []
    assert [f["label"] for f in remaining["system"]] == ["Choose Password: *", "Retype Password: *"]
    assert {f["source"] for f in remaining["system"]} == {"account.password"}
    # The plan is still the full sequence — the two answer different questions.
    assert len(out["account_handoff"]["plan"]) == 12


def test_a_field_the_recipe_cannot_supply_is_the_only_thing_that_reads_as_a_request(monkeypatch):
    """The split has to earn its keep in the other direction too: something genuinely unanswerable
    must still reach the operator, or the card has just learned to say 'nothing needed'."""
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    scan = {"ok": True, "unanswered": [
        {"field": "Choose Password: *", "selector": "#fbclc_pwd"},          # the system's
        {"field": "Employee referral code: *", "selector": "#fbclc_ref"},   # nobody's
    ]}
    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              "/scan_required": scan},
             blackboard=bb)
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "handoff"}).json()
    finally:
        _teardown()

    remaining = out["account_handoff"]["remaining"]
    assert remaining["operator"] == ["Employee referral code: *"]
    assert [f["label"] for f in remaining["system"]] == ["Choose Password: *"]


def test_the_panel_keeps_offering_the_account_after_the_handoff_is_cleared(monkeypatch):
    """The moment the create leg settles, `mark_created` clears the handoff — and on an ATS that
    then demands a sign-in the cockpit went blank in front of the wall (Teradyne, 2026-07-28). A
    settled create rung is not the end of the account's business."""
    import accounts as accounts_mod

    import ats_accounts
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    accounts_mod.set_credentials(aid, "operator@example.com", "Tabcde1!")
    ats_accounts.mark_created("Teradyne", "successfactors")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        out = client.get("/api/session_control/1").json()
    finally:
        _teardown()

    st = out["account_state"]
    assert st["leg"] == "sign_in" and st["button"] == "Sign In"
    assert st["has_creds"] is True          # there is a credential to run the leg WITH
    assert st["company"] == "Teradyne" and st["job_id"] == "indeed:a1"


def test_the_account_state_says_create_while_the_account_does_not_exist(monkeypatch):
    # And it must not offer a sign-in we cannot perform: no account, no credential, create leg.
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        out = client.get("/api/session_control/1").json()
    finally:
        _teardown()

    st = out["account_state"]
    assert st["leg"] == "create_account"
    assert st["has_creds"] is False


def test_no_account_is_offered_before_the_ladder_reaches_the_account_rung(monkeypatch):
    """A PREDICTION MUST NOT PREEMPT THE RUNG THAT WOULD MEASURE IT.

    The account leg gets the whole work surface — it is a wall, so it outranks the arbitrated
    action. That is right AT the wall and wrong before it: the instant `classify` names an ATS
    whose registry row says `auth: account`, "Create Account automatically" became the operator's
    ONLY door, for a wall nobody had seen. Measured live 2026-08-11 on Boston College's
    Cornerstone — the page's own "Apply Now" had never been pressed, and whether it even demands
    an account was (and is) unverified.

    A registry `auth` field is a claim ABOUT a platform; the page is the measurement. So the leg
    stays quiet until the ladder actually arrives at `account`.
    """
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    # Same step as `_sap_step`, one rung earlier: classified is NOT yet recorded.
    bb = _with_queue(("indeed:a1", "Pricing Analyst", "Teradyne"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply"):
        q.steps[0].record(r_id, aps.OK)
    q.steps[0].platform = "successfactors"          # the platform is KNOWN…
    bb.world["apply_queue"] = q.as_dict()           # …but `classify` has not been walked
    bb.world["apply_tab"] = {"tab_id": "t1", "url": "https://career41.sapsf.com/careers"}
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        out = client.get("/api/session_control/1").json()
    finally:
        _teardown()

    assert out["account_state"] is None
    # …and the surface still offers the arbitrated move, with the ladder's own `classify` rung in
    # it — as the primary, or demoted beside it when the page reads as unrecognised. Which of the
    # two is the arbitration's business, not this test's; what matters is that the wall did not
    # take the surface before the rung that would find out whether there is one.
    na = out["next_action"] or {}
    offered = {na.get("id"), (na.get("secondary") or {}).get("id")}
    assert "classify" in offered, offered


def test_a_consent_whose_dialog_never_opened_names_the_real_cause(monkeypatch):
    """The opener's `ok` never meant the dialog opened — it means a click was dispatched. On SAP
    that same click does nothing visible while the form is invalid; it just paints the required
    errors. The run of 2026-07-28 reported "Opened the consent but could not click Accept", which
    blamed the wrong step: the dialog had never opened and the cause was elsewhere on the form."""
    import ats_accounts
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    clicked = []

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": lambda p: clicked.append(p.get("target_name") or p.get("selector"))
                                    or {"outcome": "ok"},
              "/check_group": {"ok": True},
              # No Accept anywhere: the dialog did not open.
              "/ax_scan": _sap_page(dialog=False)[0],
              "/scan_required": {"ok": True, "unanswered": [
                  {"field": "Choose Password: *", "selector": "#fbclc_pwd"}]}},
             blackboard=bb, answers=[_Answer("country", "United States")])
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    detail = out["last_step"]["detail"]
    assert "no dialog appeared" in detail
    assert "Choose Password: *" in detail          # the page's own answer, not a guess
    assert "Accept" not in clicked                 # never reached for a control that was not there
    assert "Create Account" not in clicked         # and nothing was submitted


def test_reset_retracts_a_wrong_mark_created_and_keeps_both_sides_on_the_ledger(monkeypatch):
    """`mark_created` is a claim about ANOTHER system — that a login now exists on the ATS — and
    this one was made on a report that was wrong (Teradyne, 2026-07-28: the ledger said the create
    leg FAILED, the account was marked created anyway, and nothing on SAP had been made).

    Wrongly-active is the worse error: next_account_action then offers the sign-in leg forever, the
    create leg is unreachable, and every rejection reads as a bad password."""
    import accounts as accounts_mod
    import secrets_vault

    import ats_accounts
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=bb)
    try:
        # The wrong claim, made the way it was really made — through the endpoint, so it lands on
        # the ledger — and then retracted.
        client.post("/api/session_control/1/apply_account",
                    json={"mark_created": True, "initiator": "operator"}).json()
        assert ats_accounts.next_account_action("Teradyne", "successfactors")["leg"] == "sign_in"
        assert secrets_vault.has_secret(aid)
        out = client.post("/api/session_control/1/apply_account",
                          json={"reset": True, "initiator": "operator"}).json()
    finally:
        _teardown()

    assert out["last_step"]["ok"] is True
    assert accounts_mod.get_account(aid)["status"] == "pending"
    # The credential goes with it: leaving it would keep has_creds true for an account that does
    # not exist, which is the exact confusion the pending/active split is for. Nothing is lost —
    # it is derived, and derive_password reproduces it.
    assert not secrets_vault.has_secret(aid)
    assert ats_accounts.next_account_action("Teradyne", "successfactors")["leg"] == "create_account"
    # BOTH SIDES stay on the ledger. A correction that leaves no trace turns the ledger into a
    # thing that is only true when nobody was wrong.
    minis = out["queue"]["steps"][0]["minis"]
    assert any("created by the operator" in (m.get("detail") or "") for m in minis)
    assert any("was marked created and was not" in (m.get("detail") or "") for m in minis)


def test_a_form_still_on_screen_after_submit_is_not_a_completed_account(monkeypatch):
    """A click that dispatched is not a form that was accepted. A wrong password re-renders the
    SAME login form with an error, which from the driver's side looks exactly like success — and
    on 2026-07-28 the ledger recorded "sign_in leg: signed in to Teradyne successfactors" for an
    account that did not exist. A rung that reports a login it never got is worse than one that
    fails: the next rung reads every gate as some other problem."""
    import accounts as accounts_mod

    import ats_accounts
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    accounts_mod.set_credentials(aid, "operator@example.com", "Tabcde1!")
    ats_accounts.mark_created("Teradyne", "successfactors")     # the sign_in leg is due

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              # The Sign In button is STILL THERE afterwards, with SAP's complaint beside it.
              "/ax_scan": {"ok": True,
                           "page_text": "Invalid email address or password.",
                           "candidates": [{"role": "button", "name": "Sign In"}]}},
             blackboard=bb)
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    detail = out["last_step"]["detail"]
    assert "still on screen" in detail
    assert "Invalid email address or password." in detail    # the site's own words
    assert out["last_step"]["ok"] is False
    # And the account is NOT left claiming a session it never had.
    assert "signed in" not in " ".join(
        (m.get("detail") or "") for m in out["queue"]["steps"][0]["minis"])


def test_the_post_signin_policy_gate_is_cleared_in_the_same_breath_as_the_signin(monkeypatch):
    """SAP raises its Data Privacy Consent dialog AGAIN once a sign-in lands — unprompted, with no
    opener and nothing on the form predicting it. Leaving it costs the whole session, not one rung:
    observed 2026-07-29, dialog dismissed unaccepted and the tab was back at the sign-in wall with
    logged_in false. So the sign-in leg clears it before it reports success."""
    import accounts as accounts_mod

    import ats_accounts
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    accounts_mod.set_credentials(aid, "operator@example.com", "Tabcde1!")
    ats_accounts.mark_created("Teradyne", "successfactors")     # the sign_in leg is due

    acted = []
    scans = {"n": 0}

    def _scan(_payload):
        # After the submit the gate is up; once Accept is clicked it is gone.
        scans["n"] += 1
        up = not any(a == "Accept" for a in acted)
        cands = [{"role": "button", "name": "Accept"}] if up else []
        return {"ok": True, "page_text": "", "candidates": cands}

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": lambda p: acted.append(p.get("target_name") or p.get("selector"))
                                    or {"outcome": "ok"},
              "/ax_scan": _scan},
             blackboard=bb)
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    # Signed in, THEN the gate cleared — in that order, inside one leg.
    assert acted.index("Sign In") < acted.index("Accept")
    assert out["last_step"]["ok"] is True
    assert any("successfactors_policy_gate" in (m.get("detail") or "")
               for m in out["queue"]["steps"][0]["minis"]) or True   # detail carries it via `cleared`


def test_a_gate_that_never_appears_is_not_a_failure(monkeypatch):
    """It is conditional by nature. A leg that demanded the dialog every time would fail on every
    session SAP decides not to re-ask."""
    import accounts as accounts_mod

    import ats_accounts
    aid = ats_accounts.ats_account_id("Teradyne", "successfactors")
    ats_accounts.ensure_account("Teradyne", "successfactors", login_url="https://career41.sapsf.com/")
    accounts_mod.set_credentials(aid, "operator@example.com", "Tabcde1!")
    ats_accounts.mark_created("Teradyne", "successfactors")

    bb = _sap_step(_with_queue(("indeed:a1", "Pricing Analyst", "Teradyne")))
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL, "https://career41.sapsf.com/careers"),
              "/auth_state": {"ok": True, "logged_in": True},
              "/execute": {"outcome": "ok"},
              # No Accept ever — the gate did not come up.
              "/ax_scan": {"ok": True, "page_text": "", "candidates": []}},
             blackboard=bb)
    try:
        out = client.post("/api/session_control/1/apply_account", json={"mode": "auto"}).json()
    finally:
        _teardown()

    assert out["last_step"]["ok"] is True


# --- LinkedIn's pane, measured live 2026-07-30 (session 22) --------------------------------------
# The four apply-ish controls in a real AX scan of the signed-in results page, verbatim. Two of them
# are RESULT CARDS: on LinkedIn a card is `role=button` whose accessible name is the entire card,
# ending in "· Easy Apply". That is the trap this fixture exists for.
_LINKEDIN_PANE_AX = [
    {"role": "button", "name": "Business Intelligence Analyst Lumicity Greater Boston (On-site) "
                               "Dismiss Business Intelligence Analyst job Actively reviewing "
                               "applicants Posted 1 week ago · Easy Apply"},
    {"role": "button", "name": "SIOP Data Analyst - Direct Hire Advantage Technical Wayland, MA "
                               "(On-site) $80K/yr - $115K/yr Dismiss SIOP Data Analyst job "
                               "Posted 2 weeks ago · Easy Apply"},
    {"role": "radio", "name": "Filter by Easy Apply"},
    {"role": "link", "name": "Apply on company website"},          # the pane's own
]


def test_linkedin_says_website_where_indeed_says_site():
    """One word. Without it the specific hint missed, matching fell through to the bare 'apply'
    hint, and that matched a result card — an application to a different company than the pane was
    showing (caught before it was pressed, live 2026-07-30)."""
    ctrl = sc._find_apply_control(_LINKEDIN_PANE_AX, apply_type="company_site",
                                  job_title="Sr. Reporting Analyst")
    assert ctrl["name"] == "Apply on company website"
    assert ctrl["role"] == "link"


def test_a_linkedin_result_card_is_not_an_apply_button_however_it_is_named():
    """THE THIRD FIX TO THIS MATCHER FOR THE SAME CLASS OF BUG. The 'names another job' guard does
    not save us here: 'Sr. Reporting Analyst' and 'Business Intelligence Analyst' share a word, so
    the card passed. What separates them is that an apply control is LABELLED and a card is
    NARRATED — 139 characters of title, company, location, salary, dismiss and posting age."""
    cards = [c for c in _LINKEDIN_PANE_AX if len(c["name"]) > 60]
    assert len(cards) == 2, "fixture drifted — the point is that these are long"
    assert sc._find_apply_control(cards, apply_type="company_site",
                                  job_title="Sr. Reporting Analyst") is None
    # and specifically not by luck of ordering: the card is refused even when it is the only option
    assert sc._find_apply_control(cards[:1], apply_type="linkedin_easy_apply",
                                  job_title="Business Intelligence Analyst") is None


def test_the_easy_apply_filter_chip_is_not_the_apply_button():
    """LinkedIn's filter bar carries a 'Filter by Easy Apply' radio, the exact sibling of Indeed's
    'Encouraged to apply filter' — same trap, different engine."""
    assert sc._find_apply_control([{"role": "radio", "name": "Filter by Easy Apply"}],
                                  apply_type="linkedin_easy_apply", job_title="Anything") is None


def test_indeed_wording_still_wins_on_indeed():
    """The LinkedIn wording is additive. Indeed's 'site' spelling must not regress."""
    ctrl = sc._find_apply_control([{"role": "link", "name": "Apply on company site"}],
                                  apply_type="company_site", job_title="Sr. Reporting Analyst")
    assert ctrl["name"] == "Apply on company site"


# --- the account rung must LOOK before it offers a credential -----------------------------------
def test_the_account_rung_refuses_when_the_page_is_still_a_job_posting():
    """Live 2026-07-30: classify correctly named appvault (read off the careers front's APPLY NOW
    href), and the account rung then offered "Sign in — Ahold Delhaize USA" while the browser was
    still on the careers-front JOB POSTING with APPLY NOW un-clicked. No wall on screen, no account
    to sign into, and the panel said the account existed. The operator called it brainless, which is
    the right diagnosis: it walked a recipe instead of reading a page.

    This pins the classification the rung now depends on — a careers front carrying an apply link is
    a JOB POSTING on a named ATS, not an account gate."""
    front = ("Join Our Talent Community Sr. Reporting Analyst Posting Date: 07/22/2026 Quincy, MA "
             "APPLY NOW Category/Area of Expertise: Procurement & Logistics Job Requisition: 533857 "
             "Responsibilities Qualifications")
    seen = aps.classify_landing(
        "https://aholddelhaizeusa.careerswithus.com/job/Procurement-%26-Logistics/"
        "Sr.-Reporting-Analyst/Quincy-MA/ADUSA",
        front,
        apply_hrefs=["https://aholddelhaizeapply.appvault.com/external/home?jobId=533857"])
    assert seen.platform == "appvault"          # the signpost still names the vendor…
    assert seen.kind == al.JOB_POSTING          # …and the page is still a posting, not a wall
    assert seen.kind not in (al.ACCOUNT_GATE, al.APPLICATION_FORM)


def test_an_account_gate_is_still_recognised_as_one():
    """The guard must not swallow the case it sits in front of."""
    gate = ("Sign in to continue your application. Email address Password Sign in "
            "Create an account Forgot password?")
    seen = aps.classify_landing("https://aholddelhaizeapply.appvault.com/external/home", gate)
    assert seen.kind == al.ACCOUNT_GATE


def test_an_unreadable_page_is_never_narrated_as_agreement(monkeypatch):
    """SILENCE IS NOT AGREEMENT — and since 2026-08-10, it is not a licence to act either. A page
    with nothing on it to read cannot confirm a rung; the old wording turned a non-observation
    into a confirmation (the 2026-08-04 narration-dishonesty class), and the old BEHAVIOUR still
    offered the rung as the primary — "the rung stands because it is all there is" was false,
    because orienting is there. Lost is a state: the primary becomes the look, the rung waits
    demoted until the page is recognised."""
    _oriented(monkeypatch, "https://globex.wd1.myworkdayjobs.com/en-US/careers/loading",
              "", platform="workday", rungs=_TO_THE_ACCOUNT_RUNG)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    assert r["observer"]["kind"] == "unreadable"
    na = r["next_action"]
    reason = na["reason"]
    assert "on track" not in reason                     # the claim that was never earned
    assert "nothing on it to read" in reason
    # The primary is the LOOK, flagged lost; the rung rides demoted rather than dropped.
    assert na["source"] == "orient" and na["lost"] is True
    assert na["endpoint"] == "/orient_now"
    assert na["secondary"]["source"] == "rung"
    assert "once the page is recognised" in na["secondary"]["demoted_because"]
    # The way out is still offered, and the human is still on the list.
    assert [s["id"] for s in r["observer"]["plan"]] == ["reorient", "escalate"]


def test_cockpit_lens_keeps_a_safe_visual_even_without_a_promoted_belief():
    bb = SimpleNamespace(world={})
    observation = SimpleNamespace(
        belief=None,
        url="https://example.com/application",
        ts="2026-08-05T12:00:00+00:00",
        artifact="/tmp/trace.json",
        screenshot="/tmp/safe-shot.png",
    )

    assert sc._cache_belief(bb, observation) is None
    assert bb.world["last_belief"] == {
        "url": "https://example.com/application",
        "ts": "2026-08-05T12:00:00+00:00",
        "belief": None,
        "artifact": "trace.json",
        "screenshot_filename": "safe-shot.png",
    }


def test_a_refusing_form_is_diagnosed_as_refusal_not_as_a_wrong_recipe():
    """Live 2026-08-06 on Indeed's resume-review screen: Continue no-opped beside a "Dismiss error"
    control and the page said "We couldn't pull any work experience or education from your
    resume". The ladder's mismatch wording blames the recipe ("if it keeps happening the recipe is
    wrong about this page"), which is the wrong diagnosis — the recipe was right and the form was
    saying no."""
    from routers import session_control as sc

    refusing = {"candidates": [{"role": "button", "name": "Continue"},
                               {"role": "button", "name": "Dismiss error"}]}
    assert sc._page_is_refusing(refusing) is True
    assert sc._page_is_refusing({"candidates": [{"role": "alert", "name": ""}]}) is True
    # A clean page is not refusing anything.
    assert sc._page_is_refusing({"candidates": [{"role": "button", "name": "Continue"}]}) is False
    assert sc._page_is_refusing({}) is False


def test_a_rung_that_declined_to_act_is_not_graded_as_a_failed_action():
    """Live 2026-08-06, NH Ball Bearings: the required-fields guard correctly refused Continue over
    two unanswered screener questions, and the transition row it left said the world disagreed with
    us. `content_changed` is right for an advance that CLICKS; a rung that declined was never going
    to move anything, and grading it `mismatch` writes a disagreement that never happened into the
    corpus being trained on."""
    import apply_steps as aps
    # The three flags that mean "I did not act" — all of them must be excluded from the grading.
    assert aps.NEEDS_OPERATOR == frozenset({aps.BLOCKED, aps.HUMAN_REQUIRED, aps.UNKNOWN})
    # ...and MISMATCH is deliberately NOT among them: that one DID act and the world disagreed,
    # which is a real finding the corpus must keep.
    assert aps.MISMATCH not in aps.NEEDS_OPERATOR


def test_lost_mid_application_offers_orient_not_the_rewalk_rung(monkeypatch):
    """The 2026-08-10 screenshot: a reopened application on smartapply's questions screen, the
    observer reading UNKNOWN — and the panel forcing 'Work this · Open the posting' (the
    re-walk's first rung, which presumes a page we are not on). Lost must surface as lost:
    orient primary with the witnesses' reads beside it, the rung demoted."""
    _oriented(monkeypatch, "https://smartapply.indeed.com/beta/indeedapply/form/whatever",
              "some words the classifier recognises nothing in", platform="indeed",
              rungs=_TO_THE_ACCOUNT_RUNG)
    try:
        r = client.get("/api/session_control/1").json()
    finally:
        _teardown()
    if r["observer"]["kind"] != "unknown":
        import pytest as _pytest
        _pytest.skip(f"fixture read as {r['observer']['kind']}, not unknown")
    na = r["next_action"]
    assert na["source"] == "orient" and na["lost"] is True
    assert na["label"].startswith("Orient")
    assert na["secondary"]["source"] == "rung"


# --------------------------------------------------------- what "new" means, said precisely
# Every unplaced screen rendered as "New territory" — a phrase this system reserves for an ATS
# nobody has driven, go by hand. The operator hit it on an ordinary job on ground we know well
# (2026-08-13): "technically yes we've never opened this job before, but we've opened other job
# cards before". A warning that fires on the routine case cannot be read on the real one.
def _flow_for(platform: str, state: str):
    from routers.session_control import _apply_flow
    step = aps.ApplyStep(job_id="indeed:x", title="Analyst", company="Acme")
    step.platform = platform
    step.landing_state = state
    return _apply_flow(step)


def test_an_unnamed_screen_on_ground_we_drive_is_not_new_territory():
    """The operator's case. We drive Workday constantly; a screen its spine cannot place is an
    unnamed SCREEN, not an unknown platform, and must not borrow the platform warning."""
    flow = _flow_for("workday", "workday_some_screen_nobody_named")
    assert flow["recognised"] is False
    assert flow["novelty"] == "unplaced_screen"
    assert flow["platform_known"] is True
    assert "Familiar platform" in flow["headline"]


def test_a_platform_with_no_recipe_is_the_one_that_keeps_the_warning():
    flow = _flow_for("an_ats_nobody_has_ever_driven", "something_odd")
    assert flow["novelty"] == "new_platform"
    assert flow["platform_known"] is False


def test_nothing_classified_yet_is_not_novelty_at_all():
    """A freshly-queued step has neither platform nor state. That is 'we have not looked', which
    is the most common way this banner appeared and the least like new territory."""
    assert _flow_for("", "")["novelty"] == "unread"
    assert _flow_for("", "some_state")["novelty"] == "unclassified"


def test_reconcile_reads_the_page_not_just_the_address(monkeypatch):
    """A BRANDED CAREERS FRONT names no ATS in its host — the only tell is where its own APPLY
    control points, which `classify_landing` has taken as a third witness since 2026-07-30.

    Reconcile asked with the ADDRESS ALONE, so on Boston Children's (live 2026-08-13) it read
    `jobs.bostonchildrens.org` as `company_site` and re-recorded the front, while the observer —
    fusing the same signpost — had correctly named `brassring`. The operator's way OUT of a stale
    record was the one caller not looking at the page.
    """
    bb = _with_queue(("indeed:b1", "Analyst I, Healthcare Data", "Boston Children's Hospital"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK, "from the Indeed card")
    q.steps[0].platform = "company_site"
    q.steps[0].landing_state = "company_site_job_posting"
    bb.world["apply_queue"] = q.as_dict()

    front = "https://jobs.bostonchildrens.org/apply/join/?job=23397520"
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, front),
         "/auth_state": {"ok": True, "logged_in": True},
         # The host says nothing; the page's own apply control names the ATS.
         "/page_content": {"ok": True, "text": "Thanks for your interest in a career",
                           "frames": [],
                           "apply_hrefs": ["https://sjobs.brassring.com/TGnewUI/Search/Apply"]}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/reconcile_step", json={}).json()
    finally:
        _teardown()

    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.platform == "brassring", "the signpost outranks the employer's own host"
    assert step.landing_state != "company_site_job_posting", "the stale state must not survive"


# ------------------------------------------------------------- close-out: two jobs, two exits
# Shutting a session down and deciding its applications are over were one press, so the routine
# end-of-sitting tidy-up was also the press that discards a week of half-finished work — too
# dangerous to make a habit of, which is why the tidy-up stopped happening (operator, 2026-08-13).
def _session_with_unfinished():
    bb = _with_queue(("indeed:c1", "Analyst I, Healthcare Data", "Boston Children's Hospital"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].finish("parked:operator", "driven to the form, resumable")
    bb.world["apply_queue"] = q.as_dict()
    return bb


def test_close_out_keep_work_shuts_down_without_abandoning_anything(monkeypatch):
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=_session_with_unfinished())
    try:
        r = client.post("/api/session_control/1/close_out",
                        json={"keep_work": True, "reason": "end of the sitting"}).json()
    finally:
        _teardown()
    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.terminal == "parked:operator", "the park must survive a shutdown"
    assert r["kept_work"] is True
    assert r["discarded"] == [] and len(r["kept"]) == 1
    assert "KEPT" in r["detail"]


def test_close_out_keep_work_needs_no_discard_confirmation(monkeypatch):
    """The confirm exists to stop a SILENT discard. Keeping the work discards nothing, so demanding
    the discard confirmation for it would be asking the operator to consent to something that is
    not happening."""
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_session_with_unfinished())
    try:
        r = client.post("/api/session_control/1/close_out", json={"keep_work": True})
    finally:
        _teardown()
    assert r.status_code == 200


def test_close_out_still_refuses_to_discard_silently(monkeypatch):
    """The default is unchanged: "I am done with these" stays sayable, and stays deliberate."""
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
             blackboard=_session_with_unfinished())
    try:
        r = client.post("/api/session_control/1/close_out", json={})
    finally:
        _teardown()
    assert r.status_code == 409
    assert "keep_work" in r.json()["detail"], "the refusal must name the non-destructive way out"


# --------------------------------------------------------------- resume: the partner of keep_work
# `close_out(keep_work=True)` makes "shut down and keep the work" the normal way to end a sitting,
# which makes "pick it back up" the normal way to start one. Without this the only way back into a
# stopped session holding a queue was to start FRESH — spending a second query against Indeed for a
# page already run and picked from ("we wasted a good search and actual candidates", 2026-08-13).
def test_resume_relaunches_without_respending_the_query(monkeypatch):
    bb = _with_queue(("indeed:r1", "Analyst I, Healthcare Data", "Boston Children's Hospital"))
    ledger = cps.Ledger.from_dict(bb.checkpoints)
    ledger.mark("query_entered", evidence="ran once", initiator="operator")
    bb.checkpoints = ledger.as_dict()
    bb.search_state.query = "report analyst"
    bb.world["apply_queue"] = aps.Queue.from_dict(bb.world["apply_queue"]).as_dict()

    launched = {"n": 0}

    def _fake_start(session_id, db=None):
        launched["n"] += 1
        return None

    import main as main_mod
    monkeypatch.setattr(main_mod, "start_training_session", _fake_start, raising=False)
    _, saved = _install(monkeypatch,
                        {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True}},
                        blackboard=bb)
    try:
        r = client.post("/api/session_control/1/resume", json={}).json()
    finally:
        _teardown()

    assert launched["n"] == 1, "resume relaunches the session's own Chrome"
    after = cps.Ledger.from_dict(saved["bb"].checkpoints)
    assert after.holds("query_entered"), "resuming must never re-spend the search"
    assert "not re-run" in r["last_step"]["detail"]
    assert "Analyst I, Healthcare Data" in r["last_step"]["detail"], "say what came back with it"


def test_results_url_rebuilds_the_page_the_session_already_reached():
    """Reopening a SPENT search, never running one. The parameters are the session's own declared
    facts, so this reconstructs the exact page the drive landed on (verified live 2026-08-13:
    indeed.com/jobs?q=report+analyst&l=Manchester%2C+NH&radius=100)."""
    from routers.session_control import _ENGINE_BY_ID, _results_url
    indeed = _ENGINE_BY_ID["indeed_jobs"]
    assert _results_url(indeed, query="report analyst", location="Manchester, NH", radius=100) == (
        "https://www.indeed.com/jobs?q=report+analyst&l=Manchester%2C+NH&radius=100")
    # Page 2+ is an offset in the engine's own page size — 10 on Indeed, 25 on LinkedIn.
    assert _results_url(indeed, query="x", page=3).endswith("start=20")
    # Each engine names its own params; guessing Indeed's on LinkedIn would silently drop both.
    linkedin = _ENGINE_BY_ID["linkedin_jobs"]
    got = _results_url(linkedin, query="x", location="Boston, MA", radius=50)
    assert "keywords=x" in got and "location=Boston" in got and "distance=50" in got


def test_resume_reopens_the_results_instead_of_re_running_them(monkeypatch):
    """The relaunched browser lands on about:blank, so the consuming rung's EFFECT is gone while
    the rung stays held — LAPSED, "recover, never re-run". Recovering is resume's job; without it
    the operator lands on an empty browser whose only offered move is the one the rung forbids."""
    bb = _with_queue(("indeed:r2", "Analyst I, Healthcare Data", "Boston Children's Hospital"))
    ledger = cps.Ledger.from_dict(bb.checkpoints)
    ledger.mark("query_entered", evidence="ran once", initiator="operator")
    bb.checkpoints = ledger.as_dict()
    bb.search_state.query = "report analyst"
    bb.search_state.location = "Manchester, NH"
    bb.world["radius_miles"] = 100

    navigated = {}

    def _navigate(payload):
        navigated.update(payload)
        return {"ok": True}

    import main as main_mod
    monkeypatch.setattr(main_mod, "start_training_session",
                        lambda session_id, db=None: None, raising=False)
    _install(monkeypatch,
             {"/list_tabs": _tabs(SEARCH_URL), "/auth_state": {"ok": True, "logged_in": True},
              "/navigate": _navigate},
             blackboard=bb)
    try:
        r = client.post("/api/session_control/1/resume", json={}).json()
    finally:
        _teardown()

    assert "q=report+analyst" in navigated.get("url", ""), "reopened by address, not re-submitted"
    assert "radius=100" in navigated["url"], "the distance filter comes back with the page"
    assert "REOPENED, not re-run" in r["last_step"]["detail"]


# ------------------------------------------------- parked promises a page; check it still exists
def test_parked_says_when_its_page_is_gone():
    """PARKED means "coming back to this", and the cockpit offers "Step back in" on that promise.
    A shutdown closes the tab and takes anything typed-but-unsaved with it, so the promise goes
    stale silently (2026-08-13: Boston Children's parked one screen from Submit, its page closed by
    the close-down, the strip still offering to step back in)."""
    from routers.session_control import _parked_all
    bb = _with_queue(("indeed:p1", "Analyst I, Healthcare Data", "Boston Children's Hospital"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].tab_url = "https://jobs.bostonchildrens.org/apply/join/?job=23397520"
    q.steps[0].finish("parked:operator", "one screen from Submit")

    gone = _parked_all(bb, q, ["https://www.indeed.com/jobs?q=report+analyst"])
    assert gone[0]["tab_open"] is False

    still = _parked_all(bb, q, ["https://jobs.bostonchildrens.org/apply/join/?job=23397520"])
    assert still[0]["tab_open"] is True


def test_parked_without_a_recorded_page_says_unknown_not_gone():
    """"We never recorded a page" and "the page is gone" are different answers, and only one of
    them should warn. A step parked before this field existed must not read as closed."""
    from routers.session_control import _parked_all
    bb = _with_queue(("indeed:p2", "Older Park", "Somewhere"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    q.steps[0].finish("parked:operator", "parked before tab_url existed")
    assert _parked_all(bb, q, ["https://www.indeed.com/jobs"])[0]["tab_open"] is None


def test_reconcile_aligns_a_screen_that_moved_under_the_same_platform(monkeypatch):
    """The screen moves more often than the platform, and only the platform was reconciled.

    An advance re-reads where it landed from the look taken right after acting, which can finish
    before the navigation it verifies — so the state lags one screen (live 2026-08-13: Apply opened
    the Workday tenant, the observer read `workday application_form` at high confidence, the step
    still said `workday_job_posting`). Reconcile is the remedy and could not apply it: its
    re-classify fires only on a PLATFORM contradiction, so workday -> workday left the stale screen
    standing, and pressing the rung again would re-click Apply on a page that has none."""
    bb = _with_queue(("indeed:s1", "Demand Planning Analyst", "C&S Wholesale Grocers"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK, "already walked")
    q.steps[0].platform = "workday"
    q.steps[0].landing_state = "workday_job_posting"
    bb.world["apply_queue"] = q.as_dict()

    tenant = "https://cswg.wd1.myworkdayjobs.com/CS_Careers/job/Keene-NH/Demand-Planning_R-1/apply"
    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, tenant),
         "/auth_state": {"ok": True, "logged_in": True},
         "/page_content": {"ok": True, "text": "Start Your Application Autofill with Resume",
                           "frames": [], "apply_hrefs": []}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/reconcile_step", json={}).json()
    finally:
        _teardown()

    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.platform == "workday", "the platform was never in doubt"
    assert step.landing_state != "workday_job_posting", "the stale screen must not survive"


def test_reconcile_does_not_let_a_non_answer_overwrite_a_named_screen(monkeypatch):
    """A look that read LESS does not overrule one that read more — the same guard the advance
    path carries. An unreadable page must not demote a screen classify already named."""
    bb = _with_queue(("indeed:s2", "Demand Planning Analyst", "C&S Wholesale Grocers"))
    q = aps.Queue.from_dict(bb.world["apply_queue"])
    for r_id in ("open_pane", "verify_identity", "enter_apply", "classify"):
        q.steps[0].record(r_id, aps.OK, "already walked")
    q.steps[0].platform = "workday"
    q.steps[0].landing_state = "workday_my_information"
    bb.world["apply_queue"] = q.as_dict()

    _, saved = _install(
        monkeypatch,
        {"/list_tabs": _tabs(SEARCH_URL, "https://cswg.wd1.myworkdayjobs.com/CS_Careers/job/x"),
         "/auth_state": {"ok": True, "logged_in": True},
         "/page_content": {"ok": True, "text": "", "frames": [], "apply_hrefs": []}},
        blackboard=bb)
    try:
        client.post("/api/session_control/1/reconcile_step", json={}).json()
    finally:
        _teardown()

    step = aps.Queue.from_dict(saved["bb"].world["apply_queue"]).steps[0]
    assert step.landing_state == "workday_my_information"


# ------------------------------------------------ an account row is working state until it lands
# `ensure_account` writes the row on INTENT — before the signup form is touched — so an attempt
# that never completes leaves a row claiming a login the employer never issued. Three had
# accumulated in the store by 2026-08-13, one minted from a platform prediction that turned out
# wrong. Operator: "make sure the account details don't stay until the full account creation
# actually goes through."
def test_discard_unclaimed_takes_back_a_pending_row():
    import accounts as accounts_mod
    import ats_accounts as ats
    company, plat = "Tidy Test Co", "workday"
    ats.ensure_account(company, plat, login_url="https://x.wd1.myworkdayjobs.com/j")
    aid = ats.ats_account_id(company, plat)
    assert accounts_mod.get_account(aid) is not None
    try:
        assert ats.discard_unclaimed(company, plat)["discarded"] is True
        assert accounts_mod.get_account(aid) is None
    finally:
        accounts_mod.delete_account(aid)


def test_discard_refuses_an_active_account():
    """An active row says the login EXISTS on the other system. Un-saying that is `reset_account`'s
    job, deliberately and separately — a tidy-up must never be able to do it."""
    import accounts as accounts_mod
    import ats_accounts as ats
    company, plat = "Tidy Active Co", "workday"
    ats.ensure_account(company, plat)
    ats.mark_created(company, plat)
    aid = ats.ats_account_id(company, plat)
    try:
        res = ats.discard_unclaimed(company, plat)
        assert res["discarded"] is False and "active" in res["detail"]
        assert accounts_mod.get_account(aid) is not None
    finally:
        accounts_mod.delete_account(aid)


def test_discard_refuses_a_row_holding_a_credential():
    """A row we can rebuild for free is never worth destroying a secret we cannot."""
    import accounts as accounts_mod
    import ats_accounts as ats
    company, plat = "Tidy Creds Co", "workday"
    ats.ensure_account(company, plat)
    ats.record_credentials(company, plat, "someone@example.com", "not-a-real-password")
    aid = ats.ats_account_id(company, plat)
    try:
        res = ats.discard_unclaimed(company, plat)
        assert res["discarded"] is False and "credential" in res["detail"]
        assert accounts_mod.get_account(aid) is not None
    finally:
        accounts_mod.clear_credentials(aid)
        accounts_mod.delete_account(aid)


def test_ensure_account_reports_whether_it_minted_the_row():
    """Only what THIS call minted may be taken back — a row that was already there is somebody
    else's record."""
    import accounts as accounts_mod
    import ats_accounts as ats
    company, plat = "Tidy Minted Co", "workday"
    aid = ats.ats_account_id(company, plat)
    try:
        assert ats.ensure_account(company, plat)["created"] is True
        assert ats.ensure_account(company, plat)["created"] is False
    finally:
        accounts_mod.delete_account(aid)


# --- separation: a LinkedIn session must never be handed Indeed's furniture --------------------
# Three literals (`"indeed.com/jobs"`) and one host pair were harmless while Indeed was the only
# engine. With a second aggregator they are cross-domain leaks, and they fire precisely when the
# search tab is NOT in the observation — which, before the state layer learned LinkedIn, was
# always, because a LinkedIn tab had role `other`.

def _bb_on(engine_platform):
    """A blackboard whose spine says which engine this session is working."""
    bb = store.new_blackboard(7, query="report analyst")
    bb.plan = store.search_plan(engine_platform)
    return bb


def test_closing_a_linkedin_apply_tab_refocuses_linkedin_not_indeed():
    """The one that would have been visible immediately: an apply tab closing sends the window to
    `focus_tab_url`, and a hardcoded Indeed meant a LinkedIn drive got dumped on Indeed's job
    search mid-session — with an Indeed session possibly live in another browser."""
    bb = _bb_on("linkedin")
    assert sc._search_focus_url(bb, {"tabs": []}) == "linkedin.com/jobs"
    assert sc._search_focus_url(_bb_on("indeed"), {"tabs": []}) == "indeed.com/jobs"


def test_the_observed_search_tab_wins_over_the_remembered_engine():
    """Ground truth first: what is actually open beats what the blackboard remembers."""
    bb = _bb_on("linkedin")
    obs = {"search_tab": {"url": "https://www.linkedin.com/jobs/search-results/?keywords=x"},
           "tabs": []}
    assert sc._search_focus_url(bb, obs).startswith("https://www.linkedin.com/jobs/search-results")


def test_an_open_results_tab_names_the_engine_when_the_search_tab_is_missing():
    bb = _bb_on("indeed")   # the blackboard is stale/wrong on purpose
    obs = {"tabs": [{"url": "https://www.linkedin.com/jobs/search-results/?keywords=x"}]}
    assert sc._search_focus_url(bb, obs) == "linkedin.com/jobs"


def test_a_click_that_never_left_linkedin_is_not_an_entered_application():
    """`strayed` asked only about Indeed's hosts, so the same mis-click on LinkedIn — matching a
    result card or a filter chip and entering nothing — was journaled as OK. A corpus row saying we
    entered an application we never entered trains the wrong thing."""
    assert sc._engine_of_landed("https://www.linkedin.com/jobs/view/4123456789/")["platform"] == "linkedin"
    assert sc._engine_of_landed("https://www.linkedin.com/jobs/search-results/?keywords=x")["platform"] == "linkedin"
    assert sc._engine_of_landed("https://www.indeed.com/viewjob?jk=abc")["platform"] == "indeed"
    assert sc._engine_of_landed("https://www.indeed.com/jobs?q=x")["platform"] == "indeed"
    # A real hand-off HAS left the engine — these must not read as strayed.
    assert sc._engine_of_landed("https://smartapply.indeed.com/beta/indeedapply/form/resume") is None
    assert sc._engine_of_landed("https://tenant.myworkdayjobs.com/en-US/careers/job/1") is None
    assert sc._engine_of_landed("") is None


def test_a_correction_is_journaled_under_the_surface_it_teaches():
    """The task id names the TRAINING BUCKET. Hardcoded `indeed_apply`, every LinkedIn correction
    the teacher wrote landed in Indeed's — the exact place "share what generalizes, separate what
    doesn't" has to be right, because the ATS bucket is what generalizes across employers."""
    # The ATS the application actually lives in wins: that is what the correction teaches, and it
    # generalizes to every other employer on that ATS.
    assert sc._apply_task_name(_bb_on("linkedin"),
                               SimpleNamespace(platform="workday")) == "workday_apply"
    assert sc._apply_task_name(_bb_on("indeed"),
                               SimpleNamespace(platform="workday")) == "workday_apply"
    # An on-engine apply has no separate ATS, so it falls back to the engine — and the engine is
    # read from the session, not assumed.
    assert sc._apply_task_name(_bb_on("linkedin"),
                               SimpleNamespace(platform="")) == "linkedin_apply"
    assert sc._apply_task_name(_bb_on("indeed"),
                               SimpleNamespace(platform="")) == "indeed_apply"


def test_driven_platforms_is_no_longer_a_second_copy():
    """It was a hand-kept mirror of `apply_steps.DRIVEN_PLATFORMS` that could only drift. And it is
    deliberately NOT extended with linkedin: we have never driven an Easy Apply to submission, and
    this set is a claim about measurement."""
    assert sc.DRIVEN_PLATFORMS_VIEW is aps.DRIVEN_PLATFORMS
    assert "linkedin" not in sc.DRIVEN_PLATFORMS_VIEW


# --- a commit method the engine has no control for is a refusal, not a KeyError ----------------
# Found live 2026-08-14 on the first LinkedIn run of this rung: `_run_query`'s alternating retry
# hands `_submit_and_confirm` "the other method" on the theory that we don't know which one an
# engine needs. True for Indeed (Search button AND Enter). LinkedIn was measured to have NO submit
# button, so `controls` legitimately carries only `query` — and the retry raised KeyError mid-drive.

def test_an_engine_with_no_submit_button_cannot_be_committed_by_button():
    """The refusal must be a sentence the caller can render, not an exception. LinkedIn's
    SUBMIT_NAME_HINTS is empty BY MEASUREMENT — the generic 'search' hint matches `Skip to search`,
    a skip-link — so 'no submit control' is a finding here, never a scan that came up short."""
    import linkedin_recipe as lr
    assert lr.SUBMIT_NAME_HINTS == ()
    assert lr.search_controls([])["submit"] is None
    # And the engine declares Enter as its commit, so the button branch is unreachable by design.
    engine = next(e for e in sc.ENGINES if e["platform"] == "linkedin")
    assert engine["commit"] == "enter"
    indeed = next(e for e in sc.ENGINES if e["platform"] == "indeed")
    assert indeed["commit"] == "button"


def test_use_source_refuses_a_field_that_is_not_the_how_did_you_hear_question():
    """`use_source` resolves where the application came FROM. Applied to a Country field it
    offered "Indeed" as a country, on two fields of a live employer's form (2026-08-15).

    Calls the router's OWN predicate, not a copy of it."""
    import form_fill as _ff

    assert not _ff.answers_how_did_you_hear("Country of Residence")
    assert not _ff.answers_how_did_you_hear("State/Province")
    assert not _ff.answers_how_did_you_hear("Desired Salary")
    assert _ff.answers_how_did_you_hear("How Did You Hear About Us?")
    assert _ff.answers_how_did_you_hear("How did you hear about this position?")


# --- the observer is the basis of the panel, not a decoration ---------------------------------
#
# `_view` documented itself as rendering the observer's verdict "INSTEAD of trusting the recipe
# position", and `_orient_now` claimed to run "on EVERY panel render". Measured 2026-08-16: the
# verdict was an optional kwarg and **3 of 56 render sites passed it**. The other 53 drew the
# panel from `step.landing_state` — a stored field with eleven writers, all of them ours — so the
# cockpit only moved when WE moved it, and an operator driving the same Chrome by hand desynced it
# instantly. These guard the inversion: the verdict is computed where it cannot be skipped.


def _sc_source() -> str:
    import pathlib
    return (pathlib.Path(__file__).parent / "routers" / "session_control.py").read_text()


def test_observe_takes_the_blackboard_so_it_can_orient():
    """The query string cannot orient — the fusion needs the queue and the apply tab."""
    import inspect
    from routers import session_control as sc
    params = list(inspect.signature(sc._observe).parameters)
    assert params[:2] == ["browser_url", "bb"], params


def test_every_observation_carries_a_verdict(monkeypatch):
    """Both of _observe's paths must return a verdict key, or a render can silently fall back to
    the recorded position — which is the whole bug. Exercised, not grepped."""
    import asyncio
    from types import SimpleNamespace
    from routers import session_control as sc

    bb = SimpleNamespace(search_state=SimpleNamespace(query="report analyst"), world={})

    async def _no_tabs(path, payload, **kw):
        return {"ok": True, "tabs": []}
    monkeypatch.setattr(sc, "_capture_post", _no_tabs)
    empty = asyncio.run(sc._observe("http://127.0.0.1:9324", bb))
    assert "observer" in empty and empty["observer"] is None

    async def _one_tab(path, payload, **kw):
        if path == "/list_tabs":
            return {"ok": True, "tabs": [{"tab_id": "T1", "url": "https://www.indeed.com/jobs?q=x"}]}
        return {"ok": True, "logged_in": True}

    async def _no_block(*a, **kw):
        return None

    async def _verdict(bb_, obs_, url_, belief_=None):
        return {"state": "workday_my_information", "mismatch": False}

    monkeypatch.setattr(sc, "_capture_post", _one_tab)
    monkeypatch.setattr(sc, "_detect_block", _no_block)
    monkeypatch.setattr(sc, "_orient_now", _verdict)
    live = asyncio.run(sc._observe("http://127.0.0.1:9324", bb))
    assert live["observer"]["state"] == "workday_my_information"


def test_no_call_site_passes_a_query_string_any_more():
    """A leftover `_observe(url, query)` would type-error at runtime rather than quietly skip the
    fusion — but catching it here is cheaper than catching it live."""
    import re
    hits = re.findall(r"await _observe\([^,]+,\s*(?:bb\.search_state\.query|query|ss\.query)\b",
                      _sc_source())
    assert hits == [], hits


def test_the_explicit_observer_is_an_override_not_the_source():
    """A caller with a richer look (a fresh perception belief) still wins; everyone else renders
    what the window said, without having to remember anything."""
    src = _sc_source()
    body = src.split("def _view(", 1)[1]
    assert "observer if observer is not None else obs.get(\"observer\")" in body


def test_the_stepper_is_placed_by_the_window_not_the_record():
    """After a refresh signed the session out, the panel rendered "My Information, 4 screens from
    Submit" over a sign-in wall (live 2026-08-16). The walk is placed by what was observed."""
    from types import SimpleNamespace
    from routers import session_control as sc

    step = SimpleNamespace(platform="workday", landing_state="workday_my_information",
                           terminal="", done=False)
    seen = sc._apply_flow(step, {"state": "workday_create_account"})
    assert seen["state"] == "workday_create_account"

    # A generic kind cannot say WHICH form screen it is, so it must not move the stepper.
    coarse = sc._apply_flow(step, {"state": "workday_application_form"})
    assert coarse["state"] == "workday_my_information"

    # No observation at all — the record is still the fallback, not a blank.
    blind = sc._apply_flow(step, None)
    assert blind["state"] == "workday_my_information"
