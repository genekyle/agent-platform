"""The transition corpus as the training spine (2026-08-09 refocus).

A teacher label on a transition row used to reach exactly one consumer — the planner's edge
table — while the perception witnesses stayed frozen on a DB corpus that stopped growing
2026-07-30. That is the cold-start circle: labels can't accrue because the witnesses are
uncertain because labels never reach them. These tests pin the way out: one labeled transition
is TWO witness training rows, and they enter through `perception.dataset`.
"""

from __future__ import annotations

import json

import pytest

from perception import dataset


def _labeled_row(*, before_artifact="a1.json", after_artifact="a2.json",
                 before_shot=None, after_shot=None,
                 before_state="indeed_serp", after_state="indeed_apply_contact"):
    return {
        "ts": "2026-08-09T00:00:00+00:00", "index": 0, "rung": "open_pane",
        "before": {"ts": "t0", "ok": True, "url": "https://indeed.test/serp",
                   "artifact": before_artifact, "screenshot": before_shot},
        "after": {"ts": "t1", "ok": True, "url": "https://indeed.test/apply",
                  "artifact": after_artifact, "screenshot": after_shot},
        "verdict": "confirmed",
        "teacher_correction": {"by": "claude-teacher", "note": "watched it land",
                               "before_state": before_state, "after_state": after_state},
    }


@pytest.fixture()
def spine(tmp_path, monkeypatch):
    (tmp_path / "transitions").mkdir()
    (tmp_path / "observer-traces").mkdir()
    (tmp_path / "observer-screenshots").mkdir()
    monkeypatch.setattr(dataset, "artifacts_root", lambda: tmp_path)
    return tmp_path


def test_a_teacher_label_becomes_two_witness_rows(spine):
    for name in ("a1.json", "a2.json"):
        (spine / "observer-traces" / name).write_text("{}")
    (spine / "observer-screenshots" / "s1.png").write_bytes(b"png")
    (spine / "observer-screenshots" / "s2.png").write_bytes(b"png")
    row = _labeled_row(before_shot=str(spine / "observer-screenshots" / "s1.png"),
                       # the stored absolute path has rotted; only the filename survives —
                       # resolution must fall back exactly as the stale-path rule says
                       after_shot="/renamed/away/observer-screenshots/s2.png")
    (spine / "transitions" / "session_7.jsonl").write_text(json.dumps(row) + "\n")

    rows = dataset.transition_label_rows()
    assert [(r.state, r.filename) for r in rows] == [
        ("indeed_serp", "a1.json"), ("indeed_apply_contact", "a2.json")]
    assert rows[0].screenshot_path == spine / "observer-screenshots" / "s1.png"
    assert rows[1].screenshot_path == spine / "observer-screenshots" / "s2.png"
    assert all(r.artifact_path is not None for r in rows)


def test_an_unlabeled_row_teaches_no_witness(spine):
    row = _labeled_row()
    del row["teacher_correction"]
    (spine / "transitions" / "session_8.jsonl").write_text(json.dumps(row) + "\n")
    assert dataset.transition_label_rows() == []


def test_a_shared_half_is_not_counted_twice(spine):
    """An after-half is routinely the next row's before-half (same artifact, same state) —
    one observation is one training example, however many rows it appears in."""
    (spine / "observer-traces" / "a1.json").write_text("{}")
    (spine / "observer-traces" / "a2.json").write_text("{}")
    r1 = _labeled_row()
    r2 = _labeled_row(before_artifact="a2.json", after_artifact="a1.json",
                      before_state="indeed_apply_contact", after_state="indeed_serp")
    r2["index"] = 1
    (spine / "transitions" / "session_9.jsonl").write_text(
        json.dumps(r1) + "\n" + json.dumps(r2) + "\n")

    rows = dataset.transition_label_rows()
    assert len(rows) == 2, "four halves, two distinct (artifact, state) observations"


def test_load_rows_folds_the_spine_in_and_censuses_it(spine, monkeypatch):
    """The DB corpus keeps working exactly as before; the transition labels ride in beside it,
    counted under their own census key so 'the corpus grew' names its source."""
    class _EmptyQuery:
        def filter(self, *a):
            return self

        def all(self):
            return []

    class _Session:
        def query(self, *a):
            return _EmptyQuery()

        def close(self):
            pass

    import db

    monkeypatch.setattr(db, "SessionLocal", lambda: _Session())
    (spine / "observer-traces" / "a1.json").write_text("{}")
    (spine / "observer-traces" / "a2.json").write_text("{}")
    (spine / "transitions" / "session_10.jsonl").write_text(json.dumps(_labeled_row()) + "\n")

    rows, census = dataset.load_rows()
    assert len(rows) == 2
    assert census["from_transitions"] == 2
    assert census["labeled"] == 2

    rows_off, _ = dataset.load_rows(include_transitions=False)
    assert rows_off == [], "the A/B flag really excludes the spine"


def test_a_teacher_label_supersedes_a_conflicting_db_label(spine, monkeypatch):
    """Same capture, different label: training both feeds the witness contradictory ground
    truth on exactly the states someone bothered to correct. The teacher who watched the
    drive wins; the census says so instead of hiding it."""
    class _Cap:
        artifact_filename = "a1.json"
        observed_page_state = "indeed_home_logged_out"
        screenshot_refs = []
        url = "https://indeed.test/old"
        domain_id = "career_search"

    class _Query:
        def filter(self, *a):
            return self

        def all(self):
            return [_Cap()]

    class _Session:
        def query(self, *a):
            return _Query()

        def close(self):
            pass

    import db

    monkeypatch.setattr(db, "SessionLocal", lambda: _Session())
    (spine / "observer-traces" / "a1.json").write_text("{}")
    (spine / "observer-traces" / "a2.json").write_text("{}")
    (spine / "transitions" / "session_11.jsonl").write_text(
        json.dumps(_labeled_row(before_state="indeed_search_results")) + "\n")

    rows, census = dataset.load_rows()
    states_for_a1 = [r.state for r in rows if r.filename == "a1.json"]
    assert states_for_a1 == ["indeed_search_results"], "the teacher's label replaced the DB's"
    assert census["superseded_by_teacher"] == 1


