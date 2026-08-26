"""Tests for the Indeed bounded auto-sweep + its helpers.

The sweep talks to a session Chrome and the capture server, neither of which exists in a unit test.
Both go through single seams (`_list_session_tabs`, `_capture_post`) plus the dependency-injected DB,
so the whole multi-page orchestration is exercised with fakes — no browser, no Postgres. The focus is
the SAFETY pre-gates (never sweep under a captcha / logged-out / sub-50mi) and the bounded page loop.
"""

import asyncio

import apply_state_store as store
import escalation_rules
import search_cadence
import main
from db import get_db
from fastapi.testclient import TestClient
from models import ObservedJob

client = TestClient(main.app)


# --- pure helper: the shortlist filter --------------------------------------------------
def test_shortlist_matches_query_tokens_and_drops_applied():
    cards = [
        {"external_id": "a1", "title": "Senior Reporting Analyst", "company": "Acme"},
        {"external_id": "w1", "title": "Welder", "company": "Z Metals"},
        {"external_id": "a2", "title": "Reporting Analyst II", "company": "Globex"},
    ]
    # query shares a token with the two analyst roles, not the welder
    short = main._shortlist_jobs(cards, "reporting analyst", applied_keys=set())
    ids = {c["external_id"] for c in short}
    assert ids == {"a1", "a2"}

    # already-applied (cross-platform identity) is dropped even on a title match
    applied = {main._applied_key("Acme", "Reporting Analyst")}
    short2 = main._shortlist_jobs(cards, "reporting analyst", applied_keys=applied)
    assert {c["external_id"] for c in short2} == {"a2"}


def test_shortlist_empty_query_keeps_unapplied():
    cards = [{"external_id": "a1", "title": "Anything", "company": "C"}]
    assert len(main._shortlist_jobs(cards, "", applied_keys=set())) == 1


# --- fakes for the endpoint ------------------------------------------------------------
class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeSession:
    chrome_debug_port = 9222


class _FakeDB:
    """Minimal stand-in for the SQLAlchemy Session the sweep uses: TrainingSession lookup,
    ObservedJob get/add, a scalars() for the applied query, and a no-op commit."""

    def __init__(self, rows=None, applied=None):
        self.rows = rows or {}            # job_id -> object with .description/.salary/.apply_type
        self.applied = applied or []
        self.added = []

    def get(self, model, key):
        if model is main.TrainingSession:
            return _FakeSession()
        return self.rows.get(key)

    def add(self, row):
        self.added.append(row)

    def scalar(self, stmt):
        # The search layer asks for the active Search row; sweep tests never seed one, so the
        # honest answer is "none on file" — ensure_active_search then creates it via add/flush.
        return None

    def flush(self):
        for i, row in enumerate(self.added, start=1):
            if getattr(row, "id", None) is None:
                row.id = i

    def commit(self):
        pass

    def scalars(self, _stmt):
        return _FakeScalars(self.applied)


def _install(monkeypatch, *, tabs, block, capture, db, no_sleep=True):
    async def _tabs(_url):
        return tabs

    async def _refine(_url, b):
        return b

    async def _capture(path, payload, timeout=40.0):
        return capture(path, payload)

    monkeypatch.setattr(main, "_list_session_tabs", _tabs)
    monkeypatch.setattr(main, "_refine_block_visibility", _refine)
    monkeypatch.setattr(main, "_capture_post", _capture)
    monkeypatch.setattr(escalation_rules, "detect_block_frames", lambda urls: block)
    monkeypatch.setattr(store, "save", lambda bb: None)
    if no_sleep:
        async def _nosleep(*_a, **_k):
            return None
        monkeypatch.setattr(asyncio, "sleep", _nosleep)
    def _override_db():
        yield db
    main.app.dependency_overrides[get_db] = _override_db


def _teardown():
    main.app.dependency_overrides.pop(get_db, None)


