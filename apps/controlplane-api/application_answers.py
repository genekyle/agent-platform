"""Application answer store — seed + question matcher.

The store holds canonical answers to repeatable job-application questions
(demographics, salary, eligibility). The matcher maps an arbitrary on-screen
question string to the best stored answer using cheap keyword overlap — no model,
no API. When nothing scores above threshold, the caller falls back to Haiku (the
same cheapest-first cascade used elsewhere). The future form-fill executor reads
the matched answer's `value`/`options` to fill the field.
"""

from __future__ import annotations

import re
from typing import Any

#: Score a question must reach before this store answers it, i.e. essentially one whole pattern
#: present verbatim (3.0). Raised from 2.0 on 2026-07-19: at 2.0, loose token overlap alone could
#: carry a match — "How many years of email marketing have you done?" scored 2.5 against the
#: SMS-consent patterns and would have been answered "No".
#:
#: The costs here are deliberately asymmetric. A MISS falls through to Haiku and then to asking
#: the operator — a few cents and a question. A FALSE POSITIVE writes a wrong answer into a real
#: job application and, once rung-0 programs replay unattended, does it silently every time. So
#: the bar is "ask, don't guess", the same rule the rest of the cascade follows.
MATCH_THRESHOLD = 3.0

_WORD = re.compile(r"[a-z0-9]+")
# Generic words that shouldn't drive a match (every question has them).
_STOP = frozenset({
    "what", "is", "are", "your", "the", "a", "an", "do", "you", "have", "to", "of",
    "for", "in", "on", "please", "select", "choose", "enter", "provide", "this",
    "would", "like", "will", "can", "we", "us", "and", "or", "with", "be", "as",
})

