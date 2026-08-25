"""A widget that NAMES its own engine must never be classified by inference.

Workday tags its prompt engine on the element as `data-uxi-widget-type="selectinput"`. On
School or University and Field of Study that is the ONLY tell: measured live 2026-08-24
(SolutionHealth, JR13051) the node is `INPUT type=text` with no role, no aria-haspopup, no
aria-expanded, not readonly, and an EMPTY data-automation-id — so the classifier's ARIA tests and
its `formField-` prompt test all miss, it falls through to `text`, and typing into it reports OK
while the answer never commits.

That failure is silent in the worst way: the census reads the field UNANSWERED afterwards, which
invites a retry, and a retry on a stateful widget is the non-idempotent path this repo already
warns about. The operator caught it from outside the system — *"while it is technically an input,
it's actually a drop down and must select"* — which is the definition of a check we owed ourselves.

Pinned as TEXT, the same enforcement style as `test_js_blob_tells`: the rule cannot be dropped by
a refactor without this failing, and the Python mirror below pins the SEMANTICS rather than the
spelling.
"""
import re

from app import widget_probe


def test_the_classifier_consults_the_widgets_own_declaration():
    js = widget_probe.DESCRIBE_WIDGET_JS
    assert "data-uxi-widget-type" in js, (
        "the classifier stopped asking the widget what it is; a Workday selectinput with no ARIA "
        "attributes will be classified as free text again")
    # It must feed the PROMPT branch — reading the attribute and then ignoring it is the same bug.
    assert re.search(r"promptish\s*=\s*/select/i\.test\(uxi\)", js), (
        "the declaration is read but no longer decides promptish")


def test_the_declaration_semantics_match_what_workday_actually_emits():
    """The Python mirror of the JS test, against the values measured on the live page."""
    def promptish_by_declaration(uxi: str) -> bool:
        return bool(re.search(r"select", uxi or "", re.I))

    assert promptish_by_declaration("selectinput") is True        # School / Field of Study
    assert promptish_by_declaration("multiselectinput") is True   # the multi-value sibling
    assert promptish_by_declaration("") is False                  # a genuine free-text input
    assert promptish_by_declaration("textinput") is False         # Workday's own free-text tag
