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
