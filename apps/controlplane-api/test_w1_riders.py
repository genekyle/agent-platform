"""The W1 riders (PLAN_inhouse_reasoner_v1 §4): geometry survives `as_row`, the snapshot
carries the censused controls, the decision journal fires its sinks, and a banked transition
row banks its vectors. Each pins the seam the 2026-08-31 recon measured as dropped."""
from __future__ import annotations

import os

from controller.bundle import build_bundle
from interaction.decision import Decision, replay_snapshot
from interaction import decision_journal
from step_runner import Observation

BBOX = {"x": 10.7, "y": 20.2, "width": 100.9, "height": 30.1}


# --- geometry lift -----------------------------------------------------------
def test_as_row_carries_geometry_aligned_with_candidates():
    obs = Observation(candidates=[
        {"role": "button", "name": "Apply", "bbox": BBOX},
        {"role": "link", "name": "Sign In", "bbox": None},
    ])
    row = obs.as_row()
    assert row["candidates"] == [("button", "Apply"), ("link", "Sign In")]  # readers untouched
    assert row["geometry"] == [[10, 20, 100, 30], None]                     # aligned, ints


def test_as_row_omits_geometry_when_scan_had_none():
    row = Observation(candidates=[{"role": "button", "name": "Apply"}]).as_row()
    assert "geometry" not in row   # honest: this look recorded no boxes, like historical rows


# --- the snapshot's censused controls ---------------------------------------
def test_replay_snapshot_carries_censused_controls_capped():
    cands = [{"role": "button", "name": f"Control {i}"} for i in range(80)]
    b = build_bundle("indeed_apply", "https://smartapply.indeed.com/questions/1",
                     goal_text="apply", ax_candidates=cands)
    snap = replay_snapshot(b)
    idents = snap["ax_identities"]
    assert idents and len(idents) <= 60                      # present, bounded
    assert any("control" in i.lower() for i in idents)       # role|name identities (digits
    assert any(i.startswith("button|") for i in idents)      # may be templated by normalizer)


# --- the decision journal's sinks -------------------------------------------
def test_decision_sink_fires_after_append(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    seen = []
    sink = seen.append
    decision_journal.register_decision_sink(sink)
    try:
        b = build_bundle("indeed_apply", "https://smartapply.indeed.com/questions/1",
                         goal_text="apply",
                         ax_candidates=[{"role": "button", "name": "Continue"}])
        d = Decision(intent="click", params={"ref": "Continue"}, confidence=0.9,
                     rung="teacher", rationale="the page offers exactly one way forward",
                     evidence=("button Continue",))
        rec = decision_journal.record_for(d, b, session_id="w1-test")
        out = decision_journal.log_decision(rec)
        assert out is not None
        assert seen and seen[0] is out and seen[0].ts        # fired once, after ts stamped
        assert seen[0].bundle_snapshot.get("ax_identities")  # the rider's food is on the row
    finally:
        decision_journal._sinks.remove(sink)


def test_failed_append_fires_no_sink(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERACTION_ARTIFACTS_DIR", str(tmp_path))
    seen = []
    decision_journal.register_decision_sink(seen.append)
    try:
        b = build_bundle("indeed_apply", "https://x.example/1", goal_text="apply")
        d = Decision(intent="observe", params={}, confidence=0.5, rung="recipe",
                     rationale="look first", evidence=())
        rec = decision_journal.record_for(d, b)
        rec.route = ""                                            # break the spine on purpose
        assert decision_journal.log_decision(rec) is None         # spine rule refused the row
        assert not seen                                           # no row, no sink
    finally:
        decision_journal._sinks.remove(seen.append)


# --- the transition rider banks vectors -------------------------------------
_ROW = {
    "session_id": "w1-test", "ts": "2026-09-02T00:00:00+00:00", "rung": "enter_apply",
    "verdict": "confirmed", "action": {"intent": "click", "control": "Apply", "ats": "workday"},
    "before": {
        "url": "https://acme.wd1.myworkdayjobs.com/jobs/1", "title": "Analyst",
        "candidates": [["button", "Apply"]],
        "belief": {"state": "workday_job_posting", "facets": {"platform": "workday"}},
        "artifact": "", "screenshot": "",
    },
    "after": {
        "url": "https://acme.wd1.myworkdayjobs.com/jobs/1/apply", "title": "Apply",
        "candidates": [["textbox", "Email"]],
        "belief": {"state": "workday_apply_method", "facets": {"platform": "workday"}},
        "artifact": "", "screenshot": "",
    },
    "teacher_correction": None,
}


def test_rider_banks_both_halves_into_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PRECEDENT_DATA_ROOT", str(tmp_path))
    from precedent import rider
    from precedent.store import VectorStore

    rider.on_transition_row(dict(_ROW))
    counts = VectorStore(tmp_path / "vectors.db").counts()
    assert counts.get("transition_before") == 1
    assert counts.get("transition_after") == 1
    rider.on_transition_row(dict(_ROW))   # idempotent on source_key
    counts = VectorStore(tmp_path / "vectors.db").counts()
    assert counts["total"] == 2


def test_rider_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PRECEDENT_DATA_ROOT", str(tmp_path))
    from settings import settings
    monkeypatch.setattr(settings, "precedent_write_vectors", False)
    from precedent import rider

    rider.on_transition_row(dict(_ROW))
    assert not os.path.exists(tmp_path / "vectors.db")


# --- the embedder reads the snapshot's controls ------------------------------
def test_doc_from_decision_reads_snapshot_controls():
    from precedent.embedder import doc_from_decision

    row = {
        "ts": "2026-09-02T00:00:00+00:00", "bundle_digest": "abc123456789",
        "intent": "click", "session_id": 34,
        "bundle_snapshot": {
            "goal_text": "apply", "state": "greenhouse_review",
            "url": "job-boards.greenhouse.io/{tenant}/jobs/{n}", "ats": "greenhouse",
            "unanswered": [], "ax_identities": ["button|Submit application", "checkbox|I agree"],
        },
    }
    doc = doc_from_decision(row)
    assert "controls:" in doc.text and "Submit application" in doc.text
