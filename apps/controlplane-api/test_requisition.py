"""The requisition extractor — and the board refusal that keeps the tier honest.

`applied_index` treats a requisition match as CERTAIN ENOUGH TO ACT ON, so a false hit silently
skips a job the operator never applied to. These pin the expensive direction first.
"""

import requisition


def test_each_mapped_ats_yields_its_own_req_id():
    # One live url per family, taken from real drives (2026-07..08).
    assert requisition.extract(
        "https://bilh.wd1.myworkdayjobs.com/External/job/Beth-Israel/Analyst_JR88822") == "JR88822"
    assert requisition.extract(
        "https://job-boards.greenhouse.io/hoodhp/jobs/5325374008?gh_src=x") == "5325374008"
    assert requisition.extract(
        "https://careers-odysseyconsult.icims.com/jobs/8308/data-business-analyst/job") == "8308"


def test_a_board_url_never_yields_a_requisition():
    # THE EXPENSIVE DIRECTION. An Indeed jk rotates per search session and a LinkedIn id is
    # LinkedIn's alone: extracting from either would manufacture a cross-engine match that means
    # nothing, and the tier that consumes it does not warn — it acts.
    assert requisition.extract("https://www.indeed.com/viewjob?jk=fac50ab899ed3f96") is None
    assert requisition.extract("https://www.linkedin.com/jobs/view/4271234567/") is None
    assert requisition.is_board_url("https://www.indeed.com/jobs?q=data+analyst")


def test_an_unmapped_ats_returns_none_rather_than_a_guess():
    # The honest answer for a vendor nobody has mapped: applied_index falls through to the tier
    # below, exactly as it did before this module existed.
    assert requisition.extract("https://careers.some-new-vendor.example/opening/abc") is None


def test_the_named_ats_narrows_and_does_not_borrow_another_vendors_grammar():
    url = "https://careers-odysseyconsult.icims.com/jobs/8308/data-business-analyst/job"
    assert requisition.extract(url, "icims") == "8308"
    # Told it is a workday url, the icims grammar must not answer for it.
    assert requisition.extract(url, "workday") is None
