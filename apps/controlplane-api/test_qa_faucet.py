"""The screener Q→A faucet (§11 item 3): every answered question journals, sensitivity is
settled at the write, corrections keep both sides, the vector store banks the question, and
the no_option refusal's offered options parse back out of its prose."""
from __future__ import annotations

import qa_journal
from precedent.embedder import doc_from_qa
from routers.session_control import _options_from_detail


def _row(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("PRECEDENT_DATA_ROOT", str(tmp_path))
    defaults = dict(
        session_id=34, job_id="indeed:abc", ats="greenhouse", state="greenhouse_apply_form",
        field="How did you hear about us?",
        question_text="How did you hear about us?",
        options=("Job Board", "Referral", "Other"),
        canonical="Indeed",
        resolution={"value": "Job Board", "method": "alias", "confidence": 1.0,
                    "rationale": "recipe alias", "needs_human": False},
        outcome="ok", initiator="teacher",
    )
    defaults.update(kw)
    return qa_journal.record_qa(**defaults)


def test_record_and_read_roundtrip(tmp_path, monkeypatch):
    row = _row(tmp_path, monkeypatch)
    assert row is not None and row["sensitive"] is False
    rows = qa_journal.read_qa()
    assert len(rows) == 1
    assert rows[0]["question_text"] == "How did you hear about us?"
    assert rows[0]["resolution"]["value"] == "Job Board"
    assert rows[0]["index"] == 0


def test_sensitive_field_banks_question_never_the_secret(tmp_path, monkeypatch):
    row = _row(tmp_path, monkeypatch, field="Create Password", question_text="Create Password",
               canonical="hunter2-real-secret",
               resolution={"value": "hunter2-real-secret", "method": "verbatim",
                           "confidence": 1.0, "rationale": "", "needs_human": False})
    assert row["sensitive"] is True
    assert "hunter2" not in str(row)              # the secret never lands, anywhere in the row
    assert row["question_text"] == "Create Password"   # the question's distribution still banks


def test_correction_keeps_both_sides(tmp_path, monkeypatch):
    _row(tmp_path, monkeypatch)
    out = qa_journal.correct_qa(0, value="Other", by="operator", note="source not offered")
    assert out["teacher_correction"]["value"] == "Other"
    assert out["teacher_correction"]["original"]["value"] == "Job Board"   # §10: both sides
    assert qa_journal.read_qa()[0]["teacher_correction"]["by"] == "operator"


def test_qa_row_banks_into_the_vector_store(tmp_path, monkeypatch):
    _row(tmp_path, monkeypatch)
    from precedent.store import VectorStore

    counts = VectorStore(tmp_path / "vectors.db").counts()
    assert counts.get("qa") == 1


def test_doc_from_qa_prefers_the_correction_as_label(tmp_path, monkeypatch):
    _row(tmp_path, monkeypatch)
    qa_journal.correct_qa(0, value="Other", by="operator")
    row = qa_journal.read_qa()[0]
    doc = doc_from_qa(row)
    assert "question: How did you hear about us?" in doc.text
    assert "options: Job Board; Referral; Other" in doc.text
    assert doc.ref == "Other" and doc.teacher_label == "Other"
    assert doc.kind == "qa" and doc.intent == "resolve_answer"


def test_options_parse_from_refusal_prose():
    detail = ("popup opened but the value is not in the list; "
              "sample: ['Acknowledge/Confirm', 'Decline']. Vocabulary miss -> ask the answer store")
    assert _options_from_detail(detail) == ("Acknowledge/Confirm", "Decline")
    assert _options_from_detail("no option(s) [\"Acknowledge\"]") == ()   # missing != offered
    assert _options_from_detail(None) == ()
    assert _options_from_detail("sample: [not python") == ()


# --- the auth leg's pure parts (§11 item 6 first cut rides this session) -----
def test_port_of_parses_browser_url():
    from routers.session_control import _port_of

    assert _port_of("http://127.0.0.1:9322") == 9322
    assert _port_of("not a url") is None
    assert _port_of("") is None


def test_newest_snapshot_picks_head_by_taken_at(monkeypatch):
    import routers.session_control as sc

    class _M:
        def __init__(self, id, taken_at):
            self.id, self.taken_at, self.profile = id, taken_at, "indeed"

    import session_snapshot as snap

    monkeypatch.setattr(snap, "list_snapshots",
                        lambda profile: [_M("old", 1.0), _M("new", 9.0)])
    assert sc._newest_snapshot_id("indeed") == "new"
    monkeypatch.setattr(snap, "list_snapshots", lambda profile: [])
    assert sc._newest_snapshot_id("indeed") is None
