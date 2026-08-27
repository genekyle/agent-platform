"""World-facts (SESSION 16) — the shape that lets a claim rot visibly.

Pinned in the order it costs to get wrong:
  1. The constructor is the enforcement point: undated, unevidenced-MEASURED, unanchored, or
     recheck-less facts are refused with the failing field named.
  2. Staleness is a JOIN, not a timer: a fact whose surface was driven AFTER it was observed is
     "outdriven"; one nothing has driven past is fresh-by-silence — and the two never render
     alike. RETRACTED facts are history, not work.
  3. The pilot migration really registered, ids unique, retractions carrying both sides.
"""
from __future__ import annotations

import pytest

import world_facts as wfm


def _fact(**over):
    base = dict(
        id="testsite.results.example",
        claim="the list renders all rows at once",
        evidence_class="MEASURED",
        observed_at="2026-08-01",
        drive={"session": 1, "date": "2026-08-01"},
        evidence="counted 25/25 on first read",
        surface={"platform": "testsite", "hosts": ["www.testsite.com"]},
        recheck="count rows on first read",
    )
    base.update(over)
    return wfm.fact(**base)


def test_the_constructor_names_the_failing_field():
    with pytest.raises(ValueError, match="observed_at"):
        _fact(observed_at="July 2026")
    with pytest.raises(ValueError, match="MEASURED without evidence"):
        _fact(evidence="")
    with pytest.raises(ValueError, match="platform and at least one host"):
        _fact(surface={"platform": "testsite", "hosts": []})
    with pytest.raises(ValueError, match="recheck"):
        _fact(recheck="")
    with pytest.raises(ValueError, match="evidence_class"):
        _fact(evidence_class="TRUST_ME")
    # a RETRACTED fact is history — exempt from recheck and evidence
    dead = _fact(evidence_class="RETRACTED", evidence="", recheck="",
                 history=[{"date": "2026-08-02", "note": "retracted: falsified live"}])
    assert dead["evidence_class"] == "RETRACTED"


def test_outdriven_and_fresh_by_silence_never_render_alike(monkeypatch):
    facts = {
        "a.b.outdriven": _fact(id="a.b.outdriven", observed_at="2026-08-01",
                               surface={"platform": "t", "hosts": ["driven.com"]}),
        "a.b.fresh": _fact(id="a.b.fresh", observed_at="2026-08-01",
                           surface={"platform": "t", "hosts": ["untouched.com"]}),
        "a.b.dead": _fact(id="a.b.dead", evidence_class="RETRACTED", evidence="", recheck="",
                          history=[{"date": "2026-08-02", "note": "retracted"}]),
    }
    monkeypatch.setattr(wfm, "collect", lambda: facts)
    monkeypatch.setattr(wfm, "_last_drive_by_host",
                        lambda: ({"driven.com": "2026-08-20T10:00:00"}, 7, "/tmp/corpus"))
    rep = wfm.staleness_report()
    assert [e["id"] for e in rep["outdriven"]] == ["a.b.outdriven"]
    assert rep["outdriven"][0]["outdriven_by_days"] == 19
    assert [e["id"] for e in rep["fresh_by_silence"]] == ["a.b.fresh"]
    assert rep["retracted_kept"] == 1
    assert rep["corpus_rows"] == 7 and rep["root"] == "/tmp/corpus"


def test_a_drive_on_the_same_day_is_not_outdriven(monkeypatch):
    facts = {"a.b.sameday": _fact(id="a.b.sameday", observed_at="2026-08-20",
                                  surface={"platform": "t", "hosts": ["driven.com"]})}
    monkeypatch.setattr(wfm, "collect", lambda: facts)
    monkeypatch.setattr(wfm, "_last_drive_by_host",
                        lambda: ({"driven.com": "2026-08-20T23:59:00"}, 1, "/tmp"))
    rep = wfm.staleness_report()
    assert rep["outdriven"] == [] and len(rep["fresh_by_silence"]) == 1


def test_the_pilot_migration_registered_with_unique_ids_and_kept_retractions():
    facts = wfm.collect()
    assert "linkedin.results.virtualised" in facts
    assert facts["linkedin.results.virtualised"]["evidence_class"] == "HYPOTHESIS"
    assert facts["linkedin.sweep.blocked_on_set_distance"]["evidence_class"] == "RETRACTED"
    assert all(f["surface"]["hosts"] for f in facts.values())
    # every live fact can be re-verified; only history is exempt
    for f in facts.values():
        if f["evidence_class"] != "RETRACTED":
            assert f["recheck"], f"{f['id']} has no recheck"