# --- pre-gates: the bot-safety stops --------------------------------------------------
def test_sweep_stops_on_active_captcha(monkeypatch):
    _install(monkeypatch,
             tabs=[{"url": "https://www.indeed.com/jobs"}],
             block={"strength": "active", "provider": "recaptcha"},
             capture=lambda p, b: {"ok": True},
             db=_FakeDB())
    try:
        r = client.post("/api/search/sweep", json={"training_session_id": 1}).json()
    finally:
        _teardown()
    assert r["ok"] is False and r["stopped_reason"] == "captcha"
    assert r["pages_swept"] == 0


def test_sweep_stops_when_logged_out(monkeypatch):
    _install(monkeypatch,
             tabs=[{"url": "https://www.indeed.com/jobs"}],
             block=None,
             capture=lambda p, b: {"ok": True, "logged_in": False} if p == "/auth_state" else {"ok": True},
             db=_FakeDB())
    try:
        r = client.post("/api/search/sweep", json={"training_session_id": 1}).json()
    finally:
        _teardown()
    assert r["ok"] is False and r["stopped_reason"] == "not_authenticated"


def test_sweep_stops_when_distance_filter_fails(monkeypatch):
    def capture(path, _b):
        if path == "/auth_state":
            return {"ok": True, "logged_in": True}
        if path == "/set_distance":
            return {"ok": True, "applied": False}  # couldn't set >=50mi
        return {"ok": True}

    _install(monkeypatch, tabs=[{"url": "https://www.indeed.com/jobs"}], block=None,
             capture=capture, db=_FakeDB())
    try:
        r = client.post("/api/search/sweep", json={"training_session_id": 1}).json()
    finally:
        _teardown()
    assert r["ok"] is False and r["stopped_reason"] == "distance_filter_failed"


# --- the bounded page loop ------------------------------------------------------------
_CARDS = [
    {"external_id": "a1", "title": "Reporting Analyst", "company": "Acme", "location": "Nashua, NH"},
    {"external_id": "w9", "title": "Welder", "company": "Z", "location": "Nashua, NH"},
]


class _DescRow:
    """An already-present ObservedJob row stub (the shortlisted card) — carries both the
    upsert fields (re-seen bumps seen_count) and the detail fields the sweep fills in."""
    def __init__(self):
        self.title = "Reporting Analyst"
        self.company = "Acme"
        self.location = "Nashua, NH"
        self.seen_count = 1
        self.last_seen_at = None
        self.search_queries = []
        self.description = ""
        self.salary = None
        self.apply_type = None
        # Unresolved: the sweep syncs a fresh description up to the canonical Job, and a sighting
        # with no canonical key is the case that must no-op rather than raise.
        self.canonical_job_key = None
        self.application_platform = None


def _loop_capture(has_next):
    def capture(path, _b):
        if path == "/auth_state":
            return {"ok": True, "logged_in": True}
        if path == "/set_distance":
            return {"ok": True, "applied": True, "selected_miles": 50}
        if path == "/extract_jobs":
            return {"ok": True, "jobs": _CARDS}
        if path == "/open_job_card":
            return {"ok": True, "description": "A great reporting analyst role.",
                    "salary": "$70,000", "apply_type": "quick_apply"}
        if path == "/next_page":
            return {"ok": True, "has_next": has_next}
        return {"ok": True}
    return capture


def test_sweep_runs_to_max_pages_and_captures_descriptions(monkeypatch):
    db = _FakeDB(rows={"indeed:a1": _DescRow()})  # shortlisted card already exists, no description yet
    _install(monkeypatch, tabs=[{"url": "https://www.indeed.com/jobs"}], block=None,
             capture=_loop_capture(has_next=True), db=db)
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "query": "reporting analyst"}).json()
    finally:
        _teardown()
    import search_cadence
    assert r["ok"] is True
    assert r["stopped_reason"] == "max_pages"
    assert r["pages_swept"] == search_cadence.BOUNDS["max_pages_per_query"]
    assert r["distance_selected"] == 50
    # only the shortlisted (analyst, not welder) card is clicked into, and only once
    assert r["descriptions_captured"] == 1
    assert db.rows["indeed:a1"].description.startswith("A great reporting")


