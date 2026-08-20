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


# --- the brief: the data actually being READ ---------------------------------------------------

def test_the_trace_url_is_nested_and_a_top_level_get_finds_nothing():
    """675MB read as 'no join key' for months because nothing looked below the top level.
    `_deep_url` is the whole trace backfill; this pins the shape that defeated the naive read."""
    trace = {"acquisition": {"page_identity": {"url": "https://une.peopleadmin.com/postings/1"}}}
    assert trace.get("url") is None
    assert bf._deep_url(trace) == "https://une.peopleadmin.com/postings/1"


def test_deep_url_gives_up_rather_than_descending_forever():
    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"url": "https://x.test/y"}}}}}}}
    assert bf._deep_url(deep) == ""


def test_folding_traces_widens_instances_without_inventing_flows():
    """A trace says 'we looked at this page', not 'we drove an application here'. Inflating the
    flow denominator with page views is how a sighting would become a driven application."""
    instances, flows = bf.aggregate(_rows())
    before_flows = len(flows)
    counts = bf.fold_traces(instances, [
        {"url": "https://recruiting.paylocity.com/Recruiting/Jobs/Details/9/Gamma-Co",
         "ts": "2026-08-20T11:00:00+00:00"}])
    assert "paylocity:gamma-co" in instances       # widened
    assert counts["paylocity:gamma-co"] == 1
    assert len(flows) == before_flows              # and no flow invented


def test_sidecars_are_skipped_so_one_page_is_not_counted_four_times():
    assert bf._SIDECAR_MARKERS == (".ax.", ".meta.", ".vision.")


class _FakeQuery:
    def __init__(self, rows): self._rows = rows
    def filter_by(self, **kw): return self
    def all(self): return self._rows
    def scalar(self): return len(self._rows)


class _FakeDb:
    """Enough of a Session for the brief — it only reads."""
    def __init__(self, instance=None, flows=(), chars=()):
        self._instance, self._flows, self._chars = instance, list(flows), list(chars)
    def get(self, model, key): return self._instance
    def query(self, *args):
        name = getattr(args[0], "__name__", "")
        if "Flow" in str(args[0]) or name == "AtsFlow": return _FakeQuery(self._flows)
        if "Characteristic" in str(args[0]): return _FakeQuery(self._chars)
        return _FakeQuery([])


def test_a_platform_we_have_never_driven_says_so_instead_of_looking_clean():
    """An empty brief that renders like a clean bill of health is worse than no brief."""
    import ats_brief
    b = ats_brief.brief("https://boards.greenhouse.io/acme/jobs/1", _FakeDb())
    assert b["known"] is False
    assert "never driven" in b["headline"]
    assert b["vendor"]["confidence"] == "never driven"
    assert "unverified" in b["caveat"]


def test_an_account_gated_vendor_is_flagged_as_stopping_on_a_human():
    """The one-line answer the UNE drive needed before it spent twenty minutes."""
    import ats_brief
    b = ats_brief.brief("https://une.peopleadmin.com/postings/26341", _FakeDb())
    assert b["blockers"] == ["account"]
    assert "stop for you" in b["headline"]


def test_the_brief_resolves_the_tenant_not_just_the_vendor():
    import ats_brief
    b = ats_brief.brief(
        "https://recruiting.paylocity.com/Recruiting/Jobs/Details/1/Alpha-Co", _FakeDb())
    assert b["instance_key"] == "paylocity:alpha-co"
    assert b["tenant_source"] == "path_regex"


def test_none_recorded_and_none_succeeded_are_different_sentences():
    """The bug this fixed: a vendor driven nine times with no outcomes recorded rendered
    `submitted_flows: 0`, which reads as 'never finished' and meant 'never recorded'."""
    import ats_brief

    class _Flow:
        def __init__(self, terminal=None):
            self.terminal, self.confirmed, self.mismatched, self.states = terminal, 3, 1, []

    no_outcomes = ats_brief.brief("https://x.wd1.myworkdayjobs.com/job/1",
                                  _FakeDb(instance=object(), flows=[_Flow(), _Flow()]))
    assert no_outcomes["vendor"]["submitted_flows"] is None
    assert no_outcomes["vendor"]["outcomes_recorded"] == 0
    assert "outcomes not recorded" in no_outcomes["headline"]

    tried_and_failed = ats_brief.brief(
        "https://x.wd1.myworkdayjobs.com/job/1",
        _FakeDb(instance=object(), flows=[_Flow("parked:account_wall"), _Flow("abandoned:operator")]))
    assert tried_and_failed["vendor"]["submitted_flows"] == 0
    assert "none of 2 finished" in tried_and_failed["headline"]

    won = ats_brief.brief("https://x.wd1.myworkdayjobs.com/job/1",
                          _FakeDb(instance=object(), flows=[_Flow("submitted"), _Flow("parked:operator")]))
    assert won["vendor"]["finish_rate"] == 0.5
    assert "1 of 2 submitted" in won["headline"]


def test_record_flow_is_idempotent_per_session_instance_and_job():
    """A terminal re-flagged must not double the denominator everything else reasons from."""
    import ats_backfill as bf2, models

    class _Db:
        def __init__(self): self.added, self._flow = [], None
        def get(self, m, k): return object()
        def query(self, m):
            outer = self
            class Q:
                def filter_by(self, **kw): return self
                def first(self): return outer._flow
            return Q()
        def add(self, o):
            self.added.append(o)
            if isinstance(o, models.AtsFlow): self._flow = o
        def flush(self): pass

    db = _Db()
    args = dict(url="https://une.peopleadmin.com/postings/1", job_key="indeed:a1",
                terminal="parked:account_wall", session_id=7, platform="peopleadmin")
    assert bf2.record_flow(db, **args) == "peopleadmin:une"
    bf2.record_flow(db, **args)
    assert sum(1 for o in db.added if isinstance(o, models.AtsFlow)) == 1


def test_record_flow_never_raises_into_the_terminal_it_describes():
    """Bookkeeping must not be able to fail the flag it is recording."""
    import ats_backfill as bf2

    class _Broken:
        def get(self, *a, **k): raise RuntimeError("db is down")
    assert bf2.record_flow(_Broken(), url="https://x.test/y", job_key="j",
                           terminal="submitted") is None
    # And a missing url is a no-op, not an exception.
    assert bf2.record_flow(None, url="", job_key="j", terminal="submitted") is None
