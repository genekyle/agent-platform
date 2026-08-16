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


# --- an all-stopword pattern is not evidence -------------------------------------------------
#
# `education_end_date` shipped with the literal pattern "to" (the "to" of a date range). The
# verbatim branch did not consult _STOP, so on 2026-08-15 four unrelated MAPFRE questions came
# back as that education date at confidence 0.75 — including "Will MAPFRE Insurance need to
# sponsor you for employment", where the truthful answer is "No".

_JUNK = [
    {"answer_key": "education_end_date", "display_name": "Education end date",
     "value": "06/2021", "question_patterns": ["end date", "graduation", "education end", "to"]},
    {"answer_key": "sponsorship_required", "display_name": "Sponsorship required",
     "value": "No", "question_patterns": ["sponsor", "visa sponsorship", "require sponsorship"]},
]


def test_a_stopword_only_pattern_cannot_claim_a_verbatim_match():
    for q in ("Are you related to anyone at any MAPFRE Location?",
              "Which of the following are you willing to work?",
              "Notice to California Applicants: would you like a copy of any public record"):
        got = aa.match_question(q, _JUNK)
        assert got.get("answer_key") != "education_end_date", f"{q!r} -> {got}"


def test_the_sponsorship_question_reaches_its_own_answer():
    got = aa.match_question(
        "Will MAPFRE Insurance need to sponsor you for employment at the present time?", _JUNK)
    assert got["matched"] and got["answer_key"] == "sponsorship_required" and got["value"] == "No"


def test_a_real_pattern_still_matches_verbatim():
    got = aa.match_question("What was your education end date?", _JUNK)
    assert got["matched"] and got["answer_key"] == "education_end_date"


def test_a_pattern_buried_inside_another_word_does_not_count():
    """"city" must not match "ethni-CITY" — but a pattern IS allowed to match a longer word it
    starts, because these patterns are stems ("sponsor" answers "sponsorship")."""
    ans = [{"answer_key": "city", "display_name": "City", "value": "Concord",
            "question_patterns": ["city"]}]
    assert not aa.match_question("What is your ethnicity?", ans).get("matched")

    stem = [{"answer_key": "sponsorship_required", "display_name": "Sponsorship",
             "value": "No", "question_patterns": ["sponsor"]}]
    assert aa.match_question("Do you require sponsorship for employment?", stem)["matched"]