def test_sweep_stops_when_no_next_page(monkeypatch):
    db = _FakeDB(rows={"indeed:a1": _DescRow()})
    _install(monkeypatch, tabs=[{"url": "https://www.indeed.com/jobs"}], block=None,
             capture=_loop_capture(has_next=False), db=db)
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "query": "reporting analyst"}).json()
    finally:
        _teardown()
    assert r["ok"] is True
    assert r["stopped_reason"] == "no_next_page"
    assert r["pages_swept"] == 1


# --- the SECOND aggregator ---------------------------------------------------------------
# The sweep is one cadence over many engines. What must change per engine is small and exact: the
# tab it aims at, and the platform every row is tagged with. What must NOT change is everything
# else — the pre-gates, the bounds, the shortlist, the human pauses. These tests pin both halves.
def _recording_capture(has_next=False):
    """Same fake as _loop_capture, but it REMEMBERS which tab_url each call was aimed at — the
    thing that decides whether we drove LinkedIn or quietly drove Indeed."""
    seen: list[tuple] = []

    def capture(path, b):
        seen.append((path, (b or {}).get("tab_url")))
        if path == "/auth_state":
            return {"ok": True, "logged_in": True}
        if path == "/set_distance":
            return {"ok": True, "applied": True, "selected_miles": 50}
        if path == "/extract_jobs":
            return {"ok": True, "jobs": _CARDS}
        if path == "/open_job_card":
            return {"ok": True, "description": "A great reporting analyst role.",
                    "salary": "$70,000", "apply_type": "linkedin_easy_apply"}
        if path == "/next_page":
            return {"ok": True, "has_next": has_next}
        return {"ok": True}
    capture.seen = seen
    return capture


def test_sweep_drives_linkedin_and_tags_its_rows_linkedin(monkeypatch):
    db = _FakeDB(rows={"linkedin:a1": _DescRow()})   # note the PLATFORM-prefixed job id
    cap = _recording_capture()
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=cap, db=db)
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                              "query": "reporting analyst"}).json()
    finally:
        _teardown()
    assert r["ok"] is True
    assert r["platform"] == "linkedin" and r["domain_id"] == "linkedin_jobs"
    # The description landed on the LINKEDIN row. If the platform tag leaked back to "indeed" the
    # sweep would look at `indeed:a1`, find nothing, and silently capture zero descriptions.
    assert r["descriptions_captured"] == 1
    assert db.rows["linkedin:a1"].description.startswith("A great reporting")


def test_every_sweep_call_is_aimed_at_this_engines_tab(monkeypatch):
    """A bare host is not enough and the wrong host is a wrong answer: once an apply is in flight a
    session holds several tabs, and the readers are picked from whichever tab we point at. Every
    capture call in the sweep must carry THIS engine's search tab."""
    db = _FakeDB(rows={"linkedin:a1": _DescRow()})
    cap = _recording_capture()
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=cap, db=db)
    try:
        client.post("/api/search/sweep",
                    json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                          "query": "reporting analyst"})
    finally:
        _teardown()
    aimed = {path: tab for path, tab in cap.seen}
    # `/set_distance` is deliberately NOT in this list: LinkedIn exposes no distance control, so
    # the sweep skips the call entirely rather than aiming it anywhere (2026-07-30). Every call it
    # DOES make must still carry this engine's tab — which is what this test is actually for.
    for path in ("/auth_state", "/extract_jobs", "/open_job_card", "/next_page"):
        assert aimed.get(path) == "linkedin.com/jobs", f"{path} was aimed at {aimed.get(path)!r}"
    assert "/set_distance" not in aimed, "asked an engine with no distance filter to set one"


def test_indeed_sweep_is_unchanged_when_no_domain_is_named(monkeypatch):
    """Every existing caller sends no domain_id at all. It must still be an Indeed sweep, aimed at
    Indeed's tab, tagging rows `indeed:` — the whole point of the default."""
    db = _FakeDB(rows={"indeed:a1": _DescRow()})
    cap = _recording_capture()
    _install(monkeypatch, tabs=[{"url": "https://www.indeed.com/jobs"}], block=None,
             capture=cap, db=db)
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "query": "reporting analyst"}).json()
    finally:
        _teardown()
    assert r["platform"] == "indeed"
    assert r["descriptions_captured"] == 1
    assert {tab for _p, tab in cap.seen} == {"indeed.com/jobs"}


