"""Disliking is a third verdict, not a louder pass.

The distinction is the whole point: passing is "not this one, out of these fifteen, today";
disliking is "do not show me this kind again". A ledger that stores them as one value teaches a
boundary in the wrong place, whichever way it collapses them.
"""
import job_decisions as jd


def test_disliked_is_its_own_verdict_and_the_vocabulary_is_closed():
    assert jd.DISLIKED == "disliked"
    assert set(jd.DECISIONS) == {"picked", "passed", "disliked"}
    assert jd.DISLIKED not in (jd.PICKED, jd.PASSED)


class _Db:
    def __init__(self): self.added = []
    def add(self, o): self.added.append(o)
    def commit(self): pass
    def get(self, *a, **k): return None


def test_a_dislike_keeps_the_card_so_the_reason_survives_the_page(monkeypatch):
    """A dislike whose text is gone can never teach why — the cards on screen exist nowhere else
    once the page moves, which is the same argument the decision ledger was built on."""
    monkeypatch.setattr(jd, "_canonical_key", lambda db, job_id: "job:abc")
    db = _Db()
    row = jd.record_dislike(db, job_id="indeed:1", reason="BCBA, not a data role",
                            query="report analyst", platform="indeed",
                            card={"title": "Board Certified Behavior Analyst", "company": "X"})
    assert row.decision == "disliked"
    assert row.job_key == "job:abc"
    assert row.card["title"].startswith("Board Certified")
    assert row.reason == "BCBA, not a data role"


def test_a_reason_is_optional_and_never_invented(monkeypatch):
    """Empty is honest and common; a fabricated reason would be worse than none."""
    monkeypatch.setattr(jd, "_canonical_key", lambda db, job_id: None)
    row = jd.record_dislike(_Db(), job_id="indeed:2")
    assert row.reason == ""
    assert row.decision == "disliked"


def test_an_overlong_reason_is_truncated_rather_than_rejected(monkeypatch):
    monkeypatch.setattr(jd, "_canonical_key", lambda db, job_id: None)
    row = jd.record_dislike(_Db(), job_id="indeed:3", reason="x" * 900)
    assert len(row.reason) == 500
