"""The ATS database: instances, characteristics, flows — and the guards against over-claiming.

The tests that matter are the ones about the axis a hostname cannot carry, and about a denominator
never going missing. A mismatch rate over three acts and one over a hundred must not render alike.
"""
import ats_backfill as bf
import ats_tenancy as ten


def test_the_hostname_axis_undercounts_paylocity_and_the_extractor_fixes_it():
    """The bug this whole thing exists for. Two employers, one shared host, and until 2026-08-20
    the corpus reported a single Paylocity 'instance'."""
    a = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4382310/Isabella-Stewart-Gardner-Museum"
    b = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4403455/Charles-River-Community-Health"
    ka = ten.instance_key("paylocity", ten.tenant_of(a, "paylocity")[0])
    kb = ten.instance_key("paylocity", ten.tenant_of(b, "paylocity")[0])
    assert ka != kb
    assert ka == "paylocity:isabella-stewart-gardner-museum"


def test_every_vendor_encodes_tenancy_differently_and_each_says_which_rule_answered():
    cases = [
        ("https://cswg.wd1.myworkdayjobs.com/CS_Careers/job/x", "workday", "cswg", "subdomain"),
        ("https://une.peopleadmin.com/postings/26341", "peopleadmin", "une", "subdomain"),
        ("https://sjobs.brassring.com/TGnewUI/Search/Home?partnerid=368&siteid=1",
         "brassring", "368", "query[partnerid]"),
        ("https://www.linkedin.com/jobs/view/1", "linkedin_easy_apply", "", "none"),
    ]
    for url, ats, tenant, how in cases:
        assert ten.tenant_of(url, ats) == (tenant, how), url


def test_a_company_careers_host_is_the_employer_not_its_leading_label():
    """`careers.solutionhealth.org` is SolutionHealth. Taking the first label would make the tenant
    'careers', which every employer on earth shares."""
    assert ten.tenant_of("https://careers.solutionhealth.org/jobs/1", "company_site") == (
        "careers.solutionhealth.org", "hostname")


def test_a_single_tenant_vendor_collapses_to_the_vendor_itself():
    assert ten.instance_key("indeed_quick_apply", "") == "indeed_quick_apply"


def test_an_unknown_vendor_falls_back_to_the_hostname_and_says_so():
    """Honest fallback beats a guess: the caller can see the answer was not from a rule."""
    assert ten.tenant_of("https://weird.example.com/x", "nobody") == (
        "weird.example.com", "fallback:hostname")


def _rows():
    """A two-flow, two-instance corpus: same vendor, different employers, one session."""
    def row(ts, url, verdict, state, sid=1):
        return {"ts": ts, "session_id": sid, "verdict": verdict,
                "before": {"url": url, "belief": {"state": state}},
                "after": {"url": url, "belief": {"state": state}}}
    A = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/1/Alpha-Co"
    B = "https://recruiting.paylocity.com/Recruiting/Jobs/Details/2/Beta-Co"
    return [row("2026-08-20T10:00:00+00:00", A, "confirmed", "paylocity_job_posting"),
            row("2026-08-20T10:05:00+00:00", A, "mismatch", "paylocity_application_form"),
            row("2026-08-20T10:20:00+00:00", B, "confirmed", "paylocity_job_posting")]


def test_aggregate_separates_instances_and_keeps_the_spine_each_flow_walked():
    instances, flows = bf.aggregate(_rows())
    assert set(instances) == {"paylocity:alpha-co", "paylocity:beta-co"}
    by_key = {f.instance_key: f for f in flows}
    assert by_key["paylocity:alpha-co"].states == [
        "paylocity_job_posting", "paylocity_application_form"]
    assert by_key["paylocity:alpha-co"].mismatched == 1


def test_a_long_idle_gap_starts_a_new_flow_rather_than_merging_two_applications():
    rows = _rows()
    rows.append({"ts": "2026-08-20T14:00:00+00:00", "session_id": 1, "verdict": "confirmed",
                 "before": {"url": rows[0]["before"]["url"], "belief": {"state": "x"}},
                 "after": {}})
    _, flows = bf.aggregate(rows)
    alpha = [f for f in flows if f.instance_key == "paylocity:alpha-co"]
    assert len(alpha) == 2, "a four-hour gap is a different application, not the same flow"


def test_a_rate_over_a_handful_of_acts_is_marked_assumed_not_measured():
    """The whole point of the confidence column: three observations is not a property of a vendor."""
    instances, flows = bf.aggregate(_rows())
    chars = bf.derive_characteristics(instances, flows)
    rate = [c for c in chars if c["kind"] == "mismatch_rate"][0]
    assert rate["confidence"] == "assumed"
    assert "of 3 observed acts" in rate["evidence"]


def test_derived_characteristics_always_carry_their_evidence():
    instances, flows = bf.aggregate(_rows())
    for c in bf.derive_characteristics(instances, flows):
        assert c["evidence"], f"{c['kind']}/{c['key']} has no evidence"
        assert c["observations"] >= 0
