"""Tests for the job-search target store — the persisted (query, location) list.

Pure/no-network: each test isolates the JSON file under tmp_path so the seed-on-first-use
and dedupe behaviour is exercised without touching the real cache."""

import json

import job_search_targets as jst


def _isolate(tmp_path, monkeypatch):
    path = tmp_path / "job_search_targets.json"
    monkeypatch.setattr(jst, "_targets_path", lambda: path)
    return path


def test_seed_on_first_use(tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    targets = jst.load_targets()
    assert path.exists()
    assert any(t["query"] == "reporting analyst" and t["location"] == "Nashua, NH"
               for t in targets)


def test_add_target_persists(tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    jst.load_targets()  # seed
    row = jst.add_target("data analyst", "Manchester, NH")
    assert row["status"] == "active"
    on_disk = json.loads(path.read_text())
    assert any(t["query"] == "data analyst" for t in on_disk)


def test_add_target_floors_radius_at_50(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    jst.load_targets()
    low = jst.add_target("qa analyst", "Boston, MA", radius_miles=25)
    assert low["radius_miles"] == 50  # floored — every search is >=50mi
    high = jst.add_target("ml engineer", "Boston, MA", radius_miles=100)
    assert high["radius_miles"] == 100  # explicit larger radius is kept


def test_add_target_is_idempotent_case_insensitive(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    jst.load_targets()
    before = len(jst.load_targets())
    jst.add_target("Reporting Analyst", "nashua, nh")  # same as seed, different case/space
    assert len(jst.load_targets()) == before  # no duplicate row


def test_add_target_requires_query(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    try:
        jst.add_target("", "Nashua, NH")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_active_target_is_first_active(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    jst.load_targets()
    jst.add_target("paused one", "Nowhere", status="paused")
    active = jst.active_target()
    assert active is not None and active["query"] == "reporting analyst"
