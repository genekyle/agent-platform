"""The StepRunner's invariants (PLAN_step_runner.md).

The one-line law: the verifier only ever DEMOTES a claimed success against the observed world —
it never promotes a failure, and it never challenges a claim it could not observe. A blind
verifier that blocks is worse than no verifier, and a verifier that invents successes is the
disease this module exists to cure.
"""

from __future__ import annotations

import json

import step_runner as sr


def _obs(*, ok=True, url="", tabs=(), candidates=(), ax_count=None):
    o = sr.Observation(ts="t", ok=ok, url=url)
    o.tabs = [{"tab_id": tid, "url": u} for tid, u in tabs]
    o.candidates = [{"role": r, "name": n} for r, n in candidates]
    o.ax_count = ax_count if ax_count is not None else len(o.candidates)
    return o


# --- diff -------------------------------------------------------------------------------------

def test_a_blind_side_yields_no_diff_at_all():
    # A diff against a failed observation is not evidence — pretending it is lets a dead tab
    # veto a good rung, the exact inversion of the open_pane bug.
    assert sr.diff(_obs(ok=False), _obs()) is None
    assert sr.diff(_obs(), _obs(ok=False)) is None


def test_the_diff_names_what_moved():
    before = _obs(url="https://a.test/1", tabs=[("t1", "https://a.test/1")],
                  candidates=[("button", "Apply now")])
    after = _obs(url="https://a.test/1?vjk=x", tabs=[("t1", "https://a.test/1?vjk=x"),
                                                     ("t2", "https://ats.test/form")],
                 candidates=[("button", "Submit")])
    d = sr.diff(before, after)
    assert d["url_changed"] is True
    assert d["tabs_opened"] == [{"tab_id": "t2", "url": "https://ats.test/form"}]
    assert d["tabs_navigated"][0]["to"] == "https://a.test/1?vjk=x"
    assert "('button', 'Submit')" in d["elements_added"][0]
    assert "('button', 'Apply now')" in d["elements_removed"][0]


# --- verify -----------------------------------------------------------------------------------

def test_open_pane_confirms_when_the_window_carries_the_id():
    ex = sr.expectation_for("open_pane", external_id="abc123")
    after = _obs(url="https://www.indeed.com/jobs?q=x&vjk=abc123")
    verdict, why = sr.verify(ex, sr.diff(_obs(url="https://www.indeed.com/jobs?q=x"), after), after)
    assert verdict == sr.CONFIRMED and "vjk=abc123" in why


def test_open_pane_mismatches_when_the_world_never_moved():
    # The rung's own reply said ok; the URL still names no job. This is the claim the world gets
    # to overrule — the whole reason the StepRunner exists.
    ex = sr.expectation_for("open_pane", external_id="abc123")
    after = _obs(url="https://www.indeed.com/jobs?q=x")
    verdict, why = sr.verify(ex, sr.diff(_obs(url="https://www.indeed.com/jobs?q=x"), after), after)
    assert verdict == sr.MISMATCH and "found it nowhere" in why


def test_a_blind_observation_challenges_nothing():
    # Faked capture servers and dead tabs both look like this: no URLs anywhere. The claim stands.
    ex = sr.expectation_for("open_pane", external_id="abc123")
    assert sr.verify(ex, None, _obs())[0] == sr.UNOBSERVED
    assert sr.verify(ex, sr.diff(_obs(), _obs()), _obs())[0] == sr.UNOBSERVED


def test_enter_apply_confirms_on_a_new_application_tab():
    ex = sr.expectation_for("enter_apply")
    before = _obs(url="https://www.indeed.com/jobs", tabs=[("t1", "https://www.indeed.com/jobs")])
    after = _obs(url="https://www.indeed.com/jobs",
                 tabs=[("t1", "https://www.indeed.com/jobs"),
                       ("t2", "https://smartapply.indeed.com/beta/indeedapply/form/contact-info")])
    verdict, why = sr.verify(ex, sr.diff(before, after), after)
    assert verdict == sr.CONFIRMED and "smartapply" in why


def test_enter_apply_mismatches_when_the_click_left_the_window_unchanged():
    ex = sr.expectation_for("enter_apply")
    same = [("t1", "https://www.indeed.com/jobs")]
    verdict, why = sr.verify(ex, sr.diff(_obs(tabs=same), _obs(tabs=same)), _obs(tabs=same))
    assert verdict == sr.MISMATCH and "unchanged" in why