# Operator-stated answers (2026-06-24). source='human' = trusted. Edit in the UI.
SEED_ANSWERS: list[dict[str, Any]] = [
    {
        "answer_key": "salary_expectation", "display_name": "Salary expectation",
        "category": "compensation", "value": "70000", "input_hint": "number",
        "question_patterns": ["salary expectation", "expected salary", "salary requirement",
                              "desired salary", "desired compensation", "compensation expectation",
                              "what are your salary requirements", "pay expectation"],
        "options": [],
        "notes": "Annual USD. Adjust per role if needed.",
    },
    {
        "answer_key": "location", "display_name": "Location / city",
        "category": "logistics", "value": "Concord, NH 03301", "input_hint": "text",
        "question_patterns": ["location", "city", "where are you located", "current location",
                              "city, state", "postal code", "zip code", "address"],
        "options": [],
        "notes": "Home base; also the default Indeed search location.",
    },
    {
        "answer_key": "race_ethnicity", "display_name": "Race / ethnicity",
        "category": "demographics", "value": "Asian", "input_hint": "select",
        "question_patterns": ["race", "ethnicity", "race/ethnicity", "racial",
                              "ethnic background", "which race", "race or ethnicity"],
        "options": ["Asian"],
        "notes": "EEO self-identification. Filipino is classified as Asian (not Pacific "
                 "Islander) in US EEO/OFCCP categories.",
    },
    {
        "answer_key": "hispanic_latino", "display_name": "Hispanic / Latino",
        "category": "demographics", "value": "No", "input_hint": "radio",
        "question_patterns": ["hispanic", "latino", "hispanic or latino",
                              "are you hispanic", "hispanic/latino", "of hispanic origin"],
        "options": ["No", "Not Hispanic or Latino"],
        "notes": "EEO self-identification — asked separately from race.",
    },
    {
        "answer_key": "terms_acknowledgment", "display_name": "Terms acknowledgment",
        "category": "acknowledgment", "value": "Yes", "input_hint": "radio",
        # NB: a bare "consent" pattern used to live here and was actively dangerous — it matched
        # "SMS recruiting-text consent" at score 3.0 and answered YES, the opposite of what the
        # teacher answered live (measured 2026-07-19). Agreeing to terms is REQUIRED to proceed;
        # opting into marketing contact is not. Keep these patterns about terms, never consent
        # in general — see `marketing_sms_consent`.
        "question_patterns": ["i have read", "read and understood", "acknowledge",
                              "agree to the terms", "terms and conditions", "privacy notice",
                              "i agree", "read the above", "have read and agree"],
        "options": ["Yes", "I have read and understand", "I agree", "I acknowledge"],
        "notes": "Auto-acknowledge the read-the-terms radio that gates EEO/self-ID pages.",
    },
    {
        "answer_key": "marketing_sms_consent", "display_name": "SMS / marketing contact consent",
        "category": "acknowledgment", "value": "No", "input_hint": "radio",
        # Patterns are deliberately multi-word: a bare "marketing" would hijack a skills question
        # ("Do you have marketing experience?") and answer No to it.
        "question_patterns": ["sms", "text message", "recruiting-text", "recruiting text",
                              "by text", "text alerts", "marketing email", "marketing message",
                              "marketing communication", "promotional"],
        "options": ["No"],
        "notes": "DECLINE marketing/SMS contact — the teacher answered No live (2026-07-18). "
                 "Deliberately separate from terms_acknowledgment so an optional opt-in can "
                 "never inherit a required agreement's Yes.",
    },
    {
        "answer_key": "work_authorization", "display_name": "Authorized to work in the US",
        "category": "eligibility", "value": "Yes", "input_hint": "radio",
        "question_patterns": ["authorized to work", "legally authorized", "eligible to work",
                              "authorization to work", "right to work", "are you authorized",
                              "legally eligible"],
        "options": ["Yes"],
        "notes": "POLARITY TRAP: this is the mirror of `sponsorship_required` — same subject, "
                 "opposite answer. One entry must never serve both, or half the applications "
                 "get the wrong answer.",
    },
    {
        "answer_key": "finance_domain_experience",
        "display_name": "Treasury / finance / accounting experience",
        "category": "eligibility", "value": "Yes", "input_hint": "radio",
        "question_patterns": ["treasury, finance, or accounting", "treasury, finance or accounting",
                              "treasury, finance", "finance, or accounting", "finance or accounting",
                              "experience in treasury", "accounting experience"],
        "options": ["Yes", "Yes, I have worked in treasury, finance or accounting"],
        "notes": "Operator-confirmed 2026-07-19: has the 1-2+ years these analyst postings ask for. "
                 "Recurs constantly on analyst roles, which is why it's stored rather than asked "
                 "each time. Patterns stay multi-word so a generic 'finance' in a job blurb can't "
                 "trigger a claim about the operator's background.",
    },
    {
        "answer_key": "sponsorship_required", "display_name": "Requires visa sponsorship",
        "category": "eligibility", "value": "No", "input_hint": "radio",
        "question_patterns": ["require sponsorship", "need sponsorship", "visa sponsorship",
                              "require visa", "immigration sponsorship",
                              "sponsorship now or in the future", "require employer sponsorship"],
        "options": ["No"],
        "notes": "Operator needs NO sponsorship. POLARITY TRAP — see `work_authorization`.",
    },
    {
        "answer_key": "gender", "display_name": "Gender",
        "category": "demographics", "value": "Male", "input_hint": "select",
        "question_patterns": ["gender", "what is your gender", "sex", "gender identity"],
        "options": ["Male"],
        "notes": "EEO self-identification.",
    },
    {
        "answer_key": "disability_status", "display_name": "Disability status",
        "category": "demographics", "value": "No, I do not have a disability",
        "input_hint": "radio",
        "question_patterns": ["disability", "do you have a disability", "disability status",
                              "are you disabled", "voluntary self-identification of disability"],
        "options": ["No, I do not have a disability"],
        "notes": "EEO self-identification.",
    },
    {
        "answer_key": "veteran_status", "display_name": "Veteran status",
        "category": "demographics", "value": "I am not a protected veteran",
        "input_hint": "radio",
        "question_patterns": ["veteran", "protected veteran", "veteran status", "military",
                              "are you a veteran", "protected veteran status"],
        "options": ["I am not a protected veteran"],
        "notes": "EEO self-identification.",
    },
]


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1}


