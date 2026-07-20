"""Tests for the application-answer question matcher."""

import application_answers as aa

ANSWERS = aa.SEED_ANSWERS


def test_salary_question_matches():
    r = aa.match_question("What is your expected salary?", ANSWERS)
    assert r["matched"] and r["answer_key"] == "salary_expectation"
    assert r["value"] == "70000"


def test_salary_paraphrase_matches():
    r = aa.match_question("Please enter your desired compensation for this position", ANSWERS)
    assert r["matched"] and r["answer_key"] == "salary_expectation"


def test_veteran_matches():
    r = aa.match_question("Are you a protected veteran?", ANSWERS)
    assert r["matched"] and r["answer_key"] == "veteran_status"


def test_gender_matches():
    r = aa.match_question("What is your gender?", ANSWERS)
    assert r["matched"] and r["answer_key"] == "gender" and r["value"] == "Male"


def test_disability_matches():
    r = aa.match_question("Voluntary self-identification of disability", ANSWERS)
    assert r["matched"] and r["answer_key"] == "disability_status"


def test_unknown_question_falls_through():
    r = aa.match_question("Describe a challenging project you led.", ANSWERS)
    assert r["matched"] is False  # → caller escalates to Haiku


def test_empty_question():
    assert aa.match_question("", ANSWERS)["matched"] is False


def test_race_not_confused_with_gender():
    # 'race/ethnicity' must not accidentally win 'gender' and vice-versa.
    assert aa.match_question("Race / Ethnicity", ANSWERS)["answer_key"] == "race_ethnicity"
    assert aa.match_question("Gender identity", ANSWERS)["answer_key"] == "gender"


# --- polarity traps + false positives (measured 2026-07-19) ------------------------
def test_sms_consent_is_not_a_terms_acknowledgment():
    """The dangerous one: a bare "consent" pattern on terms_acknowledgment matched
    "SMS recruiting-text consent" at 3.0 and answered YES — the opposite of the teacher's live
    answer. A rung-0 replay would have opted the operator INTO recruiting texts."""
    r = aa.match_question("SMS recruiting-text consent", ANSWERS)
    assert r["matched"] and r["answer_key"] == "marketing_sms_consent"
    assert r["value"] == "No"


def test_required_agreement_still_says_yes():
    """Declining marketing must not bleed into agreements that GATE the form."""
    for q in ["read and accept the above acknowledgement",
              "I have read and agree to the privacy notice",
              "I agree to the terms and conditions"]:
        r = aa.match_question(q, ANSWERS)
        assert r["matched"] and r["answer_key"] == "terms_acknowledgment", q
        assert r["value"] == "Yes"


def test_work_authorization_and_sponsorship_are_opposite_and_never_swap():
    """Same subject, opposite answers. One entry serving both would get half of them wrong."""
    auth = aa.match_question("Are you authorized to work in the United States?", ANSWERS)
    assert auth["matched"] and auth["answer_key"] == "work_authorization" and auth["value"] == "Yes"

    spon = aa.match_question(
        "Will you now or in the future require sponsorship for an employment visa?", ANSWERS)
    assert spon["matched"] and spon["answer_key"] == "sponsorship_required" and spon["value"] == "No"


def test_a_shared_word_across_patterns_cannot_manufacture_a_match():
    """Scoring takes the BEST single pattern, not the sum — otherwise a common word repeated
    across several patterns invents evidence. "Do you have marketing experience?" used to score
    3.0 against the three "marketing …" patterns and come back as an SMS-consent question."""
    for decoy in ["Do you have marketing experience?",
                  "Describe your text analytics background",
                  "How many years of email marketing have you done?"]:
        r = aa.match_question(decoy, ANSWERS)
        assert r["answer_key"] != "marketing_sms_consent" if r["matched"] else True, decoy
