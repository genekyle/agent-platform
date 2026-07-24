"""Tests for source-of-application resolution — 'how did you hear' by context."""

import apply_source as src


def test_indeed_source_tries_indeed_then_simplyhired_then_other():
    """The operator's case: job board > Indeed (or SimplyHired), Other if neither is offered."""
    assert src.source_candidates("indeed") == ["Indeed", "SimplyHired", "Other"]


def test_other_is_always_the_last_resort():
    for source in ("indeed", "linkedin", "glassdoor", "totally_unknown", ""):
        assert src.source_candidates(source)[-1] == "Other"


def test_an_unknown_source_still_answers_with_other():
    """Never empty — a source we cannot place is honestly 'Other', not a crash or a guess."""
    assert src.source_candidates("carrier_pigeon") == ["Other"]


def test_simplyhired_and_indeed_are_siblings():
    assert "Indeed" in src.source_candidates("simplyhired")
    assert "SimplyHired" in src.source_candidates("indeed")


def test_linkedin_foreshadows_the_next_domain():
    assert src.source_candidates("linkedin") == ["LinkedIn", "Other"]


def test_source_from_job_id():
    assert src.source_from_job_id("indeed:abc123") == "indeed"
    assert src.source_from_job_id("linkedin:xyz") == "linkedin"
    assert src.source_from_job_id("") == ""


def test_candidates_dedupe_case_insensitively():
    # a source whose only leaf were 'Other' must not list it twice
    assert src.source_candidates("indeed").count("Other") == 1


def test_source_paths_drill_category_then_leaf():
    """The nested case: Indeed hides under a Job Board category, so each is a 2-level path, and
    Other stays flat."""
    paths = src.source_paths("indeed")
    assert paths == [["Job Board", "Indeed"], ["Job Board", "SimplyHired"], ["Other"]]


def test_source_paths_end_in_other_flat():
    for source in ("indeed", "linkedin", "unknown"):
        assert src.source_paths(source)[-1] == ["Other"]
