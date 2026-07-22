"""The live seam: perceive a running tab, and collect while doing it.

Two contracts under test, and the second one matters more than it looks: **perception is an aid,
never a dependency.** A missing witness, a dead capture server, or a screenshot that never
arrives must leave the drive running exactly as it ran before — otherwise wiring perception in
turns a working controller into a broken one on any machine that has not fitted a model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perception import live as perception_live


@pytest.fixture(autouse=True)
def _no_cached_observer():
    perception_live.reset_observer()
    yield
    perception_live.reset_observer()


# --- the shared featurizer ------------------------------------------------------------
def test_live_ax_candidates_become_the_artifact_shape_the_trainer_reads():
    """One featurizer, two callers. Two featurizers is how the corpus and the runtime quietly
    stop describing the same page."""
    artifact = perception_live.artifact_from_live(
        url="https://acme.wd5.myworkdayjobs.com/en-US/x", title="My Information",
        ax_candidates=[{"role": "textbox", "caption": "First Name"},
                       {"role": "button", "name": "Save and Continue"}])
    from perception.dom_witness import extract_tokens
    tokens = extract_tokens(artifact)
    assert "title:information" in tokens
    assert "role:textbox" in tokens
    assert "tok:continue" in tokens


def test_the_candidate_name_is_read_the_same_way_the_fingerprint_reads_it():
    """`caption or name` — matching `fingerprint.ax_summary`, so the witness and the fingerprint
    cannot disagree about what a control is called."""
    artifact = perception_live.artifact_from_live(
        ax_candidates=[{"role": "button", "caption": "Continue", "name": "ignored"},
                       {"role": "link", "name": "Sign in"}])
    labels = [c["target"]["label"] for c in artifact["ranked_candidates"]]
    assert labels == ["Continue", "Sign in"]


def test_a_candidate_with_neither_role_nor_name_is_dropped_not_blanked():
    artifact = perception_live.artifact_from_live(ax_candidates=[{}, {"role": "", "name": ""}])
    assert artifact["ranked_candidates"] == []


# --- perception is an aid, never a dependency -----------------------------------------
def test_sense_returns_none_when_nothing_is_promoted(monkeypatch):
    monkeypatch.setattr(perception_live, "observer", lambda: None)
    assert perception_live.sense(url="https://x.test/", page_text="hello") is None


def test_sense_swallows_a_broken_observer(monkeypatch):
    class _Exploding:
        def observe(self, *a, **k):
            raise RuntimeError("witness on fire")

    monkeypatch.setattr(perception_live, "observer", lambda: _Exploding())
    assert perception_live.sense(url="https://x.test/") is None


def test_sense_returns_a_serialized_belief(monkeypatch):
    from interaction.belief import BeliefState

    class _Fake:
        def observe(self, obs, prior=()):
            assert obs.artifact is not None      # the live surfaces made it through
            return BeliefState(state="workday_questions", agreement="agree",
                               uncertainty={"state": 0.1, "novelty": 0.2}, prior=tuple(prior))

    monkeypatch.setattr(perception_live, "observer", lambda: _Fake())
    belief = perception_live.sense(url="https://x.test/", prior=("workday_questions",))
    assert belief["state"] == "workday_questions"
    assert belief["uncertainty"]["state"] == 0.1


def test_sense_featurizes_the_real_capture_rather_than_rebuilding_one(monkeypatch):
    """The witnesses are fitted on `/capture` artifacts, so a turn that HAS one must perceive it.

    Live, 2026-07-22: the synthesized artifact scored cosine 0.2755 against the centroid of a
    state with 20 training examples, where a real capture of that state scores 0.82-0.86. The
    class-conditional novelty percentile therefore read 1.00 on every page, and since a novelty
    block grades RED, the controller could never act. One artifact shape, not two.
    """
    seen = {}

    class _Fake:
        def observe(self, obs, prior=()):
            from interaction.belief import BeliefState
            seen["artifact"] = obs.artifact
            return BeliefState(state="indeed_search_results", agreement="agree",
                               uncertainty={"state": 0.1, "novelty": 0.1})

    monkeypatch.setattr(perception_live, "observer", lambda: _Fake())
    real = {"acquisition": {"page_identity": {"url": "https://x.test/", "title": "Real"},
                            "actionable_elements": [{"role": "button", "name": "Continue",
                                                     "placeholder": "search here"}]},
            "ranked_candidates": [{"target": {"role": "button", "label": "Continue"}}]}
    perception_live.sense(url="https://x.test/", artifact=real,
                          ax_candidates=[{"role": "link", "name": "Synthesized"}])
    assert seen["artifact"] is real, "the capture was rebuilt instead of read"


def test_sense_still_synthesizes_when_the_capture_did_not_land(monkeypatch):
    """The fallback stays: a capture server that is down must not blind the drive."""
    seen = {}

    class _Fake:
        def observe(self, obs, prior=()):
            from interaction.belief import BeliefState
            seen["artifact"] = obs.artifact
            return BeliefState(state="x", agreement="one_sided", uncertainty={"state": 0.5})

    monkeypatch.setattr(perception_live, "observer", lambda: _Fake())
    perception_live.sense(url="https://x.test/", artifact=None, title="Indeed",
                          ax_candidates=[{"role": "link", "name": "Synthesized"}])
    acq = seen["artifact"]["acquisition"]
    assert acq["page_identity"]["title"] == "Indeed"
    assert acq["actionable_elements"][0]["name"] == "Synthesized"


def test_a_failed_capture_never_breaks_the_turn():
    def _boom(path, payload):
        raise RuntimeError("capture server down")

    captured = perception_live.capture_now(_boom, {"tab_id": "t"})
    assert captured.artifact is None and captured.screenshot is None


def test_capture_with_no_filename_is_none_not_a_guess():
    captured = perception_live.capture_now(lambda p, b: {}, {"tab_id": "t"})
    assert captured.artifact is None and captured.screenshot is None


# --- the screenshot is READ from the artifact, never guessed --------------------------
def _write_artifact(root: Path, name: str, screenshots: list[dict]) -> None:
    (root / "observer-traces").mkdir(parents=True, exist_ok=True)
    (root / "observer-traces" / name).write_text(
        json.dumps({"acquisition": {"screenshots": screenshots}}))


def test_screenshot_is_resolved_from_the_artifact_not_from_the_stem(tmp_path, monkeypatch):
    """The artifact is named for the capture timestamp and the screenshot for `now()` at write
    time, with a different suffix — so `<artifact stem>.png` names a file that does not exist."""
    monkeypatch.setattr("perception.dataset.artifacts_root", lambda: tmp_path)
    shots = tmp_path / "observer-screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    real = shots / "2026-07-22T10-00-00+00-00__controller_turn.png"
    real.write_bytes(b"png")
    _write_artifact(tmp_path, "2026-07-22T09-59-59+00-00__live__controller_turn.json",
                    [{"filename": real.name, "path": str(real)}])

    found = perception_live.screenshot_for_artifact(
        "2026-07-22T09-59-59+00-00__live__controller_turn.json")
    assert found == real


def test_a_stale_absolute_path_falls_back_to_the_filename(tmp_path, monkeypatch):
    """The 2026-07-22 finding: 101 rows point at `apps/mcp-mock/…`, a directory since renamed.
    The file is there; the pointer rotted."""
    monkeypatch.setattr("perception.dataset.artifacts_root", lambda: tmp_path)
    shots = tmp_path / "observer-screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    real = shots / "shot.png"
    real.write_bytes(b"png")
    _write_artifact(tmp_path, "a.json",
                    [{"filename": "shot.png", "path": "/gone/apps/mcp-mock/output/shot.png"}])
    assert perception_live.screenshot_for_artifact("a.json") == real


def test_a_missing_artifact_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr("perception.dataset.artifacts_root", lambda: tmp_path)
    assert perception_live.screenshot_for_artifact("nope.json") is None


# --- the field set: reachable now, and inert until the corpus has it -------------------
def test_the_scan_reaches_the_capture_so_the_corpus_can_learn_the_phase():
    """The signal that separates two form phases of the same ATS is which controls the form
    REQUIRES — and it reached the live Bundle and stopped there, which is why every remaining
    state error is intra-platform confusion the corpus cannot be taught out of."""
    sent = {}

    def _post(path, payload):
        sent[path] = payload
        return {"filename": ""}

    perception_live.capture_now(_post, {"tab_id": "t"},
                                form_state={"unanswered": [{"field": "Sponsorship"}]})
    assert sent["/capture"]["form_state"] == {"unanswered": [{"field": "Sponsorship"}]}


def test_no_scan_means_no_key_rather_than_an_empty_one():
    sent = {}
    perception_live.capture_now(lambda p, b: sent.setdefault(p, b) or {"filename": ""},
                                {"tab_id": "t"})
    assert "form_state" not in sent["/capture"]


def test_the_field_set_becomes_its_own_namespace_and_outweighs_page_furniture():
    from perception.dom_witness import extract_tokens
    artifact = {
        "acquisition": {
            "page_identity": {"url": "https://acme.wd5.myworkdayjobs.com/x", "title": "Workday"},
            "actionable_elements": [{"role": "link", "name": "Careers"}] * 5,
            "form_state": {"unanswered": [
                {"field": "Are you legally authorized to work?", "kind": "radio_group"},
                {"field": "Desired salary", "kind": "text"},
            ]},
        },
        "ranked_candidates": [],
    }
    toks = extract_tokens(artifact)
    assert toks.count("field:salary") == 3          # weighted, so a few fields beat the chrome
    assert "fieldkind:radio_group" in toks
    assert "field:authorized" in toks


def test_a_capture_without_a_field_set_is_byte_identical_to_before():
    """v4 must be inert on the 174 captures written before it, or every existing row silently
    changes meaning — the mid-corpus feature drift the version marker exists to prevent."""
    from perception.dom_witness import extract_tokens
    artifact = {"acquisition": {"page_identity": {"url": "https://x.test/", "title": "T"},
                                "actionable_elements": [{"role": "button", "name": "Continue"}]},
                "ranked_candidates": []}
    toks = extract_tokens(artifact)
    assert not any(t.startswith("field:") or t.startswith("fieldkind:") for t in toks)