#: The three control shapes that decide whether a stored value can LAND in a control at all.
#: A textarea is a prose box; a chooser has a fixed option set and is answered by picking, not
#: typing; everything else is one short line. Deliberately coarser than the census's `kind` — the
#: question here is not "which widget is this" but "could this value ever be a right answer here".
LONG_TEXT, CHOICE, SHORT_TEXT = "long_text", "choice", "short_text"

#: Census `kind` / AX `role` -> control class. Both vocabularies land here because the fill plan
#: is fed from both scanners and they name the same widgets differently.
_KIND_CLASS: dict[str, str] = {
    "textarea": LONG_TEXT,
    "button": CHOICE, "select": CHOICE, "combobox": CHOICE,
    "radio": CHOICE, "checkbox": CHOICE, "radiogroup": CHOICE,
    "input": SHORT_TEXT, "textbox": SHORT_TEXT, "text": SHORT_TEXT,
}

#: A stored answer's `input_hint` -> the control class it was WRITTEN for. `text`/`number`/`date`
#: are all one short line, so they collapse together.
_HINT_CLASS: dict[str, str] = {
    "textarea": LONG_TEXT,
    "select": CHOICE, "radio": CHOICE, "checkbox": CHOICE,
}


def control_class(kind: str = "", role: str = "") -> str:
    """Which of the three shapes this control is, or '' when nothing said.

    `kind` (the DOM census) wins over `role` (the AX tree) because the census reads the tag and
    the AX tree flattens `<textarea>` and `<input>` into the same `textbox` role — which is
    exactly the distinction the guards below turn on.
    """
    for token in ((kind or "").strip().lower(), (role or "").strip().lower()):
        if token in _KIND_CLASS:
            return _KIND_CLASS[token]
    return ""


def answer_class(input_hint: str) -> str:
    return _HINT_CLASS.get((input_hint or "").strip().lower(), SHORT_TEXT)


def kind_refuses(control: str, answer: str) -> str:
    """Why this answer can never be right for this control's shape, or '' if it might be.

    Only the pairings that are wrong on their FACE are refused; this is a shape check, not a
    judgement about the question. A short `text` answer in a textarea is fine (Workday asks for a
    full legal name in one), and a short `text` answer on a chooser is fine too (`work_authorization`
    is stored as the text "Yes" and is answered by picking Yes). What can never be right is prose
    in a box that cannot hold it, or an option pick offered as prose.
    """
    if not control or not answer:
        return ""
    if answer == LONG_TEXT and control != LONG_TEXT:
        return "a multi-line block cannot be the answer to a single-line control"
    if answer == CHOICE and control == LONG_TEXT:
        return "an option pick is not prose, and this control is a free-text box"
    return ""


