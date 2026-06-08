"""Lightweight query normalization for Florence-2 zero-shot grounding.

Florence-2's <CAPTION_TO_PHRASE_GROUNDING> task was trained on short noun-phrase
captions ("a red car", "the wooden chair"), not full imperative sentences. When
we send "Click on the log in button", it grounds substrings like "the log"
to whatever 'log' it can find — often a logo. Stripping the imperative scaffold
down to the noun phrase tends to help.

This is a deliberately simple heuristic preprocessor — it teaches the principle
that model input format matters as much as the model itself, without dragging
in NLP dependencies.

Examples:
  "Click on the log in button"                          → "log in button"
  "Clear out the current wrong input in the email"      → "email"
  "Tap the Sign Up link at the top"                     → "sign up link"
"""
from __future__ import annotations

import re

# Imperative verb phrases stripped from the START of the query (longest first).
_LEADING_VERBS = [
    "clear out the",
    "clear out",
    "click on the",
    "click on",
    "click the",
    "click",
    "tap on the",
    "tap the",
    "tap",
    "press the",
    "press",
    "select the",
    "select",
    "type into the",
    "type in the",
    "type the",
    "type",
    "enter the",
    "enter",
    "choose the",
    "choose",
    "open the",
    "open",
    "find the",
    "find",
]

# Generic stopwords removed anywhere in the string (after the leading-verb strip).
# Kept very small — over-pruning destroys the signal.
# Conservative: only articles + "currently/current". Stripping prepositions like
# "in"/"on" destroys phrases like "log in button" / "sign up button".
_STOPWORDS = {"the", "a", "an", "currently"}

# Trailing phrases dropped — "at the top", "in the corner", etc.
_TRAILING_PHRASES = [
    r"\s+at the (top|bottom|left|right|center|middle).*$",
    r"\s+in the (corner|header|footer|sidebar).*$",
    r"\s+on the (top|bottom|left|right).*$",
]


def normalize_element_query(query: str) -> str:
    if not query:
        return ""
    text = query.strip().rstrip(".").lower()

    # Strip leading imperative verb phrase (longest match first).
    for phrase in _LEADING_VERBS:
        if text.startswith(phrase + " "):
            text = text[len(phrase) + 1:]
            break
        if text == phrase:
            text = ""
            break

    # Drop trailing locational scaffolding.
    for pattern in _TRAILING_PHRASES:
        text = re.sub(pattern, "", text)

    # Remove stopwords token-wise.
    tokens = [t for t in re.split(r"\s+", text) if t and t not in _STOPWORDS]
    result = " ".join(tokens).strip()

    # If we stripped to nothing (pathological), fall back to the original (sans period).
    return result or query.strip().rstrip(".")
