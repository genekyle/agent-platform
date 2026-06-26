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
        "answer_key": "race_ethnicity", "display_name": "Race / ethnicity",
        "category": "demographics", "value": "Asian", "input_hint": "select",
        "question_patterns": ["race", "ethnicity", "race/ethnicity", "racial",
                              "are you hispanic or latino", "ethnic background"],
        "options": ["Asian"],
        "notes": "EEO self-identification.",
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


def match_question(question: str, answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Map an on-screen question to the best stored answer.

    Scoring: a question_pattern that appears as a substring of the question is a strong
    signal (weight 3); otherwise token overlap between the question and the pattern /
    answer name (weight 1 each). Returns {matched, answer_key, value, options, score,
    confidence, reason}. `matched` is False when nothing clears the threshold — the
    caller then escalates to Haiku."""
    q = (question or "").lower().strip()
    q_tokens = _tokens(q)
    if not q_tokens:
        return {"matched": False, "reason": "empty_question", "score": 0.0}

    best = None
    best_score = 0.0
    for a in answers:
        score = 0.0
        for pat in a.get("question_patterns") or []:
            patl = pat.lower().strip()
            if patl and patl in q:
                score += 3.0  # whole pattern present verbatim
            else:
                overlap = len(_tokens(pat) & q_tokens)
                score += overlap * 1.0
        # The answer_key / display_name tokens themselves are weak signals.
        score += 0.5 * len(_tokens(a.get("display_name", "")) & q_tokens)
        if score > best_score:
            best_score, best = score, a

    # Threshold: need at least one solid pattern hit (3) or two token overlaps.
    matched = best is not None and best_score >= 2.0
    if not matched:
        return {"matched": False, "score": round(best_score, 2),
                "reason": "below_threshold", "best_key": best["answer_key"] if best else None}
    return {
        "matched": True,
        "answer_key": best["answer_key"],
        "value": best.get("value", ""),
        "options": best.get("options") or [],
        "input_hint": best.get("input_hint", "text"),
        "score": round(best_score, 2),
        "confidence": round(min(1.0, best_score / 4.0), 3),
    }