def test_the_bot_safety_pre_gates_apply_to_linkedin_too(monkeypatch):
    """The gates are about how we behave, not about whose site it is — a captcha stops a LinkedIn
    sweep exactly as it stops an Indeed one."""
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}],
             block={"strength": "active", "provider": "recaptcha"},
             capture=lambda p, b: {"ok": True}, db=_FakeDB())
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs"}).json()
    finally:
        _teardown()
    assert r["ok"] is False and r["stopped_reason"] == "captcha"


# --- the single-page-app problem ----------------------------------------------------------------
# On Indeed every consequential act navigates, and the navigation is the proof it landed. LinkedIn
# pushes `?start=` and re-renders in place, so a pause-then-extract can read the page we already
# recorded and upsert it as the next one — every row a duplicate, nothing raised, the corpus wrong.
# The sweep therefore takes a SIGNATURE of the results before paging and requires it to change.
def _spa_capture(*, changed, has_next=True):
    calls: list[str] = []

    def capture(path, b):
        calls.append(path)
        if path == "/auth_state":
            return {"ok": True, "logged_in": True}
        if path == "/set_distance":
            return {"ok": True, "applied": True, "selected_miles": 50}
        if path == "/extract_jobs":
            return {"ok": True, "jobs": _CARDS}
        if path == "/open_job_card":
            return {"ok": True, "description": "A great reporting analyst role.", "salary": ""}
        if path == "/results_signature":
            return {"ok": True, "signature": {"start": "0", "ids": ["a1", "w9"]}}
        if path == "/next_page":
            return {"ok": True, "has_next": has_next}
        if path == "/await_results":
            return {"ok": True, "changed": changed, "settled": changed}
        return {"ok": True}
    capture.calls = calls
    return capture


def test_a_spa_sweep_stops_rather_than_re_reading_a_page_that_never_changed(monkeypatch):
    """The silent-duplicate bug, made loud. `has_next` was true and the click dispatched fine — but
    the list underneath never changed, so extracting again would record page 1 twice."""
    cap = _spa_capture(changed=False)
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=cap, db=_FakeDB(rows={"linkedin:a1": _DescRow()}))
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                              "query": "reporting analyst"}).json()
    finally:
        _teardown()
    assert r["stopped_reason"] == "page_did_not_advance"
    assert r["pages_swept"] == 1          # the page it DID read, counted once


def test_a_spa_sweep_walks_on_once_the_results_actually_change(monkeypatch):
    cap = _spa_capture(changed=True)
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=cap, db=_FakeDB(rows={"linkedin:a1": _DescRow()}))
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                              "query": "reporting analyst"}).json()
    finally:
        _teardown()
    import search_cadence
    assert r["ok"] is True
    assert r["pages_swept"] == search_cadence.BOUNDS["max_pages_per_query"]


def test_indeed_does_not_pay_for_the_spa_check(monkeypatch):
    """Indeed navigates, so it needs neither call — and adding a per-page round trip to a path that
    does not need one is a cost with no answer attached."""
    cap = _spa_capture(changed=True)
    _install(monkeypatch, tabs=[{"url": "https://www.indeed.com/jobs"}], block=None,
             capture=cap, db=_FakeDB(rows={"indeed:a1": _DescRow()}))
    try:
        client.post("/api/search/sweep",
                    json={"training_session_id": 1, "query": "reporting analyst"})
    finally:
        _teardown()
    assert "/results_signature" not in cap.calls
    assert "/await_results" not in cap.calls