def test_read_only_rungs_are_paired_but_never_judged():
    for rung in ("verify_identity", "classify", "account"):
        assert sr.expectation_for(rung).kind == "read_only"
    assert sr.verify(sr.Expectation(kind="read_only"), None, _obs())[0] == sr.READ_ONLY


# --- the corpus row ---------------------------------------------------------------------------

def test_the_transition_row_is_the_full_training_row(tmp_path, monkeypatch):
    # before · evidence · action · expected · after · changes · verdict · claim · correction —
    # the core training row, not a screenshot in a folder.
    monkeypatch.setattr(sr, "_transitions_dir", lambda: tmp_path)
    before, after = _obs(url="https://a.test/1"), _obs(url="https://a.test/1?vjk=x")
    ex = sr.expectation_for("open_pane", external_id="x")
    path = sr.record_transition(session_id=99, rung_id="open_pane",
                                action={"rung": "open_pane", "job_id": "indeed:x"},
                                expect=ex, before=before, after=after,
                                changes=sr.diff(before, after),
                                verdict=sr.CONFIRMED, evidence="the window carries vjk=x",
                                claimed="ok")
    row = json.loads(path.read_text().splitlines()[-1])
    for key in ("before", "after", "changes", "expected", "action", "verdict", "evidence",
                "claimed", "teacher_correction"):
        assert key in row
    assert row["teacher_correction"] is None            # null until a teacher overrides
    assert row["before"]["belief"] is None or isinstance(row["before"]["belief"], dict)
    assert sr.read_transitions(99)[-1]["verdict"] == "confirmed"


def test_submit_is_the_irreversible_one():
    assert "submit" in sr.IRREVERSIBLE_RUNGS
    assert "open_pane" not in sr.IRREVERSIBLE_RUNGS


# --- the generalised expectations (the StepRunner spread, 2026-08-03) --------------------------

def test_content_changed_confirms_on_any_movement_and_demotes_a_frozen_world():
    ex = sr.Expectation(kind="content_changed")
    a = _obs(url="https://a.test/1", candidates=[("button", "Next")])
    b_moved = _obs(url="https://a.test/1", candidates=[("textbox", "Password")])
    assert sr.verify(ex, sr.diff(a, b_moved), b_moved)[0] == sr.CONFIRMED
    b_same = _obs(url="https://a.test/1", candidates=[("button", "Next")])
    verdict, why = sr.verify(ex, sr.diff(a, b_same), b_same)
    assert verdict == sr.MISMATCH and "nothing observable changed" in why
    # Blind on one side: the claim stands — a dead probe never vetoes a rung.
    assert sr.verify(ex, None, b_same)[0] == sr.UNOBSERVED


def test_url_value_is_encoding_tolerant():
    # 'data warehouse' rides as data+warehouse or data%20warehouse depending on the engine, and
    # demoting a landed query over percent-encoding would reopen the one CONSUMING rung.
    ex = sr.Expectation(kind="url_value", value="data warehouse")
    for landed in ("https://www.indeed.com/jobs?q=data+warehouse&l=Boston",
                   "https://www.linkedin.com/jobs/search-results/?keywords=data%20warehouse"):
        after = _obs(url=landed)
        assert sr.verify(ex, sr.diff(_obs(url="x://y"), after), after)[0] == sr.CONFIRMED
    wrong = _obs(url="https://www.indeed.com/jobs?q=&l=Boston")
    assert sr.verify(ex, sr.diff(_obs(url="x://y"), wrong), wrong)[0] == sr.MISMATCH


def test_unmodeled_is_declared_blindness_not_a_judgement():
    # A step that changes the world with no measured postcondition records `unobserved` — never
    # a guess in either direction, even when the diff shows movement.
    ex = sr.Expectation(kind="unmodeled")
    a, b = _obs(url="https://a.test/1"), _obs(url="https://a.test/2")
    verdict, why = sr.verify(ex, sr.diff(a, b), b)
    assert verdict == sr.UNOBSERVED and "no measured postcondition" in why


