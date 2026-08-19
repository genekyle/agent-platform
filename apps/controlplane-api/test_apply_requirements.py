"""The third axis: what does this application ask for, and when.

The tests that matter are the ones about NOT over-generalising — the operator's own stated worry.
A single sighting must never present as a rule, and a word in a job description must never present
as a requirement.
"""
import apply_landing as al
import apply_requirements as ar


def test_peopleadmin_declares_its_documents_before_any_form_is_opened():
    """MEASURED 2026-08-19, une.peopleadmin.com/postings/26341 — the posting states its own
    requirements, which is free information a drive can read at classify time."""
    text = ("Documents Needed to Apply Required Documents Cover Letter Resume "
            "Optional Documents Names and contact information for three professional references "
            "Supplemental Questions Required fields are indicated with an asterisk")
    reqs = {s.requirement for s in ar.detect(text, declared_only=True)}
    assert {ar.RESUME, ar.COVER_LETTER, ar.REFERENCES, ar.SUPPLEMENTAL_QUESTIONS} <= reqs


def test_a_word_in_a_job_description_is_not_a_requirement():
    """The false positive that would poison a planner: 'portfolio' in prose."""
    text = ("The analyst will manage the reporting portfolio across departments and present "
            "to leadership. Experience with SQL preferred.")
    assert ar.detect(text, declared_only=True) == []
    # It is still SEEN — the distinction is declared vs mentioned, not present vs absent.
    assert any(s.requirement == ar.PORTFOLIO for s in ar.detect(text))


def test_the_resume_slot_lands_at_a_different_kind_on_every_vendor():
    """The whole reason this module exists. Three real flows, 2026-08-19, one requirement,
    three timings — Indeed's first screen, Paylocity's form, PeopleAdmin's ACCOUNT page."""
    obs = [
        ar.Observation("indeed_quick_apply", al.APPLICATION_FORM, ar.RESUME, declared=True),
        ar.Observation("paylocity", al.APPLICATION_FORM, ar.RESUME, declared=True),
        ar.Observation("peopleadmin", al.ACCOUNT_GATE, ar.RESUME, declared=True),
    ]
    kinds = {o.platform: o.kind for o in obs}
    assert kinds["peopleadmin"] == al.ACCOUNT_GATE != kinds["paylocity"]


def test_one_sighting_out_of_one_flow_is_provisional_not_a_rule():
    obs = [ar.Observation("peopleadmin", al.JOB_POSTING, ar.COVER_LETTER, declared=True)]
    s = ar.summarise(obs, flows_by_platform={"peopleadmin": 1})
    row = s["peopleadmin"]["requirements"][0]
    assert row["seen"] == 1 and row["flows"] == 1
    assert row["confidence"] == "provisional"
    assert "not a rule" in s["peopleadmin"]["caveat"]


def test_without_a_denominator_the_summary_refuses_to_pretend():
    """A requirement seen once out of one flow and once out of twenty look identical without the
    denominator, and only one of them is a rule."""
    obs = [ar.Observation("peopleadmin", al.JOB_POSTING, ar.COVER_LETTER)]
    s = ar.summarise(obs)
    assert s["peopleadmin"]["flows"] is None
    assert s["peopleadmin"]["requirements"][0]["confidence"] == "unknown"
    assert "not a recipe" in s["peopleadmin"]["caveat"]


def test_three_consistent_flows_earn_a_stronger_word_and_no_more():
    obs = [ar.Observation("paylocity", al.APPLICATION_FORM, ar.RESUME) for _ in range(3)]
    s = ar.summarise(obs, flows_by_platform={"paylocity": 3})
    assert s["paylocity"]["requirements"][0]["confidence"] == "consistent"


def test_blockers_answer_the_cheap_question_before_entering():
    """Will this flow stop on a human? Asked before twenty minutes are spent reaching the wall."""
    obs = [ar.Observation("peopleadmin", al.ACCOUNT_GATE, ar.ACCOUNT, declared=True),
           ar.Observation("peopleadmin", al.JOB_POSTING, ar.COVER_LETTER, declared=True)]
    assert ar.blockers(obs, platform="peopleadmin") == [ar.ACCOUNT]
    assert ar.blockers(obs, platform="paylocity") == []


def test_observe_pins_a_requirement_to_the_page_kind_it_was_met_on():
    obs = ar.observe("peopleadmin", al.ACCOUNT_GATE,
                     "Create an Account. You must have an account to apply to open positions. "
                     "Save time and upload your resume to prefill sections of your application.")
    got = {(o.requirement, o.kind) for o in obs}
    assert (ar.ACCOUNT, al.ACCOUNT_GATE) in got
    assert (ar.RESUME, al.ACCOUNT_GATE) in got


def test_the_vocabulary_is_closed_and_every_member_has_evidence():
    """A requirement with no markers would silently never fire."""
    assert set(ar.MARKERS) == set(ar.REQUIREMENTS)
    assert ar.NEEDS_HUMAN <= set(ar.REQUIREMENTS)