# --- WHICH cards get opened is the engine's call ------------------------------------------
# Operator-directed 2026-07-30: on LinkedIn the traversal IS the search — scroll the list, open
# every card, record each — because the list is virtualised, so a card not opened is a result we
# never really saw. Indeed keeps the keyword shortlist: its cards are all in the DOM already, so a
# detail read there is a click we CHOOSE to spend. One sweep, two lists.
def _openers_capture():
    """Records the external_id of every card the sweep clicks into."""
    opened: list[str] = []

    def capture(path, b):
        if path == "/auth_state":
            return {"ok": True, "logged_in": True}
        if path == "/set_distance":
            return {"ok": True, "applied": True, "selected_miles": 50}
        if path == "/extract_jobs":
            return {"ok": True, "jobs": _CARDS,
                    "meta": {"scroll": {"batches": 3, "moved": 2100}}}
        if path == "/open_job_card":
            opened.append((b or {}).get("external_id"))
            return {"ok": True, "description": "desc", "salary": "", "apply_type": ""}
        if path == "/next_page":
            return {"ok": True, "has_next": False}
        return {"ok": True}
    capture.opened = opened
    return capture


def _both_rows(prefix):
    return _FakeDB(rows={f"{prefix}:a1": _DescRow(), f"{prefix}:w9": _DescRow()})


def test_linkedin_opens_every_card_not_just_the_query_matches(monkeypatch):
    cap = _openers_capture()
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=cap, db=_both_rows("linkedin"))
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                              "query": "reporting analyst"}).json()
    finally:
        _teardown()
    # the welder does not match the query and is opened anyway — that is the point
    assert cap.opened == ["a1", "w9"]
    assert r["click_into"] == "every_card"
    # and what the scrolling did comes back with the summary, per page
    assert r["scroll"] and r["scroll"][0]["batches"] == 3


def test_indeed_still_opens_only_the_shortlist(monkeypatch):
    cap = _openers_capture()
    _install(monkeypatch, tabs=[{"url": "https://www.indeed.com/jobs"}], block=None,
             capture=cap, db=_both_rows("indeed"))
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "query": "reporting analyst"}).json()
    finally:
        _teardown()
    assert cap.opened == ["a1"]
    assert r["click_into"] == "shortlist"


def test_the_detail_cap_reports_what_it_dropped(monkeypatch):
    """A 25-card page asked to open every card, with max_details_per_page=1, reads as "25 found, 1
    description" — which looks like success. The cap has to say what it left."""
    cap = _openers_capture()
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=cap, db=_both_rows("linkedin"))
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                              "query": "reporting analyst", "max_details_per_page": 1}).json()
    finally:
        _teardown()
    assert cap.opened == ["a1"]
    assert r["details_skipped_by_cap"] == 1


def test_a_detail_read_that_FAILED_is_counted_and_explained(monkeypatch):
    """THE HALF-FAILED SWEEP (live 2026-08-26, session 34). Six of twelve `/open_job_card` calls
    came back `card <id> not found` — the list had scrolled past the row and the blind hunt walked
    the wrong way — and the run reported `descriptions_captured: 6` with nothing else. That number
    is indistinguishable from a cap doing its job, and `/open_job_card` is not journaled, so the
    swallow here was the only record that would ever have existed. A failure has to be counted
    separately from a card we chose not to open, and it has to carry the engine's own reason."""
    def capture(path, b):
        if path == "/auth_state":
            return {"ok": True, "logged_in": True}
        if path == "/extract_jobs":
            return {"ok": True, "jobs": _CARDS}
        if path == "/open_job_card":
            return {"ok": False, "detail": f"card {(b or {}).get('external_id')} not found "
                                           f"(no node for this id (not rendered yet?))"}
        if path == "/next_page":
            return {"ok": True, "has_next": False}
        return {"ok": True}

    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=capture, db=_both_rows("linkedin"))
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                              "query": "reporting analyst"}).json()
    finally:
        _teardown()
    assert r["descriptions_captured"] == 0
    assert r["details_failed"] == len(_CARDS)          # every attempt, not just the ones we saw
    assert r["details_skipped_by_cap"] == 0            # and NOT reported as a cap
    assert "not rendered yet" in r["detail_failures"][0]["detail"]
    assert r["detail_failures"][0]["page"] == 1


