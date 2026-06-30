"""Tests for the Indeed bounded auto-sweep + its helpers.

The sweep talks to a session Chrome and the capture server, neither of which exists in a unit test.
Both go through single seams (`_list_session_tabs`, `_capture_post`) plus the dependency-injected DB,
so the whole multi-page orchestration is exercised with fakes — no browser, no Postgres. The focus is
the SAFETY pre-gates (never sweep under a captcha / logged-out / sub-50mi) and the bounded page loop.
"""

import asyncio

import apply_state_store as store
import escalation_rules
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
