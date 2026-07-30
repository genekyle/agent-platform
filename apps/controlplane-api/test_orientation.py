"""Orientation — witness fusion, the mismatch safety catch, and the 1–2 step way out.

The fixture throughout is the live 2026-07-30 case that motivated the module: an apply click landed
on Ahold Delhaize's careers front (a branded AppVault wrapper), and the account rung offered a
sign-in for a wall that was not on screen while the panel strummed along. Every assertion here is a
piece of that afternoon.
"""

from __future__ import annotations

import apply_landing as al
import orientation as om

_FRONT_URL = ("https://aholddelhaizeusa.careerswithus.com/job/Procurement-%26-Logistics/"
              "Sr.-Reporting-Analyst/Quincy-MA/ADUSA")
_FRONT_TEXT = ("Join Our Talent Community Sr. Reporting Analyst Posting Date: 07/22/2026 Quincy, MA "
               "APPLY NOW Job Requisition: 533857 Responsibilities Qualifications")
_FRONT_SIGNPOST = ["https://aholddelhaizeapply.appvault.com/external/home?jobId=533857"]


def _front(rung="account", **kw):
    return om.orient(_FRONT_URL, _FRONT_TEXT, apply_hrefs=_FRONT_SIGNPOST, rung=rung,
                     company="Ahold Delhaize USA", ats_lookup=lambda c: "appvault", **kw)


# --- fusion -------------------------------------------------------------------------------------
def test_a_named_ats_beats_any_number_of_shrugs():
    """The URL witness answers company_site on every branded wrapper — that is a shrug, not a
    vendor. The signpost and memory both name appvault, and the fusion must prefer a name."""
    o = _front()
    assert o.platform == "appvault"
    assert o.kind == al.JOB_POSTING
    assert o.state == "appvault_job_posting"


def test_dissent_stays_on_the_record():
    """The losing witness is often the finding: company_site-vs-appvault IS the branded-wrapper
    diagnosis, and hiding it would hide what the page is."""
    o = _front()
    claims = {(w.source, w.claim) for w in o.witnesses}
    assert ("url", "company_site") in claims          # the dissenter, kept
    assert ("signpost", "appvault") in claims
    assert ("memory", "appvault") in claims


def test_agreement_sets_confidence():
    o = _front()
    assert o.confidence in ("medium", "high")
    # strip it to one lone witness and confidence has to fall
    lone = om.orient(_FRONT_URL, "", rung="account")
    assert lone.confidence == "low"


def test_a_learned_witness_joins_without_changing_the_shape():
    """The seam the trained observers arrive through: a perception witness votes like any other,
    with its own weight — the fusion does not change when models graduate."""
    seen = om.Witness("perception", "workday", "screenshot sits nearest the workday cluster",
                      weight=2.0)
    o = om.orient("https://careers.example.com/jobs/123", "", rung="classify",
                  extra_witnesses=[seen])
    assert o.platform == "workday"
    assert any(w.source == "perception" for w in o.witnesses)


# --- the safety catch ----------------------------------------------------------------------------
def test_the_account_rung_on_a_job_posting_is_a_mismatch():
    """THE LIVE CASE. The rung declares it needs an account gate; the page is a posting; the verdict
    says the recipe and the world have drifted apart — instead of the panel offering a sign-in."""
    o = _front(rung="account")
    assert o.mismatch is not None
    assert o.mismatch["expected"] == [al.ACCOUNT_GATE]
    assert o.mismatch["observed"] == al.JOB_POSTING
    assert "follow the plan, not the rung" in o.mismatch["detail"]


def test_the_same_page_is_no_mismatch_for_the_rung_that_wants_it():
    assert _front(rung="enter_apply").mismatch is None


def test_classify_never_mismatches():
    """classify's whole job is not knowing yet — it cannot be contradicted by any page."""
    assert _front(rung="classify").mismatch is None


def test_an_unreadable_page_never_fires_the_catch():
    """UNKNOWN must not read as disagreement — 'could not look' and 'looked and saw otherwise' lead
    to different next moves, and only the second is a mismatch."""
    o = om.orient("https://x.example.com/", "", rung="account")
    assert o.kind in (al.UNKNOWN, al.UNREADABLE)
    assert o.mismatch is None


# --- the way out ---------------------------------------------------------------------------------
def test_the_plan_off_a_posting_is_press_apply_then_reorient():
    plan = _front().plan
    assert len(plan) == 2
    assert "apply control" in plan[0]["action"]
    assert "re-orient" in plan[1]["action"]


def test_the_plan_off_an_account_gate_is_the_account_rung():
    gate = "Sign in to continue your application. Email address Password Sign in Create an account"
    o = om.orient("https://aholddelhaizeapply.appvault.com/external/home", gate, rung="account")
    assert o.kind == al.ACCOUNT_GATE
    assert o.mismatch is None
    assert "account rung" in o.plan[0]["action"]


def test_an_undriven_form_plans_an_attended_drive_not_an_autofill():
    form_text = ("Application for employment. First name Last name Email address Phone Resume "
                 "upload Submit application required field")
    o = om.orient("https://apply.example.com/form", form_text, rung="submit", known_recipe=False)
    assert o.kind == al.APPLICATION_FORM
    assert "attended" in o.plan[0]["action"]


def test_nothing_recognised_plans_a_screenshot_and_a_human():
    o = om.orient("https://x.example.com/", "", rung="account")
    assert "escalate" in o.plan[0]["action"]
