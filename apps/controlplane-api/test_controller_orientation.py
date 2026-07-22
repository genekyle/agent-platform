"""Route identification — the deep end, where an Indeed apply leaves the engine.

The test that carries this module is `test_a_branded_wrapper_is_caught_by_the_pixels`. A branded
wrapper — an employer serving a Workday or Greenhouse form from their own domain — is the one case
where the URL is confidently unhelpful (`company_site`) and the screen is not. It is already
documented in `ats_registry` as the KKR case, and it is the first place in this codebase where the
visual witness's measured strength (93–94% at PLATFORM level) is actually load-bearing rather than
decorative.

The other guard: `test_orientation_never_proposes_submit`. Entering an ATS is a per-prospect
decision the operator approves; proposing to SEND an application is never this module's business.
"""

from __future__ import annotations

import pytest

from interaction.contract import Intent
from interaction.decision import DECISION_CONFIDENCE_THRESHOLD, Bundle

from controller import orientation
from controller.decide import decide


class NoPrograms:
    def get(self, task, state):
        return None


def belief_with_visual(label):
    """A journaled belief whose VISUAL witness says `label`. Read from the witness rather than the
    fused state on purpose — the fused answer is mostly the DOM's, and the point is a second
    opinion with a different failure mode."""
    return {"state": label, "uncertainty": {"state": 0.2, "novelty": 0.2},
            "assessed": ["state", "novelty"],
            "witnesses": [{"name": "dom:tfidf", "label": None},
                          {"name": "visual:apple", "label": label}]}


def bundle(url="https://myworkdayjobs.com/acme/job/123", *, identities=(), belief=None,
           state=None):
    return Bundle(task="apply", goal_text="", done=False, url=url, route="/x", state=state,
                  is_branch=False, human_required=False,
                  ax_identities=tuple(identities), belief=belief)


APPLY_PAGE = ("button|Apply", "link|Back to search", "heading|Senior Data Engineer")


# --- the URL witness ------------------------------------------------------------------
def test_a_known_host_is_named_from_the_url():
    view = orientation.orient(bundle("https://acme.wd5.myworkdayjobs.com/x/job/1"))
    assert view.url_platform == "workday"
    assert view.platform == "workday"
    assert "host resolves to workday" in view.rationale


def test_an_unmapped_host_with_no_pixels_is_reported_as_unidentified():
    view = orientation.orient(bundle("https://careers.example.com/apply"))
    assert view.platform in ("", orientation.UNMAPPED)
    assert view.visual_platform == ""
    assert view.agreement in ("one_sided", "no_evidence")


# --- the pixel witness, where it actually earns its place -----------------------------
def test_a_branded_wrapper_is_caught_by_the_pixels():
    """The KKR case, generalised: the host is the employer's own domain, so `classify_ats` says
    `company_site` — and the screen is unmistakably Workday. Without the second witness we would
    grow a bespoke per-employer path for what is just Workday."""
    view = orientation.orient(bundle("https://careers.kkr.com/jobs/1234",
                                     belief=belief_with_visual("workday_my_information")))
    assert view.branded_wrapper
    assert view.platform == "workday"
    assert "branded wrapper" in view.rationale


def test_the_url_leads_when_it_recognises_the_host():
    """A host match is a FACT. The visual witness is 93% — good, and not good enough to overrule
    a fact. It leads only where the URL has nothing."""
    view = orientation.orient(bundle("https://boards.greenhouse.io/acme/jobs/1",
                                     belief=belief_with_visual("workday_my_information")))
    assert view.platform == "greenhouse"
    assert view.agreement == "split"
    assert not view.branded_wrapper


def test_agreement_is_recorded_when_both_witnesses_speak():
    view = orientation.orient(bundle("https://acme.wd5.myworkdayjobs.com/x",
                                     belief=belief_with_visual("workday_questions")))
    assert view.agreement == "agree"


def test_no_visual_witness_is_no_evidence_not_a_guess():
    """The perception cascade skips the eyes when the ears are clear, so 'not consulted' is the
    NORMAL case — it must not be laundered into a weak opinion."""
    dom_only = {"state": "workday_questions", "uncertainty": {"state": 0.1},
                "assessed": ["state"], "witnesses": [{"name": "dom:tfidf", "label": "workday_questions"}]}
    view = orientation.orient(bundle("https://careers.example.com/x", belief=dom_only))
    assert view.visual_platform == ""


# --- finding Apply --------------------------------------------------------------------
def test_the_apply_control_is_found_and_named_as_rendered():
    view = orientation.orient(bundle(identities=("button|Apply on company site",)))
    assert view.apply_control == "Apply on company site"


@pytest.mark.parametrize("label", [
    "Apply", "Apply now", "Apply for this job", "Start your application", "Easy Apply",
])
def test_the_lexicon_covers_the_labels_we_actually_meet(label):
    assert orientation.orient(bundle(identities=(f"button|{label}",))).apply_control == label


def test_the_most_specific_apply_label_wins():
    view = orientation.orient(bundle(identities=("button|Apply", "button|Apply on company site")))
    assert view.apply_control == "Apply on company site"


def test_orientation_never_proposes_submit():
    """Entering an application is reversible and is the operator's call per prospect; SENDING one
    is irreversible and is never this module's business."""
    for label in ("Submit application", "Send application", "Confirm and submit"):
        view = orientation.orient(bundle(identities=(f"button|{label}",)))
        assert view.apply_control == "", label


def test_a_page_with_no_controls_reports_the_gap():
    view = orientation.orient(bundle(identities=()))
    assert "page:no-addressable-controls" in view.gaps


# --- the prediction it produces -------------------------------------------------------
def test_predict_proposes_the_apply_click_but_never_takes_it():
    d = orientation.predict(bundle(identities=APPLY_PAGE))
    assert d is not None
    assert d.intent == Intent.CLICK.value and d.params == {"control": "Apply"}
    assert d.escalate, "entering an ATS needs per-prospect approval — this proposes, never acts"
    assert d.confidence < DECISION_CONFIDENCE_THRESHOLD


def test_predict_returns_none_when_this_is_not_a_landing_page():
    assert orientation.predict(bundle(identities=("textbox|Phone",))) is None


def test_decide_uses_the_orienter_on_an_unknown_state():
    """The deep end has no form to read shape from yet, so the shape guess would return `observe`
    and teach nothing. Orientation is tried first for exactly that reason."""
    d = decide(bundle(identities=APPLY_PAGE), programs=NoPrograms(),
               orient=orientation.predict)
    assert d.escalate and d.escalation_axis == "unknown_state"
    assert d.intent == Intent.CLICK.value and d.params == {"control": "Apply"}
    assert "local guess" in d.rationale


def test_decide_falls_back_to_form_shape_when_orientation_has_nothing():
    d = decide(
        Bundle(task="apply", goal_text="", done=False, url="https://x/y", route="/y",
               state="workday_questions", is_branch=False, human_required=False, ats="workday",
               unanswered=({"field": "phone", "kind": "input"},)),
        programs=NoPrograms(), orient=orientation.predict)
    assert d.intent == Intent.SET_TEXT.value and d.params == {"field": "phone"}


def test_decide_without_an_orienter_is_unchanged():
    """The hook is injected precisely so `decide` keeps its no-IO, no-registry purity."""
    d = decide(bundle(identities=APPLY_PAGE), programs=NoPrograms())
    assert d.intent == Intent.OBSERVE.value        # no form, no advance control, no orienter


def test_the_live_route_wires_the_orienter():
    import inspect
    from routers import controller as router
    assert "orient=orientation.predict" in inspect.getsource(router.run_live)