def _belief(state, unc=0.2):
    return {"state": state, "uncertainty": {"state": unc}}


def _confirmed_row(**kw):
    """A confirmed, teacher-untouched row whose witnesses agreed — the self-supervision shape."""
    row = _labeled_row(**{k: v for k, v in kw.items() if k not in ("b_unc", "a_unc", "verdict")})
    del row["teacher_correction"]
    row["verdict"] = kw.get("verdict", "confirmed")
    row["before"]["belief"] = _belief("indeed_serp", kw.get("b_unc", 0.2))
    row["after"]["belief"] = _belief("indeed_apply_contact", kw.get("a_unc", 0.2))
    return row


def test_a_confirmed_row_with_agreeing_witnesses_labels_itself(spine):
    """The 2026-08-20 finding: 146 confirmed rows sat beside 16 teacher labels and the trainer
    read only the teacher's. Declared expectation + observed world + witness agreement is three
    sources concurring; that row is a label nobody had to write — tagged `self`, never `teacher`,
    so the two corpora stay separable."""
    for name in ("a1.json", "a2.json"):
        (spine / "observer-traces" / name).write_text("{}")
    (spine / "transitions" / "session_9.jsonl").write_text(json.dumps(_confirmed_row()) + "\n")

    rows = dataset.transition_label_rows()
    assert [(r.state, r.filename, r.label_source) for r in rows] == [
        ("indeed_serp", "a1.json", "self"), ("indeed_apply_contact", "a2.json", "self")]


def test_only_a_confirmed_verdict_may_self_label(spine):
    """`mismatch` is the world disagreeing — the one row that must never label itself — and
    `read_only` declared no expectation, so there is no third witness to concur."""
    rows = [_confirmed_row(verdict="mismatch"),
            _confirmed_row(verdict="read_only"),
            _confirmed_row(verdict="unobserved")]
    (spine / "transitions" / "session_10.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    assert dataset.transition_label_rows() == []


def test_split_witnesses_do_not_self_label(spine):
    """The observer clamps a witness split to uncertainty >= 0.5; such a half stays unlabeled."""
    (spine / "transitions" / "session_11.jsonl").write_text(
        json.dumps(_confirmed_row(b_unc=0.5, a_unc=0.5)) + "\n")
    assert dataset.transition_label_rows() == []


def test_the_teacher_owns_any_artifact_it_ever_spoke_about(spine):
    """A correction is a disagreement with the belief by construction. If the teacher called this
    artifact something else on ANY row, the belief's claim on it must not train — feeding both
    sides of a settled argument is how a witness unlearns its corrections."""
    taught = _labeled_row(before_state="indeed_home", after_state="indeed_serp")
    # The same after-artifact appears in a later confirmed row whose belief disagrees.
    disputed = _confirmed_row(before_artifact="a2.json", after_artifact="a3.json")
    disputed["before"]["belief"] = _belief("somewhere_else", 0.1)
    (spine / "transitions" / "session_12.jsonl").write_text(
        json.dumps(taught) + "\n" + json.dumps(disputed) + "\n")

    rows = dataset.transition_label_rows()
    by_file = {r.filename: r for r in rows}
    assert by_file["a2.json"].state == "indeed_serp"           # the teacher's word
    assert by_file["a2.json"].label_source == "teacher"
    assert by_file["a3.json"].label_source == "self"           # the untouched half still enters


def test_a_corrected_rows_beliefs_are_sub_judice(spine):
    """Even a half the correction did not rename: the teacher looked at this ROW, so its beliefs
    do not self-label."""
    row = _labeled_row()
    row["before"]["belief"] = _belief("indeed_serp", 0.1)
    row["after"]["belief"] = _belief("indeed_apply_contact", 0.1)
    row["teacher_correction"] = {"by": "claude-teacher", "note": "half a label",
                                 "before_state": "indeed_serp", "after_state": ""}
    (spine / "transitions" / "session_13.jsonl").write_text(json.dumps(row) + "\n")

    rows = dataset.transition_label_rows()
    assert [(r.filename, r.label_source) for r in rows] == [("a1.json", "teacher")]


def test_the_census_counts_self_supervision_separately(spine, monkeypatch):
    """A self-supervised corpus that cannot be A/B'd against the teacher-only one is a corpus
    you cannot argue with — `from_self_supervision` is the number that keeps them separable."""
    for name in ("a1.json", "a2.json"):
        (spine / "observer-traces" / name).write_text("{}")
    (spine / "transitions" / "session_14.jsonl").write_text(json.dumps(_confirmed_row()) + "\n")

    class _NoDB:
        def query(self, *a):
            class _Q:
                def filter(self, *a): return self
                def all(self): return []
            return _Q()
        def close(self): pass
    monkeypatch.setattr("db.SessionLocal", lambda: _NoDB())

    rows, census = dataset.load_rows()
    assert census["from_self_supervision"] == 2
    assert census["from_transitions"] == 2
    assert {r.label_source for r in rows} == {"self"}