def test_the_checkpoint_ladder_declares_only_what_it_measured():
    assert sr.expectation_for_checkpoint("run_query", query="q").kind == "url_value"
    assert sr.expectation_for_checkpoint("probe_browser").kind == "read_only"
    assert sr.expectation_for_checkpoint("review_page").kind == "read_only"
    # auth_probe and set_distance genuinely vary per engine — unmodeled, not invented.
    assert sr.expectation_for_checkpoint("auth_probe").kind == "unmodeled"
    assert sr.expectation_for_checkpoint("set_distance").kind == "unmodeled"


def test_default_claimed_reads_both_reply_shapes_and_refuses_the_rest():
    assert sr.default_claimed({"outcome": "ok"}) == "ok"
    assert sr.default_claimed({"outcome": "committed_unconfirmed"}) == "ok"
    assert sr.default_claimed({"outcome": "not_found"}) == "failed"
    assert sr.default_claimed({"ok": True}) == "ok"
    assert sr.default_claimed({"ok": False}) == "failed"
    # A 422 body, an empty dict, a None — not results, not claims (the sailed-through lesson).
    assert sr.default_claimed({"detail": [{"loc": "..."}]}) == "none"
    assert sr.default_claimed(None) == "none"


# --- run_step: the whole sequence as one call --------------------------------------------------

class _FakeCapture:
    """A capture server whose page moves when the act flips it — a world, not a script."""

    def __init__(self):
        self.moved = False
        self.calls = []

    async def __call__(self, path, payload, timeout=30.0):
        self.calls.append(path)
        if path == "/list_tabs":
            url = "https://a.test/2" if self.moved else "https://a.test/1"
            return {"ok": True, "tabs": [{"tab_id": "t1", "url": url}]}
        if path == "/ax_scan":
            name = "After" if self.moved else "Before"
            return {"ok": True, "candidates": [{"role": "button", "name": name}],
                    "target_url": "https://a.test/2" if self.moved else "https://a.test/1"}
        if path == "/capture":
            return {"ok": True, "filename": "cap.json"}
        return {"ok": True}


def test_run_step_observes_acts_verifies_and_records(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(sr, "_transitions_dir", lambda: tmp_path)
    cap = _FakeCapture()

    async def _act():
        cap.moved = True
        return {"outcome": "ok"}

    report = asyncio.run(sr.run_step(
        _act, action={"action": "demo"}, expect=sr.Expectation(kind="content_changed"),
        capture_post=cap, browser_url="http://b", tab_id="t1", session_id=77, rung_id="demo"))
    assert report.claimed == "ok" and report.verdict == sr.CONFIRMED
    assert not report.demotes
    row = sr.read_transitions(77)[-1]
    assert row["rung"] == "demo" and row["verdict"] == sr.CONFIRMED and row["claimed"] == "ok"


def test_run_step_demotes_a_claimed_ok_over_a_world_that_never_moved(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(sr, "_transitions_dir", lambda: tmp_path)
    cap = _FakeCapture()          # never flips: the click lands on nothing

    async def _act():
        return {"outcome": "ok"}

    report = asyncio.run(sr.run_step(
        _act, action={"action": "demo"}, expect=sr.Expectation(kind="content_changed"),
        capture_post=cap, browser_url="http://b", tab_id="t1", session_id=78, rung_id="demo"))
    assert report.demotes and report.verdict == sr.MISMATCH


def test_run_step_collect_false_is_the_credential_posture(tmp_path, monkeypatch):
    # The §4 rule made mechanical: identity-only looks — /capture is never called, no artifact
    # and no screenshot land in the row, and the belief may still ride on the DOM witness.
    import asyncio
    monkeypatch.setattr(sr, "_transitions_dir", lambda: tmp_path)
    cap = _FakeCapture()

    async def _act():
        cap.moved = True
        return {"outcome": "ok"}

    report = asyncio.run(sr.run_step(
        _act, action={"action": "login"}, expect=sr.Expectation(kind="content_changed"),
        capture_post=cap, browser_url="http://b", tab_id="t1", session_id=79,
        rung_id="login", collect=False))
    assert "/capture" not in cap.calls
    assert report.before.artifact is None and report.before.screenshot is None
    row = sr.read_transitions(79)[-1]
    assert row["before"]["artifact"] is None and row["after"]["artifact"] is None