def match_question(question: str, answers: list[dict[str, Any]],
                   kind: str = "", role: str = "") -> dict[str, Any]:
    """Map an on-screen question to the best stored answer.

    Scoring: a question_pattern that appears as a substring of the question is a strong
    signal (weight 3); otherwise token overlap between the question and the pattern /
    answer name (weight 1 each). Returns {matched, answer_key, value, options, score,
    confidence, reason}. `matched` is False when nothing clears the threshold — the
    caller then escalates to Haiku.

    THE CONTROL'S SHAPE IS EVIDENCE, and leaving it out cost a right answer to a wrong one.
    Eversource's Workday asks *"List three business references (previous supervisors); include
    name, title, company, city,"* in a TEXTAREA, and `references_long_form` holds exactly that
    block. But `location`'s bare "city" pattern hit the sentence's last word verbatim (3.0) and
    took the display-name bonus on the same word (3.5), while the references entry could only
    reach 3.0 on token overlap — so the planned fill for a three-reference box was the single
    word **"Concord"**, one Execute away from a real employer's form (live, 2026-08-17).

    Text alone could not separate them; the widget could. So a `kind`/`role` refuses the answers
    whose shape can never fit, and breaks the remaining tie toward the answer written for this
    shape. Both are optional: called without a kind this scores exactly as it did before.
    """
    q = (question or "").lower().strip()
    q_tokens = _tokens(q)
    if not q_tokens:
        return {"matched": False, "reason": "empty_question", "score": 0.0}

    want = control_class(kind, role)
    best = None
    best_score = 0.0
    refused: list[str] = []
    for a in answers:
        why_not = kind_refuses(want, answer_class(a.get("input_hint", "")))
        if why_not:
            refused.append(a.get("answer_key", ""))
            continue
        # The BEST single pattern decides — deliberately not the sum. Summing let a common word
        # repeated across several patterns manufacture a match: "Do you have marketing
        # experience?" scored 3.0 against the three "marketing …" patterns and came back as an
        # SMS-consent question (found 2026-07-19). Evidence is how well ONE pattern fits, not how
        # many patterns happen to share a word.
        score = 0.0
        for pat in a.get("question_patterns") or []:
            patl = pat.lower().strip()
            pat_tokens = _tokens(pat)
            # A PATTERN MADE ONLY OF STOPWORDS IS NOT EVIDENCE, and the verbatim branch used to be
            # the one place that never asked. `_STOP` has contained "to" from the start and
            # `_tokens` filtered it correctly — but `patl in q` bypassed all of that, so
            # `education_end_date`'s literal "to" pattern (meant as the "to" of a date range)
            # scored the full 3.0 on any question containing the word. Measured 2026-08-15:
            # "Will MAPFRE Insurance need to sponsor you for employment" resolved to
            # `education_end_date` = '06/2021' at confidence 0.75, and so did "Are you related to
            # anyone", "Which of the following are you willing to work" and the California notice.
            # A wrong answer to a sponsorship question, wearing a trustworthy number.
            #
            # ANCHORED AT THE START OF A WORD, AND ONLY THERE. `field_answer_key` grew full word
            # boundaries after "Ethni-CITY" became the operator's home town, but that rule is too
            # strict here: these patterns are written as STEMS, and both edges would break
            # "acknowledge" against "acknowledgement" and "sponsor" against "sponsorship" — the
            # sponsorship entry's own pattern. Anchoring only the left edge keeps the stem match
            # and still refuses the needle buried inside another word, because "city" in
            # "ethnicity" does not begin one.
            if patl and pat_tokens and re.search(rf"(?<![a-z0-9]){re.escape(patl)}", q):
                score = max(score, 3.0)          # whole pattern present verbatim
            else:
                score = max(score, len(pat_tokens & q_tokens) * 1.0)
        # The answer_key / display_name tokens themselves are weak signals.
        score += 0.5 * len(_tokens(a.get("display_name", "")) & q_tokens)
        # WRITTEN FOR THIS SHAPE. Worth a whole pattern's weight because it is the same order of
        # evidence: an entry whose hint is `textarea` was authored to fill a prose box, and that
        # says more about fitness than one more shared word does. Only ever a tiebreak — it
        # cannot lift an answer over MATCH_THRESHOLD on its own, since 1.0 < 3.0.
        if want and answer_class(a.get("input_hint", "")) == want:
            score += 1.0
        if score > best_score:
            best_score, best = score, a

    matched = best is not None and best_score >= MATCH_THRESHOLD
    if not matched:
        return {"matched": False, "score": round(best_score, 2),
                "reason": "below_threshold", "best_key": best["answer_key"] if best else None,
                "control_class": want, "refused_for_kind": refused}
    return {
        "matched": True,
        "answer_key": best["answer_key"],
        "value": best.get("value", ""),
        "options": best.get("options") or [],
        "input_hint": best.get("input_hint", "text"),
        "score": round(best_score, 2),
        "confidence": round(min(1.0, best_score / 4.0), 3),
        # What the shape contributed, so a surprising match is explainable without a re-run.
        "control_class": want,
        "refused_for_kind": refused,
    }
