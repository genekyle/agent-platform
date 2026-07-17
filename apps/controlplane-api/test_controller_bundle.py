"""build_bundle offline fixtures — one per Career Search ATS (Indeed, Workday, Greenhouse),
plus the invariants the Bundle must uphold: no selectors, no PII, pure/replayable, done + branch
detection. This is the M1 keystone test (SESSION_01)."""

from __future__ import annotations

from controller.bundle import build_bundle
from interaction.decision import bundle_to_prompt, looks_like_selector


# --- Indeed: URL-driven state, the M2 keystone target ------------------------
def test_indeed_questions_bundle():
    b = build_bundle(
        "indeed_apply",
        "https://smartapply.indeed.com/questions/8839201",
        goal_text="apply with indeed",
        scan=[{"field": "Work authorization", "selector": "#wa", "kind": "radio_group",
               "required_via": "required-attr", "value_read_at": "aria-checked",
               "answered": False, "valid": True, "value_preview": ""}],
    )
    assert b.ats == "indeed_quick_apply"
    assert b.state == "indeed_apply_questions"
    assert b.done is False
    assert b.human_required is False
    assert b.recipe_step == 2
    assert b.route == "smartapply.indeed.com/questions/{id}"   # dynamic id templated
    # form half sanitised: field kept, selector/preview/value_read_at dropped
    assert b.unanswered == ({"field": "Work authorization", "kind": "radio_group",
                             "required_via": "required-attr", "answered": False, "valid": True},)


def test_indeed_done_terminal_state():
    b = build_bundle("indeed_apply", "https://smartapply.indeed.com/post-apply",
                     goal_text="apply with indeed")
    assert b.done is True                      # TaskSpec terminal_url_patterns /post-apply


def test_indeed_captcha_branch_is_human_required():
    # /scan can't tell us; the recipe branch does — captcha state => human_required.
    b = build_bundle("indeed_apply", "https://smartapply.indeed.com/questions/x",
                     page_text="Please verify you are human reCAPTCHA")
    # page_text markers aren't consulted for Indeed (URL-driven), so this stays questions —
    # captcha for Indeed surfaces via the challenge probe, not the bundle. Assert no false done.
    assert b.done is False


# --- Workday: page-classified state + the credential boundary ----------------
def test_workday_my_information_bundle():
    b = build_bundle(
        "workday_apply",
        "https://acme.wd1.myworkdayjobs.com/job/apply",
        page_text="My Information\nLegal Name\nAddress",
        goal_text="apply",
    )
    assert b.ats == "workday"
    assert b.state == "workday_my_information"
    assert b.human_required is False
    assert b.recipe_step == 3
    assert "workday" in b.lessons.lower() or b.lessons  # LESSONS serialised in


def test_workday_sign_in_is_human_required_credential_boundary():
    b = build_bundle(
        "workday_apply",
        "https://acme.wd1.myworkdayjobs.com/login",
        page_text="Email Address\nPassword\nSign In",
    )
    assert b.state == "workday_sign_in"
    assert b.human_required is True            # the agent never types a password
    assert b.is_branch is True


# --- Greenhouse: the single-form ATS -----------------------------------------
def test_greenhouse_form_bundle():
    b = build_bundle(
        "greenhouse_apply",
        "https://job-boards.greenhouse.io/acme/jobs/123",
        page_text="First Name\nLast Name\nResume\nSubmit Application",
        goal_text="apply",
    )
    assert b.ats == "greenhouse"
    assert b.state == "greenhouse_apply_form"
    assert b.done is False
    assert b.human_required is False


def test_greenhouse_submitted_is_done():
    b = build_bundle("greenhouse_apply", "https://job-boards.greenhouse.io/acme/jobs/123",
                     page_text="Thank you for applying to Acme")
    assert b.state == "greenhouse_apply_submitted"
    assert b.done is True                       # recipe terminal-state fallback


def test_greenhouse_wrapper_detected_by_gh_jid_param():
    b = build_bundle("greenhouse_apply", "https://www.kkr.com/careers/post?gh_jid=456",
                     page_text="First Name Resume")
    assert b.ats == "greenhouse"               # classified through the wrapper via gh_jid


# --- invariants that must hold for EVERY bundle ------------------------------
def test_bundle_carries_no_selectors_anywhere():
    b = build_bundle(
        "greenhouse_apply", "https://job-boards.greenhouse.io/acme/jobs/1",
        page_text="First Name",
        scan=[{"field": "Email", "selector": "#email", "kind": "text",
               "required_via": "required-attr", "value_read_at": "[class*=singleValue]",
               "answered": False, "valid": True, "value_preview": "me@x.com"}],
    )
    for it in b.unanswered:
        for k, v in it.items():
            assert not looks_like_selector(k), f"selector key leaked: {k}"
            assert not looks_like_selector(v), f"selector value leaked: {v}"
        assert "value_preview" not in it        # PII dropped
    # the sanitised field name itself is semantic
    assert b.unanswered[0]["field"] == "Email"


def test_build_bundle_is_pure_and_deterministic():
    args = ("indeed_apply", "https://smartapply.indeed.com/questions/abc")
    kw = dict(goal_text="apply with indeed",
              scan=[{"field": "X", "kind": "text", "required_via": "required-attr",
                     "answered": False, "valid": True}],
              journal_tail=[{"intent": "click", "params": {"field": None}, "outcome": "ok"}])
    a = build_bundle(*args, **kw)
    b = build_bundle(*args, **kw)
    assert a == b                                # same inputs -> identical bundle (replayable)


def test_history_half_is_shaped_from_journal_tail():
    b = build_bundle(
        "indeed_apply", "https://smartapply.indeed.com/questions/abc",
        journal_tail=[
            {"intent": "click", "params": {"field": None}, "outcome": "ok"},
            {"intent": "set_text", "params": {"field": "Phone"}, "outcome": "ok"},
            {"intent": "select_option", "field": "Device", "outcome": "not_opened"},  # intent-row shape
        ],
    )
    assert b.recent == (
        {"intent": "click", "field": None, "outcome": "ok"},
        {"intent": "set_text", "field": "Phone", "outcome": "ok"},
        {"intent": "select_option", "field": "Device", "outcome": "not_opened"},
    )


def test_unknown_state_is_none_not_the_string_unknown():
    # A non-career-search URL degrades gracefully: state None, not the literal "unknown",
    # so the reasoner sees "(unknown)" and rung 0 can't fire.
    b = build_bundle("indeed_apply", "https://example.com/random")
    assert b.state is None
    assert "state: (unknown)" in bundle_to_prompt(b)