def test_a_sweep_does_not_stop_over_a_filter_the_engine_does_not_have(monkeypatch):
    """The distance gate is the FIRST thing the sweep does, so on LinkedIn it stopped the run
    before a single card was read — enforcing a 50-mile floor about a widget that does not exist
    there. Indeed still refuses to gather when its pill cannot be set; the floor is scoped, not
    retired."""
    cap = _openers_capture()
    _install(monkeypatch, tabs=[{"url": "https://www.linkedin.com/jobs/search"}], block=None,
             capture=cap, db=_both_rows("linkedin"))
    try:
        r = client.post("/api/search/sweep",
                        json={"training_session_id": 1, "domain_id": "linkedin_jobs",
                              "query": "reporting analyst"}).json()
    finally:
        _teardown()
    assert r["ok"] is True and r["stopped_reason"] != "distance_filter_failed"
    assert r["jobs_found"] == 2 and cap.opened == ["a1", "w9"]


def test_indeed_still_refuses_to_gather_when_its_distance_pill_fails(monkeypatch):
    """The scoping must not leak: an engine that HAS the control and cannot set it still stops."""
    def capture(path, _b):
        if path == "/auth_state":
            return {"ok": True, "logged_in": True}
        if path == "/set_distance":
            return {"ok": True, "applied": False}
        return {"ok": True}

    _install(monkeypatch, tabs=[{"url": "https://www.indeed.com/jobs"}], block=None,
             capture=capture, db=_FakeDB())
    try:
        r = client.post("/api/search/sweep", json={"training_session_id": 1}).json()
    finally:
        _teardown()
    assert r["ok"] is False and r["stopped_reason"] == "distance_filter_failed"


# --- naming: containment picks the wrong control (2026-08-14, live) ------------------------------

def test_the_submit_button_is_the_one_named_search_not_the_one_mentioning_it():
    """LIVE FAILURE, session #28: Indeed's results page with a detail pane open carries a button
    named "Return to Search Result". It CONTAINS "search", it sorts ahead of the real Search button
    in AX order, and it is not a submit — so `run_query` typed the query, clicked that, and could
    not commit. Third layer to learn the same rule in two days: a name that IS the words is the
    control; one that merely contains them is a coincidence."""
    controls = search_cadence.find_search_controls([
        {"role": "combobox", "name": "search: Job title, keywords, or company"},
        {"role": "combobox", "name": "location: City, state, zip code, or 'remote'"},
        {"role": "button", "name": "Return to Search Result"},
        {"role": "button", "name": "Search"},
    ])
    assert controls["submit"] == {"role": "button", "name": "Search"}
    assert controls["query"]["name"] == "search: Job title, keywords, or company"
    assert controls["location"]["name"] == "location: City, state, zip code, or 'remote'"


def test_a_leading_match_beats_a_buried_one_when_nothing_is_exact():
    """No exact "search" on the page: "Search jobs" is the control qualified, "Return to Search
    Result" is the coincidence. Leading is the tier that separates them."""
    controls = search_cadence.find_search_controls([
        {"role": "combobox", "name": "What: job title, keywords, or company"},
        {"role": "button", "name": "Return to Search Result"},
        {"role": "button", "name": "Search jobs"},
    ])
    assert controls["submit"]["name"] == "Search jobs"


def test_a_containing_match_is_still_taken_when_it_is_all_there_is():
    """The tiers rank, they do not filter — a page whose only submit buries the word is still
    driveable. Loosening the criteria is the fallback, never the first choice."""
    controls = search_cadence.find_search_controls([
        {"role": "combobox", "name": "What"},
        {"role": "button", "name": "Go and search now"},
    ])
    assert controls["submit"]["name"] == "Go and search now"


def test_the_more_specific_hint_still_wins_among_equals():
    """`_SUBMIT_HINTS` is ordered most-specific first, and ranking by tier must not throw that
    away: two exact matches are ordered by which hint they answered."""
    controls = search_cadence.find_search_controls([
        {"role": "combobox", "name": "What"},
        {"role": "button", "name": "Search"},
        {"role": "button", "name": "Find jobs"},
    ])
    assert controls["submit"]["name"] == "Find jobs"      # "find jobs" leads _SUBMIT_HINTS
