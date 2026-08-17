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


# --- the control's SHAPE is evidence (measured live 2026-08-17, Eversource/Workday) -----------
#
# The live store holds both of these. Neither the seed list nor the question text can separate
# them on the references question: `location`'s bare "city" pattern hits the sentence's last word
# verbatim (3.0) and takes the display-name bonus on the same word (3.5), while the references
# entry reaches only 3.0 on token overlap. The widget is what tells them apart.
_REFERENCES = {
    "answer_key": "references_long_form", "display_name": "References (long form)",
    "category": "references", "input_hint": "textarea",
    "value": "Alex Wall — Development Database Manager\nAixa Lovezzola — Director of Finance",
    "question_patterns": ["please list your references", "list three references",
                          "list your references", "professional references"],
    "options": [],
}
_CITY = {
    "answer_key": "city", "display_name": "City", "category": "logistics",
    "value": "Concord", "input_hint": "text",
    "question_patterns": ["city"], "options": [],
}
_SHAPED = [_CITY, _REFERENCES]

# The census cuts field names at ~90 chars, which is why this ends mid-clause on the word `city,`.
_REFS_Q = ("List three business references (previous supervisors); "
           "include name, title, company, city,")


def test_a_three_reference_textarea_does_not_resolve_to_the_home_town():
    """The bug, exactly as it was planned live: 'Concord' into a three-reference box."""
    assert aa.match_question(_REFS_Q, _SHAPED)["answer_key"] == "city"      # text alone: wrong
    r = aa.match_question(_REFS_Q, _SHAPED, kind="textarea")                # + the widget: right
    assert r["matched"] and r["answer_key"] == "references_long_form"
    assert r["control_class"] == aa.LONG_TEXT


def test_a_multi_line_block_is_refused_by_a_single_line_control():
    """The mirror: the references block must never be typed into a short City input."""
    r = aa.match_question("City", _SHAPED, kind="input")
    assert r["matched"] and r["answer_key"] == "city"
    assert "references_long_form" in r["refused_for_kind"]


def test_a_prose_answer_is_refused_by_a_chooser():
    r = aa.match_question(_REFS_Q, _SHAPED, kind="button")
    assert "references_long_form" in r["refused_for_kind"]


def test_the_ax_role_is_used_when_the_census_kind_is_absent():
    assert aa.control_class("", "combobox") == aa.CHOICE
    assert aa.control_class("textarea", "textbox") == aa.LONG_TEXT   # kind wins over role


def test_a_short_text_answer_still_fills_a_textarea():
    """Workday asks for a full legal name in a textarea — `text` into prose is not refused."""
    name = {"answer_key": "full_name", "display_name": "Full name", "input_hint": "text",
            "value": "Gene Kyle Magsipoc",
            "question_patterns": ["full legal name", "your full name"], "options": []}
    r = aa.match_question("Please list your full legal name.*", [name], kind="textarea")
    assert r["matched"] and r["answer_key"] == "full_name"


def test_a_text_answer_still_wins_a_chooser_it_was_always_right_for():
    """`work_authorization` is stored as the text 'Yes' and is answered by PICKING Yes."""
    r = aa.match_question("Are you legally authorized to work in the United States?*",
                          ANSWERS, kind="button")
    assert r["matched"] and r["answer_key"] == "work_authorization"


def test_the_shape_bonus_cannot_carry_a_match_on_its_own():
    """+1.0 is a tiebreak, never evidence: below threshold stays below threshold."""
    r = aa.match_question("Describe a challenging project you led.", ANSWERS, kind="textarea")
    assert r["matched"] is False


def test_scoring_without_a_kind_is_unchanged():
    for q in ("What is your gender?", "Are you a protected veteran?",
              "Voluntary self-identification of disability"):
        assert aa.match_question(q, ANSWERS) == aa.match_question(q, ANSWERS, kind="", role="")
