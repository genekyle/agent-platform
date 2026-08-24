"""The verifier has to be right about pages it has never seen, and refuse the ones that look close.

The expensive direction is a FALSE POSITIVE: an application marked sent that never was is a job
the operator never applied to and will never chase. So the review step, the validation error and
the "thank you for your interest" rejection all get their own test.
"""
import submission_verifier as sv


def test_the_paylocity_confirmation_we_actually_submitted_through():
    """MEASURED live 2026-08-19, Isabella Stewart Gardner Museum, req 4382310."""
    v = sv.verify(
        "https://recruiting.paylocity.com/Recruiting/Jobs/Success/4382310?source=Indeed_Feed",
        "Isabella Stewart Gardner Museum - Application Successful",
        "Your application has been received! Thank you for applying. If we feel your experience "
        "and qualifications are a fit for the position, we will contact you shortly.",
        platform="paylocity")
    assert v.submitted and v.confidence == "high"
    assert v.platform_known
    # The claim always arrives with its argument.
    assert "your application has been received" in v.evidence_line()


def test_an_unknown_ats_still_gets_a_verdict_from_the_generic_tier():
    """The 2am case: a host nobody has met. Generic signals must carry it, and SAY that they did."""
    v = sv.verify("https://careers.example-corp.com/apply/thank-you",
                  "Thank you - Example Corp Careers",
                  "Your application was submitted. We will review it shortly.")
    assert v.submitted
    assert not v.platform_known
    assert "generic signals only" in v.evidence_line()


def test_a_review_step_that_says_submit_is_not_a_confirmation():
    """The dangerous near-miss: every review page contains the word submit."""
    v = sv.verify("https://smartapply.indeed.com/beta/indeedapply/form/review-module",
                  "Review the contents of this job application",
                  "Review your application. You won't be able to edit your application after you "
                  "submit. Submit your application",
                  platform="indeed_quick_apply")
    assert not v.submitted


def test_a_page_still_reporting_a_required_field_is_disqualified_outright():
    """A validation error can co-occur with cheerful copy; the demand wins."""
    v = sv.verify("https://recruiting.paylocity.com/Recruiting/Jobs/Apply/4382310",
                  "Apply", "Thank you for applying. Email Address is required",
                  platform="paylocity")
    assert not v.submitted
    assert v.disqualified_by and "required" in v.disqualified_by
    assert v.confidence == "none"


def test_two_soft_signals_do_not_add_up_to_a_submission():
    """'Thank you for your interest' plus 'we will contact you' is how rejections open."""
    v = sv.verify("https://careers.example.com/jobs/1234",
                  "Careers",
                  "Thank you for your interest. We will contact you if a suitable role arises.")
    assert not v.submitted
    assert v.score < sv.CONFIRM_THRESHOLD


def test_a_success_word_inside_a_longer_path_segment_does_not_count():
    """Segment matching, not substring: `success-factors` in a route is not a confirmation."""
    v = sv.verify("https://career.successfactors.com/careers/job/1234", "Job", "")
    assert not v.submitted


def test_the_best_tab_wins_when_a_window_holds_several():
    v = sv.verify_tabs([
        {"url": "https://www.indeed.com/jobs?q=report+analyst", "title": "25 Report Analyst Jobs"},
        {"url": "https://recruiting.paylocity.com/Recruiting/Jobs/Success/4382310",
         "title": "Application Successful", "text": "Your application has been received!"},
    ], platform="paylocity")
    assert v.submitted


def test_a_hint_can_only_add_and_never_veto():
    """An unmeasured platform name must not make a real confirmation read as unconfirmed."""
    text = "Your application has been received!"
    known = sv.verify("https://x.test/success", "Application Successful", text, platform="paylocity")
    unknown = sv.verify("https://x.test/success", "Application Successful", text, platform="nobody")
    assert known.submitted and unknown.submitted
    assert known.score >= unknown.score


def test_extra_hints_let_a_caller_teach_it_a_new_ats_without_editing_the_module():
    """The flexibility the operator asked for: usable in a situation it is not familiar with."""
    v = sv.verify("https://jobs.newats.test/app/9/final", "Done",
                  "we got it, your form is in",
                  extra_hints={"url_re": r"/app/\d+/final",
                               "text_re": r"we got it", "why": "taught at the call site"})
    assert v.submitted
    assert any(s.id.startswith("hint:") for s in v.signals)


def test_applicantmanager_needs_its_hint_because_the_generic_prose_is_weak():
    """TAM (measured live 2026-08-24, CEDENT): posts its confirmation back to the SAME url, so
    there is no terminal route, and its generic prose ("thank you for your interest", "we will
    contact") also appears on rejection pages — it scored 0.75 and refused a page that HAD
    submitted. The first-person past-tense line is what carries it."""
    import submission_verifier as sv

    url = "https://theapplicantmanager.com/jobs?pos=dt10072&src=Indeed"
    generic = "Thank you for your interest in employment opportunities with CEDENT. we will contact you."
    # Without the decisive line the module must still REFUSE — weak prose never carries a verdict.
    assert sv.verify(url=url, title="CEDENT", text=generic, platform="applicantmanager").submitted is False
    # With it, the hint tier supplies the evidence the generic signals could not.
    confirmed = generic + " You applied with this email: genomags@gmail.com"
    assert sv.verify(url=url, title="CEDENT", text=confirmed, platform="applicantmanager").submitted is True


def test_applicantmanager_confirms_from_its_applied_route_when_the_text_is_gone():
    """TAM's tab moves off the confirmation body to /applied?app=<id>, whose visible text is a
    Google-Translate language list. A flag re-verified at that moment was refused on a genuinely
    submitted application (live 2026-08-24) — the route carries the evidence the body lost."""
    import submission_verifier as sv

    v = sv.verify(url="https://theapplicantmanager.com/applied?co=DT&app=1343244",
                  title="", text="Select Language Abkhaz Acehnese Afar Afrikaans",
                  platform="applicantmanager")
    assert v.submitted is True
    # A TAM url WITHOUT an application id is not a confirmation — a bare /applied route could be
    # a listing of applications, which is a place you visit, not a thing you just did.
    assert sv.verify(url="https://theapplicantmanager.com/applied",
                     title="", text="", platform="applicantmanager").submitted is False
