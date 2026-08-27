"""Naming debt (SESSION 15) — the ranked unnamed/ambiguous-screen report.

Pinned in the order it costs to get wrong:
  1. The situation key is STRUCTURAL: content churn in link roles must not mint new keys (the
     first live run credited indeed_search_results with 50 "situations"), and long content-bearing
     button names fall out of the chrome set.
  2. Ambiguity reads the `assessed` list — the uncertainty dict fills unassessed axes with 1.0,
     so a bare read of uncertainty.state would call a confident half ambiguous.
  3. Recurrence gates the queue (one-offs counted, never shown), blank tabs are excluded, and a
     disputed situation (two names for one screen) queues even when each name was confident.
  4. split_names counts RECURRENT shapes only — content churn must not read as a split screen.
"""
from __future__ import annotations

import naming_debt as nd


def _half(url, state, *, unc=0.2, assessed=("state",), agreement="agree", novelty=0.1,
          candidates=None, screenshot=None):
    return {
        "url": url,
        "candidates": candidates or [["button", "Search"], ["textbox", "What"]],
        "screenshot": screenshot,
        "belief": {
            "state": state,
            "assessed": list(assessed),
            "agreement": agreement,
            "uncertainty": {"state": unc, "novelty": novelty},
        },
    }


def _row(index, before, after, ts="2026-08-27T10:00:00"):
    return {"index": index, "ts": ts, "before": before, "after": after}


def test_the_situation_key_ignores_content_and_drops_long_names():
    chrome = [["button", "Date posted"], ["combobox", "Sort by"]]
    a = nd.situation_key("https://x.com/jobs?start=0", chrome + [["link", "Data Analyst - Acme"]])
    b = nd.situation_key("https://x.com/jobs?start=25", chrome + [["link", "Welder - Bravo Corp"]])
    assert a == b, "link-role content or paging params minted a new situation"
    long_button = [["button", "Dismiss Senior Data Analyst, Revenue Operations job"]]
    c = nd.situation_key("https://x.com/jobs", chrome + long_button)
    assert c == a, "a content-bearing long button name entered the chrome set"
    d = nd.situation_key("https://x.com/jobs", [["button", "LinkedIn Apply"]] + chrome)
    assert d != a, "genuinely different chrome must be a different situation"


def test_an_unassessed_state_axis_is_ambiguous_and_an_assessed_confident_one_is_not():
    confident = nd._half_reading(_half("https://x.com/a", "known_state"))
    assert confident["ambiguous"] is False
    unassessed = nd._half_reading(_half("https://x.com/a", "borrowed_name",
                                        unc=1.0, assessed=()))
    assert unassessed["ambiguous"] is True
    split = nd._half_reading(_half("https://x.com/a", "known_state", unc=0.3,
                                   agreement="split"))
    assert split["ambiguous"] is True


def test_recurrence_gates_the_queue_and_blanks_are_excluded_but_counted():
    hot = [_row(i, _half("https://x.com/form", "maybe_form", unc=0.9,
                         screenshot=f"/shots/hot_{i}.png"),
                _half("about:blank", "maybe_form")) for i in range(3)]
    one_off = [_row(9, _half("https://x.com/rare", "rare_state", unc=0.9), {})]
    rep = nd.build_naming_debt({"s1": hot + one_off})
    assert rep["blank_halves"] == 3
    assert rep["one_off_situations"] == 1
    assert [e["encounters"] for e in rep["queue"]] == [3]
    assert rep["queue"][0]["exemplar"]["screenshot"] == "hot_2.png", \
        "exemplar must be the newest screenshotted half, as a basename"


def test_a_disputed_situation_queues_even_when_each_name_was_confident():
    rows = [_row(i, _half("https://x.com/eeo", "task_complete" if i % 2 else "icims_eeo",
                          unc=0.1), {}) for i in range(4)]
    rep = nd.build_naming_debt({"s1": rows})
    assert rep["queue"], "two confident names on one screen is exactly a naming dispute"
    called = {c["state"]: c["n"] for c in rep["queue"][0]["called"]}
    assert called == {"task_complete": 2, "icims_eeo": 2}


def test_split_names_counts_recurrent_shapes_only():
    shape_a = [["button", "Date posted"]]
    shape_b = [["button", "LinkedIn Apply"], ["button", "Date posted"]]
    rows = []
    for i in range(3):
        rows.append(_row(i, _half("https://x.com/jobs/search", "linkedin_job_search",
                                  candidates=shape_a, screenshot=f"/s/a{i}.png"), {}))
        rows.append(_row(10 + i, _half("https://x.com/jobs/search", "linkedin_job_search",
                                       candidates=shape_b, screenshot=f"/s/b{i}.png"), {}))
    # content churn: three one-off shapes under the same name must NOT count as splits
    # (letters, not numbers — the name normalizer strips digit runs, which is its own feature)
    for tag in ("ay", "bee", "sea"):
        rows.append(_row(20, _half("https://x.com/jobs/search", "linkedin_job_search",
                                   candidates=shape_a + [["button", f"oddity {tag} pill"]]), {}))
    rep = nd.build_naming_debt({"s1": rows})
    assert rep["split_names_total"] == 1
    entry = rep["split_names"][0]
    assert entry["state"] == "linkedin_job_search"
    assert entry["situations"] == 2, "only the two RECURRENT shapes count"
    assert entry["situations_incl_one_offs"] == 5
    assert len(entry["examples"]) == 2 and all(e["screenshot"] for e in entry["examples"])


def test_the_report_names_its_root_and_an_empty_corpus_carries_the_warning(monkeypatch):
    import step_runner as sr
    monkeypatch.setattr(sr, "list_corpora", lambda: [])
    rep = nd.naming_report()
    assert rep["rows"] == 0 and "root" in rep
    assert "OBSERVER_ARTIFACTS_DIR" in rep.get("note", ""), \
        "an empty answer must never read as a clean one"
