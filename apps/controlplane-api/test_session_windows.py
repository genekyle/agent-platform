"""The session window recorder — the temporal half `controller/window.py` deliberately lacks.

`window.survey` is pure over a SNAPSHOT: it names roles and finds duplicates. Every question the
SSO drive actually raised is about CHANGE — when did that window appear, what was on screen, is it
still there — and no snapshot can answer those. These pin the diff and the one property that makes
the record trustworthy: it sees a change whoever caused it.
"""

from __future__ import annotations

import pytest

import session_windows as sw


@pytest.fixture(autouse=True)
def _tmp_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "_path", lambda: tmp_path / "session_windows.json")


def _tabs(*pairs):
    return [{"tab_id": tid, "url": url} for tid, url in pairs]


def test_a_new_window_is_an_event_with_a_role():
    sw.record(1, _tabs(("a", "https://www.linkedin.com/jobs/")))
    events = sw.record(1, _tabs(("a", "https://www.linkedin.com/jobs/"),
                                ("b", "https://accounts.google.com/v3/signin/identifier")))
    assert [e["kind"] for e in events] == ["opened"]
    assert events[0]["tab_id"] == "b"
    assert events[0]["role"] == "errand"          # accounts.google.com is the errand role


def test_a_url_change_is_a_navigation_not_a_new_window():
    """Conflating them would invent a popup every time a page redirected — which on an SSO flow is
    constantly, and would make the record useless exactly where it is needed."""
    sw.record(2, _tabs(("a", "https://accounts.google.com/v3/signin/identifier")))
    events = sw.record(2, _tabs(("a", "https://accounts.google.com/v3/signin/challenge/pk")))
    assert [e["kind"] for e in events] == ["navigated"]
    assert events[0]["from_url"].endswith("identifier")
    assert sw.summarize(2)["windows_opened"] == 1     # the original, not a second


def test_a_closed_window_is_recorded_not_forgotten():
    sw.record(3, _tabs(("a", "https://x.test/"), ("b", "https://accounts.google.com/signin")))
    events = sw.record(3, _tabs(("a", "https://x.test/")))
    assert [e["kind"] for e in events] == ["closed"]
    assert events[0]["tab_id"] == "b"


def test_it_records_a_change_nobody_told_it_about():
    """THE POINT. The diff is not wired to our own actions, so it cannot be blind to the
    operator's: a window the human opened by hand is the same event as one we opened."""
    sw.record(4, _tabs(("a", "https://www.linkedin.com/jobs/")))
    events = sw.record(4, _tabs(("a", "https://www.linkedin.com/jobs/"),
                                ("human", "https://mail.google.com/mail/u/0")),
                       note="operator opened something", actor="operator")
    assert [e["kind"] for e in events] == ["opened"]
    assert events[0]["actor"] == "operator"
    assert events[0]["note"] == "operator opened something"


def test_an_unchanged_window_produces_no_noise():
    """A recorder that logs on every poll is a recorder nobody reads."""
    tabs = _tabs(("a", "https://x.test/"))
    sw.record(5, tabs)
    assert sw.record(5, tabs) == []
    assert sw.record(5, tabs) == []


def test_openers_answers_the_question_an_sso_flow_asks():
    sw.record(6, _tabs(("a", "https://www.linkedin.com/jobs/")))
    sw.record(6, _tabs(("a", "https://www.linkedin.com/jobs/"),
                       ("p", "https://accounts.google.com/gsi/select")))
    sw.record(6, _tabs(("a", "https://www.linkedin.com/jobs/")))     # popup closed again
    opened = sw.openers(6)
    assert [e["tab_id"] for e in opened] == ["a", "p"]
    # ...and the closure is still on the record, so "it was there and went" is answerable
    assert any(e["kind"] == "closed" and e["tab_id"] == "p" for e in sw.timeline(6))


def test_a_tab_without_an_id_is_dropped_rather_than_guessed_at():
    """It cannot be tracked across reads, and inventing an identity would produce a phantom
    open/close pair on every single poll."""
    assert sw._snapshot([{"url": "https://x.test/"}]) == {}


def test_the_ledger_keeps_a_tail_not_a_life():
    for i in range(sw.MAX_EVENTS_PER_SESSION + 40):
        sw.record(7, _tabs(("a", f"https://x.test/{i}")))
    assert len(sw.timeline(7, limit=10_000)) == sw.MAX_EVENTS_PER_SESSION


def test_recording_never_raises_into_a_drive():
    """Observation must not be the thing that breaks a drive."""
    import builtins
    sw.record(8, _tabs(("a", "https://x.test/")))
    orig = sw._save
    sw._save = lambda doc: (_ for _ in ()).throw(OSError("disk gone"))
    try:
        assert sw.record(8, _tabs(("a", "https://y.test/"))) == []
    finally:
        sw._save = orig
